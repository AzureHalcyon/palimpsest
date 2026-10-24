"""Split `<think>` blocks out of model text.

Some OpenAI-compatible gateways put the model's reasoning inside the normal
`content` field as `<think>...</think>` instead of returning a separate
`reasoning_content` field. The UI and the turn log need the two separated.
"""

from __future__ import annotations

import re
from typing import List, Tuple

_OPEN = "<think"
_CLOSE = "</think"
_TAG_RE = re.compile(r"<think\b[^>]*>|</think\s*>", re.IGNORECASE)


def split_think(text: str) -> Tuple[str, str]:
    """Return (reasoning, visible) for a complete block of text."""
    text = str(text or "")
    reasoning: List[str] = []
    visible: List[str] = []
    depth = 0
    last = 0
    for match in _TAG_RE.finditer(text):
        chunk = text[last:match.start()]
        if chunk:
            (reasoning if depth else visible).append(chunk)
        if match.group(0).lower().startswith("</"):
            depth = max(0, depth - 1)
        else:
            depth += 1
        last = match.end()
    tail = text[last:]
    if tail:
        (reasoning if depth else visible).append(tail)
    return "".join(reasoning).strip(), "".join(visible).strip()


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of `text` that starts `tag`."""
    limit = min(len(text), len(tag) - 1)
    for size in range(limit, 0, -1):
        if text[-size:].lower() == tag[:size].lower():
            return size
    return 0


def has_think(text: str) -> bool:
    return bool(_TAG_RE.search(str(text or "")))


def normalize_message(message: dict) -> dict:
    """Move `<think>` blocks from an assistant message's content to reasoning."""
    data = dict(message or {})
    content = str(data.get("content") or "")
    if not has_think(content):
        return data
    reasoning = str(data.get("reasoning_content")
                    or data.get("reasoning") or "")
    inside, visible = split_think(content)
    if reasoning:
        if inside:
            reasoning = reasoning.rstrip() + "\n\n" + inside
    else:
        reasoning = inside
    data["content"] = visible
    if reasoning:
        data["reasoning_content"] = reasoning
    return data


def _normalize_timeline(blocks) -> list:
    out = []
    for block in blocks or []:
        if isinstance(block, dict) and block.get("kind") == "text":
            text = str(block.get("text") or "")
            if not has_think(text):
                if text.strip():
                    out.append(block)
                continue
            inside, visible = split_think(text)
            if inside:
                out.append({"kind": "reasoning", "text": inside})
            if visible.strip():
                out.append(dict(block, text=visible))
            continue
        if isinstance(block, dict) and block.get("kind") == "reasoning":
            if str(block.get("text") or "").strip():
                out.append(block)
            continue
        out.append(block)
    return out


def normalize_turn(record: dict) -> dict:
    """Clean a stored turn record (`assistant`, `reasoning`, `timeline`)."""
    data = dict(record or {})
    for key in ("assistant", "reply"):
        if key not in data:
            continue
        text = str(data.get(key) or "")
        if not has_think(text):
            continue
        inside, visible = split_think(text)
        data[key] = visible
        if inside:
            old = str(data.get("reasoning") or "")
            data["reasoning"] = (old.rstrip() + "\n\n" + inside).strip() if old else inside
    if isinstance(data.get("timeline"), list):
        data["timeline"] = _normalize_timeline(data["timeline"])
    return data


class ThinkFilter:
    """Incremental version of `split_think` for streamed deltas."""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, text: str) -> List[Tuple[str, str]]:
        self._buffer += str(text or "")
        out: List[Tuple[str, str]] = []
        while self._buffer:
            lower = self._buffer.lower()
            if self._in_think:
                index = lower.find(_CLOSE)
                if index < 0:
                    hold = _partial_suffix(self._buffer, _CLOSE)
                    emit = self._buffer[:-hold] if hold else self._buffer
                    if emit:
                        out.append(("reasoning", emit))
                    self._buffer = self._buffer[-hold:] if hold else ""
                    break
                if index:
                    out.append(("reasoning", self._buffer[:index]))
                    self._buffer = self._buffer[index:]
                    continue
                end = self._buffer.find(">")
                if end < 0:
                    break
                self._buffer = self._buffer[end + 1:]
                self._in_think = False
                continue
            index = lower.find(_OPEN)
            if index < 0:
                hold = _partial_suffix(self._buffer, _OPEN)
                emit = self._buffer[:-hold] if hold else self._buffer
                if emit:
                    out.append(("content", emit))
                self._buffer = self._buffer[-hold:] if hold else ""
                break
            if index:
                out.append(("content", self._buffer[:index]))
                self._buffer = self._buffer[index:]
                continue
            end = self._buffer.find(">")
            if end < 0:
                break
            self._buffer = self._buffer[end + 1:]
            self._in_think = True
        return out

    def flush(self) -> List[Tuple[str, str]]:
        if not self._buffer:
            return []
        kind = "reasoning" if self._in_think else "content"
        text = self._buffer
        self._buffer = ""
        return [(kind, text)]
