"""把事件对象格式化成聊天消息文本。"""

from __future__ import annotations

from .assets import format_size
from .events import Asset, IssueInfo, PushInfo, ReleaseInfo

_MAX_TITLE = 160
_MAX_LINE = 120


def _one_line(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _excerpt(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    if limit <= 0 or not text:
        return ""
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def format_issue(issue: IssueInfo, body_chars: int) -> str:
    action_labels = {
        "opened": "新 Issue",
        "closed": "Issue 已关闭",
        "reopened": "Issue 重新打开",
        "labeled": "Issue 标签更新",
    }
    header = action_labels.get(issue.action, "Issue 更新")
    lines = [f"[{header}] {issue.repo} #{issue.number}"]
    if issue.title:
        lines.append(f"标题：{_one_line(issue.title, _MAX_TITLE)}")
    if issue.author:
        lines.append(f"作者：@{issue.author}")
    if issue.labels:
        lines.append("标签：" + ", ".join(issue.labels[:8]))
    excerpt = _excerpt(issue.body, body_chars)
    if excerpt:
        lines.append(f"摘要：{excerpt}")
    if issue.url:
        lines.append(issue.url)
    return "\n".join(lines)


def _compare_url(push: PushInfo) -> str:
    if push.before and not set(push.before) == {"0"}:
        return f"https://github.com/{push.repo}/compare/{push.before}...{push.head}"
    if push.head:
        return f"https://github.com/{push.repo}/commit/{push.head}"
    return f"https://github.com/{push.repo}"


def format_push(push: PushInfo, max_commits: int) -> str:
    lines = [f"[Push] {push.repo} {push.branch}"]
    if push.total > 0:
        if push.pusher:
            lines.append(f"{push.total} 个提交 · @{push.pusher}")
        else:
            lines.append(f"{push.total} 个提交")
    elif push.pusher:
        lines.append(f"新提交 · @{push.pusher}")
    else:
        lines.append("新提交")
    for commit in push.commits[:max_commits]:
        short_sha = commit.sha[:7]
        message = _one_line(commit.message, _MAX_LINE)
        if message:
            lines.append(f"- {short_sha} {message}".strip())
    remaining = push.total - min(len(push.commits), max_commits)
    if remaining > 0:
        lines.append(f"…还有 {remaining} 个提交")
    lines.append(_compare_url(push))
    return "\n".join(lines)


def format_release(release: ReleaseInfo, summary: str) -> str:
    label = "预发布" if release.prerelease else "Release"
    header = f"[{label}] {release.repo} {release.tag}"
    if release.name and release.name != release.tag:
        header += f" · {_one_line(release.name, 60)}"
    lines = [header]
    if summary:
        lines.append(summary)
    if release.url:
        lines.append(release.url)
    return "\n".join(lines)


def format_asset_fallback(assets: list[Asset]) -> str:
    lines = ["以下附件未能发送，请手动下载："]
    for asset in assets:
        name = _one_line(asset.name, _MAX_LINE)
        size = format_size(asset.size)
        url = asset.browser_url or asset.api_url
        lines.append(f"- {name}（{size}）{url}".rstrip())
    return "\n".join(lines)
