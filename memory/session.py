# -*- coding: utf-8 -*-
"""一次会话（= 一场）：前缀 → 每轮 [用户, 注入] → 生成 → 可选"再查一次" → 收场时归档。

★ 排列顺序是**做过对照的**：`[system① 处境+用法] [system② 分寸] … [user] [注入]`
  —— 注入**紧贴生成**（几种摆放里这个位置最好）。
★ "再查一次"（`read_z`）**由模型自己决定**该不该调 —— **少触发是对的**，
  ⛔ **不要为了让它多查而调参**（【实测】80 轮里只触发 2 次，都在 1 次往返内完成）。

★★ **注入冷却掩膜**（2026-10-06）：读取器原样跑完，再把**还在冷却期里的那几条**
  从要注入的里面摘掉（用户："就做最简单的，一个掩膜，放在最后决定给不给 AI 看就行"）。
  要治的现象是"**他连发几条消息时，每一轮都想起同一批、于是每一轮都回类似的话**"。
  ⛔ 它**只挡自动注入**：`read_z` 与归档的 `search_memory` 照旧 ——
  那两条是"我要查"，不是"我又被提醒了一遍"，挡了会在池子小的时候把它逼进死角。

★★ **撤回最后一轮**（2026-10-06）：`withdraw_last()` —— 把最后那一轮**当成没发生过**
  （上下文 / 轮次 / 记账 / 冷却掩膜四处一起退），撤掉的那一版**不进记忆**。
  用途是"撤回上一次对话、重新编辑"（用户口径：「就和 DeepSeek 网页一样的逻辑」）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Sequence

from .prompts import load_session_prompts
from .transcript import format_turn_line, push_block
# ⚠️ [W] 编写 **不要**在模块级 import（2026-10-17 真机踩到）：
#    rom . import writing 会在 memory/__init__.py 还在跑的时候反过来把
#    memory.session 弄成"半初始化"⇒ 宿主 rom memory.session import Session 当场
#    ImportError: cannot import name 'Session' from partially initialized module。
#    ⇒ 改成**用的时候再 import**（与 ilm 同一个做法）。桌面复现不出来，只有真机报。


class _替身结果:
    """给"新路"造一个与旧读取器**同形**的结果（`texts/hits/k/k_raw/eff_n/gate_blocked`）。

    为什么不一处处改：下游（记账、`human_line`、逐轮记录）都按这几个字段读；
    新路换的是**取件方式**，不是下游的形状。等旧路删掉时再把这些字段一起收掉。
    """

    def __init__(self, texts: List[str], 件数: int = 0):
        self.texts = list(texts)
        self.hits: List[object] = []
        self.k = int(件数)
        self.k_raw = int(件数)
        self.eff_n = float(len(texts))
        self.gate_blocked = 0


class Session:
    def __init__(self, engine, k: int, opener: Optional[str] = None):
        self.engine = engine
        self.k = k
        self.cfg = engine.cfg
        # ★ 会话前缀走 `load_session_prompts()`：默认就是 `prompts.py` 里那两条**冻结**常量；
        #   用户在界面上改过，就用他改的那一份（整条替换，见 prompts.py 的说明）。
        s1, s2 = load_session_prompts()
        self.msgs: List[dict] = [{"role": "system", "content": s1},
                                 {"role": "system", "content": s2}]
        self.rec: Dict[str, object] = {
            "k": k, "open": opener, "turns": [], "pulls": [],
            "temp_at_start": len(engine.temp_pool.items),
            "push_n": [], "push_comp": {"底色": 0, "情境": 0, "记忆": 0, "临时": 0,
                                        "中期": 0, "悬着": 0},
            "ts_start": time.time(),
        }
        # ★★ 注入冷却的**掩膜**（2026-10-06）：`{条目文本: 解禁轮号}`。
        #   用户口径原话：「就做最简单的，**一个掩膜，放在最后决定给不给 AI 看**就行。」
        #   ⇒ 它作用在**最后一步**：读取器原样跑完（τ/mass/clip/排序一律不动），
        #     再把还在冷却期里的那几条从"要注入的"里面摘掉。
        #   ⛔ **不做替补**（"最简单"的代价，已认）：取回的前 k 条若全被掩掉，
        #     这一轮 push 就是空的 —— 不去捞第 k+1 名。
        #   单位 = **一轮**（一次 `ask`）；**一场之内有效**，新场从空开始。
        self.cd_mask: Dict[str, int] = {}
        self._film = None

    # ------------------------------------------------------------------ 底片
    @property
    def 场id(self) -> str:
        """`YYYY-MM-DD-NN`（NN = 这一场的 `k`）—— 与 `流程框架 §3.1.1` 的 `场id` 约定一致。"""
        ts = float(self.rec.get("ts_start") or time.time())
        return "%s-%02d" % (time.strftime("%Y-%m-%d", time.localtime(ts)), int(self.k))

    def _底片(self):
        """这一场的底片（懒开；恢复在途那一场时 `film.py` 自己会把已有的接回来）。"""
        if self._film is None:
            from .film import 底片, 指纹
            from .prompts import SYS1, SYS2
            self._film = 底片(self.场id, 指纹(self.cfg, (SYS1, SYS2)), self.engine.cache_dir,
                             系统前缀=[str(m.get("content") or "") for m in self.msgs[:2]])
        return self._film

    # ------------------------------------------------------------------ 一轮
    def ask(self, user_text: str, progress=None) -> dict:
        """问一句。

        ★ `progress(kind, text)`（2026-10-07）：给了就**流式**回话 ——
          `kind` ∈ {`reasoning`, `content`}（CoT 与正文分开，界面上分两块显示）。
          ⛔ 它只影响"看得见什么"，不影响任何记账/池子/上下文。
        """
        eng = self.engine
        now = eng.clock
        # ★ 本轮是第几轮（从 1 数）。**由已记录的轮数推**，所以恢复在途那一场之后不会错位。
        turn_no = len(self.rec.get("turns") or []) + 1
        # ★ 这一轮**动上下文之前**的长度 —— 撤回时按它把 `msgs` 截回这一轮之前。
        #   为什么只记长度就够：`ask` 只往 `msgs` 的**尾巴上追加**（user / push / 工具往返 /
        #   最终那条 assistant），从不动前面已有的条 ⇒ 一个长度就能精确截回去。
        msgs_len_before = len(self.msgs)
        # ★★ **取件源**（2026-10-17）：新路 = `[R] 回忆`（件 / 标记集 / 传播）；
        #   旧路 = 池子 + 余弦 + softmax。旧路**留着当兜底**（`session.use_recall=False`），
        #   真机验过之后删（作者口径：旧池子删掉，旧仓库有完整的）。
        取用: List[dict] = []
        回忆为什么 = ""
        用回忆 = bool(getattr(self.cfg.session, "use_recall", True))
        if 用回忆:
            from . import recall as _回忆          # ★ 懒导入（见文件头那条注释）
            from .graph import 图 as _图
            g = _图.载入(eng.cache_dir)
            r = _回忆.回忆(user_text, g, os.path.join(eng.cache_dir, "底片"),
                          嵌入=getattr(eng.embedder, "raw_embed", None))
            回忆为什么 = str(r.get("为什么") or "")
            masked = self._cd_masked(turn_no)
            件们 = []
            for one in (r.get("件") or []):
                原文们 = [t for t in (one.get("原文们") or []) if t not in masked]
                if not 原文们:
                    continue        # 这一件全在冷却期里 ⇒ 不注入（⛔ 不找替补，沿用旧规）
                for 文本 in 原文们:
                    件们.append({"文本": 文本, "强度": float(one.get("分数") or 0.0)})
                取用.append({"件id": one["件id"], "分数": float(one.get("分数") or 0.0),
                             "强度": float(one.get("强度") or 0.0)})
            shown = [x["文本"] for x in 件们]
            shown_hits = []
            blocked = 0
            res = _替身结果(shown, 件数=len(取用))
        else:
            res = eng.reader.retrieve_range(user_text, now, eng.long_pool, eng.temp_pool, eng.temp_V)
            # ★★ 掩膜在这**最后一步**生效：读取器给的那 k 条里，冷却期内的**不注入**。
            #   ⛔ `res` 本身不动（`k` / `k_raw` / `eff_n` 都还是读取器的读数，诊断要用）。
            masked = self._cd_masked(turn_no)
            shown = [t for t in res.texts if t not in masked]
            shown_hits = [h for h in res.hits if h.text not in masked]
            blocked = len(res.texts) - len(shown)
            件们 = [{"文本": h.text, "强度": float(getattr(h, "score", 0.0) or 0.0)}
                   for h in shown_hits]
        turn = self.msgs + [{"role": "user", "content": user_text}]
        # ★★ **注入从"条目清单"改成"一段回忆"**（2026-10-17 作者口径：
        #   「原来的条目变成一段」·「编写的时候，把原文和节点强度给编写模型」）。
        #   取件（`shown`）不变 —— 变的是"给她看什么"：先 `[W] 编写` 成一段，再注入。
        #   ⛔ 三条退路都要有：编写挂了 ⇒ 退回条目清单（别把信息丢掉）；模型说"没有" ⇒ 不注入；
        #      开关关着 ⇒ 老样子（`session.render_recall = False`）。
        回忆: dict = {}
        if shown:
            from . import writing          # ★ 懒导入（见文件头那条注释）
            if bool(getattr(self.cfg.session, "render_recall", True)):
                回忆 = writing.编写(eng.llm, 件们, 场id=self.场id,
                                    轮=turn_no, 线索=user_text)
                writing.记说(eng.cache_dir, self.场id, turn_no, user_text, 件们, 回忆)
                if 回忆.get("编写"):
                    turn.append({"role": "system",
                                 "content": self.cfg.session.push_header + "\n" + 回忆["编写"]})
                elif not 回忆.get("ok"):
                    # 编写失败（或正文被推理吃光）⇒ 退回条目清单：宁可给清单，也别白丢这一轮
                    turn.append({"role": "system",
                                 "content": push_block(self.cfg.session.push_header, shown)})
                # 模型说"没有" ⇒ 这一轮不注入（由它自己判断"此刻想不起什么"）
            else:
                turn.append({"role": "system",
                             "content": push_block(self.cfg.session.push_header, shown)})
        pulls, wake_calls = [], []
        used = 0
        # ★★ 这一轮的 **CoT**（2026-10-07 用户要求：「聊天的 COT 呢？最好也记下来吧？」）。
        #   为什么要记：它原先**只活在界面的内存里**（`ChatViewModel` 那几个流式字段），
        #   App 一被回收就没了 —— 而"我说这句话时在想什么"正是事后最想回看的东西。
        #   ⚠️ 一轮里可能有**多次模型往返**（模型自己"再查一次"）⇒ **都收进来**，
        #     与界面那条流（把所有 delta 拼起来）是同一份东西。
        #   ⛔ 只记在**轮次记录**里，**绝不塞回 `msgs`**：DeepSeek 那边明确不要
        #     把 `reasoning_content` 传回去，塞了既费 token 又可能带偏下一轮。
        reason_parts: List[str] = []
        # ★★ **全流程全记录**（2026-10-17 作者口径：「底片是存档…… 全流程全记录，可以多存，
        #   不能少存」）：这一轮里的**每一次模型往返**都留一份 —— 请求（首次含完整上下文、
        #   之后只记新追加的那几条）、输出（正文/推理/工具调用）、`logprobs`、用量、耗时。
        调用记录: List[dict] = []
        已记到哪 = len(turn)          # 上一次记完之后 `turn` 的长度（下一次只记新增的那几条）
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        while True:
            try:
                起点 = 已记到哪
                msg = eng.llm.chat(turn, tools=eng.session_tools(), progress=progress,
                                   logprobs=True)
                _acc_usage(usage, getattr(eng.llm, "last_usage", None))
            except Exception as e:  # noqa: BLE001
                reply = "[ERR] %r" % e
                break
            _r = str(msg.get("reasoning_content") or "")
            if _r:
                reason_parts.append(_r)
            # ---- 全流程：把这一次调用记下来
            try:
                记录 = dict(msg.get("_call") or {})
                记录["序"] = len(调用记录)
                记录["请求条数"] = len(turn)
                # ★★ **只存增量**（2026-10-17 作者口径：「去存增量」）：
                #   首条 = 这一轮相对**上一轮结束时的上下文**新追加的那几条（`msgs_len_before` 起）；
                #   之后 = 同一次调用之间新追加的（工具往返）。
                #   ⛔ 每条都抄一份"完整上下文"是**平方级**的：上下文 1M token 时每轮多存 3.9 MB、
                #     1000 轮 ⇒ 3.8 GB（实测推算）。拼回来完全等价：系统前缀 + 逐条增量。
                起点 = msgs_len_before if 记录["序"] == 0 else 起点
                记录["请求增量"] = list(turn[起点:])
                记录["输出"] = {"content": msg.get("content") or "",
                                "reasoning": _r,
                                "tool_calls": [{"name": (c.get("function") or {}).get("name"),
                                                "arguments": (c.get("function") or {}).get("arguments")}
                                               for c in (msg.get("tool_calls") or [])]}
                记录["logprobs"] = msg.get("logprobs") or {}
                记录["usage"] = dict(getattr(eng.llm, "last_usage", None) or {})
                调用记录.append(记录)
                已记到哪 = len(turn)
            except Exception:                            # noqa: BLE001  ⛔ 记账失败不打断对话
                pass
            calls = msg.get("tool_calls")
            # ★ `max_pull` 管的是**模型往返次数**（那才是钱），不是"查了几次记忆"——
            #   唤醒工具占用的是同一个往返预算。想放宽就调这个，别去动各工具自己的上限。
            if calls and used < self.cfg.session.max_pull:
                turn = turn + [{"role": "assistant", "content": msg.get("content") or "",
                                "tool_calls": calls}]
                for c in calls:
                    text, rec_one = _dispatch(eng, c, now)
                    if rec_one and rec_one.get("kind") == "pull":
                        pulls.append(rec_one)
                    elif rec_one:
                        wake_calls.append(rec_one)
                    turn = turn + [{"role": "tool", "tool_call_id": c.get("id"),
                                    "content": text}]
                used += 1
                continue
            reply = (msg.get("content") or "").strip()
            break

        self.msgs = turn + [{"role": "assistant", "content": reply}]
        # ★ 掩膜生效**之后**才登记冷却：只冷却"真的给它看过"的那几条
        #   （被掩掉的那些这一轮没进上下文 ⇒ 没理由给它们续期）。
        # ★ 登记了哪几条要**记下来**：撤回这一轮时得把它们从冷却里摘掉
        #   （这一轮已经不算发生过了）。见 `withdraw_last`。
        cd_added = self._cd_mark(shown, turn_no)
        item = {"user": user_text, "ai": reply, "ts": time.time(),
                # ★★ 撤回要用（2026-10-06）：这一轮追加上下文之前的长度、这一轮登记的冷却。
                #   没有它们，"撤回"就只能靠猜 —— 而恢复在途那一场之后猜不出来。
                "msgs_len_before": msgs_len_before, "cd_added": cd_added,
                # ⚠️ `push_items` / `push_n` / `layers` 记的都是**真正注入的**（掩膜之后）
                #    —— 转写要能回答"它这一轮到底看到了什么"。
                "push_items": shown, "push_n": len(shown), "k_raw": res.k_raw,
                # ★ 被掩膜挡掉几条（读取器给了 `res.k`，实际只注入了 `push_n`）。
                #   这个数是**开 / 关冷却那次对照**的读数，别省。
                "cd_blocked": blocked,
                # ★ 被**阈值门**挡掉几条（相关度 < gate_tau）。与 `cd_blocked` 是两回事：
                #   那个挡的是"刚说过"，这个挡的是"根本不够相关"。
                "gate_blocked": int(res.gate_blocked),
                "eff_n": res.eff_n, "layers": _layer_count(shown_hits),
                # 这一轮花了多少 token（服务端给的），以及模型自己"再查一次"的痕迹。
                # ★ 新路：这一轮**取用了哪些件**（旧路为空）+ 没取到的原因（一句话）
                "取用": 取用, "回忆为什么": 回忆为什么,
                # ★★ 界面要的两样：**编写出的那一段** + 「怎么编的」（用了哪几条 / 版本 / 耗时）。
                #   ⛔ 只给用户看；`msgs` 里只有那段文字本身（不进读取路径）。
                "回忆": ({"编写": 回忆.get("编写") or "",
                          "没有": bool(回忆.get("没有")),
                          "用了": [{"文本": x.get("文本"), "强度": x.get("强度")} for x in 件们],
                          "版本": dict(回忆.get("版本") or {}),
                          "耗时ms": int(回忆.get("耗时ms") or 0)}
                         if 回忆 else None),
                # ★ 两个都是**给用户看的**：与"模型看到什么"无关。
                "usage": usage, "pulls": pulls,
                # ★ **这一轮的 CoT**（推理原文，可能在多次往返里）。给界面"点开看推理"用，
                #   也随转写留档 —— 它就是"它说那句话时在想什么"。
                "reasoning": "".join(reason_parts),
                # ★ **它自己排的唤醒**也要记进转写 —— 那是"它做过的事"的证据
                "wakeups": wake_calls}
        self.rec["turns"].append(item)
        self.rec["push_n"].append(len(shown))
        self.rec["pulls"].extend(pulls)
        for kk, vv in item["layers"].items():
            self.rec["push_comp"][kk] = self.rec["push_comp"].get(kk, 0) + vv

        # ★ 每轮落盘：归档是**一场结束时**才做的，而这一场随时可能被划掉（手机上尤其）。
        #   不落盘 ⇒ 那几轮只活在内存里，进程一没就整段丢。
        #   `msgs` 一起存：否则恢复出来的是"记得发生过什么、却接不上刚才那句"。
        # ★ 掩膜状态一起落盘：手机上进程随时可能被杀，恢复之后不该把刚说过的话再说一遍。
        self.rec["cd_mask"] = self.cd_mask
        eng.checkpoint(self.rec, msgs=self.msgs)

        # ★★ **落底片**（2026-10-17 · 存档：全流程全记录）
        #   存的不只是"说了什么"，还有这一轮每一次模型往返的请求/输出/`logprobs`/用量/耗时。
        #   ⛔ 出任何错都不许打断对话（`加轮` / `落盘` 自己已经 try 住；这里再兜一层）。
        try:
            f = self._底片()
            # ★ 编写那一次调用排在最前（它是这一轮流程的第一步）——「全流程全记录」
            全流程 = ([dict(回忆["调用"], 序=0)] if 回忆.get("调用") else []) + 调用记录
            全流程 = [dict(c, 序=i) for i, c in enumerate(全流程)]
            f.加轮(turn_no, user_text, reply, 调用=全流程,
                   取用=(取用 or None),             # ★ 新路给件id+分数；旧路仍是 `null`（合法值）
                   旧池取用=shown,                   # 过渡字段：件层接上就删
                   参数={"形状": "旧池（无件层）",
                         "k": int(res.k), "k_raw": int(res.k_raw),
                         "gate_tau": float(getattr(self.cfg.reader, "gate_tau", 0.0) or 0.0),
                         "cd_blocked": int(blocked), "gate_blocked": int(res.gate_blocked),
                         "注入": ("编写" if 回忆.get("编写") else ("清单" if shown else "空")),
                         "编写耗时ms": int(回忆.get("耗时ms") or 0),
                         "编写提示词哈希": str(回忆.get("提示词哈希") or "")},
                   成本=dict(usage))
            f.落盘()
        except Exception:                                # noqa: BLE001
            pass

        return {"reply": reply, "push": shown, "k": res.k, "k_raw": res.k_raw,
                "cd_blocked": blocked,
                "gate_blocked": int(res.gate_blocked),
                "eff_n": res.eff_n, "pulls": pulls, "usage": usage,
                # ★★ 「那段回忆」（编写原文 + 用了哪几条 + 版本/耗时）—— 界面点开要看的就是它
                "回忆": 回忆 or None,
                # ★ 它自己排的唤醒 / 调过的渠道也回给宿主（界面与转写要看"它做过什么"）
                "wakeups": wake_calls,
                "human_line": format_turn_line(self.cfg.reader.tau, res.eff_n, len(shown),
                                               res.k_raw, shown)}

    # ------------------------------------------------------------------ 撤回
    def withdraw_last(self) -> dict:
        """★ **撤回最后一轮**（2026-10-06）：把这一轮**当成没发生过**，可连续用。

        用户口径：「撤回上一次对话，重新编辑 —— 就和 DeepSeek 网页一样的逻辑」。

        **撤的是什么**（四处一起退，缺一处就会前后不一致）：

        | # | 退什么 | 为什么要退 |
        |---|---|---|
        | ① | `msgs`（送进模型的那份上下文） | 不回退 ⇒ 它**记得那句被撤掉的话**，重发时等于说了两遍 |
        | ② | `turns[-1]`（轮次记录） | 归档、转写、`push_comp` 都按它算 ⇒ 退了它，**被撤的那一版不进记忆** |
        | ③ | 记账（`push_n` / `pulls` / `push_comp`） | 它们都是逐轮累加的，不退就等于把没发生的算进去了 |
        | ④ | 冷却掩膜（这一轮登记过的那几条） | 不退 ⇒ 那一轮想起来的条目会**卡在冷却里**，重发时反而想不起来 |

        ⛔ **不碰池子**：池子只在归档时变（对话期间冻结）。所以撤回**天然不污染记忆** ——
           `again/done/promote`、`hits` 累计、归档输入，全部以"最后留下的那些轮次"为准。
        ⛔ **也不能撤已归档的场次**：那时候记忆已经落地，回退它要动池子（本函数不做，也做不到）。

        ★ **留痕**：撤回是**事件**（一条说过又消失的话）。转写一场只写一行，
           事后**重建不出来** ⇒ 记进 `rec["withdraws"]`（原文照留），跟着那一行进转写。
        """
        turns = self.rec.get("turns") or []
        if not turns:
            return {"ok": False, "err": "这一场还没有能撤回的一轮"}
        last = turns[-1]

        # ① 上下文截回这一轮之前。
        n = last.get("msgs_len_before")
        if n is None:
            # 旧版本记的轮次（或从旧 `pending.json` 恢复的）没这个字段。
            # ⛔ 不许**猜**一个长度：猜错就是把别人的话接到它嘴里（比撤不回来严重得多）。
            return {"ok": False, "err": "这一轮是旧版本记的（没记上下文长度），"
                                        "撤回会把上下文接错 —— 先收场，再重新开一场"}
        n = int(n)
        if not (0 <= n <= len(self.msgs)):
            return {"ok": False, "err": "上下文长度对不上（记的是 %d、现在是 %d）"
                                        % (n, len(self.msgs))}
        self.msgs = self.msgs[:n]

        # ④ 冷却掩膜：把这一轮登记过的那几条摘掉。
        #   ⚠️ 直接删就行：`_cd_mark` 只会登记**这一轮真的注入过的**，而注入过的按定义
        #      不在冷却期内（在的话会被掩膜挡掉）⇒ 那些键本来就只属于这一轮。
        for t in (last.get("cd_added") or []):
            self.cd_mask.pop(str(t), None)

        # ② 轮次记录
        turns.pop()

        # ③ 记账
        if self.rec.get("push_n"):
            self.rec["push_n"].pop()
        n_pull = len(last.get("pulls") or [])
        if n_pull:
            self.rec["pulls"] = (self.rec.get("pulls") or [])[:-n_pull]
        comp = dict(self.rec.get("push_comp") or {})
        for k, v in (last.get("layers") or {}).items():
            comp[k] = max(0, int(comp.get(k) or 0) - int(v or 0))
        self.rec["push_comp"] = comp

        # 留痕：原文照留（撤掉的那一版**不进记忆**，但"它曾经说过"要查得到）
        self.rec.setdefault("withdraws", []).append({
            "n": len(turns) + 1, "user": last.get("user"), "ai": last.get("ai"),
            "ts": time.time(),
        })

        # 同一次落盘：上下文与掩膜不一起写下去，恢复出来就是自相矛盾的一份状态。
        self.rec["cd_mask"] = self.cd_mask
        self.engine.checkpoint(self.rec, msgs=self.msgs)

        # ⑤ ★★ **底片里留痕，但⛔不删原文**（2026-10-17 作者口径：底片是存档；
        #    "撤回"本身也是**发生过的事**，它也要留下来）。⇒ 只给那一轮打标 + 记一笔。
        try:
            f = self._底片()
            f.撤回(len(turns) + 1)
            f.落盘()
        except Exception:                                # noqa: BLE001
            pass

        return {"ok": True, "user": str(last.get("user") or ""),
                "ai": str(last.get("ai") or ""), "turns_left": len(turns)}

    # ------------------------------------------------------------------ 冷却掩膜
    def _cd_masked(self, turn_no: int) -> set:
        """现在还在冷却期里的条目文本（顺手把**过期的丢掉**，别让它无限胀）。"""
        if not self.cfg.cooldown.enabled:
            return set()
        self.cd_mask = {t: u for t, u in self.cd_mask.items() if int(u) >= turn_no}
        return set(self.cd_mask)

    def _cd_mark(self, shown: Sequence[str], turn_no: int) -> List[str]:
        """把这一轮**真正注入过的**条目放进冷却：解禁轮号 = 本轮 + `cooldown.turns`。

        ★ 返回**这次登记了哪几条** —— 撤回这一轮时按它把冷却摘掉（见 `withdraw_last`）。
        ⚠️ **一次登记就够，不做续期**：冷却期内它进不了注入 ⇒ 不可能"再命中刷新"，
           于是 SillyTavern 那条"已激活期间再命中不刷新时长"**天然成立**（规则 5）。
        ⭐ 回来之后若再被注入，会重新登记一次（那时它已经过了一个完整的冷却）。
        """
        if not self.cfg.cooldown.enabled:
            return []
        until = turn_no + max(1, int(self.cfg.cooldown.turns))
        for t in shown:
            self.cd_mask[str(t)] = until
        return [str(t) for t in shown]

    # ------------------------------------------------------------------ 恢复
    @classmethod
    def restore(cls, engine, rec: dict) -> "Session":
        """从"在途的那一场"（`pending.json`）接着来 —— 宿主被杀之后再打开就是这条路。

        ★ 上下文（`msgs`）必须一起恢复。只恢复 `turns` 的后果是：它**记得发生过什么**
          （归档能看见），却**接不上刚才那句**（生成时看不到）—— 手机上 App 被杀是常态，
          这个差别用户一眼就能看出来。
        ★ 只恢复上下文，**不碰池子**：池子只在归档时变，中途恢复不该留下任何痕迹。
        ★ **冷却掩膜也要一起回来**：不然被杀一次之后，刚说过的话会被再说一遍。
        """
        s = cls(engine, int(rec.get("k") or (engine.sessions_done + 1)),
                opener=rec.get("open"))
        s.rec = dict(rec)
        msgs = rec.get("msgs")
        if msgs:
            s.msgs = list(msgs)
        for t, u in (rec.get("cd_mask") or {}).items():
            try:
                s.cd_mask[str(t)] = int(u)
            except (TypeError, ValueError):
                continue
        return s

    # ------------------------------------------------------------------ 收场
    def finish(self, transcript=None, progress=None) -> dict:
        self.rec["ts_end"] = time.time()
        return self.engine.archive(self.rec, transcript=transcript, progress=progress)

    # ---------------------------------------------------------------- 归档期（2026-10-07）
    # 用户设计：归档 = **接着刚才对话的特殊对话** —— 它发完草案你还能接着说，
    # 最后按「完成」才落地。三条路分开，都是为了"池子在你按完成之前一字不动"。
    def archive_draft(self, progress=None) -> dict:
        """★ **只出草案**（不落地），草案存进 `rec["archive_draft"]` 并随 pending 落盘。

        ★★ 2026-10-17 换成**新线**（`archive_step`）：草案不再是"我打算记哪几条"，而是
          「**这一场认出了哪几处 → 每处归进件X / 新建件Y**」。⛔ 图与标记集一字不动。
        """
        self.rec["ts_end"] = time.time()
        from . import archive_step                 # ★ 懒导入（模块级 import 会踩循环，见文件头）
        from .graph import 图
        f = self._底片()
        f.落盘()
        底片数据 = {"场id": f.场id, "系统前缀": f.系统前缀, "原文": f.原文(),
                    "逐轮": f.逐轮, "撤回记录": f.撤回记录, "指纹": f.指纹}
        g = 图.载入(self.engine.cache_dir)
        草 = archive_step.草案(底片数据, g, self.engine.llm, f.场id,
                              嵌入=getattr(self.engine.embedder, "raw_embed", None),
                              进度=progress)
        self.rec["archive_draft"] = {"land": "yes", "草": 草, "talk": [],
                                     "rounds": len(草.get("标记") or []), "repairs": 0}
        self.engine.pending.save(self.rec)
        return {"land": "yes", "草": 草}

    def archive_revise(self, user_text: str, progress=None) -> dict:
        """★ 归档期里用户又说了话 ⇒ 改草案（**图与标记集仍然一字不动**）。

        ⚠️ 过渡实现（诚实版）：新线的草案是"几处 → 归进/新建"，
          这里按**字面**理解用户的意见 —— 认出「第 N 轮」就**跳过**那一处（标成 跳过），
          认不出就把话记进 `talk` 并如实说"没认出要改哪一处"。
          ⛔ 不偷偷调模型重判（那会花钱且不可预期）；等这一版上了真机、看清真实说法再升。
        """
        import re
        d = dict(self.rec.get("archive_draft") or {})
        草 = d.get("草")
        if not 草:
            return {"land": "failed", "err": "还没有草案可改（先跑一次归档）"}
        轮们 = {int(x) for x in re.findall(r"第\s*(\d+)\s*轮", str(user_text))}
        改了 = []
        if 轮们:
            for p in (草.get("判") or []):
                if int(p.get("轮") or 0) in 轮们 and not p.get("跳过"):
                    p["跳过"] = True
                    改了.append(int(p["轮"]))
            if 改了:
                草["摘要"] = (草.get("摘要") or "") + "\n（按你说的，第 %s 轮这次不记。）" % (
                    "、".join(str(x) for x in sorted(改了)))
        d["talk"] = (list(d.get("talk") or []) + [str(user_text)])[-20:]
        d["草"] = 草
        self.rec["archive_draft"] = d
        self.engine.pending.save(self.rec)
        return {"land": "yes", "草": 草, "改了": 改了,
                "err": "" if 改了 else "没认出要改哪一处 —— 说「第 3 轮别记」就行"}

    

    def archive_commit(self, transcript=None, progress=None) -> dict:
        """★ 「完成」：把草案**落地**（这一步才真正写图/标记集，然后收尾）。

        ★★ 新线（2026-10-17）：先 `archive_step.完成`（④ 层1/层2 + 版本 +1 + `判/`），
          再把 `land` 置成 `"no"` **借旧线那条"幂等收尾"** 走一遍收尾
          （写转写 / 场次 +1 / 清 pending）—— ⛔ 不重写收尾，也不让旧池子动一分。
        """
        d = dict(self.rec.get("archive_draft") or {})
        草 = d.get("草")
        if not 草:
            return self.engine.archive_land(self.rec, transcript=transcript, progress=progress)
        from . import archive_step
        from .graph import 图
        g = 图.载入(self.engine.cache_dir)
        # 「跳过」的那些不算：先滤掉再落地
        有效 = dict(草)
        有效.setdefault("场id", self.场id)      # ★ 旧版本存下的草案可能没有这一格
        有效["判"] = [p for p in (草.get("判") or []) if not p.get("跳过")]
        res = archive_step.完成(g, 有效)
        self.rec["archive_draft"] = {"land": "no"}      # ★ 借"池子不动，只收尾"那条路
        self.rec["新线"] = {"件": [x.get("件") for x in (res.get("层1") or [])],
                          "版本": res.get("版本"),
                          "剪出": (res.get("层2") or {}).get("剪出") or []}
        out = self.engine.archive_land(self.rec, transcript=transcript, progress=progress)
        return out


def waiting_for_review(rec) -> bool:
    """★★ 这一场**已经收工、草案已经出来、但还没落地** —— 也就是"用户在审"那一段吗？

    用户设计（原话）：「把归档看作**接着刚才对话的特殊对话** —— 模型发完消息之后
    用户可以接着发消息，最后归档完成之后**完成按钮用户点**」
    ⇒ 推论只有一条，但很硬：**没按「完成」就一直搁着、池子一字不动**。

    ⛔ 所以宿主那两条"补做"的路（后台 tick 的 `resume_pending`、下次打开的 `chat_open`）
      **必须先问这一句**：否则 App 被系统回收一次，用户还没看过的那份草案
      就被"补做"那条老路**自动落地**了 —— 池子变了，而他从没按过「完成」。
    ★ 判据只看 rec 自己（不碰 engine、不碰网络），所以这条判断**能单独自检**。
    ★ 反面（**不算**审阅期，照旧补做）：
      · 没有 `ts_end`：聊到一半被杀 ⇒ 那是"接回来继续聊"；
      · 没有草案（`land` 不是 `"yes"`）：归档对话**跑到一半**被杀（`land="failed"`）、
        或幂等命中（`land="no"`）⇒ 这两条老路本来就是为它们准备的。
    """
    if not isinstance(rec, dict) or not rec.get("ts_end"):
        return False
    d = rec.get("archive_draft")
    if not isinstance(d, dict):
        return False
    # ★★ 两代草案都要认：旧线 `ar`（dream 产出的 JSON）；新线 `草`（archive_step 的「判」）。
    #   ⛔ 只认 `ar` 的后果很实：新线的草案被当成"没有草案"⇒ 重启时走**旧路补做**，
    #      直接写进旧池子，而用户从没按过「完成」。
    return str(d.get("land") or "") == "yes" and bool(d.get("ar") or d.get("草"))


def _回忆工具(eng, 线索: str):
    """`recall` 工具走新线：`[R]` 取件 → 把**整组原文**当作"查到的内容"回给它。

    ★ 返回的形状与旧读取器一致（`_替身结果`：`texts/hits/k/k_raw/eff_n/gate_blocked`），
      因为下游那段"拼话"的代码一行都不用改。
    """
    from . import recall as _回忆
    from .graph import 图 as _图
    g = _图.载入(eng.cache_dir)
    r = _回忆.回忆(线索, g, os.path.join(eng.cache_dir, "底片"),
                  嵌入=getattr(eng.embedder, "raw_embed", None))
    文本们: List[str] = []
    for one in (r.get("件") or []):
        文本们.extend(one.get("原文们") or [])
    return _替身结果(文本们, 件数=len(r.get("件") or []))


def _dispatch(eng, call: dict, now: float):
    """**按工具名分派**。返回 `(回给模型的文本, 记账字典或 None)`。

    ★ 为什么要按名字分派：原来这里写死了"每次调用都是 `read_z`"（`_query_of` 只取 `query`）。
      加了唤醒工具以后那个假设不成立。
    ★⛔ **每条分支都必须"回一句它能读懂的话"**（中文、说清为什么），**不许抛异常** ——
      抛了它就以为"工具坏了"，而它需要的是一条能据此改主意的理由
      （"太近了，最短 15 分钟"）。
    """
    fn = call.get("function") or {}
    name = str(fn.get("name") or "")
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except Exception:                                            # noqa: BLE001
        args = {}
    if not isinstance(args, dict):
        args = {}

    # ★ 2026-10-17 改名：`read_z` → `recall`（新线的叫法就是 `[R] 回忆`）。
    #   ⚠️ 旧名**留着当别名**：老 `pending.json` 的历史里有 `read_z` 的 tool 记录，
    #   恢复在途那一场时不该因为改名而认不出来。
    if name in ("recall", "read_z"):
        q = str(args.get("query") or "")
        # ★ 2026-10-17：她"自己再查一次"也走 **`[R] 回忆`**（与自动注入同一条路）；
        #   `use_recall=False` 时才退回旧读取器（过渡兜底）。
        if bool(getattr(eng.cfg.session, "use_recall", True)):
            got = _回忆工具(eng, q)
        else:
            got = eng.reader.retrieve_range(q, now, eng.long_pool, eng.temp_pool, eng.temp_V)
        # ★★ 2026-10-06：**空结果要有一句话**，而且这句话**不许有任何歧义**。
        #   为什么不能是空字符串：那正是"问了一件池子里没有的事"的现场 —— 旧写法回 `""`，
        #   模型只看到"工具没给东西"，于是**当成查到了**、拿自己手里的规矩顶上（真机抓到过）。
        #   ⛔ 也不能说"池子里没有相关的"（第一版，用户拨回）：门槛只是一条**分数线**，
        #      低分只说明"没翻到贴得上的"，**不等于"这事不存在"** —— 那是过度断言。
        #   ⛔ 也不能说"想不起来"（第二版，用户又拨回）：它在中文里至少三种读法，
        #      每一种都危险 —— ① "我以前知道、只是取不出来" ⇒ **凭空暗示存在过一段记忆**
        #      （等于伪造过去）；② "我记不清了"（人推脱时的说法）；
        #      ③ 被当成"这事发生过"。⇒ **回复里不许出现任何记忆/存在论的说法**。
        #   ★ 所以：只说**这次调用真正发生了什么**（排过序、没有一条够得上那条线），
        #     再把三条"不许"讲清，最后指向底色里已有的那句话（A05：「这个我还不知道」）。
        #   ⚠️ 三版措辞都被人拨过，一次比一次准，记着免得退回去：
        #      ① "池子里没有相关的" ⇒ 过度断言（把"没翻到"说成"不存在"）；
        #      ② "想不起来"       ⇒ 歧义（暗示"以前知道、只是取不出来"＝伪造过去）；
        #      ③ "没有返回任何条目" ⇒ **不实**（检索跑了，只是没有一条过线）——
        #         而且把"没有过线的"这条**本身有用的信息**丢掉了。
        if not got.texts:
            return ("（这条查过了：池子里没有哪一条够得上「贴得上」那条线，最靠前的也没够上"
                    " —— 也就是说，没有贴得上的东西。\n"
                    "· 别当成「查到了」：不许拿手里现有的条目当成他的意思；\n"
                    "· 别当成「没有这回事」：这只是「没有贴得上的」，不等于事实不存在；\n"
                    "· 别让他以为我以前知道过这件事。\n"
                    "真要用到这一块，就照底色的写法说：这个我还不知道。）",
                    {"kind": "pull", "query": q, "got": [],
                     "empty": True, "gate_blocked": int(getattr(got, "gate_blocked", 0) or 0)})
        return "；".join(got.texts[:4]), {"kind": "pull", "query": q, "got": got.texts[:4],
                                          "empty": False,
                                          "gate_blocked": int(getattr(got, "gate_blocked", 0) or 0)}

    if name == "schedule_wakeup":
        try:
            mins = float(args.get("after_minutes"))
        except (TypeError, ValueError):
            return ("没排上：after_minutes 要是个数字（多少分钟之后）",
                    {"kind": "wakeup", "tool": name, "args": args, "ok": False})
        r = eng.wakeups.schedule(after_minutes=mins, why=str(args.get("why") or ""))
        if r.get("ok"):
            txt = "排上了：%.0f 分钟后（今天还剩 %d 次）。到点我会自己醒来看一眼。" % (
                mins, int(r.get("remaining_today") or 0))
        else:
            txt = "没排上：%s" % r.get("reason")
        return txt, {"kind": "wakeup", "tool": name, "args": args, "result": r,
                     "ok": bool(r.get("ok"))}

    if name == "list_wakeups":
        items = eng.wakeups.list()
        if not items:
            txt = "现在没有挂着的唤醒。"
        else:
            txt = "挂着 %d 条：" % len(items) + "；".join(
                "%.0f 分钟后（%s）" % (it.get("in_minutes") or 0, it.get("why") or "没说为什么")
                for it in items)
        return txt, {"kind": "wakeup", "tool": name, "result": items, "ok": True}

    if name == "cancel_wakeup":
        r = eng.wakeups.cancel(str(args.get("id") or ""))
        txt = ("撤掉了第 %s 条。" % r.get("id")) if r.get("ok") else ("没撤掉：%s" % r.get("reason"))
        return txt, {"kind": "wakeup", "tool": name, "args": args, "result": r,
                     "ok": bool(r.get("ok"))}

    if name == "web_search":
        r = eng.web.summarize(str(args.get("query") or ""))
        return str(r.get("text") or ""), {"kind": "web", "tool": name, "ok": bool(r.get("ok"))}

    if name == "web_fetch":
        r = eng.web.detail(str(args.get("url") or ""))
        return str(r.get("text") or ""), {"kind": "web", "tool": name, "ok": bool(r.get("ok"))}

    if name == "list_channels":
        # ★ L1 目录（很便宜）：有哪些渠道 + 各自有没有变化。
        #   ⛔ 这里**不**顺手去问插件 —— 刷新是 L0 机械检查的事（那一步不花模型的钱）。
        return eng.channels.render(), {"kind": "channels", "tool": name, "ok": True}

    # ★★ 渠道工具：名字**由插件声明**（`toolName`），宿主只查表 ⇒ 不硬编码任何一个渠道
    ch = eng.channel_tool(name)
    if ch is not None:
        r = eng.read_channel(ch, args)
        return str(r.get("text") or ""), {"kind": "channel", "tool": name,
                                          "metric": ch.get("metric"), "ok": bool(r.get("ok"))}
    # ★ 不认识的工具：如实说，别装作做过什么
    return "没有这个工具：%s" % (name or "(空名字)"), {"kind": "unknown", "tool": name, "ok": False}


def _acc_usage(acc: Dict[str, int], one) -> None:
    """把一次调用的 `usage` 累加进这一轮的总量。

    ★ 一轮里可能调**多次**模型（工具往返）⇒ 必须累加，不能只取最后一次：
      只取最后一次会低估这一轮真实花的量，而"花了多少"正是要看的东西。
    ★ 服务端字段可能缺（有的实现不给 `usage`）⇒ 缺就当 0，不许因此报错。
    """
    if not isinstance(one, dict):
        return
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            acc[k] = int(acc.get(k, 0)) + int(one.get(k) or 0)
        except (TypeError, ValueError):
            pass


def _layer_count(hits) -> Dict[str, int]:
    # ⚠️ 这里的键含**档位名**（`中期` / `悬着`），不只是池子的层 —— 见 `pool.TIER_MID` / `pool.TIER_OPEN`。
    #   它们是临时池内部的档，**不是** `LAYERS` 里的层（那四个键没动）。
    #   ★ 2026-10-06 把 `悬着` 补进声明（此前只在 `.get` 里被动新增，形状声明是过期的）。
    out = {"底色": 0, "情境": 0, "记忆": 0, "临时": 0, "中期": 0, "悬着": 0}
    for h in hits:
        out[h.layer] = out.get(h.layer, 0) + 1
    return out
