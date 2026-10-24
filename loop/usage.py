"""用量仪表盘：按天看步数、输入输出、缓存命中率。

    python -m loop.usage --data data/loop --days 7

数据就是 `usage` 表（每步一行，真数来自服务端），不另存汇总。
"""

from __future__ import annotations

import argparse
import os
import sys

from .store import LoopStore


def _k(tokens: int) -> str:
    tokens = int(tokens or 0)
    if tokens < 1000:
        return str(tokens)
    return "%.1fk" % (tokens / 1000.0)


def collect(rows) -> dict:
    """按天汇总；日期取 ts 前 10 位。"""
    days = {}
    for row in rows:
        day = str(row.get("ts") or "")[:10] or "?"
        item = days.setdefault(day, {"steps": 0, "prompt": 0, "completion": 0,
                                     "cache_hit": 0, "peak": 0})
        prompt = int(row.get("prompt") or 0)
        item["steps"] += 1
        item["prompt"] += prompt
        item["completion"] += int(row.get("completion") or 0)
        item["cache_hit"] += int(row.get("cache_hit") or 0)
        item["peak"] = max(item["peak"], prompt)
    for item in days.values():
        item["hit_rate"] = (item["cache_hit"] / item["prompt"]
                            if item["prompt"] else 0.0)
        item["total"] = item["prompt"] + item["completion"]
    return days


def render(days, limit: int = 7) -> str:
    """最近 limit 天 + 合计，终端对齐的一张表。"""
    chosen = sorted(days.items())[-limit:] if limit else sorted(days.items())
    if not chosen:
        return "还没有用量：库是空的。"
    lines = ["日期         步数     输入      输出    命中率   最大一次"]
    totals = {"steps": 0, "prompt": 0, "completion": 0, "cache_hit": 0, "peak": 0}
    for day, item in chosen:
        lines.append("%s  %5d  %8s  %8s  %6.0f%%  %8s" % (
            day, item["steps"], _k(item["prompt"]), _k(item["completion"]),
            100.0 * item["hit_rate"], _k(item["peak"])))
        for key in totals:
            totals[key] += item[key]
    rate = (totals["cache_hit"] / totals["prompt"]) if totals["prompt"] else 0.0
    lines.append("合计          %5d  %8s  %8s  %6.0f%%  %8s" % (
        totals["steps"], _k(totals["prompt"]), _k(totals["completion"]),
        100.0 * rate, _k(totals["peak"])))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="loop.usage", description="循环用量仪表盘")
    parser.add_argument("--data", default="data/loop", help="上下文数据目录")
    parser.add_argument("--days", type=int, default=7, help="看最近几天；0=全部")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass

    db_path = os.path.join(args.data, "loop.sqlite3")
    if not os.path.exists(db_path):
        print("没有库：%s" % os.path.abspath(db_path), file=sys.stderr)
        return 1
    store = LoopStore(db_path)
    try:
        print(render(collect(store.usages()), args.days), flush=True)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
