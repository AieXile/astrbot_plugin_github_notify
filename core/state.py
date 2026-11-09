"""轮询状态：ETag、已处理事件 id、首次基线标记。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_SEEN_IDS = 200
STATE_KEY = "github_notify_state"


@dataclass
class RepoState:
    etag: str = ""
    seen: list[str] = field(default_factory=list)
    initialized: bool = False
    heads: dict[str, str] = field(default_factory=dict)


def get_repo_state(raw: dict[str, Any], repo: str) -> RepoState:
    repos = raw.get("repos") if isinstance(raw, dict) else None
    item = repos.get(repo.lower()) if isinstance(repos, dict) else None
    if not isinstance(item, dict):
        return RepoState()
    seen = item.get("seen")
    heads = item.get("heads")
    return RepoState(
        etag=str(item.get("etag") or ""),
        seen=[str(value) for value in seen] if isinstance(seen, list) else [],
        initialized=bool(item.get("initialized")),
        heads={
            str(name): str(sha)
            for name, sha in heads.items()
            if str(name) and str(sha)
        }
        if isinstance(heads, dict)
        else {},
    )


def set_repo_state(
    raw: dict[str, Any],
    repo: str,
    state: RepoState,
) -> None:
    repos = raw.setdefault("repos", {})
    if not isinstance(repos, dict):
        raw["repos"] = repos = {}
    repos[repo.lower()] = {
        "etag": state.etag,
        "seen": state.seen[-MAX_SEEN_IDS:],
        "initialized": state.initialized,
        "heads": state.heads,
    }


def is_seen(seen: list[str], key: str) -> bool:
    return key in set(seen)


def mark_seen(seen: list[str], keys: list[str] | tuple[str, ...]) -> list[str]:
    result = list(seen)
    existing = set(result)
    for key in keys:
        if key and key not in existing:
            result.append(key)
            existing.add(key)
    return result[-MAX_SEEN_IDS:]
