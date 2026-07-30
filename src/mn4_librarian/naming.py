"""표준 파일명 조립: {연도}-{제목}-{저자1,저자2 외}-{출판사}.pdf

필드는 항상 구조화된 값(BookMeta)에서 새로 조립하며, 기존 파일명을 하이픈
기준으로 재분리하지 않는다 — 제목 안에 하이픈이 섞여 있어도(Test-Driven 등)
깨지지 않게 하기 위함이다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import config


@dataclass
class BookMeta:
    title: str  # 부제 제거된 핵심 제목
    authors: list[str]
    publisher: str
    year: str  # "2023" 같은 4자리 문자열. 확인 불가 시 빈 문자열
    category: str | None = None  # 대분류 (예: "E. Programming Languages")
    subcategory: str | None = None  # 소분류 (예: "Python")


def nfc(text: str) -> str:
    """macOS(APFS/HFS+)는 한글 파일명을 NFD로 저장해 동일 문자열도 바이트가
    달라진다. 비교·매칭 전에는 항상 NFC로 정규화한다."""
    return unicodedata.normalize("NFC", text)


def sanitize_field(text: str) -> str:
    text = nfc(text).strip()
    for bad, repl in config.FILENAME_SANITIZE_MAP.items():
        text = text.replace(bad, repl)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def format_authors(authors: list[str]) -> str:
    cleaned = [sanitize_field(a) for a in authors if a and a.strip()]
    if not cleaned:
        return "미상"
    shown = cleaned[: config.MAX_AUTHORS_SHOWN]
    result = ",".join(shown)
    if len(cleaned) > config.MAX_AUTHORS_SHOWN:
        result += config.AUTHOR_OVERFLOW_SUFFIX
    return result


def build_filename(meta: BookMeta) -> str:
    year = meta.year.strip() if meta.year else ""
    title = sanitize_field(meta.title)
    authors = format_authors(meta.authors)
    publisher = sanitize_field(meta.publisher) if meta.publisher else "미상"

    year_part = f"{year}-" if year else ""
    return f"{year_part}{title}-{authors}-{publisher}.pdf"


_LEADING_YEAR_RE = re.compile(r"^(19|20)\d{2}-")


def already_new_format(filename: str) -> bool:
    """파일명이 이미 {연도}-... 형식인지 (마이그레이션 대상에서 제외할지) 판단."""
    return bool(_LEADING_YEAR_RE.match(nfc(filename)))
