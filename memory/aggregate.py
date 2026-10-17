# -*- coding: utf-8 -*-
"""读数的**聚合与趋势** —— 把一堆采样压成"模型该看到的那几行"。

★★ 这一段为什么放在**宿主（Python）**，而不是插件里
------------------------------------------------
插件只干三件**它才懂**的事：**采（按各自方法注册）· 分桶 · 落盘**。
**压成什么形状给模型看**是**宿主**的事，理由：

1. 趋势 / 分位 / 覆盖率是**设计 sophistication 所在**，而项目的自检都在 Python 侧 ⇒
   放这里**能离线验证**；放 Kotlin 里就只能靠跑真机看。
2. **换插件不用重写数学**：加一个手环插件，趋势算法一行不改。
3. 契约因此更薄：`read()` 回 **桶**，宿主算趋势（`Reading.trend` 仍可回填，但由宿主负责）。

★ 时间切片（设计稿 §2.9）：`1min(7天) → 1h(90天) → 1day(2年)`。
**切片是存储格式，不是给模型的格式** —— 给模型的永远是「摘要 + 窗口」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# ★ 三档保留期（设计稿 §2.9 定的）
KEEP_MINUTE_MS = 7 * DAY_MS
KEEP_HOUR_MS = 90 * DAY_MS
KEEP_DAY_MS = 2 * 365 * DAY_MS

# ★ 算"分位"至少要有多少天历史。不够就**如实说"基线不够"**，别硬算。
MIN_BASELINE_DAYS = 3


@dataclass
class Bucket:
    """一个时间桶。`t` 是桶起点（毫秒），`n` 是桶里有多少条原始采样。"""
    t: int
    mean: float
    lo: float
    hi: float
    n: int = 1

    def as_row(self) -> List[float]:
        return [self.t, self.mean, self.lo, self.hi, self.n]


def bucketize(samples: Sequence[Tuple[int, float]], level_ms: int) -> List[Bucket]:
    """把 `(时刻, 值)` 采样按 `level_ms` 分桶（桶内取 均值/最小/最大/条数）。

    ★ 为什么留 min/max：均值会把"剧烈但短暂"抹平（他跑了 5 分钟），
      而那种事恰恰是读数里最有信息量的。代价是每桶 5 个数而不是 2 个。
    """
    acc: Dict[int, List[float]] = {}
    for t, v in samples:
        k = int(t) - (int(t) % int(level_ms))
        acc.setdefault(k, []).append(float(v))
    out: List[Bucket] = []
    for k in sorted(acc):
        vs = acc[k]
        out.append(Bucket(t=k, mean=sum(vs) / len(vs), lo=min(vs), hi=max(vs), n=len(vs)))
    return out


def rollup(buckets: Sequence[Bucket], level_ms: int) -> List[Bucket]:
    """把细桶并成粗桶（1min → 1h → 1day）。**合并要按 n 加权**，不是简单平均 ——
    否则"缺数据的那半天"会被当成"跟满数据的一天一样重"。"""
    acc: Dict[int, List[Bucket]] = {}
    for b in buckets:
        k = int(b.t) - (int(b.t) % int(level_ms))
        acc.setdefault(k, []).append(b)
    out: List[Bucket] = []
    for k in sorted(acc):
        bs = acc[k]
        n = sum(b.n for b in bs) or 1
        out.append(Bucket(
            t=k,
            mean=sum(b.mean * b.n for b in bs) / n,
            lo=min(b.lo for b in bs),
            hi=max(b.hi for b in bs),
            n=n,
        ))
    return out


def prune(buckets: Sequence[Bucket], now_ms: int, keep_ms: int) -> List[Bucket]:
    """按保留期丢掉太老的桶（设计稿 §2.9 的三档）。"""
    cut = int(now_ms) - int(keep_ms)
    return [b for b in buckets if b.t >= cut]


def coverage(buckets: Sequence[Bucket], from_ms: int, to_ms: int, level_ms: int) -> float:
    """窗口内**实际有数据的桶**占应有的比例（0–1）。

    ★ 必须报它：不报的话，"他没动"和"传感器没数据"在读数上长得一模一样。
    """
    span = max(int(to_ms) - int(from_ms), 1)
    expect = max(span // int(level_ms), 1)
    got = len([b for b in buckets if int(from_ms) <= b.t <= int(to_ms)])
    return float(min(got, expect)) / float(expect)


def trend(buckets: Sequence[Bucket]) -> Dict[str, float]:
    """窗口内的趋势：条数 / 均值 / 极值 / 首末 / **斜率（每小时）**。"""
    bs = [b for b in buckets if b.n > 0]
    if not bs:
        return {}
    means = [b.mean for b in bs]
    dt_h = (bs[-1].t - bs[0].t) / float(HOUR_MS)
    slope = (means[-1] - means[0]) / dt_h if dt_h > 1e-9 else 0.0
    return {
        "n": float(sum(b.n for b in bs)),
        "buckets": float(len(bs)),
        "mean": sum(means) / len(means),
        "min": min(b.lo for b in bs),
        "max": max(b.hi for b in bs),
        "first": means[0],
        "last": means[-1],
        "slope_per_hour": slope,
    }


def percentile_of(value: float, history: Sequence[float]) -> Optional[float]:
    """★ 这个值在**自身历史**里的分位（0–1）。

    ⛔ 历史不够（< `MIN_BASELINE_DAYS` 天）⇒ 返回 `None`，
      由上层如实写"基线不够" —— **不许拿两天的数据假装知道"他平时怎样"**。
    """
    h = [float(x) for x in history if x is not None]
    if len(h) < MIN_BASELINE_DAYS:
        return None
    below = len([x for x in h if x <= float(value)])
    return float(below) / float(len(h))


def baseline_from_days(day_buckets: Sequence[Bucket], now_ms: int,
                       days: int = 7) -> List[float]:
    """取最近 `days` 天的**日聚合**当基线（不含今天 —— 今天要和"过去"比，不能跟自己比）。"""
    today = int(now_ms) - (int(now_ms) % DAY_MS)
    cut = today - days * DAY_MS
    return [b.mean for b in day_buckets if cut <= b.t < today]


def render_reading(
    *,
    name: str,
    unit: str,
    sources: Sequence[str],
    buckets: Sequence[Bucket],
    level_ms: int,
    from_ms: int,
    to_ms: int,
    day_buckets: Optional[Sequence[Bucket]] = None,
    now_ms: Optional[int] = None,
    baseline_days: int = 7,
    note: str = "",
) -> str:
    """把读数渲染成**模型真正看到的那几行**（设计稿 §2.8）。

    ★ 每条**必带**：信源 · 覆盖率 · 窗口。少了这三个，"他没动"和"没数据"就分不出来。
    ★ 分位只有在**基线够**的时候才写；不够就写"基线不够"（诚实优先于好看）。
    """
    u = (" " + unit) if unit else ""
    src = "、".join([s for s in sources if s]) or "未知信源"
    cov = coverage(buckets, from_ms, to_ms, level_ms)
    hours = max((int(to_ms) - int(from_ms)) / float(HOUR_MS), 0.01)
    lines = ["[%s%s] 信源：%s（覆盖 %.0f%%，窗口 %.1fh）"
             % (name, u, src, cov * 100, hours)]
    tr = trend(buckets)
    if not tr:
        lines.append("窗口内没有数据。%s" % (note or ""))
        return "\n".join(l.rstrip() for l in lines)
    lines.append("最近：%.3g（均值 %.3g，最小 %.3g，最大 %.3g）"
                 % (tr["last"], tr["mean"], tr["min"], tr["max"]))
    if abs(tr["slope_per_hour"]) > 1e-9:
        lines.append("趋势：%.3g/小时" % tr["slope_per_hour"])
    if day_buckets is not None and now_ms is not None:
        base = baseline_from_days(day_buckets, now_ms, days=baseline_days)
        pct = percentile_of(tr["last"], base)
        if pct is None:
            lines.append("基线：不够（只有 %d 天历史，至少要 %d 天）⇒ 不判断偏多偏少"
                         % (len(base), MIN_BASELINE_DAYS))
        else:
            lines.append("基线：近 %d 天分位 %.0f%%（%s）"
                         % (baseline_days, pct * 100, "偏少" if pct <= 0.25 else
                            ("偏多" if pct >= 0.75 else "平常")))
    if note:
        lines.append("说明：%s" % note)
    return "\n".join(l.rstrip() for l in lines)
