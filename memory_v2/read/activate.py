"""Activation: keyword seeds, spread over edges and content, competition."""

from __future__ import annotations

import hashlib
import math
import time
from datetime import datetime

from ..core.embed import cosine
from ..core.text import idf, terms
from ..store.vectors import VectorStore
from .search import event_date, event_last_ts, event_text


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def event_vectors(graph, data_dir: str, texts: dict, embedder, store=None) -> dict:
    if store is None:
        config = getattr(embedder, "config", None)
        store = VectorStore.open(data_dir,
                                 model=str(getattr(config, "embed_model", "") or ""),
                                 dim=int(getattr(config, "embed_dim", 0) or 0))
    pending = []
    for event_id, text in texts.items():
        digest = _digest(text)
        if store.get(event_id, digest) is None and embedder and embedder.available:
            pending.append((event_id, text, digest))
    if pending:
        vectors = embedder.embed([item[1] for item in pending])
        if len(vectors) != len(pending):
            raise ValueError("embedding count %d != pending %d"
                             % (len(vectors), len(pending)))
        for (event_id, _text, digest), vector in zip(pending, vectors):
            store.put(event_id, digest, vector)
        store.save()
    return {event_id: store.get(event_id, _digest(text)) or []
            for event_id, text in texts.items()}


def _similarity(first: str, second: str, sets: dict, vectors: dict) -> float:
    if vectors.get(first) and vectors.get(second):
        return cosine(vectors[first], vectors[second])
    left, right = sets[first], sets[second]
    return len(left & right) / math.sqrt(len(left) * len(right) + 1.0)


def _recency(graph, data_dir: str, event_id: str) -> float:
    stamp = event_last_ts(graph, data_dir, event_id)
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return 0.0
    days = max(0.0, (datetime.now() - when).total_seconds() / 86400.0)
    return math.exp(-days / 30.0)


def _access(event: dict, now: float) -> float:
    value = float(event.get("access", 0.0))
    stamp = event.get("access_ts")
    if stamp:
        days = max(0.0, (now - float(stamp)) / 86400.0)
        value *= 0.9 ** days
    return min(1.0, value)


