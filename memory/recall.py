# -*- coding: utf-8 -*-
"""回忆 `[R]`：给一句线索，在**节点与组**上取用一小片，返回 k 件（每件带**整组原文**）。

═══════════════════════════════════════════════════════════════════════
按 `docs/流程框架.md` 的 `[R]` 写：
═══════════════════════════════════════════════════════════════════════

    起点[单元] = 相似度(线索, 单元) × 权重[单元]
    传播 T 轮：组亮 = 池化(成员的传播后表示)；成员 += α × 组亮；组内 := γ（组间 := β）
    条目 = 组 与 单点 排同一个榜 ⟶ 件 = 按强度取前 k 件
    对每件：取原文 := **整组**（不是只取点亮的那几条）

★ 相似度在这里**正当**：它只"提候选/排序"，⛔ 不"定去留"（去留归 `⑤` 判）。
★ 参数一律来自 `memory/knobs.py`（α/β/γ_传播/T/k 都带依据等级）。

## 现在能跑的是哪几段（如实）

| 段 | 状态 |
|---|---|
| 起点（相似度 × 权重） | ✅ **权重先用「标记集置信度」顶**（文档要的是"共现·参与度"，那要真材料统计，占位） |
| 组内传播 `γ_传播` / 成员加成 `α` | ✅ 算法在（`传播()`），但**cell 还没有向量** ⇒ 真实数据下这两个系数**还没吃上**；自检里用合成的 cell 向量验过数学 |
| 组间传播 `β` | ✅ **用"同场共现"当边**（同一场里都出现过 ⇒ 相关）—— 这是唯一现在有真数的一种边 |
| 组与单点同榜 | ⚠️ 现在只排**件**；单点（还没成件的 cell）要等 ① 也给 cell 存表示 |
| 取原文按整组 | ✅ |
| 留痕（碰了哪些位置） | ✅ 返回 `留痕[]`，⛔ 不进读取路径（只给记录层） |

⛔ 本模块**只读**：不改图、不改底片。写盘的事归 `film.py` / `archive_step.py`。
"""
from typing import Callable, Dict, List, Optional, Sequence

from . import markset
from .embedder import 取向量
from .film import 读底片, 切回原文
from .graph import 图
from .knobs import 回忆 as K


def _v(name: str):
    return K[name]["值"]


def 传播(起点: Dict[str, float], 单元向量: Dict[str, Sequence[float]],
         组内成员: Dict[str, Sequence[str]], 组间边: Dict[str, Dict[str, float]],
         α: float = 0.0, β: float = 0.0, γ: float = 0.0, T: int = 0) -> Dict[str, float]:
    """文档那条传播：`组亮 → 成员加成(α)` · `组内(γ)` · `组间(β)`，跑 T 轮。

    ★ 单元 = 组的成员（这里是 cell）与组本身（这里是件）在同一张表里算。
    ★ `组间边[件a][件b]` = 共现强度（0–1）。没有边就不传 —— ⛔ 不凭空造边。
    ★ 池化用 **max**（先粗糙，文档也承认"max 会拍平路径"；等有读数再换软池化）。
    """
    s = dict(起点)
    for _ in range(int(T)):
        新 = dict(s)
        # ---- 组亮 → 成员加成（软加成，⛔ 不硬补齐）
        for 组, 成员们 in 组内成员.items():
            亮 = max([s.get(组, 0.0)] + [s.get(m, 0.0) for m in 成员们]) if 成员们 else s.get(组, 0.0)
            for m in 成员们:
                新[m] = s.get(m, 0.0) + α * 亮
            if 成员们:
                新[组] = s.get(组, 0.0) + γ * 亮          # 组内传播：成员也把亮度回灌给组
        # ---- 组间（现算边，按 β）
        for a, 边 in 组间边.items():
            for b, w in 边.items():
                if b in s:
                    新[a] = 新.get(a, 0.0) + β * float(w) * s[b]
        s = 新
    return s


