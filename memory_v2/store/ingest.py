"""Rebuild the graph from recorded turns."""

from __future__ import annotations

import os

from .graph import Graph
from .session import SessionStore


def ingest(graph: Graph, data_dir: str) -> dict:
    directory = os.path.join(data_dir, "状态")
    if not os.path.isdir(directory):
        return {"sessions": 0, "events": 0}

    before = len(graph.events)
    filenames = sorted(name for name in os.listdir(directory) if name.endswith(".jsonl"))
    records = []
    for filename in filenames:
        session = filename[:-6]
        for turn in SessionStore(data_dir, session).turns():
            records.append((session, turn))

    id_map = {}
    for _session, turn in records:
        old = str(turn.get("event_id") or "")
        name = turn.get("event")
        if old and name and old not in id_map:
            id_map[old] = graph.add_event(str(name))

    for session, turn in records:
        old = str(turn.get("event_id") or "")
        event_id = id_map.get(old)
        if not event_id:
            continue
        graph.attach(event_id, session, int(turn.get("turn") or 0))
        for call in turn.get("calls") or []:
            for parent in call.get("parents") or []:
                target = id_map.get(str(parent)) or graph.resolve(str(parent))
                if target:
                    graph.add_edge(event_id, target, "属于")
            related = call.get("related")
            if related:
                target = id_map.get(str(related)) or graph.resolve(str(related))
                if target:
                    graph.add_edge(event_id, target, "相关")
            for link in call.get("links") or []:
                reference = str(link.get("event") or "")
                target = id_map.get(reference) or graph.resolve(reference)
                if target:
                    graph.add_edge(event_id, target, "相关")

    graph.save()
    return {"sessions": len(filenames), "events": len(graph.events) - before}
