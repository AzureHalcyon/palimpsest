# -*- coding: utf-8 -*-
"""引擎：把池子 / 读取器 / 归档 拧在一起，并负责落盘与恢复。

一个 `cache_dir` 里放五样东西：

    pool.npz            长期池向量（**原始向量，不减均值**）+ 三层条数
    pool_texts.jsonl    长期池文本（`{"text":..., "layer":...}`，一行一条）
    state.json          时钟（天）/ 临时池 / 上一场怎么收的 / 场次号 / 编码器指纹 / 已落地场次号
    pending.json        **在途的那一场**（还没归档完）—— 见 `pending.py`
    <tag>_out.txt  <tag>_transcripts.jsonl     见 `transcript.py`

★ **时钟单位 = 天**：模拟时由 `sample_gap()` 推进；真人使用时由**真挂钟时间**推进
  （`advance_to_wall()`），于是"间隔中位 3 天"那个【假设】**自然消失**。
★ **换编码器 = 已有参数作废** ⇒ 缓存里记编码器指纹，不一致就**拒绝加载**并要求重建，
  不许悄悄混用两个坐标系的向量。
★ **归档可续**（`pending.json` + `applied_sids`）：归档要跑 3–4 回合模型，桌面上被杀是意外，
  手机上是常态。规矩压缩成一句 —— **`pending.json` 存在就表示"这一场还没到终态"**；
  终态只有两个：**落地成功**，或**试满 `dream.resume_attempts` 次后彻底放弃**；
  两者都要写转写、场次 +1、清标记 ⇒ **转写永远只写一行**，量具不被重复记录污染。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, NamedTuple, Optional, Sequence

import numpy as np

from .activity import ActivityLog
from .budgets import BudgetStore
from .channels import ChannelCatalog
from .deliver import Gate
from .web import Search
from .config import Config, Credentials, dream_topk
from .dreaming import dream, empty_archive
from .embedder import Embedder, embed_fingerprint
from .llm import BaseLLM, parse_json
from . import merging
from .pending import PendingStore
from .pool import LongPool, TempPool
from .prompts import DREAM_SYS, TOOLS_AUTONOMY, TOOLS_MEMORY, TOOLS_WEB
from .reading import ReadingBuilder
from .reader import Reader
from .wakeups import WakeupStore

# 2 = 加了 `applied_sids`（归档幂等账本）与 `pending.json` 续做。
# ⚠️ 版本不符 ⇒ `_read_cache` **拒绝加载**（宁可重建，不许凑合）。
STATE_VERSION = 2


class PoolSnapshot(NamedTuple):
    """只读快照：池子 + 时钟 + 元数据。**不含模型、不含凭证。**

    它是 `Engine.peek()` 的返回值。用 NamedTuple 而不是直接把 `Engine` 造出来，
    是为了让类型本身说清楚一件事：**这条路拿不到能干活的对象。**
    """

    long_pool: LongPool
    temp_pool: TempPool
    clock: float
    sessions: int
    meta: Dict[str, object]
    # ★ 临时池的向量也一起给：算分布图要用，而**重新编码要调接口**（只读路径不该调）。
    temp_V: Optional[np.ndarray] = None


class Engine:
    def __init__(self, cfg: Config, embedder, llm, long_pool: LongPool, temp_pool: TempPool,
                 cache_dir: str, clock: float = 0.0, temp_V: Optional[np.ndarray] = None):
        self.cfg = cfg
        self.embedder = embedder
        self.llm = llm
        self.long_pool = long_pool
        self.temp_pool = temp_pool
        self.reader = Reader(cfg.reader, embedder, cfg.salience)
        self.cache_dir = cache_dir
        self.clock = float(clock)
        self.last_ts: Optional[float] = None
        self.sessions_done = 0
        self.last_tail: Optional[str] = None
        self.scene: Optional[str] = None
        self.temp_V: Optional[np.ndarray] = None
        if temp_V is not None and len(temp_V) == len(temp_pool.items):
            self.temp_V = np.asarray(temp_V, dtype=float)
        self.pending = PendingStore(cache_dir)
        # ★ **主动唤醒队列**（模型自己给自己排的"过一会儿再看"）。
        #   放在 Engine 上而不是 Session 上：唤醒必须**跨场次**活着 ——
        #   这一场聊完、App 被杀、下次打开，那条唤醒还在。
        self.wakeups = WakeupStore(cache_dir)
        # ★ **渠道目录 + 读数构造器**（2026-10-05）。
        #   桌面端**没有插件宿主** ⇒ 目录是空的、`reading` 是 None ⇒
        #   工具表里就**一个渠道工具都不出现**（静默降级，✓ 不是报错）。
        #   手机上由 Chaquopy 那侧 `attach_channels()` 把"调 provider"的取数函数接进来。
        # ★ **三本账里的两本**（模型唤起 ≤50/天 · 投递 ≤5/天，见 `budgets.py`）。
        #   L1 唤起模型之前**必须先 `take("llm")` 成功**才准花钱。
        self.budgets = BudgetStore(cache_dir)
        # ★ **活动记录**（它做过什么；可查、不推送）+ L1 要用到的账本在上一条。
        self.activity = ActivityLog(cache_dir)
        # ★ **投递闸门**（活跃 ≠ 开口）：开口这件事单独一道门，默认不开。
        self.gate = Gate(cache_dir, self.budgets)
        # ★ **联网**（工具，不是池子内容）：两层 + 围栏 + 信源等级 D。
        #   搜索源还没定 ⇒ `search_fn/fetch_fn` 都是 None，它只会如实回"还没接"。
        self.web = Search(self.budgets)
        self.channels = ChannelCatalog(cache_dir)
        self.reading: Optional[ReadingBuilder] = None
        # 已落地场次的账本（幂等键）。**只增不删**——补做时靠它认出"其实已经存过了"。
        self.applied_sids: List[int] = []
        # ★ 上一次归档**动了什么**（每层增/减、临时池增/减/升迁）。
        #   界面拿它显示「上次 +2 −1」。存在这里而不是每次现算：现算只能算"现在有多少"，
        #   而"这一场带来了什么变化"是**事件**，事后无法重建。
        self.last_delta: Dict[str, object] = {}
        os.makedirs(cache_dir, exist_ok=True)
        # ★ 重算临时池向量要**调编码接口**，而 `Engine.load` 每次开场都会走到这里
        #   （等于每次开场白打一次接口）。能从缓存带进来就别重算；
        #   条数对不上才重算 —— 宁可多花一次调用，也不许向量与条目错位。
        if self.temp_V is None:
            self.refresh_temp_vectors()

    # ------------------------------------------------------------------ 渠道
    def attach_channels(self, fetch_fn) -> None:
        """把**取数函数**接进来（宿主侧实现：手机上就是调插件的 provider）。

        `fetch_fn(plugin, metric, from_ms, to_ms) -> dict` —— 形状见设计稿 §2.1 的 `Reading`。
        """
        self.reading = ReadingBuilder(fetch_fn)

    def attach_web(self, search_fn=None, fetch_fn=None) -> None:
        """把**搜索源**接进来（定下来之后只换这两个函数，污染防护一行不用改）。"""
        self.web.search_fn = search_fn
        self.web.fetch_fn = fetch_fn

    def channel_tool(self, name: str) -> Optional[dict]:
        """按**工具名**找渠道。★ 名字是插件声明的，宿主只查表 —— 不硬编码任何一个渠道。"""
        for t in self.channels.tools():
            if t.get("name") == name:
                return t
        return None

    def read_channel(self, tool: dict, args: dict, now_ms: Optional[int] = None) -> dict:
        """执行一次渠道读数，返回 `ReadingBuilder.build()` 的结果（含 `text`）。

        ★ 窗口由**模型给**（`window_hours`），但**上限由插件定**（`maxWindowMs`）——
          模型想要 30 天而渠道只有 2 小时，就以插件为准（设计稿 §2.9）。
        """
        name = str(tool.get("desc") or tool.get("metric") or "渠道")
        if self.reading is None:
            return {"ok": False, "text": "[%s] 这个渠道现在拿不到：手机侧还没接上插件宿主"
                                          "（桌面端本来就没有插件）" % name}
        try:
            hours = float((args or {}).get("window_hours") or 24.0)
        except (TypeError, ValueError):
            hours = 24.0
        hours = max(1.0, min(hours, 24.0 * 30))          # 1 小时 ~ 30 天
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        return self.reading.build(
            str(tool.get("plugin") or ""), str(tool.get("metric") or ""), now,
            name=name, unit=str(tool.get("unit") or ""),
            window_ms=int(hours * 3_600_000),
            max_window_ms=int(tool.get("maxWindowMs") or 0) or None)

    def session_tools(self) -> List[dict]:
        """这一场**给模型的工具表**（= 冻结的静态工具 + 由目录导出的渠道工具）。

        ★ 分界很清楚：
          · 前面几个（`read_z` / 唤醒三件）是**我们写的**、冻结的；
          · 后面的渠道工具是**插件声明的**（`toolName`/`useWhen`）⇒ 装了什么插件就有什么工具。
        ⛔ 所以**不许**在这里硬编码任何渠道名。
        """
        tools = list(TOOLS_MEMORY) + list(TOOLS_AUTONOMY) + list(TOOLS_WEB)
        tools.append({"type": "function", "function": {
            "name": "list_channels",
            "description": ("看看你现在能查哪些渠道、各自最近有没有变化。"
                            "★ 想查什么之前先看这个 —— 它很便宜；具体的读数要用下面那些工具。"),
            "parameters": {"type": "object", "properties": {}}}})
        for t in self.channels.tools():
            desc = str(t.get("useWhen") or t.get("desc") or "")
            tools.append({"type": "function", "function": {
                "name": str(t["name"]),
                "description": "%s。%s" % (t.get("desc") or "", desc),
                "parameters": {"type": "object", "properties": {
                    "window_hours": {"type": "number",
                                     "description": "看最近多少小时（默认 24）"}}}}})
        return tools

    # ------------------------------------------------------------------ 构造
    @classmethod
    def build_pool(cls, cfg: Config, embedder, baseline: Sequence[str],
                   situational: Sequence[str], memories: Sequence[str]) -> LongPool:
        """把三层文本编码成长期池（**原始向量，不减均值**）。"""
        texts = list(baseline) + list(situational) + list(memories)
        V = embedder.raw_embed(texts) if texts else np.zeros((0, cfg.embed.dim))
        # ★ 种子条目的元数据：`how="seed"`（建池时就在，不是哪一场聊出来的）。
        #   时间**留空**——它们是我们手工写进去的，给个假时间不如不给。
        seed_meta = [{"how": "seed"} for _ in texts]
        pool = LongPool(texts=list(texts), V=V, n_baseline=len(baseline),
                        n_situational=len(situational), n_memories=len(memories),
                        meta=seed_meta)
        pool.prep()
        return pool

    @classmethod
    def load(cls, cfg: Config, cache_dir: str, creds: Optional[Credentials] = None,
             embedder=None, llm=None) -> "Engine":
        """从缓存加载。**不传凭证也能离线自检**（把 `embedder`/`llm` 换成替身即可）。"""
        cfg.check()
        embedder = embedder or Embedder(cfg.embed, creds or Credentials.load())
        llm = llm or BaseLLM(cfg.chat, creds or Credentials.load())
        pool, temp, clock, meta, tV = _read_cache(cache_dir, cfg, embedder.fingerprint)
        eng = cls(cfg, embedder, llm, pool, temp, cache_dir, clock, temp_V=tV)
        eng.last_ts = meta.get("last_ts")
        eng.sessions_done = int(meta.get("sessions", 0))
        eng.last_tail = meta.get("last_tail")
        eng.scene = meta.get("scene")
        eng.applied_sids = [int(x) for x in (meta.get("applied_sids") or [])]
        eng.last_delta = meta.get("last_delta") or {}
        return eng

    @classmethod
    def peek(cls, cfg: Config, cache_dir: str) -> "PoolSnapshot":
        """**只读**缓存：不造模型、不要凭证 —— 给界面上"看池子"这类只读页面用。

        为什么单独开这个口子，而不是让 `load` 容忍"没有凭证"：
          `load` 要造 `Embedder` / `BaseLLM`，而**造它们必须先有凭证**；
          但"读池子"在物理上跟凭证毫无关系（池子就是磁盘上的几个文件）。
          与其让 `load` 学会"半死不活地构造"，不如明说：**这条路不造模型。**
          ⇒ 没配 API Key 的人打开"池子"页，看到的是**"池子还没建"**这个真话，
            而不是一句莫名其妙的"找不到凭证文件"。

        ★ 它**共用** `_read_cache` 与同一个指纹出处（`embed_fingerprint`），
          不是另写一份读盘逻辑 —— 两份读盘逻辑迟早漂移，
          而漂移的表现是"界面显示的池子与实际用的不是同一个"。
        """
        cfg.check()
        pool, temp, clock, meta, tV = _read_cache(cache_dir, cfg, embed_fingerprint(cfg.embed))
        return PoolSnapshot(long_pool=pool, temp_pool=temp, clock=clock,
                            sessions=int(meta.get("sessions", 0)), meta=meta, temp_V=tV)

    # ------------------------------------------------------------------ 向量缓存
    def refresh_temp_vectors(self) -> None:
        texts = [it.text for it in self.temp_pool.items]
        self.temp_V = self.embedder.raw_embed(texts) if texts else None

    # ------------------------------------------------------------------ 时间
    def advance_to_wall(self, ts: Optional[float] = None) -> float:
        """按**真挂钟时间**推进时钟（单位：天）。第一次调用只记起点。"""
        ts = time.time() if ts is None else float(ts)
        if self.last_ts is not None:
            self.clock += max(0.0, (ts - self.last_ts) / 86400.0)
        self.last_ts = ts
        return self.clock

    # ------------------------------------------------------------------ 归档
    def checkpoint(self, rec: dict, msgs: Optional[Sequence[dict]] = None) -> None:
        """每轮把"这一场到目前为止"**原子落盘**。

        ★ 手机上一场对话随时可能被划掉，而归档是**一场结束时**才做的 ⇒ 中间那几轮
          此前只活在内存里。落盘的是同一个 `pending.json`（一场只有一份在途）。
        ★ `msgs` 也要一起存：**只存 `turns` 会让它记得发生过什么、却接不上刚才那句**
          （`turns` 是给人看/归档用的记录，`msgs` 是真正喂给模型的上下文）。
        """
        if msgs is not None:
            rec = dict(rec)
            rec["msgs"] = list(msgs)
        self.pending.save(rec)

    def has_pending(self) -> bool:
        return self.pending.exists()

    def resume_pending(self, transcript=None) -> Optional[dict]:
        """把上一次**没到终态**的那一场补做完。**入口（talk.py / 手机壳）启动时调一次。**

        ★ 用**那一场结束时的时钟**（`rec["clock_at_end"]`）算归档，不是现在的时钟：
          归档是"那场结束后它自己待着做的回顾"；时钟往前跳会让它当时看得见的临时条目
          先淡掉、`again/done/promote` 的序号跟着错位。
          补完**当前时钟不动**（`archive(now=...)` 只影响这一次归档的计算，
          `self.save()` 写的仍是当前 `self.clock`）⇒ 时钟不会倒退。
        """
        rec = self.pending.load()
        if not rec:
            return None
        return self.archive(rec, transcript=transcript,
                            now=float(rec.get("clock_at_end", self.clock)))

    # ------------------------------------------------------------------ 归档用的工具
    def _dream_tool(self, now: float):
        """★ 归档那一步能用的一组工具（**机械端；作用域只有池子条目**）。

        为什么做成**一个分发器**而不是给每个工具加入参：以后再添原语，`dream()` 的签名
        不用动（用户点出来的方法论：**给原语，别补丁式地加**）。
        ⛔ **作用域严格限定在池子**：不读文件、不碰仓库、不跑外部进程。
        ⛔ 一律**不抛异常** —— 工具抛错会让它以为"这条路不通"，而它需要的是一句能读懂的话。
        """
        def run(name: str, args: dict) -> List[str]:
            a = args if isinstance(args, dict) else {}
            if name == "find_text":
                return self.reader.find_text(str(a.get("pattern") or ""), now,
                                             self.long_pool, self.temp_pool, self.temp_V,
                                             dream_topk(self.cfg))
            if name == "read_entry":
                return self._read_entry(_as_int(a.get("idx"), -1))
            if name == "list_entries":
                return self._list_entries(str(a.get("layer") or ""),
                                          _as_int(a.get("n"), 20), now)
            if name == "pool_stats":
                return self._pool_stats()
            return ["（没有这个工具：%s）" % name]
        return run

    def _read_entry(self, i: int) -> List[str]:
        """**按编号读一条**：全文 + 元信息（改过没有 · 上一版原文 · 被想起过几次）。

        为什么要它：模型手上只有"一行文本"，要动一条之前**看不到它的来历**
        ⇒ 只能猜着改写。元信息（尤其 `updated.was`）就是**上一版原文**。
        """
        n = len(self.long_pool.texts)
        if not (0 <= i < n):
            return ["没有第 %d 条（池子里现在 %d 条，编号 0–%d）" % (i, n, max(n - 1, 0))]
        lay = self.long_pool.layer_of(i)
        out = ["[%s #%d] %s" % (lay, i, self.long_pool.texts[i]),
               "层：%s · 被想起过 %d 次" % (lay, self.long_pool.hits_at(i))]
        u = self.long_pool.meta_at(i).get("updated")
        if isinstance(u, dict):
            out.append("改过：%s（第 %s 场）" % (u.get("how") or "?", u.get("sid")))
            if u.get("was"):
                out.append("上一版原文：%s" % str(u["was"])[:200])
        if i < self.long_pool.n_baseline + self.long_pool.n_situational:
            out.append("★ 这是**手写层**（底色/情境）⇒ 归档**不许改**它。")
        return out

    def _list_entries(self, layer: str, n: int, now: float) -> List[str]:
        """**按层列条目**。记忆层默认**最近写入/改动的在前**；临时条目带档位、**没有编号**。"""
        n = max(1, min(int(n or 20), 60))
        want = layer.strip()
        lp = self.long_pool
        if want in ("临时", "短期", "中期", "悬着"):
            alive = self.temp_pool.alive_idx(now)
            if not alive:
                return ["临时池现在是空的"]
            out = ["临时池 %d 条（存活 %d）· **没有编号，改不了**"
                   % (len(self.temp_pool.items), len(alive))]
            labels = [self.temp_pool.layer_label(j) for j in alive]
            for lbl, t in zip(labels[:n], self.temp_pool.texts(alive)[:n]):
                out.append("[%s] %s" % (lbl, str(t)[:100]))
            return out
        rows = [(i, lp.layer_of(i), t) for i, t in enumerate(lp.texts)
                if (not want) or want == lp.layer_of(i)]
        if not rows:
            return ["这一层没有条目（可选：底色 / 情境 / 记忆 / 临时）"]
        if not want or want == "记忆":
            rows.sort(key=lambda r: float(lp.meta_at(r[0]).get("t") or 0.0), reverse=True)
        return ["%s 共 %d 条，列前 %d（编号可直接当 `old_idx`，也能拿去 `read_entry`）"
                % (want or "记忆", len(rows), min(len(rows), n))] + \
               ["[%s #%d] %s" % (lay, i, t[:100]) for i, lay, t in rows[:n]]

    def _pool_stats(self) -> List[str]:
        """池子总账：各层条数 · 临时存活 · **这一场最多能改几条**。"""
        lp = self.long_pool
        c = lp.composition(range(len(lp.texts)))
        alive = len(self.temp_pool.alive_idx(self.clock))
        return ["底色 %d · 情境 %d · 记忆 %d（长期池共 %d 条）"
                % (c.get("底色", 0), c.get("情境", 0), c.get("记忆", 0), len(lp.texts)),
                "临时池 %d 条（存活 %d）" % (len(self.temp_pool.items), alive),
                "★ 这一场最多能改 %d 条（超出的会被降级成 add）；核对时每层各取 %d 条邻居"
                % (self.cfg.merge.max_updates_per_session, self.cfg.merge.top_k_per_layer)]

    # ------------------------------------------------------------------ 落档前核对
    def _precheck(self, rec: dict, facts: list, kept: list, now: float, progress=None) -> tuple:
        """★★ **落档前核对**（2026-10-05）：写新条目之前，先跟已有条目对一遍。

        用户口径："加一个环节，在写新条目之前，先算一下和已有条目的关联，
        要能把互相打架的、重复的等找出来，然后要求 AI 重新想，或者更新旧条目。"
        "所有新条目都要这样算一下，而且**不同层之间的也是**。"

        流程：**机械取邻居**（按层各 K 条，不用阈值）→ **模型判**（add/replace/merge/skip）
        → **过闸门**（`merging.sanitize`）→ 就地改写或交给调用方新增。

        返回 `(facts, kept, 记账)`；记账写进转写的 `merge` 字段。
        """
        cfg = self.cfg.merge
        book = {"ran": False, "rounds": 0, "updates": [], "flags": [], "notes": []}
        if not cfg.enabled:
            book["notes"].append("核对被关掉（config.merge.enabled=False）")
            return facts, kept, book
        if not (facts or kept):
            return facts, kept, book

        n_total = len(self.long_pool.texts)
        n_prot = self.long_pool.n_baseline + self.long_pool.n_situational
        V = self.long_pool.prep()
        layers = [self.long_pool.layer_of(i) for i in range(n_total)]
        texts = list(self.long_pool.texts)

        ftexts = [str(f.get("text", "")).strip() for f in facts]
        ktexts = [str(k.get("text", "")).strip() for k in kept]
        allnew = ftexts + ktexts
        vecs = self.embedder.raw_embed(allnew) if allnew else []
        vecs = [v / max(float(np.linalg.norm(v)), 1e-12) for v in vecs]
        fblk = [merging.neighbors(V, layers, texts, vecs[i], cfg.top_k_per_layer)
                for i in range(len(ftexts))]
        kblk = [merging.neighbors(V, layers, texts, vecs[len(ftexts) + i],
                                  cfg.top_k_per_layer) for i in range(len(ktexts))]

        # ★★ 2026-10-06：**认下它自己指的名**（`facts[i]["old_idx"]`）。
        #   为什么：归档侧现在能从 `search` 拿到池内编号（`[记忆 #252] …`）⇒ 它比向量
        #   更知道"这条跟哪一条是同一件事"。认下它 ⇒ 核对的候选从"9 条里挑"缩到"就这一条"，
        #   ⇒ 既少想（**实测核对占了 78% 的时间**），也少漏（它指的那条一定在眼前）。
        #   ⛔ 闸门照旧：只认**记忆层**的下标（底色/情境不许改），非法就当没提。
        #   ⚠️ 笔记先攒在 `my_notes` 里 —— `book["notes"]` 稍后会被 `sanitize` 的
        #      `notes` **整段覆盖**（2026-10-06 实测踩到：写在前面会被吃掉）。
        my_notes: list = []
        named = 0
        for i, f in enumerate(facts):
            if i >= len(fblk):
                break
            try:
                oi = int(f.get("old_idx"))
            except (TypeError, ValueError):
                continue
            if not (n_prot <= oi < n_total):
                my_notes.append("facts#%d 的 old_idx=%s 不在记忆层 ⇒ 当没提"
                                % (i + 1, f.get("old_idx")))
                continue
            named += 1                      # ★ 它指了名就算认下（哪怕本来就在候选里）
            if any(x[0] == oi for x in fblk[i]):
                continue                    # 本来就在邻居里 ⇒ 不用插到前面
            fblk[i] = ([(oi, layers[oi], texts[oi],
                         float(np.asarray(V[oi], dtype=float) @ vecs[i]))] + list(fblk[i]))
        if named:
            book["named"] = named

        payload = merging.build_input([("facts", ftexts, fblk), ("kept", ktexts, kblk)])
        _drain(self.llm)                       # ★ 先清空 ⇒ 之后的调用都算"核对那一段"
        if progress is not None:
            progress("phase", "核对：跟已有条目对一遍（%d 条新条目）" % (len(facts) + len(kept)))
        raw, rounds = merging.run(self.llm, cfg, payload, progress=progress)
        book["timings"] = _drain(self.llm)     # ★ 给"核对"这一段单独计时（用户要求可观测）
        book["ran"] = True
        book["rounds"] = rounds
        if rounds < 0:
            book["notes"].append("核对没跑成（给不出合法 JSON）⇒ 全部当 add")
            return facts, kept, book

        decisions, flags, notes = merging.sanitize(
            raw, len(facts), len(kept), n_prot, n_total, cfg)
        book["flags"] = flags
        # ★ 把核对自己的 notes 与我前面攒的（`old_idx` 那几条）**接起来** ——
        #   不能直接赋值，否则会把前面的吃掉（2026-10-06 实测踩到）。
        #   ⚠️⚠️ 而且要把 `my_notes` **重新指向这个活列表**：下面的应用阶段（乐观锁、
        #   跳过不删）还会往里追加 —— 不指向活列表，那些追加就永远进不了记账
        #   （2026-10-06 被自检第二次抓到：锁拦住了，但记账是空的 ⇒ **静默**）。
        my_notes = book["notes"] = list(notes) + my_notes
        by = {(d["kind"], d["i"]): d for d in decisions}

        out_f, out_k = [], []
        # ★★ 2026-10-06 修（**实测跑出来的真 bug**）：原来这里是**边判边改** ——
        #   `rewrite` 之后紧接着 `drop(others)`，而 `drop` 会让池子**缩短**；
        #   同一场里**后面那几组**的 `idxs` 是按**删之前**的条数校验、也是按删之前的下标给的
        #   ⇒ 轻则 `IndexError`（而"归档失败 = 这一场的记忆整段丢掉"），
        #   **重则指到错条目、把不相干的一条改写掉（静默写坏记忆）**。
        #   原注释只想到"一组之内先改后删"，**没想到一场里有多组**。
        #   ⇒ 改成**两段式**：① 先做完全部 `rewrite`（不改条数 ⇒ 下标全程有效）；
        #     ② 再统一 `drop`，**按下标从大到小**删（前面的下标不受影响）。
        rewrites = []
        for i, f in enumerate(facts, 1):
            d = by.get(("facts", i)) or {"action": "add"}
            act = d.get("action")
            if act == "skip":
                book["updates"].append({"kind": "facts", "i": i, "action": "skip",
                                        "why": d.get("why", ""), "text": f.get("text")})
                continue
            if act in ("replace", "merge"):
                idxs = [int(x) for x in (d.get("idxs") or [d.get("idx")])]
                # ⛔⛔ **硬断言**：手写层绝不许被归档改（闸门已挡一次，这里是第二道）
                for x in idxs:
                    if x < n_prot:
                        raise AssertionError(
                            "落档前核对试图改手写层 idx=%d（底色/情境共 %d 条）" % (x, n_prot))
                # ⛔ **越界不许崩**：模型给的下标可能超出池子（校验是按**当时**的条数做的）
                #   ⇒ 越界就**降级成 add** 并记账 —— 宁可多一条，也**不许整场丢**。
                if any(x >= len(self.long_pool.texts) for x in idxs):
                    book["notes"].append("facts#%d 的 idx %s 越界（池子 %d 条）⇒ 当 add"
                                         % (i, idxs, len(self.long_pool.texts)))
                    out_f.append(f)
                    continue
                rewrites.append((i, idxs[0], list(idxs[1:]), str(d["text"]), d.get("why", ""),
                                 texts[idxs[0]], {x: texts[x] for x in idxs[1:]}))
                continue
            out_f.append(f)

        # ② 统一 drop：**排除掉任何一组的 primary**（它可能同时出现在别处的 others 里），
        #    再**从大到小**删 —— 否则删掉一条会让它后面的下标前移。
        prim = set(p for _i, p, _o, _t, _w, _sp, _so in rewrites)
        drop_idx = sorted({x for _i, _p, o, _t, _w, _sp, _so in rewrites for x in o
                           if x not in prim}, reverse=True)
        snap_of = {}
        for _i, _p, _o, _t, _w, _sp, so in rewrites:
            snap_of.update(so)
        # ① 先改写（不改条数 ⇒ 下标全程有效）
        #   ★★ **乐观锁**（2026-10-06 用户要求）：编号会随删除前移，而"改错了哪一条"是
        #   **静默**的（最坏的一类）。所以收集那一刻先把这几条的**文本快照**下来，
        #   应用前**校验 `texts[idx]` 还是不是那条** —— 不是就**跳过这一条并记账**，
        #   ⛔ 绝不默默改下去。将来若又出现新的错位路径，它会当场喊出来。
        skipped_groups = set()
        for i, primary, others, new_text, why, snap_p, snap_o in rewrites:
            if self.long_pool.texts[primary] != snap_p:
                skipped_groups.add(i)
                my_notes.append("facts#%d 的下标 %d 对不上（现在不是当时那条）⇒ 跳过不改"
                                % (i, primary))
                continue
            act0 = (by.get(("facts", i)) or {}).get("action")
            was = self.long_pool.texts[primary]
            self.long_pool.rewrite(
                primary, new_text, self.embedder.raw_embed([new_text]),
                patch={"t": float(now), "ts": time.time(),
                       "sid": int(rec.get("k") or 0), "how": "核对·" + str(act0)})
            book["updates"].append({"kind": "facts", "i": i, "action": act0,
                                    "idx": primary, "idxs": [primary] + others,
                                    "dropped": [{"idx": x, "text": snap_o.get(x, "")} for x in others],
                                    "why": why, "text": new_text, "was": was})
        # 被跳过的组 ⇒ **它的 `others` 也不删**（别把一个没改成的组拆散）
        safe_drop = [x for x in drop_idx
                     if snap_of.get(x) == self.long_pool.texts[x]
                     and not any(x in r[2] and r[0] in skipped_groups for r in rewrites)]
        for x in drop_idx:
            if x not in safe_drop:
                my_notes.append("下标 %d 对不上（现在不是当时那条）⇒ 不删" % x)
        if safe_drop:
            self.long_pool.drop(safe_drop)
        for i, k in enumerate(kept, 1):
            d = by.get(("kept", i)) or {"action": "add"}
            if d.get("action") == "skip":
                book["updates"].append({"kind": "kept", "i": i, "action": "skip",
                                        "why": d.get("why", ""), "text": k.get("text")})
                continue
            out_k.append(k)
        return out_f, out_k, book

    def archive_draft(self, rec: dict, now: Optional[float] = None, progress=None) -> dict:
        """★★ **只出草案**（2026-10-07）：跑归档对话，**池子一字不动**。

        用户设计：「把归档看作**接着刚才对话的特殊对话** —— 模型发完消息之后用户可以接着发消息，
        最后归档完成之后**完成按钮用户点**」⇒ 落地必须能被推迟到用户按「完成」那一刻。
        ⇒ 原来那种"跑完就写池子"的路径仍然要能用，见 [archive]（它就是 draft + land）。

        ★ **核对（`_precheck`）留在落地那一半**：它会**就地改写旧条目**（replace/merge 真动池子），
          所以审阅期间绝不能跑 —— 否则"池子一字不动"就是空话。
          （代价：审阅时看不到"这条会并进第 N 条"这类核对判决；它在按「完成」之后发生。）

        返回 `{"land": bool, "rec": …}`；`land=False` = 不必落地（幂等命中 / 归档失败留中间态）。
        """
        now = self.clock if now is None else float(now)
        sid = int(rec.get("sid") or (self.sessions_done + 1))
        rec["sid"] = sid
        rec.setdefault("clock_at_end", now)
        attempts = int(rec.get("attempts") or 0) + 1
        rec["attempts"] = attempts
        self.pending.save(rec)                     # ★ 动模型之前先落盘

        temp_lines = self.temp_pool.view_lines(now)
        search = lambda q: self.reader.search(q, now, self.long_pool, self.temp_pool,
                                              self.temp_V, dream_topk(self.cfg))
        # ★ 2026-10-06：**其余工具走一个分发器**（`find_text` 按形态找 · `read_entry` 按号读 ·
        #   `list_entries` 按层列 · `pool_stats` 看预算）—— 以后再加原语，这里和 `dream()`
        #   的签名都不用动。⛔ 作用域只有池子条目：不读文件、不碰仓库、不跑外部进程。
        try:
            _drain(self.llm)                   # ★ 先清空 ⇒ 之后的调用都算"归档那一段"
            # ★ 2026-10-07：归档这一段**也流式**（用户要求「COT 也可见」）——
            #   每轮报一条 `phase`，推理/正文按 `reasoning`/`content` 边收边给。
            #   ⛔ 只影响"看得见什么"：`ar` / `rounds` / 落地全都不变。
            if progress is not None:
                progress("phase", "归档：开始（最多 %d 轮）" % int(self.cfg.dream.turns))
            ar, rounds, repairs = dream(self.llm, rec.get("turns") or [], temp_lines, search,
                                        self.cfg.dream, self.cfg.dream.turns,
                                        tool=self._dream_tool(now), progress=progress)
            if isinstance(ar, dict):
                ar["timings"] = _drain(self.llm)   # ★ 归档那一段的逐次耗时与 token
        except Exception as e:  # noqa: BLE001
            # ★ 归档失败必须**可见**（这一场的记忆会丢），不许静默吞掉
            ar, rounds, repairs = empty_archive("ERR %r" % e), -1, 0

        failed = rounds < 0
        if failed and attempts < max(1, int(self.cfg.dream.resume_attempts)):
            # 留在中间态：**不写转写、场次不加、标记不清**，下次启动再试
            rec.update({"archive": ar, "dream_rounds": rounds, "dream_repair": repairs,
                        "pending": "第 %d 次归档失败，下次启动再试（共 %d 次机会）"
                                   % (attempts, self.cfg.dream.resume_attempts)})
            self.pending.save(rec)
            # ★★ **三态之"失败"**（2026-10-07 实测逼出来的）：这条路**不许跑收尾** ——
            #   要保住 `pending`、场次不加，等下次启动补做（`t10_archive_recoverable` 钉着它）。
            #   ⛔ 别把它并进下面那个幂等分支（那条**要**收尾）。
            rec["archive_draft"] = {"land": "failed"}
            return {"land": "failed", "rec": rec}


        if sid in self.applied_sids:
            # ★ 幂等：这一场其实已经落地过 ⇒ **只收尾，不再动池子**
            rec.update({"archive": ar, "dream_rounds": rounds, "dream_repair": repairs,
                        "applied": {"skipped": "这一场已经落地过（幂等键命中）"},
                        "facts_added": 0, "kept_added": 0,
                        "temp_after": len(self.temp_pool.items),
                        "temp_alive_after": len(self.temp_pool.alive_idx(now))})
            rec["archive_draft"] = {"land": "no"}      # 幂等：不必落地（但要收尾）
            return {"land": "no", "rec": rec}

        # ★ 草案**落盘**（`land:"yes"` 记在草案里 ⇒ `archive_land` 只认 rec 就能判三态）
        rec["archive_draft"] = {"land": "yes", "ar": ar, "rounds": rounds, "repairs": repairs,
                                "now": float(now), "sid": int(sid), "talk": []}
        self.pending.save(rec)                 # ★ 审到一半杀掉 App 也不丢
        # ⛔ `land` **三态一律用字符串**（"yes"/"no"/"failed"）—— 别在这写成 `True`：
        #   胶水那边是拿它跟 `"yes"` 比的，布尔 `True` 会当场变成"失败"
        #   （2026-10-07 真机踩到：明明出了草案，界面报「这次归档没成」）。
        return {"land": "yes", "rec": rec, "ar": ar, "rounds": rounds,
                "repairs": repairs, "now": now, "sid": sid}

    def archive_revise(self, rec: dict, user_text: str, progress=None) -> dict:
        """★★ **归档期里用户又说了话 ⇒ 让它在同一场归档的上下文里改草案**（2026-10-07）。

        用户设计：「把归档看作**接着刚才对话的特殊对话** —— 模型发完消息之后用户可以接着发消息，
        最后归档完成之后完成按钮用户点」。

        ★ 这一条**刻意做成无状态**：不靠"还留着一个 `dream()` 的 msgs 内存对象"，
          而是把**草案 + 你们这几句对话**都记在 `rec["archive_draft"]["talk"]` 里、
          随 `pending.json` 落盘。理由：审阅可能跨重启（用户会随手划掉 App），
          内存态一丢，草案就白跑了（而那是一次真金白银的模型调用）。

        返回**新的草案**（`{"ok":…}` 里带 `draft`）；模型给不出合法 JSON 时**保留旧草案**并如实说。
        """
        draft = dict(rec.get("archive_draft") or {})
        ar = draft.get("ar") or {}
        talk = list(draft.get("talk") or [])
        if not ar:
            return {"ok": False, "err": "还没有草案可改（先跑一次归档）"}

        msgs = [{"role": "system", "content": DREAM_SYS}]
        msgs += talk
        msgs.append({"role": "user", "content":
                     "这是你刚给出的归档草案（JSON）：\n%s\n\n"
                     "用户看了草案之后说：%s\n\n"
                     "★ 按他这句改。没提到的地方**原样保留**（别顺手重写别的条目）。\n"
                     "**给出修改后的最终 JSON**，格式与刚才完全一样。"
                     % (json.dumps(ar, ensure_ascii=False), str(user_text))})
        if progress is not None:
            progress("phase", "归档：按你说的改草案")
        try:
            msg = self.llm.chat(msgs, temp=0.2, json_mode=True, progress=progress)
            new_ar = parse_json(msg.get("content") or "")
            if not isinstance(new_ar, dict) or not new_ar:
                raise ValueError("给不出合法 JSON")
        except Exception as e:  # noqa: BLE001
            # ⛔ 改不动就**留着旧草案**（不许把已经审过的东西弄丢），并如实报
            return {"ok": False, "err": "这次没改成（%s: %s）⇒ 草案保持原样"
                                        % (type(e).__name__, e), "draft": draft}

        talk += [{"role": "user", "content": str(user_text)},
                 {"role": "assistant", "content": json.dumps(new_ar, ensure_ascii=False)}]
        draft["ar"] = new_ar
        draft["talk"] = talk[-20:]                 # ★ 有界：只留最近 20 句（与"旧文本只留最近一次"同规矩）
        rec["archive_draft"] = draft
        self.pending.save(rec)
        return {"ok": True, "draft": draft}

    def archive_land(self, rec: dict, transcript=None, progress=None) -> dict:
        """★ **把草案落地**（2026-10-07 从 `archive()` 里切出来的那一段，**逻辑一字未改**，只降了一级缩进）。

        顺序与原来完全一致：先拍变化快照 → `again`/`promote`/死亡集 → **核对** → facts 进长期池
        → `kept`/会变的情形进临时池 → 记账 → 写转写 → 落盘 → 清 pending。

        ★ 只认 `rec`（草案在 `rec["archive_draft"]` 里）—— 这样"出草案"和"落地"可以隔着
          一次重启/几十秒的对话；`land` 三态见 `archive_draft`。
        """
        d = rec.get("archive_draft") or {}
        land = d.get("land")
        if land == "failed":
            # ★ **归档失败留在中间态** ⇒ **只返回，不跑收尾**：`pending` 保着、场次不加，
            #   下次启动补做。⛔ 与下面那个幂等分支**不是一回事**（那条要收尾）。
            return rec
        if land != "yes":
            # 幂等命中：池子不动，只把这一场收尾（与老路径同）
            if transcript is not None:
                transcript.session(rec)
            self.sessions_done += 1
            if rec.get("turns"):
                last = rec["turns"][-1]
                self.last_tail = ("%s\n%s" % (last.get("user", ""), last.get("ai", "")))[:400]
            self.save()
            self.pending.clear()                       # ★ 落地并落盘之后才清
            return rec

        ar = d["ar"]
        rounds = int(d.get("rounds") or 0)
        repairs = int(d.get("repairs") or 0)
        now = d.get("now")
        sid = int(d.get("sid"))

        # ★ 变化是**事件**，必须在动手之前拍快照，事后重建不出来。
        before_layers = self.long_pool.counts()
        before_temp = len(self.temp_pool.items)

        applied = self.temp_pool.apply_archive(ar, now)

        facts = [f for f in (ar.get("facts") or []) if str(f.get("text", "")).strip()]
        kept = [k for k in (ar.get("kept") or []) if str(k.get("text", "")).strip()]

        # ★★ **facts 分流**（2026-10-05 · 用户："先把中期货源接上"）
        #   「会变的情形」（在戒烟 / 在找工作 / 猫在生病）⇒ **出生即中期**（慢衰减）；
        #   「不变的事」⇒ 才进**永久**的长期池。
        #   ⚠️ **缺标记 ⇒ 当"会变的"**（进中期）：用户："**永久的这个要慎重**" ——
        #     拿不准的时候，宁可让它日后能退出去，也不要一进去就出不来。
        stable = [f for f in facts if not _is_changing(f)]
        changing = [f for f in facts if _is_changing(f)]

        # ★★ **落档前核对**：写进去之前先跟已有条目对一遍。
        #   它会就地改写被 `replace` / `merge` 的旧条目、真删被取代的、把 `skip` 的剔掉。
        #   ⚠️ 只核对**要进永久池的那批** —— 会变的那些走中期，有衰减兜底。
        stable, kept, merge_book = self._precheck(rec, stable, kept, now, progress=progress)
        merge_book["changing"] = [str(f["text"]) for f in changing]
        rec["merge"] = merge_book

        if stable:
            texts = [str(f["text"]) for f in stable]
            # ★ 每条长期记忆记下：进池的引擎钟与墙钟、来自哪一场、怎么进来的。
            #   ⚠️ 2026-10-05 起长期池**可以被"取代"删条**（用户裁），所以
            #   "只增不减的底片"这句话不再成立 —— 见 `pool.LongPool.drop`。
            when = time.time()
            fmeta = [{"t": float(now), "ts": when, "sid": int(rec.get("k") or 0),
                      "how": "dream"} for _ in texts]
            self.long_pool.add(texts, self.embedder.raw_embed(texts), meta=fmeta)

        # ★ 会变的情形 ⇒ 中期（`add_kept(..., mid=True)`）
        added_mid = self.temp_pool.add_kept(changing, now, rec.get("k"), mid=True)
        merge_book["mid_added"] = added_mid
        added_kept = self.temp_pool.add_kept(kept, now, rec.get("k"))
        self.refresh_temp_vectors()
        self.applied_sids.append(sid)

        # ★ 累计"这条被注入过几次"。这一场真正喂给模型的条目都在 `push_items` 里。
        #   现在只有分布图要用它，但这是**事件流** —— 归档时顺手加几乎免费，
        #   事后再想补就得翻全部转写。
        self.long_pool.bump_hits(
            [t for tn in (rec.get("turns") or [])
             for t in (tn.get("push_items") or [])])

        after_layers = self.long_pool.counts()
        self.last_delta = {
            "sid": sid,
            "clock": float(now),
            "layers": {k: {"add": int(after_layers.get(k, 0) - before_layers.get(k, 0)),
                           "del": int(max(0, before_layers.get(k, 0) - after_layers.get(k, 0)))}
                       for k in set(before_layers) | set(after_layers)},
            "temp": {
                "add": int(added_kept),
                "del": int(applied.get("dropped", 0)) if isinstance(applied, dict) else 0,
                "promote": int((applied.get("promoted_by_hits", 0)
                                + applied.get("promoted_by_model", 0))
                               if isinstance(applied, dict) else 0),
                "after": len(self.temp_pool.items),
                "delta_total": len(self.temp_pool.items) - before_temp,
                # ★★ **按档位拆开**（2026-10-06）：上面那个 `add`/`del` 是**整个临时池**的，
                #   界面把它印到"短期记忆"那一行就会出现自相矛盾 ——
                #   实测：一条「还没说完的」（悬着的事）进来，短期显示 0 却写着 +1。
                #   ⇒ 各档自己显示自己的：加几条按 `from_session` 现算（界面侧），
                #     减几条（含"中期被 done 退回短期"）在这里按档记下来。
                "del_by_tier": dict(applied.get("drop_by_tier") or {})
                               if isinstance(applied, dict) else {},
                "demote_by_tier": dict(applied.get("demote_by_tier") or {})
                                  if isinstance(applied, dict) else {},
            },
        }

        rec.update({"archive": ar, "dream_rounds": rounds, "dream_repair": repairs,
                    "applied": applied, "facts_added": len(facts),
                    "kept_added": added_kept,
                    "temp_after": len(self.temp_pool.items),
                    "temp_alive_after": len(self.temp_pool.alive_idx(now))})


        if transcript is not None:
            transcript.session(rec)
        self.sessions_done += 1
        if rec.get("turns"):
            last = rec["turns"][-1]
            self.last_tail = ("%s\n%s" % (last.get("user", ""), last.get("ai", "")))[:400]
        self.save()
        self.pending.clear()                       # ★ 落地并落盘之后才清
        return rec

        return rec

    def archive(self, rec: dict, transcript=None, now: Optional[float] = None,
                progress=None) -> dict:
        """一次归档 = **出草案 + 立刻落地**（可续、幂等）。

        ⛔ 这条是"老路径"，行为与拆分之前**完全一样**：
          `archive_draft()` 跑归档对话，`archive_land()` 落地。
          界面那条"归档期可对话、按完成才落地"的路走 `archive_draft` / `archive_land` 两步。
        """
        d = self.archive_draft(rec, now=now, progress=progress)
        return self.archive_land(d["rec"], transcript=transcript, progress=progress)


    # ------------------------------------------------------------------ 用户手动改池子
    # ★★ 2026-10-06（用户要求）：对话里**展示出来的**那些条目（`push` 的与 `再查一次` 带回的）
    #   要能**长按就地改**，看到不合适的先手动改掉、再重新生成那一轮，之后在电脑上整理。
    #
    # ⛔ **这条路与归档那条路的界线**（别混，混了就把"手写层不许模型碰"给破了）：
    #   · 归档（模型在改池子）：**底色 / 情境绝不许碰**，`_precheck` 里有硬断言；
    #   · 这里（**用户**在改池子）：**底色 / 情境也允许** —— 那两层本来就是他自己写的，
    #     他是主人。所以函数名带 `user_`，不与归档共用入口。
    # ★ **留痕**：每一条改动都写进 `cache/pool_edits.jsonl`（原文 + 新文 + 层 + 时间 + 动作）。
    #   为什么必须有它：桌面那边下次 `push_pool.ps1` 会把池子**整个覆盖**，
    #   手改只活在手机里 ⇒ 这份账本是"手机上的临时改动"唯一的凭据（`backup.ps1` 会取回它）。
    def _pool_edit_log_path(self) -> str:
        return os.path.join(self.cache_dir, "pool_edits.jsonl")

    def _log_pool_edit(self, rec: dict) -> None:
        import json as _json
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(self._pool_edit_log_path(), "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")

    def pool_edits(self) -> List[dict]:
        """读那份账本（界面用它给"手改过的条目"打记号）。读不出来就当空 —— 不抛。"""
        import json as _json
        path = self._pool_edit_log_path()
        if not os.path.exists(path):
            return []
        out = []
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        out.append(_json.loads(line))
        except Exception:                                        # noqa: BLE001
            return out
        return out

    def edited_texts(self) -> List[str]:
        """**现在池子里**、且是手机上手改过的那几条文本（删掉的不算 ⇒ 界面不会标空）。"""
        alive = set(self.long_pool.texts) | {it.text for it in self.temp_pool.items}
        latest = {}
        for rec in self.pool_edits():
            if rec.get("action") == "edit" and rec.get("new"):
                latest[str(rec["new"])] = True
            elif rec.get("action") == "delete" and rec.get("old"):
                latest.pop(str(rec["old"]), None)
        return sorted(t for t in latest if t in alive)

    def _find_entry(self, text: str):
        """按**原文**找条目（长期池 + 临时池）。为什么按文本找而不是按编号：
        `push_items` / `pulls.got` 里记的就是文本，编号会随归档与删除漂移。"""
        t = str(text)
        longs = [i for i, x in enumerate(self.long_pool.texts) if x == t]
        temps = [i for i, it in enumerate(self.temp_pool.items) if it.text == t]
        return longs, temps

    def user_edit_entry(self, old_text: str, new_text: str) -> dict:
        """★ 用户手改一条：**就地替换**文本 + 重编码那一行向量（条目数不变）。"""
        new_text = str(new_text).strip()
        if not new_text:
            return {"ok": False, "err": "改成空的了 —— 要删就用删除，别留一条空条目"}
        longs, temps = self._find_entry(old_text)
        if not longs and not temps:
            return {"ok": False, "err": "池子里找不到这一条了（可能刚被归档改过）"}
        ts = time.time()
        vec = self.embedder.raw_embed([new_text])
        for i in longs:
            lay = self.long_pool.layer_of(i)
            self.long_pool.rewrite(i, new_text, vec,
                                   patch={"t": float(self.clock), "ts": ts, "how": "用户手改"})
            self._log_pool_edit({"ts": ts, "action": "edit", "where": "长期池", "layer": lay,
                                 "old": str(old_text), "new": new_text})
        for i in temps:
            self.temp_pool.items[i].text = new_text
            self._log_pool_edit({"ts": ts, "action": "edit", "where": "临时池",
                                 "layer": self.temp_pool.layer_label(i),
                                 "old": str(old_text), "new": new_text})
        if temps:
            self.refresh_temp_vectors()
        self.save()
        return {"ok": True, "n": len(longs) + len(temps),
                "where": ["长期池"] * len(longs) + ["临时池"] * len(temps)}

    def user_delete_entry(self, text: str) -> dict:
        """★ 用户手删一条（**底色/情境也允许**，见本节开头那段界线）。"""
        longs, temps = self._find_entry(text)
        if not longs and not temps:
            return {"ok": False, "err": "池子里找不到这一条了（可能刚被归档改过）"}
        ts = time.time()
        layers = [self.long_pool.layer_of(i) for i in longs]
        tlayers = [self.temp_pool.layer_label(i) for i in temps]
        if longs:
            self.long_pool.drop_any(longs)
        for i in sorted(temps, reverse=True):
            self.temp_pool.items.pop(i)
        if temps:
            self.refresh_temp_vectors()
        for lay in layers:
            self._log_pool_edit({"ts": ts, "action": "delete", "where": "长期池",
                                 "layer": lay, "old": str(text)})
        for lay in tlayers:
            self._log_pool_edit({"ts": ts, "action": "delete", "where": "临时池",
                                 "layer": lay, "old": str(text)})
        self.save()
        return {"ok": True, "n": len(longs) + len(temps),
                "where": ["长期池"] * len(longs) + ["临时池"] * len(temps)}

    # ------------------------------------------------------------------ 落盘 / 恢复
    def save(self) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        V = self.long_pool.V if self.long_pool.V is not None else np.zeros((0, self.cfg.embed.dim))
        TV = self.temp_V
        if TV is None or not len(TV):
            TV = np.zeros((0, self.cfg.embed.dim))
        np.savez_compressed(os.path.join(self.cache_dir, "pool.npz"),
                            V=np.asarray(V, dtype=np.float32),
                            temp_V=np.asarray(TV, dtype=np.float32),
                            n_baseline=self.long_pool.n_baseline,
                            n_situational=self.long_pool.n_situational,
                            n_memories=self.long_pool.n_memories)
        with open(os.path.join(self.cache_dir, "pool_texts.jsonl"), "w", encoding="utf-8") as f:
            for i, t in enumerate(self.long_pool.texts):
                # ★ 元数据与文本**同一行**：两处分开放会错位，而错位是无声的。
                line = {"text": t, "layer": self.long_pool.layer_of(i)}
                line.update(self.long_pool.meta_at(i))
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        meta = {"version": STATE_VERSION, "clock": self.clock, "last_ts": self.last_ts,
                "sessions": self.sessions_done, "last_tail": self.last_tail, "scene": self.scene,
                "encoder": {"model": self.embedder.fingerprint[0],
                            "dim": self.embedder.fingerprint[1]},
                "applied_sids": self.applied_sids,
                "last_delta": self.last_delta,
                "temp": self.temp_pool.to_state()}
        tmp = os.path.join(self.cache_dir, "state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.cache_dir, "state.json"))


def _drain(llm) -> list:
    """★ **取走并清空**底座的调用日志（没有就返回空）。

    用途：给"归档那一段"与"核对那一段"**分别计时/计 token** —— 现在的做法是
    **调用方**在前后各清一次，而不是在 `dream()` / `merging.run()` 里到处插桩
    （插桩点越少，越不容易漏、也越不容易互相污染）。

    ★ 为什么要它：2026-10-06 查"这次为什么慢"时，仓库里**没有任何计时**
    （`ts_start/ts_end` 只覆盖到收场那一刻，归档整段在它之外）⇒ 只能在**探针里
    临时 monkey-patch `LLM.chat`** 才查出来。那不该是一次性的手艺。
    """
    log = getattr(llm, "call_log", None)
    if not isinstance(log, list):
        return []
    out = list(log)
    del log[:]
    return out


def _as_int(v, default: int = 0) -> int:
    """宽松取整数（工具参数可能给字符串、给 null）—— ⛔ 不抛。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _is_changing(fact: dict) -> bool:
    """★ 这条 `facts` 是「**会变的情形**」还是「**不变的事**」？

    归档要每条给一个 `changes` 标记（`DREAM_SYS` 里要求的）：
    `true` = 会变（在戒烟 / 在找工作 / 猫在生病）⇒ **进中期**（慢衰减，一个季度不提就退）；
    `false` = 不变（他叫什么 / 他养了猫）⇒ **进永久**（长期池）。

    ⚠️ **缺标记 / 读不出来 ⇒ 当"会变的"**：
      用户原话"**永久的这个要慎重**" ⇒ 拿不准的时候，宁可让它日后能退出去，
      也不要一进去就出不来（永久池**没有出口**）。
    """
    v = (fact or {}).get("changes")
    if v is None:
        v = (fact or {}).get("会变")
    if isinstance(v, bool):
        return v
    s = str(v if v is not None else "").strip().lower()
    if s in ("true", "1", "yes", "y", "会变", "可变", "changing", "change"):
        return True
    if s in ("false", "0", "no", "n", "不变", "稳定", "stable", "fixed"):
        return False
    return True


