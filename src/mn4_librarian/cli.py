"""mn4-librarian CLI 진입점."""
from __future__ import annotations

import shutil
import sys

from . import __version__, fill_years, ingest, migrate, settings, tags_audit, tags_fix

HELP = """\
사용법: mn4-librarian <명령> [옵션...]

  init                                최초 설정 마법사 (라이브러리/신규문서 폴더 지정)

  ingest [--apply] [--allow-new-major]
      New Documents 폴더의 신규 PDF를 분류하고 표준 파일명으로 라이브러리에 넣는다.

  migrate [--apply] [--limit N] [--workers N] [--revert <manifest>]
      기존 라이브러리 전체를 새 파일명 형식으로 마이그레이션한다.

  fill-years [--apply] [--workers N]
      연도 미확인 파일들만 더 깊이 스캔해서 연도를 채워 넣는다.

  tags audit
      MarginNote4 태그를 감사한다 (읽기 전용).

  tags fix [--apply]
      감사에서 발견된 문제 중 파일을 건드리지 않는 안전한 것만 자동 수정한다.

  tags review-files
      폴더 이동/중복파일 삭제처럼 파일을 건드려야 하는 항목을 검토용 JSON으로 만든다.

  tags apply-file-actions <reviewed.json>
      review-files에서 만든 JSON을 사람이 검토·수정한 뒤, 그 결정대로 실제 파일을
      이동/삭제하고 태그를 갱신한다.

  전부 기본은 dry-run이며 --apply를 붙여야 실제로 파일이 이동/변경된다.
"""


def _check_claude_cli() -> None:
    if shutil.which("claude") is None:
        print(
            "claude CLI를 찾을 수 없습니다. mn4-librarian은 분류 작업에 claude CLI가 필요합니다.\n"
            "https://docs.claude.com/claude-code 안내를 따라 설치 후 로그인하세요.",
            file=sys.stderr,
        )
        sys.exit(1)


def _ensure_configured() -> None:
    if settings.load_settings() is None:
        print("설정이 없습니다. 먼저 초기 설정을 진행합니다.\n")
        settings.run_init_wizard()


def _dispatch_tags(rest: list[str]) -> int:
    if not rest:
        print("사용법: mn4-librarian tags <audit|fix|review-files|apply-file-actions> [옵션...]", file=sys.stderr)
        return 1
    sub, sub_rest = rest[0], rest[1:]
    _ensure_configured()

    if sub == "audit":
        return tags_audit.main(sub_rest)
    if sub == "fix":
        _check_claude_cli()
        return tags_fix.cmd_fix(sub_rest)
    if sub == "review-files":
        _check_claude_cli()
        return tags_fix.cmd_review_files(sub_rest)
    if sub == "apply-file-actions":
        return tags_fix.cmd_apply_file_actions(sub_rest)

    print(f"알 수 없는 tags 하위명령: {sub}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help"):
        print(HELP)
        return 0
    if argv[0] in ("-v", "--version"):
        print(f"mn4-librarian {__version__}")
        return 0

    cmd, rest = argv[0], argv[1:]

    if cmd == "init":
        settings.run_init_wizard()
        return 0

    if cmd in ("ingest", "migrate", "fill-years"):
        _check_claude_cli()
        _ensure_configured()
        module = {"ingest": ingest, "migrate": migrate, "fill-years": fill_years}[cmd]
        return module.main(rest)

    if cmd == "tags":
        return _dispatch_tags(rest)

    print(f"알 수 없는 명령: {cmd}\n", file=sys.stderr)
    print(HELP, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
