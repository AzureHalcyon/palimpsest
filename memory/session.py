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
# ⚠️ 2026-10-17：`push_block` 不再 import —— 注入只剩"编写那一段"一条路，
#    ⛔ 没有"退回条目清单"这回事了（原文不出读取层）。它在 `transcript.py` 里还在，
#    但**没有人再往她的上下文里铺原文**。
# ⚠️ [W] 编写 **不要**在模块级 import（2026-10-17 真机踩到）：
#    rom . import writing 会在 memory/__init__.py 还在跑的时候反过来把
#    memory.session 弄成"半初始化"⇒ 宿主 rom memory.session import Session 当场
#    ImportError: cannot import name 'Session' from partially initialized module。
#    ⇒ 改成**用的时候再 import**（与 ilm 同一个做法）。桌面复现不出来，只有真机报。


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
            "push_n": [],
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
            # ★★ 2026-10-17（审查抓到）：这里没传 `读法版本` ⇒ 永远落默认 0，
            #   而 `版本对` 写的是 `knobs.版本["读法版本"]`（1）⇒ **同一份归档两处说法不一致**。
            from .knobs import 版本 as _K版本
            self._film = 底片(self.场id,
                              指纹(self.cfg, (SYS1, SYS2),
                                  读法版本=int(_K版本["读法版本"]["值"])),
                              self.engine.cache_dir,
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
        # ★★ **取件源**（2026-10-17）：只有一条 —— `[R] 回忆`（件 / 标记集 / 传播）。
        #   ⛔ 旧路（池子 + 余弦 + softmax）已经整个删掉：`session.use_recall` 这个开关
        #      连同它的兜底分支一起没了（旧仓库里有完整的那一份）。
        取用: List[dict] = []
        回忆为什么 = ""
        from . import recall as _回忆          # ★ 懒导入（见文件头那条注释）
        from .graph import 图 as _图
        # ★★ 2026-10-17（作者：「对照组实验是可以做一下的」）：**自动推送**是一个开关。
        #   关掉 ⇒ 这一轮不查也不编，全靠她自己调 `recall`（那才是 pull-only 那一臂）。
        #   两臂要能跑在同一份图上，才谈得上对照。
        自动推 = bool(getattr(self.cfg.session, "auto_push", True))
        为什么自动推 = ""
        if 自动推:
            g = _图.载入(eng.cache_dir)
            r = _回忆.回忆(user_text, g, os.path.join(eng.cache_dir, "底片"),
                          嵌入=getattr(eng.embedder, "raw_embed", None))
            回忆为什么 = str(r.get("为什么") or "")
        else:
            r = {"件": []}
            为什么自动推 = "（自动推送关着：这一轮不查记忆，等她自己去 recall）"
        masked = self._cd_masked(turn_no)
        blocked = 0            # ★ 必须在循环之前（它被循环里的 continue 累加）
        件们 = []
        for one in (r.get("件") or []):
            原文们 = [t for t in (one.get("原文们") or []) if t not in masked]
            强度们 = list(one.get("强度们") or [])
            if not 原文们:
                # ★★ 2026-10-17（审查抓到）：`blocked` 初始化之后**从来没累加过** ⇒
                #   `cd_blocked` 永远是 0，而它正是"开/关冷却那次对照"的读数
                #   ⇒ 对照实验会得出"冷却没挡任何东西"的错误结论。
                blocked += len(one.get("原文们") or [])
                continue        # 这一件全在冷却期里 ⇒ 不注入（⛔ 不找替补，沿用旧规）
            for n, 文本 in enumerate(原文们):
                # ★ 每段带**自己的**强度（该格的起点，∈[0,1]），⛔ 一件里所有段同一个数
                强 = (强度们[n] if n < len(强度们)
                      else float(one.get("分数") or 0.0))
                件们.append({"文本": 文本, "强度": float(强)})
            取用.append({"件id": one["件id"], "分数": float(one.get("分数") or 0.0),
                         "强度": float(one.get("强度") or 0.0)})
        shown = [x["文本"] for x in 件们]
        turn = self.msgs + [{"role": "user", "content": user_text}]
        # ★★ **注入从"条目清单"改成"一段回忆"**（2026-10-17 作者口径：
        #   「原来的条目变成一段」·「编写的时候，把原文和节点强度给编写模型」）。
        #   取件（`shown`）不变 —— 变的是"给她看什么"：先 `[W] 编写` 成一段，再注入。
        # ★★★ 2026-10-17（作者："反正不该直接透露原文，原文信息密度肯定还是没有编写的高"）：
        #   **她永远不该看见原文**。所以原来那条"编写挂了 ⇒ 退回条目清单"的退路**删掉了** ——
        #   那是把 `他：…/她：…` 的生料直接塞进她的上下文：既破人称，密度也不如编写。
        #   ⇒ 编写失败就**这一轮不注入**，把原因写进 `注入` 与 `回忆.err`（大声留痕，⛔ 静默降级）。
        #   ⚠️ 只留一个**调试用**的开关 `session.render_recall=False`（对照用，默认开）。
        回忆: dict = {}
        # ★★★ 2026-10-17（作者指出）：「一条都没注入」有**几种**完全不同的原因，
        #   原来它们全都记成 `"空"` ⇒ 我上一份汇报就把「这一段两轮前给过、还在 `msgs` 里」
        #   误读成「编写没写成」。⛔ 混成一个值就会造出**假故障**（也会盖住真故障）。
        注入方式 = ("空（关了自动推送）" if not 自动推
                    else ("空（没取到件）" if not blocked else "空（都在冷却期）"))
        if shown:
            注入方式 = "空（编写说没有）"     # 只有走到「编写说没有」才留这个
            from . import writing          # ★ 懒导入（见文件头那条注释）
            if bool(self.cfg.session.render_recall):
                回忆 = writing.编写(eng.llm, 件们, 场id=self.场id,
                                    轮=turn_no, 线索=user_text)
                writing.记说(eng.cache_dir, self.场id, turn_no, user_text, 件们, 回忆)
                if 回忆.get("编写"):
                    注入方式 = "编写"
                    turn.append({"role": "system",
                                 "content": self.cfg.session.push_header + "\n" + 回忆["编写"]})
                else:
                    注入方式 = "空（编写失败）"
                # 模型说"没有" ⇒ 这一轮不注入（由它自己判断"此刻想不起什么"）
            else:
                # ★★★ 2026-10-17（作者："反正不该直接透露原文"）：关掉编写 = **这一轮不注入**。
                #   原来这条会退回**条目清单**（原文生料直接进她的上下文）—— 那是"原文出读取层"的
                #   最后一条缝，堵掉。这个开关现在只剩"对照/排查"一个用途。
                注入方式 = "空（关了注入）"
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
                    text, rec_one = _dispatch(eng, c, now, self)
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
                "push_items": shown, "push_n": len(shown),
                # ★ 这一轮**取到几件**（`取用` 里带件id与分数）+ 一共注入了多少条原文。
                #   件层没有"读取器 k"那种东西了（旧池子已删），别再造一个编出来的数。
                "件数": len(取用),
                # ★ 被**冷却掩膜**挡掉几条（刚说过的原文不再注入）——
                #   开 / 关冷却那次对照的读数，别省。
                "cd_blocked": blocked,
                # 这一轮花了多少 token（服务端给的），以及模型自己"再查一次"的痕迹。
                # ★ 新路：这一轮**取用了哪些件**（旧路为空）+ 没取到的原因（一句话）
                "取用": 取用, "回忆为什么": (回忆为什么 + 为什么自动推),
                # ★★★ 2026-10-17（作者口径）：**整个激活分布**（⛔ 只前 k）。
                #   归档时按它强化已有件 —— 那才是「**提得多 ⇒ 记得牢**」（REM-SWS 的 repetition）。
                #   ★ 而"取用"只记前 k，是给注入用的；**强化要看全场**。
                "激活": dict(r.get("激活") or {}),  # ★ r（recall），⛔ 回忆（编写）
                # ★★ 界面要的两样：**编写出的那一段** + 「怎么编的」（用了哪几条 / 版本 / 耗时）。
                #   ⛔ 只给用户看；`msgs` 里只有那段文字本身（不进读取路径）。
                "回忆": ({"编写": 回忆.get("编写") or "",
                          "没有": bool(回忆.get("没有")),
                          # ★ 失败要**留痕**（现在编写失败不再退回原文清单，只剩下这一格能说明为什么）
                          "err": str(回忆.get("err") or ""),
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
            # ★★ 2026-10-17：**底片里记的必须是真的**（它是存档，作者口径「全流程全记录」）。
            #   这里原来写的是旧池那一套（`形状: 旧池（无件层）` + `k/k_raw` + `gate_tau` +
            #   `旧池取用`），而这一版取件走 `[R] 回忆`（件 / 标记集）⇒ 再那么写就是记假账。
            f.加轮(turn_no, user_text, reply, 调用=全流程,
                   取用=(取用 or None),             # 件id + 分数（这一轮真正取到的件）
                   # ★★★ 整个激活分布（⛔ 只前 k）—— 归档时按它强化已有件（重提 / repetition）。
                   #   ⚠️ 这一格 2026-10-17 漏过：rec["turns"] 里有，但 加轮() 没收 ⇒
                   #   底片里恒缺键 ⇒「重提强化」是死代码（场 5 实测：16 个件 conf 全降）。
                   激活=dict(r.get("激活") or {}),  # ★★ 从 **r**（recall 的结果）读，⛔ 从 回忆（**编写**的结果）
                   参数={"形状": "件层",
                         "件数": len(取用),
                         "原文条数": len(shown),
                         # 回忆那一份参数账（k / α / β / γ / T）原样抄进来 ⇒ 事后能重建
                         "回忆参数": dict(r.get("参数") or {}),  # ★ 同上：r 才是 recall
                         "cd_blocked": int(blocked),
                         "注入": 注入方式,
                         "编写耗时ms": int(回忆.get("耗时ms") or 0),
                         "编写提示词哈希": str(回忆.get("提示词哈希") or "")},
                   成本=dict(usage))
            f.落盘()
        except Exception:                                # noqa: BLE001
            pass

        return {"reply": reply, "push": shown,
                "k": len(取用),                  # 这一轮用了几件
                "cd_blocked": blocked, "pulls": pulls, "usage": usage,
                # ★★ 「那段回忆」（编写原文 + 用了哪几条 + 版本/耗时）—— 界面点开要看的就是它
                "回忆": 回忆 or None,
                # ★ 它自己排的唤醒 / 调过的渠道也回给宿主（界面与转写要看"它做过什么"）
                "wakeups": wake_calls}

    # ------------------------------------------------------------------ 撤回
    def 记住这一轮(self, 为什么: str = "") -> dict:
        """★★ **用户说「这条记住」** ⇒ 给**这一轮**打 `手工` 标记（⛔ 过阈值）。

        ★★★ 2026-10-17（作者定：「她自报的给普通，当兜底用」）：**永久档必须由用户触发** ——
          否则"永不剪出"就成了她自己说了算。而在这之前，全仓**只有她那一条路**
          （`remember` 工具，`谁="她"`）⇒ `手工` 这一档**没有任何生产者**（等于不存在）。
        ⇒ 这是用户那一侧的唯一入口（宿主：`host_glue.chat_remember`）。
        ⚠️ `轮` ＝ **最近一轮**（`turns` 的最后一条）；还没有任何一轮时返回 `err`，⛔ 瞎标。
        """
        turns = list(self.rec.get("turns") or [])
        if not turns:
            return {"err": "还没有一轮可标"}
        轮 = int(len(turns))
        self._底片().手工记(轮, 谁="用户", 为什么=str(为什么 or ""))
        return {"轮": 轮, "谁": "用户"}

    def withdraw_last(self) -> dict:
        """★ **撤回最后一轮**（2026-10-06）：把这一轮**当成没发生过**，可连续用。

        用户口径：「撤回上一次对话，重新编辑 —— 就和 DeepSeek 网页一样的逻辑」。

        **撤的是什么**（四处一起退，缺一处就会前后不一致）：

        | # | 退什么 | 为什么要退 |
        |---|---|---|
        | ① | `msgs`（送进模型的那份上下文） | 不回退 ⇒ 它**记得那句被撤掉的话**，重发时等于说了两遍 |
        | ② | `turns[-1]`（轮次记录） | 归档与转写都按它算 ⇒ 退了它，**被撤的那一版不进记忆** |
        | ③ | 记账（`push_n` / `pulls`） | 都是逐轮累加的，不退就等于把没发生的算进去了 |
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
        """**一步到底**：出草案 → 立刻落地（给"不需要审"的场合：工具 / 回归）。

        ★ 2026-10-17 换新线：原来这里调 `engine.archive()`（那条路已删）。现在就是
          `archive_draft` + `archive_commit` 两下 —— 与界面那条"出草案 → 审 → 完成"
          走的是**同一份实现**，只是中间不给人插话的机会。
        """
        self.rec["ts_end"] = time.time()
        self.archive_draft(progress=progress)
        return self.archive_commit(transcript=transcript, progress=progress)

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
        # ★★★ **⛔ 手工拼这个 dict** —— 走 `f.导出()`。
        #   2026-10-17 真对话抓到两次同一个病：这里手工拼，漏过 `其他底片`（⇒ 候选全空、
        #   「归进」整条死路）、漏过 `手工标记`（⇒ 用户说"记住"在常规归档里不生效）。
        #   少写一格**不会报错**，它只会静默地不工作。
        底片数据 = f.导出()
        g = 图.载入(self.engine.cache_dir)
        # ★★★ 2026-10-17 真对话第 3 场抓到的真 bug：这里原来**没传 `其他底片`**。
        #   而 `_候选行()` 要靠 `底片们.get(c["场id"])` 取候选件的**代表原文** ——
        #   别的场次的底片不在手里 ⇒ 文本渲染成空 ⇒ 候选行成了 `件-0001（像 0.7x）：「」`
        #   ⇒ 模型看到的是"**空标题的件**" ⇒ 只能新建 ⇒ **"归进"这条路从来就没通过**
        #   （三场 19 处标记，一次"归进"都没有）。
        #   ⇒ 归档前把 `<cache>/底片/` 下的底片都读进来（图是缓存，底片才是存档）。
        #   ⚠️ 这一版是"全读"：场次多了会慢，将来可只读候选涉及的那几场。
        # ★★★ 2026-10-17（"几十场还顶不顶得住"）：这里原来是**全读** `<cache>/底片/` ——
        #   只为拿"候选件代表 cell 所在那一场"的原文，而场次线性涨 ⇒ **每归档一次就
        #   O(全部历史) 解压一遍**。改成按需读（`archive_step.底片表`：问哪场读哪场、读不到就记下）。
        其他底片 = archive_step.底片表(os.path.join(self.engine.cache_dir, "底片"))
        草 = archive_step.草案(底片数据, g, self.engine.llm, f.场id,
                              嵌入=getattr(self.engine.embedder, "raw_embed", None),
                              其他底片=其他底片,
                              # ★★★ 2026-10-17（作者定）：「直接就是对话完之后的状态，
                              #   fork 一份接着问就可以」⇒ `⑤` **接着这一场的对话**问，
                              #   ⛔ 由我们把整场重新渲染一遍塞进提示词。
                              对话=list(self.msgs),
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
        # ★★★ 2026-10-17（审查抓到）：`完成()` 的 `err` **原来没人看** —— 它没写图，
        #   这里照样收尾（场次 +1 / pending 清 / 转写写一行）⇒ **这一场就再也不会被归档**，
        #   而界面上是"归档完成"。⇒ 有 `err` 就**停在审阅态**：草案留着、pending 不动，
        #   让人再按一次（`完成` 自己有幂等凭据，重复按不会重复建件）。
        if res.get("err"):
            self.rec["archive_draft"] = d               # 原样留着，⛔ 不动
            return {"ok": False, "land": "failed", "err": str(res.get("err")),
                    "新线": {"件": [x.get("件") for x in (res.get("层1") or [])]},
                    "提示": "这一场没归档（图没写成）。草案还在，可以再按一次「完成」。"}
        # ★★★ 2026-10-17（作者指出："转写里写的是 land=no，屏幕上是 land=yes"）：
        #   这里原来写 `{"land": "no"}` —— 那是**借旧线"幂等命中 ⇒ 只收尾"**那条路的判据，
        #   于是转写里「已经落地」和「本来就没落地」是**同一个值**。⇒ 分不清。
        #   现在四种状态各有一个值，转写一行就看得懂这一场到底走到哪一步：
        #     `yes`   草案已出、**在等你按完成**（池子一字未动）
        #     `done`  **已落地**（图写了、版本 +1），正在收尾
        #     `failed` 图没写成（草案留着，可以再按一次）
        #     `no`    旧版遗留（等价于"已落地/幂等"），只为读旧数据保留
        self.rec["archive_draft"] = {"land": "done"}
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
        或已经落地/幂等命中（`land="done"` / 旧数据的 `"no"`）⇒ 这两条老路本来就是为它们准备的。
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


def _回忆工具(eng, 线索: str, sess=None) -> Dict:
    """`recall` 工具走新线：`[R]` 取件 → **编写成一段** → 把那段回给它。

    ★★★ 2026-10-17（作者："反正不该直接透露原文，原文信息密度肯定还是没有编写的高"）：
      **这里原来把 `他：… / 她：…` 的原文整段回给它** —— 同一个功能、两种质地：
      推送那条是编写过的，自查这条却是生料（破人称、密度也低）。
      ⇒ 现在两条路**同一质地**：都只给她**编写过的那段**。⛔ 原文永远不出读取层。
    ⚠️ 代价：每次自查多一次 fast 调用（实测她自己查 = 245 轮里 22 次，很便宜）。
    ⚠️ 编写失败 / 它说"没有" ⇒ 回一句"这次没想起来"，⛔ 不回原文（宁可空手）。
    """
    from . import recall as _回忆
    from . import writing
    from .graph import 图 as _图
    g = _图.载入(eng.cache_dir)
    r = _回忆.回忆(线索, g, os.path.join(eng.cache_dir, "底片"),
                  嵌入=getattr(eng.embedder, "raw_embed", None))
    件们: List[Dict] = []
    for one in (r.get("件") or []):
        原文们 = list(one.get("原文们") or [])
        强度们 = list(one.get("强度们") or [])
        for n, 文本 in enumerate(原文们):
            件们.append({"文本": 文本,
                         "强度": float(强度们[n] if n < len(强度们)
                                       else (one.get("强度") or 0.0))})
    if not 件们:
        return {"回忆": "", "件数": 0, "为什么": "没取到件"}
    记得 = writing.编写(eng.llm, 件们,
                        场id=(getattr(sess, "场id", "") or ""),
                        轮=len((getattr(sess, "rec", {}) or {}).get("turns") or []) + 1,
                        线索=线索)
    if 记得.get("编写"):
        if sess is not None and getattr(sess, "engine", None) is not None:
            try:                                      # ★ 编写过的东西一律留档
                writing.记说(sess.engine.cache_dir,
                             getattr(sess, "场id", "") or "",
                             len((sess.rec or {}).get("turns") or []) + 1,
                             线索, 件们, 记得)
            except Exception:                        # noqa: BLE001
                pass
        return {"回忆": 记得["编写"], "件数": len(r.get("件") or []), "为什么": ""}
    # ⛔ 不回原文（宁可空手）：失败与"它说没有"要分开说，否则工具会回一句不实的话
    if 记得.get("ok") and 记得.get("没有"):
        return {"回忆": "", "件数": 0, "为什么": "编写说没有"}
    return {"回忆": "", "件数": len(r.get("件") or []), "为什么": "编写失败"}


def _dispatch(eng, call: dict, now: float, sess=None):
    """兜底包装：**任何一条分支炸了，都不许把这一轮对话炸掉**。

    ★★ 2026-10-17（审查抓到）：`_dispatch` 的说明写着"不许抛异常"，可它是**约定**、
      不是机制 —— 分支里任何一处没接住（`eng.wakeups` 为 None、参数类型不对、
      `markset` 抛）都会直接冒到 `ask`，**用户那一轮整个失败**。
      而这里恰恰是"她要靠结果改主意"的地方：失败必须变成**一句她能读懂的话**。
    ⇒ 真异常一律转成文本 + `None`（⛔ 假装做成：明确说"没跑成，别当已经做了"）。
      每一条分支自己该给的**具体理由**（"太近了，最短 15 分钟"）仍在 `_dispatch_内` 里给。
    """
    try:
        return _dispatch_内(eng, call, now, sess)
    except Exception as e:                           # noqa: BLE001
        name = str(((call or {}).get("function") or {}).get("name") or "?")
        return ("`%s` 这次没跑成（%s: %s）。⛔ 别当它已经做了 —— "
                "要做的话换个说法再试一次。" % (name, type(e).__name__, e), None)


def _dispatch_内(eng, call: dict, now: float, sess=None):
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

    # ★★ 2026-10-17（作者定）：**她的「记住」** —— 第三个标记来源。
    #   写进这一场底片的 `手工标记` ⇒ 归档时**无条件**进标记（⛔ 看误差大小）。
    #   `轮` = 已经记下的轮数 + 1（这一轮在 `ask` 里还没 append）。
    if name in ("记住", "remember"):
        为什么 = str(args.get("why") or args.get("为什么") or "").strip()
        if sess is None:
            return "这一轮标不上（没拿到会话），归档时按「意外」那条走。", None
        try:
            轮 = int(len(getattr(sess, "rec", {}).get("turns") or [])) + 1
            sess._底片().手工记(轮, 谁="她", 为什么=为什么)
        except Exception as e:                       # noqa: BLE001
            return "没记上（%s）。" % type(e).__name__, None
        return "记下了。归档时这一轮**无条件**进记忆，不看它意不意外。", None

    # ★ 2026-10-17 改名：`read_z` → `recall`（新线的叫法就是 `[R] 回忆`）。
    #   ⚠️ 旧名**留着当别名**：老 `pending.json` 的历史里有 `read_z` 的 tool 记录，
    #   恢复在途那一场时不该因为改名而认不出来。
    if name in ("recall", "read_z"):
        q = str(args.get("query") or "")
        # ★ 2026-10-17：她"自己再查一次"也走 **`[R] 回忆`**（与自动注入**同一条路**）。
        #   ⛔ 旧读取器那条兜底已经删掉（池子不存在了）。
        got = _回忆工具(eng, q, sess)
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
        # ★★★ 2026-10-17（作者："不该直接透露原文"）：回给它的**只有编写过的那一段**，
        #   ⛔ 不再是原文清单。所以下面判空看的是 `got["回忆"]`；而"编写失败"要单独说，
        #   否则会回一句不实的话（"没有贴得上的"，其实取到了、只是没编出来）。
        if str(got.get("为什么") or "") == "编写失败":
            return ("（这条查过了：取到了几段，但**没整理出来**（编写那一步失败了）。\n"
                    "· 别当成「查到了」：我手里没有可以照着说的东西；\n"
                    "· 别当成「没有这回事」。\n"
                    "真要用到这一块，就照底色的写法说：这个我还不知道。）",
                    {"kind": "pull", "query": q, "got": [], "empty": True,
                     "件数": int(got.get("件数") or 0), "err": "编写失败"})
        if not got.get("回忆"):
            return ("（这条查过了：池子里没有哪一条够得上「贴得上」那条线，最靠前的也没够上"
                    " —— 也就是说，没有贴得上的东西。\n"
                    "· 别当成「查到了」：不许拿手里现有的条目当成他的意思；\n"
                    "· 别当成「没有这回事」：这只是「没有贴得上的」，不等于事实不存在；\n"
                    "· 别让他以为我以前知道过这件事。\n"
                    "真要用到这一块，就照底色的写法说：这个我还不知道。）",
                    {"kind": "pull", "query": q, "got": [], "empty": True,
                     "件数": int(got.get("件数") or 0)})
        return (str(got["回忆"]),
                {"kind": "pull", "query": q, "got": [str(got["回忆"])], "empty": False,
                 "件数": int(got.get("件数") or 0)})

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
            # ★★ 2026-10-17（审查抓到）：`list()` 明明带 `id`，这里**却没写进回复文本** ——
            #   而 `cancel_wakeup` 的说明写着"id 从 list_wakeups 拿" ⇒ 这对工具是**断的**：
            #   模型只能瞎猜 id，撤销大概率回"没有这条"。**而且不报错。**
            txt = "挂着 %d 条：" % len(items) + "；".join(
                "%s → %.0f 分钟后（%s）" % (
                    it.get("id") or "?", it.get("in_minutes") or 0,
                    it.get("why") or "没说为什么")
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


