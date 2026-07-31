from mn4_librarian.naming import BookMeta, already_new_format, build_filename, format_authors, sanitize_field


def test_build_filename_basic():
    meta = BookMeta(title="Clean Code", authors=["Robert C. Martin"], publisher="Prentice Hall", year="2008")
    assert build_filename(meta) == "2008-Clean Code-Robert C. Martin-Prentice Hall.pdf"


def test_build_filename_missing_year_has_no_prefix():
    meta = BookMeta(title="Some Book", authors=["Author"], publisher="Pub", year="")
    assert build_filename(meta) == "Some Book-Author-Pub.pdf"


def test_build_filename_missing_publisher_falls_back_to_unknown():
    meta = BookMeta(title="Some Book", authors=["Author"], publisher="", year="2020")
    assert build_filename(meta) == "2020-Some Book-Author-미상.pdf"


def test_format_authors_overflow_uses_suffix():
    assert format_authors(["A", "B", "C"]) == "A,B 외"


def test_format_authors_empty_falls_back_to_unknown():
    assert format_authors([]) == "미상"


def test_sanitize_field_strips_colon_and_normalizes_slash():
    assert sanitize_field("Test: A/B") == "Test A-B"


def test_build_filename_strips_comma_from_title_to_avoid_author_list_ambiguity():
    # "제목, 5th Edition-저자1,저자2-출판사.pdf"처럼 제목 안의 콤마가 저자 목록
    # 구분자 콤마와 헷갈리는 걸 막는다 (실사용자 라이브러리에서 실제로 보고된 케이스)
    meta = BookMeta(
        title="Modern Operating Systems, 5th Edition",
        authors=["Andrew S. Tanenbaum", "Herbert Bos"],
        publisher="Pearson",
        year="2022",
    )
    filename = build_filename(meta)
    assert filename == "2022-Modern Operating Systems 5th Edition-Andrew S. Tanenbaum,Herbert Bos-Pearson.pdf"
    # 파일명 전체에서 콤마는 저자 구분자 하나만 남아야 한다
    assert filename.count(",") == 1


def test_already_new_format_detects_leading_year():
    assert already_new_format("2020-Some Book-Author-Pub.pdf")
    assert not already_new_format("Some Book-Author-Pub.pdf")
