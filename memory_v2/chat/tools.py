"""Tool definitions and argument parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List


EVENT_TOOL: Dict = {
    "type": "function",
    "function": {
        "name": "event",
        "description": (
            "标这一轮说的事。调一次标一个节点，一轮可以调多次；每次要么给 event（已有节点），"
            "要么给 new（新节点名字）。可以同时标这一轮的大话题和具体点。links 可选：把这个"
            "节点连到已有节点；新节点不填 links 就是孤立节点。event 是正文前最后一个工具调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "description": (
                        "已有节点的 id 或唯一名字。这一轮在说它；不新建节点。"
                    ),
                },
                "new": {
                    "type": "string",
                    "description": (
                        "新节点的名字。四到八个字，大话题或具体点都可以；不要用空泛类别。"
                        "已有同名就改用 event 挂已有。"
                    ),
                },
                "links": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选，0 到多个已有节点（id 或唯一名字）。"
                        "给 event 时表示这个已有点还连到这些点；"
                        "给 new 时表示新节点连到这些点。不填就是孤立节点。"
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
    event = str(raw.get("event") or "").strip()
    name = str(raw.get("new") or "").strip()
    links = []
    for item in raw.get("links") or []:
        target = str(item).strip()
        if target and target not in links:
            links.append(target)
    if event and name:
        raise ToolCallError("event 和 new 只能给一个；标两件事就调两次")
    if not event and not name:
        raise ToolCallError("missing event/new")
    return EventCall(event=event, new=name, links=links)


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
