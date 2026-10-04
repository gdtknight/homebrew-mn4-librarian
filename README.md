# mn4-librarian

MarginNote4 PDF 라이브러리를 위한 파일명 표준화 + 태그 관리 자동화 CLI.

- **파일명 정리**: 새로 들어온 PDF를 표지/내용 기반으로 분류하고
  `{연도}-{제목}-{저자1,저자2 외}-{출판사}.pdf` 형식으로 이름을 바꿔 라이브러리
  폴더에 넣는다. 기존 라이브러리 전체의 파일명만 일괄 마이그레이션할 수도 있다.
- **MarginNote4 태그 관리**: MarginNote4의 SQLite DB를 감사(누락/불일치/중복 태그
  탐지)하고, 파일을 건드리지 않는 수정은 자동으로, 폴더 이동·중복 파일 삭제처럼
  파일을 건드리는 수정은 사람이 검토한 뒤 적용한다.

분류체계(대분류/소분류)는 코드에 하드코딩돼 있지 않다 — 라이브러리 폴더의 실제
디렉토리 구조를 그대로 분류체계로 쓴다. 이미 폴더별로 정리해 둔 라이브러리라면
설정 없이 그대로 인식되고, 새 폴더를 만들면 다음 실행부터 자동으로 반영된다.

## 설치

```bash
brew tap gdtknight/mn4-librarian
brew trust gdtknight/mn4-librarian   # 처음 추가하는 서드파티 tap이라 필요
brew install mn4-librarian
```

### 전제조건

[`claude` CLI](https://docs.claude.com/claude-code)가 설치되어 로그인돼 있어야
한다 (Claude 구독 크레딧 사용, API 키 과금 없음).

## 사용법

### 최초 설정

```bash
mn4-librarian init
```

MarginNote4 라이브러리 폴더 경로와 신규 PDF를 넣어둘 폴더 경로를 물어본다.
MarginNote4의 SQLite DB 위치는 자동으로 감지한다.

### 파일명 정리

```bash
mn4-librarian ingest [--apply]              # 신규 PDF 분류 + 이름변경 + 이동
mn4-librarian migrate [--apply]             # 기존 라이브러리 파일명만 일괄 정리
mn4-librarian fill-years [--apply]          # 연도 미확인 파일에 연도만 보완
```

모든 명령은 기본이 dry-run이며 `--apply`를 붙여야 실제로 파일이 바뀐다.

### MarginNote4 태그 관리

```bash
mn4-librarian tags audit                          # 읽기 전용 감사
mn4-librarian tags fix [--apply]                   # 파일을 안 건드리는 안전한 수정만 자동 적용
mn4-librarian tags review-files                     # 폴더 이동/중복파일 삭제가 필요한 항목을 JSON으로 출력
mn4-librarian tags apply-file-actions <reviewed.json>  # 검토한 JSON대로 실제 적용
```

`tags fix`는 미태깅/대분류 누락/폴더-태그 불일치/근접중복 태그 같은 **파일을
옮기거나 지우지 않는** 문제만 자동으로 고친다. 폴더 자체가 잘못됐거나 중복
파일이 있는 경우는 항상 `tags review-files`로 검토 항목을 만들고, 사람이 JSON을
직접 확인·수정한 뒤 `tags apply-file-actions`로 적용해야 한다 — 파일 이동/삭제는
한 번의 명령으로 자동 실행되지 않는다.

`ingest`나 폴더 정리는 파일만 옮기고 MarginNote4 DB는 갱신하지 않는다. 감사 결과
대부분은 DB 기준이므로, 폴더를 바꾼 뒤에는 MarginNote4를 열어 동기화하고 종료한
다음 `tags audit`을 다시 실행해야 반영된다 (DB가 폴더보다 오래됐으면 감사 출력
맨 위에 경고가 뜬다).

DB를 직접 수정하는 명령(`tags fix --apply`, `tags apply-file-actions`)은
실행 전 MarginNote4가 종료돼 있어야 하며, 매번 자동으로 타임스탬프 백업을 만든다.

## 개발

```bash
git clone https://github.com/gdtknight/homebrew-mn4-librarian.git
cd homebrew-mn4-librarian
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```
