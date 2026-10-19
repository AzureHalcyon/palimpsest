"""Embedding client for the coarse content layer."""

from __future__ import annotations

import json
import math
import urllib.request


class EmbedError(RuntimeError):
    pass


class Embedder:
    def __init__(self, config, credentials):
        self.config = config
        self.credentials = credentials

    @property
    def available(self) -> bool:
        return bool(self.credentials.embed_url and self.credentials.embed_key)

    def embed(self, texts) -> list:
        texts = [str(text or "") for text in texts]
        if not texts or not self.available:
            return []
        vectors = []
        for start in range(0, len(texts), self.config.embed_batch):
            chunk = texts[start:start + self.config.embed_batch]
            body = json.dumps({"model": self.config.embed_model,
                               "input": chunk,
                               "dimensions": self.config.embed_dim}).encode("utf-8")
            request = urllib.request.Request(
                self.credentials.embed_url + "/embeddings", data=body, method="POST")
            request.add_header("Authorization", "Bearer " + self.credentials.embed_key)
            request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request,
                                            timeout=self.config.embed_timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                raise EmbedError(str(exc)) from exc
            items = sorted(payload.get("data") or [], key=lambda item: item.get("index", 0))
            for item in items:
                vector = item.get("embedding") or []
                if len(vector) != self.config.embed_dim:
                    raise EmbedError(
                        "embedding dimension %d != requested %d"
                        % (len(vector), self.config.embed_dim))
                vectors.append(vector)
        return vectors


def cosine(first, second) -> float:
    if not first or not second:
        return 0.0
    if len(first) != len(second):
        raise ValueError("vector dimension mismatch: %d != %d"
                         % (len(first), len(second)))
    dot = sum(a * b for a, b in zip(first, second))
    norm = math.sqrt(sum(a * a for a in first)) * math.sqrt(sum(b * b for b in second))
    return dot / norm if norm else 0.0
