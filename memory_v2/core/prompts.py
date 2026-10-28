"""Text loaded from prompts/.

`persona` 是**接口**，核心仓里没有内容：它从实例的数据目录读（`<data>/persona.md`），
那份文件在 `.gitignore` 里，部署时再放进去。没有就是没有，不拿仓库里的东西顶替。

先看给的目录，再看上一层——习惯把记忆放在 `<实例>/data/memory`，而 persona.md 在
`<实例>/data/` 里，所以给记忆目录时也要找得到。
"""

from __future__ import annotations

import io
import os
import time
from typing import Dict, List


_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def _read(name: str) -> str:
    with io.open(os.path.join(_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def persona_text(data_dir: str = "") -> str:
    """从实例目录读自我描述；没有就是空串（不退回仓库里的任何文本）。"""
    if not data_dir:
        return ""
    folder = os.path.abspath(str(data_dir))
    for candidate in (folder, os.path.dirname(folder)):
        path = os.path.join(candidate, "persona.md")
        try:
            with io.open(path, encoding="utf-8") as handle:
                body = handle.read().strip()
        except OSError:
            continue
        if body:
            return body
    return ""


def instance_text(data_dir: str = "", name: str = "") -> str:
    """从实例目录读一份提示词：`<data>/prompts/<name>`，再看上一层。

    声音与规矩（sys2）属于实例，仓里不带；没有就是空串，调用方自己决定发不发。
    """
    if not data_dir or not name:
        return ""
    folder = os.path.abspath(str(data_dir))
    for candidate in (folder, os.path.dirname(folder)):
        path = os.path.join(candidate, "prompts", name)
        try:
            with io.open(path, encoding="utf-8") as handle:
                body = handle.read().strip()
        except OSError:
            continue
        if body:
            return body
    return ""


def system_messages(data_dir: str = "") -> List[Dict[str, str]]:
    """persona 与 sys2 都是实例内容：有才发那一条，没有就不占一个空的 system。"""
    today = time.strftime("%Y-%m-%d")
    messages: List[Dict[str, str]] = []
    persona = persona_text(data_dir)
    if persona:
        messages.append({"role": "system", "content": persona})
    messages.append({"role": "system", "content": _read("event-system.txt")})
    voice = instance_text(data_dir, "sys2.txt")
    if voice:
        messages.append({"role": "system", "content": voice})
    messages.append({"role": "system",
                     "content": _read("date.txt").replace("$today", today)})
    return messages


def writer_prompt() -> str:
    return _read("write-memory.txt")


def archive_prompt() -> str:
    """把过期的短期记忆各压成一行。"""
    return _read("archive.txt")
