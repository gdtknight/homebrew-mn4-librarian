"""library_dir 폴더 구조에서 분류체계(대분류/소분류)를 실시간으로 읽어온다.

분류체계를 코드나 설정 파일에 하드코딩하지 않는다 — 사용자마다 읽는 책의
분야가 다르므로, "library_dir 아래 2단계 디렉토리 구조" 자체를 분류체계의
유일한 소스로 삼는다. 새 폴더가 생기면 다음 스캔부터 자동으로 반영된다.
"""
from __future__ import annotations

from pathlib import Path

from . import config


def _visible_dirs(path: Path, exclude: frozenset[str] = frozenset()) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(
        p.name
        for p in path.iterdir()
        if p.is_dir()
        and not p.name.startswith(config.IGNORED_FILENAME_PREFIXES)
        and p.name not in exclude
    )


def scan(library_dir: Path, exclude: frozenset[str] = frozenset()) -> dict[str, list[str]]:
    """library_dir 바로 아래 디렉토리를 대분류, 그 아래 디렉토리를 소분류로 읽는다.

    exclude: 대분류로 취급하면 안 되는 디렉토리 이름 (예: library_dir 안에 신규
    PDF 인박스 폴더가 함께 있는 구성이면 그 폴더명을 넘긴다)."""
    result: dict[str, list[str]] = {}
    for major in _visible_dirs(library_dir, exclude):
        result[major] = _visible_dirs(library_dir / major)
    return result


def exclude_for(settings_obj) -> frozenset[str]:
    """settings.Settings를 받아, library_dir 바로 아래 중첩된 inbox_dir가 있으면
    그 이름을 분류체계 스캔에서 제외할 집합으로 반환한다."""
    if settings_obj.inbox_dir.parent == settings_obj.library_dir:
        return frozenset({settings_obj.inbox_dir.name})
    return frozenset()


class TaxonomyView:
    """분류 작업 배치 동안 사용하는 뷰.

    --apply 모드에서는 앞선 책이 만든 폴더가 실제 디스크에 생기므로 매 책마다
    scan()을 다시 부르면 자연히 반영된다. dry-run 모드에서는 폴더가 생기지
    않으므로, 이번 배치에서 LLM이 방금 제안한 신규 카테고리를 메모리에 쌓아
    다음 책의 프롬프트에도 "이번 배치에서 제안된 카테고리"로 함께 넘긴다.
    이렇게 해야 같은 배치 안의 비슷한 책들이 "Machine Learning"/"ML"/"AI-ML"
    처럼 제각각 변종 카테고리를 만들지 않고 서로 재사용한다.
    """

    def __init__(self, library_dir: Path, exclude: frozenset[str] = frozenset()):
        self.library_dir = library_dir
        self.exclude = exclude
        self._proposed: dict[str, list[str]] = {}

    def current(self) -> dict[str, list[str]]:
        base = scan(self.library_dir, self.exclude)
        for major, subs in self._proposed.items():
            existing = base.setdefault(major, [])
            for sub in subs:
                if sub not in existing:
                    existing.append(sub)
        return base

    def has_any_major(self) -> bool:
        """이미 실제 폴더로 존재하는 대분류가 하나라도 있는지 (신규 대분류 가드 판단용).

        이번 배치에서 제안만 된(아직 폴더로 안 생긴) 대분류는 세지 않는다 —
        가드의 목적은 "기존 체계와 헷갈릴 위험"을 막는 것이라 실제 확정된
        구조 기준으로 판단해야 한다.
        """
        return bool(scan(self.library_dir, self.exclude))

    def record_proposal(self, major: str, subcategory: str | None) -> None:
        """dry-run에서 실제 폴더를 만들지 않고도 이후 프롬프트에 반영되도록 기록."""
        subs = self._proposed.setdefault(major, [])
        if subcategory and subcategory not in subs:
            subs.append(subcategory)
