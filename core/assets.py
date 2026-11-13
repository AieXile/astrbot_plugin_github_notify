"""Release 附件筛选与优先级评分。"""

from __future__ import annotations

import fnmatch
from typing import Iterable

from .events import Asset
from .settings import AssetSettings


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, p.lower()) for p in patterns)


# 常见 ABI 缩写不一定出现在用户配置的关键字里（如 v8a / v7a），
# 这里内置识别，避免 universal 包或 32 位包被误选。
_BUILTIN_64BIT = ("arm64", "aarch64", "x86_64", "x64", "win64", "amd64", "v8a")
_BUILTIN_32BIT = ("armeabi", "armv7", "v7a", "win32", "i386", "x86")


def _contains(name: str, keywords: tuple[str, ...]) -> bool:
    return any(kw.lower() in name for kw in keywords)


def _score(asset: Asset, settings: AssetSettings) -> int:
    name = asset.name.lower()
    score = 0

    for index, pattern in enumerate(settings.patterns):
        if fnmatch.fnmatchcase(name, pattern.lower()):
            # 越靠前的匹配规则优先级越高
            score += (len(settings.patterns) - index) * 3
            break

    if settings.prefer_64bit:
        if _contains(name, settings.prefer_keywords) or _contains(
            name, _BUILTIN_64BIT
        ):
            score += 20
        elif _contains(name, settings.avoid_keywords) or _contains(
            name, _BUILTIN_32BIT
        ):
            score -= 20

    return score


def select_assets(
    assets: Iterable[Asset],
    settings: AssetSettings,
    limit_bytes: int = 0,
) -> list[Asset]:
    """按匹配规则和 64 位优先策略挑选附件。

    limit_bytes > 0 时先剔除超出大小上限的附件，避免高优先级的大包
    （例如 universal 包）挤掉同版本中可发送的附件。
    """
    candidates = list(assets)
    matched = [a for a in candidates if _matches(a.name, settings.patterns)]
    if not matched and settings.fallback_all:
        matched = candidates

    if limit_bytes > 0:
        fitting = [
            a for a in matched if not a.size or a.size <= limit_bytes
        ]
        if fitting:
            matched = fitting

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
