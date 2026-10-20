"""Consolidation: marked seeds, uneven spread, reinforcement, decay, prune."""

from __future__ import annotations

import itertools
import random

from ..core.text import terms
from ..read.activate import event_vectors, _similarity
from ..read.search import event_text
from ..store.frozen import update_global
from ..store.graph import edge_key
from ..store.session import SessionStore

REINFORCE = 0.02
DECAY = 0.87
MODEL_FLOOR = 0.15
PRUNE = 0.12


def _marked_seeds(graph, data_dir: str, session: str) -> list:
    seeds = set(graph.events_in_session(session))
    for turn in SessionStore(data_dir, session).turns():
        for call in turn.get("calls") or []:
            for reference in (call.get("id"), call.get("event")):
                target = graph.resolve(str(reference or ""))
                if target:
                    seeds.add(target)
        for event in turn.get("events") or []:
            target = graph.resolve(str(event.get("id") or ""))
            if target:
                seeds.add(target)
        for item in turn.get("remember") or []:
            target = graph.resolve(str(item.get("id") or ""))
            if target:
                seeds.add(target)
    for event_id, event in graph.events.items():
        if event.get("reward"):
            seeds.add(event_id)
    return sorted(seeds)


def propagate(graph, seed: str, sets: dict, vectors: dict, hops: int = 2,
              content_k: int = 1):
    lit = {seed: 1.0}
    edge_hits = {}
    content_pairs = {}
    frontier = {seed: 1.0}
    for _hop in range(hops):
        nxt = {}
        for node, score in frontier.items():
            for other, edge in graph.neighbors(node):
                weight = 0.85 if edge.get("source") == "model" else 0.6
                gain = score * float(edge.get("strength", 0.2)) * weight
                if gain <= 0.05:
                    continue
                nxt[other] = max(nxt.get(other, 0.0), gain)
                key = edge_key(edge.get("a"), edge.get("b"), edge.get("relation"))
                edge_hits[key] = edge_hits.get(key, 0.0) + gain
            scored = []
            for other in graph.events:
                if other == node:
                    continue
                similarity = _similarity(node, other, sets, vectors)
                if similarity > 0:
                    scored.append((similarity, other))
            scored.sort(reverse=True)
            for similarity, other in scored[:content_k]:
                gain = score * 0.5 * similarity
                if gain <= 0.05:
                    continue
                nxt[other] = max(nxt.get(other, 0.0), gain)
                pair = tuple(sorted((node, other)))
                content_pairs[pair] = content_pairs.get(pair, 0.0) + gain
        for event_id, score in nxt.items():
            lit[event_id] = max(lit.get(event_id, 0.0), score)
        frontier = nxt
    return lit, edge_hits, content_pairs


def consolidate(graph, data_dir: str, session: str, sample: int = 5,
                hops: int = 2, embedder=None) -> dict:
    seeds = _marked_seeds(graph, data_dir, session)
    others = [event_id for event_id in graph.events if event_id not in seeds]
    picker = random.Random(session)
    extra = picker.sample(others, min(sample, len(others))) if others else []
    seeds = seeds + extra

    texts = {event_id: event_text(graph, data_dir, event_id) for event_id in graph.events}
    sets = {event_id: terms(text) | terms(str(graph.events[event_id].get("name") or ""))
            for event_id, text in texts.items()}
    vectors = event_vectors(graph, data_dir, texts, embedder)

    counts = {}
    pair_support = {}
    edge_seeds = {}
    content_total = 0
    for seed in seeds:
        lit, edge_hits, content_pairs = propagate(
            graph, seed, sets, vectors, hops=hops)
        for event_id in lit:
            counts[event_id] = counts.get(event_id, 0) + 1
        for first, second in itertools.combinations(sorted(lit), 2):
            pair_support[(first, second)] = pair_support.get((first, second), 0) + 1
        for key in edge_hits:
            edge_seeds.setdefault(key, set()).add(seed)
        content_total += len(content_pairs)

    reinforced = 0
    for key, seed_set in edge_seeds.items():
        if len(seed_set) < 2:
            continue
        edge = graph.edges.get(key)
        if edge:
            edge["strength"] = min(1.0, float(edge.get("strength", 0.0))
                                   + REINFORCE * len(seed_set))
            edge["support"] = int(edge.get("support", 0)) + len(seed_set)
            reinforced += 1

    for (first, second), support in pair_support.items():
        key = "%s|%s" % (first, second)
        record = graph.pairs.setdefault(key, {"a": first, "b": second, "support": 0})
        record["support"] = float(record.get("support", 0)) + support

    formed = 0
    for key, record in list(graph.pairs.items()):
        if float(record.get("support", 0)) >= 6:
            edge = graph.add_edge(record["a"], record["b"], "相关", source="replay")
            edge["strength"] = min(1.0, float(edge.get("strength", 0.2)))
            edge["support"] = int(edge.get("support", 0)) + int(record["support"])
            record["support"] = 0.0
            formed += 1
        else:
            record["support"] = float(record.get("support", 0)) * 0.9
            if record["support"] < 0.1:
                graph.pairs.pop(key, None)

    pruned = 0
    for key, edge in list(graph.edges.items()):
        if edge.get("source") == "model":
            edge["strength"] = max(MODEL_FLOOR, float(edge.get("strength", 0.0)) * DECAY)
            continue
        edge["strength"] = float(edge.get("strength", 0.0)) * DECAY
        if edge["strength"] < PRUNE:
            del graph.edges[key]
            pruned += 1

    for event_id, count in counts.items():
        event = graph.events[event_id]
        event["consolidation"] = min(1.0, float(event.get("consolidation", 0)) + 0.1 * count)

    graph.save()
    SessionStore(data_dir, session).archive()
    update_global(data_dir)
    return {"seeds": len(seeds), "lit": len(counts), "edges": formed,
            "reinforced": reinforced, "pruned": pruned, "content": content_total}
