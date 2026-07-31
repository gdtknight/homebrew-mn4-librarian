"""감사에서 발견된 문제를 고친다.

파일을 건드리지 않는 수정(태그 생성/병합/책 태그 교정)만 `tags fix --apply`로
자동 처리한다. 폴더 이동이나 중복 파일 삭제처럼 물리 파일을 건드리는 수정은
항상 `tags review-files` -> 사람 검토 -> `tags apply-file-actions` 3단계를
거친다 (한 번의 --apply로 자동 실행되지 않음 — 이번 세션에 실제로 폴더 자체가
틀린 책들과 중복 파일들을 매번 사람이 확인했던 것과 같은 원칙).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import mn4_db, pdf_extract, safety, settings, tags_audit, taxonomy
from .llm_parse import LlmParseError, judge_folder_match, reparse_existing_book


@dataclass
class BookFixPlan:
    book_key: str
    target_major: str
    target_subcategory: str
    reason: str


@dataclass
class ReviewItem:
    kind: str  # "folder_mismatch" | "duplicate_md5" | "duplicate_filename" | "judge_failed"
    file: str
    detail: dict


def _analyze_mismatches(
    con: sqlite3.Connection,
    library_dir: Path,
    folder_taxonomy: dict[str, list[str]],
    report: tags_audit.AuditReport,
) -> tuple[list[BookFixPlan], list[ReviewItem]]:
    """no_major/폴더-태그 불일치 책마다 LLM에게 "폴더가 내용과 맞는지" 물어본다.
    맞으면 안전한 태그 교정 계획으로, 틀리면(폴더 자체가 의심) 검토 큐로 보낸다."""
    books = mn4_db.iter_library_books(con)
    books_by_key = {f"{b.rel_dir}/{b.file}": b for b in books}

    keys = [f for f, _names in report.no_major_books]
    keys += [f"{m['rel']}/{m['file']}" for m in report.folder_tag_mismatches]

    safe_plans: list[BookFixPlan] = []
    review: list[ReviewItem] = []

    for key in keys:
        book = books_by_key.get(key)
        if book is None or not book.path_major:
            continue
        signal = pdf_extract.extract_signal(library_dir / book.rel_dir / book.file)
        try:
            judgment = judge_folder_match(
                signal, book.path_major, book.path_sub, folder_taxonomy, filename_hint=book.file
            )
        except LlmParseError as exc:
            review.append(ReviewItem("judge_failed", key, {"error": str(exc)}))
            continue

        if judgment["folder_matches"]:
            safe_plans.append(
                BookFixPlan(
                    book_key=key,
                    target_major=book.path_major,
                    target_subcategory=book.path_sub or judgment["suggested_subcategory"],
                    reason=judgment["reason"],
                )
            )
        else:
            review.append(ReviewItem("folder_mismatch", key, judgment))

    return safe_plans, review


def _duplicate_review_items(report: tags_audit.AuditReport) -> list[ReviewItem]:
    items = []
    for md5, entries in report.exact_md5_duplicates.items():
        items.append(ReviewItem("duplicate_md5", entries[0], {"md5": md5, "candidates": entries}))
    for name, entries in report.same_name_diff_md5.items():
        items.append(ReviewItem("duplicate_filename", name, {"candidates": entries}))
    return items


def _replace_major_sub_tags(
    tag_index: mn4_db.TagIndex,
    known_major_names: set[str],
    book: mn4_db.Book,
    new_major_id: str,
    new_sub_id: str | None,
) -> list[str]:
    ids = list(book.taglist)
    sub_ids = {c for links in tag_index.links_by_id.values() for c in links}

    old_major = next((t for t in ids if tag_index.name_by_id.get(t) in known_major_names), None)
    if old_major is not None:
        ids[ids.index(old_major)] = new_major_id
    else:
        ids.insert(0, new_major_id)

    if new_sub_id is not None:
        old_sub = next((t for t in ids if t != new_major_id and t in sub_ids), None)
        if old_sub is not None:
            ids[ids.index(old_sub)] = new_sub_id
        elif new_sub_id not in ids:
            ids.insert(ids.index(new_major_id) + 1, new_sub_id)
    return ids


# ---------------------------------------------------------------------------
# tags fix: DB-only 안전 수정
# ---------------------------------------------------------------------------

def cmd_fix(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="태그 감사 결과 중 파일을 건드리지 않는 항목만 자동 수정")
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 수정한다 (기본은 dry-run)")
    args = parser.parse_args(argv)

    st = settings.get_settings()
    report = tags_audit.run_audit(st.mn4_db_path, st.library_dir, taxonomy.exclude_for(st))

    if not args.apply:
        _dry_run_fixes(report)
        print("\n실제로 적용하려면 --apply 옵션을 붙여 다시 실행하세요.")
        return 0

    safety.prepare_for_write(st.mn4_db_path)
    review_needed = safety.with_write_transaction(
        st.mn4_db_path, lambda con: _apply_fixes(con, st, report)
    )
    print("적용 완료.")
    if review_needed:
        print(f"\n폴더 자체가 의심되는 {len(review_needed)}건은 자동 수정하지 않았습니다:")
        for key in review_needed:
            print(f"  {key}")
        print("`mn4-librarian tags review-files`로 검토 항목을 만드세요.")
    print("\n변경 사항을 확인하려면 `mn4-librarian tags audit`을 다시 실행하세요.")
    return 0


def _dry_run_fixes(report: tags_audit.AuditReport) -> None:
    if report.unregistered_files:
        print(f"\n[처리 불가] MN4에 전혀 등록 안 된 파일 {len(report.unregistered_files)}건")
        print("  MD5는 MN4가 자체 알고리즘으로 생성하므로 mn4-librarian이 대신 등록할 수 없습니다.")
        print("  MarginNote4 앱에서 해당 파일을 한 번 열어 등록시킨 뒤 다시 감사하세요.")
        for f in report.unregistered_files:
            print(f"  {f}")

    if report.missing_in_db:
        print(f"\n[태그 생성] 폴더는 있는데 DB 태그가 없는 {len(report.missing_in_db)}건")
        for major, sub in report.missing_in_db:
            print(f"  {major}" + (f" / {sub}" if sub else " (대분류 전체)"))

    if report.near_duplicate_tags:
        print(f"\n[태그 병합] 근접중복 태그 {len(report.near_duplicate_tags)}그룹 (사용량 많은 쪽으로 통합)")
        for names in report.near_duplicate_tags.values():
            print(f"  {names}")

    if report.untagged_books:
        print(f"\n[태그 신규 부여] 미태깅 책 {len(report.untagged_books)}건")
        for f in report.untagged_books:
            print(f"  {f}")

    total_mismatch = len(report.no_major_books) + len(report.folder_tag_mismatches)
    if total_mismatch:
        print(f"\n[태그 교정 검토] 대분류 누락/폴더-태그 불일치 {total_mismatch}건")
        print("  claude로 폴더-내용 일치 여부를 판단해, 맞으면 태그만 교정하고 안 맞으면 검토 큐로 보냅니다.")

    if not any(
        [
            report.missing_in_db,
            report.near_duplicate_tags,
            report.untagged_books,
            report.no_major_books,
            report.folder_tag_mismatches,
        ]
    ):
        print("자동 수정할 항목이 없습니다.")


def _apply_fixes(con: sqlite3.Connection, st: settings.Settings, report: tags_audit.AuditReport) -> list[str]:
    tag_index = mn4_db.TagIndex(con)
    library_dir = st.library_dir
    folder_taxonomy = taxonomy.scan(library_dir, taxonomy.exclude_for(st))
    known_major_names = set(folder_taxonomy.keys())

    # 1) 폴더는 있는데 DB 태그가 없는 것 생성
    for major, sub in report.missing_in_db:
        mid = tag_index.get_or_create_root_tag(major)
        if sub is None:
            for s in folder_taxonomy.get(major, []):
                tag_index.get_or_create_tag(s, mid)
        else:
            tag_index.get_or_create_tag(sub, mid)

    # 2) 근접중복 태그 병합 (사용량이 가장 많은 쪽을 canonical로)
    for names in report.near_duplicate_tags.values():
        candidates = []
        for name in names:
            tid = tag_index.find_id(name)
            if tid is None:
                continue
            (count,) = con.execute(
                "SELECT COUNT(*) FROM ZBOOKCONFIG WHERE ZTAGLIST LIKE ?", (f"%{tid}%",)
            ).fetchone()
            candidates.append((tid, count))
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda x: -x[1])
        keep_id = candidates[0][0]
        for tid, _count in candidates[1:]:
            tag_index.merge_tag(keep_id, tid)

    # 3) 대분류 누락 / 폴더-태그 불일치 -> 폴더가 맞다고 판단된 것만 태그 교정
    safe_plans, review = _analyze_mismatches(con, library_dir, folder_taxonomy, report)
    books = mn4_db.iter_library_books(con)
    books_by_key = {f"{b.rel_dir}/{b.file}": b for b in books}
    for plan in safe_plans:
        book = books_by_key.get(plan.book_key)
        if book is None:
            continue
        mid = tag_index.get_or_create_root_tag(plan.target_major)
        sid = tag_index.get_or_create_tag(plan.target_subcategory, mid) if plan.target_subcategory else None
        ids = _replace_major_sub_tags(tag_index, known_major_names, book, mid, sid)
        mn4_db.set_taglist(con, book, ids)

    # 4) 미태깅 책 -> LLM으로 서지정보 재구성 + 폴더 기반 대/소분류 부여
    for key in report.untagged_books:
        book = books_by_key.get(key)
        if book is None or not book.path_major:
            continue
        signal = pdf_extract.extract_signal(library_dir / book.rel_dir / book.file)
        try:
            meta = reparse_existing_book(signal, existing_filename_stem=Path(book.file).stem)
        except LlmParseError:
            continue

        mid = tag_index.get_or_create_root_tag(book.path_major)
        sub_name = book.path_sub or "General"
        sid = tag_index.get_or_create_tag(sub_name, mid)
        ids = [mid, sid]
        if meta.year:
            year_root = tag_index.get_or_create_root_tag("출판연도")
            ids.append(tag_index.get_or_create_tag(meta.year, year_root))
        if meta.publisher:
            pub_root = tag_index.get_or_create_root_tag("출판사")
            ids.append(tag_index.get_or_create_tag(meta.publisher, pub_root))
        author_root = tag_index.get_or_create_root_tag("저자") if meta.authors else None
        for author in meta.authors:
            ids.append(tag_index.get_or_create_tag(author, author_root))
        mn4_db.set_taglist(con, book, ids)

    return [r.file for r in review if r.kind == "folder_mismatch"]


# ---------------------------------------------------------------------------
# tags review-files: 파일 조치가 필요한 항목을 검토용 JSON으로 저장
# ---------------------------------------------------------------------------

def cmd_review_files(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="파일 이동/삭제가 필요한 항목을 검토용 JSON으로 만든다")
    parser.parse_args(argv)

    st = settings.get_settings()
    report = tags_audit.run_audit(st.mn4_db_path, st.library_dir, taxonomy.exclude_for(st))
    folder_taxonomy = taxonomy.scan(st.library_dir, taxonomy.exclude_for(st))

    con = mn4_db.connect_readonly(st.mn4_db_path)
    try:
        _safe_plans, mismatch_review = _analyze_mismatches(con, st.library_dir, folder_taxonomy, report)
    finally:
        con.close()

    items: list[dict] = []
    for r in mismatch_review:
        if r.kind == "folder_mismatch":
            items.append(
                {
                    "kind": "folder_mismatch",
                    "file": r.file,
                    "llm_suggestion": r.detail,
                    "decision": "pending",  # "move" 로 바꾸면 target_category/target_subcategory로 이동
                    "target_category": r.detail.get("suggested_category"),
                    "target_subcategory": r.detail.get("suggested_subcategory"),
                }
            )
        else:
            items.append({"kind": r.kind, "file": r.file, "detail": r.detail, "decision": "pending"})

    for r in _duplicate_review_items(report):
        items.append(
            {
                "kind": r.kind,
                "file": r.file,
                "candidates": r.detail["candidates"],
                "decision": "pending",  # "keep" 로 바꾸고 keep_path를 채우면 나머지를 삭제
                "keep_path": None,
            }
        )

    if not items:
        print("검토가 필요한 파일 조치 항목이 없습니다.")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = settings.logs_dir() / f"review-{ts}.json"
    path.write_text(json.dumps({"generated_at": ts, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"검토 항목 {len(items)}건 -> {path}")
    print('파일을 열어 각 항목의 "decision"을 채운 뒤 `mn4-librarian tags apply-file-actions <경로>`로 적용하세요.')
    print('  - folder_mismatch: decision을 "move"로 (target_category/target_subcategory 확인/수정) 또는 "skip"')
    print('  - duplicate_*: decision을 "keep"으로 하고 keep_path에 남길 파일 경로를 적기 (나머지는 삭제됨), 또는 "skip"')
    return 0


# ---------------------------------------------------------------------------
# tags apply-file-actions: 검토된 JSON대로 실제 파일 이동/삭제 + 태그 갱신
# ---------------------------------------------------------------------------

def cmd_apply_file_actions(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검토된 JSON대로 파일 이동/삭제 + 태그 갱신을 실행한다")
    parser.add_argument("reviewed_json", type=Path)
    args = parser.parse_args(argv)

    data = json.loads(args.reviewed_json.read_text(encoding="utf-8"))
    st = settings.get_settings()

    safety.prepare_for_write(st.mn4_db_path)
    summary = safety.with_write_transaction(st.mn4_db_path, lambda con: _apply_file_actions(con, st, data))
    print(summary)
    return 0


def _apply_file_actions(con: sqlite3.Connection, st: settings.Settings, data: dict) -> str:
    tag_index = mn4_db.TagIndex(con)
    folder_taxonomy = taxonomy.scan(st.library_dir, taxonomy.exclude_for(st))
    known_major_names = set(folder_taxonomy.keys())
    books = mn4_db.iter_library_books(con)
    books_by_key = {f"{b.rel_dir}/{b.file}": b for b in books}

    moved, deleted, skipped = 0, 0, 0
    for item in data.get("items", []):
        decision = item.get("decision")
        kind = item.get("kind")

        if kind == "folder_mismatch":
            if decision != "move":
                skipped += 1
                continue
            book = books_by_key.get(item["file"])
            major, sub = item.get("target_category"), item.get("target_subcategory")
            if book is None or not major:
                skipped += 1
                continue
            mid = tag_index.get_or_create_root_tag(major)
            sid = tag_index.get_or_create_tag(sub, mid) if sub else None
            mn4_db.move_book_file(con, book, st.library_dir, major, sub or "General")
            ids = _replace_major_sub_tags(tag_index, known_major_names, book, mid, sid)
            mn4_db.set_taglist(con, book, ids)
            moved += 1

        elif kind in ("duplicate_md5", "duplicate_filename"):
            if decision != "keep":
                skipped += 1
                continue
            keep_path = item.get("keep_path")
            for candidate in item.get("candidates", []):
                if candidate == keep_path:
                    continue
                book = books_by_key.get(candidate)
                if book is not None:
                    mn4_db.delete_book_file(st.library_dir, book)
                    deleted += 1
        else:
            skipped += 1

    return f"이동 {moved}건, 삭제 {deleted}건, 건너뜀 {skipped}건"
