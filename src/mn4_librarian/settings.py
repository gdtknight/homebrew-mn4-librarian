"""사용자별 경로 설정: 최초 실행 마법사 + ~/.config/mn4-librarian/config.toml.

패키지로 설치되면(Homebrew 등) "프로젝트 루트"라는 개념이 없어지므로, 라이브러리
폴더·신규 문서 폴더 경로는 전부 이 모듈이 관리하는 사용자별 설정 파일에서 온다.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR_NAME = "mn4-librarian"


def _home_dir() -> Path:
    """HOME 환경변수를 우선 신뢰한다. Path.home()은 pwd 데이터베이스 조회로
    폴백하는데 이게 일부 환경에서 간헐적으로 실패하는 게 실제로 확인됐다."""
    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def default_mn4_db_path() -> Path:
    """MarginNote4가 iCloud 컨테이너에 두는 SQLite DB의 고정 경로 패턴."""
    return (
        _home_dir()
        / "Library/Containers/QReader.MarginStudy.easy/Data/Library/Private Documents"
        / "MN4NotebookDatabase/0/MarginNotes.sqlite"
    )


class SettingsNotConfigured(RuntimeError):
    pass


@dataclass
class Settings:
    library_dir: Path
    inbox_dir: Path
    mn4_db_path: Path


def config_dir() -> Path:
    return _home_dir() / ".config" / CONFIG_DIR_NAME


def config_file() -> Path:
    return config_dir() / "config.toml"


def logs_dir() -> Path:
    d = config_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backups_dir() -> Path:
    d = config_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings() -> Settings | None:
    path = config_file()
    if not path.exists():
        return None
    with path.open("rb") as f:
        data = tomllib.load(f)
    mn4_db_path = Path(data["mn4_db_path"]) if data.get("mn4_db_path") else default_mn4_db_path()
    return Settings(
        library_dir=Path(data["library_dir"]),
        inbox_dir=Path(data["inbox_dir"]),
        mn4_db_path=mn4_db_path,
    )


def save_settings(settings: Settings) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    lines = [
        f'library_dir = "{settings.library_dir}"',
        f'inbox_dir = "{settings.inbox_dir}"',
    ]
    if settings.mn4_db_path != default_mn4_db_path():
        lines.append(f'mn4_db_path = "{settings.mn4_db_path}"')
    config_file().write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_settings() -> Settings:
    settings = load_settings()
    if settings is None:
        raise SettingsNotConfigured(
            "설정이 없습니다. 먼저 `mn4-librarian init`을 실행해 라이브러리 폴더 경로를 지정하세요."
        )
    return settings


# ---------------------------------------------------------------------------
# 최초 실행 마법사
# ---------------------------------------------------------------------------


def _expand_path(raw: str) -> Path:
    """Path.expanduser()는 HOME 환경변수가 없으면 pwd 데이터베이스 조회로
    폴백하는데, 이 조회가 일부 환경(디렉토리 서비스 지연 등)에서 간헐적으로
    실패해 RuntimeError("Could not determine home directory")를 던진다.
    HOME 환경변수를 직접 먼저 확인해 그 폴백 경로를 아예 안 타게 하고,
    그래도 안 되면 크래시 대신 명확한 안내 메시지를 낸다."""
    if raw == "~" or raw.startswith("~/"):
        home = os.environ.get("HOME")
        if home:
            return Path(home + raw[1:]).resolve()
        try:
            return Path(raw).expanduser().resolve()
        except RuntimeError as exc:
            raise ValueError(
                "홈 디렉토리(~)를 확인할 수 없습니다. 물결표 없이 전체 경로를 입력해주세요 "
                "(예: /Users/이름/Downloads/PDF)."
            ) from exc
    return Path(raw).expanduser().resolve()


def _prompt_path(question: str, *, must_exist: bool, default: Path | None = None) -> Path:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"{question}{suffix}: ").strip()
        if not raw and default is not None:
            raw = str(default)
        if not raw:
            print("경로를 입력하세요.")
            continue
        try:
            path = _expand_path(raw)
        except ValueError as exc:
            print(str(exc))
            continue
        if must_exist and not path.is_dir():
            print(f"디렉토리를 찾을 수 없습니다: {path}")
            continue
        return path


def run_init_wizard() -> Settings:
    print("mn4-librarian 최초 설정")
    print("=" * 40)
    library_dir = _prompt_path(
        "MarginNote4 라이브러리 폴더 경로 (iCloud Documents 등)", must_exist=True
    )
    inbox_dir = _prompt_path(
        "신규 PDF를 넣어둘 폴더 경로 (없으면 새로 만듭니다)", must_exist=False
    )
    inbox_dir.mkdir(parents=True, exist_ok=True)

    default_db_path = default_mn4_db_path()
    if default_db_path.exists():
        mn4_db_path = default_db_path
        print(f"MarginNote4 DB 자동 감지됨: {mn4_db_path}")
    else:
        print(f"MarginNote4 DB를 기본 위치에서 찾지 못했습니다: {default_db_path}")
        while True:
            raw = input("MarginNotes.sqlite 전체 경로를 직접 입력하세요: ").strip()
            try:
                candidate = _expand_path(raw)
            except ValueError as exc:
                print(str(exc))
                continue
            if candidate.is_file():
                mn4_db_path = candidate
                break
            print(f"파일을 찾을 수 없습니다: {candidate}")

    settings = Settings(library_dir=library_dir, inbox_dir=inbox_dir, mn4_db_path=mn4_db_path)
    save_settings(settings)
    print(f"\n설정 저장 완료: {config_file()}")
    return settings
