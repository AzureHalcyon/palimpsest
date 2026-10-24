"""提示词从 `prompts/` 读，不写在代码里。

改完不用重启：每次调用都重新读文件。占位符用 `$名字`（与
`memory_v2/prompts/date.txt` 的 `$today` 一致），因为提示词里常有 JSON 花括号，
`str.format` 那种 `{}` 会和正文打架。

和 App 那边一样分两层：仓库里的 `prompts/*.txt` 是默认；用户在运行目录下放的
`prompts.json` 是覆盖，**不改仓库源文件**（升级不会被冲掉）。`META` 是注册表：
每份提示词是什么、改坏了会怎样——改之前先看一眼。
"""

from __future__ import annotations

import io
import json
import os
import re
from typing import Dict

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# 键 → (显示名, 说明, 文件名)。加提示词要在这里登记，自检会查有没有漏。
META = {
    "sink": ("sink · 开场与边界",
             "常驻在 system 最前面：它是谁、怎么说话、这段历史怎么用。改坏了整个人的口吻都会变。",
             "sink.txt"),
    "compress-system": ("压缩 · 系统",
                        "只负责要求摘要模型输出 JSON；一般不用动。",
                        "compress-system.txt"),
    "compress": ("压缩 · 正文",
                 "一块记录怎么压成一段摘要：越早越略、保留专名数字、结尾写“现在到哪了”。",
                 "compress.txt"),
    "tools": ("工具说明",
              "remember / recall / recollect / wait 四个工具的说明文字（`## 键` 分段，键名不能改）。",
              "tools.txt"),
    "watchdog": ("心跳提醒",
                 "连着几步没有外界动静时问的那一句。改的是语气，不是逻辑。",
                 "watchdog.txt"),
}

_PLACEHOLDER = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_OVERRIDES_DIR = ""


class PromptError(RuntimeError):
    """提示词文件缺失、读不了，或占位符对不上。"""


def use_overrides(data_dir: str) -> None:
    """从这儿之后用 `<data_dir>/prompts.json` 里的覆盖；仓库里的文件当默认。"""
    global _OVERRIDES_DIR
    _OVERRIDES_DIR = str(data_dir or "")


def overrides() -> Dict[str, str]:
    """用户覆盖：{键: 文本}，只认注册表里有的键。"""
    if not _OVERRIDES_DIR:
        return {}
    path = os.path.join(_OVERRIDES_DIR, "prompts.json")
    if not os.path.exists(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()
            if key in META and str(value).strip()}


def text(name: str) -> str:
    """读一份提示词：有覆盖用覆盖，否则读仓库里的默认；原样返回。"""
    key = str(name or "")
    if key.endswith(".txt"):
        key = key[:-4]
    if key not in META:
        raise PromptError("没有登记的提示词：%s" % name)
    override = overrides().get(key)
    if override:
        return override
    path = os.path.join(_DIR, META[key][2])
    try:
        with io.open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise PromptError("提示词读不了：%s" % path) from exc


def render(filename: str, **fields) -> str:
    """读文件并把 `$名字` 换成给定值；有没给的占位符就报错，别带着 `$x` 发给模型。"""
    raw = text(filename)
    out = raw
    for key, value in fields.items():
        out = out.replace("$" + key, str(value))
    missing = sorted({match.group(1) for match in _PLACEHOLDER.finditer(out)})
    if missing:
        raise PromptError("%s 里还有没给的占位符：%s"
                          % (filename, "、".join(missing)))
    return out


_SECTION = re.compile(r"^## +(\S+)[ \t]*$", re.M)


def sections(name: str) -> Dict[str, str]:
    """按 `## 键` 切段，每段取到下一个 `##` 为止。"""
    raw = text(name)
    marks = list(_SECTION.finditer(raw))
    parts: Dict[str, str] = {}
    for index, mark in enumerate(marks):
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(raw)
        parts[mark.group(1)] = raw[start:end].strip()
    return parts


# 工具面要的每一段文字。缺一段就报错：宁可起不来，也不要静默少一句说明。
TOOL_KEYS = (
    "remember", "remember.name", "remember.event", "remember.note",
    "remember.links", "remember.scope", "remember.days",
    "recall", "recall.keywords", "recall.after", "recall.before", "recall.exclude",
    "recollect", "recollect.event",
    "wait", "wait.until", "wait.kinds",
)


def tool_texts() -> Dict[str, str]:
    parts = sections("tools.txt")
    missing = [key for key in TOOL_KEYS if not str(parts.get(key) or "").strip()]
    if missing:
        raise PromptError("tools.txt 缺这些段：%s" % "、".join(missing))
    return parts
