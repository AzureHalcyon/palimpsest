"""Event graph: events, edges, candidate pairs, strengths."""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import Dict, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class GraphError(RuntimeError):
    """graph.json cannot be read; the caller must stop instead of continuing empty."""


def edge_key(first: str, second: str, relation: str) -> str:
    left, right = sorted((first, second))
    return "%s|%s|%s" % (left, right, relation)


def event_number(event_id: str) -> int:
    digits = str(event_id)[2:]
    return int(digits) if digits.isdigit() else -1


class Graph:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.events: Dict[str, dict] = {}
        self.edges: Dict[str, dict] = {}
        self.pairs: Dict[str, dict] = {}
        self.load()

    @classmethod
    def open(cls, data_dir: str) -> "Graph":
        return cls(os.path.join(data_dir, "graph.json"))

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self._quarantine_corrupt(exc)
        if not isinstance(data, dict):
            self._quarantine_corrupt(ValueError("root is not an object"))
        try:
            events = data.get("events") or {}
            edges = data.get("edges") or {}
            pairs = data.get("pairs") or {}
            if not (isinstance(events, dict) and isinstance(edges, dict)
                    and isinstance(pairs, dict)):
                raise ValueError("events/edges/pairs are not objects")
            self.events = dict(events)
            self.edges = dict(edges)
            for edge in self.edges.values():
                edge.setdefault("source", "model")
            self.pairs = dict(pairs)
        except (TypeError, AttributeError, ValueError) as exc:
            self._quarantine_corrupt(exc)

    def _quarantine_corrupt(self, error: Exception) -> None:
        base = self.path + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S")
        target = base
        suffix = 1
        while os.path.exists(target):
            target = "%s-%d" % (base, suffix)
            suffix += 1
        try:
            os.replace(self.path, target)
        except OSError as move_error:
            raise GraphError("graph.json 读不了（%s），隔离到 %s 也失败：%s"
                             % (error, target, move_error)) from error
        raise GraphError("graph.json 读不了（%s），已隔离到 %s；请检查后再重建或修复。"
                         % (error, target)) from error

    def reset(self) -> None:
        """Drop the in-memory graph before a full rebuild from state records."""
        self.events.clear()
        self.edges.clear()
        self.pairs.clear()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {"events": self.events, "edges": self.edges, "pairs": self.pairs}
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def new_event_id(self) -> str:
        while True:
            candidate = "e-" + secrets.token_hex(6)
            if candidate not in self.events:
                return candidate

    def next_seq(self) -> int:
        highest = 0
        for event_id, event in self.events.items():
            highest = max(highest, int(event.get("seq", 0) or 0))
            highest = max(highest, event_number(event_id))
        return highest + 1

    def event_order(self, event_id: str):
        event = self.events.get(event_id) or {}
        seq = int(event.get("seq", 0) or 0) or event_number(event_id)
        return (str(event.get("created") or ""), seq)

    def add_event(self, name: str, event_id: Optional[str] = None) -> str:
        if event_id:
            event_id = str(event_id)
            if event_id in self.events:
                return event_id
        else:
            event_id = self.new_event_id()
        self.events[event_id] = {
            "name": name,
            "seq": self.next_seq(),
            "created": _now(),
            "segments": [],
            "notes": [],
            "scope": "long",
            "expires_at": "",
            "consolidation": 0.0,
            "access": 0.0,
            "access_ts": 0.0,
            "reward": False,
        }
        return event_id

    def add_note(self, event_id: str, text: str, ts: str = "",
                 scope: str = "", expires_at: str = "") -> dict:
        """Append model-written material to an event (no dialogue turn needed)."""
        event = self.events[event_id]
        notes = event.setdefault("notes", [])
        notes.append({"ts": str(ts or _now()), "text": str(text)})
        event["updated"] = _now()
        if scope:
            event["scope"] = str(scope)
        if expires_at:
            event["expires_at"] = str(expires_at)
        return event

    def purge_expired(self, now_iso: str = "") -> int:
        """Drop short-scope events whose expiry has passed."""
        now_iso = str(now_iso or _now())
        expired = [event_id for event_id, event in self.events.items()
                   if str(event.get("scope") or "long") == "short"
                   and str(event.get("expires_at") or "")
                   and str(event["expires_at"]) <= now_iso]
        for event_id in expired:
            self.remove_event(event_id)
        return len(expired)

    def archived_expired(self, now_iso: str = "") -> List[str]:
        """到期待归档的短期事件：过期时间已过。"""
        now_iso = str(now_iso or _now())
        return [event_id for event_id, event in self.events.items()
                if str(event.get("scope") or "long") == "short"
                and str(event.get("expires_at") or "")
                and str(event["expires_at"]) <= now_iso]

    def resolve(self, reference: str) -> Optional[str]:
        reference = (reference or "").strip()
        if not reference:
            return None
        if reference in self.events:
            return reference
        matches = [event_id for event_id, event in self.events.items()
                   if event.get("name") == reference]
        if not matches:
            return None
        return max(matches, key=self.event_order)

    def attach(self, event_id: str, session: str, turn: int) -> None:
        event = self.events[event_id]
        segments = event.setdefault("segments", [])
        turn = int(turn)
        for segment in segments:
            if segment.get("session") != session:
                continue
            start = int(segment.get("start", 0))
            end = int(segment.get("end", 0))
            if start <= turn <= end:
                return
            if end + 1 == turn:
                segment["end"] = turn
                event["updated"] = _now()
                return
            if turn + 1 == start:
                segment["start"] = turn
                event["updated"] = _now()
                return
        segments.append({"session": session, "start": turn, "end": turn})
        event["updated"] = _now()

    def add_edge(self, first: str, second: str, relation: str, strength: float = 0.2,
                 source: str = "model") -> dict:
        key = edge_key(first, second, relation)
        edge = self.edges.get(key)
        if edge is None:
            edge = {"a": first, "b": second, "relation": relation,
                    "strength": strength, "support": 0, "source": source,
                    "updated": _now()}
            self.edges[key] = edge
        return edge

    def neighbors(self, event_id: str) -> List[tuple]:
        out = []
        for edge in self.edges.values():
            if edge.get("a") == event_id:
                out.append((edge.get("b"), edge))
            elif edge.get("b") == event_id:
                out.append((edge.get("a"), edge))
        return out

    def rename_event(self, event_id: str, name: str) -> str:
        """Rename an existing node in place; return the old name."""
        event = self.events.get(event_id)
        if event is None:
            raise KeyError(event_id)
        name = str(name or "").strip()
        if not name:
            raise ValueError("empty name")
        for other_id, other in self.events.items():
            if other_id != event_id and str(other.get("name") or "") == name:
                raise ValueError("name already used by %s" % other_id)
        old = str(event.get("name") or "")
        event["name"] = name
        event["updated"] = _now()
        return old

    def remove_edge(self, first: str, second: str, relation: str = "") -> int:
        """Remove edges between two nodes; return how many were removed."""
        removed = 0
        for key, edge in list(self.edges.items()):
            if {edge.get("a"), edge.get("b")} != {first, second}:
                continue
            if relation and str(edge.get("relation") or "") != relation:
                continue
            del self.edges[key]
            removed += 1
        return removed

    def remove_event(self, event_id: str) -> None:
        self.events.pop(event_id, None)
        for key in [key for key, edge in self.edges.items()
                    if event_id in (edge.get("a"), edge.get("b"))]:
            del self.edges[key]
        for key in [key for key, pair in self.pairs.items()
                    if event_id in (pair.get("a"), pair.get("b"))]:
            del self.pairs[key]

    def events_in_session(self, session: str) -> List[str]:
        return [event_id for event_id, event in self.events.items()
                if any(segment.get("session") == session
                       for segment in event.get("segments") or [])]
