# -*- coding: utf-8 -*-
"""`wakeups.json` —— **主动唤醒**队列：模型给自己排一个"到点再看一眼"。

两类唤醒（设计稿 §1.5）
----------------------
· **被动唤醒**：程序发起（WorkManager 轮询 / 开机 / 蓝牙连断 / 他的闹钟变了）。
  那是宿主的事，**不在这个文件里**。
· ★ **主动唤醒**：**模型自己**发起 —— 就是这里。它说"我两小时后想再看看"，
  我们记下来；到点了由宿主把它叫起来。

★ 为什么要给上限（用户明确要求："**也给模型自己一个把自己唤醒的接口，但给出次数限制**"）
------------------------------------------------------------------------------
没有上限就会**自激**：它醒来 → 又排一个"1 分钟后" → 无限烧钱。
所以 `MIN_GAP_MS` 不是体验参数，**是安全阀**。

四条规矩
--------
| 规矩 | 值 | 防什么 |
|---|---|---|
| ★ 最小间隔 | **≥ 15 min** | **自激循环**（安全阀） |
| 每天主动唤醒 | **≤ 10** | 它把自己的额度吃光、把被动那份也挤没 |
| 同时挂着 | **≤ 3** | 排一堆 |
| 同刻去重 | **± 2 min** | 反复排同一个时间 |
| `why` 必填 | —— | ★ 进**活动记录**，用户翻得到"它为什么那时候醒" |

两条实现约定
------------
1. **用墙钟毫秒**，不用引擎钟（`clock`）。
   理由：Android 的调度器（WorkManager/AlarmManager）本来就是墙钟；
   引擎钟只服务池子衰减那套"过了几场"的语义。两者混用会把"两小时后"算错。
2. **写盘是原子的**（临时文件 + `os.replace`），照 `pending.py` 的规矩 ——
   手机上进程被 HyperOS 随手 kill 是常态，写坏一半的文件不能留在那个位置。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------- 四条上限
MIN_GAP_MS = 15 * 60 * 1000          # ★ 最小间隔 15 min（安全阀，防自激）
MAX_SCHEDULED_PER_DAY = 10           # ★ 每天主动唤醒 ≤10（排一次最多醒一次 ⇒ 也管住了唤醒次数）
MAX_PENDING = 3                      # ★ 同时挂着 ≤3
DEDUPE_MS = 2 * 60 * 1000            # 同一时刻 ±2 min 视为同一条
MAX_WHY_LEN = 200                    # `why` 过长没意义，截断（但要留够信息）


def _day_of(ms: int) -> str:
    """当地日期 `YYYY-MM-DD`。★ 用**当地日**而不是滚动 24h：
    "每天 ≤10"要跟"当日投递 ≤5"用同一把尺子，半夜重置比滚动窗口好解释。"""
    return time.strftime("%Y-%m-%d", time.localtime(ms / 1000.0))


@dataclass
class Wakeup:
    id: str
    at_ms: int                       # 到点时刻（墙钟毫秒）
    why: str                         # ★ 必填 —— 进活动记录
    created_ms: int
    day: str                         # 排它那天的当地日期（算"每天 ≤10"）
    fired: bool = False              # 已经醒过了
    cancelled: bool = False          # 被它自己撤了


class WakeupStore:
    """主动唤醒队列。**纯逻辑、不碰模型、不碰 Android** ⇒ 可以离线自检。"""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.path = os.path.join(cache_dir, "wakeups.json")
        self.items: List[Wakeup] = []
        self._seq = 0
        self.load()

    # ------------------------------------------------------------------ 落盘
    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception:
            # ★ 读坏不当崩：唤醒队列丢了不会伤记忆，最坏是它少醒一次。
            self.items, self._seq = [], 0
            return
        self._seq = int(raw.get("seq") or 0)
        self.items = []
        for d in raw.get("items") or []:
            try:
                self.items.append(Wakeup(
                    id=str(d["id"]), at_ms=int(d["at_ms"]), why=str(d.get("why") or ""),
                    created_ms=int(d.get("created_ms") or 0), day=str(d.get("day") or ""),
                    fired=bool(d.get("fired")), cancelled=bool(d.get("cancelled"))))
            except Exception:
                continue

    def save(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        # ★ 唯一 tmp 名：与 `channels.py` 同一个理由 —— 两处并发落盘时，
        #   共用 `path + ".tmp"` 会让其中一个 rename 找不到文件（手机实测过）。
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"seq": self._seq, "items": [asdict(w) for w in self.items]},
                      f, ensure_ascii=False, indent=1)
        try:
            os.replace(tmp, self.path)      # ★ 原子替换
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------------ 内部
    def _pending(self, now_ms: int) -> List[Wakeup]:
        return [w for w in self.items if not w.fired and not w.cancelled and w.at_ms > now_ms]

    def _scheduled_today(self, now_ms: int) -> int:
        d = _day_of(now_ms)
        return len([w for w in self.items if w.day == d and not w.cancelled])

    # ------------------------------------------------------------------ 排
    def schedule(self, *, after_minutes: Optional[float] = None, at_ms: Optional[int] = None,
                 why: str = "", now_ms: Optional[int] = None) -> Dict[str, object]:
        """排一个主动唤醒。**被拒也要正常返回**（`ok:false` + `reason`），不抛异常。

        为什么被拒不抛：这个函数将来是给模型当工具用的，
        跨进程/跨语言的异常会让它"以为工具坏了"，而它需要的是一条**能读懂的理由**
        （"太近了，最短 15 分钟"）好去换个时间重排。
        """
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        why = (why or "").strip()
        if not why:
            return {"ok": False, "reason": "why 必填：得记下你为什么想在那时候醒"}
        why = why[:MAX_WHY_LEN]

        if at_ms is None:
            if after_minutes is None:
                return {"ok": False, "reason": "要么给 after_minutes，要么给 at_ms"}
            at = now + int(float(after_minutes) * 60_000)
        else:
            at = int(at_ms)

        # ① ★ 最小间隔 —— 安全阀
        if at - now < MIN_GAP_MS:
            return {"ok": False, "reason": "太近了：两次之间至少 %d 分钟（防自激）" % (MIN_GAP_MS // 60000)}
        # ② 同刻去重（幂等：把已有那条的 id 还给它，不新建）
        for w in self._pending(now):
            if abs(w.at_ms - at) <= DEDUPE_MS:
                return {"ok": False, "reason": "同一时刻已经排过了（±2 分钟算同一条）",
                        "existing_id": w.id, "at_ms": w.at_ms}
        # ③ 同时挂着 ≤3
        pend = self._pending(now)
        if len(pend) >= MAX_PENDING:
            return {"ok": False, "reason": "同时最多挂 %d 条，先撤一条再排" % MAX_PENDING,
                    "pending_ids": [w.id for w in pend]}
        # ④ 每天 ≤10
        used = self._scheduled_today(now)
        if used >= MAX_SCHEDULED_PER_DAY:
            return {"ok": False, "reason": "今天主动唤醒的次数用完了（上限 %d）" % MAX_SCHEDULED_PER_DAY}

        self._seq += 1
        w = Wakeup(id=str(self._seq), at_ms=at, why=why, created_ms=now, day=_day_of(now))
        self.items.append(w)
        self.save()
        return {"ok": True, "id": w.id, "at_ms": at, "why": why,
                "remaining_today": MAX_SCHEDULED_PER_DAY - (used + 1)}

    def cancel(self, wid: str) -> Dict[str, object]:
        for w in self.items:
            if w.id == str(wid) and not w.fired:
                w.cancelled = True
                self.save()
                return {"ok": True, "id": w.id}
        return {"ok": False, "reason": "没有这条（或者已经醒过了）"}

    def list(self, *, now_ms: Optional[int] = None) -> List[Dict[str, object]]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        return [{"id": w.id, "at_ms": w.at_ms, "in_minutes": round((w.at_ms - now) / 60000.0, 1),
                 "why": w.why} for w in self._pending(now)]

    def due(self, *, now_ms: Optional[int] = None) -> List[Dict[str, object]]:
        """到点的（未触发、未取消）⇒ **标记已触发并返回**。同一轮问两次，第二次不再吐。"""
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        out: List[Dict[str, object]] = []
        for w in self.items:
            if w.fired or w.cancelled or w.at_ms > now:
                continue
            w.fired = True
            out.append({"id": w.id, "at_ms": w.at_ms, "why": w.why,
                        "late_minutes": round((now - w.at_ms) / 60000.0, 1)})
        if out:
            self.save()
        return out

    def stats(self, *, now_ms: Optional[int] = None) -> Dict[str, object]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        return {"scheduled_today": self._scheduled_today(now),
                "remaining_today": MAX_SCHEDULED_PER_DAY - self._scheduled_today(now),
                "pending": len(self._pending(now)),
                "pending_max": MAX_PENDING,
                "min_gap_minutes": MIN_GAP_MS // 60000,
                "fired_total": len([w for w in self.items if w.fired])}
