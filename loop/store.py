"""SQLite：旧段、块与原文、检索记录。

块分三态：open（当前块）/ previous（上一块，原文）/ compressed（已压成摘要）。
当前块和上一块的原文都存；重启时按状态重建。
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import List

from .context import Band, Block, BlockEntry, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS bands (
    level INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    unfinished TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS entries (
    block_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls TEXT NOT NULL DEFAULT '[]',
    tool_call_id TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL,
    PRIMARY KEY (block_id, seq)
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recall_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    question TEXT NOT NULL,
    event_ids TEXT NOT NULL DEFAULT '[]'
);
"""


class LoopStore:
    def __init__(self, path: str):
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(blocks)")}
        if "state" not in columns:
            # 旧库的块都是压缩过的；补上状态列，历史行按 compressed 读。
            self.db.execute(
                "ALTER TABLE blocks ADD COLUMN state TEXT NOT NULL DEFAULT 'open'")
            self.db.execute("UPDATE blocks SET state = 'compressed'")

    def close(self) -> None:
        self.db.close()

    def next_turn(self) -> int:
        """Persistent step number; survives restarts (used for event step tags)."""
        row = self.db.execute("SELECT value FROM state WHERE key = 'turn'").fetchone()
        turn = int(row["value"]) + 1 if row and str(row["value"]).isdigit() else 1
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('turn', ?)",
                            (str(turn),))
        return turn

    # ── 旧段 ──────────────────────────────────────────────────────────────
    def bands(self) -> List[Band]:
        rows = self.db.execute(
            "SELECT level, text, start_ts, end_ts FROM bands ORDER BY level").fetchall()
        return [Band(int(row["level"]), str(row["text"]),
                     str(row["start_ts"]), str(row["end_ts"])) for row in rows]

    def replace_bands(self, bands: List[Band]) -> None:
        with self.db:
            self.db.execute("DELETE FROM bands")
            self.db.executemany(
                "INSERT INTO bands (level, text, start_ts, end_ts) VALUES (?, ?, ?, ?)",
                [(band.level, band.text, band.start_ts, band.end_ts)
                 for band in sorted(bands, key=lambda band: band.level)])

    # ── 块与原文 ──────────────────────────────────────────────────────────
    def load_state(self) -> dict:
        previous_row = self.db.execute(
            "SELECT id FROM blocks WHERE state = 'previous' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        open_row = self.db.execute(
            "SELECT id FROM blocks WHERE state = 'open' ORDER BY id DESC LIMIT 1"
        ).fetchone()

        previous = Block()
        previous_id = 0
        if previous_row:
            previous_id = int(previous_row["id"])
            previous = self.load_block(previous_id)

        if open_row:
            current_id = int(open_row["id"])
            current = self.load_block(current_id)
        else:
            start = now()
            current_id = self.open_block(start)
            current = Block(start_ts=start)

        return {"bands": self.bands(), "previous": previous,
                "previous_id": previous_id, "current": current,
                "current_id": current_id}

    def load_block(self, block_id: int) -> Block:
        row = self.db.execute(
            "SELECT start_ts, end_ts FROM blocks WHERE id = ?", (block_id,)).fetchone()
        if row is None:
            return Block()
        entries = []
        for item in self.db.execute(
                "SELECT role, content, tool_calls, tool_call_id, ts"
                " FROM entries WHERE block_id = ? ORDER BY seq", (block_id,)):
            message = {"role": str(item["role"]),
                       "content": str(item["content"] or "")}
            calls = json.loads(item["tool_calls"] or "[]")
            if calls:
                message["tool_calls"] = calls
            if str(item["tool_call_id"] or ""):
                message["tool_call_id"] = str(item["tool_call_id"])
            entries.append(BlockEntry(message, str(item["ts"])))
        return Block(entries, str(row["start_ts"]), str(row["end_ts"]))

    def open_block(self, start_ts: str) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO blocks (start_ts, state) VALUES (?, 'open')", (start_ts,))
        return int(cursor.lastrowid)

    def append_entry(self, block_id: int, entry: BlockEntry) -> None:
        message = entry.message
        seq = int(self.db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM entries WHERE block_id = ?",
            (block_id,)).fetchone()[0])
        with self.db:
            self.db.execute(
                "INSERT INTO entries (block_id, seq, role, content, tool_calls,"
                " tool_call_id, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (block_id, seq, str(message.get("role") or ""),
                 str(message.get("content") or ""),
                 json.dumps(list(message.get("tool_calls") or []), ensure_ascii=False),
                 str(message.get("tool_call_id") or ""), entry.ts))

    def promote_block(self, block_id: int, end_ts: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE blocks SET state = 'previous', end_ts = ? WHERE id = ?",
                (end_ts, block_id))

    def finish_previous(self, block_id: int, summary: str,
                        keywords: List[str], unfinished: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE blocks SET state = 'compressed', summary = ?, keywords = ?,"
                " unfinished = ? WHERE id = ?",
                (summary, json.dumps(list(keywords or []), ensure_ascii=False),
                 unfinished, block_id))

    def blocks(self) -> List[dict]:
        rows = self.db.execute(
            "SELECT id, start_ts, end_ts, summary, keywords, unfinished, state"
            " FROM blocks ORDER BY id").fetchall()
        return [{"id": int(row["id"]),
                 "start_ts": str(row["start_ts"]),
                 "end_ts": str(row["end_ts"]),
                 "summary": str(row["summary"]),
                 "keywords": json.loads(row["keywords"] or "[]"),
                 "unfinished": str(row["unfinished"]),
                 "state": str(row["state"])} for row in rows]

    def entries(self, block_id: int) -> List[dict]:
        rows = self.db.execute(
            "SELECT role, content, ts FROM entries WHERE block_id = ? ORDER BY seq",
            (block_id,)).fetchall()
        return [{"role": str(row["role"]), "content": str(row["content"]),
                 "ts": str(row["ts"])} for row in rows]

    # ── 检索记录 ──────────────────────────────────────────────────────────
    def add_recall(self, question: str, event_ids: List[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO recall_log (ts, question, event_ids) VALUES (?, ?, ?)",
                (now(), question, json.dumps(list(event_ids or []), ensure_ascii=False)))

    def recalls(self) -> List[dict]:
        rows = self.db.execute(
            "SELECT id, ts, question, event_ids FROM recall_log ORDER BY id").fetchall()
        return [{"id": int(row["id"]), "ts": str(row["ts"]),
                 "question": str(row["question"]),
                 "event_ids": json.loads(row["event_ids"] or "[]")} for row in rows]
