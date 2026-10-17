# -*- coding: utf-8 -*-
"""归档（= dreaming）：**一段它自己待着的对话**，不是"一个固定提示一次性吐 JSON"。

流程：
    带处境 + 任务说清 + 可以调 `search_memory` 去池子里查（**可查多次**），查够了再产出
    `facts / kept / again / done / promote`
    —— 模型**自己挑、自己排序、自己打 tag、自己决定升迁**。

★★ **2026-10-06：收口权也交给它自己**（`cfg.require_deliver`）。
   原来"跑满回合就强制收口"（`给出最终 JSON（不要再查了）`）⇒ **"改干净了没有"没机会被检查**
   —— 实测两次三场模拟里，都留下了没改净的旧说法。
   现在它可以写 `"deliver": false` + `"todo"`，表示"还没弄干净" ⇒ **带着它自己的 `todo` 再来一轮**；
   只有它写 `deliver: true`（或缺省）才算交付。回合数只是**硬护栏**（`dream.turns`）。
   ⚠️ 防打转：连着几轮 `todo` 一模一样 ⇒ 收口并标 `stuck`（记账里看得见，不许静默）。

【实测】拆成"对话式归档"之后 **8/8 场成功**；此前"一次性吐 JSON"的写法只有 **5/8**。

★ **归档失败 = 这一场的记忆整段丢掉**（实测 8 场里 3 场解析失败：空回复 / 字符串里有裸换行）
  ⇒ 这里必须有**修 JSON 回路**：
      解析失败 ⇒ 把原文和错误回给模型，让它只重出 JSON；
      回合用尽 ⇒ **不给工具、强制 JSON 模式**再试 `json_mode_retries` 次；
      仍失败   ⇒ **抛异常**，由调用方标成**可见的失败**（⛔ 不许静默吞掉）。
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import DreamConfig
from .llm import parse_json
from .prompts import DREAM_SYS, DREAM_TOOLS


def build_dream_input(session_turns: Sequence[dict], temp_lines: Sequence[str]) -> str:
    """把这一场的逐轮记录 + 当前"暂时的"清单拼成一段给归档看的文本。"""
    lines = ["【这场对话】"]
    for i, t in enumerate(session_turns):
        lines.append("[%d] 用户: %s" % (i + 1, str(t.get("user", "")).replace("\n", " ")))
        lines.append("[%d] 它  : %s" % (i + 1, str(t.get("ai", "")).replace("\n", " ")))
    lines.append("")
    lines.append('【现在挂着的"暂时的"记忆】')
    lines.extend(list(temp_lines) or ["（无）"])
    return "\n".join(lines)


def dream(llm, session_turns: Sequence[dict], temp_lines: Sequence[str],
          search: Callable[[str], List[str]], cfg: Optional[DreamConfig] = None,
          max_turns: Optional[int] = None,
          tool: Optional[Callable[[str, dict], List[str]]] = None,
          progress=None) -> Tuple[dict, int, int]:
    """跑归档对话。返回 `(归档对象, 用了几回合, 修了几次 JSON)`。

    `search` = **按意思**查池子（`search_memory`）。
    `tool` = **其余工具的**分发器：`tool(名字, 参数) -> List[str]`（`find_text` /
    `read_entry` / `list_entries` / `pool_stats`）。由调用方注入 —— 离线自检不必都造。
    ★ 做成**一个分发器**而不是每个工具一个入参：以后再添原语，这里的签名不用动
      （用户点出来的方法论：**给原语，别补丁式地加**）。
    ⛔ `tool` 缺省时，除 `search_memory` 外的调用一律退回 `search`（不许因此抛异常）。
    """
    cfg = cfg or DreamConfig()
    max_turns = int(max_turns or cfg.turns)

    msgs: List[dict] = [{"role": "system", "content": DREAM_SYS},
                        {"role": "user", "content": build_dream_input(session_turns, temp_lines)}]
    last, repair = "", 0
    # ★★ 2026-10-06：**交付判定权归模型**（`cfg.require_deliver`）。
    #   它写 `deliver:false` + `todo` ⇒ 不交付，带着自己的 `todo` 再来一轮（可继续查、继续改）。
    #   `last_todo`/`stuck` 是**防原地打转**的护栏：连着两轮 `todo` 一样 ⇒ 收口并在记账里标出来。
    last_todo, stuck = "", 0

    def _deliver_or_continue(ar: dict, r: int):
        """返回 `(要交付的归档对象, 用了几回合)`；返回 `None` 表示**继续**。"""
        nonlocal last_todo, stuck
        if not cfg.require_deliver or ar.get("deliver") is not False:
            ar["delivered"] = True
            ar["rounds_used"] = r + 1
            return ar, r + 1, repair
        todo = str(ar.get("todo") or "").strip()
        stuck = stuck + 1 if todo and todo == last_todo else 0
        last_todo = todo
        if stuck >= 2:                      # 连着三轮说同一件事 ⇒ 它卡住了，别再烧
            ar["delivered"] = False
            ar["stuck"] = True
            ar["rounds_used"] = r + 1
            return ar, r + 1, repair
        # 把它的 `todo` 当下一轮的任务交回去（这就是"继续"）
        msgs.append({"role": "assistant", "content": last or "（继续）"})
        msgs.append({"role": "user", "content":
                     "你说还没交付，todo 是：%s\n"
                     "现在继续 —— **可以接着调 `search_memory` 查、可以改**；"
                     "弄定了就写 `deliver:true`，还没弄完就再写一条**更具体**的 `todo`。"
                     % (todo or "（你没写 todo —— 那就先想清楚到底差哪一条，再写清）")})
        return None

    for r in range(max_turns):
        # ★ 2026-10-07：每轮报一条 `phase`（界面上看得到"第几轮/共几轮"），
        #   并把 `progress` 透给 `chat` ⇒ 这一轮的 CoT 与正文**边收边显示**。
        if progress is not None:
            progress("phase", "归档：第 %d/%d 轮" % (r + 1, max_turns))
        msg = llm.chat(msgs, tools=DREAM_TOOLS, temp=cfg.temperature, progress=progress)
        calls = msg.get("tool_calls")
        if calls:
            msgs.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
            for c in calls:
                # ★ 2026-10-06：**按工具名分派**（原来写死了只认 `query` ⇒ 加第二个工具就会串）。
                #   `find_text` 是**正则找形态**（治"旧值还在别处出现"那类漏）；
                #   `read_entry` / `list_entries` / `pool_stats` 是**看**（读一条 / 列一层 / 看预算）。
                #   ⛔ 没注入 `tool`（离线自检常见）就退回 `search`，不许因此抛异常。
                nm = str(((c.get("function") or {}).get("name")) or "")
                if tool is not None and nm and nm != "search_memory":
                    got = tool(nm, _tool_args(c))
                else:
                    got = search(_tool_query(c))
                msgs.append({"role": "tool", "tool_call_id": c.get("id"),
                             "content": "；".join(got) if got else "（没查到）"})
            continue
        last = msg.get("content") or ""
        try:
            ar = parse_json(last)
        except Exception as e:  # noqa: BLE001
            repair += 1
            msgs.append({"role": "assistant", "content": last or "（空）"})
            msgs.append({"role": "user", "content":
                         "上面那条不是合法 JSON（%s）。**只输出一个 JSON 对象**："
                         "不要解释、不要 markdown 代码块、字符串里不要出现裸换行。" % e})
            continue
        got = _deliver_or_continue(ar, r)
        if got is not None:
            return got

    msgs.append({"role": "user", "content": "给出最终 JSON（不要再查了）。"})
    if progress is not None:
        progress("phase", "归档：收尾（要它给最终 JSON）")
    for i in range(cfg.json_mode_retries):
        msg = llm.chat(msgs, temp=0.2, json_mode=True, progress=progress)
        last = msg.get("content") or ""
        try:
            ar = parse_json(last)
            ar["delivered"] = False
            ar["cut_short"] = True              # ★ 回合用尽被截断（**不是**它自己说交付的）
            ar["rounds_used"] = max_turns
            return ar, max_turns, repair + i
        except Exception as e:  # noqa: BLE001
            msgs.append({"role": "assistant", "content": last or "（空）"})
            msgs.append({"role": "user", "content":
                         "这也不是合法 JSON（%s）。只输出 JSON 对象本身。" % e})
    raise ValueError("归档 %d 次都没给出合法 JSON；最后一条：%r"
                     % (cfg.json_mode_retries, last[:200]))


def empty_archive(note: str = "") -> Dict[str, list]:
    return {"facts": [], "kept": [], "again": [], "done": [], "promote": [], "note": note}


def _tool_query(call: dict) -> str:
    try:
        return json.loads((call.get("function") or {}).get("arguments") or "{}").get("query", "")
    except Exception:  # noqa: BLE001
        return ""


def _tool_args(call: dict) -> dict:
    """把工具调用的 `arguments` 解成 dict。⛔ 解不出来就给空 dict，**不抛**。"""
    try:
        d = json.loads((call.get("function") or {}).get("arguments") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}