def activate(graph, data_dir: str, keywords, embedder=None, prior=None,
             session: str = "", exclude=(), after: str = "", before: str = "",
             limit: int = 3, vector_store=None) -> dict:
    excluded = {exclude} if isinstance(exclude, str) else set(exclude or ())
    excluded.discard("")
    allowed = set()
    for event_id, event in graph.events.items():
        if event_id in excluded:
            continue
        segments = event.get("segments") or []
        if session and not any(str(segment.get("session") or "") != session
                               for segment in segments):
            continue
        allowed.add(event_id)

    texts = {event_id: event_text(graph, data_dir, event_id) for event_id in graph.events}
    sets = {event_id: terms(text) | terms(str(graph.events[event_id].get("name") or ""))
            for event_id, text in texts.items()}
    weights = idf(list(sets.values()))
    vectors = event_vectors(graph, data_dir, texts, embedder, store=vector_store)

    query_terms = set()
    for keyword in keywords:
        query_terms |= terms(str(keyword))
    query_text = " ".join(str(keyword) for keyword in keywords).strip()
    query_vector = (embedder.embed([query_text])[0]
                    if embedder and embedder.available and query_text else [])

    dates = {event_id: event_date(graph, data_dir, event_id) for event_id in graph.events}

    query_idf = sum(weights.get(term, 1.0) for term in query_terms)
    term_norms = {}
    for event_id in allowed:
        name_terms = terms(str(graph.events[event_id].get("name") or ""))
        text_idf = sum(weights.get(term, 1.0) for term in query_terms & sets[event_id])
        name_idf = sum(weights.get(term, 1.0) for term in query_terms & name_terms)
        if query_idf:
            term_norms[event_id] = min(
                1.0, (text_idf + 4.0 * name_idf) / (query_idf * 5.0))

    sims = {event_id: (cosine(query_vector, vectors.get(event_id) or [])
                       if query_vector else 0.0)
            for event_id in graph.events}

    seeds = {}
    for event_id in allowed:
        stamp = dates[event_id]
        if after and stamp and stamp < after:
            continue
        if before and stamp and stamp > before:
            continue
        term_norm = term_norms.get(event_id, 0.0)
        if query_vector:
            vec_norm = max(0.0, min(1.0, (sims[event_id] - 0.3) / 0.5))
            score = 0.8 * vec_norm + 0.2 * term_norm
        else:
            score = term_norm
        if score >= 0.15:
            seeds[event_id] = score

    if seeds:
        ceiling = max(seeds.values())
        seeds = {event_id: score / ceiling for event_id, score in seeds.items()}

    lit = {}
    contributions = {}
    for seed, seed_score in seeds.items():
        local = {}
        frontier = {seed: seed_score}
        for _hop in range(2):
            nxt = {}
            for node, score in frontier.items():
                for other, edge in graph.neighbors(node):
                    if other not in allowed:
                        continue
                    weight = 0.9 if edge.get("relation") == "属于" else 0.6
                    gain = score * float(edge.get("strength", 0.2)) * weight
                    if gain > 0.05:
                        nxt[other] = max(nxt.get(other, 0.0), gain)
                for other in graph.events:
                    if other == node or other not in allowed:
                        continue
                    similarity = _similarity(node, other, sets, vectors)
                    if similarity <= 0:
                        continue
                    gain = score * 0.5 * similarity
                    if gain > 0.05:
                        nxt[other] = max(nxt.get(other, 0.0), gain)
            for event_id, score in nxt.items():
                local[event_id] = local.get(event_id, 0.0) + score
            frontier = nxt
        local[seed] = local.get(seed, 0.0) + seed_score
        for event_id, score in local.items():
            lit[event_id] = lit.get(event_id, 0.0) + score
            contributions.setdefault(event_id, set()).add(seed)

    if prior:
        for event_id, score in prior.items():
            if event_id in allowed:
                lit[event_id] = lit.get(event_id, 0.0) + 0.5 * float(score)

    now = time.time()
    seed_ids = set(seeds)
    eligible = set()
    for event_id in lit:
        if event_id not in allowed:
            continue
        if event_id in seed_ids:
            eligible.add(event_id)
        elif len(contributions.get(event_id, ())) >= 2:
            similar = (sims[event_id] >= 0.5) if query_vector else (term_norms.get(event_id, 0.0) > 0)
            if similar:
                eligible.add(event_id)

    ranked = []
    for event_id, score in lit.items():
        if event_id not in allowed:
            continue
        direct = seeds.get(event_id, 0.0)
        if event_id in seed_ids:
            value = direct + 0.2 * max(0.0, score - direct)
        else:
            value = 0.4 * max(0.0, score)
        event = graph.events[event_id]
        value *= 1.0 + 0.2 * min(1.0, float(event.get("consolidation", 0.0)))
        value *= 1.0 + 0.1 * _access(event, now)
        value *= 1.0 + 0.1 * _recency(graph, data_dir, event_id)
        ranked.append([value, event_id, event_id in eligible])
    ranked.sort(reverse=True)

    eligible_items = [item for item in ranked if item[2]]
    other_items = [item for item in ranked if not item[2]]
    if eligible_items:
        winner = eligible_items[0][1]
        for item in eligible_items[1:]:
            similarity = _similarity(winner, item[1], sets, vectors)
            if similarity > 0.5:
                item[0] *= 1.0 - 0.5 * similarity
        eligible_items.sort(reverse=True)
    ordered = eligible_items + other_items

    items = []
    for value, event_id, _eligible in ordered[:limit]:
        items.append({
            "id": event_id,
            "name": str(graph.events[event_id].get("name") or ""),
            "date": dates[event_id],
            "score": round(value, 3),
            "hits": [str(keyword) for keyword in keywords
                     if terms(str(keyword)) & sets[event_id]],
        })

    near = []
    if not seeds and query_vector:
        scored = []
        for event_id in allowed:
            if not vectors.get(event_id):
                continue
            stamp = dates[event_id]
            if after and stamp and stamp < after:
                continue
            if before and stamp and stamp > before:
                continue
            scored.append((cosine(query_vector, vectors[event_id]), event_id))
        scored.sort(reverse=True)
        near = [{"id": event_id,
                 "name": str(graph.events[event_id].get("name") or ""),
                 "date": event_date(graph, data_dir, event_id),
                 "score": round(score, 3), "hits": []}
                for score, event_id in scored[:limit]]

    return {
        "winner": items[0] if items and eligible_items else None,
        "candidates": items,
        "near": near,
        "activation": {item["id"]: item["score"] for item in items[:5]},
        "has_seed": bool(seeds),
        "query_terms": query_terms,
        "query_vector": query_vector,
    }
