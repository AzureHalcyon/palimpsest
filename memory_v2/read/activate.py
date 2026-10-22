"""Activation: keyword seeds, spread over edges and content, competition."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import OrderedDict
from datetime import datetime

from ..core.embed import cosine
from ..core.text import idf, terms
from ..store.vectors import VectorStore
from .search import event_date, event_label, event_last_ts


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
        if not str(text or "").strip():
            # 刚建出来还没挂上正文的节点（segments 为空）：没有可嵌的文本，
            # 词面相似度顶上，等它有了正文再补向量。
            continue
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


def _lexical_neighbors(ids: list, sets: dict, limit: int) -> dict:
    postings = {}
    for event_id in ids:
        for term in sets.get(event_id, ()):
            postings.setdefault(term, []).append(event_id)
    out = {}
    for event_id in ids:
        left = sets.get(event_id, set())
        counts = {}
        for term in left:
            for other in postings.get(term, ()):
                if other != event_id:
                    counts[other] = counts.get(other, 0) + 1
        scored = []
        for other, count in counts.items():
            size = len(sets.get(other, ()))
            if size:
                scored.append((count / math.sqrt(len(left) * size + 1.0), other))
        scored.sort(reverse=True)
        out[event_id] = scored[:limit]
    return out


_CONTENT_CACHE = OrderedDict()
_CONTENT_CACHE_LIMIT = 1
ANN_MIN = 3000
NEIGHBOR_LIMIT = 256


def _vector_neighbors(ids: list, vectors: dict, limit: int) -> dict:
    import numpy as np

    matrix = np.asarray([vectors[event_id] for event_id in ids], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = matrix / norms
    keep = min(limit, len(ids) - 1)
    out = {event_id: [] for event_id in ids}
    if keep <= 0:
        return out

    if len(ids) >= ANN_MIN:
        try:
            import faiss

            index = faiss.IndexHNSWFlat(matrix.shape[1], 32,
                                        faiss.METRIC_INNER_PRODUCT)
            index.add(normalized)
            distances, indexes = index.search(normalized, keep + 1)
            for row, event_id in enumerate(ids):
                scored = [(float(distances[row, column]),
                           ids[int(indexes[row, column])])
                          for column in range(indexes.shape[1])
                          if int(indexes[row, column]) >= 0
                          and int(indexes[row, column]) != row]
                scored.sort(reverse=True)
                out[event_id] = [(value, other) for value, other in scored if value > 0][:keep]
            return out
        except Exception:
            pass

    similarity = normalized @ normalized.T
    np.fill_diagonal(similarity, -1.0)
    indexes = np.argpartition(-similarity, keep, axis=1)[:, :keep]
    for row, event_id in enumerate(ids):
        scored = [(float(similarity[row, column]), ids[int(column)])
                  for column in indexes[row]]
        scored.sort(reverse=True)
        out[event_id] = [(value, other) for value, other in scored if value > 0]
    return out


def _neighbor_fingerprint(texts: dict, sets: dict, store, limit: int,
                          vectors: dict = None) -> str:
    digest = hashlib.sha1()
    model = str(getattr(store, "model", "") or "")
    dim = int(getattr(store, "dim", 0) or 0)
    digest.update(("k=%d|model=%s|dim=%d|" % (limit, model, dim)).encode("utf-8"))
    for event_id in sorted(texts or {}):
        digest.update(str(event_id).encode("utf-8"))
        digest.update(hashlib.sha1(str(texts[event_id]).encode("utf-8")).digest())
        digest.update(hashlib.sha1(
            "\0".join(sorted(sets.get(event_id, ()))).encode("utf-8")).digest())
        digest.update(b"1" if (vectors or {}).get(event_id) else b"0")
    return digest.hexdigest()


def _load_neighbor_cache(path: str, fingerprint: str):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if str((payload.get("meta") or {}).get("fingerprint") or "") != fingerprint:
        return None
    items = payload.get("items")
    if not isinstance(items, dict):
        return None
    return {event_id: [(float(value), str(other))
                       for value, other in pairs]
            for event_id, pairs in items.items()}


def _save_neighbor_cache(path: str, fingerprint: str, content: dict) -> None:
    if not path:
        return
    payload = {
        "meta": {"fingerprint": fingerprint},
        "items": {event_id: [[value, other] for value, other in pairs]
                  for event_id, pairs in content.items()},
    }
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass


def content_neighbors(graph, sets: dict, vectors: dict, limit: int = NEIGHBOR_LIMIT,
                      texts: dict = None, data_dir: str = "", store=None) -> dict:
    """Top-k content neighbours, computed once per graph/vector version.

    The old path compared every node with every event inside the propagation
    loop.  With 1024-d vectors that is hundreds of thousands of Python cosine
    calls per recall.  Here the same work is done once (numpy or faiss when
    available), cached in memory and on disk, and propagation only walks the
    short list.
    """
    fingerprint = _neighbor_fingerprint(texts or {}, sets, store, limit, vectors)
    cached = _CONTENT_CACHE.get(fingerprint)
    if cached is not None:
        _CONTENT_CACHE.move_to_end(fingerprint)
        return cached
    path = os.path.join(data_dir, "content-index.json") if data_dir else ""
    cached = _load_neighbor_cache(path, fingerprint)
    if cached is not None:
        _CONTENT_CACHE[fingerprint] = cached
        while len(_CONTENT_CACHE) > _CONTENT_CACHE_LIMIT:
            _CONTENT_CACHE.popitem(last=False)
        return cached

    ids = [event_id for event_id in graph.events if event_id in sets]
    with_vectors = [event_id for event_id in ids if vectors.get(event_id)]
    lexical = _lexical_neighbors(ids, sets, limit)
    if with_vectors:
        try:
            vector = _vector_neighbors(with_vectors, vectors, limit)
        except (ImportError, ValueError):
            vector = {}
        merged = {}
        for event_id in ids:
            candidates = {}
            for value, other in lexical.get(event_id, ()):
                candidates[other] = max(candidates.get(other, 0.0), value)
            for value, other in vector.get(event_id, ()):
                candidates[other] = max(candidates.get(other, 0.0), value)
            merged[event_id] = sorted(
                ((value, other) for other, value in candidates.items()),
                reverse=True)[:limit]
        content = merged
    else:
        content = lexical

    _CONTENT_CACHE[fingerprint] = content
    while len(_CONTENT_CACHE) > _CONTENT_CACHE_LIMIT:
        _CONTENT_CACHE.popitem(last=False)
    _save_neighbor_cache(path, fingerprint, content)
    return content


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

    texts = {event_id: event_label(graph, event_id) for event_id in graph.events}
    sets = {
        event_id: terms(text)
        | terms(str(graph.events[event_id].get("name") or ""))
        for event_id, text in texts.items()
    }
    weights = idf(list(sets.values()))
    config = getattr(embedder, "config", None)
    store = vector_store or VectorStore.open(
        data_dir,
        model=str(getattr(config, "embed_model", "") or ""),
        dim=int(getattr(config, "embed_dim", 0) or 0))
    vectors = event_vectors(graph, data_dir, texts, embedder, store=store)
    content = content_neighbors(graph, sets, vectors, limit=NEIGHBOR_LIMIT, texts=texts,
                                data_dir=data_dir, store=store)

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
                    weight = 0.85 if edge.get("source") == "model" else 0.6
                    gain = score * float(edge.get("strength", 0.2)) * weight
                    if gain > 0.05:
                        nxt[other] = max(nxt.get(other, 0.0), gain)
                for similarity, other in content.get(node, ()):
                    if other not in allowed:
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
