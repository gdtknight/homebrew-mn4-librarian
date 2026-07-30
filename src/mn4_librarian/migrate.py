"""기존 mn4 라이브러리(이미 분류된 파일들)의 파일명을 새 표준 형식으로 일괄 변경.

카테고리 폴더 위치는 건드리지 않는다 — 폴더 배치가 곧 기존 분류이므로 그대로
신뢰하고, 파일명만 {연도}-{제목}-{저자1,저자2 외}-{출판사}.pdf 로 바꾼다.

기본은 dry-run이며, 결과를 로그 디렉토리에 JSON 리포트로 남긴다. --apply 시에는
실제 rename과 함께 되돌리기용 매니페스트(old_path -> new_path)를 같이 남긴다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import config, settings
from .llm_parse import LlmParseError, reparse_existing_book
from .naming import BookMeta, already_new_format, build_filename, nfc
from .pdf_extract import extract_signal


@dataclass
class MigrateResult:
    path: str
    old_name: str
    new_name: str | None = None
    error: str | None = None
    needs_review: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.new_name is not None

    @property
    def safe_to_apply(self) -> bool:
        return self.ok and not self.needs_review


_WORD_RE = re.compile(r"[a-zA-Z가-힣0-9]+")


def _content_mismatch(old_stem: str, meta: BookMeta) -> bool:
    """PDF 실제 내용에서 뽑은 제목·저자가 기존 파일명과 거의 겹치지 않으면,
    파일명과 실제 내용이 다른 책일 가능성(오분류/파일 뒤바뀜)으로 보고 사람
    확인을 요구한다. 제목만으로는 "Grokking Algorithms" vs "Grokking AI
    Algorithms"처럼 다른 책인데도 단어가 겹치는 경우를 못 잡아내므로, 저자
    이름이 기존 파일명에 전혀 등장하지 않는 것도 별도 신호로 본다.

    macOS(APFS)는 한글 파일명을 NFD로 저장하므로 비교 전 반드시 NFC로
    정규화한다 — 안 하면 육안상 같은 한글도 다른 코드포인트로 취급되어
    오탐(false positive)이 대량 발생한다.
    """
    old_norm = nfc(old_stem).lower()
    old_words = set(_WORD_RE.findall(old_norm))

    new_title_words = set(_WORD_RE.findall(nfc(meta.title).lower()))
    if not new_title_words:
        return True
    title_overlap = len(old_words & new_title_words) / len(new_title_words)

    author_hit = any(nfc(a).lower().replace(" ", "") in old_norm.replace(" ", "") for a in meta.authors)

    return title_overlap < 0.3 or not author_hit


def _scan_library(library_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in library_dir.rglob("*.pdf")
        if not p.name.startswith(config.IGNORED_FILENAME_PREFIXES)
        and not already_new_format(p.name)
    )


def _unique_new_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def process_one(pdf_path: Path) -> MigrateResult:
    result = MigrateResult(path=str(pdf_path), old_name=pdf_path.name)
    try:
        signal = extract_signal(pdf_path)
        if signal.error and not signal.text and not signal.meta_title:
            result.error = signal.error
            return result

        try:
            meta = reparse_existing_book(signal, existing_filename_stem=pdf_path.stem)
        except LlmParseError as exc:
            result.error = str(exc)
            return result

        if not meta.title:
            result.error = f"필수 필드 누락 (title): {meta}"
            return result
        # authors/publisher가 비어도 실패로 치지 않는다 — 발표자료·솔루션북처럼
        # 정식 출판사/저자 개념이 약한 문서가 있다 (build_filename이 "미상"으로 채움).

        result.new_name = build_filename(meta)
        if _content_mismatch(pdf_path.stem, meta):
            result.needs_review = True
        return result
    except Exception as exc:  # noqa: BLE001 - 배치 작업 중 한 파일 실패로 전체가 죽으면 안 됨
        result.error = f"예상 못한 오류: {exc!r}"
        return result


def run(paths: list[Path], workers: int, checkpoint_path: Path | None = None) -> list[MigrateResult]:
    results: list[MigrateResult] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_one, p): p for p in paths}
        done = 0
        total = len(paths)
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  진행: {done}/{total}", file=sys.stderr)
                if checkpoint_path is not None:
                    # 배치 중간에 죽어도 여기까지의 결과는 남기기 위한 체크포인트.
                    checkpoint_path.write_text(
                        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
    # 원래 스캔 순서로 재정렬 (진행 로그는 완료 순, 리포트는 안정적인 순서로)
    order = {str(p): i for i, p in enumerate(paths)}
    results.sort(key=lambda r: order[r.path])
    return results


def apply_renames(results: list[MigrateResult]) -> list[dict]:
    manifest = []
    for r in results:
        if not r.safe_to_apply:
            continue
        old_path = Path(r.path)
        intended_new_path = old_path.with_name(r.new_name)

        if not old_path.exists():
            if intended_new_path.exists():
                # 이전 --apply-from 실행에서 이미 처리된 항목을 재적용하는
                # 경우(리포트를 재사용/병합할 때 옛 경로가 남아있을 수 있음).
                # 조용히 건너뛴다 — 실제로 다시 할 일이 없다.
                continue
            print(f"[건너뜀] 원본이 없음(이미 처리됐거나 이동된 듯): {old_path}")
            continue

        if intended_new_path == old_path:
            # 새로 계산한 이름이 기존 이름과 완전히 같은 경우(부제 없음+저자 2인
            # 이하+연도 미확인 등) — 실제로 바꿀 게 없다. _unique_new_path에
            # 넘기면 "자기 자신과 충돌"로 오인해 불필요하게 " (2)"를 붙이므로
            # 여기서 걸러낸다.
            continue

        new_path = _unique_new_path(intended_new_path)
        old_path.rename(new_path)
        manifest.append({"old_path": str(old_path), "new_path": str(new_path)})
    return manifest


def revert(manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    reverted, missing = 0, 0
    for entry in reversed(manifest):
        new_path = Path(entry["new_path"])
        old_path = Path(entry["old_path"])
        if not new_path.exists():
            print(f"[건너뜀] 대상이 없음: {new_path}")
            missing += 1
            continue
        new_path.rename(old_path)
        reverted += 1
    print(f"되돌림 {reverted}건, 건너뜀(대상 없음) {missing}건")
    return 0


def retry_failed(prior_report_path: Path, workers: int) -> list[MigrateResult]:
    """이전 dry-run 리포트에서 실패(error)한 파일만 다시 시도하고, 성공했던
    항목은 그대로 재사용해 병합한다. rate limit 등으로 배치 뒷부분이 무더기로
    실패했을 때 전체를 처음부터 다시 돌리지 않기 위함."""
    prior = json.loads(prior_report_path.read_text(encoding="utf-8"))

    retry_paths = [Path(r["path"]) for r in prior if r.get("error")]
    print(f"이전 리포트: 총 {len(prior)}건 중 실패 {len(retry_paths)}건 재시도", file=sys.stderr)
    if not retry_paths:
        return [_result_from_dict(r) for r in prior]

    retried = run(retry_paths, workers=workers)
    retried_by_path = {r.path: r for r in retried}

    merged: list[MigrateResult] = []
    for r in prior:
        if r["path"] in retried_by_path:
            merged.append(retried_by_path[r["path"]])
        else:
            merged.append(_result_from_dict(r))
    return merged


def _result_from_dict(d: dict) -> MigrateResult:
    return MigrateResult(
        path=d["path"],
        old_name=d["old_name"],
        new_name=d.get("new_name"),
        error=d.get("error"),
        needs_review=d.get("needs_review", False),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="기존 mn4 라이브러리 파일명 일괄 마이그레이션")
    parser.add_argument("--apply", action="store_true", help="실제로 rename한다 (기본은 dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="테스트용: 처음 N개만 처리")
    parser.add_argument("--workers", type=int, default=4, help="동시 LLM 호출 수 (기본 4)")
    parser.add_argument("--revert", type=Path, default=None, help="지정한 매니페스트로 되돌리기")
    parser.add_argument(
        "--retry-failed", type=Path, default=None, help="지정한 이전 dry-run 리포트의 실패 건만 재시도"
    )
    parser.add_argument(
        "--apply-from",
        type=Path,
        default=None,
        help="LLM을 다시 호출하지 않고, 지정한 이전 dry-run 리포트 내용 그대로 적용한다 "
        "(사람이 검토 후 needs_review 값을 직접 고친 리포트를 적용할 때 사용)",
    )
    args = parser.parse_args(argv)

    if args.revert:
        return revert(args.revert)

    st = settings.get_settings()
    log_dir = settings.logs_dir()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = log_dir / f"migrate-dryrun-{ts}.json"

    if args.apply_from:
        prior = json.loads(args.apply_from.read_text(encoding="utf-8"))
        results = [_result_from_dict(r) for r in prior]
        return _finish(results, 0.0, args.apply_from, apply=True, ts=ts, log_dir=log_dir)

    if args.retry_failed:
        t0 = time.time()
        results = retry_failed(args.retry_failed, workers=args.workers)
        elapsed = time.time() - t0
        return _finish(results, elapsed, report_path, apply=args.apply, ts=ts, log_dir=log_dir)

    paths = _scan_library(st.library_dir)
    if args.limit:
        paths = paths[: args.limit]

    if not paths:
        print("마이그레이션 대상이 없습니다 (이미 모두 새 형식이거나 라이브러리가 비어있음).")
        return 0

    print(f"대상 {len(paths)}건, 동시 실행 {args.workers}개로 처리 시작...", file=sys.stderr)
    t0 = time.time()
    results = run(paths, workers=args.workers, checkpoint_path=report_path)
    elapsed = time.time() - t0
    return _finish(results, elapsed, report_path, apply=args.apply, ts=ts, log_dir=log_dir)


def _finish(
    results: list[MigrateResult], elapsed: float, report_path: Path, apply: bool, ts: str, log_dir: Path
) -> int:
    ok = [r for r in results if r.ok]
    safe = [r for r in ok if r.safe_to_apply]
    review = [r for r in ok if r.needs_review]
    failed = [r for r in results if not r.ok]
    no_year = [r for r in safe if r.new_name and not r.new_name[:4].isdigit()]

    report_path.write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n소요 시간: {elapsed:.0f}s ({elapsed/60:.1f}분)")
    print(
        f"총 {len(results)}건 — 적용 가능 {len(safe)}, 확인 필요 {len(review)}, "
        f"실패 {len(failed)}, (적용 가능 중) 연도 미확인 {len(no_year)}"
    )
    print(f"전체 리포트: {report_path}")

    print("\n--- 샘플 (앞 10건, 적용 가능만) ---")
    for r in safe[:10]:
        print(f"[OK] {r.old_name}\n  -> {r.new_name}")

    if review:
        print(f"\n--- 확인 필요: 파일명과 실제 PDF 내용이 크게 다름 ({len(review)}건) ---")
        print("    (--apply 를 실행해도 이 파일들은 자동으로 이름이 바뀌지 않습니다)")
        for r in review:
            print(f"  {r.old_name}\n    실제 내용 기준 제목: {r.new_name}")

    if failed:
        print(f"\n--- 실패 목록 ({len(failed)}건) ---")
        for r in failed:
            print(f"  {r.old_name}: {r.error}")

    if apply:
        manifest = apply_renames(results)
        manifest_path = log_dir / f"migrate-applied-{ts}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{len(manifest)}건 실제 rename 완료. 되돌리기용 매니페스트: {manifest_path}")
    else:
        print("\n실제로 이름을 바꾸려면 --apply 옵션을 붙여 다시 실행하세요.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
