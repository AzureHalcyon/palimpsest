"""Archive of written memories, one line per recall."""

from __future__ import annotations

import json
import os
import time


class WriterArchive:
    def __init__(self, data_dir: str):
        self.dir = os.path.join(os.path.abspath(data_dir), "说法")

    def append(self, event_id: str, session: str, cue, memory: str) -> None:
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, str(event_id) + ".jsonl")
        line = json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session": session,
            "cue": list(cue),
            "memory": memory,
        }, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
