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
#
# 这里只放**引擎自己的**提示词。工具描述随工具走：谁实现工具，谁把它那几份文本
# 登记进来（`register_tools`），核心只负责读、渲染、校验。`wait` 是唯一例外——
# 它由引擎自己实现（等动静 / 等到点），所以描述也留在这一侧。
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
    "watchdog": ("心跳提醒",
                 "连着几步没有外界动静时问的那一句。改的是语气，不是逻辑。",
                 "watchdog.txt"),
    "persona": ("自我描述 · 整理",
                "后台整理任务用的提示词：把这段时间里对自己更清楚的部分沉淀进自我描述。",
                "persona.txt"),
    "ask": ("问要不要继续",
            "上一轮说了话、没调工具时，在记录末尾挂一句问它要不要继续。要中性，别引导。",
            "ask.txt"),
}

# 工具名 → 参数名。用来校验文件里的 `## 段` 不多不少；工具实现不在这一侧，
# 参数名也只能由登记方给，核心不替它们猜。
_TOOL_PARAMS: Dict[str, list] = {}

# 键 → 文件所在目录。默认 `loop/prompts`；登记进来的工具用它自己的目录。
_BASES: Dict[str, str] = {}


def _register_tool(name: str, label: str, hint: str, params, base: str) -> None:
    if name in META:
        raise PromptError("工具登记重复：%s" % name)
    META[name] = (label, hint + "开头那段是工具描述，`## 参数名` 段落是参数说明，键名不能改。",
                  "tools/%s.txt" % name)
    _TOOL_PARAMS[name] = [str(item) for item in params]
    _BASES[name] = str(base)


def register_tools(entries, base: str) -> None:
    """把一组工具描述登记进来：`{工具名: (显示名, 一句话说明, 参数名列表)}`。

    `base` 是这些文件的根目录（下面是 `tools/<工具>.txt`）。登记之后
    `tool_texts()` 和网页上的提示词编辑都会带上它们；同名重复登记会被拒。
    """
    for name, (label, hint, params) in dict(entries).items():
        _register_tool(str(name), str(label), str(hint), params, base)


def register_prompts(entries, base: str) -> None:
    """登记普通提示词（不是工具）：`{键: (显示名, 一句话说明, 文件名)}`。

    声音（她是谁、怎么说话）这类「实例的内容」归落地侧，由那边登记进来；
    核心只留算法与机制的提示词。没登记就不存在——`has()` 问得到。
    """
    for name, (label, hint, filename) in dict(entries).items():
        key = str(name)
        if key in META:
            raise PromptError("提示词登记重复：%s" % key)
        META[key] = (str(label), str(hint), str(filename))
        _BASES[key] = str(base)


def has(name: str) -> bool:
    """登记过没有（可选的提示词用它判断，别用 try/except）。"""
    key = str(name or "")
    if key.endswith(".txt"):
        key = key[:-4]
    return key in META

_PLACEHOLDER = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_OVERRIDES_DIR = ""


class PromptError(RuntimeError):
    """提示词文件缺失、读不了，或占位符对不上。"""


# 引擎自己的工具：等动静 / 等到点。它的描述留在这一侧；别的工具由实现方登记。
_register_tool("wait", "工具 · wait",
               "停在这儿等对方的动静，或者等到某个时间点。",
               ["until", "kinds"], _DIR)


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
    return default_text(key)


def default_text(name: str) -> str:
    """仓库里那份（不含用户覆盖）：界面上「恢复默认」要拿它。"""
    key = str(name or "")
    if key.endswith(".txt"):
        key = key[:-4]
    if key not in META:
        raise PromptError("没有登记的提示词：%s" % name)
    path = os.path.join(_BASES.get(key, _DIR), META[key][2])
    try:
        with io.open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise PromptError("提示词读不了：%s" % path) from exc


def write_override(data_dir: str, key: str, body: str) -> None:
    """写一份覆盖；`body` 为空就是删掉这条覆盖、回到默认。

    整份重写而不是追加：`prompts.json` 是给人和网页看的，顺序稳定比较好读。
    """
    key = str(key or "")
    if key not in META:
        raise PromptError("没有登记的提示词：%s" % key)
    current = overrides()
    if str(body or "").strip():
        current[key] = str(body)
    else:
        current.pop(key, None)
    target = os.path.join(str(data_dir or "."), "prompts.json")
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with io.open(target, "w", encoding="utf-8") as handle:
        json.dump(current, handle, ensure_ascii=False, indent=2)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


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


def file_parts(name: str):
    """切成 (开头那段, {`## 键`: 正文})；同一份里重复的键当场报错。

    重复是有过事故的：`tools.txt` 里 `## run.command` 写了两遍，后一段静默盖掉
    前一段，模型照着过期的那段理解工具。
    """
    raw = text(name)
    marks = list(_SECTION.finditer(raw))
    head = raw[:marks[0].start()] if marks else raw
    parts: Dict[str, str] = {}
    for index, mark in enumerate(marks):
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(raw)
        key = mark.group(1)
        if key in parts:
            raise PromptError("%s 里有两段 `## %s`：后一段会盖掉前一段" % (name, key))
        parts[key] = raw[start:end].strip()
    return head.strip(), parts


def sections(name: str) -> Dict[str, str]:
    """按 `## 键` 切段，每段取到下一个 `##` 为止。"""
    return file_parts(name)[1]


def tool_names() -> list:
    """已登记的工具名：`wait` 在前，其余按登记顺序。"""
    return list(_TOOL_PARAMS)


def tool_keys() -> list:
    """工具面的全部键：`wait` 与 `wait.until` 这种（工具名 + 参数名）。"""
    out: list = []
    for tool in tool_names():
        out.append(tool)
        out.extend("%s.%s" % (tool, param) for param in _TOOL_PARAMS[tool])
    return out


def tool_texts() -> Dict[str, str]:
    """一个工具一份文件：开头那段是描述，`## 参数名` 是参数说明。

    缺段、多段、重复段都在这里报错——宁可起不来，也不要让模型照着错的说明用工具。
    """
    out: Dict[str, str] = {}
    for tool in tool_names():
        head, parts = file_parts(tool)
        if not head:
            raise PromptError("%s 开头要有工具描述（第一个 `## ` 之前那段）" % tool)
        out[tool] = head
        wanted = set(_TOOL_PARAMS[tool])
        extra = sorted(set(parts) - wanted)
        if extra:
            raise PromptError("%s 里多了一段 `## %s`：schema 里没有这个参数"
                              % (tool, "`、`## ".join(extra)))
        missing = sorted(wanted - set(parts))
        if missing:
            raise PromptError("%s 少了 `## %s`" % (tool, "`、`## ".join(missing)))
        for key, body in parts.items():
            if not body:
                raise PromptError("%s 的 `## %s` 是空的" % (tool, key))
            out[tool + "." + key] = body
    return out
