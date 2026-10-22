"""Replay evidence tests (no network, no model)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import memory_v2.consolidate.replay as replay
from memory_v2.store.graph import Graph
from memory_v2.store.session import SessionStore


def write_turn(data, session, user, assistant):
    SessionStore(data, session).append({
        "turn": 1, "user": user, "assistant": assistant,
        "ts": "2026-10-21T20:00:00", "calls": [], "events": [],
    })


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    data = tempfile.mkdtemp(prefix="replay-check-")
    try:
        graph = Graph(os.path.join(data, "graph.json"))
        write_turn(data, "s0", "旧事", "旧答")
        old = graph.add_event("旧事件")
        graph.attach(old, "s0", 1)

        # Direct promotion: evidence at the threshold creates one replay edge.
        write_turn(data, "s1", "新事1", "新答1")
        seed = graph.add_event("新事件1")
        graph.attach(seed, "s1", 1)
        graph.events[old]["replay_evidence"] = replay.EVIDENCE_THRESHOLD
        graph.events[old]["replay_pairs"] = {seed: replay.EVIDENCE_THRESHOLD}
        stats = {"edges": 0, "new_edges": 0}
        replay._promote(graph, old, {old: "old", seed: "seed"}, stats)
        promoted = [edge for edge in graph.edges.values()
                    if edge.get("source") == "replay" and edge.get("v") == 3]
        check(len(promoted) == 1, promoted)
        check(promoted[0]["support"] >= replay.EVIDENCE_THRESHOLD, promoted)
        check(promoted[0]["floor"] >= 0.08, promoted)
        check(graph.events[old]["replay_evidence"] == 0, graph.events[old])

        # Full consolidation still runs multiple passes and cannot create
        # structural two-way edges.
        for index in range(2, 7):
            session = "s%d" % index
            write_turn(data, session, "新事%d" % index, "新答%d" % index)
            event_id = graph.add_event("新事件%d" % index)
            graph.attach(event_id, session, 1)
            graph.add_edge(event_id, old, "关联", strength=0.2, source="model")
            result = replay.consolidate(graph, data, session, embedder=None)
            check(result["passes"] == replay.PASSES, result)
            check(not any(edge.get("source") == "window"
                          for edge in graph.edges.values()), "window edge found")

        event = graph.events[old]
        check(float(event.get("replay_evidence", 0)) <= replay.EVIDENCE_CAP, event)
        check(len(event.get("replay_pairs") or {}) <= replay.PAIR_LIMIT, event)
        check(any(old in (edge["a"], edge["b"])
                  and edge.get("source") in ("model", "replay")
                  and float(edge.get("support", 0)) > 0
                  for edge in graph.edges.values()),
              "old event did not gain replay support")
    finally:
        shutil.rmtree(data, ignore_errors=True)

    # 一场归档远远不够：证据不到阈值就不建 replay 边。
    data2 = tempfile.mkdtemp(prefix="replay-early-")
    try:
        graph2 = Graph(os.path.join(data2, "graph.json"))
        write_turn(data2, "s0", "旧事", "旧答")
        old2 = graph2.add_event("旧事件")
        graph2.attach(old2, "s0", 1)
        write_turn(data2, "s1", "新事", "新答")
        seed2 = graph2.add_event("新事件")
        graph2.attach(seed2, "s1", 1)
        graph2.add_edge(seed2, old2, "关联", strength=0.5, source="model")
        graph2.save()
        result = replay.consolidate(graph2, data2, "s1", embedder=None)
        check(result["new_edges"] == 0, result)
        reopened = Graph.open(data2)
        check(float(reopened.events[old2].get("replay_evidence", 0)) < replay.EVIDENCE_THRESHOLD,
              reopened.events[old2])
        check(not any(edge.get("source") == "replay" for edge in reopened.edges.values()),
              "one archive must not create a replay edge")
        check(int(reopened.events[old2].get("archive_hits") or 0) == 1, reopened.events[old2])
    finally:
        shutil.rmtree(data2, ignore_errors=True)

    print("replay check OK")


if __name__ == "__main__":
    main()
