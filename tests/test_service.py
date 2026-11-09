"""轮询服务自测：首次基线、去重、分仓库目标、Release 摘要与附件。"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ---- 最小 AstrBot 桩，仅用于 dispatcher 构建消息链 ----
astrbot = types.ModuleType("astrbot")
astrbot.__path__ = []
api = types.ModuleType("astrbot.api")
api.__path__ = []
event_mod = types.ModuleType("astrbot.api.event")


class MessageChain:
    def __init__(self, chain: list[Any] | None = None) -> None:
        self.chain = list(chain or [])

    def message(self, text: str) -> "MessageChain":
        self.chain.append(("text", text))
        return self


components = types.ModuleType("astrbot.api.message_components")


class File:
    def __init__(self, name: str, file: str = "", url: str = "") -> None:
        self.name = name
        self.file = file
        self.url = url


event_mod.MessageChain = MessageChain
components.File = File
api.event = event_mod
api.message_components = components
sys.modules.update(
    {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event_mod,
        "astrbot.api.message_components": components,
    }
)

from astrbot_plugin_github_notify.core.events import (  # noqa: E402
    Asset,
    CommitInfo,
    ReleaseInfo,
)
from astrbot_plugin_github_notify.core.github import (  # noqa: E402
    BranchInfo,
    EventsPage,
    GitHubError,
)
from astrbot_plugin_github_notify.core.service import (  # noqa: E402
    GitHubNotifyService,
)
from astrbot_plugin_github_notify.core.settings import Settings  # noqa: E402


class FakeLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.infos: list[str] = []

    def exception(self, message: str, *args: Any) -> None:
        self.errors.append(message % args if args else message)

    def info(self, message: str, *args: Any) -> None:
        self.infos.append(message % args if args else message)

    def warning(self, message: str, *args: Any) -> None:
        self.infos.append(message % args if args else message)


class FakePlatform:
    def meta(self):
        return SimpleNamespace(name="aiocqhttp", id="qq1")


class FakeContext:
    def __init__(self) -> None:
        self.platform_manager = SimpleNamespace(platform_insts=[FakePlatform()])
        self.sent: list[tuple[str, MessageChain]] = []

    async def send_message(self, umo: str, chain: MessageChain) -> bool:
        self.sent.append((umo, chain))
        return True

    async def get_using_provider_async(self):
        return SimpleNamespace(meta=lambda: SimpleNamespace(id="p1"))

    async def llm_generate(self, chat_provider_id: str, prompt: str):
        return SimpleNamespace(completion_text="修复闪退并优化播放")


class FakeClient:
    def __init__(
        self,
        pages: list[Any],
        branches: list[BranchInfo] | None = None,
    ) -> None:
        self.pages = list(pages)
        self.branches = list(
            branches
            if branches is not None
            else [
                BranchInfo(
                    "main",
                    "a" * 40,
                    "fix crash",
                    "alice",
                    "2026-11-08T01:00:00Z",
                )
            ]
        )
        self.calls: list[str] = []
        self.downloaded: list[str] = []
        self.download_paths: list[Path] = []
        self.compare_result: tuple[CommitInfo, ...] = (
            CommitInfo("a" * 40, "fix crash", "alice"),
        )
        self.commit_result = CommitInfo("a" * 40, "fix crash", "alice")

    async def fetch_events(self, repo: str, etag: str = "") -> EventsPage:
        self.calls.append(repo)
        if not self.pages:
            return EventsPage()
        page = self.pages.pop(0)
        if isinstance(page, BaseException):
            raise page
        return page

    async def fetch_branches(self, repo: str) -> tuple[BranchInfo, ...]:
        self.calls.append("branches")
        return tuple(self.branches)

    def set_branch_head(
        self,
        sha: str,
        message: str = "feat: 公告投票入口",
        author: str = "AieXile",
        date: str = "2026-11-08T18:11:02Z",
    ) -> None:
        self.branches = [
            BranchInfo("main", sha, message, author, date)
        ]

    async def previous_release_tag(self, repo: str, tag: str) -> str:
        self.calls.append("previous")
        return "v1.0.0"

    async def compare_commits(self, repo: str, base: str, head: str):
        self.calls.append("compare")
        return self.compare_result

    async def fetch_commit(self, repo: str, sha: str):
        self.calls.append("commit")
        return self.commit_result

    async def download_asset(self, asset: Asset, dest_dir: Path, limit: int) -> Path:
        self.calls.append("download")
        self.downloaded.append(asset.name)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / asset.name
        path.write_bytes(b"apk")
        self.download_paths.append(path)
        return path


def _issue(event_id: str = "1") -> dict:
    return {
        "id": event_id,
        "type": "IssuesEvent",
        "repo": {"name": "owner/repo"},
        "actor": {"login": "bob"},
        "payload": {
            "action": "opened",
            "issue": {
                "number": 3,
                "title": "新问题",
                "html_url": "https://github.com/owner/repo/issues/3",
                "body": "复现步骤",
                "labels": [{"name": "bug"}],
                "user": {"login": "bob"},
            },
        },
    }


def _release(event_id: str = "2", size: int = 1000) -> dict:
    return {
        "id": event_id,
        "type": "ReleaseEvent",
        "repo": {"name": "owner/repo"},
        "actor": {"login": "bob"},
        "payload": {
            "action": "published",
            "release": {
                "tag_name": "v1.2.0",
                "name": "v1.2.0",
                "body": "## Changes\n- fix crash",
                "html_url": "https://github.com/owner/repo/releases/tag/v1.2.0",
                "prerelease": False,
                "draft": False,
                "assets": [
                    {
                        "id": "1",
                        "name": "app-arm64-v8a.apk",
                        "size": size,
                        "url": "https://api.github.com/assets/1",
                        "browser_download_url": "https://github.com/a/1",
                    },
                    {
                        "id": "2",
                        "name": "app-armeabi-v7a.apk",
                        "size": size,
                        "url": "https://api.github.com/assets/2",
                        "browser_download_url": "https://github.com/a/2",
                    },
                ],
            },
        },
    }


def _push_without_commits(event_id: str = "3") -> dict:
    return {
        "id": event_id,
        "type": "PushEvent",
        "repo": {"name": "owner/repo"},
        "actor": {"login": "alice"},
        "payload": {
            "ref": "refs/heads/main",
            "before": "a" * 40,
            "head": "b" * 40,
            "deleted": False,
        },
    }


def _texts(context: FakeContext) -> list[str]:
    return [
        chain.chain[0][1]
        for _, chain in context.sent
        if chain.chain
        and isinstance(chain.chain[0], tuple)
        and chain.chain[0][0] == "text"
    ]


def _settings(**overrides: Any) -> Settings:
    config = {
        "github_token": "t",
        "poll_interval": 300,
        "repositories": [
            {
                "repo": "owner/repo",
                "group_ids": ["123"],
                "asset_patterns": ["*.apk"],
            }
        ],
        "summary_max_chars": 100,
    }
    config.update(overrides)
    return Settings.from_mapping(config)


class TestService(unittest.IsolatedAsyncioTestCase):
    async def _service(self, settings: Settings, client: FakeClient, state: dict):
        context = FakeContext()
        logger = FakeLogger()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        service = GitHubNotifyService(
            context=context,
            settings=settings,
            client=client,
            logger=logger,
            state=state,
            download_root=Path(tmp.name),
        )
        return service, context, logger

    async def test_first_run_baseline_no_send(self) -> None:
        client = FakeClient([EventsPage(events=(_issue(),))])
        service, context, _ = await self._service(_settings(), client, {})
        report = await service.poll_all()
        self.assertEqual(context.sent, [])
        self.assertEqual(report.polled, 1)
        self.assertEqual(client.calls, ["owner/repo", "branches"])

    async def test_dedupe_and_new_issue(self) -> None:
        page1 = EventsPage(events=(_issue("1"),))
        page2 = EventsPage(events=(_issue("2"), _issue("1")))
        client = FakeClient([page1, page2])
        service, context, _ = await self._service(_settings(), client, {})
        await service.poll_all()  # baseline
        report = await service.poll_all()
        self.assertEqual(len(context.sent), 1)
        umo, chain = context.sent[0]
        self.assertEqual(umo, "qq1:GroupMessage:123")
        self.assertIn("[新 Issue]", chain.chain[0][1])
        self.assertEqual(report.sent, 1)

    async def test_notify_on_first_run(self) -> None:
        client = FakeClient([EventsPage(events=(_issue(),))])
        service, context, _ = await self._service(
            _settings(notify_on_first_run=True), client, {}
        )
        await service.poll_all()
        self.assertEqual(len(context.sent), 1)

    async def test_release_summary_and_asset(self) -> None:
        client = FakeClient(
            [EventsPage(events=()), EventsPage(events=(_release(),))]
        )
        service, context, _ = await self._service(_settings(), client, {})
        await service.poll_all()  # baseline
        report = await service.poll_all()

        texts = [
            chain.chain[0][1]
            for _, chain in context.sent
            if chain.chain
            and isinstance(chain.chain[0], tuple)
            and chain.chain[0][0] == "text"
        ]
        self.assertTrue(any("[Release]" in text for text in texts))
        self.assertTrue(any("修复闪退并优化播放" in text for text in texts))

        files = [
            chain.chain[0]
            for _, chain in context.sent
            if chain.chain and isinstance(chain.chain[0], File)
        ]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "app-arm64-v8a.apk")
        self.assertEqual(client.downloaded, ["app-arm64-v8a.apk"])
        self.assertFalse(client.download_paths[0].exists())
        self.assertEqual(report.sent, 2)

    async def test_asset_too_large_falls_back_to_link(self) -> None:
        release = _release(size=2 * 1024 * 1024)
        client = FakeClient(
            [EventsPage(events=()), EventsPage(events=(release,))]
        )
        service, context, _ = await self._service(
            _settings(asset_max_mb=1), client, {}
        )
        await service.poll_all()
        await service.poll_all()
        texts = [
            chain.chain[0][1]
            for _, chain in context.sent
            if chain.chain
            and isinstance(chain.chain[0], tuple)
            and chain.chain[0][0] == "text"
        ]
        self.assertTrue(any("未能发送" in text for text in texts))
        self.assertEqual(client.downloaded, [])

    async def test_no_targets_reports_error(self) -> None:
        settings = _settings(
            repositories=[{"repo": "owner/repo"}],
            group_ids=[],
        )
        client = FakeClient([EventsPage(events=())])
        service, context, _ = await self._service(settings, client, {})
        await service.poll_all()  # baseline
        client.pages.append(EventsPage(events=(_issue(),)))
        report = await service.poll_all()
        self.assertEqual(context.sent, [])
        self.assertTrue(any("未配置推送目标" in e for e in report.errors))

    async def test_push_detected_from_branch_head(self) -> None:
        client = FakeClient(
            [EventsPage(events=()), EventsPage(events=())]
        )
        service, context, _ = await self._service(_settings(), client, {})
        await service.poll_all()  # baseline branches
        client.set_branch_head("b" * 40)
        client.compare_result = (
            CommitInfo(
                "b" * 40,
                "feat: 公告投票入口、顶栏玻璃圆角自定义与引导页拆分",
                "AieXile",
            ),
        )
        await service.poll_all()
        self.assertIn("compare", client.calls)
        texts = _texts(context)
        self.assertTrue(any("[Push]" in text for text in texts))
        self.assertTrue(any("公告投票入口" in text for text in texts))

    async def test_replay_latest_without_touching_state(self) -> None:
        state: dict = {}
        client = FakeClient(
            [EventsPage(events=(_push_without_commits(),))]
        )
        service, context, _ = await self._service(_settings(), client, state)
        report = await service.replay_latest("push", "owner/repo")
        self.assertEqual(report.sent, 1)
        self.assertEqual(state, {})
        texts = _texts(context)
        self.assertTrue(any("[Push]" in text for text in texts))
        self.assertTrue(any("fix crash" in text for text in texts))

    async def test_rate_limit_reports_retry(self) -> None:
        client = FakeClient([GitHubError(403, "rate limited", retry_after=60)])
        service, _, _ = await self._service(_settings(), client, {})
        report = await service.poll_all()
        self.assertEqual(report.retry_after, 60)
        self.assertTrue(any("HTTP 403" in e for e in report.errors))


class TestSummarizerFallback(unittest.IsolatedAsyncioTestCase):
    async def test_llm_failure_uses_excerpt(self) -> None:
        from astrbot_plugin_github_notify.core.summarizer import summarize_release

        release = ReleaseInfo(
            repo="owner/repo",
            tag="v1",
            name="v1",
            body="## Changes\n- fix crash\n- improve player",
            url="",
            assets=(),
            prerelease=False,
            published_at="",
        )

        class Context:
            async def get_using_provider_async(self):
                return None

        text = await summarize_release(
            Context(),
            release=release,
            commits=(),
            provider_id="",
            max_chars=100,
            input_chars=6000,
            instruction="总结 {max_chars} 字",
        )
        self.assertIn("fix crash", text)
        self.assertLessEqual(len(text), 101)


if __name__ == "__main__":
    unittest.main(verbosity=2)
