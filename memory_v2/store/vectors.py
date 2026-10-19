"""Cached event vectors, keyed by event id and text hash."""

from __future__ import annotations

import json
import os
from typing import Dict, Optional


class VectorStore:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.items: Dict[str, dict] = {}
        self.load()

    @classmethod
    def open(cls, data_dir: str) -> "VectorStore":
        return cls(os.path.join(data_dir, "vectors.json"))

    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                self.items = dict(json.load(handle))
        except (OSError, json.JSONDecodeError):
            self.items = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.items, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def get(self, event_id: str, text_hash: str) -> Optional[list]:
        item = self.items.get(event_id)
        if not item or item.get("hash") != text_hash:
            return None
        return item.get("vector")

    def put(self, event_id: str, text_hash: str, vector: list) -> None:
        self.items[event_id] = {"hash": text_hash, "vector": list(vector)}
