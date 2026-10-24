"""过期归档：短期记忆到期后压成一行、搬出图。

`short` 的语义是"工作用，几天后作废"。作废不是降级——所以到了期先压成一行，写进
`archive.jsonl`，再从图里删掉：检索、激活、注意力都不再包含它。要考古就去翻那个
文件（一行一条，`grep` 得到）。

压不动就退回规则压缩：取最后一条 note 的第一句，至少别丢。
"""

from __future__ import annotations

import io
import json
import os
import time
from typing import Callable, Dict, Optional

from ..core.prompts import archive_prompt

ARCHIVE_NAME = "archive.jsonl"


def archive_path(data_dir: str) -> str:
    return os.path.join(str(data_dir or "."), ARCHIVE_NAME)


def fallback_line(event: dict) -> str:
    """规则压缩：最后一条 note 的第一句，最多 40 字。"""
    notes = event.get("notes") or []
    text = str(notes[-1].get("text") or "") if notes else ""
    text = text.strip().replace("\n", " ")
    for stop in ("。", "！", "？", "；"):
        head = text.split(stop)[0]
        if head:
            text = head
            break
    if len(text) > 40:
        text = text[:39] + "…"
    return text or str(event.get("name") or "").strip() or "（没有材料）"


def build_material(graph, ids) -> str:
    blocks = []
    for event_id in ids:
        event = graph.events.get(event_id) or {}
        lines = ["%s · %s" % (event_id, str(event.get("name") or ""))]
        for note in event.get("notes") or []:
            stamp = str(note.get("ts") or "")[:16]
            lines.append("%s %s" % (stamp, str(note.get("text") or "")))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def ask_model(client, material: str) -> Dict[str, str]:
    """让模型一条压一行；返回 id → 一行。失败交给上层退回规则压缩。"""
    messages = [{"role": "system", "content": archive_prompt()},
                {"role": "user", "content": material}]
    message = client.chat(messages, temperature=0.2)
    raw = str(message.get("content") or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        payload = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value).strip() for key, value in payload.items()
            if str(value).strip()}


def write_archive(data_dir: str, rows) -> int:
    """一行一条追加到 archive.jsonl；不改图，也不参与检索。"""
    if not rows:
        return 0
    target = archive_path(data_dir)
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    with io.open(target, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def archive_expired(graph, compress: Optional[Callable[[str], Dict[str, str]]] = None,
                    data_dir: str = "", now_iso: str = "") -> int:
    """把到期的短期事件压成一行搬进归档文件，并从图里删掉；返回处理了几件。"""
    ids = graph.archived_expired(now_iso)
    if not ids:
        return 0
    lines: Dict[str, str] = {}
    if compress is not None:
        try:
            lines = compress(build_material(graph, ids)) or {}
        except Exception:
            lines = {}
    stamp = str(now_iso or time.strftime("%Y-%m-%dT%H:%M:%S"))
    rows = []
    for event_id in ids:
        event = graph.events.get(event_id) or {}
        text = lines.get(event_id) or fallback_line(event)
        rows.append({"id": event_id,
                     "name": str(event.get("name") or ""),
                     "text": text,
                     "archived_at": stamp})
        # 归档 = 搬走：图和检索都不再包含它。
        graph.remove_event(event_id)
    write_archive(data_dir or ".", rows)
    graph.save()
    return len(rows)
