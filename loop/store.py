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
    step INTEGER NOT NULL DEFAULT 0,
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
    consumed INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT '',
    chat_type TEXT NOT NULL DEFAULT 'private',
    chat_id TEXT NOT NULL DEFAULT '',
    chat_name TEXT NOT NULL DEFAULT '',
    sender_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT '',
    policy TEXT NOT NULL DEFAULT 'wake',
    state TEXT NOT NULL DEFAULT 'pending',
    notified INTEGER NOT NULL DEFAULT 0,
    direct INTEGER NOT NULL DEFAULT 0,
    ext_id TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}'
);

-- 每个会话一条：策略（wake / batch / mute）在这里，进件继承它。
CREATE TABLE IF NOT EXISTS chats (
    chat_id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    chat_type TEXT NOT NULL DEFAULT 'private',
    chat_name TEXT NOT NULL DEFAULT '',
    policy TEXT NOT NULL DEFAULT 'wake',
    updated TEXT NOT NULL DEFAULT ''
);

-- 收过的外部编号，只增不删（进件会被 trim 掉，这张留底）：补拉历史靠它去重。
-- 叫 inbox_seen 不叫 inbox_ext：以前迁移建过一个同名索引，撞名起不来。
CREATE TABLE IF NOT EXISTS inbox_seen (
    ext_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT ''
);
"""


class LoopStore:
    def __init__(self, path: str):
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        # 网页那边另开一条连接读同一个库：WAL 让读不挡写，忙等给并发留余量。
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=NORMAL")
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
        if "step" not in entry_columns:
            # 旧库的原文不知道是哪一步写的；补 0，网页上归到「没有步号」那一档。
            self.db.execute(
                "ALTER TABLE entries ADD COLUMN step INTEGER NOT NULL DEFAULT 0")
        inbox_columns = {row[1] for row in self.db.execute("PRAGMA table_info(inbox)")}
        had_direct = "direct" in inbox_columns
        for name, ddl in (
                ("source", "TEXT NOT NULL DEFAULT ''"),
                ("chat_type", "TEXT NOT NULL DEFAULT 'private'"),
                ("chat_id", "TEXT NOT NULL DEFAULT ''"),
                ("chat_name", "TEXT NOT NULL DEFAULT ''"),
                ("sender_id", "TEXT NOT NULL DEFAULT ''"),
                ("sender_name", "TEXT NOT NULL DEFAULT ''"),
                ("policy", "TEXT NOT NULL DEFAULT 'wake'"),
                ("state", "TEXT NOT NULL DEFAULT 'pending'"),
                ("notified", "INTEGER NOT NULL DEFAULT 0"),
                ("direct", "INTEGER NOT NULL DEFAULT 0"),
                ("ext_id", "TEXT NOT NULL DEFAULT ''"),
                ("meta", "TEXT NOT NULL DEFAULT '{}'")):
            if name not in inbox_columns:
                self.db.execute("ALTER TABLE inbox ADD COLUMN %s %s" % (name, ddl))
        if "ext_id" not in inbox_columns:
            # 老的通道进件把外部编号存在 meta.message_id 里：搬到列上，
            # 补拉历史时才认得出"这条已经收过"。
            try:
                self.db.execute(
                    "UPDATE inbox SET ext_id = COALESCE(json_extract(meta, '$.message_id'), '')"
                    " WHERE ext_id = '' AND meta LIKE '%message_id%'")
            except sqlite3.OperationalError:
                pass                  # 老 sqlite 没带 JSON1：那就没有可比对的老编号
        if not had_direct:
            # 加 direct 那一趟顺手把旧的「没来源」补成网页：那时候只有页面的 /say
            # 会这么写（标准输入没在这儿开过），补上标签仪表盘才说得清是谁说的。
            with self.db:
                self.db.execute("UPDATE inbox SET direct = 1 WHERE source = ''")
                self.db.execute("UPDATE inbox SET source = '网页' WHERE source = ''")
        # 编号台账：进件会被 trim 掉，收过哪些编号得留底（补拉靠它去重）。
        # 每次都跑：新建库也要有，老库也要补上历史。
        with self.db:
            self.db.execute("DROP INDEX IF EXISTS inbox_ext")   # 旧版本留下的空索引
            self.db.execute(
                "INSERT OR IGNORE INTO inbox_seen (ext_id, ts)"
                " SELECT source || ':' || ext_id, ts FROM inbox"
                " WHERE ext_id != '' AND ext_id NOT LIKE '%:%'")

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
                "SELECT seq, role, content, tool_calls, tool_call_id, ts, meta"
                " FROM entries WHERE block_id = ? ORDER BY seq", (block_id,)):
            message = {"role": str(item["role"]),
                       "content": str(item["content"] or "")}
            calls = json.loads(item["tool_calls"] or "[]")
            if calls:
                message["tool_calls"] = calls
            if str(item["tool_call_id"] or ""):
                message["tool_call_id"] = str(item["tool_call_id"])
            entries.append(BlockEntry(message, str(item["ts"]),
                                      json.loads(item["meta"] or "{}"),
                                      int(item["seq"])))
        return Block(entries, str(row["start_ts"]), str(row["end_ts"]))

    def open_block(self, start_ts: str) -> int:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO blocks (start_ts, state) VALUES (?, 'open')", (start_ts,))
        return int(cursor.lastrowid)

    def append_entry(self, block_id: int, entry: BlockEntry, step: int = 0) -> None:
        message = entry.message
        seq = int(self.db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM entries WHERE block_id = ?",
            (block_id,)).fetchone()[0])
        with self.db:
            self.db.execute(
                "INSERT INTO entries (block_id, seq, role, content, tool_calls,"
                " tool_call_id, ts, meta, step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (block_id, seq, str(message.get("role") or ""),
                 str(message.get("content") or ""),
                 json.dumps(list(message.get("tool_calls") or []), ensure_ascii=False),
                 str(message.get("tool_call_id") or ""), entry.ts,
                 json.dumps(dict(entry.meta or {}), ensure_ascii=False),
                 int(step or 0)))

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

    def purge_block_entries(self, block_id: int) -> int:
        """把一块的原文从库里删掉（搬进归档文件之后）。

        块行留着：摘要、时间、关键词还在，网页按块分段的骨架不会塌；
        删掉的只有原文那几行——`load_state` 只读 `previous` 与 `open` 两块，
        所以运行起来一点不受影响。返回删了几条。
        """
        with self.db:
            cursor = self.db.execute("DELETE FROM entries WHERE block_id = ?",
                                     (int(block_id),))
        return int(cursor.rowcount or 0)

    # ── 网页：增量流与状态 ────────────────────────────────────────────────
    def feed(self, since_block: int = 0, since_seq: int = 0,
             limit: int = 300) -> List[dict]:
        """按 (block_id, seq) 增量读原文：网页断线重连靠它补齐，不重不漏。"""
        rows = self.db.execute(
            "SELECT block_id, seq, role, content, tool_calls, tool_call_id, ts,"
            " meta, step FROM entries"
            " WHERE block_id > ? OR (block_id = ? AND seq > ?)"
            " ORDER BY block_id, seq LIMIT ?",
            (int(since_block), int(since_block), int(since_seq),
             max(1, int(limit)))).fetchall()
        return [{"block": int(row["block_id"]), "seq": int(row["seq"]),
                 "role": str(row["role"]), "text": str(row["content"] or ""),
                 "calls": json.loads(row["tool_calls"] or "[]"),
                 "call_id": str(row["tool_call_id"] or ""),
                 "ts": str(row["ts"]), "meta": json.loads(row["meta"] or "{}"),
                 "step": int(row["step"])} for row in rows]

    def usage_since(self, since_step: int = 0, limit: int = 300) -> List[dict]:
        rows = self.db.execute(
            "SELECT step, ts, prompt, completion, cache_hit, cache_miss, reasoning"
            " FROM usage WHERE step > ? ORDER BY step LIMIT ?",
            (int(since_step), max(1, int(limit)))).fetchall()
        return [{"step": int(row["step"]), "ts": str(row["ts"]),
                 "prompt": int(row["prompt"]), "completion": int(row["completion"]),
                 "cache_hit": int(row["cache_hit"]),
                 "cache_miss": int(row["cache_miss"]),
                 "reasoning": int(row["reasoning"])} for row in rows]

    def set_status(self, kind: str, detail: str = "") -> None:
        """它现在在干什么：想 / 等 / 挂起 / 重试。网页读这一行，不碰运行时内存。"""
        value = json.dumps({"kind": str(kind), "detail": str(detail), "ts": now()},
                           ensure_ascii=False)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO state (key, value) VALUES ('status', ?)", (value,))

    def status(self) -> dict:
        row = self.db.execute(
            "SELECT value FROM state WHERE key = 'status'").fetchone()
        if row is None:
            return {"kind": "", "detail": "", "ts": ""}
        try:
            data = json.loads(row["value"])
        except ValueError:
            return {"kind": "", "detail": "", "ts": ""}
        return {"kind": str(data.get("kind") or ""),
                "detail": str(data.get("detail") or ""),
                "ts": str(data.get("ts") or "")}

    def delete_entry(self, block_id: int, seq: int) -> List[int]:
        """删一条原文，连它的配对一起删（调用删结果、结果删调用）。

        只允许改 open / previous 两块：已压缩的块，原文和摘要是一份东西，
        删了原文摘要还在，只会更乱。
        """
        row = self.db.execute(
            "SELECT state FROM blocks WHERE id = ?", (block_id,)).fetchone()
        if row is None:
            raise ValueError("没有这一块")
        if str(row["state"]) == "compressed":
            raise ValueError("这一块已经压成摘要了，删原文没有意义")
        target = self.db.execute(
            "SELECT seq, tool_call_id, tool_calls FROM entries"
            " WHERE block_id = ? AND seq = ?", (block_id, seq)).fetchone()
        if target is None:
            raise ValueError("没有这一条")
        doomed = {int(seq)}
        ids = [str(call.get("id") or "")
               for call in json.loads(target["tool_calls"] or "[]")]
        if ids:
            rows = self.db.execute(
                "SELECT seq FROM entries WHERE block_id = ? AND tool_call_id IN (%s)"
                % ",".join("?" * len(ids)), (block_id, *ids)).fetchall()
            doomed.update(int(item["seq"]) for item in rows)
        call_id = str(target["tool_call_id"] or "")
        if call_id:
            for item in self.db.execute(
                    "SELECT seq, tool_calls FROM entries WHERE block_id = ?",
                    (block_id,)).fetchall():
                if any(str(call.get("id") or "") == call_id
                       for call in json.loads(item["tool_calls"] or "[]")):
                    doomed.add(int(item["seq"]))
        with self.db:
            self.db.execute(
                "DELETE FROM entries WHERE block_id = ? AND seq IN (%s)"
                % ",".join("?" * len(doomed)), (block_id, *sorted(doomed)))
        return sorted(doomed)

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

    def usage_live(self) -> dict:
        """只算还挂在记录上的步：删掉一步的最后一条，这一步就从这里退出去。

        接缝是 `entries.step` 与 `usage.step`。老条目没有步号（记 0），它们真实的
        花费只出现在 `usage_totals` 里；网页把这一份放在输入框下面（跟着删除退），
        `usage_totals` 放在设置里当总账（钱已经花了，不退）。
        """
        row = self.db.execute(
            "SELECT COUNT(*) AS steps, COALESCE(SUM(prompt), 0) AS prompt,"
            " COALESCE(SUM(completion), 0) AS completion,"
            " COALESCE(SUM(cache_hit), 0) AS cache_hit"
            " FROM usage WHERE step IN"
            " (SELECT DISTINCT step FROM entries WHERE step > 0)").fetchone()
        live = {"steps": int(row["steps"]), "prompt": int(row["prompt"]),
                "completion": int(row["completion"]),
                "cache_hit": int(row["cache_hit"])}
        live["total"] = live["prompt"] + live["completion"]
        live["hit_rate"] = (live["cache_hit"] / live["prompt"]
                            if live["prompt"] else 0.0)
        return live

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
    def push(self, kind: str, text: str, policy: str = "", source: str = "",
             chat_type: str = "", chat_id: str = "", chat_name: str = "",
             sender_id: str = "", sender_name: str = "", meta=None,
             direct: Optional[bool] = None, ext_id: str = "") -> int:
        """往 inbox 放一条。

        别的进程也能直接 INSERT 进来（网关的 `/inbox` 就是这么做的），这就是入口。
        `policy` 不传就继承这个会话在 `chats` 里的策略；会话没登记过就按 wake。

        `source` 是"从哪儿来的"，照实写（网页 / 命令行 / QQ / …）——它只是个标签，
        给她看、给网页看，核心不认识具体是哪个通道。
        `direct` 是投递方式：正文直接进记录（人就在这一头跟她说话）。不传就按来源
        推断——没有 source 的只能是人在这头说话；有 source 的走响铃那条路。
        通道转进来的（direct 关）要先响铃、正文等她 `messages` 来取。
        `ext_id` 是外头那条消息自己的编号，带通道前缀（`QQ:12345`）：同一个编号
        只收一次，补拉历史、重连补漏都靠它去重；重复的返回原来那条的 id（0 = 编号
        收过但那条进件已经被 trim 掉了）。
        """
        ext = str(ext_id or "")
        if ext and self.inbox_has_ext(ext):
            row = self.db.execute("SELECT id FROM inbox WHERE ext_id = ? LIMIT 1",
                                  (ext,)).fetchone()
            return int(row["id"]) if row is not None else 0
        chat = str(chat_id or "")
        if direct is None:
            direct = not str(source or "").strip()
        policy = str(policy or "").strip().lower()
        if chat:
            self._remember_chat(str(source or ""), str(chat_type or "private"),
                                chat, str(chat_name or ""))
            if not policy:
                policy = self.chat_policy(chat)
        if not policy:
            policy = "wake"
        if policy not in ("wake", "batch", "mute"):
            policy = "wake"
        stamp = now()
        with self.db:
            if ext:
                # 在同一个事务里抢台账：抢不到说明别人刚插过（并发补拉 + 实时事件），
                # 回原来那条——"先查后插"中间那一下正好是重复的来源。
                won = self.db.execute(
                    "INSERT OR IGNORE INTO inbox_seen (ext_id, ts) VALUES (?, ?)",
                    (ext, stamp)).rowcount
                if not won:
                    row = self.db.execute(
                        "SELECT id FROM inbox WHERE ext_id = ? LIMIT 1",
                        (ext,)).fetchone()
                    return int(row["id"]) if row is not None else 0
            cursor = self.db.execute(
                "INSERT INTO inbox (ts, kind, text, source, chat_type, chat_id,"
                " chat_name, sender_id, sender_name, policy, direct, ext_id, meta)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (stamp, str(kind or "user"), str(text or ""), str(source or ""),
                 str(chat_type or "private"), chat, str(chat_name or ""),
                 str(sender_id or ""), str(sender_name or ""), policy,
                 1 if direct else 0, ext,
                 json.dumps(meta or {}, ensure_ascii=False)))
            if not direct and str(source or "").strip():
                # "外部一共来过多少"要只增：进件会被 trim 掉，数行会越数越少
                self.db.execute(
                    "INSERT INTO state (key, value) VALUES ('inbox_outside', '1')"
                    " ON CONFLICT(key) DO UPDATE SET"
                    " value = CAST(value AS INTEGER) + 1")
        return int(cursor.lastrowid)

    def inbox_has_ext(self, ext_id: str) -> bool:
        """这个外部编号收过没有：补拉历史先问一句，省得白插一条。

        台账（`inbox_seen`）只增不删，进件被 trim 掉也不会忘。
        """
        ext = str(ext_id or "")
        if not ext:
            return False
        if self.db.execute("SELECT 1 FROM inbox_seen WHERE ext_id = ? LIMIT 1",
                           (ext,)).fetchone() is not None:
            return True
        return self.db.execute("SELECT 1 FROM inbox WHERE ext_id = ? LIMIT 1",
                               (ext,)).fetchone() is not None

    def _remember_chat(self, source: str, chat_type: str, chat_id: str,
                       chat_name: str) -> None:
        """登记一个会话（没有就建，有就更新来源与名字，策略不动）。

        新会话的默认策略按类型分：**群默认 batch**（让她自己去看，别一句一句叫醒），
        私聊默认 wake。想改就在 `channels_set` 或网页上改，之后不会被这里覆盖。
        """
        default = "batch" if str(chat_type) == "group" else "wake"
        with self.db:
            self.db.execute(
                "INSERT INTO chats (chat_id, source, chat_type, chat_name, policy, updated)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(chat_id) DO UPDATE SET"
                " source = excluded.source, chat_type = excluded.chat_type,"
                " chat_name = CASE WHEN excluded.chat_name != ''"
                "                  THEN excluded.chat_name ELSE chats.chat_name END,"
                " updated = excluded.updated",
                (str(chat_id), str(source), str(chat_type), str(chat_name),
                 default, now()))

    def chat_policy(self, chat_id: str) -> str:
        row = self.db.execute("SELECT policy FROM chats WHERE chat_id = ?",
                              (str(chat_id),)).fetchone()
        return str(row["policy"]) if row else "wake"

    def has_pending(self, kinds: Optional[List[str]] = None,
                    policies: Optional[List[str]] = None) -> bool:
        """有没有还没读的进件——启动时用它判断「该不该开口」。"""
        sql = "SELECT 1 FROM inbox WHERE consumed = 0"
        args: list = []
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        if policies:
            sql += " AND policy IN (%s)" % ",".join("?" * len(policies))
            args += list(policies)
        sql += " LIMIT 1"
        return self.db.execute(sql, args).fetchone() is not None

    def peek(self, kinds: Optional[List[str]] = None,
             policies: Optional[List[str]] = None,
             unnotified: bool = False) -> Optional[dict]:
        """看最早一条没读过的，**不**标已读；kinds / policies 是白名单。

        分开 peek/consume 是为了不让消息丢：先把它落进记录，再标已读。
        以前 take() 一步就标已读，而记录要等到这一步的模型调用之后才写——
        中途重启（改代码、崩了）就会出现"人说了话，进了进件，但记录里没有"。
        """
        sql = "SELECT * FROM inbox WHERE consumed = 0"
        args: list = []
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += list(kinds)
        if policies:
            sql += " AND policy IN (%s)" % ",".join("?" * len(policies))
            args += list(policies)
        if unnotified:
            sql += " AND notified = 0"
        sql += " ORDER BY id LIMIT 1"
        row = self.db.execute(sql, args).fetchone()
        return self._item(row) if row is not None else None

    def mark_notified(self, item_id: int) -> None:
        """响过铃了：不重复响，但正文还没被读走（consumed 仍为 0）。"""
        with self.db:
            self.db.execute("UPDATE inbox SET notified = 1 WHERE id = ?",
                            (int(item_id),))

    def consume(self, item_id: int) -> None:
        """标已读：只在这条消息真的写进记录（或被她读到）之后调。"""
        with self.db:
            self.db.execute(
                "UPDATE inbox SET consumed = 1, state = 'surfaced' WHERE id = ?",
                (int(item_id),))

    def consume_many(self, ids) -> int:
        ids = [int(item) for item in (ids or [])]
        if not ids:
            return 0
        with self.db:
            cursor = self.db.execute(
                "UPDATE inbox SET consumed = 1, state = 'surfaced'"
                " WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
        return int(cursor.rowcount or 0)

    def inbox_in_record(self, item_id: int) -> bool:
        """这条进件已经在记录里了吗（直接插成 user，或作为工具结果被读走）。"""
        try:
            wanted = int(item_id)
        except (TypeError, ValueError):
            return False
        if not wanted:
            return False
        for row in self.db.execute(
                "SELECT meta FROM entries WHERE meta LIKE ?", ("%%%d%%" % wanted,)):
            try:
                meta = json.loads(row["meta"] or "{}")
            except ValueError:
                continue
            if int(meta.get("inbox_id") or 0) == wanted:
                return True
            for ident in (meta.get("inbox_ids") or []):
                try:
                    if int(ident) == wanted:
                        return True
                except (TypeError, ValueError):
                    continue
        return False

    def take(self, kinds: Optional[List[str]] = None) -> Optional[dict]:
        """peek + consume（自检和旧调用用）；正式路径请用 peek 再 consume。"""
        item = self.peek(kinds)
        if item is not None:
            self.consume(item["id"])
        return item

    def inbox_unappended(self, limit: int = 20, since: str = "") -> list:
        """已读、但记录里找不到的进件（按 `meta.inbox_id` / `meta.inbox_ids` 对账）。

        `messages` 读完的那几条正文是作为**一条工具结果**进记录的（编号记在
        `inbox_ids` 里），跟"直接插成一条 user"一样算落库——不然重启时会被当成
        丢了，又插一条 user 消息。

        从新往旧找（`since` 之外的老账不翻）：先按 id 升序取 20 条的话，攒够一批
        永远补不了的旧条目，新出现的洞就再也排不上了。
        """
        seen = set()
        for row in self.db.execute("SELECT meta FROM entries WHERE meta LIKE '%inbox_id%'"):
            try:
                meta = json.loads(row["meta"] or "{}")
            except ValueError:
                continue
            if meta.get("inbox_id"):
                seen.add(int(meta["inbox_id"]))
            for ident in (meta.get("inbox_ids") or []):
                try:
                    seen.add(int(ident))
                except (TypeError, ValueError):
                    continue
        sql = "SELECT * FROM inbox WHERE consumed = 1 AND direct = 0"
        args: list = []
        if since:
            sql += " AND ts >= ?"
            args.append(str(since))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        out = []
        for row in self.db.execute(sql, args):
            if int(row["id"]) in seen:
                continue
            out.append(self._item(row))
        return list(reversed(out))

    def trim_inbox(self, keep: int = 200, keep_ext: int = 5000) -> int:
        """删掉旧进件，别让这张表一直涨。

        - 已读的留最近 `keep` 条；
        - 设成 mute 的（她永远不会读了）另算一份，同样留最近 `keep` 条，
          否则它们既不会被读走、也永远删不掉；
        - 编号台账（`inbox_seen`）留最近 `keep_ext` 条：台账是去重用的，删干净了
          补拉历史就会重复投递，但也不能无限长。
        """
        removed = 0
        with self.db:
            cursor = self.db.execute(
                "DELETE FROM inbox WHERE consumed = 1 AND id NOT IN"
                " (SELECT id FROM inbox WHERE consumed = 1 ORDER BY id DESC LIMIT ?)",
                (max(0, int(keep)),))
            removed += int(cursor.rowcount or 0)
            cursor = self.db.execute(
                "DELETE FROM inbox WHERE consumed = 0 AND policy = 'mute' AND id NOT IN"
                " (SELECT id FROM inbox WHERE consumed = 0 AND policy = 'mute'"
                "  ORDER BY id DESC LIMIT ?)", (max(0, int(keep)),))
            removed += int(cursor.rowcount or 0)
            self.db.execute(
                "DELETE FROM inbox_seen WHERE rowid NOT IN"
                " (SELECT rowid FROM inbox_seen ORDER BY rowid DESC LIMIT ?)",
                (max(0, int(keep_ext)),))
        return removed

    # ── 攒着的那些（batch）：计数、会话、读回来 ──────────────────────────────
    def unread_batches(self, chats: int = 5, senders: int = 3) -> dict:
        """batch 积压的概况：总共几条、每个会话几条、谁说得最多。

        只给数字和名字，不给正文——正文要她自己调 `messages` 才拿。
        """
        rows = self.db.execute(
            "SELECT source, chat_type, chat_id, chat_name, sender_name, COUNT(*) AS n"
            " FROM inbox WHERE consumed = 0 AND policy != 'mute'"
            " GROUP BY chat_id, sender_name ORDER BY chat_id, n DESC").fetchall()
        total = 0
        grouped: dict = {}
        for row in rows:
            count = int(row["n"])
            total += count
            key = str(row["chat_id"] or "")
            entry = grouped.setdefault(key, {
                "chat_id": key, "source": str(row["source"] or ""),
                "chat_type": str(row["chat_type"] or "private"),
                "chat_name": str(row["chat_name"] or ""), "count": 0, "senders": []})
            entry["count"] += count
            if len(entry["senders"]) < senders and row["sender_name"]:
                entry["senders"].append("%s %d" % (str(row["sender_name"]), count))
        order = sorted(grouped.values(), key=lambda item: -item["count"])
        return {"total": total, "chats": order[:chats], "chat_total": len(grouped)}

    def channels(self) -> list:
        """所有登记过的会话 + 未读数。

        `unread` 与 `unread_batches()` 一个口径：`consumed = 0 且策略不是 mute`
        —— 设成 mute 的消息她自己根本读不到，不能算在"还有几条没看"里。
        """
        out = []
        for row in self.db.execute(
                "SELECT * FROM chats ORDER BY updated DESC"):
            chat_id = str(row["chat_id"])
            unread = self.db.execute(
                "SELECT COUNT(*) AS n FROM inbox"
                " WHERE consumed = 0 AND policy != 'mute' AND chat_id = ?",
                (chat_id,)).fetchone()
            out.append({
                "chat_id": chat_id, "source": str(row["source"] or ""),
                "chat_type": str(row["chat_type"] or "private"),
                "chat_name": str(row["chat_name"] or ""),
                "policy": str(row["policy"] or "wake"),
                "unread": int(unread["n"]) if unread else 0,
                "updated": str(row["updated"] or ""),
            })
        return out

    def set_policy(self, chat_id: str, policy: str) -> int:
        """改一个会话的策略；还没读的进件跟着改。

        返回"跟着改的未读条数"（0 是正常的：这个会话没有未读）。
        没有这个会话的时候抛 KeyError——"改了 0 条"和"压根没这个会话"
        是两件事，别让调用方猜。
        """
        policy = str(policy or "").strip().lower()
        if policy not in ("wake", "batch", "mute"):
            raise ValueError("policy 只认 wake / batch / mute：%s" % policy)
        row = self.db.execute("SELECT 1 FROM chats WHERE chat_id = ?",
                              (str(chat_id),)).fetchone()
        if row is None:
            raise KeyError(str(chat_id))
        with self.db:
            self.db.execute(
                "UPDATE chats SET policy = ?, updated = ? WHERE chat_id = ?",
                (policy, now(), str(chat_id)))
            cursor = self.db.execute(
                "UPDATE inbox SET policy = ? WHERE chat_id = ? AND consumed = 0",
                (policy, str(chat_id)))
        return int(cursor.rowcount or 0)

    def inbox_recent(self, limit: int = 60) -> list:
        """最近的进件，不管读没读、什么策略——给网页当仪表盘看。

        她自己只看得见 `messages` 里还没读的那些；这一条是**给人看**的视角：
        外部说过的话、哪条还在等她、哪条已经并进记录了，一眼能对上。
        """
        rows = self.db.execute("SELECT * FROM inbox ORDER BY id DESC LIMIT ?",
                               (max(1, int(limit)),)).fetchall()
        out = []
        for row in reversed(rows):
            item = self._item(row)
            for key in ("notified", "consumed"):
                try:
                    item[key] = int(row[key])
                except (IndexError, KeyError, TypeError):
                    item[key] = 0
            out.append(item)
        return out

    def inbox_counts(self) -> dict:
        """大盘上的两个数：还在等她读的、从外面进来过的总数。

        `waiting` 与 `unread_batches()` 同口径（mute 不算——她读不到）；
        `outside` 数的是"历来从外面进来过多少"，进件会被 trim 掉，
        所以拿 push 时累加的计数，别去数行。
        """
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM inbox"
            " WHERE consumed = 0 AND policy != 'mute'").fetchone()
        waiting = int(row["n"]) if row else 0
        row = self.db.execute("SELECT value FROM state WHERE key = 'inbox_outside'").fetchone()
        if row is None:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM inbox WHERE source != '' AND direct = 0").fetchone()
            outside = int(row["n"]) if row else 0
            # 第一次问就把底数记下来：不然下一条消息会把计数器从 1 重新起算
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO state (key, value)"
                    " VALUES ('inbox_outside', ?)", (str(outside),))
        else:
            outside = int(str(row["value"]) or 0)
        return {"waiting": waiting, "outside": outside}

    def messages(self, chat_id: str = "", limit: int = 20, before: int = 0) -> list:
        """把攒着的读回来：按时间正序，最多 limit 条，可往前翻页（before=id）。

        点名某个会话时不排除 mute：策略是"不主动提示"，不是"锁起来"——
        她自己想起来要看，就该看得到。
        """
        sql = "SELECT * FROM inbox WHERE consumed = 0"
        args: list = []
        if chat_id:
            sql += " AND chat_id = ?"
            args.append(str(chat_id))
        else:
            sql += " AND policy != 'mute'"
        if before:
            sql += " AND id < ?"
            args.append(int(before))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        rows = self.db.execute(sql, args).fetchall()
        return [self._item(row) for row in reversed(rows)]

    @staticmethod
    def _item(row) -> dict:
        """一行进件 → 字典；旧库没有的列按默认值兜住。"""
        item = {"id": int(row["id"]), "ts": str(row["ts"]),
                "kind": str(row["kind"]), "text": str(row["text"])}
        for key, fallback in (("source", ""), ("chat_type", "private"),
                              ("chat_id", ""), ("chat_name", ""),
                              ("sender_id", ""), ("sender_name", ""),
                              ("policy", "wake"), ("state", "pending"),
                              ("ext_id", "")):
            try:
                item[key] = str(row[key])
            except (IndexError, KeyError):
                item[key] = fallback
        try:
            item["meta"] = json.loads(row["meta"] or "{}")
        except (IndexError, KeyError, TypeError, ValueError):
            item["meta"] = {}
        # 旧库没这一列、或者调用方压根没写来源：都按"本机直投"算（以前就是这么判的）
        try:
            stored = int(row["direct"] or 0)
        except (IndexError, KeyError, TypeError, ValueError):
            stored = 0
        # 只认列值：写入时已经归一（没来源的就是直投）。这里再"按有没有来源"
        # 猜一遍，会和 SQL 那边的口径打架。
        item["direct"] = stored
        return item

    # ── 检索记录 ──────────────────────────────────────────────────────────
    def add_recall(self, question: str, event_ids: List[str]) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO recall_log (ts, question, event_ids) VALUES (?, ?, ?)",
                (now(), question, json.dumps(list(event_ids or []), ensure_ascii=False)))
