"""
风格层 · 状态块 v0
==================

它是什么
--------
风格层里"状态"那一路的载体。给风格层提供**她的当下**：一个跨轮、跨对话留存、
带多时间尺度的向量。**⛔ 永不直接进大模型的上下文**——它只能经小模型写成文字绕一圈。

结构（v0，故意小、故意不完美，方向对就行）
------------------------------------------
分层泄漏积分器组（layered leaky reservoir）：

    文本 ──哈希编码──▶ x = tanh(W_in @ enc)        ← 瞬时
    h[0] ← (1-α₀)h[0] + α₀·tanh(W₀ @ x)           ← 快区：跟随当下
    h[k] ← (1-α_k)h[k] + α_k·tanh(W_k @ h[k-1])    ← 慢区：积分更快那一层 ⇒ 底色

- α 在对数轴上排开 ⇒ **有效时间常数差几个数量级**：快区管"跟随聊天演化"，
  ★ **最慢那一层管"对话开始时让大模型进入状态"**（跨对话留存、几乎不动）
- W 是随机正交阵乘 ρ<1 ⇒ **echo state property**：输入停了状态有稳态，⛔ 不会漂
- **读出零初始化** ⇒ 初始等价于不注入；可训（岭回归）；可拔

对齐的既有约束
--------------
| 约束 | 这里怎么落的 |
|---|---|
| 底座冻结 ＋ 外挂可训 | W_in / W 冻结随机，**只有 W_out 可训** |
| 零初始化 · 可拔 · 独立版本 | `W_out = 0` · `enable()/disable()` · `version()` |
| 跨归档用衰减，⛔ 不用硬边界 | `decay()` ＋ 慢区的天然泄漏；**没有边界这个概念** |
| ⛔ 不手列结构量 | 状态不预设低维、不列分量；尺度只定**时间常数** |
| 只吃真实发生过的交互 | 只有 `observe_user()` / `observe_her()` 两个入口 |
| 每轮两次更新 · 轮内冻结 | `freeze()` / `unfreeze()`；冻结期 `observe_*` 直接报错 |
| ⛔ 不承担记忆 | `injection()` 是**低秩倾向**，⛔ 不是可检索的内容；读不出事实 |
| 确定性 · 留痕 | `seed` ＋ `version()` ＋ `trace` |

往哪接
------
- 下一步：`injection()` 喂给端侧小模型的**旁路注入**（薄注入点 · 每 4 层 · 低秩）
- 训练：`train_readout(X, Y)` —— X 是历次 `vector()`，Y 是"当时该有的倾向"
- 小模型还没有 ⇒ 现在先只跑状态块本身，用 `selftest.py` 读它的性质
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# 默认五个尺度，相邻差 5 倍 ⇒ 有效时间常数（1/α）2 / 10 / 50 / 250 / 1250 步
DEFAULT_ALPHAS = (0.5, 0.1, 0.02, 0.004, 0.0008)


def encode_text(text: str, hash_dim: int, seed: int = 0) -> np.ndarray:
    """把一段话编成一个定长稀疏向量。特征哈希，⛔ 无参数、⛔ 不训。

    v0 只用 字 + 相邻两字，够用就好 —— 它的职责只是"给状态一个可微的输入"，
    真正的语义压缩将来归编码器（方案 B），不归这里。
    """
    s = (text or "").strip()
    v = np.zeros(hash_dim, dtype=np.float64)
    if not s:
        return v
    grams = [(s[i], 1.0) for i in range(len(s))]
    grams += [(s[i:i + 2], 1.0) for i in range(len(s) - 1)]
    key = bytes([seed % 256])
    for g, w in grams:
        h = int.from_bytes(
            hashlib.blake2b(g.encode("utf-8"), digest_size=8, key=key).digest(), "little"
        )
        v[h % hash_dim] += w if (h >> 63) & 1 else -w
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


@dataclass
class Step:
    """一轮里的一次更新。留痕用，⛔ 不进读取路径。"""
    t: int
    source: str          # 'user' | 'her'
    session: int
    delta: float         # 状态变化的 L2 幅度
    norm: float          # 变化后的状态范数


class StyleState:
    def __init__(
        self,
        dim: int = 256,
        alphas=DEFAULT_ALPHAS,
        inject_dim: int = 64,
        hash_dim: int = 4096,
        seed: int = 0,
        spectral: float = 0.9,
        input_gain: float = 1.0,
    ):
        self.dim = int(dim)
        self.alphas = np.asarray(alphas, dtype=np.float64)
        self.levels = len(self.alphas)
        self.inject_dim = int(inject_dim)
        self.hash_dim = int(hash_dim)
        self.seed = int(seed)
        self.spectral = float(spectral)
        # ★ 输入增益：太小 ⇒ tanh 停在近线性区 ⇒ 层间只是正交变换 ⇒ 储备池退化成线性滤波器。
        #   enc 已经是单位长度，所以 `W_in = gain · N(0,1)` 直接让 x 每一维的标准差 ≈ gain。
        #   gain = 1.0 ⇒ 平均|x| ≈ 0.8，tanh 真的在弯。
        self.input_gain = float(input_gain)

        rng = np.random.default_rng(seed)
        # 输入投影：冻结
        self.W_in = self.input_gain * rng.standard_normal((self.dim, self.hash_dim))
        # 层间：随机正交 × ρ<1 ⇒ 谱半径 ρ，回声状态性质
        self.W = np.empty((self.levels, self.dim, self.dim))
        for k in range(self.levels):
            q, _ = np.linalg.qr(rng.standard_normal((self.dim, self.dim)))
            self.W[k] = self.spectral * q
        # 读出：★ 零初始化 ⇒ 初始等价于不注入
        self.W_out = np.zeros((self.inject_dim, self.dim * self.levels))

        self.h = np.zeros((self.levels, self.dim))
        self.t = 0
        self.session = 0
        self.trace: list[Step] = []
        self._frozen = False
        self._enabled = True
        self.readout_version = 0
        self._trained_on = 0

    # ---------------------------------------------------------------- 结构信息

    def version(self) -> str:
        """读法版本。结构 ＋ 种子决定；读出训过就换版本。"""
        struct = json.dumps(
            [self.dim, list(map(float, self.alphas)), self.seed, self.spectral,
             self.hash_dim, self.inject_dim, self.input_gain],
            sort_keys=True,
        )
        h = hashlib.blake2b(struct.encode(), digest_size=6).hexdigest()
        return f"{h}.r{self.readout_version}"

    def time_constants(self) -> np.ndarray:
        """各层有效时间常数（步）。相邻应当差几倍到几十倍。"""
        return 1.0 / self.alphas

    def activation(self, text: str) -> np.ndarray:
        """这一句进网络后的瞬时激活 x。用来看 tanh 到底在不在非线性区干活。"""
        return np.tanh(self.W_in @ encode_text(text, self.hash_dim, self.seed))

    def activation_stats(self, text: str) -> dict:
        x = self.activation(text)
        return {
            "mean_abs": round(float(np.mean(np.abs(x))), 4),
            "frac_above_half": round(float(np.mean(np.abs(x) > 0.5)), 4),
            "saturated": round(float(np.mean(np.abs(x) > 2.5)), 4),
        }

    # ---------------------------------------------------------------- 开关

    def freeze(self) -> None:
        """★ 一轮之内冻结：loop 的多跳共享同一份状态，⛔ 不自我引燃。"""
        self._frozen = True

    def unfreeze(self) -> None:
        self._frozen = False

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        """可拔。摘掉之后 `injection()` 恒为零 ⇒ 下游等价于没注入。"""
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---------------------------------------------------------------- 更新

    def _step(self, text: str, source: str) -> float:
        if self._frozen:
            raise RuntimeError("状态已冻结（轮内冻结）：这一轮内 ⛔ 不许再更新")
        enc = encode_text(text, self.hash_dim, self.seed)
        x = np.tanh(self.W_in @ enc)
        before = self.h.copy()

        self.h[0] = (1 - self.alphas[0]) * self.h[0] + self.alphas[0] * np.tanh(self.W[0] @ x)
        for k in range(1, self.levels):
            drive = np.tanh(self.W[k] @ self.h[k - 1])
            self.h[k] = (1 - self.alphas[k]) * self.h[k] + self.alphas[k] * drive

        self.t += 1
        delta = float(np.linalg.norm(self.h - before))
        self.trace.append(Step(self.t, source, self.session, delta, float(np.linalg.norm(self.h))))
        return delta

    def observe_user(self, text: str) -> float:
        """生成前：吃用户这一句（即时）。"""
        return self._step(text, "user")

    def observe_her(self, text: str) -> float:
        """生成后：吃她的回应（持续）。⛔ 不吃 prefill、⛔ 不吃 CoT——那些没发生。"""
        return self._step(text, "her")

    def new_session(self) -> None:
        """★ 换一段对话：⛔ **不重置**状态。跨对话留存就是靠这一条。"""
        self.session += 1

    def decay(self, factor: float) -> None:
        """跨归档的模糊地带：衰减，⛔ 不用硬边界/复位点。"""
        self.h *= float(factor)

    # ---------------------------------------------------------------- 读出

    def vector(self) -> np.ndarray:
        return self.h.reshape(-1).copy()

    def injection(self) -> np.ndarray:
        """给旁路注入用的低秩倾向向量。

        ★ 这是状态**唯一**的出口，而且它是一段倾向，⛔ 不是内容。
        它 ⛔ 不直接进大模型的上下文——必须先经小模型写成文字。
        """
        if not self._enabled:
            return np.zeros(self.inject_dim)
        return self.W_out @ self.vector()

    def train_readout(self, X: np.ndarray, Y: np.ndarray, lam: float = 1e-3) -> None:
        """训读出（唯一可训的东西）。X: (N, dim*levels) ← 历次 `vector()`；Y: (N, inject_dim)。"""
        X = np.atleast_2d(np.asarray(X, dtype=np.float64))
        Y = np.atleast_2d(np.asarray(Y, dtype=np.float64))
        n, d = X.shape
        if Y.shape[1] != self.inject_dim:
            Y = Y.reshape(n, -1)
        if Y.shape[1] != self.inject_dim:
            raise ValueError(f"Y 的列数应当是 {self.inject_dim}，拿到 {Y.shape[1]}")
        if n <= d:                                   # 对偶式：样本少时更省
            a = np.linalg.solve(X @ X.T + lam * np.eye(n), Y)
            w = X.T @ a
        else:
            w = np.linalg.solve(X.T @ X + lam * np.eye(d), X.T @ Y)
        self.W_out = w.T.copy()
        self.readout_version += 1
        self._trained_on += n

    # ---------------------------------------------------------------- 存续

    def save(self, path: str | Path) -> None:
        np.savez(
            path,
            h=self.h,
            W_out=self.W_out,
            meta=np.array([json.dumps({
                "dim": self.dim, "alphas": list(map(float, self.alphas)),
                "inject_dim": self.inject_dim, "hash_dim": self.hash_dim,
                "seed": self.seed, "spectral": self.spectral,
                "input_gain": self.input_gain,
                "t": self.t, "session": self.session,
                "readout_version": self.readout_version,
            })], dtype=object),
        )

    @classmethod
    def load(cls, path: str | Path) -> "StyleState":
        z = np.load(path, allow_pickle=True)
        meta = json.loads(str(z["meta"][0]))
        s = cls(dim=meta["dim"], alphas=meta["alphas"], inject_dim=meta["inject_dim"],
                hash_dim=meta["hash_dim"], seed=meta["seed"], spectral=meta["spectral"],
                input_gain=meta.get("input_gain", 8.0))
        s.h = z["h"]
        s.W_out = z["W_out"]
        s.t = meta["t"]
        s.session = meta["session"]
        s.readout_version = meta["readout_version"]
        return s

    # ---------------------------------------------------------------- 留痕

    def summary(self) -> dict:
        return {
            "version": self.version(),
            "levels": self.levels,
            "input_gain": self.input_gain,
            "time_constants": [round(float(x), 1) for x in self.time_constants()],
            "t": self.t,
            "session": self.session,
            "enabled": self._enabled,
            "norm": round(float(np.linalg.norm(self.h)), 6),
            "injection_norm": round(float(np.linalg.norm(self.injection())), 6),
            "trained_on": self._trained_on,
        }
