"""사용자별 경로 설정: 최초 실행 마법사 + ~/.config/mn4-librarian/config.toml.

패키지로 설치되면(Homebrew 등) "프로젝트 루트"라는 개념이 없어지므로, 라이브러리
폴더·신규 문서 폴더 경로는 전부 이 모듈이 관리하는 사용자별 설정 파일에서 온다.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR_NAME = "mn4-librarian"

# MarginNote4가 iCloud 컨테이너에 두는 SQLite DB의 고정 경로 패턴.
DEFAULT_MN4_DB_PATH = (
    Path.home()
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
    return Path.home() / ".config" / CONFIG_DIR_NAME


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
    mn4_db_path = Path(data["mn4_db_path"]) if data.get("mn4_db_path") else DEFAULT_MN4_DB_PATH
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
    if settings.mn4_db_path != DEFAULT_MN4_DB_PATH:
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


def _prompt_path(question: str, *, must_exist: bool, default: Path | None = None) -> Path:
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"{question}{suffix}: ").strip()
        if not raw and default is not None:
            raw = str(default)
        if not raw:
            print("경로를 입력하세요.")
            continue
        path = Path(raw).expanduser().resolve()
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

    if DEFAULT_MN4_DB_PATH.exists():
        mn4_db_path = DEFAULT_MN4_DB_PATH
        print(f"MarginNote4 DB 자동 감지됨: {mn4_db_path}")
    else:
        print(f"MarginNote4 DB를 기본 위치에서 찾지 못했습니다: {DEFAULT_MN4_DB_PATH}")
        while True:
            raw = input("MarginNotes.sqlite 전체 경로를 직접 입력하세요: ").strip()
            candidate = Path(raw).expanduser().resolve()
            if candidate.is_file():
                mn4_db_path = candidate
                break
            print(f"파일을 찾을 수 없습니다: {candidate}")

    settings = Settings(library_dir=library_dir, inbox_dir=inbox_dir, mn4_db_path=mn4_db_path)
    save_settings(settings)
    print(f"\n설정 저장 완료: {config_file()}")
    return settings
