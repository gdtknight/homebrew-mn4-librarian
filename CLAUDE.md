# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

`mn4-librarian`은 MarginNote4 PDF 라이브러리를 위한 CLI 도구. Homebrew로
배포되는 패키지이며 (1) PDF 파일명 표준화/분류 파이프라인과 (2) MarginNote4
SQLite DB의 태그 감사/수정 기능, 두 축으로 구성된다. `claude` CLI가 필수
전제조건이다 (Max 구독 크레딧으로 LLM 호출, API 과금 없음).

**분류체계는 코드에 하드코딩돼 있지 않다.** 각 사용자의 라이브러리 폴더 구조
(대분류/소분류 2단계 디렉토리) 자체가 분류체계이며, `taxonomy.scan()`이 매번
실시간으로 읽는다 — 사용자마다 읽는 책 분야가 다르기 때문. 새 폴더가 생기면
다음 실행부터 자동 반영된다.

## 명령어

```bash
# 개발 환경 준비 (Homebrew용 python@3.12 이상 필요 — tomllib가 stdlib에 있어야 함)
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest tests/
.venv/bin/pytest tests/test_naming.py::test_build_filename_basic   # 단일 테스트
.venv/bin/pip install build && .venv/bin/python -m build --sdist   # CI도 수행 — sdist 구성 확인용 (아래 패키징 주의사항)

# CLI 사용 (설치 후)
mn4-librarian init                                  # 최초 설정 마법사
mn4-librarian ingest [--apply] [--allow-new-major]   # 신규 PDF 분류+이름변경+이동
mn4-librarian migrate [--apply] [--limit N] [--workers N] [--revert <manifest>]
mn4-librarian fill-years [--apply] [--workers N]
mn4-librarian tags audit                             # 읽기 전용 감사
mn4-librarian tags fix [--apply]                     # 파일 안 건드리는 안전한 수정만 자동 적용
mn4-librarian tags review-files                       # 파일 이동/삭제가 필요한 항목 검토용 JSON 생성
mn4-librarian tags apply-file-actions <reviewed.json> # 검토된 JSON대로 실제 적용
```

전부 기본은 dry-run, `--apply`로 실제 반영. 설정은
`~/.config/mn4-librarian/config.toml`(라이브러리 경로, 인박스 경로, MN4 DB
경로), 로그/백업은 `~/.config/mn4-librarian/{logs,backups}/`.

## 아키텍처

```
src/mn4_librarian/
  settings.py     사용자별 경로 설정 (config.toml 로드/저장 + init 마법사)
  taxonomy.py     library_dir 폴더 구조 → {대분류: [소분류...]} 실시간 스캔 (하드코딩 없음)
  config.py       기계적 상수만 (파일명 sanitize, CLAUDE_* 등) — 분류체계/경로 없음
  pdf_extract.py  pypdf로 메타데이터 + 앞부분 페이지 텍스트(PdfSignal) 추출
  naming.py       BookMeta -> 표준 파일명 조립 (기존 파일명을 재분리하지 않고 항상 새로 조립)
  llm_parse.py    claude CLI(-p, subprocess) 호출 — 분류/재파싱/연도추출/폴더-내용 일치판단
  ingest.py       New Documents -> 분류 -> 표준 파일명 -> 라이브러리 이동
  migrate.py      기존 라이브러리 파일명만 일괄 정리 (폴더 위치는 신뢰)
  fill_years.py   연도 미확인 파일만 깊이 스캔해서 연도 보완
  mn4_db.py       MN4 SQLite 접근 계층 (TagIndex: 태그 조회/생성/rename/병합, 책 조회/이동)
  safety.py       MN4 프로세스 체크 + WAL checkpoint 백업 + 트랜잭션(롤백/integrity_check)
  tags_audit.py   읽기 전용 감사 (drift/미태깅/폴더불일치/중복태그/중복파일 등 8종)
  tags_fix.py     안전 자동수정(DB만) + 파일조치 검토큐(review-files/apply-file-actions)
  cli.py          argparse 진입점, 서브커맨드 dispatch
```

