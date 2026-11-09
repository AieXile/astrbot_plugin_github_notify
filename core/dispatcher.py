"""通过 AstrBot Context 向 UMO 会话发送文本与文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


def _text_chain(text: str) -> Any:
    from astrbot.api.event import MessageChain

    return MessageChain().message(text)


def _file_chain(path: Path, name: str) -> Any:
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import File

    return MessageChain(chain=[File(name=name, file=str(path))])


async def send_text(
    context: Any,
    umo: str,
    text: str,
    logger: Any,
) -> bool:
    try:
        return bool(await context.send_message(umo, _text_chain(text)))
    except Exception:
        logger.exception("GitHub 通知发送文本失败：%s", umo)
        return False


async def send_file(
    context: Any,
    umo: str,
    path: Path,
    name: str,
    logger: Any,
) -> bool:
    try:
        return bool(await context.send_message(umo, _file_chain(path, name)))
    except Exception:
        logger.exception("GitHub 通知发送文件失败：%s", umo)
        return False


async def send_to_targets(
    context: Any,
    targets: Iterable[str],
    text: str,
    logger: Any,
) -> list[str]:
    """发送文本，返回发送成功的会话列表。"""
    succeeded: list[str] = []
    for umo in targets:
        if await send_text(context, umo, text, logger):
            succeeded.append(umo)
    return succeeded


async def send_file_to_targets(
    context: Any,
    targets: Iterable[str],
    path: Path,
    name: str,
    logger: Any,
) -> list[str]:
    succeeded: list[str] = []
    for umo in targets:
        if await send_file(context, umo, path, name, logger):
            succeeded.append(umo)
    return succeeded
