"""Tool definitions and argument parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List


EVENT_MODES = ("继续", "子事", "回", "换事", "接旧", "关联")


EVENT_TOOL: Dict = {
    "type": "function",
    "function": {
        "name": "event",
        "description": ("记录这一轮在说哪件事。每轮正文前最后调它：先 recall/remember，"
                        "event 之后直接写正文，不要再调工具；返回现在在哪一层、和哪些事件相连。"),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(EVENT_MODES),
                    "description": (
                        "继续：这一轮还是上一件，不需要 name；"
                        "子事：当前这件事里的一个新小节，必须给 name；"
                        "回：退回上一层，不需要别的参数；"
                        "换事：换到另一件不相干的事，必须给 name；"
                        "接旧：接续以前某件旧事，必须给 event；"
                        "关联：只加一条相关，不换当前事件，必须给 event。"
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "换事 / 子事时必填；三到五个字，具体到是什么（如“空调一拖四”），"
                        "不要用“聊天”“日常”这种空名；当前路径上已有的同名不要重复建。"
                    ),
                },
                "parent": {
                    "type": "string",
                    "description": (
                        "可选，只用于子事。要挂到哪一件下面（id 或唯一名字）；"
                        "不给就挂当前这件。如果这件其实是上一层的小节，指到上一层，别挂错。"
                    ),
                },
                "parents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选，只用于子事。它同时还属于别的根时列在这里（id 或唯一名字），"
                        "每条会加一个属于；当前这件自动算一个父，不要重复列。"
                    ),
                },
                "event": {
                    "type": "string",
                    "description": (
                        "接旧 / 关联时必填。优先用 id；名字只有在唯一时可用，多个同名必须用 id。"
                    ),
                },
                "because": {
                    "type": "string",
                    "description": (
                        "接旧 / 关联的依据。写具体的同一处：同一个对象、同一个人、"
                        "同一段时间或地点；不要写“同一件”这种空话。"
                    ),
                },
                "links": {
                    "type": "array",
                    "description": (
                        "可选。只加相关边，和别的旧事有关时写；包含关系用子事，不要写这里。"
                        "没有就省略，不要传空数组。"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "event": {"type": "string",
                                      "description": "事件 id，或唯一的名字。"},
                            "relation": {"type": "string", "enum": ["相关"],
                                         "description": "固定为相关。"},
                        },
                        "required": ["event"],
                    },
                },
            },
            "required": ["mode"],
        },
    },
}


RECALL_TOOL: Dict = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "按关键词找以前场次的事，返回最像的一件和备选。只搜旧场次；"
            "当前这场已经在对话里，不要用 recall 查当前这场。"
            "一次只问一件事；两个话题分两次调用。没找到就说没找到，不要硬选。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "一到五个具体名词：人名、物、地点、事由。"
                        "不要写“上次”“最近”这种相对时间；不要把两个话题混在一次里。"
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
                        "可选，明确不想再看到的旧事件（id 或唯一名字），一般不用。"
                        "不要用它排除当前场：当前场本来就不参与回忆。"
                    ),
                },
            },
            "required": ["keywords"],
        },
    },
}


REMEMBER_TOOL: Dict = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "取回一件旧事的回忆（一段第一人称的话）。一次最多两件；"
            "只取旧场事件；先 recall 再 remember，不要凭印象拼 id。"
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


TOOLS = [EVENT_TOOL, RECALL_TOOL, REMEMBER_TOOL]


@dataclass
class EventCall:
    mode: str
    name: str = ""
    event: str = ""
    because: str = ""
    parent: str = ""
    parents: List[str] = field(default_factory=list)
    links: List[Dict[str, str]] = field(default_factory=list)


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


def parse_event_call(arguments: str) -> EventCall:
    raw = _load(arguments)
    mode = str(raw.get("mode") or "").strip()
    name = str(raw.get("name") or "").strip()
    event = str(raw.get("event") or raw.get("claim") or "").strip()
    because = str(raw.get("because") or "").strip()
    parent = str(raw.get("parent") or "").strip()
    parents = [str(item).strip() for item in (raw.get("parents") or [])
               if str(item).strip()]

    links = []
    for item in raw.get("links") or []:
        if isinstance(item, str):
            target = item.strip()
            if target:
                links.append({"event": target, "relation": "相关"})
            continue
        if not isinstance(item, dict):
            continue
        target = str(item.get("event") or "").strip()
        if target:
            links.append({"event": target,
                          "relation": str(item.get("relation") or "相关").strip() or "相关"})

    if not mode:
        if event:
            mode = "接旧"
        elif name:
            mode = "自动"
        else:
            raise ToolCallError("missing mode")
    if mode not in EVENT_MODES and mode != "自动":
        raise ToolCallError("unknown mode: %s" % mode)
    if mode in ("换事", "子事") and not name:
        raise ToolCallError("missing name")
    if mode in ("接旧", "关联") and not event:
        raise ToolCallError("missing event")

    return EventCall(mode=mode, name=name, event=event, because=because,
                     parent=parent, parents=parents, links=links)


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


def parse_remember_call(arguments: str) -> str:
    raw = _load(arguments)
    event = str(raw.get("event") or "").strip()
    if not event:
        raise ToolCallError("missing event")
    return event