**핵심 흐름 (파일명 파이프라인)**: `pdf_extract.extract_signal()` → PDF 신호 →
`llm_parse`(분류/재파싱) → `BookMeta` → `naming.build_filename()`. `ingest`는
`taxonomy.TaxonomyView`로 배치 안에서 분류체계를 계속 재조회한다 —
`--apply` 모드는 앞선 책이 만든 폴더가 실제로 생기므로 자연히 반영되고,
dry-run 모드는 폴더가 안 생기므로 이번 배치에서 제안된 카테고리를 메모리에
누적해 다음 책 프롬프트에 넘긴다(같은 배치의 비슷한 책들이 "ML"/"Machine
Learning"처럼 변종 카테고리를 만들지 않도록).

**신규 대분류 생성 가드**: 대분류가 1개 이상 있는 라이브러리에 LLM이 새 대분류를
제안하면 `--allow-new-major` 없이는 거부한다(기존 카테고리와 헷갈릴 위험).
대분류가 0개인 완전히 빈 라이브러리에서는 비교 대상이 없으므로 자유롭게 생성
허용 — 안 그러면 최초 사용자가 아무것도 못 하게 막힌다.

**핵심 흐름 (태그 관리)**: `tags_audit.run_audit()`이 `taxonomy.scan()`(폴더
구조, 진실의 원천) vs MN4 DB 태그 계층을 비교하고 책 단위 문제(미태깅/대분류
누락/폴더-태그 불일치/중복 파일)를 찾는다. `tags_fix.py`는 이를 두 갈래로
나눈다:
- **파일을 안 건드리는 수정**(태그 생성/병합/교정)은 `judge_folder_match()`로
  "폴더가 책 내용과 맞는지" LLM 판단을 거쳐 맞으면 `tags fix --apply`가 자동
  적용
- **파일을 건드리는 수정**(폴더 자체가 틀림 → 이동, 중복 파일 → 삭제)은 절대
  한 번에 자동 실행하지 않는다. `tags review-files`가 검토용 JSON을 만들고,
  사람이 `decision`/`target_category`/`keep_path` 등을 채운 뒤
  `tags apply-file-actions`로만 실행된다

**DB 쓰기 안전장치 (safety.py)**: 쓰기 전 항상 (1) MarginNote 4가 실행 중이면
즉시 중단 — WAL을 동시에 쓰면 손상 위험, (2) `PRAGMA wal_checkpoint(TRUNCATE)`
후 타임스탬프 백업, (3) 트랜잭션 — 예외 시 커밋 안 하고 종료(자동 롤백), 성공
시 커밋 후 `PRAGMA integrity_check`.

**LLM 인증**: `llm_parse.call_claude()`는 `ANTHROPIC_API_KEY=""`로 환경변수를
덮어써 API 과금 대신 Claude 구독 크레딧을 사용한다. `MN4_CLAUDE_BIN`
환경변수로 바이너리 경로 오버라이드 가능.

**표준 파일명 형식**: `{연도}-{제목}-{저자1,저자2 외}-{출판사}.pdf` (연도
미확인 시 접두어 생략, 저자 3인 이상은 "저자1,저자2 외"로 축약, 콜론은 파일명에서
제거). `naming.already_new_format()`으로 이미 이 형식인 파일을 판별해 마이그레이션
대상에서 제외한다.

**migrate 안전장치**: `_content_mismatch()`가 PDF 실제 내용(제목/저자)과 기존
파일명이 크게 다르면(제목 단어 겹침 30% 미만이거나 저자명이 파일명에 전혀
없음) `needs_review=True`로 표시해 `--apply`에서도 자동 제외한다. `--apply`
실행 시 되돌리기용 매니페스트(`--revert`)를 남긴다. `ThreadPoolExecutor`
배치 처리 중 10건마다 체크포인트를 저장해 중간에 죽어도 결과가 남는다.

## 테스트

테스트는 `claude` CLI나 실제 MN4 DB를 쓰지 않는다. `test_tags_audit.py`는
`tmp_path`에 MN4의 Core Data 스키마(`ZBOOK`/`ZBOOKCONFIG`/`ZBOOKTAG`/
`Z_PRIMARYKEY`)를 재현한 SQLite와 라이브러리 폴더를 만들어 쓴다. 태그명은
`$$$CATEGORY1$$$` 접두어, 태그-책 링크는 `$$$MNDOCLINK$$$...` 형식이므로
픽스처를 추가할 때 이 형식을 따라야 한다. LLM을 호출하는 경로(`llm_parse`를
쓰는 ingest/migrate/fill_years/tags_fix)는 현재 테스트가 없다.

## 릴리스 절차

**이 리포 자체가 Homebrew tap이다** (별도 `homebrew-*` 리포 없음). 리포 이름이
`homebrew-` 접두어가 아니므로 사용자는 `brew tap gdtknight/mn4-librarian
https://github.com/gdtknight/mn4-librarian`처럼 URL을 명시해 tap한다. 따라서
`Formula/mn4-librarian.rb`를 `main`에 push하는 것이 곧 배포다.

릴리스는 커밋 2개로 나뉜다:
1. `chore: Bump version to X.Y.Z` — `pyproject.toml`의 `version`과
   `src/mn4_librarian/__init__.py`의 `__version__`을 **둘 다** 올린다
   (`--version` 출력은 `__init__.py` 값을 쓰고, Formula의 `test` 블록이 이를 검사)
2. GitHub에 `vX.Y.Z` 태그/릴리스를 만든 뒤
   `chore(formula): Point at vX.Y.Z release tarball` — `Formula/mn4-librarian.rb`의
   `url`과 `sha256`을 새 태그 tarball로 갱신

## 주의사항

- macOS(APFS)는 한글 파일명을 NFD로 저장한다. 파일명 비교 시 `naming.nfc()`로
  항상 NFC 정규화 후 비교해야 한다 (안 하면 육안상 같은 한글도 오탐 발생).
- 저작권 페이지에 여러 판의 연도가 나열된 경우(`First edition 1996, ...,
  Twelfth edition 2021`), 초판이 아니라 파일명/제목의 판수와 일치하는 연도를
  써야 한다 — LLM 프롬프트에 이 규칙이 명시되어 있다.
- 손상되었거나 암호화된 PDF는 `pdf_extract.py`에서 넓게 예외를 잡아 배치 전체가
  죽지 않도록 한다.
- MN4는 파일명이 바뀐 뒤에도 예전 `ZBOOKCONFIG` 행을 정리하지 않고 같은 MD5에
  여러 개 남겨두는 경우가 있다(고아 행). `mn4_db.iter_library_books()`는 파일명과
  `ZTITLE`이 일치하는 행을 "현재" 행으로 우선 채택하고 없으면 가장 최근(`Z_PK`
  최대) 행을 쓴다.
- `settings.Settings.inbox_dir`이 `library_dir` 바로 아래 중첩된 구성이면
  (예: 인박스 폴더가 라이브러리 루트 안에 있음) `taxonomy.exclude_for()`로
  분류체계 스캔에서 제외해야 한다 — 안 그러면 인박스 폴더 자체가 대분류로
  오인된다.
- 패키징 시 리포지토리 루트에 사용자의 실제 iCloud 라이브러리를 가리키는
  심볼릭 링크가 있을 수 있다. `pyproject.toml`의 `[tool.hatch.build.targets.sdist]`는
  `only-include`로 화이트리스트를 명시해야 한다 — `include`(추가 방식)만 쓰면
  hatchling이 심볼릭 링크를 따라가거나 프로젝트 전체에서 `LICENSE` 파일을
  긁어가는 등 의도치 않은 파일이 sdist에 딸려 들어간다(실제로 한 번 11GB
  tar.gz가 만들어진 적 있음).
