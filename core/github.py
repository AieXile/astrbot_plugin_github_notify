"""GitHub REST API 客户端：事件轮询、对比提交、Release 附件下载。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from .events import Asset, CommitInfo

API_BASE = "https://api.github.com"
USER_AGENT = "astrbot-plugin-github-notify/1.0.0"

SessionFactory = Callable[[float], Any]

_REDIRECT_STATUS = {301, 302, 303, 307, 308}
_SAFE_FILENAME = re.compile(r"[^\w.\-]+", re.UNICODE)


class GitHubError(Exception):
    def __init__(
        self,
        status: int,
        message: str = "",
        retry_after: int = 0,
    ) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.retry_after = max(0, retry_after)


class AssetTooLarge(Exception):
    def __init__(self, size: int, limit: int) -> None:
        super().__init__(f"asset {size} bytes exceeds {limit} bytes")
        self.size = size
        self.limit = limit


@dataclass(frozen=True)
class EventsPage:
    events: tuple[dict[str, Any], ...] = ()
    etag: str = ""
    not_modified: bool = False


@dataclass(frozen=True)
class BranchInfo:
    name: str
    sha: str
    message: str
    author: str
    date: str


def _default_session_factory(timeout_seconds: float) -> Any:
    import aiohttp

    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout_seconds)
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _retry_after(headers: Mapping[str, str] | Any | None) -> int:
    if headers is None:
        return 0
    try:
        raw = headers.get("Retry-After")
    except AttributeError:
        return 0
    if raw:
        try:
            return max(0, int(str(raw).strip()))
        except ValueError:
            pass
    try:
        reset = int(str(headers.get("X-RateLimit-Reset") or "").strip())
        remaining = str(headers.get("X-RateLimit-Remaining") or "").strip()
    except (TypeError, ValueError):
        return 0
    if remaining == "0" and reset:
        return max(0, min(3600, reset - int(time.time())))
    return 0


def _message_from_body(body: str) -> str:
    try:
        data = json.loads(body) if body else {}
    except (TypeError, ValueError):
        return body[:120]
    if isinstance(data, dict):
        return str(data.get("message") or "")[:120]
    return ""


def sanitize_filename(name: str) -> str:
    base = Path(str(name or "")).name
    cleaned = _SAFE_FILENAME.sub("_", base).strip("._ ")
    return cleaned[:120] or "asset.bin"


class GitHubClient:
    def __init__(
        self,
        token: str = "",
        timeout_seconds: float = 30,
        api_base: str = API_BASE,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._token = token
        self._timeout = timeout_seconds
        self._api_base = api_base.rstrip("/")
        self._session_factory = session_factory or _default_session_factory

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, Any]:
        async with self._session_factory(self._timeout) as session:
            async with session.get(
                url, params=params, headers=self._headers(headers)
            ) as response:
                return (
                    int(response.status),
                    await response.text(),
                    response.headers,
                )

    async def fetch_events(
        self,
        repo: str,
        etag: str = "",
    ) -> EventsPage:
        headers = {"If-None-Match": etag} if etag else None
        status, body, response_headers = await self._request(
            f"{self._api_base}/repos/{repo}/events",
            params={"per_page": 100},
            headers=headers,
        )
        if status == 304:
            return EventsPage(not_modified=True, etag=etag)
        if status != 200:
            raise GitHubError(
                status,
                _message_from_body(body),
                _retry_after(response_headers),
            )
        try:
            data = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise GitHubError(200, f"invalid json: {exc}") from exc
        if not isinstance(data, list):
            raise GitHubError(200, "events response is not a list")
        events = tuple(item for item in data if isinstance(item, dict))
        return EventsPage(
            events=events,
            etag=str(response_headers.get("ETag") or ""),
        )

    async def fetch_branches(self, repo: str) -> tuple[BranchInfo, ...]:
        """获取仓库前 100 个分支的最新 head。

        Events API 的 PushEvent 可能缺失或延迟，Push 通知以这里的分支
        head 变化为准。
        """
        status, body, response_headers = await self._request(
            f"{self._api_base}/repos/{repo}/branches",
            params={"per_page": 100},
        )
        if status != 200:
            raise GitHubError(
                status,
                _message_from_body(body),
                _retry_after(response_headers),
            )
        try:
            data = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise GitHubError(status, f"invalid json: {exc}") from exc
        if not isinstance(data, list):
            raise GitHubError(status, "branches response is not a list")

        branches = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            commit = _as_dict(item.get("commit"))
            sha = str(commit.get("sha") or "").strip()
            if not name or not sha:
                continue
            inner = _as_dict(commit.get("commit"))
            message = str(inner.get("message") or "").splitlines()
            author = str(_as_dict(commit.get("author")).get("login") or "")
            if not author:
                author = str(_as_dict(inner.get("author")).get("name") or "")
            branches.append(
                BranchInfo(
                    name=name,
                    sha=sha,
                    message=message[0].strip() if message else "",
                    author=author,
                    date=str(_as_dict(inner.get("author")).get("date") or ""),
                )
            )
        return tuple(branches)

    async def fetch_commit(self, repo: str, sha: str) -> CommitInfo | None:
        """获取单个提交的简要信息（分支列表接口不含提交说明）。"""
        if not sha:
            return None
        try:
            data = await self.get_json(f"/repos/{repo}/commits/{sha}")
        except GitHubError:
            return None
        if not isinstance(data, dict):
            return None
        commit = _as_dict(data.get("commit"))
        message = str(commit.get("message") or "").splitlines()
        author = str(_as_dict(data.get("author")).get("login") or "")
        if not author:
            author = str(_as_dict(commit.get("author")).get("name") or "")
        return CommitInfo(
            sha=str(data.get("sha") or sha),
            message=message[0].strip() if message else "",
            author=author,
        )

    async def get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        status, body, response_headers = await self._request(
            f"{self._api_base}{path}", params=params
        )
        if status != 200:
            raise GitHubError(
                status,
                _message_from_body(body),
                _retry_after(response_headers),
            )
        try:
            return json.loads(body)
        except (TypeError, ValueError) as exc:
            raise GitHubError(status, f"invalid json: {exc}") from exc

    async def previous_release_tag(self, repo: str, current_tag: str) -> str:
        """返回当前 release 的上一个非草稿 tag；找不到返回空。"""
        try:
            releases = await self.get_json(
                f"/repos/{repo}/releases", params={"per_page": 10}
            )
        except GitHubError:
            return ""
        if not isinstance(releases, list):
            return ""
        tags = [
            str(item.get("tag_name") or "").strip()
            for item in releases
            if isinstance(item, dict)
            and not item.get("draft")
            and str(item.get("tag_name") or "").strip()
        ]
        if current_tag in tags:
            index = tags.index(current_tag)
            return tags[index + 1] if index + 1 < len(tags) else ""
        for tag in tags:
            if tag != current_tag:
                return tag
        return ""

    async def compare_commits(
        self,
        repo: str,
        base: str,
        head: str,
        limit: int = 20,
    ) -> tuple[CommitInfo, ...]:
        if not base or not head:
            return ()
        path = (
            f"/repos/{repo}/compare/{quote(base, safe='')}..."
            f"{quote(head, safe='')}"
        )
        try:
            data = await self.get_json(path)
        except GitHubError:
            return ()
        commits = data.get("commits") if isinstance(data, dict) else None
        if not isinstance(commits, list):
            return ()
        details = []
        for item in commits[:limit]:
            if not isinstance(item, dict):
                continue
            commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
            message = str(commit.get("message") or "")
            first_line = message.splitlines()[0].strip() if message else ""
            author = (
                str(commit.get("author", {}).get("name") or "")
                if isinstance(commit.get("author"), dict)
                else ""
            )
            details.append(
                CommitInfo(
                    sha=str(item.get("sha") or ""),
                    message=first_line,
                    author=author,
                )
            )
        return tuple(details)

    async def download_asset(
        self,
        asset: Asset,
        dest_dir: Path,
        max_bytes: int,
    ) -> Path:
        if asset.size and asset.size > max_bytes:
            raise AssetTooLarge(asset.size, max_bytes)
        source = asset.api_url or asset.browser_url
        if not source:
            raise GitHubError(0, "asset has no download url")

        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{asset.id or 'asset'}-{sanitize_filename(asset.name)}"
        dest = dest_dir / filename

        async with self._session_factory(self._timeout) as session:
            headers = self._headers({"Accept": "application/octet-stream"})
            async with session.get(
                source, headers=headers, allow_redirects=False
            ) as response:
                status = int(response.status)
                if status in _REDIRECT_STATUS and response.headers.get(
                    "Location"
                ):
                    location = str(response.headers.get("Location"))
                    async with session.get(
                        location,
                        headers={
                            "Accept": "application/octet-stream",
                            "User-Agent": USER_AGENT,
                        },
                        allow_redirects=True,
                    ) as final:
                        return await self._stream_to_file(
                            final, dest, max_bytes
                        )
                return await self._stream_to_file(
                    response, dest, max_bytes
                )

    async def _stream_to_file(
        self,
        response: Any,
        dest: Path,
        max_bytes: int,
    ) -> Path:
        status = int(response.status)
        if status != 200:
            raise GitHubError(status, "asset download failed")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise AssetTooLarge(int(content_length), max_bytes)
            except ValueError:
                pass

        partial = dest.with_name(dest.name + ".part")
        total = 0
        try:
            with partial.open("wb") as handle:
                async for chunk in response.content.iter_chunked(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise AssetTooLarge(total, max_bytes)
                    handle.write(chunk)
            partial.replace(dest)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return dest
