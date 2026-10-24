"""memory_v2 的工具面：remember / recall / recollect。

写：模型自己写 note，存进事件；note 不进当前上下文。
读：recall 联想（激活、光标）→ recollect 取具体，编写模型写成回忆。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from memory_v2.core.embed import EmbedError
from memory_v2.read.activate import activate
from memory_v2.read.search import event_date, event_segments
from memory_v2.read.write import write_memory
from memory_v2.store.frozen import global_dir
from memory_v2.store.graph import Graph

from .context import now


REMEMBER_TOOL = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "把这件事写进记忆。name 是以后搜它的键：主语 + 动作 + 结果，6–24 字；"
            "note 是你自己写的记得的内容，一两段，写结论、数字、待办，不抄过程。"
            "scope=long 长期保留，short 只留几天（工作用）。"
            "写进去的内容不会回到当前记录，之后要看就用 recall / recollect。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "新事件的名字；已有同名就挂到那件上。"},
                "event": {"type": "string",
                          "description": "可选，挂到已有事件（id 或唯一名字）。"},
                "note": {"type": "string",
                         "description": "要记住的内容，一两段。"},
                "links": {"type": "array", "items": {"type": "string"},
                          "description": "可选，连到相关的旧事件。"},
                "scope": {"type": "string", "enum": ["long", "short"],
                          "description": "long 长期，short 只留几天；默认 long。"},
                "days": {"type": "integer",
                         "description": "scope=short 时保留几天，默认 3。"},
            },
            "required": ["note"],
        },
    },
}


RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "按关键词联想记忆，返回事件的 id、名字、时间。"
            "关键词用具体名词：人名、物、地点、事由；一次只问一件事。"
            "看具体用 recollect；连边用 remember 的 links。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"},
                             "description": "一到五个具体名词，不要写相对时间。"},
                "after": {"type": "string",
                          "description": "可选，只要这一天之后（含当天），YYYY-MM-DD。"},
                "before": {"type": "string",
                           "description": "可选，只要这一天之前（含当天），YYYY-MM-DD。"},
                "exclude": {"type": "array", "items": {"type": "string"},
                            "description": "可选，不想再看到的 id 或名字。"},
            },
            "required": ["keywords"],
        },
    },
}


RECOLLECT_TOOL = {
    "type": "function",
    "function": {
        "name": "recollect",
        "description": (
            "取一件具体的记忆，由编写模型写成一段第一人称回忆；只有这段回忆进上下文。"
            "先 recall 再 recollect，不要凭印象拼 id。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event": {"type": "string",
                          "description": "事件 id，来自 recall 的候选或 remember 的返回。"},
            },
            "required": ["event"],
        },
    },
}


class MemoryBridge:
    def __init__(self, data_dir: str = "", writer: Optional[Callable[[str], str]] = None,
                 embedder=None, lexical: bool = False, recent_steps: int = 0,
                 enabled: bool = True):
        self.data_dir = str(data_dir or "")
        self.writer = writer
        self.embedder = embedder
        self.lexical = bool(lexical)
        self.recent_steps = int(recent_steps or 0)
        self.step = 0
        self.enabled = bool(enabled and self.data_dir)
        self.graph = Graph.open(self.data_dir) if self.enabled else None
        if self.graph is not None:
            self._merge_frozen()
        self.activation = {}
        self.candidates: List[str] = []

    @classmethod
    def disabled(cls) -> "MemoryBridge":
        return cls(data_dir="", enabled=False)

    def _merge_frozen(self) -> None:
        frozen = os.path.join(global_dir(self.data_dir), "graph.json")
        if not os.path.exists(frozen):
            return
        other = Graph(frozen)
        for event_id, event in other.events.items():
            self.graph.events.setdefault(event_id, event)
        for key, edge in other.edges.items():
            self.graph.edges.setdefault(key, edge)

    def tools(self) -> List[dict]:
        if not self.enabled:
            return []
        return [REMEMBER_TOOL, RECALL_TOOL, RECOLLECT_TOOL]

    def call(self, name: str, arguments: str) -> str:
        if not self.enabled:
            return "记忆还没接上。"
        try:
            payload = json.loads(arguments or "{}")
        except ValueError:
            return "%s 的参数不是 JSON。" % name
        if name == "remember":
            return self._remember(payload)
        if name == "recall":
            return self._recall(payload)
        if name == "recollect":
            return self._recollect(payload)
        return "没有这个工具：%s" % name

    # ── 写 ────────────────────────────────────────────────────────────────
    def _remember(self, payload: dict) -> str:
        note = str(payload.get("note") or "").strip()
        if not note:
            return "remember 需要 note。"
        name = str(payload.get("name") or "").strip()
        reference = str(payload.get("event") or "").strip()

        event_id = ""
        if reference:
            event_id = self.graph.resolve(reference) or ""
            if not event_id:
                return "没有这一件：%s。" % reference
        elif name:
            same = [event_id for event_id, event in self.graph.events.items()
                    if str(event.get("name") or "") == name]
            if same:
                event_id = max(same, key=self.graph.event_order)

        created = not event_id
        if created:
            if not name:
                return "remember 需要 name 或 event。"
            event_id = self.graph.add_event(name)

        scope = str(payload.get("scope") or "long").strip().lower()
        if scope not in ("long", "short"):
            scope = "long"
        expires_at = ""
        if scope == "short":
            try:
                days = int(payload.get("days") or 3)
            except (TypeError, ValueError):
                days = 3
            expires_at = (datetime.now() + timedelta(days=max(0, days))).strftime(
                "%Y-%m-%dT%H:%M:%S")

        self.graph.add_note(event_id, note, now().replace(" ", "T"),
                            scope=(scope if created else ""),
                            expires_at=(expires_at if created else ""))
        self.graph.events[event_id]["step"] = int(self.step or 0)
        for reference in payload.get("links") or []:
            target = self.graph.resolve(str(reference))
            if target and target != event_id:
                self.graph.add_edge(event_id, target, "关联")
        self.graph.save()

        event = self.graph.events[event_id]
        return "已记：%s（%s，%s）" % (event.get("name") or event_id, event_id,
                                      event.get("scope") or "long")

    # ── 读 ────────────────────────────────────────────────────────────────
    def _recall(self, payload: dict) -> str:
        keywords = [str(item).strip() for item in (payload.get("keywords") or [])
                    if str(item).strip()]
        if not keywords:
            return "recall 需要 keywords。"
        exclude = [str(item).strip() for item in (payload.get("exclude") or [])
                   if str(item).strip()]
        after = str(payload.get("after") or "").strip()
        before = str(payload.get("before") or "").strip()
        embedded = self.embedder is not None and getattr(self.embedder, "available", False)
        if not embedded and not self.lexical:
            return "嵌入不可用，词面开关关着，这次查不到。"
        try:
            result = activate(self.graph, self.data_dir, keywords,
                              embedder=self.embedder if embedded else None,
                              exclude=exclude, after=after, before=before,
                              limit=5, lexical=self.lexical,
                              recent_steps=self.recent_steps,
                              current_step=self.step)
        except EmbedError:
            if not self.lexical:
                return "嵌入调用失败，词面开关关着，这次查不到。"
            embedded = False
            result = activate(self.graph, self.data_dir, keywords, embedder=None,
                              exclude=exclude, after=after, before=before,
                              limit=5, lexical=True,
                              recent_steps=self.recent_steps,
                              current_step=self.step)
        self.activation = dict(result["activation"])
        self.candidates = [item["id"] for item in result["candidates"]]
        items = list(result["candidates"] or [])
        if not items:
            items = list(result.get("near") or [])
        if not items:
            return "没有找到。换个关键词再试。"
        lines = ["找到 %d 件：" % len(items)]
        if not embedded:
            lines.append("（嵌入服务不可用，只按词面找）")
        for item in items[:5]:
            lines.append("%s %s · %s" % (item["id"], item.get("name") or "",
                                         item.get("date") or ""))
        lines.append("看具体用 recollect；连边用 remember 的 links。")
        return "\n".join(lines)

    def _recollect(self, payload: dict) -> str:
        reference = str(payload.get("event") or "").strip()
        event_id = self.graph.resolve(reference)
        if not event_id:
            return "没有这一件：%s。" % reference
        event = self.graph.events[event_id]
        notes = event.get("notes") or []
        if notes:
            material = "\n\n".join(
                "%s · 0.80\n%s" % (str(note.get("ts") or "")[:10],
                                   str(note.get("text") or ""))
                for note in notes)
        else:
            segments = event_segments(self.graph, self.data_dir, event_id)
            material = "\n\n".join(
                "%s · 0.60\n%s" % (segment["date"], segment["text"])
                for segment in segments)
        if not material.strip():
            return "这一件没有材料。"
        if self.writer is None:
            return "编写模型还没接上。"
        try:
            recollection = self.writer(material)
        except Exception as exc:
            return "编写失败：%s" % exc
        event["access"] = min(1.0, float(event.get("access", 0.0)) + 0.1)
        event["access_ts"] = time.time()
        self.graph.save()
        return recollection