def 共现边(图: 图) -> Dict[str, Dict[str, float]]:
    """组间边 = **同场共现**：两个件的 cell 落在同一场里 ⇒ 连一条，权重 = 1/（各自场数的几何平均）。

    ★ 这是唯一"现在就有真数"的边：`待决 §14`（依赖簇阈值）还挂着，共现是无参数的那一档。
    ⛔ 不是"相似度" —— 相似度只用来提候选，不能用它当"经历上同属一件事"的证据（文档明确）。
    """
    场的件: Dict[str, set] = {}
    cell到件 = {}
    for 件id, it in 图.件.items():
        for cid in (it.get("成员") or []):
            cell到件[cid] = 件id
    # ★ 2026-10-17：**同场共现要按「轮距」衰减**。
    #   原来写的是"同场就全连、权 `1/√(na·nb)`" ⇒ 同一场里 4 个件两两全连、权全是 1.0
    #   ⇒ 图几乎**全连通** ⇒ 边的信息量等于零，块内边那 1.0 也被淹掉（真对话实测）。
    #   改成：同一场里**隔得越远越弱** —— 相邻的件（轮距 1）权 ≈ base/2，隔 10 轮 ≈ base/11。
    #   ★ 这仍然是个占位（`待决 §14`）：它不是"依赖"，只是"同场 + 距离"这个最便宜的先验。
    件的轮: Dict[str, Dict[str, List[int]]] = {}      # 件id → {场id: [轮…]}
    for c in 图.cell:
        件id = cell到件.get(c["id"])
        if 件id:
            场的件.setdefault(c["场id"], set()).add(件id)
            件的轮.setdefault(件id, {}).setdefault(c["场id"], []).append(int(c["轮"]))
    次数: Dict[str, set] = {}
    for 场, 件们 in 场的件.items():
        for a in 件们:
            次数.setdefault(a, set()).add(场)

    def _最近轮距(场: str, a: str, b: str) -> int:
        """两件在场里**最近的那两轮**之间隔几轮。挨在一起 ⇒ 1。"""
        ra = 件的轮.get(a, {}).get(场) or []
        rb = 件的轮.get(b, {}).get(场) or []
        if not ra or not rb:
            return 10                                  # 拿不到 ⇒ 当隔得比较远
        return max(1, min(abs(x - y) for x in ra for y in rb))

    边: Dict[str, Dict[str, float]] = {}
    for 场, 件们 in 场的件.items():
        件们 = sorted(件们)
        for i, a in enumerate(件们):
            for b in 件们[i + 1:]:
                na, nb = len(次数.get(a) or ()), len(次数.get(b) or ())
                base = 1.0 / max(1.0, (na * nb) ** 0.5)
                w = base / (1.0 + _最近轮距(场, a, b))   # ★ 轮距衰减
                边.setdefault(a, {})[b] = max(边.get(a, {}).get(b, 0.0), w)
                边.setdefault(b, {})[a] = max(边.get(b, {}).get(a, 0.0), w)
    # ★★ 2026-10-17（作者定 A）：**块内边** —— 同一类 / 同一种情绪的件之间连一条。
    #   它比"同场共现"强（那是碰巧聊到，权 ≤ 1；这是经历上的归类）⇒ 权重取 1.0，用 max 合。
    #   ★ 这就是"猫的两件事"在读取时真的能互相带起来的唯一途径 ——
    #     它们**不是同一件**（不在同一个件里），但同在一个块里。
    W = float(K["块内边权重"]["值"]) if "块内边权重" in K else 1.0
    for 块id, it in (getattr(图, "块", {}) or {}).items():
        员 = sorted(str(x) for x in (it.get("成员") or []))
        for i, a in enumerate(员):
            for b in 员[i + 1:]:
                边.setdefault(a, {})[b] = max(边.get(a, {}).get(b, 0.0), W)
                边.setdefault(b, {})[a] = max(边.get(b, {}).get(a, 0.0), W)
    return 边


