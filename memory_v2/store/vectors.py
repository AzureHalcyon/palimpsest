"""Cached event vectors, keyed by event id and text hash.

The cache also carries the embedding identity `(model, dim)`. A cache made by
another model or another embedding dimension is a different coordinate system
and must never be mixed into retrieval; stale files are renamed, not reused.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, Optional


_WARNED_BAD_CACHE = set()


class VectorStore:
    def __init__(self, path: str, model: str = "", dim: int = 0):
        self.path = os.path.abspath(path)
        self.model = str(model or "")
        self.dim = int(dim or 0)
        self.items: Dict[str, dict] = {}
        self.load()

    @classmethod
    def open(cls, data_dir: str, model: str = "", dim: int = 0) -> "VectorStore":
        return cls(os.path.join(data_dir, "vectors.json"), model=model, dim=dim)

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError) as exc:
            self._quarantine("读不了（%s）" % exc)
            return
        if not isinstance(data, dict) or "items" not in data:
            self._quarantine("旧格式，没有 model/dim 指纹")
            return
        items = data.get("items")
        meta = data.get("meta") or {}
        if not isinstance(items, dict) or not isinstance(meta, dict):
            self._quarantine("结构不对")
            return
        cached_model = str(meta.get("model") or "")
        try:
            cached_dim = int(meta.get("dim") or 0)
        except (TypeError, ValueError):
            self._quarantine("dim 不是整数")
            return
        if self.model and not cached_model:
            self._quarantine("缓存没有模型指纹")
            return
        if self.dim and not cached_dim:
            self._quarantine("缓存没有维度指纹")
            return
        if self.model and cached_model != self.model:
            self._quarantine("模型不符（缓存 %s，当前 %s）"
                             % (cached_model, self.model))
            return
        if self.dim and cached_dim != self.dim:
            self._quarantine("维度不符（缓存 %d，当前 %d）"
                             % (cached_dim, self.dim))
            return
        self.model = cached_model or self.model
        self.dim = cached_dim or self.dim
        self.items = dict(items)

    def _quarantine(self, reason: str) -> None:
        base = self.path + ".stale-" + time.strftime("%Y%m%d-%H%M%S")
        target = base
        suffix = 1
        while os.path.exists(target):
            target = "%s-%d" % (base, suffix)
            suffix += 1
        try:
            os.replace(self.path, target)
        except OSError:
            pass
        if self.path not in _WARNED_BAD_CACHE:
            _WARNED_BAD_CACHE.add(self.path)
            print("警告：向量缓存作废，已重建：%s（%s）"
                  % (self.path, reason), file=sys.stderr)
        self.items = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {"meta": {"model": self.model, "dim": self.dim},
                   "items": self.items}
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def get(self, event_id: str, text_hash: str) -> Optional[list]:
        item = self.items.get(event_id)
        if not item or item.get("hash") != text_hash:
            return None
        vector = item.get("vector") or []
        if self.dim and len(vector) != self.dim:
            return None
        return vector

    def put(self, event_id: str, text_hash: str, vector: list) -> None:
        if self.dim and len(vector) != self.dim:
            raise ValueError("vector dimension %d != cache dimension %d"
                             % (len(vector), self.dim))
        self.items[event_id] = {"hash": text_hash, "vector": list(vector)}
