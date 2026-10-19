"""Text loaded from prompts/."""

from __future__ import annotations

import io
import os
import time
from typing import Dict, List


_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def _read(name: str) -> str:
    with io.open(os.path.join(_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def system_messages() -> List[Dict[str, str]]:
    today = time.strftime("%Y-%m-%d")
    return [
        {"role": "system", "content": _read("persona.txt")},
        {"role": "system", "content": _read("event-system.txt")},
        {"role": "system", "content": _read("sys2.txt")},
        {"role": "system",
         "content": _read("date.txt").replace("$today", today)},
    ]


def writer_prompt() -> str:
    return _read("write-memory.txt")
