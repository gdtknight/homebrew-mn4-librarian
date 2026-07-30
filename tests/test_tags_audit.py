import sqlite3
import uuid

import pytest

from mn4_librarian import tags_audit

SCHEMA = """
CREATE TABLE ZBOOKTAG (
    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZTAGCLOSE INTEGER,
    ZUSN INTEGER, ZSYNCHASH VARCHAR, ZTAGID VARCHAR, ZTAGLINKS VARCHAR, ZTAGNAME VARCHAR
);
CREATE TABLE ZBOOK (
    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZLASTVISIT TIMESTAMP,
    ZAUTHOR VARCHAR, ZBOOKURL VARCHAR, ZCURRENTTOPICID VARCHAR, ZFILE VARCHAR,
    ZMD5 VARCHAR, ZMD5LONG VARCHAR, ZPATH VARCHAR, ZTHUMBNAIL BLOB
);
CREATE TABLE ZBOOKCONFIG (
    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZCURRPAGE INTEGER,
    ZCURRPAGEOFF INTEGER, ZSYNCMODE INTEGER, ZUSNFTS INTEGER, ZUSNPROPERTIES INTEGER,
    ZCURRPAGEPERCENT FLOAT, ZFONTSCALE FLOAT, ZCLOUDURL VARCHAR, ZFONTNAME VARCHAR,
    ZMD5 VARCHAR, ZMD5LONG VARCHAR, ZOPTIONS VARCHAR, ZTAGLIST VARCHAR, ZTITLE VARCHAR
);
CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY, Z_NAME VARCHAR, Z_SUPER INTEGER, Z_MAX INTEGER);
"""

PREFIX = "$$$CATEGORY1$$$"
DOCLINK_PREFIX = "$$$MNDOCLINK$$$iCloud.QReader.MarginStudy.easy/"


def _make_tag(con, pk, name, links=""):
    tagid = str(uuid.uuid4()).upper()
    con.execute(
        "INSERT INTO ZBOOKTAG (Z_PK, Z_ENT, Z_OPT, ZTAGCLOSE, ZUSN, ZSYNCHASH, ZTAGID, ZTAGLINKS, ZTAGNAME) "
        "VALUES (?, 7, 1, NULL, 0, NULL, ?, ?, ?)",
        (pk, tagid, links, PREFIX + name),
    )
    return tagid


def _make_book(con, pk, file, rel_dir, md5, taglist):
    path = DOCLINK_PREFIX + rel_dir
    con.execute("INSERT INTO ZBOOK (Z_PK, ZFILE, ZMD5, ZPATH) VALUES (?, ?, ?, ?)", (pk, file, md5, path))
    con.execute(
        "INSERT INTO ZBOOKCONFIG (Z_PK, ZMD5, ZTITLE, ZTAGLIST) VALUES (?, ?, ?, ?)",
        (pk, md5, file.rsplit(".", 1)[0], taglist),
    )


@pytest.fixture
def fixture_db(tmp_path):
    library_dir = tmp_path / "library"
    (library_dir / "A. Major" / "Sub1").mkdir(parents=True)
    (library_dir / "A. Major" / "Sub2").mkdir(parents=True)

    tagged_name = "2024-Tagged Book-Author-Publisher.pdf"
    untagged_name = "2024-Untagged Book-Author-Publisher.pdf"
    mismatched_name = "2024-Mismatched Book-Author-Publisher.pdf"
    for rel, name in [
        ("A. Major/Sub1", tagged_name),
        ("A. Major/Sub2", untagged_name),
        ("A. Major/Sub1", mismatched_name),
    ]:
        (library_dir / rel / name).write_bytes(b"fake pdf bytes")

    db_path = tmp_path / "MarginNotes.sqlite"
    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)
    con.execute("INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME, Z_SUPER, Z_MAX) VALUES (7, 'BookTag', 0, 10)")

    sub1_id = _make_tag(con, 2, "Sub1")
    sub2_id = _make_tag(con, 3, "Sub2")
    major_id = _make_tag(con, 1, "A. Major", links=f"{sub1_id}|{sub2_id}")

    _make_book(con, 1, tagged_name, "A. Major/Sub1", "md5-tagged", f"{major_id}|{sub1_id}")
    _make_book(con, 2, untagged_name, "A. Major/Sub2", "md5-untagged", "")
    # 폴더는 Sub1인데 태그는 Sub2로 붙어 있는 불일치 케이스
    _make_book(con, 3, mismatched_name, "A. Major/Sub1", "md5-mismatched", f"{major_id}|{sub2_id}")

    con.commit()
    con.close()
    return db_path, library_dir


def test_tagged_book_has_no_issues(fixture_db):
    db_path, library_dir = fixture_db
    report = tags_audit.run_audit(db_path, library_dir)
    assert not any("Tagged Book" in f and "Untagged" not in f and "Mismatched" not in f for f in report.untagged_books)
    assert not any(m["file"].startswith("2024-Tagged Book") for m in report.folder_tag_mismatches)


def test_untagged_book_detected(fixture_db):
    db_path, library_dir = fixture_db
    report = tags_audit.run_audit(db_path, library_dir)
    assert any("Untagged Book" in f for f in report.untagged_books)


def test_folder_tag_mismatch_detected(fixture_db):
    db_path, library_dir = fixture_db
    report = tags_audit.run_audit(db_path, library_dir)
    mismatched = [m for m in report.folder_tag_mismatches if "Mismatched Book" in m["file"]]
    assert len(mismatched) == 1
    assert mismatched[0]["rel"] == "A. Major/Sub1"
    assert mismatched[0]["tag_sub"] == "Sub2"


def test_clean_library_is_not_reported_clean_due_to_fixtures(fixture_db):
    db_path, library_dir = fixture_db
    report = tags_audit.run_audit(db_path, library_dir)
    # fixture 자체가 미태깅/불일치 책을 포함하므로 clean이면 안 된다 (감사 로직이 실제로 뭔가 잡아냈는지 확인)
    assert not report.is_clean()
