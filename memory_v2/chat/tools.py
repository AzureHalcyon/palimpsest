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
        "description": "记录这一轮在说哪件事。每轮回复前先调它；返回现在在哪一层。",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(EVENT_MODES),
                    "description": (
                        "继续＝还是同一件；子事＝进到当前这件事里的一个小节；"
                        "回＝小节说完，回上一层；换事＝换到另一件不相干的事；"
                        "接旧＝接着以前某件事说；关联＝只记一条关系，不换当前这件事。"
                    ),
                },
                "name": {
                    "type": "string",
                    "description": "换事 / 子事时的名字，三到五个字，具体到是什么。",
                },
                "parent": {
                    "type": "string",
                    "description": "可选。子事要挂到哪一件下面（名字或 id）；不给就挂当前这件。",
                },
                "parents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选。子事还同时属于别的根时，列出来（名字或 id）；当前这件自动算一个父。",
                },
                "event": {
                    "type": "string",
                    "description": "接旧 / 关联时给另一件事的 id 或名字。",
                },
                "because": {
                    "type": "string",
                    "description": "接旧 / 关联的依据：同一个对象、同一个人、同一段时间。",
                },
                "links": {
                    "type": "array",
                    "description": "可选。和别的旧事相关时写；包含关系用子事，不必写这里。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "event": {"type": "string", "description": "事件 id 或名字。"},
                            "relation": {"type": "string", "enum": ["相关"]},
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
        "description": "按关键词找以前的事；返回最像的一件和备选（id、名字、日期、命中或语义近）。",
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "两到五个关键词：人名、物；不要写“上次”这种相对时间。",
                },
                "after": {
                    "type": "string",
                    "description": "可选。只要这一天之后的（含），YYYY-MM-DD。",
                },
                "before": {
                    "type": "string",
                    "description": "可选。只要这一天之前的（含），YYYY-MM-DD。",
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
        "description": "取回一件旧事的回忆（一段第一人称的话），按事件 id。",
        "parameters": {
            "type": "object",
            "properties": {
                "event": {"type": "string", "description": "事件 id，来自候选或 event 的返回。"},
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
    return {
        "keywords": keywords,
        "after": str(raw.get("after") or "").strip(),
        "before": str(raw.get("before") or "").strip(),
    }


def parse_remember_call(arguments: str) -> str:
    raw = _load(arguments)
    event = str(raw.get("event") or "").strip()
    if not event:
        raise ToolCallError("missing event")
    return event
