# -*- coding: utf-8 -*-
"""轴框架：40→36 根**双极轴** + 它们的锚句向量。

## 为什么锚向量要预编码进缓存

手机上开一次「记忆」屏就要算成分权重。若那时才去编 144 句锚，就要**每次开图都调一次
编码器**（网络 + 花钱 + 断网就画不出来）。⇒ 锚向量跟 `pool.npz` 一样，是**缓存的一部分**：
`axes.npz`（`V` 144×d · `center` d · `id`/`name`/`pole` 三个字符串数组）。

## 为什么几何方向是**约定**（不是从数据算）

用户原话：**"空轴本身要画出图，但目前空间没有那一部分"** ——
空轴没有条目 ⇒ **没有任何数据能定出它的方向** ⇒ 方向只能来自约定。
⇒ 拆成两件事：**几何方向 = 约定（画骨架）· 语义权重 = 锚（决定落点）**。

于是：**框架是约定，落点是数据，密度是结论。**
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

AXES_NPZ = "axes.npz"
AXES_JSONL = "axes.jsonl"

# 组内偏离组方向的固定倾角（度）。纯约定。
TILT_DEG = 22.0


# --------------------------------------------------------------------- 读 / 写
def load_axes_file(path: str) -> List[Dict[str, object]]:
    """读 `轴锚.jsonl`：一行一句 `{"axis","name","pole","text"}`（可多 `empty` 字段）。"""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def save_axes_npz(path: str, rows: Sequence[Dict[str, object]], V: np.ndarray,
                  center: np.ndarray) -> None:
    np.savez_compressed(
        path,
        V=np.asarray(V, dtype=np.float32),
        center=np.asarray(center, dtype=np.float32),
        id=np.array([str(r["axis"]) for r in rows]),
        name=np.array([str(r.get("name", "")) for r in rows]),
        pole=np.array([str(r["pole"]) for r in rows]),
        text=np.array([str(r["text"]) for r in rows]),
        # ★ 手标的「空」：**这是内容层的判断，不是坐标层的统计量**。
        #   实测：本语料上 36 根轴的 use 全在 0.11–0.18，**数据判不出空轴**
        #   （因为条目对每根轴的亲近度差别太小）。⇒ 空轴由人标，图上照画、只是标出来。
        empty=np.array([int(r.get("empty", 0) or 0) for r in rows]),
    )


def load_axes_npz(path: str) -> Optional[Dict[str, object]]:
    """读回；文件不在就返回 None（老缓存没有这一段，界面要能说实话）。"""
    import os
    if not os.path.exists(path):
        return None
    z = np.load(path, allow_pickle=False)
    return {"V": z["V"].astype(float), "center": z["center"].astype(float),
            "id": [str(x) for x in z["id"]], "name": [str(x) for x in z["name"]],
            "pole": [str(x) for x in z["pole"]], "text": [str(x) for x in z["text"]],
            "empty": ([int(x) for x in z["empty"]] if "empty" in z.files
                      else [0] * len(z["id"]))}


# --------------------------------------------------------------------- 语义侧
def axis_table(data: Dict[str, object]) -> Tuple[List[str], Dict[str, List[int]], Dict[str, List[int]]]:
    """返回 `(轴号列表, {轴号: 正端行号}, {轴号: 负端行号})`；顺序 = 文件里首次出现的顺序。"""
    ids: List[str] = []
    pos: Dict[str, List[int]] = {}
    neg: Dict[str, List[int]] = {}
    for i, a in enumerate(data["id"]):          # type: ignore[index]
        if a not in pos:
            ids.append(a)
            pos[a], neg[a] = [], []
        (pos if data["pole"][i] == "+" else neg)[a].append(i)   # type: ignore[index]
    return ids, pos, neg


def axis_vectors(data: Dict[str, object]):
    """每根轴的两端方向 + 轴方向。返回 `(ids, names, A_plus, A_minus, D, center)`。

    `center` = **144 句锚的均值**（冻结）—— 结构发现一律在中心化空间里做
    （`实现规范-中介层.md` §「一个空间、两个表示」）。
    """
    V = np.asarray(data["V"], dtype=float)
    center = np.asarray(data["center"], dtype=float)
    ids, pos, neg = axis_table(data)
    names = {}
    for a, n in zip(data["id"], data["name"]):       # type: ignore[index]
        names.setdefault(str(a), str(n))

    def n1(v):
        v = np.asarray(v, dtype=float).reshape(-1)
        return v / max(float(np.linalg.norm(v)), 1e-12)

    Vc = V - center
    Vc = Vc / np.maximum(np.linalg.norm(Vc, axis=1, keepdims=True), 1e-12)
    Ap = np.array([n1(Vc[pos[a]].mean(axis=0)) for a in ids])
    Am = np.array([n1(Vc[neg[a]].mean(axis=0)) for a in ids])
    D = np.array([n1(Ap[k] - Am[k]) for k in range(len(ids))])
    return ids, names, Ap, Am, D, center


def coords(V: np.ndarray, Ap: np.ndarray, Am: np.ndarray, center: np.ndarray) -> np.ndarray:
    """条目在每根轴上的坐标 `x = cos(v−中心, A+) − cos(v−中心, A−)`。"""
    Vc = np.asarray(V, dtype=float) - center
    Vc = Vc / np.maximum(np.linalg.norm(Vc, axis=1, keepdims=True), 1e-12)
    return Vc @ Ap.T - Vc @ Am.T


def weights(X: np.ndarray, tau: float) -> np.ndarray:
    """成分权重：`softmax(x / τ)`，逐行归一化。**τ 必须由规则定出来，不许拍**（见 `pick_tau`）。"""
    Z = X / max(float(tau), 1e-9)
    Z = Z - Z.max(axis=1, keepdims=True)
    E = np.exp(Z)
    return E / np.maximum(E.sum(axis=1, keepdims=True), 1e-12)


def pick_tau(X_anchor: np.ndarray, own_idx: np.ndarray, target: float = 0.5) -> float:
    """★ **τ 的定法（有依据、可复算）**：

        取"让**锚句自己那根轴**上的权重中位 = `target`"的那个 τ。

    理由：锚是**这句话属于这一维**的定义；若连锚自己都散在几十根轴上，那 τ 太软（等于没定位）；
    若锚几乎独热，那 τ 太硬（"成分混杂"就消失了，而混杂是这个设计要看的东西之一）。

    ⚠️ `own_idx[i]` 是第 i 句锚**自己**那根轴的列号 —— 不能拿 `np.diag`（锚与轴不是一一对齐的）。
    """
    own_idx = np.asarray(own_idx, dtype=int)
    rows = np.arange(len(own_idx))
    lo, hi = 1e-3, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        W = weights(X_anchor, mid)
        own = float(np.median(W[rows, own_idx]))
        if own > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------- 几何侧（约定）
def _fib_sphere(n: int) -> np.ndarray:
    """斐波那契球：n 个单位方向，准均匀。**与数据无关**。"""
    i = np.arange(int(n), dtype=float) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi),
                     np.sin(theta) * np.sin(phi),
                     np.cos(phi)], axis=1)


def frame_dirs(ids: Sequence[str]) -> np.ndarray:
    """★ **几何方向：纯约定**（与数据无关，所以空轴也有方向、画得出来）。

    做法：**7 个组各占一个扇区**（斐波那契球摆 7 个组方向），**组内按序均分在一个小圆上**
    （固定倾角 `TILT_DEG`）。⇒ 同一组的轴在图上挨着，跨组的轴分得开。
    """
    groups: List[str] = []
    for a in ids:
        g = str(a)[0]
        if g not in groups:
            groups.append(g)
    gd = _fib_sphere(len(groups))
    gdir = {g: gd[i] for i, g in enumerate(groups)}

    out = np.zeros((len(ids), 3), dtype=float)
    for gi, g in enumerate(groups):
        members = [i for i, a in enumerate(ids) if str(a)[0] == g]
        u = gdir[g]
        # 组方向的局部正交基（固定取法，与数据无关）
        ref = np.array([0.0, 0.0, 1.0]) if abs(u[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        e1 = np.cross(u, ref)
        e1 = e1 / max(float(np.linalg.norm(e1)), 1e-12)
        e2 = np.cross(u, e1)
        t = np.radians(TILT_DEG)
        for j, idx in enumerate(members):
            th = 2.0 * np.pi * j / max(len(members), 1)
            v = np.cos(t) * u + np.sin(t) * (np.cos(th) * e1 + np.sin(th) * e2)
            out[idx] = v / max(float(np.linalg.norm(v)), 1e-12)
    return out
