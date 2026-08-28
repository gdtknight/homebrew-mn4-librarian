# 설계 원칙 적용 기준 (mn4-librarian 전용)

전역 규칙(`~/.claude/rules/engineering.md`)의 SOLID/DRY/YAGNI/KISS를 이
코드베이스의 실제 아키텍처에 어떻게 적용하는지 정의한다. 새 기능 추가나
리팩토링 전에 아래 기준을 먼저 확인한다.

## SRP — 모듈은 정확히 하나의 책임만 진다

- `tags_audit.py`는 읽기 전용이다. 절대 DB에 쓰지 않는다.
- `tags_fix.py`만 DB 쓰기 로직을 가진다. 감사(진단)와 수정(처방)을 한
  함수·모듈에 섞지 않는다.
- `naming.py`는 파일명 "조립"만 한다. 메타데이터 추출(`pdf_extract.py`)이나
  분류 판단(`llm_parse.py`) 로직을 naming.py로 끌어오지 않는다.

## OCP — 분류체계는 데이터이지 코드가 아니다

- 대분류/소분류는 `taxonomy.scan()`이 라이브러리 폴더 구조에서 실시간으로
  읽는다. 새 카테고리 추가는 폴더를 만드는 것만으로 끝나야 한다. 카테고리를
  코드에 하드코딩(enum, if-elif 분기 등)하는 변경은 이 프로젝트의 핵심
  설계를 위반하므로 반려한다.

## DIP — LLM 호출은 llm_parse.py 뒤로 숨긴다

- `claude` CLI를 subprocess로 직접 호출하는 코드는 `llm_parse.py`에만
  존재해야 한다. 다른 모듈은 llm_parse가 노출하는 함수 인터페이스에만
  의존한다 — 호출 방식(subprocess, 향후 API 등)이 바뀌어도 나머지 모듈은
  영향받지 않아야 한다.

## DRY — 파일명 조립은 naming.build_filename()이 유일한 진실

- migrate.py, fill_years.py, ingest.py는 전부 `naming.build_filename()`을
  재사용한다. 파일명 문자열을 조립하는 로직을 다른 곳에 새로 만들지 않는다.
- 단, migrate.py의 "기존 파일명과 실제 내용 비교"(`_content_mismatch`)와
  ingest.py의 "신규 분류 판단"은 겉보기엔 비슷해도 변경 이유가 다른
  우연한 유사성이므로 강제로 통합하지 않는다.

## YAGNI — 1인용 Homebrew CLI 범위를 벗어나는 추상화 금지

- 멀티 유저, 플러그인 시스템, MN4 외 다른 DB 백엔드 지원처럼 현재
  요구사항에 없는 확장 포인트를 미리 만들지 않는다.
- 설정은 `config.toml` 하나로 충분하다 — 프로파일/환경 분기 같은 기능은
  실제 요청이 생기기 전까지 추가하지 않는다.

## KISS — 파괴적 명령은 전부 같은 안전 패턴을 따른다

- dry-run 기본값 + `--apply` 명시 + safety.py의 백업/트랜잭션은 이
  프로젝트의 유일한 "위험한 변경" 처리 방식이다. 새로운 쓰기 명령을 추가할
  때 이 패턴을 재사용한다 — 별도 확인 방식(y/n 프롬프트, 다른 이름의
  플래그 등)을 새로 발명하지 않는다.
