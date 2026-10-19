# -*- coding: utf-8 -*-
"""**L1：它自己的时间**（设计稿 §3.3）—— L0 说"有动静"之后，才轮到这一步。

★★ 2026-10-06 的一次**设计改动**（用户原话："这个规则开放给 AI，让 AI 自己决定，我们只给好工具"）
------------------------------------------------------------------------------------------
原来这里是硬编码的：`changed_n > 0` ⇒ 唤起模型。也就是说**"什么算事"由代码判断**。
现在拆成两层：

| 层 | 谁定 | 做什么 |
|---|---|---|
| **机械层**（`attention.decide`） | 我们 | 合并节流 · **按 AI 写的策略**过滤 · 额度上限 |
| **判断层**（模型） | ★ **AI 自己** | 这批动静算不算事 · 要不要说话 · **要不要改自己的策略** |

于是这里做的事：把**原料**给全（每条变化带"旧值→新值 + 这个数为什么会变"），
把它**自己的策略现状**也给它看，并允许它在回复里带一段 `<<policy {...}>>` **改自己的规则**。
⛔ 我们**一行"重要性"都不判断**。

★★ 三条仍然硬的规矩（这些是"管道"的职责，不是判断）
1. **先扣账再花钱**：额度拿不到 ⇒ 一次模型都不调，如实记进活动记录。
2. **一次**：一轮 L1 只唤起一次模型（不做多轮工具往返）。
3. **花不成要退**：调用抛异常 ⇒ `budgets.refund`。
"""
from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from . import attention
from .budgets import LLM
from .channels import ChannelCatalog, apply_l0

AskFn = Callable[[str], str]
SavePolicyFn = Callable[[Dict[str, object]], None]


def self_prompt(l0: Dict[str, object], policy: Dict[str, object],
                last_wake_ms: Optional[int], budget_left: Optional[int],
                last_policy_note: str = "") -> str:
    """给"它自己的时间"用的提示词。

    ★ 只说事实 + 给原料：**变化明细（含性质）** · 可查渠道现状 · 它自己的策略 · 今天还剩几次。
    ★ 不安排任务、不给步骤；也不要求"必须说话"（说不说话由投递闸门管）。
    ★★ 2026-10-06 补一条**关键的工具说明**：写规则时渠道要写**工具表里的渠道名（metric id）** ——
       实测它第一次写的是**人读的名字**，机械层按 id 比对 ⇒ 规则**静默失效**。
       现在两头都做：提示词里给可用键；机械层也接受名字并翻译，翻不出来的**明确拒绝**。
    """
    dig = l0.get("changed_digest") or []
    lines = []
    for d in dig:
        try:
            # ★ 必须带上 **metric id**：模型要"再查深一点"时，工具表里认的是 id（如 `accel`），
            #   只写人读的名字（"加速度"）它就没法把这个变化和工具对上（自检专门盯这条）。
            lines.append("· %s（%s）：%s → %s %s（性质：%s）" % (
                d.get("name"), d.get("metric"), d.get("prev"), d.get("now"),
                d.get("unit") or "", d.get("nature")))
        except Exception:                                        # noqa: BLE001
            lines.append("· %s" % d)
    detail = "\n".join(lines) or "（没有明细）"
    return ("（这是你自己待着的时候看到的东西，不是用户说的）\n"
            "几个渠道**有动静**，明细如下：\n%s\n\n"
            "%s\n可查的渠道现状：\n%s\n"
            "写规则时渠道一律用这些**键**（工具表里的名字）：%s\n"
            "今天模型唤起还剩 %s 次。\n\n"
            "你想到了什么？一句话就够；没什么可说的就空着。\n"
            "★ 如果你觉得**这类动静以后不值得叫你**，就把规则改掉"
            "（在回复末尾附一段 `<<policy ...>>`，只写要改的字段）。"
            % (detail,
               attention.policy_prompt(policy, last_wake_ms, attention.now_ms(),
                                       keys=str(l0.get("channel_keys") or ""),
                                       last_note=last_policy_note),
               l0.get("text") or "",
               l0.get("channel_keys") or "（这次没拿到键）",
               "不清楚" if budget_left is None else budget_left))


def run_once(*, catalog: ChannelCatalog, payload: Dict[str, object], now_ms: int,
             budgets, activity, ask_fn: Optional[AskFn] = None,
             policy: Optional[Dict[str, object]] = None,
             last_wake_ms: Optional[int] = None,
             save_policy: Optional[SavePolicyFn] = None,
             budget_left: Optional[int] = None,
             last_policy_note: str = "") -> Dict[str, object]:
    """跑一轮 L1。**任何失败都正常返回**（`ran: False` + `why`），不抛。

    ⚠️ **`apply_l0` 只在这里调一次**（2026-10-06 修的真 bug）：宿主那边曾经先调一次
       `apply_l0` 拿报告、再把同一个 payload 交给这里 ⇒ 这里再比一次必然"没变化"
       ⇒ **模型永远唤不起来**（真机联动测试才暴露；离线自检因为直接调这里所以看不见）。
       ⇒ 规矩：**一轮 tick 只比一次**，报告用这次的结果。
    """
    l0 = apply_l0(catalog, payload, now_ms)
    pol = dict(policy or attention.DEFAULT_POLICY)
    alias = catalog.alias_map()          # ★ AI 写的是名字还是 id，都要能对上（否则静默失效）

    # ★ 唤醒与否 = **照 AI 自己的策略**（机械层只执行，不做价值判断）
    wake, why = attention.decide(pol, l0.get("changed_digest") or [], now_ms, last_wake_ms, alias)
    if not wake:
        # ★ **不记活动**：一天几十次"没动静"会把记录淹掉（§activity 的约定 1）
        return {"ran": False, "why": why, "l0": l0, "policy": pol}

    if not budgets.take(LLM, now_ms=now_ms):
        activity.log("budget_out", now_ms=now_ms, changed=l0.get("changed"))
        return {"ran": False, "why": "budget", "l0": l0, "policy": pol}

    if ask_fn is None:
        budgets.refund(LLM, now_ms=now_ms)
        return {"ran": False, "why": "no_ask_fn", "l0": l0, "policy": pol}

    try:
        raw = str(ask_fn(self_prompt(l0, pol, last_wake_ms, budget_left, last_policy_note)) or "")
    except Exception as e:                                       # noqa: BLE001
        budgets.refund(LLM, now_ms=now_ms)                       # ★ 花不成要退
        activity.log("ask_failed", now_ms=now_ms, err="%s: %s" % (type(e).__name__, e))
        return {"ran": False, "why": "ask_failed", "err": str(e), "l0": l0, "policy": pol}

    # ★ 它顺手改了自己的规则？（`<<policy {...}>>` 段不参与"它说的话"）
    text, patch = attention.extract_patch(raw)
    pol_note = ""
    if patch:
        new_pol, err = attention.merge_patch(pol, patch, now_ms, alias)
        if err:
            pol_note = err
        else:
            pol = new_pol
            if save_policy:
                save_policy(pol)
            pol_note = "策略已更新：%s" % patch

    activity.log("looked", now_ms=now_ms, changed=l0.get("changed"),
                 wake_reason=why, policy_note=pol_note,
                 thought=text[:500], said=bool(text))
    return {"ran": True, "text": text, "l0": l0, "cost": 1,
            "policy": pol, "policy_note": pol_note, "wake_reason": why}
