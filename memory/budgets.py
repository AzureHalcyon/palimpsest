# -*- coding: utf-8 -*-
"""`budgets.json` —— **三本账里的两本**：模型唤起 · 投递。

用户定的三个数（2026-10-05）
----------------------------
> 「模型唤起上限给个50吧。**投递5次吧**。但**模型自己活动是另一回事**。」

| 账 | 上限 | 花的是 |
|---|---|---|
| **模型唤起**（本文件） | **≤ 50 / 天** | 钱 |
| **投递**（本文件） | **≤ 5 / 天** | ★ **用户的注意力** |
| 主动唤醒（`wakeups.py`，≤10/天） | —— | 那一本管的是"它给自己排了几次闹钟" |
| ★ **它自己的活动** | **不设次数上限** | —— |

★★ **两本必须分开记**（用户那句"模型自己活动是另一回事"的要害）：
"它今天只动了 5 次"和"它今天只说了 5 次"**不是同一个数字** ——
投递额度用完**不该**让它停止活动；反过来，它活动得多**也不该**因此多刷屏。

★ 三条实现约定（照 `wakeups.py`）
--------------------------------
1. **当地日**重置（跟"当日投递 ≤5"用同一把尺子）；
2. **原子 + pid 唯一 tmp**（手机上进程随时可能被杀、两次 L0 可能并发）；
3. ⛔ **`take()` 不够就不扣**（绝不透支）—— 上限要么真管住，要么别写。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional

LLM = "llm"                 # 模型唤起
DELIVERY = "delivery"       # 对用户开口
# ★ 联网搜索的**两本小账**（设计稿 §6.5.3）：搜索比模型唤起更贵、更该克制。
SEARCH = "search"           # L1 摘要
DETAIL = "detail"           # L2 详情（**只有它点名要看**才花）

# ★ 用户定的数，别偷偷改（改要留痕：这是"烦不烦 / 花多少"的唯一旋钮）
CAPS: Dict[str, int] = {LLM: 50, DELIVERY: 5, SEARCH: 10, DETAIL: 5}


def _day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ms / 1000.0))


class BudgetStore:
    """两本账。**纯逻辑、不碰模型** ⇒ 可离线自检。"""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.path = os.path.join(cache_dir, "budgets.json")
        self.day: str = ""
        self.used: Dict[str, int] = {}
        self.load()

    # ------------------------------------------------------------------ 落盘
    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception:                                        # noqa: BLE001
            self.day, self.used = "", {}                          # ★ 读坏当新的一天，不崩
            return
        self.day = str(raw.get("day") or "")
        self.used = {str(k): int(v or 0) for k, v in (raw.get("used") or {}).items()}

    def save(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        tmp = "%s.tmp.%d" % (self.path, os.getpid())              # ★ 并发安全（实测过的坑）
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"day": self.day, "used": self.used}, f, ensure_ascii=False)
        try:
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------------ 日界
    def roll(self, now_ms: Optional[int] = None) -> bool:
        """跨了当地日 ⇒ 两本都归零。返回"是否刚重置"。"""
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        d = _day(now)
        if d != self.day:
            self.day = d
            self.used = {}
            self.save()
            return True
        return False

    # ------------------------------------------------------------------ 记账
    def used_of(self, kind: str, now_ms: Optional[int] = None) -> int:
        self.roll(now_ms)
        return int(self.used.get(kind, 0))

    def remaining(self, kind: str, now_ms: Optional[int] = None) -> int:
        return max(0, CAPS.get(kind, 0) - self.used_of(kind, now_ms))

    def can(self, kind: str, n: int = 1, now_ms: Optional[int] = None) -> bool:
        return self.remaining(kind, now_ms) >= int(n)

    def take(self, kind: str, n: int = 1, now_ms: Optional[int] = None) -> bool:
        """扣一次。★ **不够就返回 False 且不扣**（绝不透支）。"""
        self.roll(now_ms)
        n = int(n)
        if n <= 0:
            return True
        if self.remaining(kind, now_ms) < n:
            return False
        self.used[kind] = int(self.used.get(kind, 0)) + n
        self.save()
        return True

    def refund(self, kind: str, n: int = 1, now_ms: Optional[int] = None) -> None:
        """把额度还回去（比如"说好要唤起，结果调用失败了" ⇒ 不该算它花掉）。"""
        self.roll(now_ms)
        self.used[kind] = max(0, int(self.used.get(kind, 0)) - int(n))
        self.save()

    def stats(self, now_ms: Optional[int] = None) -> Dict[str, object]:
        return {"day": self.day or _day(int(now_ms if now_ms is not None else time.time() * 1000)),
                "caps": dict(CAPS),
                "used": {k: self.used_of(k, now_ms) for k in CAPS},
                "remaining": {k: self.remaining(k, now_ms) for k in CAPS}}
