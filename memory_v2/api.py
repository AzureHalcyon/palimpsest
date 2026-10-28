"""记忆的动词层：六个动作，一条落盘规矩。

工具面（现在在 `loop/memory_bridge.py`）只负责两件事——把模型给的 JSON 翻成这里的
参数、把返回的结果翻成一句中文。**判断都在这里**：算不算重复、名字像不像 id、
到期没有、能不能连、重名怎么办。以前这些散在工具面里，措辞一改策略就跟着飘，
自检也只能去匹配中文句子。

每个动词返回一个结果字典：

    {"ok": bool, "reason": <码>, "data": {...}}

`reason` 是**稳定的码**，不是给人看的句子：自检断它，措辞留在工具面。码表见
`REASONS`，加码就往里登记，别在调用处现编字符串。

落盘纪律（`graph.save()`）也只在这里：动词改完图自己存。工具面不该再碰 `graph`，
否则「谁负责落盘」就说不清了。
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from .core.embed import EmbedError
from .read.activate import activate
from .read.archive import archive_expired as _archive_expired
from .read.search import event_segments
from .store.frozen import global_dir
from .store.graph import Graph

# 原因码：每加一个就在这里写清「什么情况下给这个码」。工具面靠它决定怎么说，
# 自检靠它断言行为——两边都不该去匹配措辞。
REASONS: Dict[str, str] = {
    "ok": "成了",
    "created": "新建了一件",
    "updated": "改了已有那件",
    "memory_off": "记忆没接上（没有数据目录或显式关掉）",
    "empty_note": "remember 没给 note",
    "name_looks_like_id": "name 填成了事件 id 的形状",
    "name_required": "新开一件却没给 name",
    "event_not_found": "按 id 或名字都找不到这一件",
    "duplicate_note": "note 和这件里已经记的一字不差",
    "need_two_sides": "link 少给了一头",
    "same_event": "link 的两头是同一件",
    "already_linked": "这两件已经连着",
    "need_event": "没给 event",
    "need_event_and_name": "rename 少给 event 或 name",
    "name_taken": "rename 的新名字和别的一件重了",
    "need_keywords": "recall 没给关键词",
    "no_index": "嵌入不可用且词面也关着，查不了",
    "embed_failed": "嵌入调用失败且词面关着，查不了",
    "not_found": "一条都没找到",
    "no_material": "这一件没有任何材料可写",
    "no_writer": "编写模型还没接上",
    "writer_failed": "编写时抛了异常",
    "bad_json": "工具参数不是 JSON 对象",
    "unknown_tool": "没有这个工具",
}


def ok(reason: str = "ok", **data) -> dict:
    return {"ok": True, "reason": reason, "data": data}


def fail(reason: str, **data) -> dict:
    return {"ok": False, "reason": reason, "data": data}


def _now() -> str:
    """和 `loop.context.now()` 同一个格式；引擎不认识 loop，自己算。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def looks_like_id(name: str) -> bool:
    """`e-` 加一串十六进制：那是事件 id，不是名字（真发生过：id 当名字建了个空壳）。"""
    body = str(name or "").strip()
    if not body.startswith("e-"):
        return False
    rest = body[2:]
    return len(rest) >= 6 and all(char in "0123456789abcdef" for char in rest.lower())


def head(text: str, limit: int = 28) -> str:
    """一句话的头：回话里带一下，才看得出写进去的是不是想写的那条。"""
    line = " ".join(str(text or "").split())
    return line[:limit] + ("…" if len(line) > limit else "")


