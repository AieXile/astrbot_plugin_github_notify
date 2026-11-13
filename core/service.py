"""轮询与事件分发服务。"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import dispatcher, formatter
from .assets import max_bytes, select_assets
from .events import (
    Asset,
    CommitInfo,
    IssueInfo,
    PushInfo,
    ReleaseInfo,
    branch_allowed,
    event_key,
    parse_event,
)
from .github import (
    AssetTooLarge,
    BranchInfo,
    GitHubClient,
    GitHubError,
)
from .settings import RepoSettings, Settings, normalize_repo
from .state import (
    RepoState,
    get_repo_state,
    is_seen,
    mark_seen,
    set_repo_state,
)
from .summarizer import fallback_summary, summarize_release
from .targets import resolve_platform_id, targets_for_repo

_SAFE_COMPONENT = re.compile(r"[^\w.\-]+", re.UNICODE)


@dataclass
class PollReport:
    polled: int = 0
    sent: int = 0
    errors: list[str] = field(default_factory=list)
    retry_after: int = 0


def _safe_component(value: str) -> str:
    text = _SAFE_COMPONENT.sub("_", str(value or "")).strip("._ ")
    return text[:80] or "unknown"


class GitHubNotifyService:
    def __init__(
        self,
        *,
        context: Any,
        settings: Settings,
        client: GitHubClient,
        logger: Any,
        state: dict[str, Any],
        download_root: Path,
    ) -> None:
        self.context = context
        self.settings = settings
        self.client = client
        self.logger = logger
        self.state = state
        self.download_root = download_root

    async def poll_all(self) -> PollReport:
        report = PollReport()
        if not self.settings.enabled_repositories:
            return report

        platform_insts = getattr(
            getattr(self.context, "platform_manager", None),
            "platform_insts",
            [],
        )
        platform_id = resolve_platform_id(
            platform_insts, self.settings.platform_id
        )

        for repo in self.settings.enabled_repositories:
            report.polled += 1
            try:
                await self._poll_repo(repo, platform_id, report)
            except GitHubError as exc:
                report.errors.append(
                    f"{repo.repo}: HTTP {exc.status} {exc.message}".strip()
                )
                report.retry_after = max(report.retry_after, exc.retry_after)
            except Exception as exc:
                report.errors.append(f"{repo.repo}: {type(exc).__name__}")
                self.logger.exception("GitHub 通知轮询失败：%s", repo.repo)
        return report

    async def replay_latest(
        self,
        kind: str = "push",
        repo: str = "",
    ) -> PollReport:
        """手动重放最近一条事件（不改变去重状态），用于验证配置。"""
        report = PollReport()
        types = {
            "push": "PushEvent",
            "release": "ReleaseEvent",
            "issue": "IssuesEvent",
        }
        target_type = types.get(kind.lower(), "")
        if not target_type:
            report.errors.append(f"未知事件类型：{kind}")
            return report

        platform_insts = getattr(
            getattr(self.context, "platform_manager", None),
            "platform_insts",
            [],
        )
        platform_id = resolve_platform_id(
            platform_insts, self.settings.platform_id
        )
        wanted_repo = normalize_repo(repo) if repo else ""
        repos = [
            item
            for item in self.settings.enabled_repositories
            if not wanted_repo or item.repo.lower() == wanted_repo.lower()
        ]
        if not repos:
            report.errors.append("没有匹配的启用仓库")
            return report

        for repo_cfg in repos:
            report.polled += 1
            try:
                targets = targets_for_repo(repo_cfg, self.settings, platform_id)
                if not targets:
                    report.errors.append(f"{repo_cfg.repo}: 未配置推送目标")
                    continue

                if target_type == "PushEvent":
                    branches = await self.client.fetch_branches(repo_cfg.repo)
                    watched = [
                        branch
                        for branch in branches
                        if branch_allowed(branch.name, repo_cfg.branches)
                    ]
                    if not watched:
                        report.errors.append(
                            f"{repo_cfg.repo}: 当前分支过滤下没有可重放的分支"
                        )
                        continue
                    branch = next(
                        (
                            item
                            for name in ("main", "master")
                            for item in watched
                            if item.name == name
                        ),
                        watched[0],
                    )
                    commit = await self.client.fetch_commit(
                        repo_cfg.repo, branch.sha
                    ) or CommitInfo(
                        branch.sha, branch.message, branch.author
                    )
                    info = PushInfo(
                        repo=repo_cfg.repo,
                        branch=branch.name,
                        before="",
                        head=branch.sha,
                        pusher=commit.author or branch.author,
                        commits=(commit,),
                        total=1,
                    )
                    await self._dispatch(info, repo_cfg, targets, report)
                    continue

                page = await self.client.fetch_events(repo_cfg.repo, "")
                candidates = [
                    raw
                    for raw in page.events
                    if str(raw.get("type") or "") == target_type
                ]
                if not candidates:
                    report.errors.append(
                        f"{repo_cfg.repo}: 最近没有 {target_type}"
                    )
                    continue

                candidates.sort(
                    key=lambda item: str(item.get("created_at") or ""),
                    reverse=True,
                )
                for raw in candidates:
                    info = parse_event(raw, repo_cfg, self.settings)
                    if info is not None:
                        await self._dispatch(info, repo_cfg, targets, report)
                        break
                else:
                    report.errors.append(
                        f"{repo_cfg.repo}: {target_type} 被当前过滤规则跳过"
                    )
            except GitHubError as exc:
                report.errors.append(
                    f"{repo_cfg.repo}: HTTP {exc.status} {exc.message}".strip()
                )
            except Exception as exc:
                report.errors.append(f"{repo_cfg.repo}: {type(exc).__name__}")
                self.logger.exception("GitHub 通知重放失败：%s", repo_cfg.repo)
        return report

    async def _poll_repo(
        self,
        repo: RepoSettings,
        platform_id: str,
        report: PollReport,
    ) -> None:
        state = get_repo_state(self.state, repo.repo)
        page = await self.client.fetch_events(repo.repo, state.etag)

        branches: tuple[BranchInfo, ...] | None = None
        try:
            branches = await self.client.fetch_branches(repo.repo)
        except GitHubError as exc:
            report.errors.append(
                f"{repo.repo} 分支: HTTP {exc.status} {exc.message}".strip()
            )
            report.retry_after = max(report.retry_after, exc.retry_after)
        except Exception as exc:
            report.errors.append(
                f"{repo.repo} 分支: {type(exc).__name__}"
            )
            self.logger.exception("GitHub 通知获取分支失败：%s", repo.repo)

        if not state.initialized:
            state.etag = page.etag or state.etag
            if not self.settings.notify_on_first_run:
                # 基线模式才把历史事件标记为已处理；补发模式留给下面正常流程
                state.seen = mark_seen(
                    state.seen, [event_key(raw) for raw in page.events]
                )
            if branches is not None:
                state.heads = self._watched_heads(repo, branches)
            state.initialized = True
            set_repo_state(self.state, repo.repo, state)
            if self.settings.notify_on_first_run:
                self.logger.warning(
                    "GitHub 通知首次运行：%s（将补发 %s 条历史事件）",
                    repo.repo,
                    len(page.events),
                )
            else:
                self.logger.warning(
                    "GitHub 通知首次建立基线：%s（跳过 %s 条历史事件，"
                    "如需补发可用 /github_notify_replay 或打开 notify_on_first_run）",
                    repo.repo,
                    len(page.events),
                )
                return

        targets = targets_for_repo(repo, self.settings, platform_id)
        if not targets:
            report.errors.append(f"{repo.repo}: 未配置推送目标")

        if not page.not_modified:
            seen = state.seen
            for raw in reversed(page.events):
                key = event_key(raw)
                if is_seen(seen, key):
                    continue
                try:
                    if str(raw.get("type") or "") == "PushEvent":
                        # Push 统一由分支 head + compare 检测，避免 Events API 漏事件
                        continue
                    info = parse_event(raw, repo, self.settings)
                    if info is not None:
                        await self._dispatch(info, repo, targets, report)
                except Exception as exc:
                    report.errors.append(
                        f"{repo.repo}/{key}: {type(exc).__name__}"
                    )
                    self.logger.exception(
                        "GitHub 通知处理事件失败：%s/%s", repo.repo, key
                    )
                finally:
                    seen = mark_seen(seen, [key])
            state.seen = seen
            state.etag = page.etag or state.etag

        if branches is not None:
            await self._poll_pushes(repo, targets, state, report, branches)

        state.initialized = True
        set_repo_state(self.state, repo.repo, state)

    @staticmethod
    def _watched_heads(
        repo: RepoSettings,
        branches: tuple[BranchInfo, ...],
    ) -> dict[str, str]:
        return {
            branch.name: branch.sha
            for branch in branches
            if branch_allowed(branch.name, repo.branches)
        }

    async def _poll_pushes(
        self,
        repo: RepoSettings,
        targets: list[str],
        state: RepoState,
        report: PollReport,
        branches: tuple[BranchInfo, ...],
    ) -> None:
        watched = [
            branch
            for branch in branches
            if branch_allowed(branch.name, repo.branches)
        ]
        current = {branch.name: branch.sha for branch in watched}

        # 首次拿到分支状态、或从旧版本升级（没有 heads）时只建立基线。
        if not state.heads:
            state.heads = current
            return

        if not repo.push:
            state.heads = current
            return

        for branch in watched:
            previous = state.heads.get(branch.name)
            if not previous or previous == branch.sha:
                continue
            details = await self.client.compare_commits(
                repo.repo, previous, branch.sha
            )
            if details:
                commits = details
            else:
                single = await self.client.fetch_commit(
                    repo.repo, branch.sha
                )
                commits = (
                    single
                    or CommitInfo(branch.sha, branch.message, branch.author),
                )
            info = PushInfo(
                repo=repo.repo,
                branch=branch.name,
                before=previous,
                head=branch.sha,
                pusher=branch.author,
                commits=commits,
                total=len(commits),
            )
            text = formatter.format_push(info, self.settings.push_max_commits)
            await self._send_text(targets, text, report)

        # 被删除/取消监听的分支会自动从 state 中移除
        state.heads = current

    async def _dispatch(
        self,
        info: IssueInfo | PushInfo | ReleaseInfo,
        repo: RepoSettings,
        targets: list[str],
        report: PollReport,
    ) -> None:
        if isinstance(info, IssueInfo):
            text = formatter.format_issue(info, self.settings.issue_body_chars)
            await self._send_text(targets, text, report)
            return
        if isinstance(info, PushInfo):
            if not info.commits:
                details = await self.client.compare_commits(
                    info.repo, info.before, info.head
                )
                info = replace(
                    info,
                    commits=details,
                    total=max(info.total, len(details)),
                )
            text = formatter.format_push(info, self.settings.push_max_commits)
            await self._send_text(targets, text, report)
            return
        await self._dispatch_release(info, repo, targets, report)

    async def _send_text(
        self,
        targets: list[str],
        text: str,
        report: PollReport,
    ) -> list[str]:
        if not targets:
            return []
        succeeded = await dispatcher.send_to_targets(
            self.context, targets, text, self.logger
        )
        report.sent += len(succeeded)
        if not succeeded:
            report.errors.append("文本推送失败")
        return succeeded

    async def _dispatch_release(
        self,
        release: ReleaseInfo,
        repo: RepoSettings,
        targets: list[str],
        report: PollReport,
    ) -> None:
        max_chars = repo.summary_max_chars or self.settings.summary_max_chars
        commits: tuple[str, ...] = ()
        if not release.body:
            previous = await self.client.previous_release_tag(
                release.repo, release.tag
            )
            if previous:
                details = await self.client.compare_commits(
                    release.repo, previous, release.tag
                )
                commits = tuple(item.message for item in details)

        if self.settings.release_use_summary:
            summary = await summarize_release(
                self.context,
                release=release,
                commits=commits,
                provider_id=self.settings.summary_provider,
                max_chars=max_chars,
                input_chars=self.settings.summary_input_chars,
                instruction=self.settings.summary_instruction,
            )
        else:
            # 默认直接展示 Release 发布说明原文，保留完整更新日志
            summary = fallback_summary(
                release, commits, self.settings.release_body_chars
            )
        text = formatter.format_release(release, summary)
        succeeded_targets = await self._send_text(targets, text, report)
        if not self.settings.send_assets or not succeeded_targets:
            return

        asset_settings = replace(
            self.settings.asset_settings,
            patterns=repo.asset_patterns
            or self.settings.asset_settings.patterns,
        )
        limit = max_bytes(asset_settings)
        selected = select_assets(release.assets, asset_settings, limit)
        if not selected:
            return

        if all(asset.size and asset.size > limit for asset in selected):
            # 全部超过大小上限时给出下载链接，避免通知里完全没有附件信息
            await self._send_text(
                succeeded_targets,
                formatter.format_asset_fallback(selected),
                report,
            )
            return

        fallback = []
        work_dir = (
            self.download_root
            / _safe_component(release.repo)
            / _safe_component(release.tag or "release")
        )
        try:
            for asset in selected:
                sent = await self._send_asset(
                    asset, succeeded_targets, work_dir, limit, report
                )
                if not sent:
                    fallback.append(asset)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        if fallback:
            await self._send_text(
                succeeded_targets,
                formatter.format_asset_fallback(fallback),
                report,
            )

    async def _send_asset(
        self,
        asset: Asset,
        targets: list[str],
        work_dir: Path,
        limit: int,
        report: PollReport,
    ) -> bool:
        """发送 Release 附件；优先让协议端直接下载，失败时回退本地文件。"""
        if asset.size and asset.size > limit:
            return False

        sent: list[str] = []
        if asset.browser_url:
            sent = await dispatcher.send_file_to_targets(
                self.context,
                targets,
                None,
                asset.name,
                self.logger,
                url=asset.browser_url,
            )
        remaining = [umo for umo in targets if umo not in sent]

        if remaining:
            try:
                path = await self.client.download_asset(asset, work_dir, limit)
            except AssetTooLarge:
                path = None
            except Exception:
                self.logger.exception(
                    "GitHub Release 附件下载失败：%s", asset.name
                )
                path = None
            if path is not None:
                sent += await dispatcher.send_file_to_targets(
                    self.context,
                    remaining,
                    path,
                    asset.name,
                    self.logger,
                )

        report.sent += len(sent)
        return len(sent) == len(targets)
