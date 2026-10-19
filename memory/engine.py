# -*- coding: utf-8 -*-
"""引擎：时钟 · 在途那一场 · 收尾 · 唤醒 · 渠道 · 三本账 —— **它不装记忆**。

★★ 2026-10-17 **重写**（不是删改）。旧引擎是"池子的门面"：`long_pool` / `temp_pool` /
  `pool.npz` 长在它身上 ⇒ **没有池子就开不了机**。真机上真出过这个病：把测试数据搬走以后
  连"看世界"那一路（渠道 / 账本 / 活动记录）都跟着不工作，只好另开一条 `_open_channels`
  绕开引擎 —— 那是补丁，不是设计。

  新线里记忆的实体是 **图（件 + 标记集）** 与 **底片（存档）**，池子整个下去了 ⇒
  引擎也跟着**脱钩**：

      ⛔ 引擎不再是记忆的容器。它只管"这一场怎么开始、怎么记在途、怎么收尾"。
      ⛔ 引擎里**不再有任何向量**：图自己落 `图.json.gz`，底片自己落 `<场id>.json.gz`。

一个 `cache_dir` 里引擎碰的东西（**都不属于记忆本身**）：

    state.json    时钟（天）/ 场次号 / 上一场的尾巴 / 已收尾场次号 / 编码器指纹
    pending.json  **在途的那一场**（还没到终态）—— 见 `pending.py`
    wakeups.json  它自己排的"过一会儿再看"
    <tag>_out.txt  <tag>_transcripts.jsonl     见 `transcript.py`
    activity.jsonl · budgets*.json · channels.json · delivery*.jsonl  见各自模块

★ **空目录也能开机**：`Engine.load` 不要求任何缓存文件先存在 —— 新装的 App 照样能开场。
★ **时钟单位 = 天**：模拟时由 `sample_gap()` 推进；真人使用时由**真挂钟时间**推进
  （`advance_to_wall()`），于是"间隔中位 3 天"那个【假设】**自然消失**。
★ **换编码器 = 已有参数作废** ⇒ 缓存里记编码器指纹，不一致就**拒绝加载**并要求重建，
  不许悄悄混用两个坐标系（图里件的向量、底片里的指纹都用它）。
★ **归档可续**（`pending.json` + `applied_sids`）：账只有一笔 ——
  **`pending.json` 存在就表示"这一场还没到终态"**；终态只有两个：
  **收尾成功**，或**失败后彻底放弃**（`archive_draft.land == "failed"`）。
  收尾必须写转写、场次 +1、清标记 ⇒ **转写永远只写一行**（`_settle` 靠 `applied_sids`
  挡住"`save()` 写了、`clear()` 之前被杀"那个窗口）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Optional

from .activity import ActivityLog
from .budgets import BudgetStore
from .channels import ChannelCatalog
from .config import Config, Credentials
from .deliver import Gate
from .embedder import Embedder
from .llm import BaseLLM
from .pending import PendingStore
from .prompts import TOOLS_AUTONOMY, TOOLS_MEMORY, TOOLS_WEB
from .reading import ReadingBuilder
from .wakeups import WakeupStore
from .web import Search

# 3 = **不再有池子**（`pool.npz` / `pool_texts.jsonl` 与 state.json 里 `temp` 那一格都没了）。
# 2 = 加了 `applied_sids`（归档幂等账本）与 `pending.json` 续做（旧线）。
# ⚠️ 读的时候 **2 和 3 都收**：2 那份里的 `temp` 直接**忽略**（池子已经不存在了），
#   其余几格（时钟 / 场次 / 已收尾 / 编码器指纹）跨版本是同一个意思 ——
#   ⇒ 不为换代把用户的历史（时钟、场次号、幂等账）清掉。
STATE_VERSION = 3
_READABLE_VERSIONS = (2, 3)


class Engine:
    def __init__(self, cfg: Config, embedder, llm, cache_dir: str, clock: float = 0.0):
        self.cfg = cfg
        self.embedder = embedder
        self.llm = llm
        self.cache_dir = cache_dir
        self.clock = float(clock)
        self.last_ts: Optional[float] = None
        self.sessions_done = 0
        self.last_tail: Optional[str] = None
        self.scene: Optional[str] = None
        self.pending = PendingStore(cache_dir)
        # ★ **主动唤醒队列**（模型自己给自己排的"过一会儿再看"）。
        #   放在 Engine 上而不是 Session 上：唤醒必须**跨场次**活着 ——
        #   这一场聊完、App 被杀、下次打开，那条唤醒还在。
        self.wakeups = WakeupStore(cache_dir)
        # ★ **三本账里的两本**（模型唤起 ≤50/天 · 投递 ≤5/天，见 `budgets.py`）。
        #   L1 唤起模型之前**必须先 `take("llm")` 成功**才准花钱。
        self.budgets = BudgetStore(cache_dir)
        # ★ **活动记录**（它做过什么；可查、不推送）。
        self.activity = ActivityLog(cache_dir)
        # ★ **投递闸门**（活跃 ≠ 开口）：开口这件事单独一道门，默认不开。
        self.gate = Gate(cache_dir, self.budgets)
        # ★ **联网**（工具，不是记忆内容）：两层 + 围栏 + 信源等级 D。
        self.web = Search(self.budgets)
        # ★ **渠道目录 + 读数构造器**（2026-10-05）。
        #   桌面端**没有插件宿主** ⇒ 目录是空的、`reading` 是 None ⇒
        #   工具表里就**一个渠道工具都不出现**（静默降级，✓ 不是报错）。
        #   手机上由 Chaquopy 那侧 `attach_channels()` 把"调 provider"的取数函数接进来。
        self.channels = ChannelCatalog(cache_dir)
        self.reading: Optional[ReadingBuilder] = None
        # 已收尾场次的账本（幂等键）。**只增不删**——补做时靠它认出"其实已经收过了"。
        self.applied_sids: List[int] = []
        # ★★ 2026-10-17（审查抓到）：`_settle(transcript=None)` 会**不留转写行**地收尾。
        #   生产路径三处都传转写，可默认值是 None ⇒ 这个洞必须能看见：
        #   把"哪几场没写进转写"记下来（只增不删），并写 stderr。
        self.缺转写: List[int] = []
        # ★ 上一次归档**动了什么**（每层增/减、临时池增/减/升迁）。
        #   界面拿它显示「上次 +2 −1」这类"这一场带来了什么变化" —— 那是**事件**，
        #   事后无法重建。新线里真正记账的是图（`archive_step.完成`），这一格留给
        #   还没搬走的只读展示用；读不到就是空，**不抛**。
        self.last_delta: Dict[str, object] = {}
        os.makedirs(cache_dir, exist_ok=True)

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
          · 前面几个（`recall` / 唤醒三件）是**我们写的**、冻结的；
          · 后面的渠道工具是**插件声明的**（`toolName`/`useWhen`）⇒ 装了什么插件就有什么工具。
        ⛔ 所以**不许**在这里硬编码任何渠道名。
        ⛔ 也**不许**把"已经没有实现的工具"留在这张表里 —— 模型会去调它，然后拿到一句
          "没有这个工具"（旧池子那五个 `find_text` / `read_entry` / `list_entries` /
          `pool_stats` / `search_memory` 就是这么被删掉的：表里有、实现没了）。
        """
        # ★ 2026-10-18（作者：唤醒那三个先不接入）：只发记忆与联网两组。
        #   `TOOLS_AUTONOMY`（schedule_wakeup / list_wakeups / cancel_wakeup）代码还在，
        #   但**不进请求** —— 表里有、她又看不见，等于没接。
        # ★ 2026-10-18（作者：唤醒那三个、以及联网那两个先不接入）：
        #   联网这一版**没接搜索源**（`web.py` 开头写着）⇒ 发出去她只会拿到"没接"，
        #   还白花一次往返。⇔ 等定了搜索源（SearXNG / 官方 API）再 `attach_web` 并放回来。
        tools = list(TOOLS_MEMORY)
        tools.append({"type": "function", "function": {
            "name": "list_channels",
            "description": ("看看你现在能查哪些渠道、各自最近有没有变化。"
                            "★ 想查什么之前先看这个 —— 它很便宜；具体的读数要用下面那些工具。"),
            "parameters": {"type": "object", "properties": {}}}})
        # ★★★ 2026-10-17（审查抓到）：渠道工具名是插件 `toolName` **原样**进来的，
        #   没有任何校验。插件写一个中文名 ⇒ **整个 `tools` 数组被端点拒**
        #   ⇒ 任何一轮都跑不了 —— 和工具名 `记住` 撞的是同一条 400
        #   （`^[a-zA-Z0-9_-]+$`）。桌面端没有插件宿主、自检用替身 ⇒ 只有真机才抓得到。
        #   ⇒ 按 ASCII 过滤，并去掉与静态工具重名的（重名会让 `_dispatch` 永远命中静态那条）。
        import re as _re
        _已有名 = {str(x.get("function", {}).get("name") or "") for x in tools}
        for t in self.channels.tools():
            _n = str(t.get("name") or "")
            if not _re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", _n) or _n in _已有名:
                continue
            _已有名.add(_n)
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
    def load(cls, cfg: Config, cache_dir: str, creds: Optional[Credentials] = None,
             embedder=None, llm=None) -> "Engine":
        """从缓存加载。**不传凭证也能离线自检**（把 `embedder`/`llm` 换成替身即可）。

        ★ 缓存目录空着也能加载（时钟 0、场次 0）—— 这是新引擎与旧引擎最大的一处不同：
          旧的那个"没有池子就不让开机"，于是"新装一个 App"这件事在代码里根本无法表达。
        """
        cfg.check()
        embedder = embedder or Embedder(cfg.embed, creds or Credentials.load())
        llm = llm or BaseLLM(cfg.chat, creds or Credentials.load())
        meta = _read_state(cache_dir, embedder.fingerprint)
        eng = cls(cfg, embedder, llm, cache_dir, float(meta.get("clock", 0.0)))
        eng.last_ts = meta.get("last_ts")
        eng.sessions_done = int(meta.get("sessions", 0))
        eng.last_tail = meta.get("last_tail")
        eng.scene = meta.get("scene")
        eng.applied_sids = [int(x) for x in (meta.get("applied_sids") or [])]
        eng.缺转写 = [int(x) for x in (meta.get("缺转写") or [])]
        eng.last_delta = meta.get("last_delta") or {}
        return eng

    # ------------------------------------------------------------------ 时间
    def advance_to_wall(self, ts: Optional[float] = None) -> float:
        """按**真挂钟时间**推进时钟（单位：天）。第一次调用只记起点。"""
        ts = time.time() if ts is None else float(ts)
        if self.last_ts is not None:
            self.clock += max(0.0, (ts - self.last_ts) / 86400.0)
        self.last_ts = ts
        return self.clock

    # ------------------------------------------------------------------ 在途
    def checkpoint(self, rec: dict, msgs: Optional[list] = None) -> None:
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
        """把上一次**没到终态**的那一场补做完。**入口（`chat_open` / 后台 tick）启动时调一次。**

        ★★ 新线（2026-10-17）：记忆的落地（④ 层1/层2 · 版本 +1）是 `archive_step.完成`
          的事，**引擎这里只剩收尾** ⇒ 补做也只剩"收尾"这类可能：
            · `land` 不是 `"yes"`（幂等命中 / 收尾被打断）⇒ 收尾；
            · `land == "yes"`（草案已出、**用户在审**）⇒ **一个字都不动**，原样交回。
          旧引擎在这里调 `archive()` 重跑一遍归档 —— 那条路会把用户还没看过的草案归档掉，
          正是 `session.waiting_for_review` 要防的事；现在引擎自己也守这一条。
        """
        rec = self.pending.load()
        if not rec:
            return None
        # ★★★ 2026-10-17（审查抓到）：这里原来把"**聊到一半被划掉**"的那一场也当"补做收尾"
        #   ⇒ 写转写、场次 +1、清 pending，而**从没跑过 `完成()`** ⇒ 这一场的记忆整段丢掉，
        #   且不能再恢复（`applied_sids` 已记）。界面还说"这一场已记账"。
        #   ⇒ 只有**已经结束**（有 `ts_end`）的才谈得上补做收尾；没有的交给 `Session.restore` 继续聊。
        if not rec.get("ts_end") and not isinstance(rec.get("archive_draft"), dict):
            return None                      # ⛔ 收尾；这是"在途"，应当继续聊
        if (rec.get("archive_draft") or {}).get("land") == "yes":
            return rec
        return self.archive_land(rec, transcript=transcript)

    # ------------------------------------------------------------------ 收尾
    def archive_land(self, rec: dict, transcript=None, progress=None) -> dict:
        """★ **收尾**：写转写 · 场次 +1 · 落盘 · 清 pending。

        ★★ 2026-10-17：原来这个方法里那一大段"把 facts 写进长期池 / kept 写进临时池 /
          `again`/`promote`/死亡集 / 落档前核对"整段删掉了 —— 它写的是一个已经不存在的
          池子。新线里 **记忆的落地由 `archive_step.完成` 做**（④ 层1/层2 · 版本 +1），
          引擎只剩收尾这一步。

        ⛔ `land == "failed"` **一律不收尾**：`pending` 保着、场次不加，等下次启动补做
          —— 这与"幂等命中"不是一回事（那条**要**收尾）。
        ⛔ `progress` 收下但**不用**：收尾不调模型，没有进度可报（保留这个参数是为了
          调用点不必分叉：`archive_commit` / `chat_finish` 都带着它）。
        """
        if (rec.get("archive_draft") or {}).get("land") == "failed":
            return rec
        return self._settle(rec, transcript)

    def _settle(self, rec: dict, transcript=None) -> dict:
        """收尾本体。**幂等**：同一场收两次不会写两行转写、也不会把场次加两次。"""
        sid = _sid_of(rec)
        if sid and sid in self.applied_sids:
            # ★ 上次走到"`save()` 写完了、`pending.clear()` 之前"被杀 ⇒ 账已经记过了。
            #   这条守的是文件头那句承诺：**转写永远只写一行**。
            self.pending.clear()
            return rec
        if transcript is not None:
            transcript.session(rec)
        elif sid:
            # ★★ 2026-10-17（审查抓到）：`transcript=None` 时**照常收尾** —— 场次 +1、
            #   `applied_sids` 记上、pending 清掉，**唯独转写那一行没写**。
            #   而 `applied_sids` 是幂等键 ⇒ 以后带转写再来一次会命中幂等分支、
            #   **那一行就永远补不上了**，且不报错（违反 `transcript.py` 头那句
            #   「一场一行，永远只追加」）。
            #   ⇒ 生产路径（`host_glue` 三处）**都传转写**；这里只是不许它悄悄漏。
            #     记下缺哪一场（落 `state.json`，可事后补），并写 stderr。
            self.缺转写.append(sid)
            sys.stderr.write("[收尾] 场 %s 没给转写对象 ⇒ **这一场在转写里没有行**"
                             "（已记进 state.json 的 `缺转写`）\n" % sid)
        self.sessions_done += 1
        if sid:
            self.applied_sids.append(sid)
        turns = rec.get("turns") or []
        if turns:
            last = turns[-1]
            self.last_tail = ("%s\n%s" % (last.get("user", ""), last.get("ai", "")))[:400]
        self.save()
        self.pending.clear()                      # ★ 落地并落盘之后才清
        return rec

    # ------------------------------------------------------------------ 落盘
    def save(self) -> None:
        """只写 `state.json` —— **这里没有向量、没有池子**（文件名就是承诺）。"""
        os.makedirs(self.cache_dir, exist_ok=True)
        meta = {"version": STATE_VERSION, "clock": self.clock, "last_ts": self.last_ts,
                "sessions": self.sessions_done, "last_tail": self.last_tail, "scene": self.scene,
                "encoder": {"model": self.embedder.fingerprint[0],
                            "dim": self.embedder.fingerprint[1]},
                "applied_sids": self.applied_sids,
                "缺转写": self.缺转写,
                "last_delta": self.last_delta}
        tmp = os.path.join(self.cache_dir, "state.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.cache_dir, "state.json"))


def _sid_of(rec: dict) -> int:
    """这一场的幂等键：`sid`（新记法）或 `k`（老的记法）。认不出就 0 = 不做幂等。"""
    for key in ("sid", "k"):
        try:
            v = int((rec or {}).get(key) or 0)
        except (TypeError, ValueError):
            v = 0
        if v:
            return v
    return 0


def _read_state(cache_dir: str, expect_fp: tuple) -> dict:
    """读 `state.json`（**没有就当空** —— 空目录也要能开机）。

    ⚠️ 版本不认识 ⇒ **拒绝加载**（宁可报错让人看见，不许悄悄拿半懂的格式干活）；
       编码器指纹不符 ⇒ 也拒绝（**换编码器 = 已有参数作废**，两个坐标系不许混用）。
    """
    path = os.path.join(cache_dir, "state.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    v = int(meta.get("version", 0))
    if v not in _READABLE_VERSIONS:
        raise ValueError("state.json 版本不符（%s 不认识，能读的是 %s）—— 缓存格式变了"
                         % (v, "/".join(str(x) for x in _READABLE_VERSIONS)))
    enc = meta.get("encoder") or {}
    if enc and (enc.get("model"), int(enc.get("dim", 0))) != (expect_fp[0], int(expect_fp[1])):
        raise ValueError("缓存是用别的编码器建的（%s@%s，现在要 %s@%s）——"
                         "**换编码器 = 已有参数作废**，必须重建缓存"
                         % (enc.get("model"), enc.get("dim"), expect_fp[0], expect_fp[1]))
    # ★ 版本 2 那份里有 `temp`（临时池）—— 池子已经不存在了，**忽略它**，读的时候不带出来。
    return meta
