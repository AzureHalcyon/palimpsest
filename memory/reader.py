# -*- coding: utf-8 -*-
"""读取器 —— **全系统唯一的读取入口**（正常生成的注入、"再查一次"、归档里的查池子，全走它）。

形式：

    s_i = cos(q, v_i)                     # 原始向量、扁平、一个池子、跨层不分槽
    p   = softmax(s_i / tau)              # 连续表示；tau = 范围旋钮
    k   = clip(累计质量 ≥ mass 所需条数, 下限, 上限)
    取 top-k（**按 s_i**）→ 这批全部进入上下文

★ **条数由该处的分布密度自然决定**：同一套参数、不同开场，实测条数范围 **1–37**
  —— 稀疏的话题只想起几条，密集的话题想起十几条。
★ ⛔ 已经定下的原则是「**读取的输出是连续表示，条数本来就该由分布自己决定**」
  ⇒ **上限只是成本截断，不是旋钮；下限本身就是个旋钮** ⇒ 所以下限 = 1（不设）。
★ ⚠️ **必须把"被上限截断/被下限兜住"的比例报出来**，否则会把"固定条数"误当成连续表示：
  【实测】某一档参数下中位条数 2、范围 1–37，但 **52% 的轮次被下限抬过、18% 被上限截断**
  ⇒ 那一档实际上在跑**固定条数**。所以 `ReadResult` 把 `k_raw`（未截断的条数）和 `k`
  （真正进上下文的条数）**一起**返回。

★ **深刻度**（见 `config.SalienceConfig`，**默认关**）：开启时 `k` **仍只由相关度定**
  （⛔ 深刻度不许参与"取哪几条"，否则等于让"我心里多重"去污染"跟情境像不像"），
  只把这 k 条的**顺序**改成 `相关度 + κ·log w`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .config import ReaderConfig, SalienceConfig
from .embedder import l2norm

LAYER_TEMP = "临时"


@dataclass
class Hit:
    text: str
    score: float          # 相关度：与查询的余弦
    idx: int              # 在"长期池 + 存活临时条目"这个拼接序列里的下标
    layer: str
    w: float = 1.0        # 深刻度（`1 + n_d + n_p`）；长期池条目暂无逐条计数 ⇒ 1.0


@dataclass
class ReadResult:
    hits: List[Hit]
    eff_n: float          # 有效条目数 = exp(熵(p))：分布有多"摊"的诊断量
    k_raw: int            # 截断前的条数（诊断用：报触底/触顶占比）
    k: int                # 截断后的条数 = 真正进上下文的条数
    gate_blocked: int = 0  # ★ 被**阈值门**挡掉几条（相关度 < gate_tau）；`k` 已扣掉它们

    @property
    def texts(self) -> List[str]:
        return [h.text for h in self.hits]


def softmax_p(S: np.ndarray, tau: float) -> np.ndarray:
    """数值稳定的 softmax（tau = 范围：越小越尖，越大越平）。"""
    z = (np.asarray(S, dtype=float) - float(np.max(S))) / max(tau, 1e-6)
    e = np.exp(z)
    return e / e.sum()


def select_k(p: np.ndarray, order: np.ndarray, mass: float,
             min_items: int, max_items: int) -> Tuple[int, int]:
    """在按相关度排好的序里，取到**累计质量 ≥ mass** 为止，再 clip。

    返回 `(k_raw, k)`；`k_raw` 用来报"触底/触顶"（**诊断，不是旋钮**）。
    """
    cum = np.cumsum(p[order])
    k_raw = int(np.searchsorted(cum, mass) + 1)
    k = int(min(max(k_raw, min_items), max_items))
    return k_raw, k


class Reader:
    def __init__(self, cfg: ReaderConfig, embedder, salience: Optional[SalienceConfig] = None):
        self.cfg = cfg
        self.embedder = embedder
        self.salience = salience or SalienceConfig()

    # ------------------------------------------------------------------ 组装当前池子
    def _stack(self, long_pool, temp_pool, temp_V, now: float):
        alive = temp_pool.alive_idx(now)
        V = long_pool.prep()
        texts = list(long_pool.texts)
        layers = [long_pool.layer_of(i) for i in range(len(long_pool.texts))]
        if alive:
            Vs = l2norm(np.asarray(temp_V)[alive]) if temp_V is not None and len(temp_V) else None
            if Vs is not None and len(Vs):
                V = np.vstack([V, Vs]) if len(V) else Vs
                texts = texts + temp_pool.texts(alive)
                # ★ 2026-10-05：临时条目现在分**两档**（短期 / 中期）⇒ 标签由池子自己给。
                #   ⚠️ 这是**档位名**，不是"池子里的层"（`LAYERS` 那四个键没动）。
                layers = layers + [temp_pool.layer_label(i) for i in alive]
        return texts, layers, V, alive

    def _query_vec(self, query: str) -> np.ndarray:
        return l2norm(self.embedder.raw_embed([query]))

    # ------------------------------------------------------------------ 排序
    @staticmethod
    def _order(S: np.ndarray) -> np.ndarray:
        """★ **完全确定的**排序：分数降序，**同分按池内序号升序**。

        为什么不能只用 `np.argsort(-S)`：它默认的快排**不稳定**，而"不稳定"意味着
        **同分**条目的先后由实现细节决定 —— 不同平台、不同 numpy 版本可能给出不同顺序
        （手机上是 numpy 1.26 + `chaquopy_openblas`，桌面是 numpy 2.2）。
        后果不是 `k` 变（同分对称，累计质量一样），而是**这一批注入的到底是哪几条**会不一样
        ⇒ 同一个池子上，手机和桌面会喂给模型**不同的内容**。

        【实测】浮点本身跨平台只差最后几个 ulp（见 `tools/precision_probe.py`）；
        真正会分叉的是这种**精确同分**（例如两条文本一模一样的底色条目）。
        ⇒ 用 `lexsort` 给一个**全序**，把这件事钉死。
        """
        return np.lexsort((np.arange(len(S)), -S))

    # ------------------------------------------------------------------ 主入口
    def retrieve_range(self, query: str, now: float, long_pool, temp_pool,
                       temp_V) -> ReadResult:
        texts, layers, V, alive = self._stack(long_pool, temp_pool, temp_V, now)
        if not len(texts):
            return ReadResult(hits=[], eff_n=0.0, k_raw=0, k=0)
        qv = self._query_vec(query)
        S = (qv @ V.T)[0]

        p = softmax_p(S, self.cfg.tau)
        order = self._order(S)                       # ★ 范围只看相关度；定序见 _order()
        k_raw, k = select_k(p, order, self.cfg.mass, self.cfg.min_items, self.cfg.max_items)
        eff_n = float(np.exp(-(p * np.log(p + 1e-30)).sum()))

        picked = [int(j) for j in order[:k]]
        # ★★ **阈值门**（2026-10-06）：相关度低于 `gate_tau` 的**直接不要** ⇒ 可以一条都不给。
        #   别问"为什么"要它：没有它，"这题我根本没货"永远表现为"我给你几条最不像样的"
        #   —— 模型分不出"查到了"和"没查到"，就会拿它自己的行为准则当"关于他的事实"用。
        #   ⚠️ 它是**下界**，不是旋钮：`k` 仍由累计质量定，门只往下扣。
        #   ⚠️ **它救不了"问法本身是自指的"那一类**（那种查询分反而更高）——
        #      标定与这个否定结果见 `docs/参数与依据.md` §1 与 `参数账.md`。
        gate_blocked = 0
        if self.cfg.gate_enabled:
            keep = [j for j in picked if float(S[j]) >= float(self.cfg.gate_tau)]
            gate_blocked = len(picked) - len(keep)
            picked = keep
        hits = [Hit(text=texts[j], score=float(S[j]), idx=j, layer=layers[j],
                    w=self._w_of(j, long_pool, temp_pool, alive)) for j in picked]

        if self.salience.enabled and self.salience.kappa:
            # ★ 只改**顺序**，不动成员（⛔ 深刻度不许参与入选）
            cap = self.salience.log_w_cap
            hits.sort(key=lambda h: h.score + self.salience.kappa * min(np.log(max(h.w, 1e-9)), cap),
                      reverse=True)
        return ReadResult(hits=hits, eff_n=eff_n, k_raw=k_raw, k=len(hits),
                          gate_blocked=gate_blocked)

    # ------------------------------------------------------------------ 归档查询
    def find_text(self, pattern: str, now: float, long_pool, temp_pool, temp_V,
                  topk: int) -> List[str]:
        """归档时"**按形态找**" —— **正则**，作用域**只有池子条目**（不碰文件系统）。

        ★★ 2026-10-06（两版）：
          ① 先做的是**子串**（用户口径：「机械算出『哪个旧值还在池子里别处出现』
             可以作为工具给」）—— 治的是实测那类**跨条目的值失效**：猫改名成"豆花"后，
             另一条「他的猫**豆子**是从朋友那儿接来的」还留着；核对侧**看见了**、
             也判对了「那是另一件事」，但「那个名字已经作废」**不在它的判据里**。
          ② 用户接着点了一句：「**像你检查错误时用的这些工具，为什么不提供？甚至直接给
             一个 rg**」⇒ 升级成**正则**（`rg` 的核心能力）。理由不是"功能更多"，而是
             **通用原语胜过补丁**：子串只是正则的退化特例，而"每发现一个漏就回来加一个
             小工具"这条路走不长 —— 给它原语，让它自己组合。
        ⛔ **作用域严格限定在池子条目文本**：不读文件、不碰仓库、不跑外部进程。
        ⚠️ **非法正则** ⇒ 回一句**人能读懂的话**（不抛异常；更不能让它把"写错了"
           误当成"池子里没有"）。
        ⚠️ 匹配到几条要**说清**（"匹配 N 条"）—— 否则空结果分不清"没有"还是"没查到"。
        """
        texts, layers, _V, _alive = self._stack(long_pool, temp_pool, temp_V, now)
        pat = (pattern or "").strip()
        if not pat or not len(texts):
            return ["（没给要找的形态）"]
        try:
            rx = re.compile(pat)
        except re.error as e:                       # noqa: PERF203
            return ["正则写法有问题（%s）⇒ 换个写法再查" % e]
        nlong = len(long_pool.texts)
        hits: List[str] = []
        for j, t in enumerate(texts):
            if not rx.search(t):
                continue
            # ★ 太长的模式不许回溯到天荒地老（池子条目都短，但别指望调用方守规矩）
            hits.append("[%s #%d] %s" % (layers[j], j, t) if j < nlong
                        else "[%s·不可改] %s" % (layers[j], t))
        if not hits:
            return ["（池子里没有出现这个形态）"]
        k = max(int(topk), 1)
        head = "（匹配 **%d** 条%s）" % (len(hits), "，列前 %d" % k if len(hits) > k else "")
        return [head] + hits[:k]

    # ------------------------------------------------------------------ 归档查询
    def search(self, query: str, now: float, long_pool, temp_pool, temp_V,
               topk: int) -> List[str]:
        """归档时"查池子"：**按相关度取前 topk 条**（这里 topk 是**截断**，不是旋钮）。

        ★★ 2026-10-06：**每条带上池内编号与层**（`[记忆 #252] …`）。
        为什么必须带：原来只回文本 ⇒ dreaming 侧**拿不到编号** ⇒ 它没法说清
        「这条跟池子里第 252 条是同一件事」，只能绕着说一句新事实，
        把判断全推到核对那一步。实测代价：核对三次占了 **347.8 / 444.3 秒**，
        单次 completion **13.9k–18.1k tokens** —— **慢与漏是同一个病**。
        ⚠️ **临时池的条目没有稳定的池内下标**（按存活位置排）⇒ 只标档位、**不给编号**；
          它本来也改不了（核对的 `idx` 只认记忆层）。
        ⚠️ 这个结果**只喂给归档那一步**（`engine.archive` 的 `search` lambda）——
          **对话里的 `read_z` 不走这里**，所以他那一侧看不到池内编号。
        """
        texts, layers, V, _alive = self._stack(long_pool, temp_pool, temp_V, now)
        if not len(texts):
            return []
        nlong = len(long_pool.texts)
        S = (self._query_vec(query) @ V.T)[0]
        out: List[str] = []
        for j in self._order(S)[:max(int(topk), 0)]:
            j = int(j)
            if j < nlong:
                out.append("[%s #%d] %s" % (layers[j], j, texts[j]))
            else:
                out.append("[%s·不可改] %s" % (layers[j], texts[j]))
        return out

    # ------------------------------------------------------------------ 深刻度
    def _w_of(self, j: int, long_pool, temp_pool, alive: Sequence[int]) -> float:
        """`w = 1 + n_d + n_p`。

        ⚠️ **当前只有临时池有计数**（条目上的 `hits` 就是 `n_d`）；`n_p`（外部材料带来的
        伪计数）属**未落地** ⇒ 恒为 0。长期池条目**暂无逐条计数** ⇒ `w = 1`。
        ⇒ 所以即使打开深刻度，**它也只影响临时条目** —— 这一点写在
        `docs/未接线与已知缺口.md`，别当成"深刻度已经实现了"。
        """
        nlong = len(long_pool.texts)
        if j < nlong:
            return 1.0
        pos = j - nlong
        if pos < len(alive):
            return 1.0 + float(temp_pool.items[alive[pos]].hits)
        return 1.0
