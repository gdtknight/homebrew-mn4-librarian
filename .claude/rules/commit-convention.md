# Commit Convention (mn4-librarian 전용)

기본 형식·타입 분류·Chris Beams 7원칙은 전역 규칙(`~/.claude/rules/git-commit.md`)을
그대로 따른다. 이 문서는 이 프로젝트에서만 쓰는 스코프 표와 릴리스 커밋 패턴만
정의한다.

## 스코프 표

| 스코프 | 대상 |
|--------|------|
| `naming` | src/mn4_librarian/naming.py — 파일명 조립 로직 |
| `pdf` | pdf_extract.py — PDF 메타데이터/신호 추출 |
| `llm` | llm_parse.py — claude CLI 호출, 분류/재파싱 프롬프트 |
| `ingest` | ingest.py |
| `migrate` | migrate.py |
| `fill-years` | fill_years.py |
| `tags` | tags_audit.py, tags_fix.py |
| `db` | mn4_db.py |
| `safety` | safety.py — 백업/트랜잭션/MN4 프로세스 체크 |
| `taxonomy` | taxonomy.py |
| `settings` | settings.py — config.toml, init 마법사 |
| `cli` | cli.py — 서브커맨드 dispatch |
| `formula` | Formula/*.rb — Homebrew 배포 |
| 생략 | 여러 모듈에 걸친 변경, 루트 설정 파일(pyproject.toml 등) |

새 모듈이 `src/mn4_librarian/`에 추가되면 이 표도 같은 커밋 또는 뒤따르는
`docs` 커밋으로 함께 갱신한다.

## 릴리스 커밋 패턴

버전 릴리스는 기존 히스토리 관례대로 항상 두 개의 별도 커밋으로 나눈다:

1. `chore: Bump version to X.Y.Z` — pyproject.toml 버전 필드만 변경
2. `chore(formula): Point at vX.Y.Z release tarball` — Formula/*.rb의 url/sha256 갱신

두 변경은 원인이 다르므로(버전 정의 vs 배포 아티팩트 갱신) 한 커밋에 섞지
않는다 — Atomic Commit 원칙.
