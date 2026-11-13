"""插件配置读取与规范化。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

DEFAULT_INSTRUCTION = (
    "请把下面的 GitHub Release 更新日志总结成中文要点，"
    "保留破坏性变更和升级注意事项，不要编造内容，"
    "总字数不超过 {max_chars} 字。"
)

_REPO_PATTERN = re.compile(r"^[^/\s]+/[^/\s]+$")


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _as_int(
    value: Any,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        items = []
    result: list[str] = []
    for item in items:
        text = _as_text(item)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def normalize_repo(value: Any) -> str:
    """把 owner/repo 或 GitHub URL 规范化为 owner/repo。"""
    text = _as_text(value)
    if not text:
        return ""
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "https://www.github.com/",
        "github.com/",
    ):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.split("?", 1)[0].split("#", 1)[0].strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text if _REPO_PATTERN.match(text) else ""


@dataclass(frozen=True)
class AssetSettings:
    patterns: tuple[str, ...]
    prefer_64bit: bool
    prefer_keywords: tuple[str, ...]
    avoid_keywords: tuple[str, ...]
    max_count: int
    max_mb: int
    fallback_all: bool


@dataclass(frozen=True)
class RepoSettings:
    repo: str
    enabled: bool = True
    issue: bool = True
    release: bool = True
    push: bool = True
    branches: tuple[str, ...] = ()
    group_ids: tuple[str, ...] = ()
    user_ids: tuple[str, ...] = ()
    umo_targets: tuple[str, ...] = ()
    asset_patterns: tuple[str, ...] = ()
    summary_max_chars: int = 0


@dataclass(frozen=True)
class Settings:
    github_token: str = ""
    repositories: tuple[RepoSettings, ...] = ()
    poll_interval: int = 300
    platform_id: str = ""
    group_ids: tuple[str, ...] = ()
    user_ids: tuple[str, ...] = ()
    umo_targets: tuple[str, ...] = ()
    issue_actions: tuple[str, ...] = ("opened",)
    issue_body_chars: int = 200
    push_max_commits: int = 5
    include_prerelease: bool = True
    notify_on_first_run: bool = False
    release_use_summary: bool = False
    release_body_chars: int = 3000
    summary_provider: str = ""
    summary_max_chars: int = 100
    summary_instruction: str = DEFAULT_INSTRUCTION
    summary_input_chars: int = 6000
    send_assets: bool = True
    asset_settings: AssetSettings = field(
        default_factory=lambda: AssetSettings(
            patterns=("*.apk", "*.exe"),
            prefer_64bit=True,
            prefer_keywords=(
                "arm64-v8a",
                "arm64",
                "aarch64",
                "x86_64",
                "x64",
                "win64",
                "amd64",
                "v8a",
            ),
            avoid_keywords=("armeabi-v7a", "armv7", "v7a", "x86", "win32", "i386"),
            max_count=1,
            max_mb=50,
            fallback_all=False,
        )
    )
    timeout: int = 30

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any] | None) -> "Settings":
        config = config or {}
        asset_settings = AssetSettings(
            patterns=_as_list(config.get("asset_patterns"))
            or ("*.apk", "*.exe"),
            prefer_64bit=_as_bool(config.get("asset_prefer_64bit"), True),
            prefer_keywords=_as_list(config.get("asset_prefer_keywords"))
            or (
                "arm64-v8a",
                "arm64",
                "aarch64",
                "x86_64",
                "x64",
                "win64",
                "amd64",
                "v8a",
            ),
            avoid_keywords=_as_list(config.get("asset_avoid_keywords"))
            or ("armeabi-v7a", "armv7", "v7a", "x86", "win32", "i386"),
            max_count=_as_int(config.get("asset_max_count"), 1, 0, 10),
            max_mb=_as_int(config.get("asset_max_mb"), 50, 1, 500),
            fallback_all=_as_bool(config.get("asset_fallback_all"), False),
        )
        instruction = _as_text(config.get("summary_instruction"))
        return cls(
            github_token=_as_text(config.get("github_token")),
            repositories=_parse_repositories(config.get("repositories")),
            poll_interval=_as_int(config.get("poll_interval"), 300, 60, 3600),
            platform_id=_as_text(config.get("platform_id")),
            group_ids=_as_list(config.get("group_ids")),
            user_ids=_as_list(config.get("user_ids")),
            umo_targets=_as_list(config.get("umo_targets")),
            issue_actions=tuple(
                item.lower()
                for item in (
                    _as_list(config.get("issue_actions")) or ("opened",)
                )
            ),
            issue_body_chars=_as_int(
                config.get("issue_body_chars"), 200, 0, 1000
            ),
            push_max_commits=_as_int(config.get("push_max_commits"), 5, 1, 20),
            include_prerelease=_as_bool(
                config.get("include_prerelease"), True
            ),
            notify_on_first_run=_as_bool(
                config.get("notify_on_first_run"), False
            ),
            release_use_summary=_as_bool(
                config.get("release_use_summary"), False
            ),
            release_body_chars=_as_int(
                config.get("release_body_chars"), 3000, 0, 5000
            ),
            summary_provider=_as_text(config.get("summary_provider")),
            summary_max_chars=_as_int(
                config.get("summary_max_chars"), 100, 30, 1000
            ),
            summary_instruction=instruction or DEFAULT_INSTRUCTION,
            summary_input_chars=_as_int(
                config.get("summary_input_chars"), 6000, 1000, 20000
            ),
            send_assets=_as_bool(config.get("send_assets"), True),
            asset_settings=asset_settings,
            timeout=_as_int(config.get("timeout"), 30, 10, 120),
        )

    @property
    def enabled_repositories(self) -> tuple[RepoSettings, ...]:
        return tuple(repo for repo in self.repositories if repo.enabled)


def _parse_repositories(value: Any) -> tuple[RepoSettings, ...]:
    if not isinstance(value, list):
        return ()
    repos: list[RepoSettings] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        repo = normalize_repo(item.get("repo"))
        key = repo.lower()
        if not repo or key in seen:
            continue
        seen.add(key)
        repos.append(
            RepoSettings(
                repo=repo,
                enabled=_as_bool(item.get("enabled"), True),
                issue=_as_bool(item.get("issue"), True),
                release=_as_bool(item.get("release"), True),
                push=_as_bool(item.get("push"), True),
                branches=_as_list(item.get("branches")),
                group_ids=_as_list(item.get("group_ids")),
                user_ids=_as_list(item.get("user_ids")),
                umo_targets=_as_list(item.get("umo_targets")),
                asset_patterns=_as_list(item.get("asset_patterns")),
                summary_max_chars=_as_int(
                    item.get("summary_max_chars"), 0, 0, 1000
                ),
            )
        )
    return tuple(repos)
