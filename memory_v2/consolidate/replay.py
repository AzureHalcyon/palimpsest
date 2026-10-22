"""Consolidation: v2 seed activation plus slow, capped replay evidence.

Seeds are the current session's marked events (plus `reward`), with a few random
old events per pass so old material is not starved.  A replay pass only spreads
from those starts; there is no structural clique between recent events.

Old events accumulate evidence across archives.  One pass adds a small amount;
an old event needs several passes/sessions before replay creates an edge.  Each
old event keeps only the strongest few seeds, and evidence decays, so noise does
not turn into a permanent star.  Edge support raises a floor: repeatedly
activated old links gradually solidify, while one-off links stay weak.

`recollect` is not part of this module and is unchanged by it.
"""

from __future__ import annotations

import hashlib
import random

from ..core.text import terms
from ..read.activate import content_neighbors, event_vectors, _similarity
from ..read.search import event_label
from ..store.frozen import update_global
from ..store.graph import edge_key
from ..store.session import SessionStore
from ..store.vectors import VectorStore


REINFORCE = 0.02
DECAY = 0.87
MODEL_FLOOR = 0.15
PRUNE = 0.12

PASSES = 3
OLD_SAMPLE = 5
EVIDENCE_THRESHOLD = 4.0
EVIDENCE_CAP = 8.0
EVIDENCE_DECAY = 0.95
PAIR_LIMIT = 3
PAIR_DECAY = 0.95
SUPPORT_DECAY = 0.98
FLOOR_PER_SUPPORT = 0.02
FLOOR_CAP = 0.35


def _segments(event: dict) -> frozenset:
    return frozenset(
        (str(segment.get("session") or ""),
         int(segment.get("start", 0) or 0),
         int(segment.get("end", 0) or 0))
        for segment in event.get("segments") or []
    )


def _same_segments(graph, first: str, second: str) -> bool:
    left = _segments(graph.events.get(first) or {})
    right = _segments(graph.events.get(second) or {})
    return bool(left) and left == right


def _in_session(graph, event_id: str, session: str) -> bool:
    return any(str(segment.get("session") or "") == session
               for segment in (graph.events.get(event_id) or {}).get("segments") or [])


def _find_edge(graph, first: str, second: str):
    for edge in graph.edges.values():
        if {edge.get("a"), edge.get("b")} == {first, second}:
            return edge
    return None


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
        for item in turn.get("recollect") or turn.get("remember") or []:
            target = graph.resolve(str(item.get("id") or ""))
            if target:
                seeds.add(target)
    for event_id, event in graph.events.items():
        if event.get("reward"):
            seeds.add(event_id)
    return sorted(seeds)


def propagate(graph, seed: str, sets: dict, vectors: dict, hops: int = 2,
              content_k: int = 1, content: dict = None):
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
            if content is not None:
                candidates = content.get(node, ())[:content_k]
            else:
                scored = []
                for other in graph.events:
                    if other == node:
                        continue
                    similarity = _similarity(node, other, sets, vectors)
                    if similarity > 0:
                        scored.append((similarity, other))
                scored.sort(reverse=True)
                candidates = scored[:content_k]
            for similarity, other in candidates:
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


def _add_pair_credit(event: dict, seed: str, credit: float) -> None:
    pairs = event.setdefault("replay_pairs", {})
    pairs[seed] = float(pairs.get(seed, 0.0)) + credit
    if len(pairs) > PAIR_LIMIT:
        for key, _value in sorted(pairs.items(), key=lambda item: item[1])[:len(pairs) - PAIR_LIMIT]:
            del pairs[key]


def _decay_evidence(graph) -> None:
    for event in graph.events.values():
        evidence = float(event.get("replay_evidence", 0.0)) * EVIDENCE_DECAY
        pairs = {seed: float(value) * PAIR_DECAY
                 for seed, value in (event.get("replay_pairs") or {}).items()
                 if seed in graph.events and float(value) * PAIR_DECAY >= 0.01}
        if evidence >= 0.01:
            event["replay_evidence"] = min(EVIDENCE_CAP, evidence)
        else:
            event.pop("replay_evidence", None)
        if pairs:
            event["replay_pairs"] = pairs
        else:
            event.pop("replay_pairs", None)


def _promote(graph, event_id: str, digests: dict, stats: dict) -> None:
    event = graph.events.get(event_id) or {}
    evidence = float(event.get("replay_evidence", 0.0))
    if evidence < EVIDENCE_THRESHOLD:
        return
    pairs = sorted((event.get("replay_pairs") or {}).items(),
                   key=lambda item: item[1], reverse=True)[:PAIR_LIMIT]
    linked = 0
    for seed, _credit in pairs:
        if seed not in graph.events:
            continue
        if _same_segments(graph, event_id, seed) or digests.get(event_id) == digests.get(seed):
            continue
        edge = _find_edge(graph, event_id, seed)
        if edge is None:
            edge = graph.add_edge(event_id, seed, "相关", strength=0.2, source="replay")
            edge["v"] = 3
            stats["new_edges"] += 1
        else:
            edge["strength"] = min(1.0, float(edge.get("strength", 0.0)) + 0.08)
        edge["support"] = float(edge.get("support", 0.0)) + EVIDENCE_THRESHOLD
        edge["floor"] = min(FLOOR_CAP, FLOOR_PER_SUPPORT * edge["support"])
        edge["replay_support"] = int(edge.get("replay_support", 0)) + int(EVIDENCE_THRESHOLD)
        linked += 1
    if linked:
        event["replay_evidence"] = max(0.0, evidence - EVIDENCE_THRESHOLD)
        for seed, credit in pairs:
            remaining = float(credit) - EVIDENCE_THRESHOLD
            if remaining > 0:
                event.setdefault("replay_pairs", {})[seed] = remaining
            else:
                event.get("replay_pairs", {}).pop(seed, None)
        if not event.get("replay_pairs"):
            event.pop("replay_pairs", None)
        stats["edges"] += linked