def _read_cache(cache_dir: str, cfg: Config, expect_fp: tuple):
    pool_path = os.path.join(cache_dir, "pool.npz")
    texts_path = os.path.join(cache_dir, "pool_texts.jsonl")
    state_path = os.path.join(cache_dir, "state.json")
    if not os.path.exists(pool_path) or not os.path.exists(texts_path):
        raise FileNotFoundError(
            "长期池缓存不存在：%s\n"
            "先跑 `python tools/prep_pool.py --baseline <底色> --memories <记忆> "
            "[--situational <情境>] --out %s`（**本仓库不带任何具体数据**）。"
            % (cache_dir, cache_dir))
    z = np.load(pool_path, allow_pickle=True)
    texts, pmeta = [], []
    with open(texts_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                texts.append(d.get("text"))
                # ★ 老缓存只有 text/layer ⇒ 元数据是空 dict，**不许因此报错**
                pmeta.append({k: v for k, v in d.items() if k not in ("text", "layer")})
    # ★ **过渡别名（2026-10-05 改名）**：`pool.npz` 里那个键从 `n_negatives` 改为
    #   `n_situational`（层名「情境负面」→「情境」，理由见 `CHANGELOG.md`）。
    #   **旧缓存照样能读** —— 层归属是按**下标**算的（`n_baseline` / `n_situational` / 其余=记忆），
    #   这个键只是个数，取哪个名都一样 ⇒ 不需要为了改名强制重建池子。
    #   ⚠️ 下一次重建池子写的就是新键；那时这条别名可以删掉（记在 `docs/数据契约.md` §3）。
    _nk = "n_situational" if "n_situational" in getattr(z, "files", []) else "n_negatives"
    pool = LongPool(texts=texts, V=np.asarray(z["V"], dtype=float),
                    n_baseline=int(z["n_baseline"]), n_situational=int(z[_nk]),
                    n_memories=int(z["n_memories"]), meta=pmeta)
    if len(pool.texts) != len(pool.V):
        raise ValueError("池子文本数（%d）与向量数（%d）不一致 —— 缓存坏了，请重建"
                         % (len(pool.texts), len(pool.V)))
    pool.prep()

    meta = {}
    if os.path.exists(state_path):
        meta = json.loads(open(state_path, encoding="utf-8").read())
        if int(meta.get("version", 0)) != STATE_VERSION:
            raise ValueError("state.json 版本不符（%s ≠ %d）—— 缓存格式变了，请重建"
                             % (meta.get("version"), STATE_VERSION))
        enc = meta.get("encoder") or {}
        if enc and (enc.get("model"), int(enc.get("dim", 0))) != (expect_fp[0], int(expect_fp[1])):
            raise ValueError("缓存是用别的编码器建的（%s@%s，现在要 %s@%s）——"
                             "**换编码器 = 已有参数作废**，必须重建缓存"
                             % (enc.get("model"), enc.get("dim"), expect_fp[0], expect_fp[1]))
    temp = TempPool.from_state(cfg.time, meta.get("temp") or [])
    # ★ 临时池向量：老缓存没有这一段 ⇒ 给 None，让上层决定要不要重算。
    tV = None
    if "temp_V" in z.files:
        arr = np.asarray(z["temp_V"], dtype=float)
        tV = arr if len(arr) == len(temp.items) else None
    return pool, temp, float(meta.get("clock", 0.0)), meta, tV
