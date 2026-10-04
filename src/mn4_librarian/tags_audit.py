"""MarginNote4 DB(태그) 감사 — 읽기 전용.

이번 세션에 실제 라이브 DB를 대상으로 손으로 짠 조사 스크립트(full_review.py
등)에서 확인한 8가지 점검을 일반화했다. DB는 절대 쓰지 않는다
(mn4_db.connect_readonly로 연다).
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import config, mn4_db, settings, taxonomy


def _normalize(name: str) -> str:
    return name.lower().replace("'", "").replace(".", "").replace(",", "").replace(" ", "").replace("-", "")


@dataclass
class AuditReport:
    # 폴더 구조(taxonomy.scan) vs DB 태그 계층 drift
    missing_in_db: list[tuple[str, str | None]] = field(default_factory=list)
    missing_in_folder: list[tuple[str, str | None]] = field(default_factory=list)
    # 0권인 소분류 태그
    unused_subcategory_tags: list[tuple[str, str]] = field(default_factory=list)
    # 정규화하면 같은 이름인 출판사/저자 태그 (오타/표기 차이 의심)
    near_duplicate_tags: dict[str, list[str]] = field(default_factory=dict)
    # ZBOOK이 가리키는데 디스크에 없는 파일
    missing_files: list[str] = field(default_factory=list)
    # 디스크에는 있는데 MN4에 전혀 등록 안 된 파일 (ZBOOK 자체가 없음)
    unregistered_files: list[str] = field(default_factory=list)
    # 완전 동일 MD5 / 동일 파일명-다른 MD5
    exact_md5_duplicates: dict[str, list[str]] = field(default_factory=dict)
    same_name_diff_md5: dict[str, list[str]] = field(default_factory=dict)
    # 책 단위 문제
    untagged_books: list[str] = field(default_factory=list)
    no_major_books: list[tuple[str, list[str]]] = field(default_factory=list)
    folder_tag_mismatches: list[dict] = field(default_factory=list)

    def is_clean(self) -> bool:
        return not any(
            [
                self.missing_in_db,
                self.missing_in_folder,
                self.unused_subcategory_tags,
                self.near_duplicate_tags,
                self.missing_files,
                self.unregistered_files,
                self.exact_md5_duplicates,
                self.same_name_diff_md5,
                self.untagged_books,
                self.no_major_books,
                self.folder_tag_mismatches,
            ]
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _check_taxonomy_drift(
    tag_index: mn4_db.TagIndex, folder_taxonomy: dict[str, list[str]]
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str | None]]]:
    db_taxonomy: dict[str, list[str]] = {}
    for root_id in tag_index.roots():
        name = tag_index.name_by_id[root_id]
        if name in mn4_db.META_ROOT_TAG_NAMES:
            continue
        db_taxonomy[name] = [tag_index.name_by_id.get(c, c) for c in tag_index.links_by_id[root_id]]

    missing_in_db: list[tuple[str, str | None]] = []
    for major, subs in folder_taxonomy.items():
        if major not in db_taxonomy:
            missing_in_db.append((major, None))
            continue
        for sub in subs:
            if sub not in db_taxonomy[major]:
                missing_in_db.append((major, sub))

    missing_in_folder: list[tuple[str, str | None]] = []
    for major, subs in db_taxonomy.items():
        if major not in folder_taxonomy:
            missing_in_folder.append((major, None))
            continue
        for sub in subs:
            if sub not in folder_taxonomy[major]:
                missing_in_folder.append((major, sub))

    return missing_in_db, missing_in_folder


def _check_unused_subcategories(
    tag_index: mn4_db.TagIndex, folder_taxonomy: dict[str, list[str]], usage: dict[str, int]
) -> list[tuple[str, str]]:
    unused = []
    for major in folder_taxonomy:
        mid = tag_index.find_id(major)
        if mid is None:
            continue
        for child in tag_index.links_by_id[mid]:
            if usage.get(child, 0) == 0:
                unused.append((major, tag_index.name_by_id.get(child, child)))
    return unused


def _check_near_duplicates(tag_index: mn4_db.TagIndex) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for parent_name in ("출판사", "저자"):
        pid = tag_index.find_id(parent_name)
        if pid is None:
            continue
        by_norm: dict[str, list[str]] = defaultdict(list)
        for child in tag_index.links_by_id[pid]:
            name = tag_index.name_by_id.get(child, child)
            by_norm[_normalize(name)].append(name)
        for names in by_norm.values():
            if len(names) > 1:
                result[f"{parent_name}:{names[0]}"] = names
    return result


def _scan_disk_pdfs(library_dir: Path, exclude: frozenset[str]) -> set[str]:
    """library_dir 아래 모든 PDF의 상대경로("대분류/소분류/파일.pdf") 집합.
    MN4 DB에 아예 등록 안 된 파일(ZBOOK 자체가 없음)을 찾는 데 쓴다 — 예를 들어
    라이브러리 폴더에 PDF를 직접 복사해 넣고 아직 MN4로 한 번도 열어보지 않은
    경우, iter_library_books()는 DB 기반이라 이런 파일을 아예 못 본다."""
    result = set()
    for p in library_dir.rglob("*.pdf"):
        if p.name.startswith(config.IGNORED_FILENAME_PREFIXES):
            continue
        rel = p.relative_to(library_dir)
        if rel.parts and rel.parts[0] in exclude:
            continue
        result.add(rel.as_posix())
    return result


def library_last_modified(library_dir: Path, exclude: frozenset[str] = frozenset()) -> datetime:
    """라이브러리 폴더 구조가 마지막으로 바뀐 시각.

    폴더 생성/삭제와 파일 추가/이동/이름변경은 모두 해당 파일이 든 디렉토리의
    mtime을 갱신하므로 디렉토리 mtime만 본다 — 파일 자체의 mtime은 이동해도
    바뀌지 않아 ingest로 옮겨진 PDF를 놓친다."""
    latest = 0.0
    for root, dirs, _files in os.walk(library_dir):
        at_top = Path(root) == library_dir
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(config.IGNORED_FILENAME_PREFIXES) and not (at_top and d in exclude)
        ]
        latest = max(latest, os.stat(root).st_mtime)
    return datetime.fromtimestamp(latest)


def print_freshness(db_modified: datetime, library_modified: datetime) -> None:
    """감사 결과 대부분은 MN4 DB에서 나오는데 ingest/폴더 정리는 DB를 갱신하지
    않는다. 그래서 폴더를 바꾼 직후 재감사해도 결과가 그대로인 이유를 알린다."""
    fmt = "%Y-%m-%d %H:%M"
    print(f"MN4 DB 마지막 변경:          {db_modified.strftime(fmt)}")
    print(f"라이브러리 폴더 마지막 변경: {library_modified.strftime(fmt)}")
    if library_modified > db_modified:
        print(
            "⚠ 라이브러리 폴더가 MN4 DB보다 나중에 바뀌었습니다. ingest/폴더 정리는 MN4 DB를\n"
            "  갱신하지 않으므로, MarginNote 4를 열어 동기화한 뒤 종료하고 다시 감사해야\n"
            "  DB 기반 항목(태그 없는 책, 폴더-태그 불일치 등)에 반영됩니다."
        )
    print()


def run_audit(db_path: Path, library_dir: Path, exclude: frozenset[str] = frozenset()) -> AuditReport:
    con = mn4_db.connect_readonly(db_path)
    try:
        tag_index = mn4_db.TagIndex(con)
        folder_taxonomy = taxonomy.scan(library_dir, exclude)
        books = mn4_db.iter_library_books(con)

        usage: dict[str, int] = defaultdict(int)
        for b in books:
            for tid in b.taglist:
                usage[tid] += 1

        missing_in_db, missing_in_folder = _check_taxonomy_drift(tag_index, folder_taxonomy)
        unused = _check_unused_subcategories(tag_index, folder_taxonomy, usage)
        near_dupes = _check_near_duplicates(tag_index)

        missing_files = []
        md5_map: dict[str, list[str]] = defaultdict(list)
        name_map: dict[str, list[str]] = defaultdict(list)
        for b in books:
            full = library_dir / b.rel_dir / b.file
            if not full.exists():
                missing_files.append(str(full.relative_to(library_dir)))
            md5_map[b.md5].append(f"{b.rel_dir}/{b.file}")
            name_map[b.file].append(f"{b.rel_dir}/{b.file}")

        exact_md5_dup = {k: v for k, v in md5_map.items() if len(v) > 1}
        same_name_diff_md5 = {k: v for k, v in name_map.items() if len(v) > 1}

        known_paths = {f"{b.rel_dir}/{b.file}" for b in books}
        unregistered = sorted(_scan_disk_pdfs(library_dir, exclude) - known_paths)

        known_majors = set(folder_taxonomy.keys())
        sub_to_major: dict[str, str] = {}
        for major, subs in folder_taxonomy.items():
            for sub in subs:
                sub_to_major[sub] = major

        untagged: list[str] = []
        no_major: list[tuple[str, list[str]]] = []
        mismatches: list[dict] = []
        for b in books:
            names = [tag_index.name_by_id.get(t, t) for t in b.taglist]
            if not b.taglist:
                untagged.append(f"{b.rel_dir}/{b.file}")
                continue
            majors_in = [n for n in names if n in known_majors]
            if not majors_in:
                no_major.append((f"{b.rel_dir}/{b.file}", names))
                continue
            subs_in = [n for n in names if n in sub_to_major]
            tmaj = majors_in[0]
            tsub = subs_in[0] if subs_in else None
            if tmaj != b.path_major or (tsub and b.path_sub and tsub != b.path_sub):
                mismatches.append(
                    {"file": b.file, "rel": b.rel_dir, "tag_major": tmaj, "tag_sub": tsub}
                )

        return AuditReport(
            missing_in_db=missing_in_db,
            missing_in_folder=missing_in_folder,
            unused_subcategory_tags=unused,
            near_duplicate_tags=near_dupes,
            missing_files=missing_files,
            unregistered_files=unregistered,
            exact_md5_duplicates=exact_md5_dup,
            same_name_diff_md5=same_name_diff_md5,
            untagged_books=untagged,
            no_major_books=no_major,
            folder_tag_mismatches=mismatches,
        )
    finally:
        con.close()


def save_report(report: AuditReport, logs_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = logs_dir / f"audit-{ts}.json"
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def print_report(report: AuditReport) -> None:
    def section(title: str) -> None:
        print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")

    print("=" * 70)
    print("요약 (조치가 필요한 순서대로)")
    print("=" * 70)
    summary_items = [
        ("디스크에는 있는데 MN4에 전혀 등록 안 된 파일", len(report.unregistered_files)),
        ("태그가 전혀 없는 책", len(report.untagged_books)),
        ("대분류 태그가 없는 책", len(report.no_major_books)),
        ("폴더-태그 불일치", len(report.folder_tag_mismatches)),
        ("중복 파일 (완전 동일 MD5)", len(report.exact_md5_duplicates)),
        ("중복 파일 (동일 파일명, 다른 MD5)", len(report.same_name_diff_md5)),
        ("디스크에 없는 파일을 가리키는 ZBOOK", len(report.missing_files)),
        ("폴더 구조 ↔ DB 태그 drift", len(report.missing_in_db) + len(report.missing_in_folder)),
        ("근접중복 태그명", len(report.near_duplicate_tags)),
        ("사용되지 않는 소분류 태그", len(report.unused_subcategory_tags)),
    ]
    for label, count in summary_items:
        mark = "⚠" if count else "✓"
        print(f"  {mark} {label}: {count}건")

    section(f"디스크에는 있는데 MN4에 전혀 등록 안 된 파일 ({len(report.unregistered_files)}건)")
    if report.unregistered_files:
        for f in report.unregistered_files:
            print(f"  {f}")
        print("  (MarginNote4에서 한 번 열어 등록시킨 뒤 다시 감사하면 아래 '태그 없는 책'으로 잡힙니다)")
    else:
        print("없음")

    section("폴더 구조에는 있는데 DB 태그가 없음 (missing_in_db)")
    if report.missing_in_db:
        for major, sub in report.missing_in_db:
            print(f"  {major}" + (f" / {sub}" if sub else " (대분류 전체)"))
    else:
        print("없음")

    section("DB 태그는 있는데 폴더가 없음 (missing_in_folder)")
    if report.missing_in_folder:
        for major, sub in report.missing_in_folder:
            print(f"  {major}" + (f" / {sub}" if sub else " (대분류 전체)"))
    else:
        print("없음")

    section("사용되지 않는(0권) 소분류 태그")
    if report.unused_subcategory_tags:
        for major, sub in report.unused_subcategory_tags:
            print(f"  {major} / {sub}")
    else:
        print("없음")

    section("근접중복 태그명 (출판사/저자)")
    if report.near_duplicate_tags:
        for names in report.near_duplicate_tags.values():
            print(f"  {names}")
    else:
        print("없음")

    section("디스크에 없는 파일을 가리키는 ZBOOK")
    print("없음" if not report.missing_files else "\n".join(f"  {p}" for p in report.missing_files))

    section("완전 동일 MD5 중복 파일")
    if report.exact_md5_duplicates:
        for md5, entries in report.exact_md5_duplicates.items():
            print(f"  MD5={md5}: {entries}")
    else:
        print("없음")

    section("동일 파일명, 다른 MD5 (버전/사본 차이)")
    if report.same_name_diff_md5:
        for name, entries in report.same_name_diff_md5.items():
            print(f"  {name}: {entries}")
    else:
        print("없음")

    section(f"태그가 전혀 없는 책 ({len(report.untagged_books)}건)")
    for f in report.untagged_books:
        print(f"  {f}")

    section(f"대분류 태그가 없는 책 ({len(report.no_major_books)}건)")
    for f, names in report.no_major_books:
        print(f"  {f}\n    보유 태그: {names}")

    section(f"폴더-태그 불일치 ({len(report.folder_tag_mismatches)}건)")
    for m in report.folder_tag_mismatches:
        print(f"  {m['file']}\n    폴더={m['rel']} / 태그={m['tag_major']}/{m['tag_sub']}")

    print(f"\n{'=' * 70}")
    print("전체 클린" if report.is_clean() else "위 항목들에 대한 조치가 필요합니다 — `mn4-librarian tags fix` 참고")
    print("=" * 70)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MarginNote4 태그 감사 (읽기 전용)")
    parser.parse_args(argv)

    st = settings.get_settings()
    exclude = taxonomy.exclude_for(st)
    report = run_audit(st.mn4_db_path, st.library_dir, exclude)
    print_freshness(mn4_db.last_modified(st.mn4_db_path), library_last_modified(st.library_dir, exclude))
    print_report(report)
    path = save_report(report, settings.logs_dir())
    print(f"\n리포트 저장: {path}")
    return 0 if report.is_clean() else 1
