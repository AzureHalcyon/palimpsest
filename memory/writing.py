# -*- coding: utf-8 -*-
"""编写 `[W]` —— 把"这一刻取用的几件事"整理成**一段回忆**（第一人称），交给底座模型。

═══════════════════════════════════════════════════════════════════════
作者口径（2026-10-17）：**「原来的条目变成一段」** · **「编写的时候，把原文和节点强度给编写模型」**
　　　　　　　　　　　　　　**「加一个能点进去的按钮，看到用哪些条目、怎么编写」**
　　　　　　　　　　　　　　**「这个编写也存」**
═══════════════════════════════════════════════════════════════════════

## 这一步在流水线里的位置

    取件（[R]，只提候选/排序） → **[W] 编写** → 一段文字 → 进上下文
                              ↓
                          落 `说/`（模型生成过的东西必须留档：温度 >0 ⇒ 重跑拿不到同一份）

## 三条纪律（都不是猜的，见 `docs/流程框架.md §六`）

| 纪律 | 为什么 |
|---|---|
| **不给情境**（不把她这一轮听到的那句话喂进来） | 实测：给了 ⇒ 它把线索原样抄进输出 |
| **不授权丢件**（"每件都要说到，一件都不许省"） | 实测：写了"用不上就别写" ⇒ 4 个件只用了 2 个 |
| **不带编号**（输入用 `·`，输出里不许出现序号/分条） | 实测：带编号 ⇒ 出口变成清单（编号是内部结构） |

★ **强度要给**（作者口径）：每行前面带一个 0–1 的小数，**只作"先说哪件"的提示**，
  并且明令 ⛔ 不许把它写进回忆 —— 给了数却不许露出，这是"给模型的读数"而不是"给人看的记录"。
  （界线沿用 `开工清单 §5` 第 6 条：**给人看的记录不许变成喂给模型的东西**；反过来，
   这里给模型的是**取用本身的读数**，不是记录层的编号。）
"""
import io
import json
import os
import time
from typing import Dict, List, Optional

# ⛔ 这几句是**逐字定过的**，改动要么有实测依据，要么先问作者（见上面的对照表）
编写提示词 = """下面是你记忆里被取用的 {件数} 件事（都是你自己说过的话）。
每行前面的小数是它此刻在你心里的分量（0 到 1）：只用来决定哪件先说、说得多一点，不要把它写出来。

{条目}

请把它们整理成【你的回忆】—— 第一人称，像你在回想。
· 每一件都要说到，一件都不许省；
· 每件事各自分明，不要揉成一件；
· 用原文里的说法，不加解释、不总结；
· 不要把编号、序号、分条带出来（写成回忆，不是列清单）；
· 不要提「取用」「记忆」「槽位」「分量」这些词。
★ 用中文想、也用中文写（连推理过程也写中文）。"""

# 模型说"没有"的几种常见形态（它不该编，所以这个分支得留）
没有的说法 = ("没有", "没有。", "无", "无。", "（没有）", "(没有)", "没有要回忆的", "没有要回忆的。")


def 条目文本(件们: List[Dict]) -> str:
    """一件事一行：`<强度> · <原文>`。强度缺省就不写那个数（⛔ 不编一个）。"""
    行 = []
    for it in 件们:
        文本 = str(it.get("文本") or it.get("text") or "").strip()
        if not 文本:
            continue
        强度 = it.get("强度", it.get("score"))
        if isinstance(强度, (int, float)):
            行.append("%.2f · %s" % (float(强度), 文本))
        else:
            行.append("· " + 文本)
    return "\n".join(行)


