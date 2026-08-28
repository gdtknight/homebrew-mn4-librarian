# 대화형(REPL) 모드 규칙 (mn4-librarian 전용)

`mn4-librarian`을 인자 없이 실행하면 프롬프트가 뜨고, 그 안에서 기존
서브커맨드를 반복 입력하며 Tab으로 자동완성할 수 있는 대화형 모드를
추가한다. 아래는 구현 시 지켜야 할 설계 기준이다.

## 의존성

- 표준 라이브러리 `cmd` + `readline`만 쓴다. `prompt_toolkit` 같은 무거운
  서드파티 의존성을 추가하지 않는다 — 이 프로젝트는 현재 런타임 의존성이
  `pypdf` 하나뿐이고(`design-principles.md`의 YAGNI 항목 참고), Homebrew
  패키징 특성상 의존성이 늘어나면 설치 크기·빌드 실패 지점이 함께 늘어난다.
- macOS 시스템 Python은 GNU readline이 아니라 libedit 기반 `readline`을 쓴다.
  탭 바인딩(`bind ^I rl_complete`)이 GNU readline과 다르므로, `readline.__doc__`
  등으로 libedit 여부를 확인해 두 경우 모두 동작하게 만든다 — 반드시 실제
  macOS 환경(brew python, 시스템 python 양쪽)에서 탭 완성을 눈으로 확인한다.

## 명령 디스패치는 기존 로직을 재사용한다

- REPL에서 한 줄을 받으면 `shlex.split()`으로 토큰화한 뒤, 기존 `main()`이
  받는 것과 동일한 `argv` 리스트를 만들어 **동일한 디스패치 함수**에 넘긴다.
  ingest/migrate/tags 등의 파싱·분기 로직을 REPL 전용으로 새로 만들지 않는다
  (DRY — `design-principles.md` 참고).
- REPL 안에서도 `_check_claude_cli()`, `_ensure_configured()` 같은 기존 가드는
  그대로 통과해야 한다. REPL이라고 우회하지 않는다.

## 세션 상태

- `taxonomy.scan()`은 "매 실행마다 실시간으로 읽는다"는 것이 이 프로젝트의
  핵심 설계다(루트 CLAUDE.md 참고). REPL은 프로세스가 여러 커맨드에 걸쳐
  오래 살아있으므로, 분류체계나 설정을 세션 시작 시 한 번만 읽어 캐싱하지
  않는다 — 매 명령 실행 시점에 다시 읽어서, 세션 도중 사용자가 폴더를
  추가하거나 `init`을 다시 돌려도 즉시 반영되게 한다.
- `--apply`, `--workers`, `--limit` 같은 플래그는 명령마다 명시적으로 받는다.
  "이전 명령에서 쓴 플래그를 기억해서 다음에도 적용"하는 암묵적 세션 상태를
  만들지 않는다 — 매 실행이 독립적이어야 dry-run 안전장치가 무력화되지 않는다.

## 자동완성 대상

- 최상위 명령(`init`, `ingest`, `migrate`, `fill-years`, `tags`, `help`, `exit`)
- `tags`의 하위명령(`audit`, `fix`, `review-files`, `apply-file-actions`)
- 각 명령의 플래그(`--apply`, `--limit`, `--workers`, `--allow-new-major`, `--revert`)
- `tags apply-file-actions <path>`와 `--revert <manifest>`처럼 경로를 받는
  위치는 파일시스템 경로 완성(`glob` 기반)을 붙인다.
- 새 서브커맨드/플래그가 추가되면 completer 테이블도 같은 커밋에서 갱신한다
  — completer가 실제 명령 목록과 어긋나는 것이 가장 나쁜 UX다.

## 히스토리 & 종료

- 명령 히스토리는 `~/.config/mn4-librarian/repl_history`에 저장한다(기존
  `~/.config/mn4-librarian/{logs,backups}/` 배치를 따르는 위치).
- `exit`, `quit`, Ctrl-D(EOF)로 정상 종료한다.
- REPL 도중 Ctrl-C는 **현재 입력 줄만 취소**하고 프롬프트로 돌아온다 — 세션
  전체를 종료시키지 않는다. 단, 배치 작업(ingest/migrate 등) 실행 **중**의
  Ctrl-C는 `error-handling.md`의 배치 중단 규칙을 따른다.
