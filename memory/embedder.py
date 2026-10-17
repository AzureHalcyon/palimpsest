# -*- coding: utf-8 -*-
"""编码器（embedding）客户端 + ★ 坐标系规矩。

★★ 本文件里最要紧的一条 —— **池子用什么坐标系，查询就必须用同一个**。

  有一种很常见的封装：编码后**减掉全体均值再归一化**（目的是让向量更"居中对齐"，
  对**聚类**确实有帮助）。但它一旦被用在**检索**上，池子和查询就会落在两个不同坐标系里。

  【实测】把两者混起来的代价：
    · 被减掉的均值范数 **0.693**（≈ 单条向量范数的 69%）；
    · 两边夹角中位 **43.2°**；
    · 对同一个池子，top-12 只重合 **6.7/12**（某个查询能取到的条目数从 9 掉到 4，
      另一个从 4 掉到 **0**）。
  ⇒ 结论：**本仓库只用直连端点取原始向量**，并且**不提供**任何会偷偷减均值的封装。
  ⇒ `selftest` 里有一条测试专门守住它（用均值变体去查，排序必须明显变差 = 陷阱还在）。
  ⇒ 换编码器时，池子与查询要**一起**重建（见 `Engine.save/load` 里的编码器指纹校验）。
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import List, Sequence

import numpy as np

from .config import Credentials, EmbedConfig


def l2norm(X: np.ndarray) -> np.ndarray:
    """按行做 L2 归一化（池子与查询都走它）。**不做中心化 / 不减均值**。"""
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[None, :]
    return X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)


def embed_fingerprint(cfg: EmbedConfig) -> tuple:
    """编码器身份 = `(模型名, 维度)`。★ **换它就是换坐标系** ⇒ 缓存必须重建。

    ★ 这个函数存在的唯一理由是**单一出处**：只读路径（`Engine.peek`，界面上"看池子"用）
      也需要这个指纹来校验缓存，但它**不该为了拿指纹而先造一个 `Embedder`** ——
      造 `Embedder` 要先有凭证，而"读池子"跟凭证毫无关系。
      若让两条路各算一次指纹，迟早会漂移；漂移的表现是
      **界面显示的池子与实际用的池子不是同一个** —— 正是本仓库最防的那种事。
    """
    return (cfg.model, cfg.dim)


class Embedder:
    """直连端点取原始向量。批量上限【实测】12–24，稳妥取 ≤20。"""

    def __init__(self, cfg: EmbedConfig, creds: Credentials):
        self.cfg = cfg
        self.url = creds.embed_base_url + "/embeddings"
        self.key = creds.embed_api_key

    @property
    def fingerprint(self) -> tuple:
        """编码器身份 = `(模型名, 维度)`。★ **换它就是换坐标系** ⇒ 缓存必须重建。"""
        return embed_fingerprint(self.cfg)

    def raw_embed(self, texts: Sequence[str]) -> np.ndarray:
        texts = [("" if t is None else str(t)) for t in texts]
        if not texts:
            return np.zeros((0, self.cfg.dim), dtype=float)
        out: List[List[float]] = []
        for i in range(0, len(texts), self.cfg.batch):
            chunk = list(texts[i:i + self.cfg.batch])
            d = self._post({"model": self.cfg.model, "input": chunk})
            out.extend(e["embedding"] for e in sorted(d["data"], key=lambda e: e["index"]))
        return np.array(out, dtype=float)

    def _post(self, body: dict) -> dict:
        data = json.dumps(body).encode()
        last = None
        for attempt in range(self.cfg.retries):
            try:
                rq = urllib.request.Request(self.url, data=data, method="POST")
                rq.add_header("Authorization", "Bearer " + self.key)
                rq.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(rq, timeout=self.cfg.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.cfg.retries - 1:
                    time.sleep(self.cfg.backoff * (attempt + 1))
                    continue
                raise
            except Exception as e:  # noqa: BLE001  网络层（含 TLS 瞬断）
                last = e
                if attempt < self.cfg.retries - 1:
                    time.sleep(self.cfg.backoff * (attempt + 1))
                    continue
                raise
        raise last  # pragma: no cover


def _stable_hash(s: str) -> int:
    """★ **跨进程稳定**的哈希（替身编码器必须用它，不能用内置 `hash()`）。

    【实测 2026-10-05】原来用 `hash((seed, g))`：同一个脚本**三次新进程给出三组
    不同向量**（`[0,0,0]` / `[0,0.5,0]` / `[0,0,0]`），固定 `PYTHONHASHSEED=0` 才稳定
    —— 因为内置 `hash()` 对字符串**按 PYTHONHASHSEED 加盐**。

    后果比"自检偶尔失败"严重：`fingerprint` 是 `("hashing-stub", 维度)`，
    两个进程**指纹一样、向量不一样** ⇒ 离线建的缓存在另一个进程里读出来就是另一套
    向量，而指纹校验**照样通过**。这正是本仓库最防的那种"**偷偷混用两个坐标系**"。
    ⇒ 换成 `blake2b`（跨进程、跨平台稳定）。
    """
    return int.from_bytes(
        hashlib.blake2b(s.encode("utf-8"), digest_size=8, person=b"cmp-embed").digest(),
        "big")


class HashingEmbedder:
    """**离线替身**：只给自检/单测用（无网络、无凭证）。

    ★ 它**不假装**是语义嵌入，而是"确定性的、对文本敏感"的向量源 ——
    用来验证**流程**（条数由分布密度决定、时间衰减、归档落地、坐标系自检）。
    ★ 这里的"确定性"是**跨进程**的（见 `_stable_hash` 那条实测记录）：否则离线跑出来的
    东西不可复现，而"不可复现"在本仓库等于"两套坐标系"。
    """

    def __init__(self, dim: int = 256, seed: int = 20261005):
        self.cfg = EmbedConfig(model="hashing-stub", dim=dim)
        self.dim = dim
        self.seed = seed

    @property
    def fingerprint(self) -> tuple:
        return (self.cfg.model, self.dim)

    def raw_embed(self, texts: Sequence[str]) -> np.ndarray:
        rows = []
        for t in texts:
            v = np.zeros(self.dim, dtype=float)
            s = str(t)
            grams = [s[i:i + 2] for i in range(max(len(s) - 1, 1))] or [s]
            for g in grams:
                h = _stable_hash("%d|%s" % (self.seed, g))
                v[h % self.dim] += 1.0
                v[(h >> 8) % self.dim] += 0.5
            rows.append(v)
        return np.array(rows, dtype=float) if rows else np.zeros((0, self.dim))


# --------------------------------------------------------------------------- 坐标系自检
def frame_angle(V: np.ndarray, W: np.ndarray) -> float:
    """两组向量的**平均夹角**（度）—— 用来量"查询与池子是不是同一个坐标系"。

    判据：【实测】干净时应接近 0°；漏换一半时 **43.2°**。
    """
    V, W = l2norm(V), l2norm(W)
    if V.shape != W.shape:
        raise ValueError("两组向量形状不一致：%s vs %s" % (V.shape, W.shape))
    cos = np.clip((V * W).sum(axis=1), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)).mean())


def topk_overlap(S1: np.ndarray, S2: np.ndarray, k: int = 12) -> float:
    """两组打分各自 top-k 的重合条数 —— 用来抓"漏换坐标系"（【实测】会掉到 6.7/12）。"""
    a = set(np.argsort(-np.asarray(S1))[:k].tolist())
    b = set(np.argsort(-np.asarray(S2))[:k].tolist())
    return float(len(a & b))
