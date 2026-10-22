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
        # 上游只要收到一个空串，整批请求就忽略 dimensions、按模型原生 2560 维
        # 返回（2026-10-21 实测，可稳定复现；纯空白、制表符不会触发）。空串
        # 本来也没有可嵌的内容，直接不送，位置用 [] 占住保持一一对应。
        kept = [(index, text) for index, text in enumerate(texts) if text.strip()]
        if not kept:
            return [[] for _ in texts]
        out = [[] for _ in texts]
        for start in range(0, len(kept), self.config.embed_batch):
            chunk = kept[start:start + self.config.embed_batch]
            body = json.dumps({"model": self.config.embed_model,
                               "input": [text for _index, text in chunk],
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
            if len(items) != len(chunk):
                raise EmbedError("embedding count %d != requested %d"
                                 % (len(items), len(chunk)))
            for (index, _text), item in zip(chunk, items):
                vector = item.get("embedding") or []
                if len(vector) != self.config.embed_dim:
                    raise EmbedError(
                        "embedding dimension %d != requested %d"
                        % (len(vector), self.config.embed_dim))
                out[index] = vector
        return out


def cosine(first, second) -> float:
    if not first or not second:
        return 0.0
    if len(first) != len(second):
        raise ValueError("vector dimension mismatch: %d != %d"
                         % (len(first), len(second)))
    dot = sum(a * b for a, b in zip(first, second))
    norm = math.sqrt(sum(a * a for a in first)) * math.sqrt(sum(b * b for b in second))
    return dot / norm if norm else 0.0
