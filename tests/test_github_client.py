"""GitHub 客户端自测（mock HTTP）。"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_github_notify.core.events import Asset  # noqa: E402
from astrbot_plugin_github_notify.core.github import (  # noqa: E402
    AssetTooLarge,
    GitHubClient,
    GitHubError,
    sanitize_filename,
)


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.content = FakeContent(chunks or [])

    async def text(self) -> str:
        return self._body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeSession:
    def __init__(self, responses: list[Any], calls: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._calls = calls

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self._calls.append({"url": url, **kwargs})
        if not self._responses:
            raise RuntimeError("no fake response")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def make_client(responses: list[Any]) -> tuple[GitHubClient, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    client = GitHubClient(
        token="test-token",
        timeout_seconds=30,
        api_base="https://api.test",
        session_factory=lambda timeout: FakeSession(responses, calls),
    )
    return client, calls


class TestFetchEvents(unittest.IsolatedAsyncioTestCase):
    async def test_200_with_etag(self) -> None:
        events = [{"id": "1", "type": "PushEvent"}]
        client, calls = make_client(
            [FakeResponse(200, json.dumps(events), {"ETag": 'W/"abc"'})]
        )
        page = await client.fetch_events("owner/repo", "")
        self.assertEqual(page.events, tuple(events))
        self.assertEqual(page.etag, 'W/"abc"')
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer test-token")

    async def test_304(self) -> None:
        client, calls = make_client([FakeResponse(304, "", {})])
        page = await client.fetch_events("owner/repo", 'W/"abc"')
        self.assertTrue(page.not_modified)
        self.assertEqual(calls[0]["headers"]["If-None-Match"], 'W/"abc"')

    async def test_403_rate_limit(self) -> None:
        reset = int(time.time()) + 120
        client, _ = make_client(
            [
                FakeResponse(
                    403,
                    json.dumps({"message": "API rate limit exceeded"}),
                    {
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset),
                    },
                )
            ]
        )
        with self.assertRaises(GitHubError) as ctx:
            await client.fetch_events("owner/repo")
        self.assertEqual(ctx.exception.status, 403)
        self.assertGreater(ctx.exception.retry_after, 0)

    async def test_429_retry_after(self) -> None:
        client, _ = make_client(
            [FakeResponse(429, "{}", {"Retry-After": "30"})]
        )
        with self.assertRaises(GitHubError) as ctx:
            await client.fetch_events("owner/repo")
        self.assertEqual(ctx.exception.retry_after, 30)

    async def test_invalid_json(self) -> None:
        client, _ = make_client([FakeResponse(200, "not json")])
        with self.assertRaises(GitHubError):
            await client.fetch_events("owner/repo")


class TestBranches(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_branches(self) -> None:
        payload = [
            {
                "name": "main",
                "commit": {
                    "sha": "b" * 40,
                    "commit": {
                        "message": "feat: 公告投票入口\n\nbody",
                        "author": {"date": "2026-11-08T18:11:02Z"},
                    },
                    "author": {"login": "AieXile"},
                },
            }
        ]
        client, _ = make_client([FakeResponse(200, json.dumps(payload))])
        branches = await client.fetch_branches("owner/repo")
        self.assertEqual(len(branches), 1)
        self.assertEqual(branches[0].name, "main")
        self.assertEqual(branches[0].sha, "b" * 40)
        self.assertEqual(branches[0].message, "feat: 公告投票入口")
        self.assertEqual(branches[0].author, "AieXile")

    async def test_fetch_branches_real_shape(self) -> None:
        # GitHub /branches 的 commit 只有 sha/url，没有提交说明
        payload = [
            {
                "name": "main",
                "commit": {
                    "sha": "c" * 40,
                    "url": "https://api.github.com/repos/o/r/commits/c",
                },
            }
        ]
        client, _ = make_client([FakeResponse(200, json.dumps(payload))])
        branches = await client.fetch_branches("owner/repo")
        self.assertEqual(branches[0].sha, "c" * 40)
        self.assertEqual(branches[0].message, "")

    async def test_fetch_branches_error(self) -> None:
        client, _ = make_client([FakeResponse(404, "{}")])
        with self.assertRaises(GitHubError):
            await client.fetch_branches("owner/repo")

    async def test_fetch_commit(self) -> None:
        payload = {
            "sha": "c" * 40,
            "commit": {
                "message": "feat: 公告投票入口\n\nbody",
                "author": {"name": "AieXile", "date": "2026-11-08T18:11:02Z"},
            },
            "author": {"login": "AieXile"},
        }
        client, _ = make_client([FakeResponse(200, json.dumps(payload))])
        commit = await client.fetch_commit("owner/repo", "c" * 40)
        self.assertEqual(commit.message, "feat: 公告投票入口")
        self.assertEqual(commit.author, "AieXile")


class TestReleaseApi(unittest.IsolatedAsyncioTestCase):
    async def test_previous_release_tag(self) -> None:
        releases = [
            {"tag_name": "v2.0.0", "draft": False},
            {"tag_name": "v1.5.0", "draft": False},
            {"tag_name": "v1.0.0", "draft": False},
        ]
        client, _ = make_client([FakeResponse(200, json.dumps(releases))])
        self.assertEqual(
            await client.previous_release_tag("owner/repo", "v1.5.0"),
            "v1.0.0",
        )

    async def test_compare_commits(self) -> None:
        payload = {
            "commits": [
                {"commit": {"message": "fix crash\n\nbody"}},
                {"commit": {"message": "feat: player"}},
            ]
        }
        client, _ = make_client([FakeResponse(200, json.dumps(payload))])
        details = await client.compare_commits("owner/repo", "v1", "v2")
        self.assertEqual(
            [item.message for item in details],
            ["fix crash", "feat: player"],
        )


class TestDownload(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_download(self) -> None:
        asset = Asset(
            "7",
            "app-arm64-v8a.apk",
            10,
            "https://api.test/assets/7",
            "https://github.com/download/7",
        )
        client, calls = make_client(
            [
                FakeResponse(302, "", {"Location": "https://cdn.test/app.apk"}),
                FakeResponse(200, "", {"Content-Length": "10"}, [b"12345", b"67890"]),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = await client.download_asset(asset, Path(tmp), 100)
            self.assertEqual(path.read_bytes(), b"1234567890")
            self.assertTrue(path.name.startswith("7-"))
        self.assertEqual(len(calls), 2)
        self.assertNotIn("Authorization", calls[1]["headers"])

    async def test_size_limit_before_download(self) -> None:
        asset = Asset("8", "big.apk", 200, "https://api.test/assets/8", "")
        client, _ = make_client([])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AssetTooLarge):
                await client.download_asset(asset, Path(tmp), 100)

    async def test_stream_limit(self) -> None:
        asset = Asset("9", "big.apk", 0, "https://api.test/assets/9", "")
        client, _ = make_client(
            [FakeResponse(200, "", {}, [b"x" * 60, b"y" * 60])]
        )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AssetTooLarge):
                await client.download_asset(asset, Path(tmp), 100)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    async def test_sanitize_filename(self) -> None:
        self.assertEqual(sanitize_filename("../a b.apk"), "a_b.apk")
        self.assertEqual(sanitize_filename(""), "asset.bin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
