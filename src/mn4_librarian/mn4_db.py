"""MarginNote4 SQLite DB(ZBOOK/ZBOOKCONFIG/ZBOOKTAG) 접근 계층.

이번 세션에 라이브 DB를 직접 조작하며 검증한 패턴(태그 조회/생성/rename/병합,
책 조회, 태그리스트 갱신, 파일 이동)을 재사용 가능한 형태로 정리한 모듈.
모든 쓰기 함수는 트랜잭션 안에서(safety.with_write_transaction) 호출되어야
하며, 이 모듈 자체는 commit/rollback을 하지 않는다.
"""
from __future__ import annotations

import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

TAG_PREFIX = "$$$CATEGORY1$$$"
# MarginNote4가 iCloud 경로를 인코딩하는 고정 접두어. 앱 자체의 iCloud 컨테이너
# 식별자라 모든 사용자에게 동일하다 (사용자별 라이브러리 경로와는 무관).
DOCLINK_PREFIX = "$$$MNDOCLINK$$$iCloud.QReader.MarginStudy.easy/"

# 대분류/소분류가 아닌 특수 루트 태그 (저자/출판사/출판연도 계층의 뿌리)
META_ROOT_TAG_NAMES = {"저자", "출판사", "출판연도"}


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """감사(audit)처럼 절대 쓰면 안 되는 작업용 — SQLite URI 읽기전용 모드로 연다."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


@dataclass
class Book:
    book_pk: int
    config_pk: int
    file: str
    path: str  # 원본 ZPATH (DOCLINK_PREFIX 포함)
    md5: str
    title: str
    taglist: list[str]  # 태그 UUID 리스트

    @property
    def rel_dir(self) -> str:
        """DOCLINK_PREFIX를 뗀 상대 디렉토리 (예: "E. Programming Languages/Python")."""
        if self.path.startswith(DOCLINK_PREFIX):
            return self.path[len(DOCLINK_PREFIX) :]
        return self.path

    @property
    def path_major(self) -> str | None:
        parts = self.rel_dir.split("/")
        return parts[0] if parts and parts[0] else None

    @property
    def path_sub(self) -> str | None:
        parts = self.rel_dir.split("/")
        return parts[1] if len(parts) >= 2 else None


def iter_library_books(con: sqlite3.Connection) -> list[Book]:
    """iCloud 라이브러리 경로에 있는 실제 책만 (스터디카드 등 비-PDF 엔트리 제외).

    MN4는 파일명이 바뀐 뒤에도 예전 ZBOOKCONFIG 행을 정리하지 않고 같은 MD5에
    여러 개 남겨두는 경우가 있다(고아 행). 한 ZBOOK에 ZBOOKCONFIG가 여러 개
    붙으면, 파일명과 ZTITLE이 정확히 일치하는 것을 "현재" 행으로 우선 채택하고,
    없으면 가장 최근(Z_PK가 가장 큰) 행을 쓴다 — 나머지 고아 행은 무시한다.
    """
    rows = con.execute(
        "SELECT b.Z_PK AS book_pk, c.Z_PK AS config_pk, b.ZFILE AS file, b.ZPATH AS path, "
        "b.ZMD5 AS md5, c.ZTITLE AS title, c.ZTAGLIST AS taglist "
        "FROM ZBOOK b JOIN ZBOOKCONFIG c ON c.ZMD5 = b.ZMD5 "
        "WHERE b.ZPATH LIKE ?",
        (f"{DOCLINK_PREFIX}%",),
    ).fetchall()

    candidates_by_book: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        candidates_by_book.setdefault(r["book_pk"], []).append(r)

    books = []
    for candidates in candidates_by_book.values():
        if len(candidates) > 1:
            stem = Path(candidates[0]["file"]).stem
            exact = [r for r in candidates if r["title"] == stem]
            chosen = exact[0] if exact else max(candidates, key=lambda r: r["config_pk"])
        else:
            chosen = candidates[0]
        ids = [x for x in (chosen["taglist"] or "").split("|") if x]
        books.append(
            Book(
                book_pk=chosen["book_pk"],
                config_pk=chosen["config_pk"],
                file=chosen["file"],
                path=chosen["path"],
                md5=chosen["md5"],
                title=chosen["title"],
                taglist=ids,
            )
        )
    return books


class TagIndex:
    """ZBOOKTAG 전체를 메모리에 로드해 이름<->id 조회/생성/rename/병합을 지원한다."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.name_by_id: dict[str, str] = {}
        self.links_by_id: dict[str, list[str]] = {}
        self._reload()
        (self._next_pk,) = con.execute(
            "SELECT Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME='BookTag'"
        ).fetchone()

    def _reload(self) -> None:
        self.name_by_id.clear()
        self.links_by_id.clear()
        for row in self.con.execute("SELECT ZTAGID, ZTAGNAME, ZTAGLINKS FROM ZBOOKTAG"):
            name = row["ZTAGNAME"] or ""
            name = name[len(TAG_PREFIX) :] if name.startswith(TAG_PREFIX) else name
            self.name_by_id[row["ZTAGID"]] = name
            self.links_by_id[row["ZTAGID"]] = [x for x in (row["ZTAGLINKS"] or "").split("|") if x]

    def find_id(self, name: str) -> str | None:
        for tid, n in self.name_by_id.items():
            if n == name:
                return tid
        return None

    def roots(self) -> list[str]:
        children = {c for links in self.links_by_id.values() for c in links}
        return [t for t in self.name_by_id if t not in children]

    def major_id(self, major_name: str) -> str:
        tid = self.find_id(major_name)
        if tid is None:
            raise KeyError(f"대분류 태그를 찾지 못함: {major_name}")
        return tid

    def sub_id(self, major_name: str, sub_name: str) -> str:
        mid = self.major_id(major_name)
        for child in self.links_by_id[mid]:
            if self.name_by_id.get(child) == sub_name:
                return child
        raise KeyError(f"소분류 태그를 찾지 못함: {major_name} / {sub_name}")

    def create_root_tag(self, name: str) -> str:
        """대분류처럼 부모가 없는 최상위 태그를 새로 만든다."""
        self._next_pk += 1
        new_id = str(uuid.uuid4()).upper()
        self.con.execute(
            "INSERT INTO ZBOOKTAG (Z_PK, Z_ENT, Z_OPT, ZTAGCLOSE, ZUSN, ZSYNCHASH, ZTAGID, ZTAGLINKS, ZTAGNAME) "
            "VALUES (?, 7, 1, NULL, 0, NULL, ?, '', ?)",
            (self._next_pk, new_id, TAG_PREFIX + name),
        )
        self.con.execute("UPDATE Z_PRIMARYKEY SET Z_MAX=? WHERE Z_NAME='BookTag'", (self._next_pk,))
        self.name_by_id[new_id] = name
        self.links_by_id[new_id] = []
        return new_id

    def get_or_create_root_tag(self, name: str) -> str:
        tid = self.find_id(name)
        if tid is not None:
            return tid
        return self.create_root_tag(name)

    def create_tag(self, name: str, parent_id: str) -> str:
        self._next_pk += 1
        new_id = str(uuid.uuid4()).upper()
        self.con.execute(
            "INSERT INTO ZBOOKTAG (Z_PK, Z_ENT, Z_OPT, ZTAGCLOSE, ZUSN, ZSYNCHASH, ZTAGID, ZTAGLINKS, ZTAGNAME) "
            "VALUES (?, 7, 1, NULL, 0, NULL, ?, '', ?)",
            (self._next_pk, new_id, TAG_PREFIX + name),
        )
        self.con.execute("UPDATE Z_PRIMARYKEY SET Z_MAX=? WHERE Z_NAME='BookTag'", (self._next_pk,))
        new_links = self.links_by_id[parent_id] + [new_id]
        self.con.execute("UPDATE ZBOOKTAG SET ZTAGLINKS=? WHERE ZTAGID=?", ("|".join(new_links), parent_id))
        self.name_by_id[new_id] = name
        self.links_by_id[new_id] = []
        self.links_by_id[parent_id] = new_links
        return new_id

    def get_or_create_tag(self, name: str, parent_id: str) -> str:
        for child in self.links_by_id[parent_id]:
            if self.name_by_id.get(child) == name:
                return child
        return self.create_tag(name, parent_id)

    def rename_tag(self, tag_id: str, new_name: str) -> None:
        self.con.execute("UPDATE ZBOOKTAG SET ZTAGNAME=? WHERE ZTAGID=?", (TAG_PREFIX + new_name, tag_id))
        self.name_by_id[tag_id] = new_name

    def merge_tag(self, keep_id: str, remove_id: str) -> int:
        """remove_id를 쓰는 모든 책의 ZTAGLIST를 keep_id로 바꾸고 remove_id 태그를
        삭제한다. 영향받은 책 수를 반환한다. (예: 출판사 오타 태그 통합)"""
        rows = self.con.execute(
            "SELECT Z_PK, ZTAGLIST FROM ZBOOKCONFIG WHERE ZTAGLIST LIKE ?", (f"%{remove_id}%",)
        ).fetchall()
        for row in rows:
            ids = [keep_id if t == remove_id else t for t in row["ZTAGLIST"].split("|") if t]
            self.con.execute("UPDATE ZBOOKCONFIG SET ZTAGLIST=? WHERE Z_PK=?", ("|".join(ids), row["Z_PK"]))

        parent_id = next((p for p, links in self.links_by_id.items() if remove_id in links), None)
        if parent_id is not None:
            new_links = [t for t in self.links_by_id[parent_id] if t != remove_id]
            self.con.execute("UPDATE ZBOOKTAG SET ZTAGLINKS=? WHERE ZTAGID=?", ("|".join(new_links), parent_id))
            self.links_by_id[parent_id] = new_links

        self.con.execute("DELETE FROM ZBOOKTAG WHERE ZTAGID=?", (remove_id,))
        del self.name_by_id[remove_id]
        del self.links_by_id[remove_id]
        return len(rows)


