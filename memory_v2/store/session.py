"""状态记录：每轮一行，落在 <data>/状态/<场id>.jsonl。

每轮结束就写，不等归档。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List


_WARNED_BAD_LINES = set()


class SessionSealed(RuntimeError):
    """The session has been archived; conversation writes are closed."""


def next_session_id(data_dir: str) -> str:
    stamp = time.strftime("%Y-%m-%d")
    directory = os.path.join(data_dir, "状态")
    index = 1
    while True:
        candidate = "%s-%02d" % (stamp, index)
        if not os.path.exists(os.path.join(directory, candidate + ".jsonl")):
            return candidate
        index += 1


class SessionStore:
    def __init__(self, data_dir: str, session_id: str):
        self.data_dir = os.path.abspath(data_dir)
        self.session_id = str(session_id)
        self.dir = os.path.join(self.data_dir, "状态")
        self.path = os.path.join(self.dir, self.session_id + ".jsonl")

    @property
    def marker(self) -> str:
        return self.path + ".archived"

    def is_archived(self) -> bool:
        return os.path.exists(self.marker)

    def archive(self) -> None:
        if not os.path.exists(self.path) or self.is_archived():
            return
        os.makedirs(self.dir, exist_ok=True)
        temporary = self.marker + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.marker)

    def append(self, record: Dict[str, Any]) -> None:
        if self.is_archived():
            raise SessionSealed("这一场已归档（封口），只读：%s" % self.path)
        os.makedirs(self.dir, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def turns(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out: List[Dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    key = (self.path, number)
                    if key not in _WARNED_BAD_LINES:
                        _WARNED_BAD_LINES.add(key)
                        print("警告：%s 第 %d 行不是 JSON，已跳过。"
                              % (self.path, number), file=sys.stderr)
                    continue
        return out
