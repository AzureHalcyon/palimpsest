# -*- coding: utf-8 -*-
"""读取路径的「势」—— 一次激活走过哪些格，叠成下一回的**引导**。

★★ 2026-10-18（作者口径，逐条对应）：

| 口径 | 落法 |
|---|---|
| 只记**够亮**的格（⛔ 半径内全记） | `亮格()` 取终结分**中位以上**，亮度按峰值归一 |
| **势**（EMA），⛔ 不是裸次数 | 记 `势` 与 `唤醒`；**引导读 `势` 本身** —— 实测 `势/唤醒` 会把「亮一次」和「亮二十次」抹平（见 `条件()`） |
| 遗忘内建 | EMA 本身带 `(1-α)` ⇒ ⛔ 另写一条衰减 |
| 引导，⛔ 不是准入 | 只乘在**传播系数**上（组内 α、组间 β、组内 γ），起点与准入都不动 |

★ **和归档侧艾宾浩斯不是一回事**（别混）：

| | 艾宾浩斯（`markset.衰减`） | 这里的势 |
|---|---|---|
| 对象 | **件**（标记集） | **格**（读取路径） |
| 时机 | 每归档一场 | 每次激活 |
| 决定 | **存不存**（掉到门槛以下剪出） | **激活往哪儿流** |
| 单位 | 场 | 次 |

⚠️ 它**不是图的一部分**：图只读（`recall` 不写图），势单独落 `缓存/势.json`，可删、可重算。
"""
import io
import json
import os
from typing import Dict, Iterable, List, Tuple


def 载入(路径: str) -> Dict[str, Dict[str, float]]:
    """读 `势.json`；没有/坏了 ⇒ 空表（⛔ 不抛：没有势就退化成"第一次走"。）"""
    try:
        if not os.path.exists(路径):
            return {}
        d = json.load(io.open(路径, encoding="utf-8"))
        out: Dict[str, Dict[str, float]] = {}
        for k, v in (d or {}).items():
            try:
                out[str(k)] = {"势": float(v.get("势") or 0.0),
                              "唤醒": float(v.get("唤醒") or 0.0)}
            except (TypeError, ValueError, AttributeError):
                continue
        return out
    except Exception:                                    # noqa: BLE001
        return {}


def 落盘(路径: str, 表: Dict[str, Dict[str, float]]) -> None:
    """写回。⛔ 写失败不打断对话（势是派生量，丢了下一轮重新攒）。"""
    try:
        目录 = os.path.dirname(路径)
        if 目录:
            os.makedirs(目录, exist_ok=True)
        写 = {str(k): {"势": round(float(v.get("势") or 0.0), 6),
                       "唤醒": round(float(v.get("唤醒") or 0.0), 6)}
              for k, v in (表 or {}).items()}
        with io.open(路径, "w", encoding="utf-8") as fh:
            json.dump(写, fh, ensure_ascii=False)
    except Exception:                                    # noqa: BLE001
        pass


def 条件(表: Dict[str, Dict[str, float]], 格id: str) -> float:
    """引导强度 ∈ [0,1]：**直接用 EMA 后的「势」**（⛔ 不是 `势/唤醒`）。

    ★★ 2026-10-18 实测（17 场 / 291 轮补出来的 316 格）：

    | | 分布 |
    |---|---|
    | `势/唤醒`（条件势） | `{0.6:19, 0.7:62, 0.8:101, 0.9:83, 1.0:51}` —— **全挤在 0.6 以上** |
    | `唤醒`（亮过几次） | `{0.1:127, 0.2:52, 0.3:61, … 1.0:25}` —— 这个才有区分度 |

    **为什么条件势失效**：「亮一次就走成路径」的比例本来接近 100% ⇒ 127 个只亮过 1 次的格
    和亮过 20 次的格，条件势是一样的。而 **EMA 本身已经带遗忘**（`(1-α)`），
    绝对势直接就是「叠了几层、多深」——既有区分度，又不会只增不减。
    ⛔ 所以这里用绝对势，函数名保留 `条件` 只为少改调用点。
    """
    p = (表 or {}).get(str(格id)) or {}
    return max(0.0, min(1.0, float(p.get("势") or 0.0)))


def 条件们(表: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """压平成 `{格id: 条件势}`，给 `传播` 直接吃。"""
    return {str(k): 条件(表, k) for k in (表 or {})}


def 亮格(终: Dict[str, float], 前几: int = 10) -> List[Tuple[str, float]]:
    """本次**够亮**的格：`[(格id, 归一亮度)]`，取**最亮的 `前几` 个**。

    ★ 2026-10-18（作者：「够亮的才记」，⛔ 半径内全记）：
      · 一开始取"中位以上"，实测**唤醒全饱和到 1.0** —— 322 格里每轮亮一半，
        分母失去意义（条件势退化成绝对势）；
      · 改成**绝对 top-N**（默认 10）：跟编写"只写最清楚的几处"同一个口径。
    ★ 亮度除以本次峰值 ⇒ ∈ (0, 1]，给 EMA 用。
    """
    项 = [(str(k), float(v)) for k, v in (终 or {}).items() if float(v) > 0]
    if not 项:
        return []
    项.sort(key=lambda kv: -kv[1])
    峰 = 项[0][1]
    return [(k, v / 峰) for k, v in 项[:max(1, int(前几))]]


def 更新(表: Dict[str, Dict[str, float]], 格们: Iterable[Tuple[str, float]],
         α: float) -> None:
    """一次激活后叠一层（EMA）：`势 += α·亮度`、`唤醒 += α`，都带 `(1-α)` 的遗忘。"""
    表 = 表 if 表 is not None else {}
    α = max(1e-6, min(1.0, float(α)))
    for cid, 亮 in (格们 or []):
        p = dict(表.get(str(cid)) or {"势": 0.0, "唤醒": 0.0})
        p["势"] = α * float(亮) + (1.0 - α) * float(p.get("势") or 0.0)
        p["唤醒"] = α * 1.0 + (1.0 - α) * float(p.get("唤醒") or 0.0)
        表[str(cid)] = p
