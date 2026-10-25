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
from memory_v2.read.archive import archive_expired
from memory_v2.read.search import event_date, event_segments
from memory_v2.store.frozen import global_dir
from memory_v2.store.graph import Graph

from . import prompt_files
from .context import now


def _remember_tool(text: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "remember",
            "description": text["remember"],
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": text["remember.name"]},
                    "event": {"type": "string",
                              "description": text["remember.event"]},
                    "note": {"type": "string",
                             "description": text["remember.note"]},
                    "scope": {"type": "string", "enum": ["long", "short"],
                              "description": text["remember.scope"]},
                },
                "required": ["note"],
            },
        },
    }


def _link_tool(text: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "link",
            "description": text["link"],
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "string", "description": text["link.a"]},
                    "b": {"type": "string", "description": text["link.b"]},
                    "relation": {"type": "string",
                                 "description": text["link.relation"]},
                },
                "required": ["a", "b"],
            },
        },
    }


def _recall_tool(text: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "recall",
            "description": text["recall"],
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"},
                                 "description": text["recall.keywords"]},
                    "after": {"type": "string",
                              "description": text["recall.after"]},
                    "before": {"type": "string",
                               "description": text["recall.before"]},
                    "exclude": {"type": "array", "items": {"type": "string"},
                                "description": text["recall.exclude"]},
                },
                "required": ["keywords"],
            },
        },
    }


def _recollect_tool(text: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "recollect",
            "description": text["recollect"],
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {"type": "string",
                              "description": text["recollect.event"]},
                },
                "required": ["event"],
            },
        },
    }


def _forget_tool(text: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "forget",
            "description": text["forget"],
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {"type": "string",
                              "description": text["forget.event"]},
                },
                "required": ["event"],
            },
        },
    }


def _rename_tool(text: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "rename",
            "description": text["rename"],
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {"type": "string",
                              "description": text["rename.event"]},
                    "name": {"type": "string",
                             "description": text["rename.name"]},
                },
                "required": ["event", "name"],
            },
        },
    }


def _head(text: str, limit: int = 28) -> str:
    """一句话的头：返回里带一下，才看得出这次写进去的是不是想写的那条。"""
    line = " ".join(str(text or "").split())
    return line[:limit] + ("…" if len(line) > limit else "")


def _looks_like_id(name: str) -> bool:
    """`e-` 加一串十六进制：那是事件 id，不是名字（真发生过：id 当名字建了个空壳）。"""
    body = str(name or "").strip()
    if not body.startswith("e-"):
        return False
    rest = body[2:]
    return len(rest) >= 6 and all(char in "0123456789abcdef" for char in rest.lower())


def _size_hint(graph, event_id: str) -> str:
    """这一件有多少字：recall 只给指针，字数帮着判断要不要深挖（正文仍然不给）。"""
    notes = graph.events.get(event_id, {}).get("notes") or []
    chars = sum(len(str(note.get("text") or "")) for note in notes)
    return " · %d 字" % chars if chars else ""


