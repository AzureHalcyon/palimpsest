"""One conversation turn: mark the events, then reply.

事件是一张网络：节点是事情，边是关联。没有父/子、没有栈、没有“回”。
一轮可以标多个节点；每个节点要么挂到已有节点，要么新建（可选连到 0..N 个已有节点）。
"""

from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional

from ..core.config import ChatConfig
from ..core.text import idf, match_score, terms
from ..read.activate import activate
from ..read.search import event_date, event_segments
from ..read.write import write_memory
from ..store.session import SessionStore, SessionSealed
from ..store.writings import WriterArchive
from . import tools


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class EventChat:
    def __init__(self, client, store: SessionStore, graph, data_dir: str,
                 session_id: str, system_messages: List[Dict[str, str]],
                 embedder=None, max_tool_rounds: int = 8,
                 echo: Optional[Callable[[str], None]] = None):
        self.client = client
        self.store = store
        if store.is_archived():
            raise SessionSealed("这一场已归档（封口），只读：%s" % store.path)
        self.graph = graph
        self.data_dir = data_dir
        self.session_id = session_id
        self.embedder = embedder
        self.config = getattr(embedder, "config", None) or ChatConfig()
        self.max_tool_rounds = max_tool_rounds
        self.echo = echo or (lambda _text: None)
        self.turn = 0
        self.activation: Dict[str, float] = {}
        self._cue_terms = set()
        self._cue_keywords: List[str] = []
        self._recollected = 0
        self._turn_events: List[str] = []
        self._turn_new: List[str] = []

        self.messages: List[Dict] = [dict(message) for message in system_messages]
        turns = store.turns()
        if turns:
            self.turn = max(int(turn.get("turn") or 0) for turn in turns)
            stored = self.store.load_messages()
            if stored:
                self.messages = stored
            else:
                self.echo("[续聊] 没有 messages 日志，只恢复 user/assistant")
                for turn in turns:
                    self.messages.append({"role": "user",
                                          "content": str(turn.get("user") or "")})
                    self.messages.append({"role": "assistant",
                                          "content": str(turn.get("assistant") or "")})

    def send(self, text: str, on_delta=None) -> str:
        if self.store.is_archived():
            raise SessionSealed("这一场已归档（封口），只读：%s" % self.store.path)
        self.turn += 1
        self._recollected = 0
        self._turn_events = []
        self._turn_new = []
        self._reasoning = ""
        self._usage = {}
        self.messages.append({"role": "user", "content": text})

        calls: List[Dict] = []
        recalled: List[Dict] = []
        recollected: List[Dict] = []
        tool_order: List[str] = []
        reply = ""
        rounds = 0
        nudges = 0
        last_event_ok = False

        try:
            while True:
                message = self.client.chat(self.messages, tools=tools.TOOLS,
                                           on_delta=on_delta)
                tool_calls = message.get("tool_calls") or []
                content = str(message.get("content") or "")
                piece = str(message.get("reasoning_content")
                            or message.get("reasoning") or "")
                if piece:
                    self._reasoning += piece
                usage = getattr(self.client, "last_usage", None) or message.get("usage") or {}
                if isinstance(usage, dict) and usage:
                    self._usage = usage

                if not tool_calls:
                    problems = []
                    if not calls:
                        problems.append("还差 event")
                    elif not last_event_ok:
                        if tool_order and tool_order[-1] == "event":
                            problems.append("event 没有成功")
                        else:
                            problems.append("event 不是最后一个工具调用")
                    if not recalled:
                        problems.append("还差 recall")
                    if problems and nudges < 2:
                        nudges += 1
                        self.messages.append({
                            "role": "system",
                            "content": ("这一轮的要求没满足：%s。顺序固定为 recall →"
                                        "（必要时 recollect）→ event → 正文；"
                                        "event 可以调多次，最后一次之后不要再调工具。"
                                        % "；".join(problems)),
                        })
                        continue
                    if problems:
                        raise RuntimeError("这一轮未记录：%s；event 必须是正文前最后一个"
                                           "成功的工具调用。" % "；".join(problems))
                    reply = content
                    self.messages.append({"role": "assistant", "content": reply})
                    break

                self.messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })

                for call in tool_calls:
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    arguments = str(function.get("arguments") or "")
                    tool_order.append(name)
                    result, record = self._dispatch(name, arguments)
                    if record is not None:
                        if name == "event":
                            if not any(item.get("id") == record.get("id")
                                       for item in calls):
                                calls.append(record)
                        elif name == "recall":
                            recalled.append(record)
                        elif name == "recollect":
                            recollected.append(record)
                    if name == "event":
                        last_event_ok = record is not None
                    else:
                        last_event_ok = False
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": result,
                    })

                rounds += 1
                if rounds > self.max_tool_rounds:
                    raise RuntimeError("工具调用轮数超过上限，本轮未记录")
        except Exception:
            # 这一轮没落盘：新建的节点撤掉，别留下孤儿。
            for event_id in self._turn_new:
                self.graph.remove_event(event_id)
            if self._turn_new:
                self.graph.save()
            raise

        for event_id in self._turn_events:
            self.graph.attach(event_id, self.session_id, self.turn)

        # 只清理这一轮建了、但最终没标上的节点（正常不会发生；防中途出错）
        for event_id in list(self._turn_new):
            if event_id not in self._turn_events:
                self.graph.remove_event(event_id)
        if self._turn_events or self._turn_new:
            self.graph.save()

        events = [{"id": str(item.get("id") or ""),
                   "name": str(item.get("name") or ""),
                   "new": bool(item.get("new")),
                   "links": list(item.get("links") or [])}
                  for item in calls]
        first = events[0] if events else {}
        self.store.append({
            "turn": self.turn,
            "ts": _now(),
            "user": text,
            "assistant": reply,
            "event": first.get("name") or None,
            "event_id": first.get("id") or None,
            "mode": "事件",
            "events": events,
            "tool_order": tool_order,
            "calls": calls,
            "recall": recalled,
            "recollect": recollected,
            "reasoning": self._reasoning,
            "usage": self._usage,
        })
        self.store.save_messages(self.messages)
        return reply

    def _annotate(self, segments: List[dict]) -> str:
        if not self._cue_terms:
            strengths = [1.0] * len(segments)
        else:
            weights = idf([terms(segment["text"]) for segment in segments])
            scores = [match_score(self._cue_terms, terms(segment["text"]), weights)
                      for segment in segments]
            top = max(scores) if scores else 0.0
            strengths = [(0.2 + 0.8 * score / top) if top > 0 else 0.2
                         for score in scores]
        return "\n\n".join("%s · %.2f\n%s" % (segment["date"], strength, segment["text"])
                           for segment, strength in zip(segments, strengths))

    def _dispatch(self, name: str, arguments: str):
        if name == "event":
            return self._handle_event(arguments)
        if name == "recall":
            return self._handle_recall(arguments)
        if name == "recollect":
            return self._handle_recollect(arguments)
        return "unknown tool: %s" % name, None

    def _lookup(self, reference: str):
        reference = str(reference or "").strip()
        if not reference:
            return None, "missing event"
        if reference in self.graph.events:
            return reference, ""
        matches = [event_id for event_id, event in self.graph.events.items()
                   if event.get("name") == reference]
        if len(matches) > 1:
            return None, "有多个同名：%s；用 id。" % "、".join(sorted(matches))
        if not matches:
            return None, "没有这一件：%s。" % reference
        return matches[0], ""

    def _turn_view(self) -> str:
        if not self._turn_events:
            return "这一轮：还没标。"
        pieces = []
        for event_id in self._turn_events:
            event = self.graph.events.get(event_id) or {}
            pieces.append("%s（%s）" % (event.get("name") or event_id, event_id))
        return "这一轮：" + "、".join(pieces)

    def _where(self, event_id: str, event: dict) -> str:
        segments = event.get("segments") or []
        current = [segment for segment in segments
                   if segment.get("session") == self.session_id]
        if current:
            starts = [int(segment.get("start") or 0) for segment in current]
            ends = [int(segment.get("end") or 0) for segment in current]
            start, end = min(starts), max(ends)
            return "本场 第%d轮" % start if start == end else "本场 第%d-%d轮" % (start, end)
        stamp = event_date(self.graph, self.data_dir, event_id)
        return stamp or "旧场"

    def _select_segments(self, segments: List[dict], limit: int = 8,
                         chars: int = 2000) -> List[dict]:
        if len(segments) <= limit:
            return segments
        cue = self._cue_terms
        scored = []
        for index, segment in enumerate(segments):
            text = str(segment.get("text") or "")
            scored.append((len(cue & terms(text)), index))
        scored.sort(reverse=True)
        picked = {index for _overlap, index in scored[:limit]}
        picked.add(len(segments) - 1)
        chosen = []
        total = 0
        for index in sorted(picked):
            text = str(segments[index].get("text") or "")
            if chosen and total + len(text) > chars:
                continue
            chosen.append(segments[index])
            total += len(text)
        return chosen

    def _handle_event(self, arguments: str):
        try:
            parsed = tools.parse_event_call(arguments)
        except tools.ToolCallError as exc:
            return "not recorded: %s" % exc, None

        if parsed.new:
            name = parsed.new
            targets = []
            for reference in parsed.links:
                target, error = self._lookup(reference)
                if error:
                    return error, None
                if target not in targets:
                    targets.append(target)
            same = [event_id for event_id, event in self.graph.events.items()
                    if event.get("name") == name]
            if same:
                return ("已有同名：%s；同一件用 event 挂已有，不同件换个名字。"
                        % "、".join(sorted(same))), None
            event_id = self.graph.add_event(name)
            self._turn_new.append(event_id)
            for target in targets:
                if target != event_id:
                    self.graph.add_edge(event_id, target, "关联")
            if event_id not in self._turn_events:
                self._turn_events.append(event_id)
            lines = ["已标：%s（%s，新）" % (name, event_id)]
            if targets:
                lines.append("连接：" + "、".join(
                    "%s（%s）" % (self.graph.events.get(target, {}).get("name") or target,
                                  target) for target in targets))
            else:
                lines.append("连接：无（孤立节点）")
            lines.append(self._turn_view())
            record = {"id": event_id, "name": name, "new": True, "links": targets,
                      "link_names": [str(self.graph.events.get(target, {}).get("name") or target)
                                     for target in targets]}
            return "\n".join(lines), record

        event_id, error = self._lookup(parsed.event)
        if error:
            return error, None
        targets = []
        for reference in parsed.links:
            target, error = self._lookup(reference)
            if error:
                return error, None
            if target != event_id and target not in targets:
                targets.append(target)
        for target in targets:
            self.graph.add_edge(event_id, target, "关联")
        if event_id not in self._turn_events:
            self._turn_events.append(event_id)
        name = str(self.graph.events[event_id].get("name") or event_id)
        lines = ["已标：%s（%s）" % (name, event_id)]
        if targets:
            lines.append("连接：" + "、".join(
                "%s（%s）" % (self.graph.events.get(target, {}).get("name") or target,
                              target) for target in targets))
        lines.append(self._turn_view())
        record = {"id": event_id, "name": name, "new": False, "links": targets,
                  "link_names": [str(self.graph.events.get(target, {}).get("name") or target)
                                 for target in targets]}
        return "\n".join(lines), record

    def _handle_recall(self, arguments: str):
        try:
            parsed = tools.parse_recall_call(arguments)
        except tools.ToolCallError as exc:
            return "not searched: %s" % exc, None

        keywords = parsed["keywords"]
        excluded = set()
        for reference in parsed.get("exclude") or []:
            target, _error = self._lookup(reference)
            if target:
                excluded.add(target)
        result = activate(self.graph, self.data_dir, keywords,
                          embedder=self.embedder, exclude=excluded,
                          after=parsed["after"], before=parsed["before"])
        self.activation = dict(result["activation"])
        self._cue_terms = set(result.get("query_terms") or set())
        self._cue_keywords = list(keywords)
        items = list(result["candidates"] or [])
        if not items:
            items = list(result.get("near") or [])
        record = {"keywords": keywords, "after": parsed["after"], "before": parsed["before"],
                  "exclude": sorted(excluded), "session": self.session_id,
                  "winner": result["winner"], "candidates": result["candidates"],
                  "has_seed": result["has_seed"]}

        if not items:
            return ("没有找到节点。要新建就 event new；换关键词再查。\n"
                    + self._turn_view()), record

        for item in items:
            event = self.graph.events.get(str(item.get("id") or "")) or {}
            item["current"] = any(
                str(segment.get("session") or "") == self.session_id
                for segment in event.get("segments") or [])
        lines = ["找到 %d 个节点：" % len(items)]
        for item in items[:6]:
            event_id = str(item.get("id") or "")
            event = self.graph.events.get(event_id) or {}
            links = len(self.graph.neighbors(event_id))
            lines.append("%s %s · %s · 连 %d" % (
                event_id, item.get("name") or event.get("name") or "",
                self._where(event_id, event), links))
        lines.append("要连就把 id 写进 event 的 event/links；本场节点不要 recollect。")
        return "\n".join(lines), record

    def _handle_recollect(self, arguments: str):
        try:
            reference = tools.parse_recollect_call(arguments)
        except tools.ToolCallError as exc:
            return "not recollected: %s" % exc, None

        event_id, error = self._lookup(reference)
        if error:
            return "没有这一件。", None
        segments = event_segments(self.graph, self.data_dir, event_id,
                                  skip_session=self.session_id)
        if not segments:
            has_current = any(
                str(segment.get("session") or "") == self.session_id
                for segment in self.graph.events[event_id].get("segments") or [])
            if has_current:
                return ("本场节点不用 recollect；原文就在对话里。"
                        "要连它就用 event 的 event/links。"), None
            return "这一件没有原文。", None
        segments = self._select_segments(segments)
        if not segments:
            return "这一件没有原文。", None

        self._recollected += 1
        memory = write_memory(self.client, self._annotate(segments))
        WriterArchive(self.data_dir).append(event_id, self.session_id,
                                            self._cue_keywords, memory)
        event = self.graph.events[event_id]
        event["access"] = min(1.0, float(event.get("access", 0)) + 0.1)
        event["access_ts"] = time.time()
        self.graph.save()
        self.echo("[回忆] %s" % memory)
        return memory + "\n\n" + self._turn_view(), {"id": event_id}
