"""Release 附件筛选与优先级评分。"""

from __future__ import annotations

import fnmatch
from typing import Iterable

from .events import Asset
from .settings import AssetSettings


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, p.lower()) for p in patterns)


def _score(asset: Asset, settings: AssetSettings) -> int:
    name = asset.name.lower()
    score = 0

    for index, pattern in enumerate(settings.patterns):
        if fnmatch.fnmatchcase(name, pattern.lower()):
            # 越靠前的匹配规则优先级越高
            score += (len(settings.patterns) - index) * 3
            break

    if settings.prefer_64bit:
        if any(kw.lower() in name for kw in settings.prefer_keywords):
            score += 20
        elif any(kw.lower() in name for kw in settings.avoid_keywords):
            score -= 20

    return score


def select_assets(
    assets: Iterable[Asset],
    settings: AssetSettings,
) -> list[Asset]:
    """按匹配规则和 64 位优先策略挑选附件。"""
    candidates = list(assets)
    matched = [a for a in candidates if _matches(a.name, settings.patterns)]
    if not matched and settings.fallback_all:
        matched = candidates

    matched.sort(key=lambda item: (-_score(item, settings), item.name.lower()))
    if settings.max_count > 0:
        return matched[: settings.max_count]
    return matched


def max_bytes(settings: AssetSettings) -> int:
    return settings.max_mb * 1024 * 1024


def format_size(size: int) -> str:
    if size <= 0:
        return "未知大小"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"
