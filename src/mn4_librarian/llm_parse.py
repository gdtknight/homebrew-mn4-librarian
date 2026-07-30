"""claude CLI(-p, 비대화형)를 호출해 PDF 신호를 구조화된 서지정보로 파싱한다.

Max 구독 인증을 쓰기 위해 ANTHROPIC_API_KEY를 빈 문자열로 덮어써 API 과금 대신
구독 크레딧을 사용한다.

분류체계(대분류/소분류)는 호출자가 taxonomy dict로 넘겨준다 (하드코딩 없음 —
taxonomy.py가 라이브러리 폴더 구조를 스캔한 결과). 기존 목록에 안 맞으면 LLM이
새 소분류/대분류를 제안할 수 있다 — 새 대분류 생성을 실제로 허용할지는 호출자
(ingest.py 등)가 taxonomy.TaxonomyView.has_any_major()와 --allow-new-major
플래그로 판단한다.
"""
from __future__ import annotations

import json
import re
import subprocess

from . import config
from .naming import BookMeta
from .pdf_extract import PdfSignal

Taxonomy = dict[str, list[str]]


class LlmParseError(RuntimeError):
    pass


def _resolve_category(raw: str, taxonomy: Taxonomy) -> str:
    """LLM이 가끔 "G. Artificial Intelligence" 대신 "Artificial Intelligence"처럼
    접두어를 빼고 반환하는 경우가 있어, 접두어 유무에 관계없이 기존 대분류와
    매칭을 시도한다. 기존 것과 매칭되지 않으면(=새 대분류 제안) 그대로 반환한다."""
    raw = raw.strip()
    if raw in taxonomy:
        return raw
    for key in taxonomy:
        _, _, name = key.partition(". ")
        if name == raw:
            return key
    return raw  # 기존에 없는 이름 — 신규 대분류 제안으로 취급


def _resolve_subcategory(raw: str, category: str, taxonomy: Taxonomy) -> str:
    raw = raw.strip()
    subs = taxonomy.get(category, [])
    if raw in subs:
        return raw
    for sub in subs:
        if sub.lower() == raw.lower():
            return sub
    return raw  # 기존에 없는 이름 — 신규 소분류 제안으로 취급


def _taxonomy_block(taxonomy: Taxonomy) -> str:
    if not taxonomy:
        return "(아직 분류체계가 없는 빈 라이브러리 — 책 내용에 맞는 대분류/소분류를 새로 제안하라)"
    lines = []
    for major, subs in taxonomy.items():
        subs_text = ", ".join(subs) if subs else "(소분류 없음)"
        lines.append(f"- {major}: {subs_text}")
    return "\n".join(lines)


def _signal_block(signal: PdfSignal) -> str:
    return (
        f"PDF 메타데이터 Title: {signal.meta_title or '(없음)'}\n"
        f"PDF 메타데이터 Author: {signal.meta_author or '(없음)'}\n"
        f"PDF 메타데이터 생성일: {signal.meta_creation_date or '(없음)'}\n"
        f"페이지 수: {signal.page_count}\n"
        f"본문 앞부분(표지/저작권 페이지) 텍스트:\n{signal.text or '(추출 실패)'}"
    )


