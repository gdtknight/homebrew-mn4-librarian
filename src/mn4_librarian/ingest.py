"""New Documents/ 에 추가된 PDF를 분류하고 표준 파일명으로 mn4 라이브러리에 넣는다.

수동/주기적 실행 전용 (실시간 감시 데몬 아님). 반복 실행해도 안전하도록,
성공적으로 이동한 파일은 더 이상 New Documents에 남지 않는다는 점 자체가
멱등성을 보장한다.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config, settings, taxonomy
from .llm_parse import LlmParseError, classify_new_book
from .naming import build_filename
from .pdf_extract import extract_signal
from .taxonomy import TaxonomyView


@dataclass
class IngestResult:
    source: Path
    dest: Path | None = None
    filename: str | None = None
    category: str | None = None
    subcategory: str | None = None
    is_new_major: bool = False
    is_new_subcategory: bool = False
    needs_new_major_confirmation: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _scan_inbox(inbox_dir: Path) -> list[Path]:
    if not inbox_dir.exists():
        return []
    return sorted(
        p
        for p in inbox_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() == ".pdf"
        and not p.name.startswith(config.IGNORED_FILENAME_PREFIXES)
    )


def _unique_dest(dest: Path) -> Path:
    """동일 파일명이 이미 있으면 ' (2)' 형태로 충돌을 피한다."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def process_one(
    pdf_path: Path,
    apply: bool,
    library_dir: Path,
    taxonomy_view: TaxonomyView,
    had_major_at_start: bool,
    allow_new_major: bool,
) -> IngestResult:
    result = IngestResult(source=pdf_path)
    signal = extract_signal(pdf_path)
    if signal.error and not signal.text and not signal.meta_title:
        result.error = signal.error
        return result

    taxonomy = taxonomy_view.current()
    try:
        meta = classify_new_book(signal, filename_hint=pdf_path.name, taxonomy=taxonomy)
    except LlmParseError as exc:
        result.error = str(exc)
        return result

    if not meta.title:
        result.error = f"필수 필드 누락 (title): {meta}"
        return result
    # authors/publisher가 비어도 실패로 치지 않는다 — build_filename이 "미상"으로 채움.

    is_new_major = meta.category not in taxonomy
    if is_new_major and had_major_at_start and not allow_new_major:
        result.needs_new_major_confirmation = True
        result.category = meta.category
        result.subcategory = meta.subcategory
        result.error = (
            f"새 대분류 제안됨('{meta.category}') — 기존 라이브러리에 새 대분류를 추가하려면 "
            "--allow-new-major 옵션을 붙여 다시 실행하세요."
        )
        return result

    taxonomy_view.record_proposal(meta.category, meta.subcategory)

    filename = build_filename(meta)
    dest_dir = library_dir / meta.category / meta.subcategory
    dest = _unique_dest(dest_dir / filename)

    result.filename = filename
    result.category = meta.category
    result.subcategory = meta.subcategory
    result.is_new_major = is_new_major
    result.is_new_subcategory = meta.subcategory not in taxonomy.get(meta.category, [])
    result.dest = dest

    if apply:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pdf_path), str(dest))

    return result


def run(apply: bool, allow_new_major: bool) -> list[IngestResult]:
    st = settings.get_settings()
    taxonomy_view = TaxonomyView(st.library_dir, taxonomy.exclude_for(st))
    had_major_at_start = taxonomy_view.has_any_major()

    results = []
    for pdf_path in _scan_inbox(st.inbox_dir):
        results.append(
            process_one(pdf_path, apply, st.library_dir, taxonomy_view, had_major_at_start, allow_new_major)
        )
    return results


def _print_report(results: list[IngestResult], apply: bool) -> None:
    if not results:
        print("New Documents에 처리할 PDF가 없습니다.")
        return

    verb = "이동함" if apply else "이동 예정"
    ok = [r for r in results if r.ok]
    needs_major = [r for r in results if r.needs_new_major_confirmation]
    failed = [r for r in results if not r.ok and not r.needs_new_major_confirmation]

    for r in ok:
        tags = []
        if r.is_new_major:
            tags.append("신규 대분류")
        if r.is_new_subcategory:
            tags.append("신규 소분류")
        tag_note = f"  [{', '.join(tags)}]" if tags else ""
        print(f"[{verb}] {r.source.name}{tag_note}\n  -> {r.category}/{r.subcategory}/{r.filename}")
    for r in needs_major:
        print(f"[확인필요] {r.source.name}\n  -> {r.error}")
    for r in failed:
        print(f"[실패] {r.source.name}\n  -> {r.error}")

    print(f"\n총 {len(results)}건 — 성공 {len(ok)}, 확인필요 {len(needs_major)}, 실패 {len(failed)}")
    if not apply and ok:
        print("실제로 옮기려면 --apply 옵션을 붙여 다시 실행하세요.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="New Documents PDF 분류/이름변경 적재")
    parser.add_argument("--apply", action="store_true", help="실제로 파일을 이동한다 (기본은 dry-run)")
    parser.add_argument(
        "--allow-new-major",
        action="store_true",
        help="이미 대분류가 있는 라이브러리에 새 대분류를 추가로 생성하도록 허용",
    )
    args = parser.parse_args(argv)

    results = run(apply=args.apply, allow_new_major=args.allow_new_major)
    _print_report(results, apply=args.apply)
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
