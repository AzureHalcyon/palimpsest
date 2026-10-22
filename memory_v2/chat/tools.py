"""Tool definitions and argument parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# 挂起最多攒几轮；满了必须先落成一件，防止缓冲变成“永远不落”。
MAX_HELD = 6

# 名字里出现“他说/她问…”这类说话动作，说明记的是谁干了什么，不是那件事本身。
_SPEECH_VERBS = (
    "说", "问", "答", "要求", "指出", "承认", "担心", "接受", "同意",
    "觉得", "认为", "提到", "补充", "强调", "提醒", "确认", "解释",
    "反问", "追问", "答应", "提议", "建议", "主张", "反驳", "抱怨",
    "笑", "讲", "聊", "回答",
)
_SPEAKERS = ("他", "她", "对方", "我")


EVENT_TOOL: Dict = {
    "type": "function",
    "function": {
        "name": "event",
        "description": (
            "标这一轮。先判断有没有值得记的内容，再判断这个名字盖不盖得住这一轮。\n\n"
            "【没值得记的内容】skip=true：寒暄、开场、问对方想聊什么、"
            "纯确认/收尾（“行/收到/那就这样”）、没有判断的闲扯（复述、附和、打岔）、"
            "只做笔记/索引/格式维护的轮。\n\n"
            "【有值得记的内容】它能不能被某个已有节点的名字原样盖住？"
            "同一个问题下的补充、例子、反驳、确认、换个说法，都算盖得住。\n"
            "· 盖得住：event=<id>，挂到那个节点上。\n"
            "· 盖不住：new，给这一轮起个名字。\n"
            "· 还看不出是什么事（刚起头、要靠上一轮才成立）：hold，最多 6 轮，满了必须先落。\n\n"
            "【名字】具体主语 + 动作 + 结果，6–24 字，至少两个实词；写内容不写“谁在说”。"
            "再想一层：以后你会用什么话把它想起来？那句话里的词要出现在名字里——"
            "名字就是以后点它的那一下。名字和已有节点重复，说明这件事已经有了，挂到那条上。\n\n"
            "【连接】links 连相关的旧节点，别把每轮串成时间链。\n\n"
            "【例子】\n"
            "new：「未验证拆成三档，每档单独印」\n"
            "挂：「明细行也要写分母」——同一问下的补充，挂到「三档加总等于分母」那个节点上\n"
            "hold：「那分母对不上呢？」——刚起头，还没成形\n"
            "skip：「收到，那就这样」\n\n"
            "【落盘】new/挂已有会把挂起的几轮并进这个节点；held 可只并指定轮次。"
            "event 是正文前最后一个工具调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "description": (
                        "已有节点的 id 或唯一名字；只有它的名字能替代这一轮的内容才挂。"
                    ),
                },
                "new": {
                    "type": "string",
                    "description": (
                        "新节点名：主语 + 动作 + 结果，6–24 字。"
                        "已有同名就改用 event 挂已有。hold 时是草稿名，可空。"
                    ),
                },
                "hold": {
                    "type": "boolean",
                    "description": (
                        "这一轮有内容，但还看不出它是什么事（刚起头、还没成形）。"
                        "最多挂 6 轮，满了必须先落。可以带 new 当草稿名。"
                        "不能和 event/links/rename/unlink/held 同用。"
                    ),
                },
                "held": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "可选，只把这几轮挂起的并进来（写轮次号）；"
                        "不写就并入全部挂起的轮，写 [] 就一个都不并。"
                    ),
                },
                "skip": {
                    "type": "boolean",
                    "description": (
                        "这一轮没有值得记的内容：寒暄、开场、纯确认/收尾、"
                        "没有判断的闲扯、只做笔记/索引/格式维护。"
                        "不建节点，不能和 new/event/hold/links 同用。"
                    ),
                },
                "links": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选，0 到多个已有节点（id 或唯一名字），连到新/已有节点上。"
                    ),
                },
                "rename": {
                    "type": "string",
                    "description": (
                        "可选，把 event 指定的节点改名；只用于改错名，不新建节点。"
                        "不能与 new 同时给。"
                    ),
                },
                "unlink": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选，断开 event 与这些已有节点之间的边（id 或唯一名字）。"
                        "只断边，不删节点；不能与 new 同时给。"
                    ),
                },
            },
            "required": [],
        },
    },
}


RECALL_TOOL: Dict = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "按关键词查所有节点（含本场），返回 id、名字、场次、连接数。"
            "用来找可连的旧节点，也可以回忆旧事；只返回节点，不返回原文。"
            "一次只问一件事；两个话题分两次调用。没找到就换个词，不要硬选。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "一到五个具体名词：人名、物、地点、事由。"
                        "不要写相对时间；不要把两个话题混在一次里。"
                    ),
                },
                "after": {
                    "type": "string",
                    "description": (
                        "可选，只要这一天之后（含当天）的，YYYY-MM-DD。"
                        "用相对时间问时，先换算成具体日期再传。"
                    ),
                },
                "before": {
                    "type": "string",
                    "description": (
                        "可选，只要这一天之前（含当天）的，YYYY-MM-DD。"
                        "用相对时间问时，先换算成具体日期再传。"
                    ),
                },
                "exclude": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选，明确不想再看到的节点（id 或唯一名字），一般不用。"
                    ),
                },
            },
            "required": ["keywords"],
        },
    },
}


RECOLLECT_TOOL: Dict = {
    "type": "function",
    "function": {
        "name": "recollect",
        "description": (
            "取回一件旧事的原文（压成一段第一人称回忆）。只取旧场事件；"
            "本场节点不用取，原文就在对话里。先 recall 再 recollect，不要凭印象拼 id。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "description": (
                        "事件 id，必须来自本轮 recall 的候选，或 event 的返回；"
                        "不要自己拼，也不要用名字。"
                    ),
                },
            },
            "required": ["event"],
        },
    },
}


TOOLS = [EVENT_TOOL, RECALL_TOOL, RECOLLECT_TOOL]


@dataclass
class EventCall:
    event: str = ""
    new: str = ""
    links: List[str] = field(default_factory=list)
    rename: str = ""
    unlink: List[str] = field(default_factory=list)
    skip: bool = False
    hold: bool = False
    held: Optional[List[int]] = None


class ToolCallError(ValueError):
    """Tool arguments cannot be used. The message is returned to the model."""


def _load(arguments: str) -> dict:
    try:
        raw = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ToolCallError("not JSON: %s" % exc) from exc
    if not isinstance(raw, dict):
        raise ToolCallError("not an object")
    return raw


def _check_name(name: str) -> None:
    for speaker in _SPEAKERS:
        if not name.startswith(speaker):
            continue
        rest = name[len(speaker):]
        for verb in _SPEECH_VERBS:
            if rest.startswith(verb):
                raise ToolCallError(
                    "名字记内容，不记谁说了什么：把“%s%s…”写成那件事本身"
                    % (speaker, verb))


def parse_event_call(arguments: str) -> EventCall:
    raw = _load(arguments)
    event = str(raw.get("event") or "").strip()
    name = str(raw.get("new") or "").strip()
    rename = str(raw.get("rename") or "").strip()
    skip = bool(raw.get("skip"))
    hold = bool(raw.get("hold"))
    links = []
    for item in raw.get("links") or []:
        target = str(item).strip()
        if target and target not in links:
            links.append(target)
    unlink = []
    for item in raw.get("unlink") or []:
        target = str(item).strip()
        if target and target not in unlink:
            unlink.append(target)
    held = None
    if "held" in raw and raw.get("held") is not None:
        if not isinstance(raw["held"], list):
            raise ToolCallError("held 写轮次数组，如 [12,13]；不要并入挂起就写 []")
        held = []
        for item in raw["held"]:
            try:
                number = int(item)
            except (TypeError, ValueError):
                raise ToolCallError("held 写轮次数字，如 [12,13]")
            if number <= 0:
                raise ToolCallError("held 写轮次数字，如 [12,13]")
            if number not in held:
                held.append(number)
    if skip:
        if event or name or links or rename or unlink or hold or held is not None:
            raise ToolCallError(
                "skip 只用于没有内容的轮，不能和 new/event/hold/links/rename/unlink/held 同用")
        return EventCall(skip=True)
    if hold:
        if event or links or rename or unlink or held is not None:
            raise ToolCallError("hold 只挂起这一轮，不能和 event/links/rename/unlink/held 同用")
        if name:
            _check_name(name)
        return EventCall(hold=True, new=name)
    if event and name:
        raise ToolCallError("event 和 new 只能给一个；标两件事就调两次")
    if not event and not name:
        raise ToolCallError("missing event/new（没有内容就用 skip=true，还没成形就用 hold=true）")
    if name:
        _check_name(name)
    if name and (rename or unlink):
        raise ToolCallError("new 不能和 rename/unlink 同时给；先建新节点，再单独改")
    if (rename or unlink) and not event:
        raise ToolCallError("rename/unlink 必须同时给 event")
    return EventCall(event=event, new=name, links=links,
                     rename=rename, unlink=unlink, held=held)


def parse_recall_call(arguments: str) -> dict:
    raw = _load(arguments)
    keywords = []
    for item in raw.get("keywords") or []:
        keyword = str(item).strip()
        if keyword:
            keywords.append(keyword)
    if not keywords:
        raise ToolCallError("missing keywords")
    exclude = [str(item).strip() for item in (raw.get("exclude") or [])
               if str(item).strip()]
    return {
        "keywords": keywords,
        "after": str(raw.get("after") or "").strip(),
        "before": str(raw.get("before") or "").strip(),
        "exclude": exclude,
    }


def parse_recollect_call(arguments: str) -> str:
    raw = _load(arguments)
    event = str(raw.get("event") or "").strip()
    if not event:
        raise ToolCallError("missing event")
    return event
