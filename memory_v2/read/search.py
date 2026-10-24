"""Event text, term sets, and expansion."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List

from ..core.text import terms
from ..store.session import SessionStore


@lru_cache(maxsize=128)
def _cached_turns(path: str, _mtime_ns: int, _size: int) -> Dict[int, dict]:
    data_dir = os.path.dirname(os.path.dirname(path))
    session = os.path.basename(path)[:-6]
    return {int(turn.get("turn")): turn
            for turn in SessionStore(data_dir, session).turns()}


def _session_turns(data_dir: str, session: str) -> Dict[int, dict]:
    path = SessionStore(data_dir, session).path
    try:
        stat = os.stat(path)
    except OSError:
        return {}
    return _cached_turns(path, stat.st_mtime_ns, stat.st_size)


def event_segments(graph, data_dir: str, event_id: str,
                   skip_session: str = "") -> List[dict]:
    event = graph.events.get(event_id)
    if not event:
        return []
    cache: Dict[str, Dict[int, dict]] = {}
    out: List[dict] = []
    for segment in event.get("segments") or []:
        session = str(segment.get("session"))
        if skip_session and session == skip_session:
            continue
        if session not in cache:
            cache[session] = _session_turns(data_dir, session)
        turns = cache[session]
        lines: List[str] = []
        date = ""
        for number in range(int(segment.get("start", 0)), int(segment.get("end", 0)) + 1):
            turn = turns.get(number)
            if not turn:
                continue
            date = date or str(turn.get("ts") or "")[:10]
            lines.append("他：%s\n我：%s" % (str(turn.get("user") or ""),
                                            str(turn.get("assistant") or "")))
        if lines:
            out.append({"date": date, "text": "\n".join(lines)})
    return out


def event_text(graph, data_dir: str, event_id: str) -> str:
    event = graph.events.get(event_id) or {}
    parts = []
    for note in event.get("notes") or []:
        stamp = str(note.get("ts") or "")[:10]
        parts.append("%s\n%s" % (stamp, str(note.get("text") or "")))
    for segment in event_segments(graph, data_dir, event_id):
        parts.append("%s\n%s" % (segment["date"], segment["text"]))
    return "\n".join(parts)


def event_label(graph, event_id: str) -> str:
    """Model-written identity of a node: name, context, and written notes.

    Retrieval (vectors and terms alike) is built on this, not on the dialogue
    transcript.  Loop events have no transcript; their notes are the material.
    """
    event = graph.events.get(event_id) or {}
    name = str(event.get("name") or "").strip()
    context = str(event.get("context") or "").strip()
    notes = " ".join(str(note.get("text") or "")
                     for note in event.get("notes") or []).strip()
    parts = [name]
    if context:
        parts.append(context)
    if notes:
        parts.append(notes)
    return "\n".join(part for part in parts if part)


def term_sets(graph, data_dir: str) -> Dict[str, set]:
    out = {}
    for event_id, event in graph.events.items():
        out[event_id] = terms(event_text(graph, data_dir, event_id)) | terms(str(event.get("name") or ""))
    return out


def event_date(graph, data_dir: str, event_id: str) -> str:
    event = graph.events.get(event_id) or {}
    notes = event.get("notes") or []
    if notes:
        stamp = str(notes[0].get("ts") or "")
        if stamp:
            return stamp[:10]
    segments = event.get("segments") or []
    if segments:
        turns = _session_turns(data_dir, str(segments[0].get("session")))
        turn = turns.get(int(segments[0].get("start", 0)))
        if turn:
            return str(turn.get("ts") or "")[:10]
    return str(event.get("created") or "")[:10]


def event_last_ts(graph, data_dir: str, event_id: str) -> str:
    event = graph.events.get(event_id) or {}
    notes = event.get("notes") or []
    if notes:
        return str(notes[-1].get("ts") or "")[:19]
    segments = event.get("segments") or []
    if not segments:
        return ""
    last = segments[-1]
    turns = _session_turns(data_dir, str(last.get("session")))
    turn = turns.get(int(last.get("end", 0)))
    return str(turn.get("ts") or "")[:19] if turn else ""


def expand(graph, data_dir: str, event_id: str) -> str:
    event = graph.events.get(event_id)
    if not event:
        return ""
    return "%s（%s）\n%s" % (event.get("name") or "", event_date(graph, data_dir, event_id),
                            event_text(graph, data_dir, event_id))
