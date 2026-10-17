# -*- coding: utf-8 -*-
"""**落档前核对**：写新条目之前，先跟已有条目对一遍（打架 / 重复 / 其实同一件事）。

用户口径（2026-10-05 原话）：
  · "加一个环节，在写新条目之前，先算一下和已有条目的关联，要能把互相打架的、重复的等找出来，
     然后**要求 AI 重新想，或者更新旧条目**。"
  · "**所有新条目**都要这样算一下，而且**不同层之间的也是**。"
  · "原始对话和归档对话有存档就够了，**条目实时更新**。但更新旧条目这个也要约束，
     不能因为能更新就让旧条目无限膨胀。"

三方分工（**别混**）：

| 谁 | 干什么 | 为什么这么分 |
|---|---|---|
| **机械**（`neighbors`） | **按层**各取 top-K 最近邻 | ★ **不用阈值** —— 实测打架的对落在 **0.497–0.742**，而不相关的对能到 **0.794**，**全局阈值不存在**（见 `companion-data/_relatedness.txt`）。机械这步只"端候选" |
| **模型**（`MERGE_SYS`） | 判 `add` / `replace` / `merge` / `skip` | 判"是不是同一件事"要读懂意思，余弦做不到 |
| **代码**（`sanitize`） | 越界与超限一律**降级成 `add`** | ⛔ 底色/情境是**手写层**，归档绝不许改；宁可多一条，不可错并 |

⚠️ **为什么邻居必须"按层各取"**：实测一条 fact 在全池里最近的是**底色**（0.655），
而真正相关的 fact 只排第二（0.600）⇒ 混着取会被底色淹没，而且模型会想去改底色。
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config import MergeConfig
from .llm import parse_json
from .prompts import MERGE_SYS

# ⛔ 这两层**只许 flag，不许改**（手写层）
PROTECTED = ("底色", "情境")

Action = Dict[str, object]


def neighbors(V: np.ndarray, layers: Sequence[str], texts: Sequence[str],
              vec: np.ndarray, k: int) -> List[Tuple[int, str, str, float]]:
    """按层各取 `k` 条最近邻。返回 `[(池内下标, 层, 文本, 余弦), …]`（降序）。

    ⚠️ **底色/情境也照列** —— 它们不是拿来改的，是拿来**提示人的**（`flags`）。
    """
    if V is None or not len(V):
        return []
    S = V @ np.asarray(vec, dtype=float)
    out: List[Tuple[int, str, str, float]] = []
    for lay in ("底色", "情境", "记忆"):
        idxs = [i for i, L in enumerate(layers) if L == lay]
        if not idxs:
            continue
        s = S[idxs]
        top = [idxs[int(j)] for j in np.argsort(-s)[:max(0, int(k))]]
        out.extend((i, lay, texts[i], float(S[i])) for i in top)
    return out


def build_input(pairs: Sequence[Tuple[str, Sequence[str],
                                      Sequence[Sequence[Tuple[int, str, str, float]]]]]) -> str:
    """拼给模型看的那段。`pairs` = `[(组名, 新条目文本们, 每条对应的邻居们), …]`。"""
    lines = ["【新条目】"]
    for kind, items, blocks in pairs:
        lines.append("· %s：" % kind)
        if not items:
            lines.append("  （无）")
            continue
        for i, (t, nb) in enumerate(zip(items, blocks), 1):
            lines.append("  %d. %s" % (i, t))
            for idx, lay, txt, cos in nb:
                lines.append("       [%s #%d · %.2f] %s" % (lay, idx, cos, txt))
    return "\n".join(lines)


def sanitize(raw: dict, n_facts: int, n_kept: int, n_protected: int,
             n_total: int, cfg: MergeConfig) -> Tuple[List[Action], List[dict], List[str]]:
    """把模型的裁决**过一遍闸门**。返回 `(decisions, flags, notes)`。

    闸门（**任何一条不满足就降级成 `add`**）：
      1. `idx` 必须是 int，且落在**记忆层**里（`>= n_protected` 且 `< n_total`）；
      2. `merge` 的 `text` 不许超过 `merge_max_len`（超了降成 `replace`）；
      3. `replace` / `merge` **总计**不超过 `max_updates_per_session`（超出的降成 `add`）；
      4. `kept` 只接受 `add` / `skip`（它进的是暂时清单，不该去改长期条目）。
    """
    notes: List[str] = []
    flags = [f for f in (raw.get("flags") or []) if isinstance(f, dict)]
    for f in flags:
        try:
            if not (n_protected <= int(f.get("idx", -1)) < n_total):
                # 报的"跟底色/情境相关"的那条，idx 本来就该指向底色/情境 ⇒ 只做范围检查
                pass
        except (TypeError, ValueError):
            pass

    seen = set()
    updates = 0
    out: List[Action] = []
    for d in (raw.get("decisions") or []):
        if not isinstance(d, dict):
            continue
        kind = str(d.get("kind") or "").strip()
        if kind not in ("facts", "kept"):
            continue
        try:
            i = int(d.get("i"))
        except (TypeError, ValueError):
            continue
        if (kind, i) in seen:                       # 同一条给两次 ⇒ 只认第一次
            notes.append("%s#%d 给了两次动作，只认第一次" % (kind, i))
            continue
        if not (1 <= i <= (n_facts if kind == "facts" else n_kept)):
            notes.append("%s#%d 序号越界，忽略" % (kind, i))
            continue
        seen.add((kind, i))

        act = str(d.get("action") or "add").strip()
        text = str(d.get("text") or "").strip()
        why = str(d.get("why") or "").strip()

        if act not in ("add", "replace", "merge", "skip"):
            notes.append("%s#%d 动作 %r 不认识 ⇒ 当 add" % (kind, i, act))
            act = "add"

        if act in ("replace", "merge"):
            bad = None
            idxs: List[int] = []
            if kind != "facts":
                bad = "kept 只许 add/skip（它不改长期条目）"
            else:
                rawidx = d.get("idxs") if d.get("idxs") is not None else d.get("idx")
                cand = list(rawidx) if isinstance(rawidx, (list, tuple)) else [rawidx]
                for x in cand:
                    try:
                        v = int(x)
                    except (TypeError, ValueError):
                        bad = bad or ("idx 里有非整数：%r" % (x,))
                        continue
                    if not (n_protected <= v < n_total):
                        # ⛔ 底色/情境：手写层，绝不许改
                        bad = "idx=%s 不在记忆层里（底色/情境不许改）" % v
                        continue
                    if v not in idxs:
                        idxs.append(v)
                if bad is None and not idxs:
                    bad = "没给可用的 idx"
            # `merge` 是"和**某一条**合成" ⇒ 只接受单个 idx；给多个当 `replace`
            if bad is None and act == "merge" and len(idxs) > 1:
                act = "replace"
                notes.append("%s#%d merge 给了 %d 个 idx ⇒ 当 replace（一条改写、其余被取代）"
                             % (kind, i, len(idxs)))
            if bad is None and act == "merge" and len(text) > cfg.merge_max_len:
                act = "replace"
                notes.append("%s#%d 合并稿 %d 字 > 上限 %d ⇒ 降成 replace"
                             % (kind, i, len(text), cfg.merge_max_len))
            # 上限按**被碰到的条目数**算（一组 replace 可能碰好几条）
            if bad is None and updates + len(idxs) > cfg.max_updates_per_session:
                bad = ("本场已改 %d 条、这组还要 %d 条 ⇒ 超上限 %d"
                       % (updates, len(idxs), cfg.max_updates_per_session))
            if bad is not None:
                notes.append("%s#%d 不能改（%s）⇒ 当 add" % (kind, i, bad))
                act = "add"
            elif not text:
                notes.append("%s#%d 要改却没给 text ⇒ 当 add" % (kind, i))
                act = "add"
            else:
                updates += len(idxs)
                out.append({"kind": kind, "i": i, "action": act,
                            "idx": idxs[0], "idxs": idxs,
                            "text": text, "why": why})
                continue

        out.append({"kind": kind, "i": i, "action": act, "text": text, "why": why})
    return out, flags, notes


def run(llm, cfg: MergeConfig, text: str, progress=None) -> Tuple[dict, int]:
    """跑一次核对对话。返回 `(原始对象, 用了几次)`。给不出合法 JSON 就返回 `({}, -1)`。

    ★ `progress`（2026-10-07）：给了就流式（CoT 可见）—— 这一步是归档里**最贵**的一段
      （实测占 1/3 时间、输出可达输入的 3 倍），看得见它在想什么比干等好。
    """
    msgs = [{"role": "system", "content": MERGE_SYS},
            {"role": "user", "content": text}]
    last = ""
    for i in range(max(1, int(cfg.json_mode_retries) + 1)):
        msg = llm.chat(msgs, temp=cfg.temperature,
                       json_mode=(i > 0), progress=progress)
        last = (msg.get("content") or "").strip()
        try:
            obj = parse_json(last)
            if isinstance(obj, dict):
                return obj, i + 1
        except Exception:  # noqa: BLE001
            msgs.append({"role": "assistant", "content": last or "（空）"})
            msgs.append({"role": "user", "content":
                         "上面那段不是合法 JSON。**只输出一个 JSON 对象**，"
                         "不要解释、不要 markdown 代码块、字符串里不要出现裸换行。"})
    return {}, -1
