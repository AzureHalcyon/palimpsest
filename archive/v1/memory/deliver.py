# -*- coding: utf-8 -*-
"""投递闸门 + `deliveries.jsonl` —— **决定"要不要对用户开口"**。

★★ 设计里最重要的一条分界（用户 2026-10-05 指出）
------------------------------------------------
**活跃 ≠ 开口**：`L0/L1` 管的是"它自己的事"（该发生就发生，只受预算约束）；
**这一个文件只管"开口"** —— 默认**不投递**。用户原话：

> 「这和是否开口不是必然相关的……存在『**AI 活跃但用户不可见消息**』和
>  『**主动给用户发消息**』两种情况。」

五道闸门（**同时**满足才投）
----------------------------
| # | 条件 | 默认 |
|---|---|---|
| 1 | ★★ **≥2 个独立信源**支持这个观察 | 硬条件（§3 交叉验证） |
| 2 | 距上次投递 ≥ 冷却 | **2.5 h**（用户：「两三小时一次」） |
| 3 | 在**活跃时段**内 | 8–23 点（可配） |
| 4 | 当日投递数 < 上限 | **5**（走 `budgets.DELIVERY`） |
| 5 | 能说出**来源** | 硬条件 |
| + | 机检：与近 N 条投递**太像** ⇒ 丢 | 字符二元组 Jaccard ≥ 0.6 |

⛔ **不做"现在别说话"**（用户已否）：用户没回就是没回，AI 被唤醒是正常情形。

★ 投递记录是**枢纽**（设计稿 §5）：它同时供"冷却 / 当日上限 / 回话上下文"三处用。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Sequence

COOLDOWN_MS = int(2.5 * 3_600_000)      # ★ 2.5 小时（用户定的"两三小时"）
ACTIVE_FROM, ACTIVE_TO = 8, 23          # ★ 活跃时段（当地小时，含 8 不含 23）
DUP_THRESHOLD = 0.6                     # 与近几条太像就不说（机检）
RECENT_FOR_DUP = 3


def _day(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ms / 1000.0))


def _hour(ms: int) -> int:
    return int(time.strftime("%H", time.localtime(ms / 1000.0)))


def _bigrams(s: str) -> set:
    s = "".join(ch for ch in s if not ch.isspace())
    return {s[i:i + 2] for i in range(max(len(s) - 1, 0))}


def _similar(a: str, b: str) -> float:
    """字符二元组 Jaccard —— 够便宜，也够抓住"同一句话说两遍"。"""
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / float(len(x | y))


class DeliveryLog:
    """投递记录：`{id, ts, day, text, sources, replied}` 一行一条。"""

    MAX_LINES = 500

    def __init__(self, cache_dir: str):
        self.path = os.path.join(cache_dir, "deliveries.jsonl")

    def append(self, text: str, sources: Sequence[str], now_ms: int) -> Dict[str, object]:
        rec = {"id": "d%d" % int(now_ms), "ts": int(now_ms), "day": _day(int(now_ms)),
               "text": str(text)[:500], "sources": [str(s) for s in sources], "replied": False}
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._trim()
        except Exception:                                        # noqa: BLE001
            pass
        return rec

    def _trim(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= self.MAX_LINES:
                return
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(lines[-self.MAX_LINES:])
        except Exception:                                        # noqa: BLE001
            pass

    def recent(self, n: int = 20) -> List[Dict[str, object]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                out = []
                for line in f.readlines()[-max(int(n), 0):]:
                    try:
                        out.append(json.loads(line))
                    except Exception:                            # noqa: BLE001
                        continue
                return out
        except Exception:                                        # noqa: BLE001
            return []

    def last_ts(self) -> Optional[int]:
        rs = self.recent(1)
        return int(rs[-1]["ts"]) if rs else None

    def mark_replied(self, did: str, reply: str = "", now_ms: Optional[int] = None) -> bool:
        """用户回了一句 ⇒ 在那条上打勾，**并把回话存下来**。

        ★ 为什么要存回话（2026-10-05）：光有"已读"没用 ——
          「我看到了」和「我在忙」对下一次开口的意义完全不同。
          存下来它才可能"记得自己上次问了什么、对方怎么答的"。
        ★ 回复本身仍然走**正常对话**那条路（界面里接成一个回合），
          这里只保证**记录不丢**。
        """
        rs = self.recent(self.MAX_LINES)
        hit = False
        for r in rs:
            if r.get("id") == did:
                r["replied"] = True
                r["reply"] = str(reply or "")[:500]
                r["replied_ms"] = int(now_ms if now_ms is not None else time.time() * 1000)
                hit = True
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                for r in rs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            return hit
        except Exception:                                        # noqa: BLE001
            return False

    def context_of(self, did: str) -> Optional[Dict[str, object]]:
        """回话时**要挂的上下文**：它当时说了什么、依据哪几个信源。

        ★ 通知上回一句，不该变成一个"没有前文的问题" ——
          所以把投递 id 带回来，就能把回话接成**正常对话的一个回合**。
        """
        for r in self.recent(self.MAX_LINES):
            if r.get("id") == did:
                return r
        return None


class Gate:
    """五道闸门。★ **纯判断，不投递**（投递由调用方在 `allow` 之后自己做）。"""

    def __init__(self, cache_dir: str, budgets, cooldown_ms: int = COOLDOWN_MS,
                 active_from: int = ACTIVE_FROM, active_to: int = ACTIVE_TO):
        self.log = DeliveryLog(cache_dir)
        self.budgets = budgets
        self.cooldown_ms = int(cooldown_ms)
        self.active_from = int(active_from)
        self.active_to = int(active_to)

    def check(self, text: str, sources: Sequence[str], now_ms: Optional[int] = None):
        """返回 `(能不能投, 为什么)`. ✅ 第一项是 **True** 时才由调用方去投。"""
        from .budgets import DELIVERY

        now = int(now_ms if now_ms is not None else time.time() * 1000)
        srcs = [str(s).strip() for s in (sources or []) if str(s).strip()]

        # ① ★★ 独立信源 ≥ 2（同一个信源说两遍**不算两个**）
        uniq = sorted(set(srcs))
        if len(uniq) < 2:
            return False, "只靠一个信源（%s）—— 不开口" % (uniq[0] if uniq else "没有")
        # ⑤ 能说出来源：至少要有实体内容
        if not str(text or "").strip():
            return False, "没内容可说"
        # ③ 活跃时段
        h = _hour(now)
        if not (self.active_from <= h < self.active_to):
            return False, "不在活跃时段（%d 点）" % h
        # ② 冷却
        last = self.log.last_ts()
        if last is not None and now - last < self.cooldown_ms:
            left = (self.cooldown_ms - (now - last)) / 60000.0
            return False, "冷却中（还要 %.0f 分钟）" % left
        # ④ 当日上限（**先问后扣**：这里只问，投成功才扣）
        if not self.budgets.can(DELIVERY, now_ms=now):
            return False, "今天的次数用完了（≤%d）" % self.budgets.stats(now)["caps"]["delivery"]
        # + 去重：跟最近几条太像就不说
        for r in self.log.recent(RECENT_FOR_DUP):
            sim = _similar(str(text), str(r.get("text") or ""))
            if sim >= DUP_THRESHOLD:
                return False, "跟刚说过的那条太像（%.2f）" % sim
        return True, "ok"

    def deliver(self, text: str, sources: Sequence[str],
                now_ms: Optional[int] = None) -> Dict[str, object]:
        """**过闸门 + 真投递**。⛔ 被拒时**不扣额度、不写记录**。"""
        from .budgets import DELIVERY

        now = int(now_ms if now_ms is not None else time.time() * 1000)
        ok, why = self.check(text, sources, now)
        if not ok:
            return {"sent": False, "why": why}
        if not self.budgets.take(DELIVERY, now_ms=now):
            return {"sent": False, "why": "今天的次数用完了"}
        rec = self.log.append(text, sorted(set(str(s) for s in sources)), now)
        return {"sent": True, "why": "ok", "record": rec,
                "remaining": self.budgets.remaining(DELIVERY, now)}
