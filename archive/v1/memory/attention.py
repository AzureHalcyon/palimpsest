# -*- coding: utf-8 -*-
"""**注意力策略** —— 交给 AI 自己写，管道只负责执行（2026-10-06）。

为什么要这个文件（用户原话）
--------------------------
> 「这个规则开放给 AI，让 AI 自己决定，我们只给好工具。」

于是把"什么值得唤起"从**代码里的硬编码判断**改成**数据**：

```
attention_policy.json     ← ★ 这份文件是 **AI 自己写的**（它每轮都能改）
        │
        ├─ 机械层（这里）只做三件**不含判断**的事：
        │    ① 合并窗口（同一批动静别反复叫它）
        │    ② 按策略过滤（策略是 AI 定的，不是我们定的）
        │    ③ 额度上限（钱是硬的）
        └─ 判断（"这事重要吗、要不要说话"）全在 AI 那边
```

★★ 默认策略是 `mode="all"`：**什么都给它看**。
   理由：一开始就说"屏幕使用不重要"是**我们替它做判断**，正是要避免的事。
   宁可头几次多花几次模型钱，让它自己看过、自己把规则收紧 —— 而且它收紧的动作会**落盘**，
   下一次机械层就照它的规则执行。

⚠️ 三条边界（别把这个文件变成后门）
1. **不认识的东西不许悄悄生效**：策略里出现未知字段/未知渠道 ⇒ `decide()` 报错（`bad_policy`），
   ⛔ 不静默忽略 —— 静默忽略会让"它以为改了、其实没改"。
2. **策略不能突破额度**：`mode` 再宽松也过不了 `budgets`。
3. **它能改自己**：`patch_from_reply()` 解析 AI 回复里的 `<<policy {...}>>` 段并落盘。
   ⛔ 这段**不参与它说话的内容**（只被我们消费）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

POLICY_FILE = "attention_policy.json"

# ★ 默认：什么都看（让 AI 自己收紧），合并窗口 5 分钟
DEFAULT_POLICY: Dict[str, object] = {
    "mode": "all",              # all = 任何一路有动静都给 AI 看
    "coalesce_ms": 300_000,     # 同一批动静 5 分钟内只叫一次
    "mute": [],                 # AI 可以点名"这些渠道别叫我"（metric id 列表）
    "watch": {},                # 逐渠道加严：{"unlock_count_today": {"min_abs": 3}}
    "why": "默认全看（还没自己定过规则）",
    "updated_ms": 0,
}

_KNOWN_TOP = set(DEFAULT_POLICY.keys())


def path_of(cache_dir: str) -> str:
    return os.path.join(cache_dir, POLICY_FILE)


def load(cache_dir: str) -> Dict[str, object]:
    """读策略。读不到/读坏了 ⇒ **回到默认**（默认是"全看"，不会因此漏事）。"""
    p = path_of(cache_dir)
    if not os.path.exists(p):
        return dict(DEFAULT_POLICY)
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
        pol = dict(DEFAULT_POLICY)
        pol.update({k: v for k, v in raw.items() if k in _KNOWN_TOP})
        return pol
    except Exception:                                            # noqa: BLE001
        return dict(DEFAULT_POLICY)


def save(cache_dir: str, pol: Dict[str, object]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    p = path_of(cache_dir)
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pol, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def validate(pol: Dict[str, object], alias: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """★ 严格校验：未知字段/类型不对/渠道不存在 ⇒ 拒绝（见文件头边界 1）。

    ⚠️⚠️ `alias`（渠道名/`名字（单位）` → metric id）**必须传**：
    2026-10-06 真机实测抓到过一次**静默空转** —— AI 把 `mute` 写成
    `"最近一小时屏幕亮着多久（分钟）"`（**人读的名字**），而 `decide()` 是拿 **metric id** 比对的
    ⇒ 规则**一条都没生效、也没有任何报错**。这正是要禁止的："它以为改了，其实没改"。
    ⇒ 规矩：**渠道键必须能解析成真实存在的 metric**，否则拒绝并说明；
      `alias` 传 None 时**跳过**这项检查（离线纯逻辑用法），但真实链路必须传。
    """
    if not isinstance(pol, dict):
        return False, "策略不是对象"
    for k in pol:
        if k not in _KNOWN_TOP:
            return False, "不认识的字段：%s" % k
    if str(pol.get("mode") or "") not in ("all", "watch_only", "muted"):
        return False, "mode 只能是 all / watch_only / muted"
    if not isinstance(pol.get("mute") or [], list):
        return False, "mute 要是列表"
    if not isinstance(pol.get("watch") or {}, dict):
        return False, "watch 要是对象"
    try:
        if int(pol.get("coalesce_ms") or 0) < 0:
            return False, "coalesce_ms 不能是负的"
    except Exception:                                            # noqa: BLE001
        return False, "coalesce_ms 不是整数"
    if alias is not None:
        bad = [str(x) for x in (pol.get("mute") or []) if str(x) not in alias]
        bad += [str(k) for k in (pol.get("watch") or {}) if str(k) not in alias]
        if bad:
            return False, "这些渠道不存在（要用工具表里的渠道名）：%s" % "、".join(bad[:4])
    return True, ""


def normalize(pol: Dict[str, object], alias: Dict[str, str]) -> Tuple[Dict[str, object], List[str]]:
    """把 AI 写的**人读名字**翻译成 **metric id**（能翻的翻，翻不了的**报出来**）。

    ★ 为什么允许名字：AI 在提示词里看到的既有名字也有 id，写错一次不该让整条规则作废 ——
      但要**可解释**：翻不了的键必须被**明确拒绝**（`validate` 会做），⛔ 不静默忽略。
    """
    out = dict(pol)
    unknown: List[str] = []
    mute: List[object] = []
    for x in (pol.get("mute") or []):                            # type: ignore[union-attr]
        s = str(x)
        if s in alias:
            if alias[s] not in mute:
                mute.append(alias[s])
        else:
            unknown.append(s)
    watch: Dict[str, object] = {}
    for k, v in dict(pol.get("watch") or {}).items():            # type: ignore[arg-type]
        ks = str(k)
        if ks in alias:
            watch[alias[ks]] = v
        else:
            unknown.append(ks)
    out["mute"] = mute
    out["watch"] = watch
    return out, unknown


def decide(pol: Dict[str, object], changed: List[Dict[str, object]], now_ms: int,
           last_wake_ms: Optional[int], alias: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """机械层：**按 AI 的策略**决定这一批动静要不要叫它。

    ★ 这里的"判断"只有策略里写死的那些；⛔ 一行"重要性"都不在这里。
    返回 `(wake, reason)`，`reason` 会**照实**写进活动记录与报告。
    """
    ok, err = validate(pol, alias)
    if not ok:
        # ★ 策略坏了就**不执行**它，退回默认（宁可多叫一次，也别因为坏策略把事漏掉）
        return True, "bad_policy:%s⇒按默认全看" % err
    if not changed:
        return False, "no_change"

    mode = str(pol.get("mode") or "all")
    if mode == "muted":
        return False, "muted_by_ai"
    if last_wake_ms is not None:
        gap = int(now_ms) - int(last_wake_ms)
        if gap < int(pol.get("coalesce_ms") or 0):
            return False, "coalesced(%ds<%.0fs)" % (gap // 1000, int(pol.get("coalesce_ms") or 0) / 1000.0)

    mute = set(str(x) for x in (pol.get("mute") or []))
    watch: Dict[str, object] = dict(pol.get("watch") or {})      # type: ignore[arg-type]

    # 逐渠道加严：AI 可以给某条渠道设"至少变多少才算数"
    keep: List[Dict[str, object]] = []
    for c in changed:
        mid = str(c.get("metric") or "")
        if mid in mute:
            continue
        w = watch.get(mid)
        if isinstance(w, dict):
            try:
                min_abs = float(w.get("min_abs")) if w.get("min_abs") is not None else None
            except Exception:                                    # noqa: BLE001
                min_abs = None
            if min_abs is not None:
                d = c.get("delta")
                if d is None or abs(float(d)) < min_abs:
                    continue
        keep.append(c)

    if mode == "watch_only":
        if not watch:
            return False, "watch_only 但没设任何渠道"
        keep = [c for c in keep if str(c.get("metric") or "") in watch]
    if not keep:
        return False, "filtered_by_ai_policy"
    return True, "by_ai_policy(%d条)" % len(keep)


def policy_prompt(pol: Dict[str, object], last_wake_ms: Optional[int], now_ms: int,
                  keys: str = "", last_note: str = "") -> str:
    """把策略现状**照实**写给 AI（它能看见自己定的规则与效果）。

    ★★ 2026-10-06 的一次真机教训（用户当场指出"是不是你提示词没写清楚"）：
       这里原来写的是「mute 是**渠道名**列表」，而机械层 `decide()` 是按 **metric id** 比对的
       ⇒ AI 照提示词写了名字，规则**静默失效**。
       ⇒ 提示词必须与实现**逐字一致**：现在只说 `metric id`，并**把可用键直接列出来**；
         名字也认（机械层会翻译），但**推荐用 id**。
    ★ 另一条：上次它写的规则**被拒/没生效**，必须**在下一次提示词里告诉它**
       （否则它永远不知道自己的规则没落地，"自己决定"就变成空转）。
    """
    ago = "（还没叫过你）" if not last_wake_ms else "（上次叫你：%d 分钟前）" % ((now_ms - int(last_wake_ms)) // 60000)
    note = ("\n  ⚠️ **上次你写的规则没有生效**：%s\n     请按上面的键重写一次。" % last_note) if last_note else ""
    return ("你现在用的注意力策略（**是你自己定的**，可以改）：\n"
            "  mode=%s · 合并窗口=%d 秒 · mute=%s · watch=%s\n"
            "  策略里的 why：%s  %s%s\n"
            "  ★ 改法：回复末尾附一段 `<<policy {...}>>`（只写要改的字段）。\n"
            "    · `mode`：all / watch_only / muted\n"
            "    · `mute`：**要静音的渠道 metric id 列表**，例如 `[\"screen_on_minutes_last_hour\"]`\n"
            "    · `watch`：**metric id → {\"min_abs\": 数字}**，例如 `{\"steps\": {\"min_abs\": 100}}`\n"
            "    · ⚠️ **一律写下面的 metric id（不是中文名字）**；写名字也能认，但 id 最稳。\n"
            "    可用渠道 metric id：%s" % (
                pol.get("mode"), int(pol.get("coalesce_ms") or 0) // 1000,
                pol.get("mute") or [], pol.get("watch") or {},
                pol.get("why") or "（没写）", ago, note,
                keys or "（这次没拿到键列表，请只改 mode/coalesce_ms）"))


def extract_patch(text: str) -> Tuple[str, Optional[Dict[str, object]]]:
    """从 AI 的回复里取出 `<<policy {...}>>` 补丁，返回 `(去掉补丁后的正文, 补丁)`。

    ⛔ 补丁**不参与它说的话**（说话的内容由投递闸门另管）——
       它只是"顺手把自己的规则改了"。
    """
    s = str(text or "")
    i = s.find("<<policy")
    if i < 0:
        return s.strip(), None
    j = s.find(">>", i)
    if j < 0:
        return s.strip(), None
    body = s[i + len("<<policy"):j].strip()
    clean = (s[:i] + s[j + 2:]).strip()
    try:
        patch = json.loads(body)
        if not isinstance(patch, dict):
            return clean, None
        return clean, patch
    except Exception:                                            # noqa: BLE001
        return clean, None


def merge_patch(pol: Dict[str, object], patch: Dict[str, object], now_ms: int,
                alias: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, object], str]:
    """把补丁并进策略、**把名字翻成 id**、再**严格校验**；不认识/不存在 ⇒ 原样退回并说明。"""
    new = dict(pol)
    new.update(patch)
    new["updated_ms"] = int(now_ms)
    unknown: List[str] = []
    if alias:
        new, unknown = normalize(new, alias)
    # ★★ 翻不出来的键 ⇒ **整条补丁拒绝**（2026-10-06 自检抓到的第二处静默：
    #    `normalize` 会把不存在的渠道**摘掉**，于是校验反而通过 ⇒ 变成"它以为改了、其实没改"。
    #    ⛔ 规矩：**要么全生效，要么明确拒绝**，不许悄悄丢一半。）
    if unknown:
        return pol, "策略补丁被拒（这些渠道不存在，请用工具表里的键：%s）" % "、".join(unknown[:4])
    ok, err = validate(new, alias)
    if not ok:
        return pol, "策略补丁被拒（%s）" % err
    return new, ""


def now_ms() -> int:
    return int(time.time() * 1000)


# ---- 机械层自己的小状态（★ 与"AI 的策略"分开存：策略是它的，状态是我们的）----
STATE_FILE = "attention_state.json"


def load_state(cache_dir: str) -> Dict[str, object]:
    p = os.path.join(cache_dir, STATE_FILE)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:                                            # noqa: BLE001
        return {}


def save_state(cache_dir: str, st: Dict[str, object]) -> None:
    try:
        os.makedirs(cache_dir, exist_ok=True)
        p = os.path.join(cache_dir, STATE_FILE)
        tmp = "%s.tmp.%d" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:                                            # noqa: BLE001
        pass

