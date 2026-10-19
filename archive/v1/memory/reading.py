# -*- coding: utf-8 -*-
"""把插件交回来的**桶**变成**模型真正看到的那几行**（设计稿 §2.8）。

它补齐的是这一环
----------------
```
插件 --(桶)--> 宿主 --(算趋势/分位/覆盖率)--> 渲染 --> 模型看到的文本
                ↑ aggregate.py              ↑ 就是这个文件
```
★ 在这一步之前，`fetch()` 只会把桶原样交给模型 —— 那不是"读数"，是**原始数据**。
设计稿 §2.8 定的形状是：

```
[活动] 信源：手机 HAR（覆盖 92%，窗口 24.0h）
最近：…
趋势：…
基线：近 7 天分位 12%（偏少）
```

★ 三条硬要求（都在自检里钉住）
--------------------------
1. **必带 信源 · 覆盖率 · 窗口** —— 少一个，「他没动」和「没数据」就分不出来。
2. ★★ **基线不够就说"不够"**，不许拿两天的数据假装知道"他平时怎样"。
3. **标量读数**（如「距上次显著运动 47 分钟」，包装层产出的）**没有时间序列** ⇒
   走另一条渲染分支，⛔ 别硬塞进桶的模板。

⚠️ 基线要额外的 `fetch`（7 天档）⇒ 每次读数两次进程内调用。
   加了个 **TTL 缓存**（默认 1 小时）：基线一天才变一次，没必要每轮都问插件。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from .aggregate import (DAY_MS, HOUR_MS, MINUTE_MS, Bucket, baseline_from_days,
                        coverage, percentile_of, render_reading, trend)

FetchFn = Callable[[str, str, int, int], Dict[str, object]]

DEFAULT_WINDOW_MS = DAY_MS            # ★ 默认读数窗口 = 一天（用户定）
DEFAULT_BASELINE_DAYS = 7
BASELINE_TTL_MS = HOUR_MS             # 基线缓存多久


def _buckets_from(rows) -> List[Bucket]:
    """插件交回来的行 `[t, mean, lo, hi, n]` ⇒ `Bucket`。坏行**跳过**，不抛。"""
    out: List[Bucket] = []
    for r in rows or []:
        try:
            t = int(float(r[0]))
            out.append(Bucket(t=t, mean=float(r[1]), lo=float(r[2]), hi=float(r[3]),
                              n=int(r[4]) if len(r) > 4 else 1))
        except Exception:                                        # noqa: BLE001
            continue
    out.sort(key=lambda b: b.t)
    return out


class ReadingBuilder:
    """读数构造器。**不碰模型**，只调注入进来的 `fetch_fn` ⇒ 可离线自检。"""

    def __init__(self, fetch_fn: FetchFn, ttl_ms: int = BASELINE_TTL_MS):
        self.fetch_fn = fetch_fn
        self.ttl_ms = int(ttl_ms)
        self._base_cache: Dict[str, object] = {}     # key -> (ts, [Bucket])
        self.stats = {"window_fetches": 0, "baseline_fetches": 0, "baseline_hits": 0,
                      "failures": 0}

    # ------------------------------------------------------------------ 基线（带 TTL）
    def _baseline(self, plugin: str, metric: str, now_ms: int, days: int) -> Optional[List[Bucket]]:
        key = "%s|%s|%d" % (plugin, metric, days)
        hit = self._base_cache.get(key)
        if hit and now_ms - hit[0] <= self.ttl_ms:
            self.stats["baseline_hits"] += 1
            return hit[1]
        # ★ 多要一天：`baseline_from_days` 会排掉"今天"，不多要就少一天
        deps = days + 1
        try:
            r = self.fetch_fn(plugin, metric, int(now_ms - deps * DAY_MS), int(now_ms)) or {}
        except Exception:                                        # noqa: BLE001
            return None
        self.stats["baseline_fetches"] += 1
        if not r.get("ok"):
            return None
        bs = _buckets_from(r.get("buckets"))
        self._base_cache[key] = (now_ms, bs)
        return bs

    # ------------------------------------------------------------------ 主入口
    def build(self, plugin: str, metric: str, now_ms: int, *,
              name: str, unit: str = "", window_ms: int = DEFAULT_WINDOW_MS,
              baseline_days: int = DEFAULT_BASELINE_DAYS, max_window_ms: Optional[int] = None,
              extra_note: str = "") -> Dict[str, object]:
        """取一次读数并渲染。**失败也正常返回**（`ok:false` + `text` 说明原因），不抛。"""
        win = int(window_ms if max_window_ms is None else min(window_ms, max_window_ms))
        from_ms = int(now_ms - win)
        try:
            r = self.fetch_fn(plugin, metric, from_ms, int(now_ms)) or {}
        except Exception as e:                                   # noqa: BLE001
            self.stats["failures"] += 1
            return {"ok": False, "text": "[%s] 取读数失败：%s" % (name, e),
                    "value": None, "percentile": None, "coverage": None,
                    "sources": [], "note": str(e)}
        self.stats["window_fetches"] += 1

        srcs = [str(s) for s in (r.get("sources") or [])]
        note = str(r.get("note") or "")
        if extra_note:
            note = (note + "；" + extra_note) if note else extra_note

        # ★ 标量读数（包装层产出的，如「距上次显著运动 47 分钟」）—— 没有时间序列
        if r.get("scalar"):
            if not r.get("ok"):
                return {"ok": False, "text": "[%s] %s" % (name, note or "暂时拿不到"),
                        "value": None, "percentile": None, "coverage": None,
                        "sources": srcs, "note": note}
            v = r.get("value")
            txt = "[%s] 信源：%s\n现在：%s %s\n说明：%s" % (
                name, "、".join(srcs) or "未知信源",
                ("%.1f" % float(v)) if v is not None else "?", str(r.get("unit") or unit), note)
            return {"ok": True, "text": txt, "value": (float(v) if v is not None else None),
                    "percentile": None, "coverage": None, "sources": srcs, "note": note,
                    "scalar": True}

        level = int(r.get("levelMs") or MINUTE_MS)
        bs = _buckets_from(r.get("buckets"))
        if not r.get("ok") or not bs:
            return {"ok": False,
                    "text": render_reading(name=name, unit=unit, sources=srcs, buckets=[],
                                           level_ms=level, from_ms=from_ms, to_ms=int(now_ms),
                                           note=note),
                    "value": None, "percentile": None, "coverage": 0.0,
                    "sources": srcs, "note": note}

        # ★ 日桶（基线）。要不到就不给分位 —— **宁可不写，不编**。
        day_bs = self._baseline(plugin, metric, now_ms, baseline_days)
        cov = coverage(bs, from_ms, int(now_ms), level)
        tr = trend(bs)
        pct = None
        if day_bs is not None and tr:
            pct = percentile_of(tr["last"], baseline_from_days(day_bs, now_ms, days=baseline_days))
        txt = render_reading(name=name, unit=unit, sources=srcs, buckets=bs, level_ms=level,
                            from_ms=from_ms, to_ms=int(now_ms), day_buckets=day_bs,
                            now_ms=int(now_ms), baseline_days=baseline_days, note=note)
        return {"ok": True, "text": txt, "value": tr.get("last") if tr else None,
                "percentile": pct, "coverage": cov, "sources": srcs, "note": note,
                "level_ms": level, "trend": tr}
