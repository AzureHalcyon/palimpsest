"""One conversation turn: event, then recall, then reply; every turn is recorded."""

from __future__ import annotations

import os
import time
from typing import Callable, Dict, List, Optional

from ..core.config import ChatConfig
from ..core.text import idf, match_score, terms
from ..read.activate import activate
from ..read.search import event_segments
from ..read.write import write_memory
from ..store.frozen import FrozenSession, global_dir
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
        self.messages: List[Dict] = [dict(message) for message in system_messages]
        self.max_tool_rounds = max_tool_rounds
        self.echo = echo or (lambda _text: None)
        self.turn = 0
        self.last_event_id: Optional[str] = None
        self.stack: List[str] = []
        self.activation: Dict[str, float] = {}
        self._cue_terms = set()
        self._cue_keywords: List[str] = []
        self._nudged = False
        self._remembered = 0
        self._pending_event: Optional[str] = None
        self._turn_created: set = set()
        self._last_candidates: set = set()
        self._remembered_ids: set = set()

        # 本场在途只读“本场开始前”的冻结副本；live 图照常维护。
        # 续聊恢复本场自己的 frozen + 完整 messages，做到和第一次完全一致。
        turns = store.turns()
        self.frozen = FrozenSession(self.data_dir, self.session_id)
        if (not turns and self.graph.events
                and not os.path.exists(os.path.join(global_dir(self.data_dir), "graph.json"))):
            self.echo("[冻结] 全局冻结不存在；先运行 freeze，本场 recall 为空")
        if turns:
            self.turn = max(int(turn.get("turn") or 0) for turn in turns)
            last = turns[-1]
            stack = [event_id for event_id in (last.get("stack") or [])
                     if event_id in graph.events]
            if not stack and last.get("event_id") in graph.events:
                stack = [str(last["event_id"])]
            self.stack = stack
            if not self.frozen.exists():
                self.echo("[冻结] 没有本场起始副本，退回最新全局冻结")
                self.frozen.clone_global()
            stored = self.store.load_messages()
            if stored:
                self.messages = stored
            else:
                self.echo("[续聊] 没有 messages 日志，只恢复 user/assistant")
                self.messages = [dict(message) for message in system_messages]
                for turn in turns:
                    self.messages.append({"role": "user",
                                          "content": str(turn.get("user") or "")})
                    self.messages.append({"role": "assistant",
                                          "content": str(turn.get("assistant") or "")})
        else:
            self.frozen.clone_global()
        self.frozen_graph, self.frozen_vectors = self.frozen.load(
            self.config.embed_model, self.config.embed_dim)

    def send(self, text: str) -> str:
        if self.store.is_archived():
            raise SessionSealed("这一场已归档（封口），只读：%s" % self.store.path)
        self.turn += 1
        self._nudged = False
        self._remembered = 0
        self._pending_event = None
        self._turn_created = set()
        self._last_candidates = set()
        self._remembered_ids = set()
        self.messages.append({"role": "user", "content": text})

        calls: List[Dict] = []
        recalled: List[Dict] = []
        remembered: List[Dict] = []
        tool_order: List[str] = []
        reply = ""
        rounds = 0

        while True:
            message = self.client.chat(self.messages, tools=tools.TOOLS)
            tool_calls = message.get("tool_calls") or []
            content = str(message.get("content") or "")

            if not tool_calls:
                missing = []
                if not calls:
                    missing.append("event")
                if not recalled:
                    missing.append("recall")
                if missing and not self._nudged:
                    self._nudged = True
                    self.messages.append({
                        "role": "system",
                        "content": "这一轮还差：%s。先调完再回复。" % "、".join(missing),
                    })
                    continue
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
                        calls.append(record)
                    elif name == "recall":
                        recalled.append(record)
                    elif name == "remember":
                        remembered.append(record)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": result,
                })

            rounds += 1
            if rounds > self.max_tool_rounds:
                reply = content
                self.messages.append({"role": "assistant", "content": reply})
                break

        if self._pending_event:
            self.graph.attach(self._pending_event, self.session_id, self.turn)
        keep = set(self.stack)
        if self._pending_event:
            keep.add(self._pending_event)
            frontier = [self._pending_event]
            seen = set()
            while frontier:
                node = frontier.pop()
                if node in seen:
                    continue
                seen.add(node)
                for edge in self.graph.edges.values():
                    if (edge.get("relation") == "属于" and edge.get("a") == node
                            and edge.get("b") in self.graph.events):
                        keep.add(edge.get("b"))
                        frontier.append(edge.get("b"))
        for event_id in list(self._turn_created):
            if event_id not in keep:
                self.graph.remove_event(event_id)
        if self._pending_event or self._turn_created:
            self.graph.save()

        last = calls[-1] if calls else None
        current_event = self.graph.events.get(self._pending_event or "") or {}
        self.store.append({
            "turn": self.turn,
            "ts": _now(),
            "user": text,
            "assistant": reply,
            "event": current_event.get("name") if self._pending_event else None,
            "event_id": self._pending_event,
            "mode": last["mode"] if last else None,
            "stack": list(self.stack),
            "tool_order": tool_order,
            "calls": calls,
            "recall": recalled,
            "remember": remembered,
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
        if name == "remember":
            return self._handle_remember(arguments)
        return "unknown tool: %s" % name, None

    def _current(self) -> Optional[str]:
        return self.stack[-1] if self.stack else None

    def _resolve_in_stack(self, reference: str) -> Optional[str]:
        reference = str(reference or "").strip()
        if not reference:
            return None
        for event_id in reversed(self.stack):
            event = self.graph.events.get(event_id) or {}
            if reference == event_id or reference == event.get("name"):
                return event_id
        return None

    def _resolve_created(self, reference: str) -> Optional[str]:
        reference = str(reference or "").strip()
        if not reference:
            return None
        for event_id in self._turn_created:
            event = self.graph.events.get(event_id) or {}
            if reference == event_id or reference == event.get("name"):
                return event_id
        return None

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

    def _state_view(self) -> str:
        current = self._current()
        if not current:
            return "状态：还没有一件事。"
        event = self.graph.events.get(current) or {}
        path = " > ".join(str(self.graph.events.get(item, {}).get("name") or item)
                          for item in self.stack)
        parents, children, related = [], [], []
        for edge in self.graph.edges.values():
            relation = edge.get("relation")
            if relation == "属于":
                if edge.get("a") == current and edge.get("b") in self.graph.events:
                    parents.append(edge.get("b"))
                elif edge.get("b") == current and edge.get("a") in self.graph.events:
                    children.append(edge.get("a"))
            elif relation == "相关":
                if edge.get("a") == current and edge.get("b") in self.graph.events:
                    related.append(edge.get("b"))
                elif edge.get("b") == current and edge.get("a") in self.graph.events:
                    related.append(edge.get("a"))

        def names(ids):
            items = sorted(set(ids))
            text = "、".join("%s %s" % (item, self.graph.events.get(item, {}).get("name"))
                            for item in items[:6])
            if len(items) > 6:
                text += " 等 %d 件" % len(items)
            return text

        lines = ["状态：%s %s" % (current, event.get("name")), "路径：%s" % path]
        if parents:
            lines.append("属于：" + names(parents))
        if children:
            lines.append("子件：" + names(children))
        if related:
            lines.append("相关：" + names(related))
        if not (parents or children or related):
            lines.append("联系：暂无")
        return "\n".join(lines)

    def _stack_for(self, event_id: str) -> List[str]:
        chain = [event_id]
        seen = {event_id}
        while len(chain) < 6:
            parents = [(float(edge.get("strength", 0.0)),
                        self.graph.event_order(edge.get("b")), edge.get("b"))
                       for edge in self.graph.edges.values()
                       if edge.get("relation") == "属于" and edge.get("a") == chain[-1]
                       and edge.get("b") in self.graph.events and edge.get("b") not in seen]
            if not parents:
                break
            parent = max(parents)[2]
            chain.append(parent)
            seen.add(parent)
        return list(reversed(chain))

    def _handle_event(self, arguments: str):
        try:
            parsed = tools.parse_event_call(arguments)
        except tools.ToolCallError as exc:
            return "not recorded: %s" % exc, None

        current = self._current()
        parent_ids: List[str] = []
        mode = parsed.mode
        if mode == "自动":
            previous = self.graph.events.get(current or "")
            mode = "继续" if previous and previous.get("name") == parsed.name else "换事"

        if mode in ("换事", "子事") and parsed.name:
            taken = {event_id for event_id in self._turn_created
                     if self.graph.events.get(event_id, {}).get("name") == parsed.name}
            taken |= {event_id for event_id in self.stack
                      if self.graph.events.get(event_id, {}).get("name") == parsed.name}
            if taken:
                return ("这个名字已经在当前这条线上：%s；同一件用继续或接旧。"
                        % "、".join(sorted(taken))), None

        if mode == "继续":
            if not current:
                return "现在还没有一件事；先说这是哪件（换事 + name）。", None
            event_id = current
        elif mode == "换事":
            event_id = self.graph.add_event(parsed.name)
            self._turn_created.add(event_id)
            self.stack = [event_id]
        elif mode == "子事":
            if current and parsed.name == self.graph.events.get(current, {}).get("name"):
                return "当前这件已经叫这个名字；用继续，或换个名字。", None
            event_id = self.graph.add_event(parsed.name)
            self._turn_created.add(event_id)
            if parsed.parent:
                parent = (self._resolve_in_stack(parsed.parent)
                          or self._resolve_created(parsed.parent)
                          or self.graph.resolve(parsed.parent))
                if not parent:
                    return "没有这一件：%s。" % parsed.parent, None
                base = self._stack_for(parent)
            elif current:
                parent = current
                base = list(self.stack)
            else:
                parent = None
                base = []
            if parent:
                self._turn_created.discard(parent)
                self.graph.add_edge(event_id, parent, "属于")
                parent_ids.append(parent)
            for extra in parsed.parents:
                target = self._resolve_in_stack(extra) or self.graph.resolve(extra)
                if target and target != event_id and target not in parent_ids:
                    self._turn_created.discard(target)
                    self.graph.add_edge(event_id, target, "属于")
                    parent_ids.append(target)
            self.stack = base + [event_id]
        elif mode == "回":
            if len(self.stack) < 2:
                return "已经在最外层，没有上一层。", None
            self.stack.pop()
            event_id = self.stack[-1]
        elif mode == "关联":
            if not current:
                return "现在还没有一件事；先换事或接旧。", None
            target = self.graph.resolve(parsed.event)
            if not target:
                return "没有这一件：%s。" % parsed.event, None
            if target != current:
                self._turn_created.discard(target)
                self.graph.add_edge(current, target, "相关")
            self._pending_event = current
            self.last_event_id = current
            name = str(self.graph.events[current].get("name") or "")
            self.echo("[事件] %s %s (关联 %s)" % (current, name, target))
            return "关联：%s %s — %s %s\n%s" % (
                current, name, target,
                self.graph.events.get(target, {}).get("name", ""),
                self._state_view()), {
                "id": current,
                "name": name,
                "mode": "关联",
                "related": target,
                "because": parsed.because,
                "stack": list(self.stack),
                "parents": [],
            }
        else:
            event_id = (self._resolve_in_stack(parsed.event)
                        or self._resolve_created(parsed.event))
            if not event_id:
                if parsed.event in self.graph.events:
                    event_id = parsed.event
                else:
                    matches = [other for other, item in self.graph.events.items()
                               if item.get("name") == parsed.event]
                    if len(matches) > 1:
                        return ("有多个同名：%s；用 id 指。"
                                % "、".join(sorted(matches))), None
                    event_id = matches[0] if matches else None
            if not event_id:
                return "没有这一件：%s。" % parsed.event, None
            self._turn_created.discard(event_id)
            self.stack = self._stack_for(event_id)

        self._pending_event = event_id
        self.last_event_id = event_id
        for link in parsed.links:
            target = self.graph.resolve(link["event"])
            if target and target != event_id:
                self.graph.add_edge(event_id, target, "相关")

        name = str(self.graph.events[event_id].get("name") or parsed.name)
        self.echo("[事件] %s %s (%s)" % (event_id, name, mode))
        lines = ["记下了：%s %s（%s）" % (event_id, name, mode), self._state_view()]
        if len(self.stack) > 1:
            lines.append("可以回。")
        if mode in ("换事", "子事") and parsed.name:
            same = [other for other, item in self.graph.events.items()
                    if other != event_id and item.get("name") == parsed.name]
            if same:
                lines.append("已有同名：%s；同一件用接旧，不同件换名字。" % "、".join(sorted(same)))
        if (mode == "接旧" and event_id not in self._last_candidates
                and event_id not in self._remembered_ids):
            lines.append("这件不在这一轮的候选里。")
        return "\n".join(lines), {
            "id": event_id,
            "name": name,
            "mode": mode,
            "links": parsed.links,
            "because": parsed.because,
            "parents": parent_ids,
            "stack": list(self.stack),
        }

    def _handle_recall(self, arguments: str):
        try:
            parsed = tools.parse_recall_call(arguments)
        except tools.ToolCallError as exc:
            return "not searched: %s" % exc, None

        keywords = parsed["keywords"]
        excluded = set()
        for reference in parsed.get("exclude") or []:
            target = self.graph.resolve(reference)
            if target:
                excluded.add(target)
        result = activate(self.frozen_graph, self.data_dir, keywords,
                          embedder=self.embedder, exclude=excluded,
                          after=parsed["after"], before=parsed["before"],
                          vector_store=self.frozen_vectors)
        self.activation = dict(result["activation"])
        self._cue_terms = set(result.get("query_terms") or set())
        self._cue_keywords = list(keywords)
        self._last_candidates = {item["id"] for item in result["candidates"]}
        record = {"keywords": keywords, "after": parsed["after"], "before": parsed["before"],
                  "exclude": sorted(excluded), "session": self.session_id,
                  "winner": result["winner"], "candidates": result["candidates"],
                  "has_seed": result["has_seed"]}

        if not result["has_seed"]:
            if result["near"]:
                lines = ["没有把握。接近的有："]
                for item in result["near"]:
                    lines.append("%s %s %s" % (item["id"], item["name"], item["date"]))
                lines.append("如果都不是，就按新的说。")
                lines.append(self._state_view())
                return "\n".join(lines), record
            return "没有找到相关的事。\n" + self._state_view(), record

        winner = result["winner"]
        evidence = "、".join(winner["hits"]) if winner["hits"] else "语义近"
        lines = ["最像：%s %s %s 命中：%s" % (
            winner["id"], winner["name"], winner["date"], evidence)]
        backups = result["candidates"][1:]
        if backups:
            lines.append("备选：" + "；".join(
                "%s %s %s" % (item["id"], item["name"], item["date"]) for item in backups))
        lines.append("要取回就用 remember %s。" % winner["id"])
        lines.append(self._state_view())
        return "\n".join(lines), record

    def _handle_remember(self, arguments: str):
        try:
            reference = tools.parse_remember_call(arguments)
        except tools.ToolCallError as exc:
            return "not remembered: %s" % exc, None

        event_id = self.graph.resolve(reference)
        if not event_id:
            return "没有这一件。", None
        self._remembered_ids.add(event_id)
        if self._remembered >= 2:
            return "这一轮已经取回两次了，够了。", None
        segments = self._select_segments(event_segments(self.graph, self.data_dir, event_id))
        if not segments:
            return "这一件没有原文。", None

        self._remembered += 1
        memory = write_memory(self.client, self._annotate(segments))
        WriterArchive(self.data_dir).append(event_id, self.session_id,
                                            self._cue_keywords, memory)
        event = self.graph.events[event_id]
        event["access"] = min(1.0, float(event.get("access", 0)) + 0.1)
        event["access_ts"] = time.time()
        current = self._current()
        if current and current != event_id:
            edge = self.graph.add_edge(current, event_id, "相关")
            edge["strength"] = min(1.0, float(edge.get("strength", 0)) + 0.03)
            edge["support"] = int(edge.get("support", 0)) + 1
        self.graph.save()
        self.echo("[回忆] %s" % memory)
        return memory + "\n\n" + self._state_view(), {"id": event_id}
