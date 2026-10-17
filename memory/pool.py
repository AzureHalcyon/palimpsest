# -*- coding: utf-8 -*-
"""两个池子与时间模型。

**长期池**（不衰减、不淡出 —— 这是已定的产品决定）：
    底色（"它自己是谁"的先验条目） + 情境（"**此刻这样才算对**"的条目：
    **负面提醒** —— 此刻不该浮上来的东西；**处置** —— 此刻该怎么处理）
    + 记忆（关于他的事实） + 归档产出的 `facts`
**临时池**（**无保底、按时间衰减到消失**）—— 内部有**两档**（2026-10-05 起）：
    归档产出的 `kept` —— 没说完的、他当时的状态、还悬着的事
    ★ **短期**：`tau = tau_state × tag 倍数`（约 2.5–10 天）⇒ 几天就淡
    ★ **中期**：`tau = tau_mid`（45 天）⇒ **慢衰减，约 88 天不提才退**
      两个入口：① 反复提到（`again ≥ promote_hits`）/ 模型 `promote`；② **出生即中期**
      —— 归档里 `changes: true`（"会变的情形"）的那批 `facts` 直接进这里（`add_kept(mid=True)`）。

时间模型（**单位 = 天**）：

    w(t) = exp(-Δt / tau)         tau 见上面两档
    w < eps（0.14）  ⇒  退出候选（**物理删除发生在归档时**，见 `apply_archive`）
    被真实重复提起（归档的 `again`）⇒ **重置计时**（t0 = now）、计数 +1
    计数 ≥ promote_hits            ⇒ 升迁进**中期**（`tau` 换成 `tau_mid`、重新计时）
    `done` 命中中期                ⇒ **退回短期**（重置计时），**不是删除**
    ⛔ **没有任何一档是"永不衰减"** —— 曾经的 `if it.long: return 1.0` 已删，
      那正是"长期事实上等于永久"的根源（用户："永久的这个要慎重"）。

⛔ **「被取用过、但他没接着提」不进任何一边**（既不算强化，也不算淘汰）。
   理由是有数据的：他说完就走、不作表态的轮次占 **51.5%**，而"明确否定"只占 **0.5%**
   ⇒ 两者在当前数据里**分不开** ⇒ 一旦把"没接话"当负反馈，系统会把自己训成一个
   "我总说错、他总不理我"的焦虑模型。

⚠️ 曾经的算法把单位错当成"场"（`clock += 1.0`）：于是「隔两天来」和「隔两周来」**完全一样**，
   衰减常数没有可解释的含义，池子也不收敛（实测单调涨到 23 条不回头）。**已改成"天"。**
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .config import TimeConfig
from .embedder import l2norm

LAYERS = ("底色", "情境", "记忆", "临时")

# ★★ 档位（2026-10-05 新增）—— ⛔ **这不是池子里的"层"**，`LAYERS` 那四个键**不动**。
#   它是**临时池内部**的两档，只用于记账与界面：
#     短期 = 刚归档进来的（`tau` 按 tag 分档，几天就淡）
#     中期 = 被反复提到 / 被模型 promote 的（`tau = cfg.tau_mid`，一个季度左右不提才退）
#   ⇒ 用户口径："目前的长期就是永久的。所以不如顺着加一个中期，给一个慢的衰减。"
TIER_MID = "中期"
TIER_SHORT = "临时"          # 与 `reader.LAYER_TEMP` 同一个串，界面上那组仍叫"短期记忆"
# ★ 第三类（2026-10-05 · 用户裁）：**「悬着的事」不按时间退，只由 `done` 结束**。
#   ⚠️ 它**不是**"升迁"，也**不是**"长期/永久" —— 它是一个**还没结的结**：
#     该在"事办完了"时解开，不该在"88 天没提"时被忘掉。
TIER_OPEN = "悬着"


@dataclass
class TempItem:
    text: str
    t0: float                      # 上次被真实提及的时刻（天）
    tau: float                     # 该条的衰减常数（天）= tau_state × tag 倍数，**中期则是 tau_mid**
    hits: int = 0
    src: Optional[list] = None
    tag: Optional[str] = None
    from_session: Optional[int] = None
    long: bool = False             # **已升迁**（进中期）—— ⚠️ 不再等于"不衰减"，衰减看 `tau`
    how: Optional[str] = None      # 升迁路径：hits>=N / 模型promote / done降级
    tier: Optional[str] = None     # None = 短期 · `TIER_MID` = 中期（老缓存没有 ⇒ None ✓）
    hanging: bool = False          # ★ 「悬着的事」—— **不按时间退**，只由 `done` 结束


@dataclass
class LongPool:
    """长期池。`V` 存**原始向量**（不减均值）；`prep()` 只做 L2 归一化。

    `meta` 与 `texts` **一一对应**，一条一项。里面的字段现在界面上还用不到，
    但**必须先存**：漏记就再也补不回来（`meta` 自己不会被重建）。
    目前记的是：进池时刻（引擎钟 `t` 与墙钟 `ts`）、来自哪一场（`sid`）、
    怎么进来的（`how`：`seed` 建池时就有 / `dream` 归档产出 / `核对·replace` 被就地改写）。

    ⚠️ **2026-10-05 起，它不再是"只增不减"的底片**：
      · `rewrite()` —— 落档前核对可以**就地改写**一条（`how` 变成 `核对·*`）；
      · `drop()`    —— 被取代的旧条目**真删**（用户裁："原始对话和归档对话有存档就够了"）。
      ⇒ 底片性现在靠**转写**（原始对话 + 归档对话）兜，不靠池子本身。
      ⛔ 但**底色 / 情境那前 `n_baseline + n_situational` 条永远不动**（手写层，删了会移位那两个计数）。
    """

    texts: List[str] = field(default_factory=list)
    V: Optional[np.ndarray] = None
    n_baseline: int = 0
    n_situational: int = 0
    n_memories: int = 0
    # ★ 与 `texts` 等长；老缓存没有这一段 ⇒ 读回时补成空 dict，不许因为缺字段就崩
    meta: List[Dict[str, object]] = field(default_factory=list)
    _VP: Optional[np.ndarray] = None

    def prep(self) -> np.ndarray:
        """L2 归一化后的池子矩阵（带缓存；`add()` 会让缓存失效）。"""
        if self._VP is None or len(self._VP) != len(self.texts):
            self._VP = l2norm(self.V) if self.V is not None and len(self.V) else \
                np.zeros((0, 0), dtype=float)
        return self._VP

    def add(self, texts: Sequence[str], V: np.ndarray,
            meta: Optional[Sequence[Dict[str, object]]] = None) -> None:
        texts = [str(t) for t in texts]
        if not texts:
            return
        self.texts.extend(texts)
        self.V = V if self.V is None or not len(self.V) else np.vstack([self.V, V])
        # ★ `meta` 补齐到与 `texts` 等长。缺的部分写空 dict 而不是省略：
        #   一旦错位，后面按序号取元数据就会张冠李戴。
        add = list(meta or [])
        add += [{} for _ in range(len(texts) - len(add))]
        self.meta.extend(add[:len(texts)])
        self._VP = None

    def rewrite(self, idx: int, text: str, V: np.ndarray,
                patch: Optional[Dict[str, object]] = None) -> None:
        """★ **就地改写**一条长期条目（文本 + 那一行向量）。

        **为什么允许改**（用户 2026-10-05）：
          · "原始对话和归档对话有存档就够了，**条目实时更新**"
          · "但更新旧条目这个也要约束，**不能因为能更新就让旧条目无限膨胀**"
        ⇒ 改法是**就地替换**（条目数不涨）；旧文本只留**最近一次**在 `meta["updated"]["was"]`
          （单条、有界 ⇒ 不会膨胀），要回查更早的去看转写。

        ⛔ **底色 / 情境不许走这条路** —— 调用方负责；`engine` 里另有硬断言。
        """
        if not (0 <= idx < len(self.texts)):
            raise IndexError("改写越界：%d（池子 %d 条）" % (idx, len(self.texts)))
        self.ensure_meta_len()
        old = self.texts[idx]
        self.texts[idx] = str(text)
        if self.V is not None and len(self.V):
            row = np.asarray(V, dtype=float)
            if row.ndim == 2:
                row = row[0]
            self.V[idx] = row
        self._VP = None                     # 向量变了 ⇒ 归一化缓存作废
        m = self.meta[idx] if isinstance(self.meta[idx], dict) else {}
        m["updated"] = dict(patch or {}, was=old)
        self.meta[idx] = m

    def drop(self, idxs: Iterable[int]) -> int:
        """★ **从长期池里真删**这几条（文本 / 向量 / 元数据一起删）。

        ★ 2026-10-05 用户裁：被 `replace` 取代的旧条目**直接删** ——
          原话："**原始对话和归档对话有存档就够了，条目实时更新。**"
          （我先做的是"标记取代、检索不取"，用户改成真删。）
        ⛔ **只允许删记忆层**（`idx >= n_baseline + n_situational`）：
          · 删底色/情境会**移位那两个计数** `n_baseline` / `n_situational`，那是分层的地基；
          · 而且它们本来就**是手写层**，归档不碰。
        返回真删了几条。**从后往前删**（否则下标会错位）。
        """
        n_prot = self.n_baseline + self.n_situational
        every = sorted({int(i) for i in idxs}, reverse=True)
        for i in every:
            if not (n_prot <= i < len(self.texts)):
                raise IndexError("只许删记忆层：idx=%d（记忆层从 %d 起，共 %d 条）"
                                 % (i, n_prot, len(self.texts)))
        for i in every:
            self.texts.pop(i)
            if self.V is not None and len(self.V):
                self.V = np.delete(self.V, i, axis=0)
            if 0 <= i < len(self.meta):
                self.meta.pop(i)
        self.n_memories = max(0, len(self.texts) - n_prot)
        self._VP = None
        return len(every)

    def drop_any(self, idxs: Iterable[int]) -> int:
        """★ **用户**从池子里真删这几条（**底色 / 情境也允许**）—— 与 `drop()` 的差别就在这。

        为什么这条路能碰手写层，而 `drop()` 不能：
          · `drop()` 走的是**归档**那条路（**模型**在改池子）⇒ 手写层不许它碰；
          · 这条是**用户在界面上手动删** —— 底色/情境本来就是**他自己写的**，他是主人。
            （用户 2026-10-06：「对那些搜到的和 push 的条目，也加上长按修改功能，
              这样我使用时看到不合适的可以自己先手动修改」。）

        怎么保证**分层不移位**（这是 `drop()` 拒绝删它们的唯一原因）：
          先把"每一条属于哪一层"用**删之前**的计数定下来，再逐条删、逐条减对应的计数。
          ⇒ 删一条底色，后面的情境/记忆整体前移一格，但 `n_baseline` 同时减 1 ⇒ 归属不变。
        返回真删了几条。
        """
        every = sorted({int(i) for i in idxs}, reverse=True)
        for i in every:
            if not (0 <= i < len(self.texts)):
                raise IndexError("删除越界：%d（池子 %d 条）" % (i, len(self.texts)))
        kinds = []
        for i in every:                       # ★ 定型必须在删之前（用的是原计数）
            if i < self.n_baseline:
                kinds.append("底色")
            elif i < self.n_baseline + self.n_situational:
                kinds.append("情境")
            else:
                kinds.append("记忆")
        for i, kind in zip(every, kinds):
            self.texts.pop(i)
            if self.V is not None and len(self.V):
                self.V = np.delete(self.V, i, axis=0)
            if 0 <= i < len(self.meta):
                self.meta.pop(i)
            if kind == "底色":
                self.n_baseline = max(0, self.n_baseline - 1)
            elif kind == "情境":
                self.n_situational = max(0, self.n_situational - 1)
        self.n_memories = max(0, len(self.texts) - self.n_baseline - self.n_situational)
        self._VP = None
        return len(every)

    def layer_of(self, idx: int) -> str:
        if idx < self.n_baseline:
            return "底色"
        if idx < self.n_baseline + self.n_situational:
            return "情境"
        return "记忆"          # 记忆层 + 归档新增的 facts

    def composition(self, indices: Iterable[int]) -> Dict[str, int]:
        out = {k: 0 for k in LAYERS}
        for i in indices:
            out[self.layer_of(int(i))] += 1
        return out

    def entries(self) -> List[Dict[str, object]]:
        """给**界面**看的结构化条目。

        ⚠️ 与 `TempPool.view_lines()` 是两回事：那个是**给模型看的**（带序号、要它能回填
        `again/done/promote`），措辞一改就可能影响归档；这个是给人看的，随便改。
        两者共用同一套序（**都从 1 数**），免得界面上的第 3 条与归档里说的第 3 条对不上。
        """
        return [dict({"idx": i + 1, "layer": self.layer_of(i), "text": t},
                     **self.meta_at(i))
                for i, t in enumerate(self.texts)]

    def meta_at(self, i: int) -> Dict[str, object]:
        """第 i 条的元数据；没有就是空 dict（老缓存 / 建池时的种子条目）。"""
        if 0 <= i < len(self.meta) and isinstance(self.meta[i], dict):
            return self.meta[i]
        return {}

    def hits_at(self, i: int) -> int:
        """第 i 条**被想起过几次**。★ 所有读取都走这里，别直接 `meta["hits"]`。

        ⚠️ 为什么要收口（2026-10-05，用户要求把改名改完整）：
          这个字段以前叫 `reads` —— **和 `Reader` 太像**，而且同一个概念在项目里有三个名字
          （长期池 `reads` / 临时池 `hits` / 配置 `promote_hits`）。
          现在统一成 `hits`（项目本来就叫这个）。
          这里**顺手兼容旧缓存的 `reads`** —— 一行保险，老池子读回来也能算对。
        """
        m = self.meta_at(i)
        return int(m.get("hits", m.get("reads", 0)) or 0)

    def ensure_meta_len(self) -> None:
        """把 `meta` 补到与 `texts` 等长（老缓存读回来时可能是短的）。"""
        if len(self.meta) < len(self.texts):
            self.meta.extend({} for _ in range(len(self.texts) - len(self.meta)))

    def bump_hits(self, pushed: Sequence[str]) -> int:
        """把"这一场真正注入给模型的条目"计数 +1，返回命中的条数。

        ★ 为什么要在归档时累加，而不是用的时候现算：
          注入是**事件**（哪一轮、注入了哪几条），事后只能从转写里翻；
          而转写一场一行、要翻全部历史才拼得回来。归档当时顺手加一下几乎免费。
        ★ 文本当键：**每次调用现建一次**映射（不是缓存），所以
          `rewrite()` / `drop()` 之后也不会张冠李戴——改写过的旧文本自然查不到、被跳过。
        """
        self.ensure_meta_len()
        idx = {t: i for i, t in enumerate(self.texts)}
        hit = 0
        for t in pushed:
            i = idx.get(str(t))
            if i is None:
                continue
            self.meta[i]["hits"] = self.hits_at(i) + 1
            hit += 1
        return hit

    def counts(self) -> Dict[str, int]:
        """每层现在有多少条（算"上次归档后的变化"要拿它做前后快照）。"""
        out = {k: 0 for k in LAYERS}
        for i in range(len(self.texts)):
            out[self.layer_of(i)] = out.get(self.layer_of(i), 0) + 1
        return out


class TempPool:
    """临时池 + 时间模型。"""

    def __init__(self, cfg: TimeConfig):
        self.cfg = cfg
        self.items: List[TempItem] = []

    # ------------------------------------------------------------------ 时间
    def tau_of(self, tag: Optional[str]) -> float:
        v = self.cfg.tag_mult.get(str(tag or "").strip(), 1.0)
        return float(self.cfg.tau_state * v)

    def is_hanging(self, tag: Optional[str]) -> bool:
        """这个 tag 是不是**「悬着的事」**（不按时间退，只由 `done` 结束）？

        ⚠️ **精确匹配**（`strip()` 后整串相等），**不做"包含"判断** ——
          模型的 tag 是自由文本，模糊匹配会误伤（比如"他当时悬着的一个状态"）。
        ⛔ 代价：**tag 写歪了就不生效** ⇒ 提示词里必须说清这个 tag 的分量。
        """
        t = str(tag or "").strip()
        return bool(t) and t in tuple(self.cfg.hanging_tags or ())

    def w(self, it: TempItem, now: float) -> float:
        """存活度 `w = exp(-Δt / tau)`。

        ★★ **2026-10-05 改**：这里原来有一句 `if it.long: return 1.0` ——
        升迁过的条目**永不衰减**，于是"长期"事实上就等于"永久"。
        现在升迁进的是**中期（慢衰减）**，所以那句**必须去掉**：
        **不永久的东西要有下坡路**（用户："永久的这个要慎重"）。

        ★ **下面这一句短路不是上面那句的翻版**，别搞混：
          上面那句是"**升迁 = 永久**"（机制泄漏成语义，已删）；
          这一句是语义本身 —— **「悬着的事」就没有"保质期"这个概念**，
          它等的是**事情办完**，不是**时间过去**（用户 2026-10-05 裁）。
        """
        if it.hanging:
            return 1.0
        return float(math.exp(-max(0.0, now - it.t0) / max(it.tau, 1e-6)))

    def layer_label(self, idx: int) -> str:
        """给读取器 / 转写 / 界面用的**档位名**（`悬着` / `中期` / `临时`）。

        ⚠️ 这不是"池子里的层" —— `LAYERS` 那四个键不动，这里只是标签。
        """
        it = self.items[idx]
        if it.hanging:
            return TIER_OPEN
        return TIER_MID if it.long else TIER_SHORT

    def alive_idx(self, now: float) -> List[int]:
        return [i for i, it in enumerate(self.items) if self.w(it, now) >= self.cfg.eps]

    def texts(self, idx: Sequence[int]) -> List[str]:
        return [self.items[i].text for i in idx]

    def weights(self, idx: Sequence[int], now: float) -> List[float]:
        return [self.w(self.items[i], now) for i in idx]

    def entries(self, now: float) -> List[Dict[str, object]]:
        """给**界面**看的结构化条目（序号从 1 数，与 `view_lines` 对齐）。

        ★ "已淡出但还没被清掉"的条目**也要出现**，并标 `alive=False`。
          理由：临时池是**归档时才结算**的（`apply_archive` 里才删），
          所以两次归档之间，池子里本来就躺着一些 w < eps 的条目。
          界面若是只显示"活的"，用户会以为规则和实际不一致 ——
          **看得见"正在淡出"，才看得懂这套时间模型在干什么。**
        """
        out: List[Dict[str, object]] = []
        for j, it in enumerate(self.items):
            w = self.w(it, now)
            out.append({
                "idx": j + 1,
                "text": it.text,
                "tag": it.tag,
                "age_days": max(0.0, now - it.t0),
                "tau": float(it.tau),
                "w": w,
                "alive": bool(w >= self.cfg.eps),
                "hits": int(it.hits),
                "long": bool(it.long),
                "tier": it.tier,
                "hanging": bool(it.hanging),
                "how": it.how,
                "from_session": it.from_session,
            })
        return out

    # ------------------------------------------------------------------ 写入
    def add_kept(self, kept: Iterable[dict], now: float, session_k: Optional[int],
                 mid: bool = False) -> int:
        """`kept` ⇒ 临时条目。**按文本去重**（同一条不重复挂）。

        ★ `mid=True`：**出生即中期**（`tau = tau_mid`、`long=True`、`tier="中期"`、重新计时）。
          给"**会变的长期事实**"用（见 `engine` 的 facts 分流）——
          它们本来就是"持续一段时间的情形"（在戒烟 / 在找工作 / 猫在生病），
          **该直接进中期，不必等"反复出现"才升迁**。
          ⚠️ 这一条同时解决了「中期没有货源」：光靠 `kept` 升迁，中期基本是空的（实测）。
        """
        added = 0
        existing = {it.text for it in self.items}
        for it in kept or []:
            t = str(it.get("text", "")).strip()
            if not t or t in existing:
                continue
            tag = it.get("tag")
            if self.is_hanging(tag):
                # ★ 「悬着的事」：**不按时间退**（`w ≡ 1`），只由 `done` 结束。
                #   ⚠️ 它的优先级**高于** `mid` —— "不按时间退"比"慢衰减"更强。
                #   `tau` 仍写一个数（界面上要显示"这条原本多久淡"），但 `w()` 不看它。
                item = TempItem(text=t, t0=now, tau=float(self.cfg.tau_mid), hits=0,
                                src=it.get("src"), tag=tag, from_session=session_k,
                                hanging=True, how="悬着的事（不按时间退）")
            elif mid:
                item = TempItem(text=t, t0=now, tau=float(self.cfg.tau_mid), hits=0,
                                src=it.get("src"), tag=tag or "会变的事",
                                from_session=session_k, long=True, tier=TIER_MID,
                                how="出生即中期")
            else:
                item = TempItem(text=t, t0=now, tau=self.tau_of(tag),
                                hits=0, src=it.get("src"), tag=tag,
                                from_session=session_k)
            self.items.append(item)
            existing.add(t)
            added += 1
        return added

    # ------------------------------------------------------------------ 归档落地
    def apply_archive(self, ar: dict, now: float) -> Dict[str, object]:
        """把 `again / done / promote` 落到池子上（`facts` 与 `kept` 由调用方处理）。

        顺序：先 `again`（重置 + 计数 + 规则升迁）→ 再 `promote`（模型升迁）
        → 最后算**死亡集**（`done` ∪「已淡出且没被重新提起」）并删除。
        """
        again = _as_int_list(ar.get("again"))
        done = _as_int_list(ar.get("done"))
        promo = _as_int_list(ar.get("promote"))

        promoted_by_hits = 0
        for j0 in again:
            j = j0 - 1
            if 0 <= j < len(self.items):
                self.items[j].t0 = now
                self.items[j].hits += 1
                if self.items[j].hits >= self.cfg.promote_hits and not self.items[j].long:
                    self._to_mid(self.items[j], now, "hits>=%d" % self.cfg.promote_hits)
                    promoted_by_hits += 1

        promoted_by_model = 0
        for j0 in promo:
            j = j0 - 1
            if 0 <= j < len(self.items) and not self.items[j].long:
                self._to_mid(self.items[j], now, "模型promote")
                promoted_by_model += 1

        # ★★ 2026-10-05：`done` **不许直接删中期条目** —— 退回短期（重置计时），不是消失。
        #   理由：升迁的定义是「**这事反复出现**」（跨场证据），
        #   而 `done` 只是「**这一场模型判它过去了**」（单场判词）。
        #   **单场判词不该推翻跨场证据。**
        dead = set()
        demoted = 0
        # ★★ **按档位记账**（2026-10-06）：`dropped` / `demoted` 只给一个总数时，
        #   界面把"减了几条"印在**短期**那一行就会出现自相矛盾 ——
        #   典型：一条**悬着的事**办完了被删，界面却在"短期记忆"上显示 −1
        #   （用户实测报的正是这一类："短期 0、上次 +1"）。
        drop_by_tier: Dict[str, int] = {}
        demote_by_tier: Dict[str, int] = {}

        def _bump(box: Dict[str, int], label: str) -> None:
            box[label] = box.get(label, 0) + 1

        for j0 in done:
            j = j0 - 1
            if not (0 <= j < len(self.items)):
                continue
            if self.items[j].hanging:
                # ★ 「悬着的事」**只有这一条出路** —— `done` 就是"这事办完了"。
                #   它不衰减，所以不删它就**永远不会走**（用户："只由 `done` 结束"）。
                _bump(drop_by_tier, TIER_OPEN)
                dead.add(j)
            elif self.items[j].long:
                _bump(demote_by_tier, TIER_MID)      # 从"中期"退回短期 ⇒ 减的是中期那一档
                self._to_short(self.items[j], now)
                demoted += 1
            else:
                _bump(drop_by_tier, TIER_SHORT)
                dead.add(j)
        # 衰减到底的照删 —— **中期也会走到这一步**（`tau_mid` 只是大，不是无限）
        faded = {j for j, it in enumerate(self.items)
                 if self.w(it, now) < self.cfg.eps and (j + 1) not in again}
        for j in faded - dead:
            _bump(drop_by_tier, self.layer_label(j))
        dead |= faded
        dropped = 0
        for j in sorted(dead, reverse=True):
            if 0 <= j < len(self.items):
                self.items.pop(j)
                dropped += 1

        return {"again": again, "done": done, "promote": promo,
                "promoted_by_hits": promoted_by_hits, "promoted_by_model": promoted_by_model,
                "demoted": demoted, "dropped": dropped,
                # ★ 按档位拆开的两个数（界面各档自己显示自己的增减，别混用）
                "drop_by_tier": drop_by_tier, "demote_by_tier": demote_by_tier}

    # ------------------------------------------------------------------ 档位
    def _to_mid(self, it: TempItem, now: float, how: str) -> None:
        """**升迁进中期**：换慢衰减（`cfg.tau_mid`）、重新计时、打标记。"""
        it.long = True
        it.tier = TIER_MID
        it.tau = float(self.cfg.tau_mid)
        it.t0 = now
        it.how = how

    def _to_short(self, it: TempItem, now: float) -> None:
        """**退回短期**：恢复按 tag 分档的 `tau`、重新计时。"""
        it.long = False
        it.tier = None
        it.tau = self.tau_of(it.tag)
        it.t0 = now
        it.how = "%s→done降级" % (it.how or "")

    # ------------------------------------------------------------------ 呈现给归档
    def view_lines(self, now: float) -> List[str]:
        """归档时给模型看的清单（**序号从 1 数**，与它回填的 `again/done/promote` 对齐）。"""
        lines = []
        for j, it in enumerate(self.items):
            w = self.w(it, now)
            lines.append("%d. (%.1f天前 · w=%.2f%s%s · τ=%.1f天) %s"
                         % (j + 1, now - it.t0, w,
                            "[悬着]" if it.hanging else ("[中期]" if it.long else ""),
                            "[已淡出]" if w < self.cfg.eps else "",
                            it.tau, it.text))
        return lines

    # ------------------------------------------------------------------ 持久化
    def to_state(self) -> list:
        return [asdict(it) for it in self.items]

    @classmethod
    def from_state(cls, cfg: TimeConfig, state: Sequence[dict]) -> "TempPool":
        p = cls(cfg)
        for d in state or []:
            p.items.append(TempItem(**{k: d.get(k) for k in
                                       ("text", "t0", "tau", "hits", "src", "tag",
                                        "from_session", "long", "how", "tier", "hanging")}))
        return p


def _as_int_list(lst) -> List[int]:
    """序号一律归一成 int（模型偶尔给字符串；**不归一就会静默漏掉**）。"""
    out = []
    for x in lst or []:
        try:
            out.append(int(x))
        except Exception:  # noqa: BLE001
            pass
    return out


def sample_gap(cfg: TimeConfig, rng) -> float:
    """抽下一次见面的间隔（天）：对数正态，中位 `gap_med`。

    ⚠️ **只给模拟用**（`tools/simulate.py`）。真人在 `tools/talk.py` 里走**真挂钟时间**，
    这个【假设】自然消失。
    """
    g = math.exp(math.log(cfg.gap_med) + cfg.gap_sigma * rng.gauss(0, 1))
    return float(min(max(g, 0.5), cfg.gap_max))