def consolidate(graph, data_dir: str, session: str, sample: int = OLD_SAMPLE,
                hops: int = 2, embedder=None) -> dict:
    # Old replay edges are not this algorithm; rebuild them as evidence allows.
    for key, edge in list(graph.edges.items()):
        if edge.get("source") == "replay" and edge.get("v") != 3:
            del graph.edges[key]
    graph.pairs.clear()

    seeds = _marked_seeds(graph, data_dir, session)
    others = [event_id for event_id in graph.events if event_id not in seeds]

    texts = {event_id: event_label(graph, event_id) for event_id in graph.events}
    digests = {event_id: hashlib.sha1(text.encode("utf-8")).hexdigest()
               for event_id, text in texts.items()}
    sets = {event_id: terms(text)
            | terms(str(graph.events[event_id].get("name") or ""))
            for event_id, text in texts.items()}
    config = getattr(embedder, "config", None)
    store = VectorStore.open(
        data_dir,
        model=str(getattr(config, "embed_model", "") or ""),
        dim=int(getattr(config, "embed_dim", 0) or 0))
    vectors = event_vectors(graph, data_dir, texts, embedder, store=store)
    content = content_neighbors(graph, sets, vectors, limit=256,
                                texts=texts, data_dir=data_dir, store=store)

    counts = {}
    edge_seeds = {}
    content_total = 0
    for pass_number in range(PASSES):
        picker = random.Random("%s-%d" % (session, pass_number))
        extra = picker.sample(others, min(sample, len(others))) if others else []
        pass_seeds = seeds + extra
        touched = set()
        for seed in pass_seeds:
            lit, edge_hits, content_pairs = propagate(
                graph, seed, sets, vectors, hops=hops, content=content)
            for event_id in lit:
                counts[event_id] = counts.get(event_id, 0) + 1
            for key in edge_hits:
                edge_seeds.setdefault(key, set()).add(seed)
            content_total += len(content_pairs)
            for old in lit:
                if old == seed or _in_session(graph, old, session):
                    continue
                if _same_segments(graph, old, seed) or digests.get(old) == digests.get(seed):
                    continue
                touched.add(old)
                _add_pair_credit(graph.events[old], seed, 1.0 / PASSES)
        # Evidence is per old event per pass, not per seed: a single pass with
        # many seeds must not look like many independent replays.
        for old in touched:
            event = graph.events[old]
            event["replay_evidence"] = min(
                EVIDENCE_CAP,
                float(event.get("replay_evidence", 0.0)) + 1.0 / PASSES)

    reinforced = 0
    for key, seed_set in edge_seeds.items():
        if len(seed_set) < 2:
            continue
        edge = graph.edges.get(key)
        if edge:
            edge["strength"] = min(1.0, float(edge.get("strength", 0.0))
                                   + REINFORCE * len(seed_set))
            edge["support"] = float(edge.get("support", 0.0)) + len(seed_set)
            reinforced += 1

    _decay_evidence(graph)
    stats = {"edges": 0, "new_edges": 0}
    for event_id in list(graph.events):
        _promote(graph, event_id, digests, stats)

    pruned = 0
    for key, edge in list(graph.edges.items()):
        if edge.get("source") == "model":
            edge["strength"] = max(MODEL_FLOOR, float(edge.get("strength", 0.0)) * DECAY)
            continue
        if edge.get("source") != "replay":
            continue
        support = float(edge.get("support", 0.0)) * SUPPORT_DECAY
        floor = min(FLOOR_CAP, FLOOR_PER_SUPPORT * support)
        strength = max(floor, float(edge.get("strength", 0.0)) * DECAY)
        edge["support"] = support
        edge["floor"] = floor
        edge["strength"] = strength
        if strength < PRUNE and floor < 0.08:
            del graph.edges[key]
            pruned += 1

    for event_id, count in counts.items():
        event = graph.events[event_id]
        event["consolidation"] = min(
            1.0, float(event.get("consolidation", 0.0)) + 0.1 * count)
        if count:
            # 跨归档的使用次数：转正看这个，不看一轮重放里的三遍
            event["archive_hits"] = int(event.get("archive_hits") or 0) + 1

    graph.save()
    SessionStore(data_dir, session).archive()
    update_global(data_dir)
    return {"seeds": len(seeds), "lit": len(counts), "edges": stats["edges"],
            "reinforced": reinforced, "pruned": pruned, "content": content_total,
            "new_edges": stats["new_edges"], "passes": PASSES,
            "evidence": sum(1 for event in graph.events.values()
                            if float(event.get("replay_evidence", 0.0)) >= 0.01)}