class Memory:
    """一层记忆介质：图在磁盘上，动词在这里。

    没有 `data_dir` 就是关着（`enabled=False`）：动词照样能调，一律回 `memory_off`，
    调用方不必到处判空。
    """

    def __init__(self, data_dir: str = "",
                 writer: Optional[Callable[[str], str]] = None,
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
    def disabled(cls) -> "Memory":
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

    def tools(self) -> list:
        """引擎的记忆**没有**工具面：remember / recall 那些是落地侧的桥。

        循环拼 tool schema 时会问一次；这里给空表，表示"这颗引擎不往外长工具"。
        """
        return []

    # ── 写 ────────────────────────────────────────────────────────────────
    def remember(self, note: str = "", name: str = "", event: str = "",
                 scope: str = "long", days: int = 3) -> dict:
        """把这句 note 记到某一件上：给 event 就改那件，只给 name 就按名字找或新建。"""
        if not self.enabled:
            return fail("memory_off")
        note = str(note or "").strip()
        if not note:
            return fail("empty_note")
        name = str(name or "").strip()
        reference = str(event or "").strip()
        if not reference and looks_like_id(name):
            return fail("name_looks_like_id", name=name)

        event_id = ""
        if reference:
            event_id = self.graph.resolve(reference) or ""
            if not event_id:
                return fail("event_not_found", reference=reference)
        elif name:
            same = [item for item, value in self.graph.events.items()
                    if str(value.get("name") or "") == name]
            if same:
                event_id = max(same, key=self.graph.event_order)

        created = not event_id
        if created:
            if not name:
                return fail("name_required")
            event_id = self.graph.add_event(name)

        scope = str(scope or "long").strip().lower()
        if scope not in ("long", "short"):
            scope = "long"
        expires_at = ""
        if scope == "short":
            try:
                days = int(days or 3)
            except (TypeError, ValueError):
                days = 3
            expires_at = (datetime.now() + timedelta(days=max(0, days))).strftime(
                "%Y-%m-%dT%H:%M:%S")

        # 一件事只有一条笔记：再记就是改这条，不是另加一条。以前是追加，结果同一件
        # 事攒了三条几乎一样的笔记，她自己都分不清哪条算数。
        event = self.graph.events[event_id]
        notes = event.get("notes") or []
        stamp = _now().replace(" ", "T")
        before = head(str((notes[0].get("text") if notes else "") or ""))
        if before and str(notes[0].get("text") or "") == note:
            return fail("duplicate_note", event_id=event_id, before=before,
                        name=str(event.get("name") or event_id))

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
        event["step"] = int(self.step or 0)
        self.graph.save()

        return ok("created" if created else "updated", event_id=event_id,
                  name=str(event.get("name") or event_id),
                  scope=str(event.get("scope") or "long"),
                  chars=len(note), created=created, before=before)

    def link(self, a: str = "", b: str = "", relation: str = "") -> dict:
        """把两件事连起来，不动任何一边的内容。"""
        if not self.enabled:
            return fail("memory_off")
        left = str(a or "").strip()
        right = str(b or "").strip()
        if not left or not right:
            return fail("need_two_sides")
        first = self.graph.resolve(left)
        second = self.graph.resolve(right)
        missing = [name for name, event_id in ((left, first), (right, second))
                   if not event_id]
        if missing:
            return fail("event_not_found", missing=missing)
        if first == second:
            return fail("same_event", event_id=first)
        for edge in self.graph.edges.values():
            if {edge.get("a"), edge.get("b")} == {first, second}:
                return fail("already_linked", first=first, second=second)
        relation = str(relation or "").strip() or "关联"
        self.graph.add_edge(first, second, relation)
        self.graph.save()
        return ok(first=first, second=second, relation=relation,
                  first_name=str(self.graph.events[first].get("name") or first),
                  second_name=str(self.graph.events[second].get("name") or second))

    def forget(self, event: str = "") -> dict:
        """删掉一件记忆（连同它的连接）。"""
        if not self.enabled:
            return fail("memory_off")
        reference = str(event or "").strip()
        if not reference:
            return fail("need_event")
        event_id = self.graph.resolve(reference)
        if not event_id:
            return fail("event_not_found", reference=reference)
        name = str(self.graph.events[event_id].get("name") or event_id)
        self.graph.remove_event(event_id)
        self.graph.save()
        return ok(event_id=event_id, name=name)

    def rename(self, event: str = "", name: str = "") -> dict:
        """给一件记忆改名；重名会被拒（否则按名字就分不清是哪件）。"""
        if not self.enabled:
            return fail("memory_off")
        reference = str(event or "").strip()
        new_name = str(name or "").strip()
        if not reference or not new_name:
            return fail("need_event_and_name")
        event_id = self.graph.resolve(reference)
        if not event_id:
            return fail("event_not_found", reference=reference)
        clash = [other for other, value in self.graph.events.items()
                 if other != event_id and str(value.get("name") or "") == new_name]
        if clash:
            return fail("name_taken", clash=clash[0])
        old_name = str(self.graph.events[event_id].get("name") or event_id)
        self.graph.events[event_id]["name"] = new_name
        self.graph.events[event_id]["updated"] = _now().replace(" ", "T")
        self.graph.save()
        return ok(event_id=event_id, old_name=old_name, new_name=new_name)

    # ── 归档 ──────────────────────────────────────────────────────────────
    def archive_expired(self) -> int:
        """到期的短期记忆压成一行搬进 archive.jsonl，图里删掉；返回几件。"""
        if not self.enabled:
            return 0
        return _archive_expired(self.graph, compress=self.archiver,
                                data_dir=self.data_dir)

    # ── 读 ────────────────────────────────────────────────────────────────
    def recall(self, keywords=None, exclude=None, after: str = "",
               before: str = "", limit: int = 5) -> dict:
        """按关键词联想：只给指针（id / 名字 / 日期 / 字数），正文另取。"""
        if not self.enabled:
            return fail("memory_off")
        words = [str(item).strip() for item in (keywords or []) if str(item).strip()]
        if not words:
            return fail("need_keywords")
        skip = [str(item).strip() for item in (exclude or []) if str(item).strip()]
        after = str(after or "").strip()
        before = str(before or "").strip()
        embedded = self.embedder is not None and getattr(self.embedder, "available", False)
        if not embedded and not self.lexical:
            return fail("no_index")
        try:
            result = activate(self.graph, self.data_dir, words,
                              embedder=self.embedder if embedded else None,
                              exclude=skip, after=after, before=before,
                              limit=limit, lexical=self.lexical,
                              recent_steps=self.recent_steps,
                              current_step=self.step)
        except EmbedError:
            if not self.lexical:
                return fail("embed_failed")
            embedded = False
            result = activate(self.graph, self.data_dir, words, embedder=None,
                              exclude=skip, after=after, before=before,
                              limit=limit, lexical=True,
                              recent_steps=self.recent_steps,
                              current_step=self.step)
        self.activation = dict(result["activation"])
        self.candidates = [item["id"] for item in result["candidates"]]
        items = list(result["candidates"] or [])
        if not items:
            items = list(result.get("near") or [])
        if self.on_recall is not None:
            try:
                self.on_recall(" ".join(words), [str(item["id"]) for item in items])
            except Exception:
                pass
        if not items:
            return fail("not_found", embedded=embedded)
        return ok(embedded=embedded, items=[
            {"id": str(item["id"]),
             "name": str(item.get("name") or ""),
             "date": str(item.get("date") or ""),
             "chars": self._note_chars(str(item["id"]))}
            for item in items])

    def recollect(self, event: str = "") -> dict:
        """把一件写成一段第一人称回忆：只有这段回忆进上下文。"""
        if not self.enabled:
            return fail("memory_off")
        reference = str(event or "").strip()
        event_id = self.graph.resolve(reference)
        if not event_id:
            return fail("event_not_found", reference=reference)
        value = self.graph.events[event_id]
        notes = value.get("notes") or []
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
            return fail("no_material", event_id=event_id)
        if self.writer is None:
            return fail("no_writer", event_id=event_id)
        try:
            recollection = self.writer(material)
        except Exception as exc:
            return fail("writer_failed", error=exc, event_id=event_id)
        value["access"] = min(1.0, float(value.get("access", 0.0)) + 0.1)
        value["access_ts"] = time.time()
        self.graph.save()
        return ok(event_id=event_id, text=recollection)

    # ── 小工具 ────────────────────────────────────────────────────────────
    def _note_chars(self, event_id: str) -> int:
        """这一件有多少字：recall 只给指针，字数帮着判断要不要深挖。"""
        notes = (self.graph.events.get(event_id) or {}).get("notes") or []
        return sum(len(str(note.get("text") or "")) for note in notes)
