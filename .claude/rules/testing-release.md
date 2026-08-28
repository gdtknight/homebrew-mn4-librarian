# 테스트 & 릴리스 체크리스트 (mn4-librarian 전용)

## 커밋 전

- `naming.py`, `tags_audit.py`처럼 순수 로직 모듈을 변경하면 대응하는
  `tests/test_*.py`도 같은 커밋에 포함한다(로직과 테스트를 분리 커밋하지
  않는다 — 다만 "테스트 파일만" 추가/수정하는 커밋은 전역 규칙의 `test`
  타입을 그대로 쓴다).
- `.venv/bin/pytest tests/` 통과 없이 커밋하지 않는다.
- `safety.py`를 건드리는 변경(백업/트랜잭션/MN4 프로세스 체크)은 실제 MN4
  DB 스키마를 흉내낸 sqlite 픽스처(`tests/test_tags_audit.py`의 SCHEMA
  패턴)로 검증한다 — 모킹으로 대체하지 않는다.

## 릴리스

1. pyproject.toml 버전 bump 커밋 (`chore: Bump version to X.Y.Z`)
2. GitHub Release 태그 + tarball 생성
3. Formula/*.rb의 url/sha256 갱신 커밋 (`chore(formula): Point at vX.Y.Z release tarball`)
4. CI(`.github/workflows/ci.yml`, macos-latest)가 두 커밋 모두 통과하는지 확인

## sdist 패키징

- 새 모듈을 `src/mn4_librarian/`에 추가하는 것은 `pyproject.toml`의
  `[tool.hatch.build.targets.sdist].only-include`가 이미 디렉토리 단위로
  잡혀 있어 안전하다. 단, 최상위에 새로운 배포 대상 디렉토리(예: 문서 자산,
  마이그레이션 스크립트)를 추가하면 이 화이트리스트를 함께 갱신해야 한다 —
  안 그러면 sdist에서 조용히 누락된다.