def 编写(llm, 件们: List[Dict], 场id: str = "", 轮: int = 0, 线索: str = "",
         温度: Optional[float] = None) -> Dict:
    """把件写成一段回忆。

    返回：`{"ok", "编写", "没有", "耗时ms", "提示词哈希", "版本"}`。
    ⛔ **失败不许抛**：编写挂了，这一轮顶多"没想起什么"，不能把对话弄断
      （`ok=False` 时调用方决定是退回条目清单还是干脆不注入）。
    """
    from .film import 提示词哈希
    t0 = time.time()
    件们 = [x for x in (件们 or []) if str(x.get("文本") or x.get("text") or "").strip()]
    if not 件们:
        return {"ok": True, "编写": "", "没有": True, "耗时ms": 0,
                "提示词哈希": "", "版本": {}, "件数": 0}
    提示 = 编写提示词.format(件数=len(件们), 条目=条目文本(件们))
    try:
        消息 = [{"role": "user", "content": 提示}]
        # ★ 要 logprobs：编写也是**模型生成过的东西**，它的 token 概率一样不可再生成。
        msg = llm.chat(消息, temp=(0.7 if 温度 is None else 温度), logprobs=True,
                    fast=True)   # ★ 关思考：编写只要一段话，不需要那 900 个推理 token
        文本 = str(msg.get("content") or "").strip()
        # ★ 「全流程全记录」：这一次编写调用也要能落进那一轮的 调用[]
        调用 = dict(msg.get("_call") or {})
        调用.update({"用途": "编写", "请求": 消息, "请求条数": len(消息),
                     "usage": dict(getattr(llm, "last_usage", None) or {}),
                    "输出": {"content": 文本,
                            "reasoning": str(msg.get("reasoning_content") or ""),
                            "tool_calls": []},
                    "logprobs": msg.get("logprobs") or {}})
        if not 文本:
            # ★ 推理模型的老坑：`max_tokens` 被推理吃光 ⇒ 正文空。
            #   那不是"没有要回忆的"，是**这次没写成** ⇒ 如实报失败，让调用方退回去。
            return {"ok": False, "编写": "", "没有": False, "耗时ms": int((time.time() - t0) * 1000),
                    "提示词哈希": 提示词哈希(提示), "版本": {}, "件数": len(件们),
                    "调用": 调用, "err": "正文是空的（可能被推理吃光）"}
        没有 = 文本.strip(" \n。.!！") in [x.strip(" \n。.!！") for x in 没有的说法]
        out = {"ok": True, "编写": "" if 没有 else 文本, "没有": 没有, "调用": 调用,
               "耗时ms": int((time.time() - t0) * 1000),
               "提示词哈希": 提示词哈希(提示), "件数": len(件们),
               "版本": {"提示词哈希": 提示词哈希(提示), "模型": 模型名(llm)}}
        return out
    except Exception as e:                           # noqa: BLE001
        return {"ok": False, "编写": "", "没有": False, "耗时ms": int((time.time() - t0) * 1000),
                "提示词哈希": "", "版本": {}, "件数": len(件们),
                "err": "%s: %s" % (type(e).__name__, e)}


def 模型名(llm) -> str:
    前 = getattr(llm, "cfg", None) or getattr(llm, "cfg", None)
    return str(getattr(前, "model", "") or "")


def 记说(cache_dir: str, 场id: str, 轮: int, 线索: str, 件们: List[Dict],
        结果: Dict) -> None:
    """落 `<cache>/说/<场id>.jsonl` —— **编写这一步的输出必须留档**。

    为什么：温度 >0 ⇒ 重跑拿不到同一份；而没有它，"改编写提示词前后"就没法比。
    """
    try:
        os.makedirs(os.path.join(cache_dir, "说"), exist_ok=True)
        行 = {"场id": str(场id), "轮": int(轮), "线索": str(线索 or ""),
              "给了哪些": [{"件id": it.get("件id"), "文本": str(it.get("文本") or it.get("text") or ""),
                            "强度": it.get("强度", it.get("score"))} for it in (件们 or [])],
              "编写": 结果.get("编写") or "",
              "没有": bool(结果.get("没有")),
              "编写版本": dict(结果.get("版本") or {}),
              "耗时ms": int(结果.get("耗时ms") or 0),
              "时间": time.time()}
        with io.open(os.path.join(cache_dir, "说", 场id + ".jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(行, ensure_ascii=False) + "\n")
    except Exception:                                # noqa: BLE001
        pass           # ⛔ 记数据失败不许打断对话
