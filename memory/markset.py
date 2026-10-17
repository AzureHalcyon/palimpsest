# -*- coding: utf-8 -*-
"""标记集：提候选件 · 强化 · 衰减（`④ 层1/层2` 的全部算法）。

★ **这三块从 `tools/记忆/标记集.py` 迁进包里**（2026-10-17）：那边是"设计仓库这一侧的离线脚本"，
  而它现在是**运行时**要用的一步（归档 ④、⑤ 之前的提候选件）⇒ 按依赖引用，不再各留一份实现
  （`tools/记忆/标记集.py` 已改成从这里 import，它那 17 条断言继续当这份的规格测试）。

★ 一条口头规矩：**只动 confidence，⛔ 不删任何件**。
  掉下去的只是"剪出标记集"，件本身还在图里（下次被碰到还能拉起）。

参数全部来自 `memory/knobs.py`（带依据等级），⛔ 不在函数签名里写死数字。
"""
import math
from typing import Dict, List, Optional, Sequence, Tuple

from .knobs import 件 as K件


def _值(name: str):
    return K件[name]["值"]


def 余弦(a: Sequence[float], b: Sequence[float]) -> float:
    """两向量的余弦。任一为零向量 ⇒ 0（⛔ 让 0/0 抛异常，那会让整批崩）。"""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def 提候选件(线索向量: Sequence[float], 件向量表: Dict[str, Sequence[float]],
             标记集: Optional[Dict[str, float]] = None, k: int = 0) -> List[Tuple[str, float]]:
    """从【标记集里的件】中取 k 个候选（相似度 = 余弦）。

    ★ 相似度在这里**正当** —— 它只"提候选/排序"，⛔ 不"定去留"（去留归 `⑤` 判）。
    ★ 只从标记集里取：已剪出去的件不参与候选。
    ★ 同分按 id 定序 ⇒ **跨进程可复现**（不靠 dict 顺序）。
    """
    k = int(k or 0) or int(提候选件默认k())
    池 = list(标记集.keys()) if 标记集 is not None else list(件向量表.keys())
    分 = [(i, 余弦(线索向量, 件向量表[i])) for i in 池 if i in 件向量表]
    分.sort(key=lambda t: (-t[1], t[0]))
    return 分[:k]


def 提候选件默认k() -> int:
    from .knobs import 判 as K判
    return int(K判["候选件数"]["值"])


def 强化(标记集: Dict[str, float], 归进的件: Optional[str] = None,
         新建的件: Optional[str] = None, 冷成员: Sequence[str] = (),
         增量: Optional[float] = None, 拉起: Optional[float] = None) -> Dict[str, float]:
    """`⑤` 判完后的 `④ 层1`：写标记集。

    · 归进的件 ⇒ 该件 +增量（上限 1.0）
    · 新建的件 ⇒ 进标记集，confidence = 初值
    · 冷成员   ⇒ **拉到 max(当前, 拉起)** ——【作者定】「第二次就强化到能用上就行」。
      ⚠️ 这一条是"低频重要"的活路：旧的不被拉起，"第二次被提起"就永远不成立。
    ★ 就地改并返回（调用方通常直接接着用）。
    """
    增量 = _值("强化增量") if 增量 is None else 增量
    拉起 = _值("冷成员拉起") if 拉起 is None else 拉起
    if 新建的件 is not None:
        标记集[新建的件] = float(_值("初始置信度"))
    if 归进的件 is not None:
        标记集[归进的件] = min(1.0, float(标记集.get(归进的件, 1.0)) + float(增量))
    for i in (冷成员 or ()):
        标记集[i] = max(float(标记集.get(i, 0.0)), float(拉起))
    return 标记集


def 衰减(标记集: Dict[str, float], 窗口数: int = 1,
         衰减率: Optional[float] = None, 底: Optional[float] = None,
         门槛: Optional[float] = None,
         来源: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, float], List[str]]:
    """`④ 层2` 的长期维护：每过一个窗口 −衰减率；到地板不再掉。

    ★ **⛔ 删件** —— 掉下去的只是"剪出标记集"，件还在图里。
      返回 `(新标记集, 被剪出去的件id)`；调用方把剪出去的那些放进「待观察池」。

    ★★ `来源`（2026-10-17 · 作者口径「记牢一些」）：`{件id: "意外"|"手工"}`。
      手工档衰减更慢（−0.02）、地板更高（0.5 > 门槛 0.2）⇒ **永不剪出**。
      不给 ⇒ 全部按普通档（旧调用的行为不变）。
    """
    衰减率 = _值("每窗口衰减") if 衰减率 is None else 衰减率
    底 = _值("地板") if 底 is None else 底
    门槛 = _值("遍历门槛") if 门槛 is None else 门槛
    手工率 = _值("手工衰减率") if 衰减率 == _值("每窗口衰减") else 衰减率
    手工底 = _值("手工地板") if 底 == _值("地板") else 底
    新: Dict[str, float] = {}
    剪出: List[str] = []
    for i, c in 标记集.items():
        是手工 = str((来源 or {}).get(i) or "") == "手工"
        r = 手工率 if 是手工 else 衰减率
        b = 手工底 if 是手工 else 底
        c2 = max(float(b), float(c) - float(r) * int(窗口数))
        新[i] = c2
        if c2 < float(门槛):
            剪出.append(i)
    return 新, 剪出
