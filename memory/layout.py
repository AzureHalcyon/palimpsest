# -*- coding: utf-8 -*-
"""把池子摆成 3D：原点 = "它是谁"，坐标只在归档时算一次。

## 为什么原点定在底色

「底色 = 它自己是谁」是这个项目本来的设定，于是"离原点多远"有了确定的含义：
**离它自己是谁有多远**。这不是为了好看挑的原点。

## 为什么半径**不是**编的（这条是这张图能成立的全部理由）

编码器（`qwen3.7-text-embedding`）**返回的向量本身是 L2 归一化的** ——
实测 14 条：范数 min 0.99999996 / max 1.00000006 / 标准差 3.4e-8。
范数恒为 1 意味着 `‖v‖ = ‖c‖ = 1`，于是

    ‖v − c‖ = √(2 − 2·cos(v, c))          ← 【实测】偏差 8.1e-8

⇒ **到原点的欧氏距离就等于余弦距离**，只是换了写法。
  而余弦正是 `Reader` 在用的度量 ⇒ **图上的半径和"读取器怎么看"是同一把尺子。**

⚠️ 但投影到 3 维之后半径**只能读排序、读不了幅度**：
  实测 3D 投影半径 vs 真半径相关 0.980，**平均相对误差 33%**
  （因为 42.3% 的方向在视野外）。精确值要给数字，不能让人拿尺子在屏幕上量。

## 为什么用幂迭代而不是全 SVD

`np.linalg.svd(..., full_matrices=False)` 会把 min(N, 1024) 个奇异值**全算**，
我们只要 3 个。实测（1024 维）：

    N=200   全 SVD  84 ms   幂迭代   5 ms
    N=1000  全 SVD 424 ms   幂迭代  12 ms
    N=3000  全 SVD 888 ms   幂迭代  42 ms

⇒ N ≤ `FULL_SVD_MAX`（256）用全 SVD（便宜且精确）；超过就换幂迭代。
   幂迭代的半径误差随 N **变小**：N=1000 时 2.4%、N=3000 时 1.5%
   （点越多，前三个方向分得越开）。而"底色全在最内圈"这个承重结论**每个 N 都成立**。

## 什么时候算

**读池子时按需算（`host_glue.pool_view_json`）。** 向量只在归档时变，实测
N=1000 幂迭代 12 ms（手机约 50 ms），而且跑在 Python 那个独占线程上、不占主线程
⇒ 不必缓存进 `state.json`（早先这里写的是"只在归档时算一次、存进 state"，
后来改成按需算是因为存进去要多维护一份会和池子不同步的副本）。
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, List, Optional

import numpy as np

# 超过这个条数就换幂迭代（见模块注释里的实测表）
FULL_SVD_MAX = 256
# 图上最多摆这么多点。
#
# ★ 400 是**旧画法**（未中心化 PCA）留下的：那时担心"每帧画 N 个圆挤爆 16.7 ms 帧预算"。
#   定轴重心图的投影是 O(n)，实测（2026-10-05，桌面）：
#       N=243 → 43 ms / 109 KB  ·  N=2400 → 91 ms / 1.0 MB  ·  N=10000 → 325 ms / 4.2 MB
#   ⇒ **画面不是瓶颈，JSON 传输 + 主线程 org.json 解析才是**（约 430 字节/点）。
#     代码别处早就记过这个坑："一次吐上千条会被 org.json 解析卡住，比分布图更早卡"。
#   ⇒ 上限按"解析扛得住"定，不按"画得动"定：3000 点约 1.3 MB。
MAX_POINTS = 3000


def _top3(W: np.ndarray, k: int = 3) -> tuple:
    """取 `W` 的前 k 个方向。返回 (坐标 N×k, 各方向占比, 有效维度或 None)。

    ★ 占比是**精确**的，两条路都是：第 i 个奇异值 σ_i = ‖X[:, i]‖（X 的列正交），
      而分母是 ‖W‖²_F —— 两个都算得起，不需要完整谱。
    ★ **有效维度只在全 SVD 那条路上给**。幂迭代本来就是"不算完整谱"换速度的，
      硬报一个有效维度就是编。给 None，界面上就不显示这个数（宁可不说）。
    """
    n = W.shape[0]
    tot = float((W ** 2).sum()) or 1.0
    if n <= FULL_SVD_MAX:
        U, S, _ = np.linalg.svd(W, full_matrices=False)
        ss = S ** 2
        share = ss[:k] / (float(ss.sum()) or 1.0)
        # 参与率 = (Σσ²)² / Σσ⁴，即"实际用掉几个维度"
        eff = float((ss.sum() ** 2) / max(float((ss ** 2).sum()), 1e-12))
        return U[:, :k] * S[:k], share, eff
    # 幂迭代：迭代 W.T@W（D×D），只做矩阵乘法 ⇒ O(N·D·k·iters)
    m = k + 2
    rng = np.random.default_rng(12345)          # 固定种子：同一份数据给同一张图
    Y = np.linalg.qr(rng.normal(size=(W.shape[1], m)))[0]
    for _ in range(12):
        Y = np.linalg.qr(W.T @ (W @ Y))[0]
    # B = W@Y = Ub·Sb·Vhb.T ⇒ W@(Y@Vhb) = Ub·Sb ⇒ 右奇异向量是 Y@Vhb（**不是** Vhb.T）
    Vhb = np.linalg.svd(W @ Y, full_matrices=False)[2]
    X = W @ (Y @ Vhb)[:, :k]
    share = (X ** 2).sum(axis=0) / tot
    return X, share, None


@lru_cache(maxsize=32)
def _random_baseline(n: int, d: int) -> tuple:
    """**同样条数、同样维度的随机向量**能铺多开 —— "扁不扁"的对照。返回 (有效维度, 前三占比)。

    ★ 为什么要这个数：`有效维度 4.8` 单独摆出来没有意义 —— 4.8 算高还是算低，
      取决于"最多能有多少"。实测 n=14 / d=1024 时随机对照 13.6。
    ★ 前三占比那个对照**两条路都给得起**（占比是精确的，见 `_top3`），
      而有效维度只有全 SVD 那条路有。所以"扁不扁"这句话至少要有前者兜底 ——
      n > `FULL_SVD_MAX` 时不能整句消失（实测 n=400 时就是这么消失的）。

    ⚠️ **不能把对照写死成"随机约等于条数"**：那只在 `n ≪ d` 时成立。
       实测 n=400 / d=1024 时有效维度只有 287.3、前三占比只有 0.75%（不是 3/400 以外的直觉值）。

    代价：只在对角化 n×n（`R·Rᵀ`）—— 比 n×d 的 SVD 便宜，而且同样的 (n, d)
    只算一次（`lru_cache`）；随机数固定种子，同一份数据给同一个数。
    """
    rng = np.random.default_rng(12345)
    R = rng.normal(size=(int(n), int(d)))
    ev = np.linalg.eigvalsh(R @ R.T)
    ev = ev[ev > 0]
    tot = float(ev.sum()) or 1.0
    eff = float((ev.sum() ** 2) / max(float((ev ** 2).sum()), 1e-12))
    return eff, float(ev[-3:].sum() / tot)          # eigvalsh 升序 ⇒ 末尾是最大的三个


def _time_of(long_pool, temp_pool, i: int):
    """第 i 条的"什么时候进来的"（引擎钟，天）；种子条目没有 ⇒ None。"""
    n = len(long_pool.texts)
    if i < n:
        t = long_pool.meta_at(i).get("t")
        return float(t) if isinstance(t, (int, float)) else None
    j = i - n
    if 0 <= j < len(temp_pool.items):
        return float(temp_pool.items[j].t0)
    return None


def radial_layout(long_pool, temp_pool, temp_V, now: float = 0.0,
                  max_points: int = MAX_POINTS) -> Dict[str, object]:
    """算出每个条目的 3D 坐标 + 这张图的质量数字。"""
    VP = long_pool.prep()
    n_b = int(getattr(long_pool, "n_baseline", 0))
    texts = [str(t) for t in long_pool.texts]
    layers = [long_pool.layer_of(i) for i in range(len(texts))]
    hits = [long_pool.hits_at(i) for i in range(len(texts))]

    V = VP
    # ★ 临时池的向量**可能还没有**：老缓存的 `pool.npz` 里没存 `temp_V`
    #   （那时每次 `load` 都重算），要等下一次归档 `save()` 才补上。
    #   那时候如果什么都不说，界面上就是"短期记忆一条都不在图里" —— 看起来像坏了。
    note = None
    if temp_V is None and len(getattr(temp_pool, "items", [])):
        note = "短期记忆这次没进图：缓存里还没有它们的向量，下一次归档补上"
    if temp_V is not None and len(temp_V) and len(temp_pool.items) == len(temp_V):
        texts = texts + [str(it.text) for it in temp_pool.items]
        # ★ 2026-10-05：临时条目现在有**三档**（短期 / 中期 / 悬着）⇒ 层标签由池子自己给。
        #   ⛔ 以前这里硬写 `["临时"] * n` —— 于是中期与悬着的事在图上**与短期同色**、
        #     图例里也不分档，看着就是"画不全"（用户原话）。
        #   ⚠️ 层标签只影响**画图与图例**，不影响坐标：`layer` 在这两个函数里
        #     只被 `base_r`（取 `== "底色"`）和出口的 `layer` 字段用到。
        layers = layers + [temp_pool.layer_label(i) for i in range(len(temp_pool.items))]
        hits = hits + [int(getattr(it, "hits", 0) or 0) for it in temp_pool.items]
        # ★★ 临时池向量要**自己归一化**。
        #   长池走的 `prep()` 已经 L2 归一化（与 `Reader` 同一份），但 `refresh_temp_vectors`
        #   存的是 `raw_embed` 的原样输出。真实端点恰好返回单位向量，所以线上看不出来 ——
        #   可这是**隐式前提**：换了编码器（或离线替身 `HashingEmbedder`，实测范数 1.581），
        #   `‖v−c‖ = √(2−2cos)` 就不成立，半径会悄悄变成别的东西。
        #   在这里把前提写死，比在注释里假设它强。
        TV = np.asarray(temp_V, dtype=float)
        TV = TV / np.maximum(np.linalg.norm(TV, axis=1, keepdims=True), 1e-12)
        V = np.vstack([VP, TV]) if len(VP) else TV

    if len(texts) == 0 or n_b <= 0:
        return {"ok": False, "why": "没有底色条目 —— 圆心定不出来", "points": []}

    c = VP[:n_b].mean(axis=0)
    nc = float(np.linalg.norm(c))
    if nc < 1e-9:
        return {"ok": False, "why": "底色向量互相抵消，圆心定不出来", "points": []}
    c = c / nc

    W = V - c
    r_true = np.linalg.norm(W, axis=1)          # = √(2−2cos)，见模块注释
    X, share, eff = _top3(W)
    # ★ "扁不扁"要有对照才有意义：同样是 n 条、同样维度的随机向量能铺多开。
    #   前三占比那个对照两条路都给得起；有效维度只有全 SVD 那条路有（幂迭代不报）。
    eff_rand, share3_rand = _random_baseline(W.shape[0], W.shape[1])
    if eff is None:
        eff_rand = None                       # 自己不报的，对照也不报（免得像在暗示什么）
    r_3d = np.linalg.norm(X, axis=1)
    corr = float(np.corrcoef(r_true, r_3d)[0, 1]) if len(texts) > 2 else 1.0

    pts = [{
        "i": i, "layer": layers[i], "temp": bool(i >= len(long_pool.texts)),
        "text": texts[i],
        "x": float(X[i, 0]), "y": float(X[i, 1]),
        "z": float(X[i, 2]) if X.shape[1] > 2 else 0.0,
        "r": float(r_true[i]),                  # 精确半径（余弦距离）
        "r3": float(r_3d[i]),                   # 图上的半径（有 33% 幅度误差）
        "hits": hits[i],
        # ★ 时间（引擎钟，单位天）。**种子条目没有** —— 它们是我们手工写进去的，
        #   给个假时间不如给 None；界面据此把它排除在"随时间"那张图外并说明。
        "t": _time_of(long_pool, temp_pool, i),
    } for i in range(len(texts))]

    # 点太多就按"相关度"挑：离准则近的、被读过多的优先 —— 并**说明没画全**
    kept = pts
    if len(pts) > max_points:
        rank = sorted(range(len(pts)),
                      key=lambda i: (pts[i]["hits"], -pts[i]["r"]), reverse=True)
        keep = set(rank[:max_points])
        kept = [pts[i] for i in sorted(keep)]

    base_r = float(np.mean([p["r"] for p in pts if p["layer"] == "底色"])) if n_b else 0.0
    return {
        "ok": True,
        "points": kept,
        "n_total": len(pts),
        "n_drawn": len(kept),
        "n_center": n_b,
        "share": [float(s) for s in share],     # 前三个方向各占多少
        "cum3": float(sum(share)),              # 3D 画出多少 —— 图上必须写
        "eff_dim": eff,
        "eff_dim_random": eff_rand,             # 同样条数的随机向量能铺多开（对照）
        "share3_random": share3_rand,           # 同上，但**两条路都给得起**（前三占比）
        "radius_corr": corr,                    # 投影半径与真半径的相关
        "base_r": base_r,                       # 底色平均半径（画参考壳用）
        "max_r": float(r_true.max()) if len(r_true) else 1.0,
        # 图**只能画成什么样**的说明（例如临时池还没向量）。空就是没话要说。
        "note": note,
    }


# ===========================================================================
# 定轴重心图（2026-10-05）
# ===========================================================================
def frame_layout(long_pool, temp_pool, temp_V, axes_data,
                 now: float = 0.0, max_points: int = MAX_POINTS) -> Dict[str, object]:
    """★ **定轴重心图**：36 根**人写双极轴**的骨架 + 锚算出的成分权重。

    为什么换掉 `radial_layout`（未中心化 PCA）：
      实测第一根主方向 97% 的能量是"全体往同一侧偏"这个**常数偏移**（它与圆心方向只差 4°），
      ⇒ 图上所有点必然挤在一侧。而"把轴从数据里算出来"（PCA / MDS）**自己会均匀化**
      ⇒ 画出来必然填满 ⇒ **没有信息**。

    这一版的三件事分开：
      **几何方向 = 约定**（`axes.frame_dirs`，与数据无关 ⇒ **空轴也有方向、画得出来**）
      **成分权重 = 锚**（`softmax(x/τ)`，τ 由"锚在自己轴上的权重中位 = 0.5"定出，见 `axes.pick_tau`）
      **半径 = 测量**（仍是离底色的余弦距离，与 `Reader` 同一把尺子）

    ⇒ `pos_i = r_i · normalize(Σ_k w_ik · u_k)`。**混杂的条目自然落在两根轴之间。**
    """
    from . import axes as ax

    if not axes_data:
        return {"ok": False, "why": "缓存里没有轴框架（axes.npz）—— 先跑 tools/prep_pool.py --axes",
                "points": [], "axes": []}

    ids, names, Ap, Am, D, center = ax.axis_vectors(axes_data)
    k = len(ids)

    VP = long_pool.prep()
    n_b = int(getattr(long_pool, "n_baseline", 0))
    if n_b <= 0:
        return {"ok": False, "why": "没有底色条目 —— 半径定不出来", "points": [], "axes": []}

    texts = [str(t) for t in long_pool.texts]
    layers = [long_pool.layer_of(i) for i in range(len(texts))]
    hits = [long_pool.hits_at(i) for i in range(len(texts))]
    n_long = len(texts)

    c = VP[:n_b].mean(axis=0)
    c = c / max(float(np.linalg.norm(c)), 1e-12)
    Wc_all = VP - c
    r_true = np.linalg.norm(Wc_all, axis=1)

    note = None
    if temp_V is None and len(getattr(temp_pool, "items", [])):
        note = "短期记忆这次没进图：缓存里还没有它们的向量，下一次归档补上"

    # 坐标：底座条目 + （有向量的）临时条目，用**同一份锚**算
    Xl = ax.coords(VP, Ap, Am, center)
    if temp_V is not None and len(temp_V) and len(temp_pool.items) == len(temp_V):
        TV = np.asarray(temp_V, dtype=float)
        TV = TV / np.maximum(np.linalg.norm(TV, axis=1, keepdims=True), 1e-12)
        texts = texts + [str(it.text) for it in temp_pool.items]
        # ★ 2026-10-05：临时条目现在有**三档**（短期 / 中期 / 悬着）⇒ 层标签由池子自己给。
        #   ⛔ 以前这里硬写 `["临时"] * n` —— 于是中期与悬着的事在图上**与短期同色**、
        #     图例里也不分档，看着就是"画不全"（用户原话）。
        #   ⚠️ 层标签只影响**画图与图例**，不影响坐标：`layer` 在这两个函数里
        #     只被 `base_r`（取 `== "底色"`）和出口的 `layer` 字段用到。
        layers = layers + [temp_pool.layer_label(i) for i in range(len(temp_pool.items))]
        hits = hits + [int(getattr(it, "hits", 0) or 0) for it in temp_pool.items]
        Wt = TV - c
        r_true = np.concatenate([r_true, np.linalg.norm(Wt, axis=1)])
        X = np.vstack([Xl, ax.coords(TV, Ap, Am, center)])
    else:
        X = Xl

    # ★ 落点权重：**不用 softmax、也不用 τ** —— 直接用坐标当权重。
    #
    # 为什么不用 softmax（实测退化了）：
    #   中心化之后，锚自己那根轴与别的轴的坐标差很小（选择性中位 ~1.9，只是"略高"），
    #   于是 softmax 是把**一把快刀砍在一个很平的信号上**：τ 大一点就均匀、小一点就独热。
    #   实测按"锚自己权重中位 = 0.5"解出来的 τ = 0.001 ⇒ 混杂度中位 **0.000**、
    #   落点仍不对称 ⇒ 等于只画了框架几何、没画数据。
    #
    # 改成：`dir_i = normalize(Σ_k x_ik · u_k)`。**没有参数**，而且两件事自动成立：
    #   · 坐标集中在某根轴 ⇒ 落在该轴上；
    #   · 坐标混在两根轴 ⇒ 落在他俩之间（用户要的"成分混杂"）；
    #   · **坐标是负的 ⇒ 自动落到那根轴的对面** ⇒ "语义相反 ⇒ 原点对称"由构造保证。
    # ★★ 落点：**只用 top-2 两根轴**（2026-10-05 实测订正）
    #
    # 上一版用 `normalize(Σ_k x_k·u_k)`（36 根全加）——**实测不成立**：
    #   合成方向与它 top1 那根轴的余弦 中位 **0.217**，而把坐标**打乱**的对照是 **0.239**
    #   ⇒ 与乱猜无异；40% 的条目方向还相反。
    #   根因是结构性的：**3D 里的轴方向是"约定"的**（斐波那契球摆放），不是语义空间里的方向
    #   ⇒ 把 36 个**带正负号**的坐标沿约定方向矢量相加，正负抵消后必然接近随机。
    #
    # 改成只用**两根**：方向由 top-1 与 top-2 按权重混合
    #   ⇒ 混在两根轴之间的条目自然落在**两根射线之间**（用户要的"成分混杂"），
    #     而且只有两项、不会互相抵消。
    Xr = X - X.mean(axis=1, keepdims=True)      # 逐条减均值：留"相对突出"，见上面那条规矩
    A = np.abs(Xr)
    order = np.argsort(-A, axis=1)
    t1, t2 = order[:, 0], order[:, 1]
    rows = np.arange(len(X))
    a1, a2 = A[rows, t1], A[rows, t2]
    w1 = a1 / np.maximum(a1 + a2, 1e-12)
    s1 = np.where(Xr[rows, t1] >= 0, 1.0, -1.0)
    s2 = np.where(Xr[rows, t2] >= 0, 1.0, -1.0)

    dirs = ax.frame_dirs(ids)                   # (k,3) 约定几何
    d1 = dirs[t1] * s1[:, None]
    d2 = dirs[t2] * s2[:, None]
    raw = d1 * w1[:, None] + d2 * (1.0 - w1)[:, None]
    nrm = np.linalg.norm(raw, axis=1, keepdims=True)
    zero = (nrm[:, 0] < 1e-9)
    if zero.any():
        raw[zero] = d1[zero]
        nrm = np.linalg.norm(raw, axis=1, keepdims=True)
    unit = raw / np.maximum(nrm, 1e-12)
    pos = unit * r_true[:, None]

    # 对齐度：合成方向到底指不指着它 top-1 那根射线（上一版 0.217；这一版应当高）
    align = np.einsum("ij,ij->i", unit, d1)

    # ★ 每根轴"被用到多少"：拿**该轴锚自己的量级**当尺子（现算对照，不当绝对阈值）
    Xa = ax.coords(np.asarray(axes_data["V"], dtype=float), Ap, Am, center)
    own_idx = np.array([ids.index(str(a)) for a in axes_data["id"]], dtype=int)
    scale = np.array([np.abs(Xa[own_idx == j, j]).mean() for j in range(k)])
    use = np.array([np.abs(Xr[:, j]).mean() / max(scale[j], 1e-12) for j in range(k)])
    stance = X.mean(axis=0)                     # 整体偏哪一端（不逐条减均值）
    # ★ 空轴 = **手标**（内容层判断），不是算出来的。
    #   实测：36 根的 use 全在 0.11–0.18 ⇒ 数据判不出"这一维没人"
    #   （条目对每根轴的亲近度差别太小）。⇒ 空轴照画，只是标出来、线画淡一点。
    marked_empty = {}
    for a, e in zip(axes_data["id"], axes_data.get("empty", [])):
        marked_empty[str(a)] = bool(int(e))

    pts = [{
        "i": i, "layer": layers[i], "temp": bool(i >= n_long),
        "text": texts[i],
        "x": float(pos[i, 0]), "y": float(pos[i, 1]), "z": float(pos[i, 2]),
        "r": float(r_true[i]), "r3": float(np.linalg.norm(pos[i])),
        "hits": hits[i],
        "t": _time_of(long_pool, temp_pool, i),
        "axis": ids[int(t1[i])], "axis2": ids[int(t2[i])],
        "pole": ("+" if s1[i] > 0 else "-"),
        "w1": float(w1[i]), "mix": float(1.0 - w1[i]),
        "align": float(align[i]),
    } for i in range(len(texts))]

    kept = pts
    if len(pts) > max_points:
        rank = sorted(range(len(pts)), key=lambda i: (pts[i]["hits"], -pts[i]["r"]), reverse=True)
        keep = set(rank[:max_points])
        kept = [pts[i] for i in sorted(keep)]

    axis_rows = [{
        "id": ids[j], "name": names.get(ids[j], ""),
        "x": float(dirs[j, 0]), "y": float(dirs[j, 1]), "z": float(dirs[j, 2]),
        "use": float(use[j]),
        "stance": float(stance[j]),
        "n_top1": int((t1 == j).sum()),
        "n_top2": int((t2 == j).sum()),
        "empty": bool(marked_empty.get(ids[j], False)),
    } for j in range(k)]

    shell = axes_quality(X, Xr, Xa, own_idx, align)
    return {
        "ok": True,
        "mode": "frame",
        "points": kept,
        "n_total": len(pts),
        "n_drawn": len(kept),
        "n_center": n_b,
        "axes": axis_rows,
        "n_axes": k,
        "quiet_axes": [a["id"] for a in axis_rows if a["empty"]],
        "mix_median": float(np.median(1.0 - w1)) if len(w1) else 0.0,
        "max_r": float(r_true.max()) if len(r_true) else 1.0,
        "base_r": float(r_true[:n_b].mean()) if n_b else 0.0,
        "note": note,
        **shell,
    }


def axes_quality(X, Xr, Xa, own_idx, align) -> Dict[str, object]:
    """给界面用的质量数字（都现算）。

    · `ax_sel_anchor` 锚的**选择性**中位 = 锚在自己轴上的 |坐标| ÷ 别的轴最大 |坐标|
      （>1 = 有分辨力；天然对照 <1，见轴框架文档 §9.2）
    · `ax_align_med`  ★ 落点**对齐度**中位 = 合成方向 与 它 top-1 射线 的余弦
      （上一版 36 根全加的写法实测 **0.217**，与"打乱坐标"的对照 0.239 无异 ⇒ 作废；
        这一版只用 top-2，应当明显更高）
    """
    def sel(M):
        o = []
        for i in range(M.shape[0]):
            own = abs(float(M[i, own_idx[i]]))
            rest = np.delete(np.abs(M[i]), own_idx[i])
            o.append(own / max(float(rest.max()), 1e-9))
        return float(np.median(o))
    return {"ax_sel_anchor": sel(Xa),
            "ax_align_med": float(np.median(align)) if len(align) else 0.0,
            "ax_align_lo": float((np.asarray(align) < 0.5).mean()) if len(align) else 0.0,
            "ax_n": int(Xa.shape[1])}
