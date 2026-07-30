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


def test_already_new_format_detects_leading_year():
    assert already_new_format("2020-Some Book-Author-Pub.pdf")
    assert not already_new_format("Some Book-Author-Pub.pdf")