def call_claude(prompt: str) -> str:
    import os

    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ""  # Max 구독 크레딧 사용
    try:
        result = subprocess.run(
            [config.CLAUDE_BIN, "-p", "--model", config.CLAUDE_MODEL, "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=config.CLAUDE_TIMEOUT_SEC,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise LlmParseError(f"claude 호출 타임아웃({config.CLAUDE_TIMEOUT_SEC}s)") from exc
    except FileNotFoundError as exc:
        raise LlmParseError(f"claude 실행파일을 찾을 수 없음: {config.CLAUDE_BIN}") from exc

    if result.returncode != 0:
        raise LlmParseError(f"claude 호출 실패 (exit {result.returncode}): {result.stderr[:500]}")
    return result.stdout


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LlmParseError(f"응답에서 JSON을 찾지 못함: {text[:300]!r}")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LlmParseError(f"JSON 파싱 실패: {exc}. 원문: {match.group(0)[:300]!r}") from exc


# ---------------------------------------------------------------------------
# 신규 유입 파일: 제목/저자/출판사/연도 추출 + 분류 (New Documents 파이프라인)
# ---------------------------------------------------------------------------

def classify_new_book(signal: PdfSignal, filename_hint: str, taxonomy: Taxonomy) -> BookMeta:
    prompt = f"""너는 개인 기술서적 라이브러리 사서다. 아래 PDF 정보를 보고 서지정보를 JSON으로만 답하라. 다른 말은 하지 마라.

원본 파일명: {filename_hint}

{_signal_block(signal)}

규칙:
1. title: 표지에 나온 "핵심 제목"만. 콜론(:) 뒤에 오는 긴 부제·설명 문구는 제외한다.
   예: "Clean Code: A Handbook of Agile Software Craftsmanship" -> "Clean Code"
   판수(2nd Edition 등)는 있으면 title 끝에 포함한다.
2. authors: 표지에 나온 실제 "저자"(지은이/지음/by/저) 이름만. "성, 이름" 순서로
   뒤집지 말고 "이름 성" 순서 그대로. 번역서라면 "옮김/역/translated by"로
   표시된 번역자는 authors에 넣지 말고 원저자만 넣는다.
3. publisher: 출판사명 하나.
4. year: title에 판수(2nd Edition 등)가 있다면 "이 판"이 나온 연도 4자리.
   저작권 페이지에 "First edition 1996, ..., Twelfth edition 2021"처럼 여러
   판의 연도가 함께 나열돼 있으면 가장 오래된 연도(초판)가 아니라 title의
   판수와 일치하는 연도(대개 가장 최근 연도)를 써야 한다. 서문·본문 중에
   언급된 다른 무관한 연도와도 혼동하지 말 것. 확인 불가능하면 빈 문자열 "".
5. category: 아래 대분류 목록 중 이 책에 맞는 게 있으면 그대로 선택한다.
   정말 어느 것에도 안 맞으면 새 대분류 이름을 제안해도 된다 — 다만 대분류는
   구조적으로 큰 카테고리이므로 신중하게, 기존 것과 조금이라도 겹치면 기존
   것을 우선한다.
6. subcategory: 선택한(또는 새로 제안한) 대분류에 맞는 소분류를 고른다. 기존
   목록에 있으면 그대로 쓰고, 없으면 적절한 새 소분류 이름을 자유롭게
   제안해도 된다 (소분류는 새로 만드는 데 제약이 없다). 기존 이름과 사소하게
   다른 변형(예: "ML" vs "Machine Learning")을 만들지 말고, 뜻이 같으면
   기존 이름을 그대로 재사용하라.

현재 라이브러리의 대분류/소분류 목록:
{_taxonomy_block(taxonomy)}

다음 JSON 형식으로만 답하라:
{{"title": "...", "authors": ["...", "..."], "publisher": "...", "year": "...", "category": "...", "subcategory": "..."}}
"""
    raw = call_claude(prompt)
    data = _extract_json(raw)

    category = _resolve_category(str(data.get("category", "")), taxonomy)
    subcategory = _resolve_subcategory(str(data.get("subcategory", "")), category, taxonomy)
    if not category:
        raise LlmParseError(f"대분류를 판단하지 못함: {data.get('category')!r}")
    if not subcategory:
        raise LlmParseError(f"소분류를 판단하지 못함: {data.get('subcategory')!r}")

    return BookMeta(
        title=str(data.get("title", "")).strip(),
        authors=[str(a).strip() for a in data.get("authors", []) if str(a).strip()],
        publisher=str(data.get("publisher", "")).strip(),
        year=str(data.get("year", "")).strip(),
        category=category,
        subcategory=subcategory,
    )


# ---------------------------------------------------------------------------
# 기존 라이브러리 마이그레이션: 이미 분류된 폴더는 유지, 파일명만 새 형식으로
# ---------------------------------------------------------------------------

def reparse_existing_book(signal: PdfSignal, existing_filename_stem: str) -> BookMeta:
    prompt = f"""너는 개인 기술서적 라이브러리 사서다. 이 책은 이미 다음 형식으로
파일명이 붙어 있다: {{제목}}-{{저자1,저자2}}-{{출판사}}.pdf (구분자는 하이픈이지만
제목·출판사 자체에 하이픈이 포함될 수 있다. 예: "Addison-Wesley", "Test-Driven ...").

기존 파일명(확장자 제외): {existing_filename_stem}

{_signal_block(signal)}

기존 파일명과 PDF 신호를 함께 보고 title/authors/publisher/year를 다시 정확히
판단하라.

규칙:
1. title: 표지의 "핵심 제목"만. 콜론(:) 뒤 부제나 긴 설명 문구는 제거한다.
   판수(2nd Edition 등)는 있으면 title 끝에 유지한다.
2. authors: 실제 "저자"(지은이/지음/by/저) 이름 목록(배열), "이름 성" 순서.
   번역서라면 "옮김/역/translated by"로 표시된 번역자는 제외하고 원저자만
   넣는다. 기존 파일명에 이미 정확히 나와 있으면 그대로 쓰고, "성, 이름"처럼
   뒤바뀐 경우만 바로잡는다.
3. publisher: 출판사명 하나. 기존 파일명의 값이 맞으면 그대로 쓴다.
4. year: title에 판수(2nd Edition 등)가 있다면 "이 판"이 나온 연도 4자리.
   저작권 페이지에 "First edition 1996, ..., Twelfth edition 2021"처럼 여러
   판의 연도가 함께 나열돼 있으면 가장 오래된 연도(초판)가 아니라 title의
   판수와 일치하는 연도(대개 가장 최근 연도)를 써야 한다. 서문·본문 중에
   언급된 다른 무관한 연도와도 혼동하지 말 것. 메타데이터·저작권 페이지·
   본문에서 전혀 확인 안 되면 빈 문자열 "".

다음 JSON 형식으로만 답하라:
{{"title": "...", "authors": ["...", "..."], "publisher": "...", "year": "..."}}
"""
    raw = call_claude(prompt)
    data = _extract_json(raw)

    authors = [str(a).strip() for a in data.get("authors", []) if str(a).strip()]
    return BookMeta(
        title=str(data.get("title", "")).strip(),
        authors=authors,
        publisher=str(data.get("publisher", "")).strip(),
        year=str(data.get("year", "")).strip(),
    )


# ---------------------------------------------------------------------------
# 연도만 다시 찾기: title/authors/publisher는 이미 확정된 경우, PDF를 더 깊이
# 읽어 초판 출간연도만 집중적으로 찾는다.
# ---------------------------------------------------------------------------

def find_year_only(signal: PdfSignal, filename_hint: str) -> str:
    prompt = f"""아래는 이미 제목·저자·출판사가 확정된 책이다(파일명 그대로 신뢰해도 됨).
파일명에 판수(2nd Edition, 12th Edition 등)가 있다면, 그 "이 판"이 출간된 연도
4자리를 찾아라. 정말 못 찾겠으면 빈 문자열 "".

파일명: {filename_hint}

{_signal_block(signal)}

**중요**: Pearson·McGraw-Hill류 교재의 저작권 페이지에는 종종 "First edition
1996, Second edition 2000, ..., Twelfth edition 2021"처럼 전체 판 이력이 함께
나열된다. 이런 경우 가장 오래된 연도(초판)를 찍으면 안 되고, 파일명에 적힌
판수와 일치하는 연도(대개 나열된 것 중 가장 최근 연도)를 골라야 한다. 판수가
파일명에 없으면 표지·저작권 페이지에서 가장 최근/최종 발행 연도를 쓴다.
확인할 곳(우선순위 순): 저작권 페이지의 "Copyright ©" 연도(여러 개면 판수와
매칭되는 것), ISBN 근처의 발행일, PDF 메타데이터 생성일. 서문·본문에서
언급되는 무관한 역사적 연도(기술 자체의 등장 연도 등)와 혼동하지 마라.

다음 JSON 형식으로만 답하라: {{"year": "..."}}
"""
    raw = call_claude(prompt)
    try:
        data = _extract_json(raw)
        return str(data.get("year", "")).strip()
    except LlmParseError:
        # 가끔 JSON 없이 연도 숫자만 답하는 경우가 있어 최후 수단으로 구제한다.
        m = re.search(r"\b(19|20)\d{2}\b", raw)
        if m:
            return m.group(0)
        raise


# ---------------------------------------------------------------------------
# 폴더-태그 불일치 자동 판단: 이 책이 지금 있는 폴더(대/소분류)가 실제 내용과
# 맞는지 LLM에게 판단시킨다. tags_fix.py가 "DB 태그만 고치면 되는 안전한
# 케이스"와 "폴더 자체가 의심스러워 사람이 봐야 하는 케이스"를 가르는 데 쓴다.
# ---------------------------------------------------------------------------

def judge_folder_match(
    signal: PdfSignal,
    current_major: str,
    current_sub: str | None,
    taxonomy: Taxonomy,
    filename_hint: str,
) -> dict:
    prompt = f"""너는 개인 기술서적 라이브러리 사서다. 아래 책이 현재 라이브러리의
"{current_major} / {current_sub or '(소분류 없음)'}" 폴더에 들어있다. PDF 실제
내용을 보고 이 폴더 위치가 맞는지 판단하라.

파일명: {filename_hint}

{_signal_block(signal)}

현재 라이브러리의 대분류/소분류 목록 (재분류가 필요하면 이 중에서 우선 고르고,
정말 안 맞으면 새 이름을 제안해도 된다):
{_taxonomy_block(taxonomy)}

판단 기준: 폴더가 책 내용과 명백히 무관하면(예: 순수 프로그래밍 언어 책이
클라우드 인프라 폴더에 있는 경우) folder_matches를 false로, 폴더가 책 내용과
합리적으로 부합하면(다른 폴더도 말이 될 수 있지만 지금 폴더가 틀렸다고 할
정도는 아니면) true로 판단한다. 애매하면 true 쪽으로 판단해 불필요한 파일
이동을 피한다.

다음 JSON 형식으로만 답하라:
{{"folder_matches": true/false, "suggested_category": "...", "suggested_subcategory": "...", "reason": "..."}}
"""
    raw = call_claude(prompt)
    data = _extract_json(raw)

    suggested_category = _resolve_category(str(data.get("suggested_category", current_major)), taxonomy)
    suggested_subcategory = _resolve_subcategory(
        str(data.get("suggested_subcategory", current_sub or "")), suggested_category, taxonomy
    )
    return {
        "folder_matches": bool(data.get("folder_matches", True)),
        "suggested_category": suggested_category,
        "suggested_subcategory": suggested_subcategory,
        "reason": str(data.get("reason", "")).strip(),
    }
