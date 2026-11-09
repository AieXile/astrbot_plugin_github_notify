"""把配置中的群号 / QQ 号 / UMO 解析成可发送的会话标识。"""

from __future__ import annotations

from typing import Any, Iterable

from .settings import RepoSettings, Settings


def resolve_platform_id(platform_insts: Iterable[Any], configured: str) -> str:
    """返回 aiocqhttp 平台实例 ID；优先使用配置值。"""
    if configured:
        return configured
    for platform in platform_insts:
        try:
            meta = platform.meta()
        except Exception:
            continue
        if getattr(meta, "name", "") == "aiocqhttp":
            return str(getattr(meta, "id", "") or "")
    return ""


def build_umos(
    *,
    group_ids: Iterable[str],
    user_ids: Iterable[str],
    umo_targets: Iterable[str],
    platform_id: str,
) -> list[str]:
    """按群号 / QQ 号 / UMO 生成去重后的目标列表。"""
    result: list[str] = []
    for umo in umo_targets:
        text = str(umo).strip()
        if text and text not in result:
            result.append(text)
    if platform_id:
        for group_id in group_ids:
            text = str(group_id).strip()
            if not text:
                continue
            umo = f"{platform_id}:GroupMessage:{text}"
            if umo not in result:
                result.append(umo)
        for user_id in user_ids:
            text = str(user_id).strip()
            if not text:
                continue
            umo = f"{platform_id}:FriendMessage:{text}"
            if umo not in result:
                result.append(umo)
    return result


def targets_for_repo(
    repo: RepoSettings,
    settings: Settings,
    platform_id: str,
) -> list[str]:
    """仓库配置了任意目标则只用仓库的，否则继承全局。"""
    has_override = bool(repo.group_ids or repo.user_ids or repo.umo_targets)
    group_ids = repo.group_ids if has_override else settings.group_ids
    user_ids = repo.user_ids if has_override else settings.user_ids
    umo_targets = repo.umo_targets if has_override else settings.umo_targets
    return build_umos(
        group_ids=group_ids,
        user_ids=user_ids,
        umo_targets=umo_targets,
        platform_id=platform_id,
    )
