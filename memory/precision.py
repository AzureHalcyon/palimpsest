# -*- coding: utf-8 -*-
"""跨平台数值一致性探针（**放在包里，因为它要在手机上跑**）。

    from memory.precision import report, verdict
    ok, lines = verdict(report())

背景（2026-10-05 用户提出"手机上会不会因为计算顺序 / 精度出故障"）
----------------------------------------------------------------
把它拆成三句，每句都可验证：

1. **持久化的数字不会分叉。** 池子向量存盘一律 float32（`engine.save`），两边**读的是
   同一个文件**，float32→float64 是精确的；编码器是远端 API，JSON 十进制→double 是
   正确舍入的。⇒ 输入**逐位相同**。
2. **一次归约会差最后几个 ulp。** 点积 / softmax / 累计质量走 BLAS，手机是 Chaquopy 的
   openblas、桌面是 numpy 自带那套（实测 `scipy-openblas 0.3.28`）⇒ 归约顺序不同，
   结果差 ~1e-16。这不影响 `k`（除极端边界），也不累积（池子只在归档时由**模型输出**改，
   不由浮点改）。
3. **真正会分叉的是"精确同分"的排序先后** —— 已在 `Reader._order()` 里用 `lexsort` 钉成
   全序（分数降序、同分按池内序号升序）。本探针把这条也一起量出来。

探针给出四行摘要，前三行**必须一致**，第四行是诊断：

    discrete_digest   `k` / `k_raw` / 入选的是哪几条          ← 必须逐位一致
    picked_digest     入选条目的下标序列                      ← 必须逐位一致
    rounded_digest    分数取 12 位有效数字后的摘要              ← 必须一致
    hashing_digest    替身编码器输出（整数计数）               ← 必须逐位一致
    exact_digest      分数的完整 repr  ← **允许不同**：那是归约顺序的最后几个 ulp

★ 输入是**逐位可复现**的（splitmix64 纯整数 + 除以 2 的幂，见 `_exact_matrix`）。
  否则"两边不一致"可能只是输入不同，这种探针等于没测。
"""
from __future__ import annotations

import hashlib
import json
import platform
from typing import Dict, List, Tuple

import numpy as np

from .config import Config, EmbedConfig
from .embedder import HashingEmbedder
from .pool import LongPool, TempPool
from .reader import Reader

POOL_N, DIM, NQ = 24, 1024, 6

# ★★ 参考值：**桌面**上跑出来的（`AMD64 · numpy 2.2.1 · scipy-openblas 0.3.28 · Python 3.13.0`）。
#    手机跑同一份代码，`verdict()` 会直接告出"是不是同一套结果"。
#    ⚠️ 只有前四行必须一致；`exact_digest` 允许不同（见文件头第 2 条）。
REFERENCE: Dict[str, str] = {
    "discrete_digest": "357b5ee5cc5d1c76",
    "picked_digest": "57c414d63bc7293a",
    "rounded_digest": "7e6005229ec302c8",
    "hashing_digest": "8b65339069ad4049",
    "exact_digest": "96bd7f1d8594ccd2",
    "_from": "Windows x86_64 · Python 3.13.0 · numpy 2.2.1 · scipy-openblas 0.3.28",
}

# 必须一致的 / 允许不同的
MUST = ("discrete_digest", "picked_digest", "rounded_digest", "hashing_digest")
MAY = ("exact_digest",)

_HASH_TEXTS = [
    "他最近在戒烟，第三天了", "猫昨天吐了一次", "他搬完家还没收拾完", "六楼那间屋子一直空着",
    "他手机充电器落在客厅了", "他说这个月项目要上线", "他不太爱解释自己", "深夜写代码是老习惯",
    "他妈妈上个月做了个手术", "他前年买过一台二手微单", "他不修图，最多裁一下", "他讨厌开会",
    "他最近睡得晚", "他换了工作，还在适应", "他养的那只猫叫小满", "他周末想去爬山",
    "他不太会拒绝别人", "他在准备一个考试", "他喜欢一个人吃饭", "他上周把车送去修了",
]


def _exact_matrix(rows: int, cols: int, seed: int) -> np.ndarray:
    """**逐位可复现**的矩阵：splitmix64（纯整数运算）+ 除以 2 的幂。

    为什么这么绕：
      · 不用 RNG —— PCG64 的流兼容性虽有保证，但没必要赌；
      · 不用 `sin`/`exp` —— libm 实现之间可能差 1 ulp，"输入不同"会让整个探针失去意义；
      · 也**不用 `(i*a + j*b) % m` 那种取模矩阵** —— 相邻行只差一个常数平移、行间高度相关，
        `softmax(tau=0.02)` 会把它压成 k≡1，探针就只测到一条路径
        （2026-10-05 踩过：六问全是 k=1）。
    splitmix64 出来的元素彼此独立、余弦≈0 ⇒ 分布是平的；再**种几问精确命中**，
    两条路径（k=1 与未截断的 k）就都有了。每个元素 = 24 位整数 ÷ 2^24 ⇒ 逐位相同。
    """
    idx = np.arange(rows * cols, dtype=np.uint64) + np.uint64(seed)
    x = idx * np.uint64(0x9E3779B97F4A7C15)
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    x = x ^ (x >> np.uint64(31))
    v = (x >> np.uint64(40)).astype(np.float64) / float(1 << 24) - 0.5
    return v.reshape(rows, cols)


