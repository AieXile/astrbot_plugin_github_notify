"""配置、目标、事件解析、附件筛选、格式化、状态的自测。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_github_notify.core import formatter  # noqa: E402
from astrbot_plugin_github_notify.core.assets import (  # noqa: E402
    format_size,
    select_assets,
)
from astrbot_plugin_github_notify.core.events import (  # noqa: E402
    Asset,
    CommitInfo,
    IssueInfo,
    PushInfo,
    ReleaseInfo,
    event_key,
    parse_event,
)
from astrbot_plugin_github_notify.core.settings import (  # noqa: E402
    RepoSettings,
    Settings,
    normalize_repo,
)
from astrbot_plugin_github_notify.core.state import (  # noqa: E402
    RepoState,
    get_repo_state,
    mark_seen,
    set_repo_state,
)
from astrbot_plugin_github_notify.core.targets import (  # noqa: E402
    build_umos,
    resolve_platform_id,
    targets_for_repo,
)


def _issue_payload(action: str = "opened") -> dict:
    return {
        "id": "100",
        "type": "IssuesEvent",
        "repo": {"name": "owner/repo"},
        "actor": {"login": "alice"},
        "payload": {
            "action": action,
            "issue": {
                "number": 12,
                "title": "播放页闪白",
                "html_url": "https://github.com/owner/repo/issues/12",
                "body": "深色模式下打开播放页会闪白，Android 14 必现。",
                "labels": [{"name": "bug"}, {"name": "ui"}],
                "user": {"login": "bob"},
            },
        },
    }


def _release_payload(
    prerelease: bool = False, body: str = "## Changes\n- fix crash"
) -> dict:
    return {
        "id": "200",
        "type": "ReleaseEvent",
        "repo": {"name": "owner/repo"},
        "actor": {"login": "alice"},
        "payload": {
            "action": "published",
            "release": {
                "tag_name": "v1.2.0",
                "name": "v1.2.0",
                "body": body,
                "html_url": "https://github.com/owner/repo/releases/tag/v1.2.0",
                "prerelease": prerelease,
                "draft": False,
                "published_at": "2026-11-09T10:00:00Z",
                "assets": [
                    {
                        "id": "1",
                        "name": "app-arm64-v8a.apk",
                        "size": 1000,
                        "url": "https://api.github.com/assets/1",
                        "browser_download_url": "https://github.com/asset/1",
                    },
                    {
                        "id": "2",
                        "name": "app-armeabi-v7a.apk",
                        "size": 900,
                        "url": "https://api.github.com/assets/2",
                        "browser_download_url": "https://github.com/asset/2",
                    },
                ],
            },
        },
    }


def _push_payload(ref: str = "refs/heads/main", deleted: bool = False) -> dict:
    return {
        "id": "300",
        "type": "PushEvent",
        "repo": {"name": "owner/repo"},
        "actor": {"login": "alice"},
        "payload": {
            "ref": ref,
            "deleted": deleted,
            "before": "a" * 40,
            "head": "b" * 40,
            "size": 2,
            "commits": [
                {"sha": "b" * 40, "message": "fix: crash\n\nmore"},
                {"sha": "c" * 40, "message": "feat: player"},
            ],
        },
    }


class TestSettings(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = Settings.from_mapping({})
        self.assertEqual(settings.poll_interval, 300)
        self.assertEqual(settings.summary_max_chars, 100)
        self.assertEqual(settings.asset_settings.patterns, ("*.apk", "*.exe"))
        self.assertTrue(settings.asset_settings.prefer_64bit)

    def test_normalize_repo(self) -> None:
        self.assertEqual(normalize_repo("owner/repo"), "owner/repo")
        self.assertEqual(
            normalize_repo("https://github.com/owner/repo/"), "owner/repo"
        )
        self.assertEqual(normalize_repo("owner/repo.git"), "owner/repo")
        self.assertEqual(normalize_repo("not a repo"), "")

    def test_repositories_and_overrides(self) -> None:
        settings = Settings.from_mapping(
            {
                "repositories": [
                    {
                        "repo": "https://github.com/owner/repo",
                        "issue": False,
                        "branches": ["main"],
                        "group_ids": ["123"],
                        "asset_patterns": ["*.zip"],
                        "summary_max_chars": 300,
                    },
                    {"repo": "owner/repo"},
                    {"repo": "bad"},
                ],
                "summary_instruction": "",
            }
        )
        self.assertEqual(len(settings.repositories), 1)
        repo = settings.repositories[0]
        self.assertEqual(repo.repo, "owner/repo")
        self.assertFalse(repo.issue)
        self.assertEqual(repo.branches, ("main",))
        self.assertEqual(repo.group_ids, ("123",))
        self.assertEqual(repo.asset_patterns, ("*.zip",))
        self.assertEqual(repo.summary_max_chars, 300)
        self.assertIn("{max_chars}", settings.summary_instruction)


class TestTargets(unittest.TestCase):
    def test_resolve_platform(self) -> None:
        class Platform:
            def __init__(self, name: str, pid: str) -> None:
                self._meta = SimpleNamespace(name=name, id=pid)

            def meta(self):
                return self._meta

        insts = [Platform("telegram", "tg"), Platform("aiocqhttp", "qq1")]
        self.assertEqual(resolve_platform_id(insts, ""), "qq1")
        self.assertEqual(resolve_platform_id(insts, "custom"), "custom")

    def test_build_umos(self) -> None:
        umos = build_umos(
            group_ids=["123"],
            user_ids=["456"],
            umo_targets=["qq1:GroupMessage:789"],
            platform_id="qq1",
        )
        self.assertEqual(
            umos,
            [
                "qq1:GroupMessage:789",
                "qq1:GroupMessage:123",
                "qq1:FriendMessage:456",
            ],
        )

    def test_repo_override(self) -> None:
        settings = Settings.from_mapping(
            {
                "group_ids": ["999"],
                "repositories": [{"repo": "owner/repo", "group_ids": ["123"]}],
            }
        )
        repo = settings.repositories[0]
        self.assertEqual(
            targets_for_repo(repo, settings, "qq1"),
            ["qq1:GroupMessage:123"],
        )
        repo2 = RepoSettings(repo="other/repo")
        self.assertEqual(
            targets_for_repo(repo2, settings, "qq1"),
            ["qq1:GroupMessage:999"],
        )


class TestEvents(unittest.TestCase):
    def test_issue(self) -> None:
        settings = Settings.from_mapping({})
        cfg = RepoSettings(repo="owner/repo")
        info = parse_event(_issue_payload(), cfg, settings)
        self.assertIsInstance(info, IssueInfo)
        self.assertEqual(info.number, 12)
        self.assertEqual(info.labels, ("bug", "ui"))
        text = formatter.format_issue(info, 20)
        self.assertIn("[新 Issue] owner/repo #12", text)
        self.assertIn("作者：@bob", text)
        self.assertIn("摘要：", text)

    def test_issue_action_filter(self) -> None:
        settings = Settings.from_mapping({"issue_actions": ["opened"]})
        cfg = RepoSettings(repo="owner/repo")
        self.assertIsNone(parse_event(_issue_payload("closed"), cfg, settings))
        settings2 = Settings.from_mapping({"issue_actions": ["closed"]})
        info = parse_event(_issue_payload("closed"), cfg, settings2)
        self.assertIsInstance(info, IssueInfo)

    def test_release(self) -> None:
        settings = Settings.from_mapping({})
        cfg = RepoSettings(repo="owner/repo")
        info = parse_event(_release_payload(), cfg, settings)
        self.assertIsInstance(info, ReleaseInfo)
        self.assertEqual(info.tag, "v1.2.0")
        self.assertEqual(len(info.assets), 2)
        text = formatter.format_release(info, "修复闪退")
        self.assertIn("[Release] owner/repo v1.2.0", text)
        self.assertIn("修复闪退", text)

    def test_prerelease_filter(self) -> None:
        settings = Settings.from_mapping({"include_prerelease": False})
        cfg = RepoSettings(repo="owner/repo")
        self.assertIsNone(
            parse_event(_release_payload(prerelease=True), cfg, settings)
        )

    def test_push_branch_filter(self) -> None:
        settings = Settings.from_mapping({})
        cfg = RepoSettings(repo="owner/repo", branches=("release/*",))
        self.assertIsNone(parse_event(_push_payload("refs/heads/main"), cfg, settings))
        info = parse_event(_push_payload("refs/heads/release/1.0"), cfg, settings)
        self.assertIsInstance(info, PushInfo)
        self.assertEqual(info.total, 2)

    def test_push_skip_deleted_and_tag(self) -> None:
        settings = Settings.from_mapping({})
        cfg = RepoSettings(repo="owner/repo")
        self.assertIsNone(parse_event(_push_payload(deleted=True), cfg, settings))
        self.assertIsNone(parse_event(_push_payload("refs/tags/v1"), cfg, settings))

    def test_event_key(self) -> None:
        self.assertEqual(event_key({"id": "42"}), "42")
        self.assertEqual(event_key({"type": "X"}), event_key({"type": "X"}))
        self.assertNotEqual(event_key({"type": "X"}), event_key({"type": "Y"}))
        self.assertEqual(len(event_key({"type": "X"})), 40)


class TestAssets(unittest.TestCase):
    def _settings(self, **kwargs):
        return Settings.from_mapping(kwargs).asset_settings

    def test_prefers_apk_and_64bit(self) -> None:
        settings = self._settings()
        assets = [
            Asset("1", "app-armeabi-v7a.apk", 10, "", ""),
            Asset("2", "app-arm64-v8a.apk", 10, "", ""),
            Asset("3", "setup.exe", 10, "", ""),
        ]
        picked = select_assets(assets, settings)
        self.assertEqual([a.name for a in picked], ["app-arm64-v8a.apk"])

    def test_pattern_order_apk_over_exe(self) -> None:
        settings = self._settings(asset_max_count=2)
        assets = [
            Asset("1", "setup.exe", 10, "", ""),
            Asset("2", "app.apk", 10, "", ""),
        ]
        picked = select_assets(assets, settings)
        self.assertEqual(picked[0].name, "app.apk")

    def test_max_count_zero_means_all(self) -> None:
        settings = self._settings(asset_max_count=0)
        assets = [
            Asset("1", "a.apk", 10, "", ""),
            Asset("2", "b.apk", 10, "", ""),
        ]
        self.assertEqual(len(select_assets(assets, settings)), 2)

    def test_fallback_all(self) -> None:
        settings = self._settings(asset_fallback_all=True)
        assets = [Asset("1", "source.tar.gz", 10, "", "")]
        self.assertEqual(len(select_assets(assets, settings)), 1)

    def test_no_match_without_fallback(self) -> None:
        settings = self._settings()
        assets = [Asset("1", "source.tar.gz", 10, "", "")]
        self.assertEqual(select_assets(assets, settings), [])

    def test_format_size(self) -> None:
        self.assertEqual(format_size(0), "未知大小")
        self.assertEqual(format_size(1024), "1 KB")
        self.assertEqual(format_size(2 * 1024 * 1024), "2.0 MB")


class TestFormatter(unittest.TestCase):
    def test_push_commit_limit(self) -> None:
        push = PushInfo(
            repo="owner/repo",
            branch="main",
            before="a" * 40,
            head="b" * 40,
            pusher="alice",
            commits=(
                CommitInfo("b" * 40, "fix crash", "alice"),
                CommitInfo("c" * 40, "feat player", "alice"),
                CommitInfo("d" * 40, "docs", "alice"),
            ),
            total=3,
        )
        text = formatter.format_push(push, 2)
        self.assertIn("3 个提交 · @alice", text)
        self.assertIn("- bbbbbbb fix crash", text)
        self.assertIn("…还有 1 个提交", text)
        self.assertIn("/compare/", text)

    def test_push_new_branch(self) -> None:
        push = PushInfo(
            repo="owner/repo",
            branch="main",
            before="0" * 40,
            head="b" * 40,
            pusher="",
            commits=(),
            total=1,
        )
        text = formatter.format_push(push, 1)
        self.assertIn("/commit/", text)

    def test_asset_fallback(self) -> None:
        text = formatter.format_asset_fallback(
            [Asset("1", "app.apk", 100, "", "https://github.com/a")]
        )
        self.assertIn("未能发送", text)
        self.assertIn("https://github.com/a", text)


class TestState(unittest.TestCase):
    def test_roundtrip(self) -> None:
        raw: dict = {}
        state = RepoState(
            etag='W/"1"',
            seen=["1"],
            initialized=True,
            heads={"main": "abc"},
        )
        set_repo_state(raw, "Owner/Repo", state)
        loaded = get_repo_state(raw, "owner/repo")
        self.assertEqual(loaded.etag, 'W/"1"')
        self.assertEqual(loaded.seen, ["1"])
        self.assertTrue(loaded.initialized)
        self.assertEqual(loaded.heads, {"main": "abc"})

    def test_mark_seen_dedup_and_cap(self) -> None:
        seen = mark_seen([], ["1", "2", "1"])
        self.assertEqual(seen, ["1", "2"])
        capped = mark_seen([str(i) for i in range(300)], ["300"])
        self.assertEqual(len(capped), 200)
        self.assertEqual(capped[-1], "300")


if __name__ == "__main__":
    unittest.main(verbosity=2)