def set_taglist(con: sqlite3.Connection, book: Book, tag_ids: list[str]) -> None:
    con.execute("UPDATE ZBOOKCONFIG SET ZTAGLIST=? WHERE Z_PK=?", ("|".join(tag_ids), book.config_pk))
    book.taglist = list(tag_ids)


def move_book_file(con: sqlite3.Connection, book: Book, library_dir: Path, new_major: str, new_sub: str) -> Path:
    """물리 파일을 새 폴더로 옮기고 ZPATH를 갱신한다. 새 경로를 반환한다."""
    src = library_dir / book.rel_dir / book.file
    new_rel = f"{new_major}/{new_sub}"
    dst_dir = library_dir / new_rel
    dst = dst_dir / book.file
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists():
        raise FileExistsError(dst)
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    con.execute("UPDATE ZBOOK SET ZPATH=? WHERE Z_PK=?", (DOCLINK_PREFIX + new_rel, book.book_pk))
    book.path = DOCLINK_PREFIX + new_rel
    return dst


def delete_book_file(library_dir: Path, book: Book) -> None:
    """물리 파일만 지운다. DB의 ZBOOK/ZBOOKCONFIG 행은 건드리지 않는다 — MN4를
    재실행하면 파일이 없는 항목을 자동으로 정리하는 게 실제로 확인됐다."""
    path = library_dir / book.rel_dir / book.file
    if path.exists():
        path.unlink()