class MemoryBridge:
    def __init__(self, data_dir: str = "", writer: Optional[Callable[[str], str]] = None,
                 embedder=None, lexical: bool = False, recent_steps: int = 0,
                 enabled: bool = True, archiver=None):
        self.data_dir = str(data_dir or "")
        self.writer = writer
        self.archiver = archiver      # 过期短期记忆的压缩：material -> {id: 一行}
        self.on_recall = None         # (问题, 命中的 id) -> None，用来记检索日志
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
        text = prompt_files.tool_texts()
        return [_remember_tool(text), _link_tool(text), _recall_tool(text),
                _recollect_tool(text), _forget_tool(text), _rename_tool(text)]

    def call(self, name: str, arguments: str) -> str:
        if not self.enabled:
            return "记忆还没接上。"
        try:
            payload = json.loads(arguments or "{}")
        except ValueError:
            return "%s 的参数不是 JSON。" % name
        if name == "remember":
            return self._remember(payload)
        if name == "link":
            return self._link(payload)
        if name == "recall":
            return self._recall(payload)
        if name == "recollect":
            return self._recollect(payload)
        if name == "forget":
            return self._forget(payload)
        if name == "rename":
            return self._rename(payload)
        return "没有这个工具：%s" % name

    # ── 写 ────────────────────────────────────────────────────────────────
    def _remember(self, payload: dict) -> str:
        note = str(payload.get("note") or "").strip()
        if not note:
            return ("remember 需要 note：这次什么都没写。把要记的内容放进 note 再调一次"
                    "（name 或 event 一起给，才知道记到哪一件上）。只加关系用 link。")
        name = str(payload.get("name") or "").strip()
        reference = str(payload.get("event") or "").strip()
        if not reference and _looks_like_id(name):
            return ("name 看着像 id（%s）：要改那件就把 id 放进 event，别当新名字用"
                    "（不然会建出一件叫这个名字的空壳）。这次什么都没写。" % name)

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
                return ("remember 需要 name 或 event：没有说记到哪一件上，这次什么都没写。"
                        "新开一件就给 name；改旧的那件就给 event（id 或名字）。")
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

        # 一件事只有一条笔记：再记就是改这条，不是另加一条。
        # 以前是追加，结果同一件事攒了三条几乎一样的笔记，她自己都分不清哪条算数。
        event = self.graph.events[event_id]
        notes = event.get("notes") or []
        stamp = now().replace(" ", "T")
        before = _head(str((notes[0].get("text") if notes else "") or ""))
        if before and str(notes[0].get("text") or "") == note:
            return ("这条和「%s」里已经记的一字不差，没再写。要改就换个说法。"
                    % (event.get("name") or event_id))

        if created or not notes:
            self.graph.add_note(event_id, note, stamp,
                                scope=(scope if created else ""),
                                expires_at=(expires_at if created else ""))
        else:
            # 老数据可能还挂着几条：合成这一条
            notes[0]["text"] = note
            notes[0]["edited"] = stamp
            del notes[1:]
            event["updated"] = stamp
            if scope == "short":
                event["scope"] = scope
                event["expires_at"] = expires_at
        self.graph.events[event_id]["step"] = int(self.step or 0)
        self.graph.save()

        event = self.graph.events[event_id]
        # 只说"新建/改了"，不回显正文：note 本身在记录里留十来步，够它回头确认；
        # 再久就该用 recollect 取（note 不进上下文，记录里也不重复）。
        if created:
            return "已记（新建）：%s（%s，%s，%d 字）" % (
                event.get("name") or event_id, event_id,
                event.get("scope") or "long", len(note))
        return "已更新：%s（%s）——换成了新的这 %d 字%s" % (
            event.get("name") or event_id, event_id, len(note),
            ("，原来那句是「%s」" % before) if before else "")

    def _link(self, payload: dict) -> str:
        """把两件事连起来，不动任何一边的内容。"""
        left = str(payload.get("a") or "").strip()
        right = str(payload.get("b") or "").strip()
        if not left or not right:
            return "link 需要 a 和 b（各给一个 id 或名字，先用 recall 拿）。"
        first = self.graph.resolve(left)
        second = self.graph.resolve(right)
        missing = [name for name, event_id in ((left, first), (right, second))
                   if not event_id]
        if missing:
            return ("没找到%s（用 recall 返回的 id，别凭印象写）。这次没连。"
                    % "".join("「%s」" % name for name in missing))
        if first == second:
            return "a 和 b 是同一件，不用连。"
        for edge in self.graph.edges.values():
            if {edge.get("a"), edge.get("b")} == {first, second}:
                return "这两件本来就连着，没再连。"
        relation = str(payload.get("relation") or "").strip() or "关联"
        self.graph.add_edge(first, second, relation)
        self.graph.save()
        return "连上了：%s — %s（%s）" % (
            self.graph.events[first].get("name") or first,
            self.graph.events[second].get("name") or second, relation)

    def _forget(self, payload: dict) -> str:
        """删掉一件记忆（连同它的连接）。先 recall 拿 id，别凭印象删。"""
        reference = str(payload.get("event") or payload.get("name") or "").strip()
        if not reference:
            return "forget 需要 event（id 或名字）。先用 recall 找。"
        event_id = self.graph.resolve(reference)
        if not event_id:
            return "没有这一件：%s（用 recall 找找看）" % reference
        name = str(self.graph.events[event_id].get("name") or event_id)
        self.graph.remove_event(event_id)
        self.graph.save()
        return "忘了：%s（%s）——这件和它的连接都删了" % (name, event_id)

    def _rename(self, payload: dict) -> str:
        """给一件记忆改名；重名会被拒（否则按名字就分不清是哪件）。"""
        reference = str(payload.get("event") or "").strip()
        new_name = str(payload.get("name") or "").strip()
        if not reference or not new_name:
            return "rename 需要 event（id 或旧名字）和 name（新名字）。"
        event_id = self.graph.resolve(reference)
        if not event_id:
            return "没有这一件：%s（用 recall 找找看）" % reference
        clash = [other for other, event in self.graph.events.items()
                 if other != event_id and str(event.get("name") or "") == new_name]
        if clash:
            return ("已经有同名的一件（%s）。换个名字，或者先 forget 掉那一件。"
                    % clash[0])
        old_name = str(self.graph.events[event_id].get("name") or event_id)
        self.graph.events[event_id]["name"] = new_name
        self.graph.events[event_id]["updated"] = now().replace(" ", "T")
        self.graph.save()
        return "改名了：%s → %s（%s）" % (old_name, new_name, event_id)

    # ── 归档 ──────────────────────────────────────────────────────────────
    def archive_expired(self) -> int:
        """到期的短期记忆压成一行搬进 archive.jsonl，图里删掉；返回几件。"""
        if not self.enabled:
            return 0
        return archive_expired(self.graph, compress=self.archiver,
                               data_dir=self.data_dir)

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
        if self.on_recall is not None:
            try:
                self.on_recall(" ".join(keywords),
                               [str(item["id"]) for item in items])
            except Exception:
                pass
        if not items:
            return "没有找到。换个关键词再试。"
        lines = ["找到 %d 件：" % len(items)]
        if not embedded:
            lines.append("（嵌入服务不可用，只按词面找）")
        for item in items[:5]:
            lines.append("%s %s · %s%s" % (
                item["id"], item.get("name") or "", item.get("date") or "",
                _size_hint(self.graph, item["id"])))
        lines.append("看具体用 recollect；连关系用 link。")
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
