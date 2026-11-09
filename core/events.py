"""GitHub Events API 事件解析。"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .settings import RepoSettings, Settings


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    size: int
    api_url: str
    browser_url: str


@dataclass(frozen=True)
class IssueInfo:
    repo: str
    action: str
    number: int
    title: str
    author: str
    labels: tuple[str, ...]
    body: str
    url: str


@dataclass(frozen=True)
class ReleaseInfo:
    repo: str
    tag: str
    name: str
    body: str
    url: str
    assets: tuple[Asset, ...]
    prerelease: bool
    published_at: str


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    message: str
    author: str


@dataclass(frozen=True)
class PushInfo:
    repo: str
    branch: str
    before: str
    head: str
    pusher: str
    commits: tuple[CommitInfo, ...]
    total: int


EventInfo = IssueInfo | ReleaseInfo | PushInfo


def _repo_name(raw: dict[str, Any], fallback: str) -> str:
    return _text(_dict(raw.get("repo")).get("name")) or fallback


def _parse_asset(item: Any) -> Asset | None:
    data = _dict(item)
    name = _text(data.get("name"))
    if not name:
        return None
    return Asset(
        id=_text(data.get("id")),
        name=name,
        size=_int(data.get("size")),
        api_url=_text(data.get("url")),
        browser_url=_text(data.get("browser_download_url")),
    )


def _parse_issue(
    raw: dict[str, Any],
    repo: str,
    settings: Settings,
) -> IssueInfo | None:
    payload = _dict(raw.get("payload"))
    action = _text(payload.get("action")).lower()
    if action not in settings.issue_actions:
        return None
    issue = _dict(payload.get("issue"))
    if issue.get("pull_request"):
        return None
    labels = tuple(
        _text(_dict(label).get("name"))
        for label in (issue.get("labels") or [])
        if _text(_dict(label).get("name"))
    )
    return IssueInfo(
        repo=repo,
        action=action,
        number=_int(issue.get("number")),
        title=_text(issue.get("title")),
        author=_text(_dict(issue.get("user")).get("login")),
        labels=labels,
        body=_text(issue.get("body")),
        url=_text(issue.get("html_url")),
    )


def _parse_release(
    raw: dict[str, Any],
    repo: str,
    settings: Settings,
) -> ReleaseInfo | None:
    payload = _dict(raw.get("payload"))
    action = _text(payload.get("action")).lower()
    if action not in {"published", "released"}:
        return None
    release = _dict(payload.get("release"))
    if _bool(release.get("draft")):
        return None
    prerelease = _bool(release.get("prerelease"))
    if prerelease and not settings.include_prerelease:
        return None
    assets = tuple(
        asset
        for asset in (_parse_asset(item) for item in (release.get("assets") or []))
        if asset is not None
    )
    return ReleaseInfo(
        repo=repo,
        tag=_text(release.get("tag_name")),
        name=_text(release.get("name")),
        body=_text(release.get("body")),
        url=_text(release.get("html_url")),
        assets=assets,
        prerelease=prerelease,
        published_at=_text(release.get("published_at")),
    )


def _bool(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def branch_allowed(branch: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    lowered = branch.lower()
    return any(
        fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in patterns
    )


def _parse_push(
    raw: dict[str, Any],
    repo: str,
    repo_cfg: RepoSettings,
    settings: Settings,
) -> PushInfo | None:
    payload = _dict(raw.get("payload"))
    if _bool(payload.get("deleted")):
        return None
    ref = _text(payload.get("ref"))
    if not ref.startswith("refs/heads/"):
        return None
    branch = ref[len("refs/heads/") :]
    patterns = repo_cfg.branches or ()
    if not branch_allowed(branch, patterns):
        return None

    commits = []
    for item in payload.get("commits") or []:
        data = _dict(item)
        message = _text(data.get("message")).splitlines()
        commits.append(
            CommitInfo(
                sha=_text(data.get("sha")),
                message=message[0] if message else "",
                author=_text(_dict(data.get("author")).get("name")),
            )
        )
    # 注意：Events API 的 PushEvent 通常不包含 commits/size，
    # 这里只解析基础信息，提交明细由 service 用 compare API 补齐。
    total = max(_int(payload.get("size"), len(commits)), len(commits))
    return PushInfo(
        repo=repo,
        branch=branch,
        before=_text(payload.get("before")),
        head=_text(payload.get("head")),
        pusher=_text(_dict(raw.get("actor")).get("login"))
        or _text(_dict(payload.get("pusher")).get("name")),
        commits=tuple(commits),
        total=total,
    )


def parse_event(
    raw: dict[str, Any],
    repo_cfg: RepoSettings,
    settings: Settings,
) -> EventInfo | None:
    """把原始事件解析成可格式化的对象；不关心的事件返回 None。"""
    event_type = _text(raw.get("type"))
    repo = _repo_name(raw, repo_cfg.repo)
    if event_type == "IssuesEvent" and repo_cfg.issue:
        return _parse_issue(raw, repo, settings)
    if event_type == "ReleaseEvent" and repo_cfg.release:
        return _parse_release(raw, repo, settings)
    if event_type == "PushEvent" and repo_cfg.push:
        return _parse_push(raw, repo, repo_cfg, settings)
    return None


def event_key(raw: dict[str, Any]) -> str:
    """事件去重键；优先使用 GitHub 的 event id。"""
    event_id = _text(raw.get("id"))
    if event_id:
        return event_id
    digest = hashlib.sha1(
        json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest
