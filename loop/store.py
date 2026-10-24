"""SQLite：旧段、块与原文、检索记录。

块分三态：open（当前块）/ previous（上一块，原文）/ compressed（已压成摘要）。
当前块和上一块的原文都存；重启时按状态重建。
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import List, Optional

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
    meta TEXT NOT NULL DEFAULT '{}',
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
CREATE TABLE IF NOT EXISTS usage (
    step INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    prompt INTEGER NOT NULL DEFAULT 0,
    completion INTEGER NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    cache_miss INTEGER NOT NULL DEFAULT 0,
    reasoning INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'user',
    text TEXT NOT NULL DEFAULT '',
    consumed INTEGER NOT NULL DEFAULT 0
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
        entry_columns = {row[1] for row in self.db.execute("PRAGMA table_info(entries)")}
        if "meta" not in entry_columns:
            self.db.execute(
                "ALTER TABLE entries ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'")

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
                "SELECT role, content, tool_calls, tool_call_id, ts, meta"
                " FROM entries WHERE block_id = ? ORDER BY seq", (block_id,)):
            message = {"role": str(item["role"]),
                       "content": str(item["content"] or "")}
            calls = json.loads(item["tool_calls"] or "[]")
            if calls:
                message["tool_calls"] = calls
            if str(item["tool_call_id"] or ""):
                message["tool_call_id"] = str(item["tool_call_id"])
            entries.append(BlockEntry(message, str(item["ts"]),
                                      json.loads(item["meta"] or "{}")))
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
                " tool_call_id, ts, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (block_id, seq, str(message.get("role") or ""),
                 str(message.get("content") or ""),
                 json.dumps(list(message.get("tool_calls") or []), ensure_ascii=False),
                 str(message.get("tool_call_id") or ""), entry.ts,
                 json.dumps(dict(entry.meta or {}), ensure_ascii=False)))

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

    # 保留：自检和排障要直接看块与原文，没有运行时调用方。
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

    # 保留：自检和排障要看某一块的原文；运行时读的是 load_block。
    def entries(self, block_id: int) -> List[dict]:
        rows = self.db.execute(
            "SELECT role, content, ts FROM entries WHERE block_id = ? ORDER BY seq",
            (block_id,)).fetchall()
        return [{"role": str(row["role"]), "content": str(row["content"]),
                 "ts": str(row["ts"])} for row in rows]

    # ── 用量 ──────────────────────────────────────────────────────────────
    def add_usage(self, step: int, usage: dict) -> None:
        """把服务端 usage 原样落一行：真数只留不猜。"""
        usage = dict(usage or {})
        details = usage.get("completion_tokens_details") or {}
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO usage (step, ts, prompt, completion,"
                " cache_hit, cache_miss, reasoning) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(step), now(),
                 int(usage.get("prompt_tokens") or 0),
                 int(usage.get("completion_tokens") or 0),
                 int(usage.get("prompt_cache_hit_tokens") or 0),
                 int(usage.get("prompt_cache_miss_tokens") or 0),
                 int(details.get("reasoning_tokens") or 0)))

    def usages(self) -> List[dict]:
        rows = self.db.execute(
            "SELECT step, ts, prompt, completion, cache_hit, cache_miss, reasoning"
            " FROM usage ORDER BY step").fetchall()
        return [{"step": int(row["step"]), "ts": str(row["ts"]),
                 "prompt": int(row["prompt"]), "completion": int(row["completion"]),
                 "cache_hit": int(row["cache_hit"]),
                 "cache_miss": int(row["cache_miss"]),
                 "reasoning": int(row["reasoning"])} for row in rows]

    def usage_totals(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*) AS steps, COALESCE(SUM(prompt), 0) AS prompt,"
            " COALESCE(SUM(completion), 0) AS completion,"
            " COALESCE(SUM(cache_hit), 0) AS cache_hit,"
            " COALESCE(MAX(prompt), 0) AS peak FROM usage").fetchone()
        totals = {"steps": int(row["steps"]), "prompt": int(row["prompt"]),
                  "completion": int(row["completion"]),
                  "cache_hit": int(row["cache_hit"]), "peak": int(row["peak"])}
        totals["total"] = totals["prompt"] + totals["completion"]
        totals["hit_rate"] = (totals["cache_hit"] / totals["prompt"]
                              if totals["prompt"] else 0.0)
        return totals

    def usage_today(self, day: str = "") -> dict:
        """今天的真数：输入 + 输出，成本上限用。"""
        day = str(day or now()[:10])
        row = self.db.execute(
            "SELECT COUNT(*) AS steps, COALESCE(SUM(prompt), 0) AS prompt,"
            " COALESCE(SUM(completion), 0) AS completion FROM usage WHERE ts >= ?",
            (day,)).fetchone()
        prompt = int(row["prompt"])
        completion = int(row["completion"])
        return {"day": day, "steps": int(row["steps"]), "prompt": prompt,
                "completion": completion, "total": prompt + completion}

    # ── 进件（谁说了一句、哪个任务回来了） ────────────────────────────────
    def push(self, kind: str, text: str) -> int:
        """往 inbox 放一条；别的进程也能直接 INSERT 进来，这就是入口。"""
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO inbox (ts, kind, text) VALUES (?, ?, ?)",
                (now(), str(kind or "user"), str(text or "")))
        return int(cursor.lastrowid)

    def take(self, kinds: Optional[List[str]] = None) -> Optional[dict]:
        """取最早一条没读过的，顺手标成已读；kinds 是白名单。"""
        sql = "SELECT id, ts, kind, text FROM inbox WHERE consumed = 0"
        args: list = []
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args = list(kinds)
        sql += " ORDER BY id LIMIT 1"
        row = self.db.execute(sql, args).fetchone()
        if row is None:
            return None
        with self.db:
            self.db.execute("UPDATE inbox SET consumed = 1 WHERE id = ?", (int(row["id"]),))
        return {"id": int(row["id"]), "ts": str(row["ts"]),
                "kind": str(row["kind"]), "text": str(row["text"])}

    def trim_inbox(self, keep: int = 200) -> int:
        """删掉已读的旧进件，只留最近 keep 条已读的：长跑别让这张表一直涨。"""
        with self.db:
            cursor = self.db.execute(
                "DELETE FROM inbox WHERE consumed = 1 AND id NOT IN"
                " (SELECT id FROM inbox WHERE consumed = 1 ORDER BY id DESC LIMIT ?)",
                (max(0, int(keep)),))
        return int(cursor.rowcount or 0)

    # ── 检索记录 ──────────────────────────────────────────────────────────
    def add_recall(self, question: str, event_ids: List[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO recall_log (ts, question, event_ids) VALUES (?, ?, ?)",
                (now(), question, json.dumps(list(event_ids or []), ensure_ascii=False)))
