"""PDF에서 LLM 파싱에 필요한 신호(메타데이터 + 앞부분 텍스트)를 뽑아낸다.

pypdf 메타데이터는 종종 비어있거나 부정확하므로(예: 스캐너 소프트웨어 이름이
Title로 들어간 경우), 표지·저작권 페이지 텍스트를 함께 넘겨 LLM이 교차검증하게
한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from . import config

# 손상되었거나 비표준인 PDF에서 나오는 "Ignoring wrong pointing object" 류의
# 경고가 대량 배치 처리 시 로그를 뒤덮는 것을 막는다. 파싱 자체에는 영향 없음.
logging.getLogger("pypdf").setLevel(logging.ERROR)


@dataclass
class PdfSignal:
    path: Path
    meta_title: str | None = None
    meta_author: str | None = None
    meta_creation_date: str | None = None
    page_count: int = 0
    text: str = ""
    error: str | None = None
    encrypted_locked: bool = False


def extract_signal(pdf_path: Path) -> PdfSignal:
    signal = PdfSignal(path=pdf_path)
    try:
        reader = PdfReader(str(pdf_path))
    except (PdfReadError, OSError, ValueError) as exc:
        signal.error = f"PDF를 열 수 없음: {exc}"
        return signal

    if reader.is_encrypted:
        # 빈 비밀번호로 시도 — 실패하면 텍스트 없이 메타데이터만 반환
        try:
            reader.decrypt("")
        except Exception:
            signal.encrypted_locked = True

    try:
        meta = reader.metadata
        if meta:
            signal.meta_title = _clean_str(meta.title)
            signal.meta_author = _clean_str(meta.author)
            signal.meta_creation_date = _clean_str(meta.creation_date_raw or meta.creation_date)
    except Exception as exc:
        signal.error = f"메타데이터 읽기 실패: {exc}"

    try:
        signal.page_count = len(reader.pages)
    except Exception as exc:
        # 손상된 PDF는 pypdf가 PdfReader() 생성 시점이 아니라 페이지를 실제로
        # 순회할 때야 LimitReachedError 등을 던지는 경우가 있다. 배치 작업 중
        # 한 파일 때문에 전체가 죽지 않도록 여기서도 넓게 잡는다.
        signal.error = f"페이지 접근 실패(손상된 PDF 가능성): {exc}"
        return signal

    if not signal.encrypted_locked:
        chunks: list[str] = []
        total_len = 0
        page_limit = min(config.SIGNAL_PAGE_COUNT, signal.page_count)
        for i in range(page_limit):
            try:
                page_text = reader.pages[i].extract_text() or ""
            except Exception:
                continue
            chunks.append(page_text)
            total_len += len(page_text)
            if total_len >= config.SIGNAL_MAX_CHARS:
                break
        signal.text = "\n".join(chunks)[: config.SIGNAL_MAX_CHARS]

    return signal


def _clean_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