class FixedEmbedder:
    """按顺序吐固定向量（探针专用；**不假装**语义嵌入）。"""

    def __init__(self, Q: np.ndarray):
        self.Q = Q
        self.cfg = EmbedConfig(model="fixed-probe", dim=int(Q.shape[1]))
        self.n = 0

    @property
    def dim(self) -> int:
        return int(self.Q.shape[1])

    @property
    def fingerprint(self) -> tuple:
        return (self.cfg.model, self.dim)

    def raw_embed(self, texts):
        out = self.Q[self.n:self.n + len(texts)]
        self.n += len(texts)
        return out


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _blas() -> str:
    """BLAS 名字（**只作诊断**，不进任何摘要 ⇒ 取不到也不影响判决）。"""
    try:
        cfg = np.show_config(mode="dicts") or {}          # numpy>=2 返回 dict
        b = (cfg.get("Build Dependencies") or {}).get("blas") or {}
        return ("%s %s" % (b.get("name", "?"), b.get("version", ""))).strip()
    except Exception:  # noqa: BLE001
        return "?"


def report() -> dict:
    """跑一遍固定输入，给出四行摘要 + 人读样本。"""
    cfg = Config()
    V = _exact_matrix(POOL_N, DIM, 20261005)
    Q = _exact_matrix(NQ, DIM, 777777)
    # ★ 前一半查询在池子里**种上精确命中**（V[i] = Q[i]，余弦 ≈ 1）⇒ 那几问 k 很小；
    #   后一半是平的 ⇒ k 不被上限截断但也更大。两条路径都要覆盖。
    planted = NQ // 2
    V[:planted] = Q[:planted]
    pool = LongPool(texts=["池内第 %d 条" % i for i in range(POOL_N)], V=V)
    pool.prep()
    emb = FixedEmbedder(Q)
    reader = Reader(cfg.reader, emb, cfg.salience)

    rows = []
    for m in range(NQ):
        emb.n = m                      # 这一次查询用第 m 行（文本→向量不由本探针负责）
        res = reader.retrieve_range("查询 %d" % m, 0.0, pool, TempPool(cfg.time), None)
        rows.append({"k": res.k, "k_raw": res.k_raw, "eff_n": float(res.eff_n),
                     "picked": [int(h.idx) for h in res.hits],
                     "scores": [float(h.score) for h in res.hits]})

    discrete = {"k": [r["k"] for r in rows], "k_raw": [r["k_raw"] for r in rows],
                "picked": [r["picked"] for r in rows]}
    rounded = [[round(s, 12) for s in r["scores"]] for r in rows]
    exact = [[repr(float(s)) for s in r["scores"]] for r in rows]
    HV = HashingEmbedder(dim=256).raw_embed(_HASH_TEXTS)

    return {
        "env": {"machine": platform.machine(), "python": platform.python_version(),
                "numpy": np.__version__, "blas": _blas()},
        "discrete_digest": _digest(discrete),
        "picked_digest": _digest(discrete["picked"]),
        "rounded_digest": _digest(rounded),
        "exact_digest": _digest(exact),
        "hashing_digest": _digest([[repr(float(x)) for x in row] for row in HV]),
        "sample": {"k": discrete["k"], "k_raw": discrete["k_raw"],
                   "picked0": discrete["picked"][0][:6],
                   "eff_n0": repr(rows[0]["eff_n"]),
                   "score0_top3": exact[0][:3],
                   "hashing_head": [repr(float(x)) for x in HV[0][:4]]},
    }


def verdict(rep: dict) -> Tuple[bool, List[str]]:
    """把报告与桌面参考值比对，返回 `(是否一致, 人读的行)`。"""
    env = rep.get("env") or {}
    lines = ["环境：%s · Python %s · numpy %s · BLAS %s"
             % (env.get("machine"), env.get("python"), env.get("numpy"), env.get("blas")),
             "样本：k=%s · k_raw=%s" % (rep["sample"]["k"], rep["sample"]["k_raw"]),
             "      第 0 问入选 %s · 分数 %s"
             % (rep["sample"]["picked0"], rep["sample"]["score0_top3"][:1])]
    if not REFERENCE:
        lines.append("（没有桌面参考值 ⇒ 只报数，不判）")
        return True, lines
    ok = True
    for k in MUST:
        same = rep.get(k) == REFERENCE.get(k)
        ok = ok and same
        lines.append("  %-16s %s" % (k, "一致 ✓" if same
                                     else "**不一致** ✗（参考 %s）" % REFERENCE.get(k)))
    for k in MAY:
        same = rep.get(k) == REFERENCE.get(k)
        lines.append("  %-16s %s" % (k, "一致 ✓" if same
                                     else "不同 —— 允许（归约顺序的最后几个 ulp）"))
    lines.append("结论：%s（参考来自 %s）"
                 % ("与桌面**完全一致** ✓" if ok else "**与桌面不一致** —— 见上面 ✗",
                    REFERENCE.get("_from", "?")))
    return ok, lines
