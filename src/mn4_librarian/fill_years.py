"""연도 접두어가 없는 기존 라이브러리 파일에 연도만 채워 넣는다.

title/authors/publisher는 이미 확정된 상태(파일명 그대로)라고 보고 건드리지
않는다. PDF를 더 깊이(기본보다 많은 페이지) 읽어 초판 출간연도만 다시 찾고,
찾으면 파일명 맨 앞에 "{연도}-"를 붙인다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import config, pdf_extract, settings
from .llm_parse import LlmParseError, find_year_only
from .naming import already_new_format

# 첫 페이지들만 보는 기본 스캔으로 연도를 못 찾은 파일들이라, 더 많은 페이지를
# 읽어 저작권 페이지가 뒤쪽에 있는 경우까지 커버한다.
DEEP_SIGNAL_PAGE_COUNT = 12
DEEP_SIGNAL_MAX_CHARS = 8000


@dataclass
class FillYearResult:
    path: str
    old_name: str
    year: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.year)


def _scan_no_year(library_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in library_dir.rglob("*.pdf")
        if not p.name.startswith(config.IGNORED_FILENAME_PREFIXES) and not already_new_format(p.name)
    )


def _extract_deep_signal(pdf_path: Path) -> pdf_extract.PdfSignal:
    original_pages, original_chars = config.SIGNAL_PAGE_COUNT, config.SIGNAL_MAX_CHARS
    config.SIGNAL_PAGE_COUNT, config.SIGNAL_MAX_CHARS = DEEP_SIGNAL_PAGE_COUNT, DEEP_SIGNAL_MAX_CHARS
    try:
        return pdf_extract.extract_signal(pdf_path)
    finally:
        config.SIGNAL_PAGE_COUNT, config.SIGNAL_MAX_CHARS = original_pages, original_chars


def process_one(pdf_path: Path) -> FillYearResult:
    result = FillYearResult(path=str(pdf_path), old_name=pdf_path.name)
    try:
        signal = _extract_deep_signal(pdf_path)
        if signal.error and not signal.text and not signal.meta_title:
            result.error = signal.error
            return result
        result.year = find_year_only(signal, filename_hint=pdf_path.stem)
        return result
    except LlmParseError as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # noqa: BLE001
        result.error = f"예상 못한 오류: {exc!r}"
        return result


def run(paths: list[Path], workers: int) -> list[FillYearResult]:
    results: list[FillYearResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_one, p): p for p in paths}
        done = 0
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            print(f"  진행: {done}/{len(paths)}", file=sys.stderr)
    order = {str(p): i for i, p in enumerate(paths)}
    results.sort(key=lambda r: order[r.path])
    return results


def apply_years(results: list[FillYearResult]) -> list[dict]:
    manifest = []
    for r in results:
        if not r.ok:
            continue
        old_path = Path(r.path)
        if not old_path.exists():
            continue
        new_path = old_path.with_name(f"{r.year}-{old_path.name}")
        if new_path.exists():
            continue
        old_path.rename(new_path)
        manifest.append({"old_path": str(old_path), "new_path": str(new_path)})
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="연도 미확인 파일에 연도 채워 넣기")
    parser.add_argument("--apply", action="store_true", help="실제로 rename한다 (기본은 dry-run)")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)

    st = settings.get_settings()
    log_dir = settings.logs_dir()

    paths = _scan_no_year(st.library_dir)
    if not paths:
        print("연도 미확인 파일이 없습니다.")
        return 0

    print(f"대상 {len(paths)}건, 페이지 {DEEP_SIGNAL_PAGE_COUNT}까지 확장 스캔...", file=sys.stderr)
    t0 = time.time()
    results = run(paths, workers=args.workers)
    elapsed = time.time() - t0

    found = [r for r in results if r.ok]
    not_found = [r for r in results if r.error is None and not r.year]
    failed = [r for r in results if r.error]

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = log_dir / f"fill-years-{ts}.json"
    report_path.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n소요 시간: {elapsed:.0f}s")
    print(f"총 {len(results)}건 — 연도 찾음 {len(found)}, 여전히 미확인 {len(not_found)}, 실패 {len(failed)}")
    print(f"리포트: {report_path}")

    for r in found:
        print(f"[찾음] {r.old_name} -> {r.year}-")
    if not_found:
        print(f"\n--- 여전히 미확인 ({len(not_found)}건) ---")
        for r in not_found:
            print(f"  {r.old_name}")
    if failed:
        print(f"\n--- 실패 ({len(failed)}건) ---")
        for r in failed:
            print(f"  {r.old_name}: {r.error}")

    if args.apply:
        manifest = apply_years(results)
        manifest_path = log_dir / f"fill-years-applied-{ts}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{len(manifest)}건 실제 적용. 매니페스트: {manifest_path}")
    else:
        print("\n실제로 적용하려면 --apply 옵션을 붙여 다시 실행하세요.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
