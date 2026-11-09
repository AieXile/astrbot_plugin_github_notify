"""Release 更新日志摘要（LLM + 原文兜底）。"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Iterable

from .events import ReleaseInfo
from .settings import DEFAULT_INSTRUCTION

_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]+\)")
_MD_MARK = re.compile(r"[*_`>#]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _plain_markdown(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text or "")
    text = _MD_MARK.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _BLANK_LINES.sub("\n\n", text).strip()


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if limit <= 0:
        return ""
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def fallback_summary(
    release: ReleaseInfo,
    commits: Iterable[str],
    max_chars: int,
) -> str:
    body = _plain_markdown(release.body)
    if body:
        return _clip(body, max_chars)
    items = [line.strip() for line in commits if line.strip()]
    if items:
        return _clip("；".join(items[:5]), max_chars)
    return _clip(release.name or release.tag or "新版本发布", max_chars)


def build_prompt(
    release: ReleaseInfo,
    *,
    instruction: str,
    max_chars: int,
    input_chars: int,
    commits: Iterable[str] = (),
) -> str:
    source = _plain_markdown(release.body)
    if not source:
        source = "\n".join(f"- {line}" for line in commits)
    source = _clip(source, input_chars)
    template = instruction or DEFAULT_INSTRUCTION
    head = template.replace("{max_chars}", str(max_chars))
    lines = [
        head,
        "",
        f"仓库：{release.repo}",
        f"版本：{release.tag}",
    ]
    if release.name and release.name != release.tag:
        lines.append(f"名称：{release.name}")
    lines.append("发布说明：")
    lines.append(source or "（无发布说明）")
    return "\n".join(lines)


async def resolve_provider_id(context: Any, configured: str) -> str:
    if configured:
        return configured
    getter = getattr(context, "get_using_provider_async", None)
    if not callable(getter):
        return ""
    try:
        provider = await getter()
    except Exception:
        return ""
    if provider is None:
        return ""
    meta = getattr(provider, "meta", None)
    if callable(meta):
        try:
            return str(getattr(meta(), "id", "") or "")
        except Exception:
            return ""
    return ""


async def summarize_release(
    context: Any,
    *,
    release: ReleaseInfo,
    commits: Iterable[str],
    provider_id: str,
    max_chars: int,
    input_chars: int,
    instruction: str,
) -> str:
    commit_list = tuple(commits)
    fallback = fallback_summary(release, commit_list, max_chars)
    if not release.body and not commit_list:
        return fallback

    prompt = build_prompt(
        release,
        instruction=instruction,
        max_chars=max_chars,
        input_chars=input_chars,
        commits=commit_list,
    )
    resolved = await resolve_provider_id(context, provider_id)
    if not resolved:
        return fallback

    generate = getattr(context, "llm_generate", None)
    if not callable(generate):
        return fallback
    try:
        response = await asyncio.wait_for(
            generate(chat_provider_id=resolved, prompt=prompt),
            timeout=60,
        )
    except Exception:
        return fallback
    text = str(getattr(response, "completion_text", "") or "").strip()
    return _clip(text, max_chars) if text else fallback
