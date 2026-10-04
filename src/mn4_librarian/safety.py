"""MarginNote4 SQLite DB를 직접 수정하기 전에 거치는 안전장치.

이 세 가지 패턴은 실제로 라이브 앱 DB를 여러 차례 수정하며 검증됐다:
1. MN4가 실행 중이면 즉시 중단 (WAL을 동시에 쓰면 손상/덮어쓰기 위험)
2. 쓰기 전 WAL 체크포인트 + 타임스탬프 백업
3. 트랜잭션: 커밋 전 예외 발생 시 자동 롤백, 커밋 후 integrity_check
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, TypeVar

from . import settings

T = TypeVar("T")


class MarginNoteRunningError(RuntimeError):
    pass


class IntegrityCheckFailed(RuntimeError):
    pass


def is_marginnote_running() -> bool:
    result = subprocess.run(["pgrep", "-f", "MarginNote"], capture_output=True, text=True)
    return result.returncode == 0


def assert_marginnote_closed() -> None:
    if is_marginnote_running():
        raise MarginNoteRunningError(
            "MarginNote 4가 실행 중입니다. DB를 직접 수정하는 동안 앱이 동시에 쓰기 작업을 하면 "
            "손상되거나 변경사항이 덮어써질 수 있습니다. MarginNote 4를 종료한 뒤 다시 실행하세요."
        )


def backup_db(db_path: Path) -> Path:
    """WAL을 본 파일에 반영(checkpoint)한 뒤 타임스탬프 백업을 만든다."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        con.close()

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = settings.backups_dir() / f"MarginNotes-{ts}.sqlite"
    shutil.copy2(db_path, backup_path)
    return backup_path


def with_write_transaction(db_path: Path, fn: Callable[[sqlite3.Connection], T]) -> T:
    """fn(con) 안에서 예외가 나면 커밋 없이 종료(롤백)되고, 성공하면 커밋 후
    integrity_check를 실행한다. fn은 자체적으로 con.commit()을 호출하지 않는다
    — 커밋은 이 함수가 성공 시 한 번만 수행한다."""
    con = sqlite3.connect(str(db_path))
    # mn4_db의 모든 함수는 컬럼명으로 행에 접근한다 (connect_readonly와 동일하게 맞춤)
    con.row_factory = sqlite3.Row
    try:
        result = fn(con)
    except Exception:
        con.rollback()
        con.close()
        raise

    con.commit()
    (check,) = con.execute("PRAGMA integrity_check;").fetchone()
    con.close()
    if check != "ok":
        raise IntegrityCheckFailed(f"커밋 후 integrity_check 실패: {check}")
    return result


def prepare_for_write(db_path: Path) -> Path:
    """쓰기 작업 전 공통 절차: MN4 종료 확인 + 백업. 백업 경로를 반환한다."""
    assert_marginnote_closed()
    return backup_db(db_path)