def 回忆(线索: str, 图: 图, 底片目录: str,
         嵌入: Optional[Callable[[Sequence[str]], List[List[float]]]] = None,
         k: Optional[int] = None) -> Dict:
    """返回 `{"件":[{件id, 分数, 强度, 原文们[], 代表原文}], "留痕":[...], "参数":{...}}`。

    ⛔ 只读：不建、不删、不改任何东西。
    """
    k = int(K["k"]["值"] if k is None else k)
    参数 = {"k": k, "α": _v("α"), "β": _v("β"), "γ_传播": _v("γ_传播"), "T": _v("T"),
            "权重": "标记集置信度（占位；文档要的是共现·参与度）"}
    if not 图.标记集:
        return {"件": [], "留痕": [], "参数": 参数, "为什么": "标记集是空的（还没有件）"}

    向量表 = 图.件向量表()
    if not 向量表:
        return {"件": [], "留痕": [], "参数": 参数, "为什么": "件还没有表示向量"}
    线索向量: List[float] = []
    if 嵌入 is not None and str(线索 or "").strip():
        try:
            # ★★ 2026-10-17 真对话抓到的：原来写的是 `嵌入([..])`，而 `Embedder` **不可调用**
            #   （方法是 `raw_embed`）⇒ TypeError ⇒ 被吞成空向量 ⇒ **起点相似度从来没算过**，
            #   于是 `[R]` 回忆一直只能返回空。⛔ 别再写成 `嵌入(...)`。
            _行 = list(取向量(嵌入, [str(线索)]))
            线索向量 = [float(x) for x in (_行[0] if _行 else [])]
        except Exception as e:                # noqa: BLE001
            线索向量 = []
            参数 = dict(参数 or {})
            参数["嵌入错误"] = "%s: %s" % (type(e).__name__, e)
    if not 线索向量:
        return {"件": [], "留痕": [], "参数": 参数, "为什么": "拿不到线索的向量"}

    # ---- 起点：相似度 × 权重（权重 = 置信度，占位）
    起点 = {i: markset.余弦(线索向量, 向量表[i]) * float(图.标记集.get(i, 0.0))
            for i in 向量表}
    # ---- 传播（组内只有"成员 = 件自己"这一层时 α/γ 不生效；cell 表示有了以后自然生效）
    组内成员 = {i: [] for i in 起点}
    s = 传播(起点, 向量表, 组内成员, 共现边(图),
              α=float(参数["α"]), β=float(参数["β"]),
              γ=float(参数["γ_传播"]), T=int(参数["T"]))

    # ---- 取前 k 件
    排 = sorted(s.items(), key=lambda t: (-t[1], t[0]))[:k]
    if not 排:
        return {"件": [], "留痕": [], "参数": 参数, "为什么": "没有取到件"}

    # ---- 每件取**整组**原文（按区间回底片切）
    底片们: Dict[str, Optional[Dict]] = {}
    import os
    out: List[Dict] = []
    留痕: List[Dict] = []
    for 件id, 分 in 排:
        it = 图.件.get(件id) or {}
        原文们: List[str] = []
        for cid in (it.get("成员") or []):
            c = next((x for x in 图.cell if x["id"] == cid), None)
            if c is None:
                continue
            场 = c["场id"]
            if 场 not in 底片们:
                底片们[场] = 读底片(os.path.join(底片目录, 场 + ".json.gz")) or \
                    读底片(os.path.join(底片目录, 场 + ".json"))
            文本 = 切回原文(底片们[场] or {}, c["轮"], "刺激") + 切回原文(底片们[场] or {}, c["轮"], "她说")
            if 文本.strip():
                原文们.append(文本)
                留痕.append({"件": 件id, "场id": 场, "轮": c["轮"], "起": c["起"], "止": c["止"]})
        out.append({"件id": 件id, "分数": float(分),
                    "强度": float(图.标记集.get(件id, 0.0)), "原文们": 原文们})
    return {"件": out, "留痕": 留痕, "参数": 参数}
