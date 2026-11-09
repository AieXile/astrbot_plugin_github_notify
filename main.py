"""AstrBot 插件入口：轮询 GitHub 并推送 Issue / Release / Push。"""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .core.dispatcher import send_to_targets
from .core.github import GitHubClient
from .core.service import GitHubNotifyService, PollReport
from .core.settings import Settings
from .core.state import STATE_KEY
from .core.targets import build_umos, resolve_platform_id, targets_for_repo

PLUGIN_NAME = "astrbot_plugin_github_notify"


@register(
    PLUGIN_NAME,
    "AieXile",
    "轮询 GitHub 仓库的 Issue / Release / Push 并推送到指定群或 QQ",
    "v1.0.2",
    "",
)
class GitHubNotifyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._task: asyncio.Task | None = None
        self._last_poll_at = 0.0
        self._last_report: PollReport | None = None

    async def initialize(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    async def terminate(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _poll_loop(self) -> None:
        delay = 10
        while True:
            await asyncio.sleep(delay)
            settings = Settings.from_mapping(self.config)
            report = None
            try:
                if settings.enabled_repositories:
                    report = await self._poll_once(settings)
                    self._last_poll_at = time.time()
                    self._last_report = report
                    logger.info(
                        "GitHub 通知轮询完成：%s 个仓库，%s 条消息，%s 个错误",
                        report.polled,
                        report.sent,
                        len(report.errors),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GitHub 通知轮询异常")
            retry_after = report.retry_after if report else 0
            delay = max(settings.poll_interval, retry_after)

    async def _poll_once(self, settings: Settings) -> PollReport:
        state = await self.get_kv_data(STATE_KEY, {})
        if not isinstance(state, dict):
            state = {}

        client = GitHubClient(settings.github_token, settings.timeout)
        service = GitHubNotifyService(
            context=self.context,
            settings=settings,
            client=client,
            logger=logger,
            state=state,
            download_root=self._download_root(),
        )
        report = await service.poll_all()
        await self.put_kv_data(STATE_KEY, state)
        return report

    def _download_root(self) -> Path:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            return (
                get_astrbot_data_path()
                / "plugin_data"
                / PLUGIN_NAME
                / "downloads"
            )
        except Exception:
            return Path("data") / "plugin_data" / PLUGIN_NAME / "downloads"

    def _platform_id(self, settings: Settings) -> str:
        platform_insts = getattr(
            getattr(self.context, "platform_manager", None),
            "platform_insts",
            [],
        )
        return resolve_platform_id(platform_insts, settings.platform_id)

    def _all_targets(self, settings: Settings) -> list[str]:
        platform_id = self._platform_id(settings)
        result: list[str] = []
        if settings.enabled_repositories:
            for repo in settings.enabled_repositories:
                for umo in targets_for_repo(repo, settings, platform_id):
                    if umo not in result:
                        result.append(umo)
        else:
            result = build_umos(
                group_ids=settings.group_ids,
                user_ids=settings.user_ids,
                umo_targets=settings.umo_targets,
                platform_id=platform_id,
            )
        return result

    @filter.command("github_notify")
    async def github_notify_status(self, event: AstrMessageEvent):
        """查看 GitHub 通知插件的配置与上次轮询状态。"""
        event.stop_event()
        if not event.is_admin():
            yield event.plain_result("仅管理员可用")
            return

        settings = Settings.from_mapping(self.config)
        last = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._last_poll_at))
            if self._last_poll_at
            else "尚未轮询"
        )
        errors = ""
        if self._last_report and self._last_report.errors:
            errors = "；".join(self._last_report.errors[:2])

        lines = [
            "GitHub 通知",
            f"仓库：{len(settings.enabled_repositories)} 个启用"
            f" / 共 {len(settings.repositories)} 个",
            f"目标：{len(self._all_targets(settings))} 个会话",
            f"轮询间隔：{settings.poll_interval} 秒",
            f"上次轮询：{last}",
        ]
        if errors:
            lines.append(f"最近错误：{errors}")
        yield event.plain_result("\n".join(lines))

    @filter.command("github_notify_replay")
    async def github_notify_replay(
        self,
        event: AstrMessageEvent,
        repo: str = "",
        kind: str = "push",
    ):
        """手动重放最近一条 Push/Release/Issue，用于验证配置。

        用法：/github_notify_replay [owner/repo] [push|release|issue]
        """
        event.stop_event()
        if not event.is_admin():
            yield event.plain_result("仅管理员可用")
            return

        settings = Settings.from_mapping(self.config)
        if not settings.enabled_repositories:
            yield event.plain_result("没有启用的仓库")
            return

        client = GitHubClient(settings.github_token, settings.timeout)
        service = GitHubNotifyService(
            context=self.context,
            settings=settings,
            client=client,
            logger=logger,
            state={},
            download_root=self._download_root(),
        )
        report = await service.replay_latest(kind=kind, repo=repo)

        lines = [
            f"重放完成：{report.polled} 个仓库，{report.sent} 条消息"
        ]
        if report.errors:
            lines.append("错误：" + "；".join(report.errors[:2]))
        yield event.plain_result("\n".join(lines))

    @filter.command("github_notify_test")
    async def github_notify_test(self, event: AstrMessageEvent):
        """向当前配置的推送目标发送一条测试消息。"""
        event.stop_event()
        if not event.is_admin():
            yield event.plain_result("仅管理员可用")
            return

        settings = Settings.from_mapping(self.config)
        targets = self._all_targets(settings)
        if not targets:
            yield event.plain_result("未配置推送目标，请先填写群号/QQ号/UMO")
            return

        succeeded = await send_to_targets(
            self.context,
            targets,
            "GitHub 通知插件测试：消息通道正常。",
            logger,
        )
        yield event.plain_result(
            f"测试消息已发送到 {len(succeeded)}/{len(targets)} 个目标"
        )
