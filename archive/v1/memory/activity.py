# -*- coding: utf-8 -*-
"""`activity.jsonl` —— **活动记录**：它自己做过什么（★ 可查，但**不推送**）。

设计稿 §5 那条原则：**不可见 ≠ 不可查。**
如果它的活动完全不可见，用户**分不清"它有自己的生活"和"它什么都没做"** —— 那"自主"就是空话。
⇒ 这里存的是"它做过的事"（看了哪个渠道、想到了什么、说了没说），
   由界面**按需翻看**（第 11 项：另开一页）。

★ 三条约定
----------
1. **只记"有事发生"的那些次** —— 每次 L0 都写一行会把记录淹掉（一天几十行"无变化"没人看）；
2. **一行一条 JSONL**，追加写（.jsonl 天然适合"只增不改"的事件流）；
3. **有上限**（默认留最近 2000 条）—— 手机上的文件不许无限长。

⛔ 这里**不放**用户可见的消息 —— 那是**投递记录**（第 10 项）。两本分开：
   `activity` = 它做过什么；`投递` = 它对你说了什么。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

MAX_LINES = 2000


def _day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ms / 1000.0))


class ActivityLog:
    """活动记录。**纯逻辑**（只读写文件）⇒ 可离线自检。"""

    def __init__(self, cache_dir: str):
        self.path = os.path.join(cache_dir, "activity.jsonl")

    def log(self, kind: str, now_ms: Optional[int] = None, **fields) -> Dict[str, object]:
        """记一条。`kind` 是它做了什么（`looked` / `no_change` / `budget_out` / `spoke` …）。"""
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        rec: Dict[str, object] = {"ts": now, "day": _day(now), "kind": str(kind)}
        rec.update(fields)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._trim()
        except Exception:                                        # noqa: BLE001
            pass                        # ★ 记账失败不许把 L1 搞崩：丢一行记录比断一次活动轻
        return rec

    def _trim(self) -> None:
        """留最近 `MAX_LINES` 行（超了才重写，平时不碰文件）。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= MAX_LINES:
                return
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LINES:])
        except Exception:                                        # noqa: BLE001
            pass

    def recent(self, n: int = 50) -> List[Dict[str, object]]:
        """最近 n 条（**给界面翻看用**；坏行跳过，不抛）。"""
        out: List[Dict[str, object]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f.readlines()[-max(int(n), 0):]:
                    try:
                        out.append(json.loads(line))
                    except Exception:                            # noqa: BLE001
                        continue
        except FileNotFoundError:
            return []
        except Exception:                                        # noqa: BLE001
            return []
        return out

    def count_today(self, now_ms: Optional[int] = None, kind: Optional[str] = None) -> int:
        """今天记了几条（带 `kind` 就只数那种）。
        ★ 它就是"**条目数与模型唤起次数对得上**"那条判据要用的数。"""
        d = _day(int(now_ms if now_ms is not None else time.time() * 1000))
        n = 0
        for r in self.recent(MAX_LINES):
            if r.get("day") == d and (kind is None or r.get("kind") == kind):
                n += 1
        return n
