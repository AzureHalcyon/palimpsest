"""Rebuild source events, segments and model-declared edges from recorded turns.

This is recovery and consistency checking, not a snapshot. It does not carry
replay-derived edges/strength/support, consolidation/access, vectors, or
说法/ writings. For a full copy of a data directory use store.snapshot
(.psnap single file, restored with snapshot.restore).
"""

from __future__ import annotations

import os
import time

from .graph import Graph
from .session import SessionStore


def _backup_path(path: str) -> str:
    base = path + ".rebuilt-" + time.strftime("%Y%m%d-%H%M%S")
    target = base
    suffix = 1
    while os.path.exists(target):
        target = "%s-%d" % (base, suffix)
        suffix += 1
    return target


def ingest(graph: Graph, data_dir: str, rebuild: bool = False) -> dict:
    directory = os.path.join(data_dir, "状态")
    if not os.path.isdir(directory):
        return {"sessions": 0, "events": 0, "backup": ""}

    backup = ""
    if rebuild:
        if os.path.exists(graph.path):
            backup = _backup_path(graph.path)
            os.replace(graph.path, backup)
        graph.reset()

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
            id_map[old] = graph.add_event(str(name), event_id=old)

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
                if isinstance(link, dict):
                    reference = str(link.get("event") or link.get("id") or "")
                else:
                    reference = str(link or "")
                target = id_map.get(reference) or graph.resolve(reference)
                if target:
                    graph.add_edge(event_id, target, "相关")
        for item in turn.get("recollect") or turn.get("remember") or []:
            if isinstance(item, dict):
                reference = str(item.get("id") or item.get("event") or "")
            else:
                reference = str(item or "")
            target = id_map.get(reference) or graph.resolve(reference)
            if target and target != event_id:
                graph.add_edge(event_id, target, "相关")

    graph.save()
    return {"sessions": len(filenames), "events": len(graph.events) - before,
            "backup": backup}
