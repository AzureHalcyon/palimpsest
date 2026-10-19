"""Event graph: events, edges, candidate pairs, strengths."""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import Dict, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


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
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        self.events = dict(data.get("events") or {})
        self.edges = dict(data.get("edges") or {})
        for edge in self.edges.values():
            edge.setdefault("source", "model")
        self.pairs = dict(data.get("pairs") or {})

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

    def add_event(self, name: str) -> str:
        event_id = self.new_event_id()
        self.events[event_id] = {
            "name": name,
            "seq": self.next_seq(),
            "created": _now(),
            "segments": [],
            "consolidation": 0.0,
            "access": 0.0,
            "access_ts": 0.0,
            "reward": False,
        }
        return event_id

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
        if (segments and segments[-1].get("session") == session
                and int(segments[-1].get("end", 0)) + 1 == int(turn)):
            segments[-1]["end"] = int(turn)
        else:
            segments.append({"session": session, "start": int(turn), "end": int(turn)})
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
