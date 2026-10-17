# -*- coding: utf-8 -*-
"""宿主胶水（Android 侧专用，**不属于业务逻辑**）。

它只干三件事：
  1. 自检：证明 numpy 与 companion 包在手机上真的跑得起来；
  2. 告诉业务包"数据放哪儿"（手机上的路径，桌面那套 ~/.dsh 在 Android 上没有意义）；
  3. 把业务包的返回值转成 Kotlin 好接的原始类型。

★ 业务代码一行都不改；改的是"从哪儿读凭证 / 往哪儿写缓存"。
"""
from __future__ import annotations

import os
import time
from typing import Dict, List


# --------------------------------------------------------------------------- 自检
def selfcheck(data_dir: str = "") -> str:
    """把 numpy + 新线那几件**机械活**在手机上跑一遍：图落盘 / 取回 · 强化 · 衰减 · 底片压缩。

    **离线、不要凭证、不碰用户数据**（在应用私有目录下开一个 `_自检/`，跑完就删）。

    ★ 2026-10-17 重写：原来这一条检的是"池子检索"（`LongPool` + `Reader`）——
      那两样都不存在了，而且它引用的 `companion.__version__` 早就改名叫 `memory` ⇒
      这个自检本身是**坏的**（点了只会抛 NameError）。现在检的是新线真用得上的东西：
      图能不能落盘再读回来、标记集的加减是不是按旋钮走、底片压缩是不是真的压得动。
    ★ 为什么不用 `tempfile`：Chaquopy 里 `tempfile.gettempdir()` 不一定可写 ⇒
      "自检自己先崩"比检不出问题更糟。这里用**应用自己的私有目录**，`finally` 里删干净。
    """
    import shutil

    import numpy

    import memory
    from memory import markset
    from memory.film import 底片
    from memory.graph import 图

    base = cache_dir(data_dir) if data_dir else os.path.join(os.path.expanduser("~"),
                                                             ".palimpsest-selftest")
    tmp = os.path.join(base, "_自检")
    shutil.rmtree(tmp, ignore_errors=True)           # 上次跑了一半留下的
    os.makedirs(tmp, exist_ok=True)
    图行 = ""
    try:
        g = 图(tmp)
        c1 = g.加cell("自检-1", 1, 0, 6)
        c2 = g.加cell("自检-1", 2, 7, 13)
        猫 = g.新件([c1["id"]], [1.0, 0.0, 0.0], 代表="他养了只猫")
        g.新件([c2["id"]], [0.0, 1.0, 0.0], 代表="他上周搬了家")
        g.归进(猫, c2["id"])                    # 第 2 轮又提到猫 ⇒ 归进同一个件
        g.标记集[猫] = 0.5
        g.落盘()
        g2 = 图.载入(tmp)
        # ④ 层1：归进 ⇒ +增量（上限 1.0）；④ 层2：过窗口 ⇒ −衰减率，到地板不再掉
        原先 = float(g2.标记集.get(猫, 0.0))
        markset.强化(g2.标记集, 归进的件=猫)
        强化后 = float(g2.标记集.get(猫, 0.0))
        新, 剪出 = markset.衰减(g2.标记集, 窗口数=3)
        衰减后 = float(新.get(猫, 0.0))
        f = 底片("自检-1", {"模型": "selfcheck", "temperature": 0.0}, tmp)
        f.加轮(1, "他说他搬家了", "嗯")
        f.落盘()
        压缩后 = os.path.getsize(os.path.join(tmp, "底片", "自检-1.json.gz"))
        图行 = ("图 %d 个件 / %d 段 · 强化 %.2f→%.2f · 衰减后 %.2f · 底片 %d B"
                % (len(g2.件), len(g2.cell), 原先, 强化后, 衰减后, 压缩后))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)      # ⛔ 别在用户的数据目录里留垃圾

    return "memory %s · numpy %s · %s" % (memory.__version__, numpy.__version__, 图行)


def python_info() -> Dict[str, str]:
    import sys
    import platform
    return {"python": sys.version.split()[0],
            "impl": platform.python_implementation(),
            "machine": platform.machine()}


def python_info_str() -> str:
    """★ 桥只传字符串：Chaquopy 的 `asMap()` 给的是 Map<PyObject, PyObject>，
    Kotlin 侧按键取值会类型推不出来 ⇒ 一律在 Python 这边拼好。"""
    i = python_info()
    return "%s %s @ %s" % (i["impl"], i["python"], i["machine"])


# --------------------------------------------------------------------------- 路径
def set_paths(data_dir: str, cred_path: str = "") -> str:
    """把 Android 的私有目录告诉业务包。

    `data_dir` 形如 /data/user/0/<包名>/files —— 池子、转写、状态都写这儿。
    `cred_path` 是凭证 yaml 的位置（留空就用 data_dir/credentials.yaml）。
    ★ `config.CRED_PATH` 本来就读环境变量 `COMPANION_CRED_PATH`，所以这里不用改业务代码。
    """
    data_dir = str(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    cred = str(cred_path) if cred_path else os.path.join(data_dir, "credentials.yaml")
    os.environ["COMPANION_CRED_PATH"] = cred
    return cred


def cache_dir(data_dir: str) -> str:
    d = os.path.join(str(data_dir), "cache")
    os.makedirs(d, exist_ok=True)
    return d



# --------------------------------------------------------------------------- 在途那一场
def pending_path(data_dir: str) -> str:
    return os.path.join(cache_dir(data_dir), "pending.json")


def has_pending(data_dir: str) -> bool:
    """有没有"上一场还没归档完"（进程被杀 / 归档失败过）。"""
    return os.path.exists(pending_path(data_dir))


def pending_lines(data_dir: str) -> List[str]:
    """给界面看的：在途的是哪一场、几轮、什么时候开的口。"""
    import json
    import time as _t
    p = pending_path(data_dir)
    if not os.path.exists(p):
        return ["没有在途的场次"]
    try:
        with open(p, encoding="utf-8") as f:
            rec = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return ["待归档标记读不出来：%s" % type(exc).__name__]
    ts = rec.get("ts_start")
    when = _t.strftime("%m-%d %H:%M", _t.localtime(ts)) if ts else "?"
    return ["在途场次：第 %s 场 · %d 轮 · 开始于 %s"
            % (rec.get("sid") or rec.get("k"), len(rec.get("turns") or []), when),
            "已试 %s 次（最多 %s 次）" % (rec.get("attempts", 0),
                                          "见 docs/流程框架.md 的归档那一节")]


def resume_pending(data_dir: str) -> List[str]:
    """把在途的那一场补归档（**要跑 3–4 回合模型，几十秒**）。

    ⚠️ 手机上必须**离开主线程**调用（否则 ANR）；真正的产品形态是前台服务 + WorkManager。

    ★★ 2026-10-06 修：**这里原来不判 `ts_end`**，而会话入口（本文件 `chat_open` 那条路）**判了**
      ⇒ 判据不一致。后果很实：如果把它接到后台（15 分钟一次的 WorkManager），它会
      **把"聊到一半被杀"的那一场当成"已收场"归档掉** —— 那一场还没结束，归档它 = 数据损坏。
      ⇒ 现在与入口**用同一条判据**：**有 `ts_end` = 用户已收场、归档被打断** ⇒ 才补做；
        没有 `ts_end` = 聊到一半 ⇒ **不该在这里动它**（那是"接回来继续聊"，由会话入口处理）。
    """
    from memory.engine import Engine
    from memory.settings import load_config
    from memory.session import waiting_for_review
    from memory.transcript import Transcript
    if not has_cred(data_dir):
        return ["没有凭证，补不了归档（先配好 credentials.yaml）"]
    cfg = load_config(data_dir=data_dir)
    eng = Engine.load(cfg, cache_dir(data_dir))
    if not eng.has_pending():
        return ["没有在途的场次"]
    _p = eng.pending.load() or {}
    if not _p.get("ts_end"):
        return ["在途的那一场**还没收场**（聊到一半）⇒ 不补归档，等它接着聊"]
    # ★★ 2026-10-07：**草案已经出来、用户在审**的那一场，谁也别替它落地。
    #   用户口径：「最后归档完成之后**完成按钮用户点**」⇒ 没按「完成」就一直搁着、池子一字不动。
    #   ⛔ 这里要是照旧补做，App 被系统回收一次（后台 tick 一走）就会**自动落地**
    #      —— 池子变了，而他从没按过「完成」。这与上面那条"判 `ts_end`"的修正是同一个病：
    #      判据必须与入口一致，而且**审阅期要单独拎出来**。
    if waiting_for_review(_p):
        return ["有一场归档**在等你按「完成」**（草案没落地、池子没动）⇒ 不替你落地"]
    tr = Transcript("mobile", out_dir=cache_dir(data_dir))
    try:
        rec = eng.resume_pending(transcript=tr)
    finally:
        tr.close()
    if not isinstance(rec, dict):
        return ["补做完了（那一场已经收过尾）"]
    if (rec.get("archive_draft") or {}).get("land") == "failed":
        return ["补做仍然失败（第 %s 次）：%s"
                % (rec.get("attempts"), (rec.get("archive") or {}).get("note") or "")]
    g = _图摘要(data_dir)
    return ["收尾完毕：这一场已记账（图里现在 %s 个件 · 底片 %s 场）"
            % (g.get("件"), g.get("场"))]


def has_cred(data_dir: str) -> bool:
    return creds_ok(data_dir)


def creds_ok(data_dir: str) -> bool:
    from memory.config import Credentials, CredentialsError
    try:
        Credentials.load()
        return True
    except CredentialsError:
        return False


def _图摘要(data_dir: str) -> Dict[str, object]:
    """**图 + 底片**的只读小账（读不到就当空，⛔ 不抛 —— 只读页面不该把 App 弄崩）。"""
    d = cache_dir(data_dir)
    out: Dict[str, object] = {"件": 0, "标记": 0, "cell": 0, "场": 0, "版本": 0}
    try:
        from memory.graph import 图
        g = 图.载入(d)
        out["件"] = len(g.件)
        out["标记"] = len(g.标记集)
        out["cell"] = len(g.cell)
        out["版本"] = int(g.版本)
    except Exception as exc:  # noqa: BLE001
        out["图错"] = "%s: %s" % (type(exc).__name__, exc)
    try:
        bd = os.path.join(d, "底片")
        if os.path.isdir(bd):
            out["场"] = sum(1 for n in os.listdir(bd)
                            if n.endswith(".json.gz") or n.endswith(".json"))
    except Exception:                                            # noqa: BLE001
        pass
    return out


def state_summary(data_dir: str) -> Dict[str, object]:
    """给手机界面看的**记忆现状**（= 图 + 底片 + 时钟；读不到就报空，不抛）。

    ★ 2026-10-17：原来是"长期池几条 / 临时池几条" —— 池子没了，这两个数也没了。
      现在报的是新线的实体：**件**（图里的节点）、**标记**（件的置信度）、**场**（底片）。
    """
    import json
    d = cache_dir(data_dir)
    sp = os.path.join(d, "state.json")
    g = _图摘要(data_dir)
    if not os.path.exists(sp):
        return {"inited": False, "clock": 0.0, "sessions": 0, "graph": g}
    with open(sp, encoding="utf-8") as f:
        st = json.load(f)
    return {"inited": True, "clock": float(st.get("clock", 0.0)),
            "sessions": int(st.get("sessions", 0)), "graph": g}


def state_summary_lines(data_dir: str) -> List[str]:
    """同上，但拼成人读的行 —— 桥只传字符串列表。"""
    try:
        s = state_summary(data_dir)
    except Exception as exc:  # noqa: BLE001
        return ["读状态失败：%s: %s" % (type(exc).__name__, exc)]
    g = s.get("graph") or {}
    out = ["记忆：%s" % ("已有图" if g.get("件") else "还是空的（还没归档过一场）"),
           "件　　　：%s 个（标记集 %s 条）" % (g.get("件"), g.get("标记")),
           "cell　　：%s 段原文" % g.get("cell"),
           "底片　　：%s 场" % g.get("场"),
           "图版本　：%s" % g.get("版本"),
           "时钟　　：%.2f 天" % float(s["clock"]),
           "已归档场次：%s" % s["sessions"]]
    if g.get("图错"):
        out.append("⚠️ 图读不出来：%s" % g["图错"])
    return out


# --------------------------------------------------------------------------- 对话
# 会话句柄放在宿主这一层：业务包不该关心"界面按了哪个按钮"。
# ⚠️ `_SESS` 只是**内存里的缓存**，不是真相来源 ——
#   真相在 `pending.json`（每轮落盘）与 `state.json`。
#   进程被杀 ⇒ 下次 `chat_open` 会把在途的那一场接回来（这正是"归档可恢复"的用处）。
_SESS: Dict[str, object] = {}


def _open_engine(data_dir: str):
    """对话用的引擎。★ 这条路**需要凭证**（要真的调模型），与只读的 peek 不同。"""
    from memory.engine import Engine
    from memory.settings import load_config
    cfg = load_config(data_dir=data_dir)
    eng = Engine.load(cfg, cache_dir(data_dir))
    # ★ 真挂钟：隔了两天再来，临时池该淡的就得淡。
    #   不推进的话时钟停在上一场，衰减就不是"按天"而是"按场"了。
    eng.advance_to_wall()
    return eng


_TICK_LOCK = None


def _tick_lock():
    """★ **一次 tick 的进程内互斥锁**（2026-10-06 真机实测抓到的并发）。

    现象：一次开机/前台切换会让 WorkManager 周期任务 + 几个广播**同时**踢 L0，
    实测**同一批动静被跑了 3 次**（`activity.jsonl` 里 3 条同 ts 记录，
    而额度只扣到 2 次 —— 说明账本也被并发读改写撞了）。
    后果：**一次变化花掉多次模型钱**，而且策略补丁被并发合并（谁最后写谁赢）。
    ⇒ 用一把进程内锁把整轮 tick 串起来（宿主只有一个进程，这一把就够）。
    """
    global _TICK_LOCK
    import threading
    if _TICK_LOCK is None:
        _TICK_LOCK = threading.Lock()
    return _TICK_LOCK


def _open_channels(data_dir: str):
    """★★ **只把"渠道机制"需要的东西装起来 —— 不碰记忆池**（2026-10-06 解耦）。

    为什么要有这个函数（真机踩到的）：`channel_tick` 原来用 `_open_engine()`，
    而 `Engine.load` 会读**池子** ⇒ 池子不在（比如刚把手机上的测试数据搬走）就抛
    「先跑 prep_pool …」⇒ **渠道机制被记忆层拖死**：明明 L0/L1 只需要
    目录 / 账本 / 活动记录 / 模型客户端，却因为"没有记忆"而完全不工作。

    ⇒ 规矩：**渠道（看世界）与记忆（记住什么）是两层，谁也不该拖死谁**。
       这条路**不读池子、不建嵌入器**（那两样都是对话才需要的）。
    """
    from memory.activity import ActivityLog
    from memory.budgets import BudgetStore
    from memory.channels import ChannelCatalog
    from memory.config import Credentials
    from memory.llm import BaseLLM
    from memory.settings import load_config
    cfg = load_config(data_dir=data_dir)
    cache = cache_dir(data_dir)
    chans = ChannelCatalog(cache)
    # ⚠️ 这里**不需要**给目录接"取数函数"：L0/L1 的读数由宿主采集后**随 payload 带进来**
    #   （`apply_l0` 里那个 `read_fn` 只是从 payload 里查）。给目录接 read_fn 是
    #   **对话里模型要调渠道工具**时才需要的事，与这条路无关 ——
    #   我一度照 Engine 的注释写了 `attach_channels(...)`，而**它在这个仓库里根本不存在**。
    return {
        "cfg": cfg,
        "channels": chans,
        "budgets": BudgetStore(cache),
        "activity": ActivityLog(cache),
        "llm": BaseLLM(cfg.chat, Credentials.load()),
    }


def channel_tick(data_dir: str, payload_json: str) -> str:
    """★★ **一次 L0→L1**（2026-10-05）：宿主把插件读一遍交上来，这里决定要不要唤起模型。

    · **L0**（`apply_l0`）：机械地算"有没有动静"，**不花钱**；
    · **L1**（`l1.run_once`）：★ 有动静、**且 AI 自己的策略说要**、**且额度够**，才唤起**一次**模型。

    ⛔ 这一步**需要凭证**（L1 可能真要调模型），与只读的 `apply_l0`（L0）不同。

    ★★ 2026-10-06 修的**真 bug（真机联动测试才暴露）**：这里原来先调一次 `apply_l0` 拿报告，
       再把**同一个 payload** 交给 `run_once` —— 而 `run_once` 里面又调一次 `apply_l0`。
       `apply_l0` 会**更新 `last_value`**，所以第二次比较必然"没变化"
       ⇒ **模型永远唤不起来**（现象：报告里 `changed:1` 却 `L1=no_change`，`activity.jsonl` 永远不生成）。
       ⇒ 规矩：**一轮 tick 只比一次**，报告用这一次的结果。
    ★★ 同日又抓到两个（都是"AI 自己写规则"实测暴露的）：
       ① **并发**：同一批动静被跑 3 次 ⇒ 用 `_tick_lock()` 串起来；
       ② **策略静默空转**：AI 把 `mute` 写成**人读的名字**，而机械层按 **metric id** 比对
          ⇒ 规则一条都没生效、也没有任何报错。⇒ 两头都补：提示词给**可用键**；
          装载/合并策略时用 `attention.normalize` 把名字翻成 id，**翻不出来的明确拒绝**。
    """
    import json
    with _tick_lock():
        try:
            from memory import attention
            from memory.l1 import run_once

            payload = json.loads(payload_json or "{}")
            # ★ **不碰记忆池**（见 `_open_channels` 的注释）：渠道机制与记忆层解耦
            comp = _open_channels(data_dir)
            chans, budgets, activity, llm = (comp["channels"], comp["budgets"],
                                             comp["activity"], comp["llm"])
            now = int(time.time() * 1000)
            cache = cache_dir(data_dir)
            alias = chans.alias_map()

            # ★ AI 自己的策略（它可能已经写过）+ 机械层的小状态（上次叫它是什么时候）
            pol = attention.load(cache)
            norm, unknown = attention.normalize(pol, alias)
            if unknown or norm != pol:
                # ★ 把 AI 写过的**名字**翻成 id 后落盘（这样它上次写的规则**这次就生效**），
                #   翻不出来的键**明确说出来**，⛔ 不静默丢掉。
                if not unknown:
                    attention.save(cache, norm)
                pol = norm
            st = attention.load_state(cache)
            last_wake_ms = st.get("last_wake_ms")
            try:
                last_wake_ms = int(last_wake_ms) if last_wake_ms is not None else None
            except Exception:                                    # noqa: BLE001
                last_wake_ms = None

            if not has_cred(data_dir):
                # ★ 没笔就不能花：如实说，别装作"看过了但没想法"。**只比一次**（L1 不会跑）
                from memory.channels import apply_l0
                out = {"ok": True, "l0": apply_l0(chans, payload, now)}
                out["l1"] = {"ran": False, "why": "no_cred"}
                out["policy"] = {"mode": pol.get("mode"), "why": pol.get("why")}
                return json.dumps(out, ensure_ascii=False)

            def ask(prompt: str) -> str:
                msg = llm.chat([{"role": "user", "content": prompt}], temp=0.7)
                return str((msg or {}).get("content") or "")

            def save_policy(new_pol):
                attention.save(cache, new_pol)

            left = None
            try:
                from memory.budgets import LLM as _LLM
                left = int(budgets.remaining(_LLM, now))
            except Exception:                                    # noqa: BLE001
                left = None

            r = run_once(catalog=chans, payload=payload, now_ms=now,
                         budgets=budgets, activity=activity, ask_fn=ask,
                         policy=pol, last_wake_ms=last_wake_ms,
                         save_policy=save_policy, budget_left=left,
                         last_policy_note=str(st.get("last_policy_note") or ""))
            # ★ 只有**真的唤起过**才更新"上次叫它"的时间（被策略/额度挡掉的不算）
            if r.get("ran"):
                st["last_wake_ms"] = now
            # ★★ 把"它上次写的规则有没有生效"**留给下一次提示词**（2026-10-06 用户指出：
            #    提示词说了"渠道名"、代码按 id 比对 ⇒ 规则静默失效，而它**永远不知道**）。
            #    ⇒ 拒绝原因必须回灌，它才有机会自己改对。
            note = str(r.get("policy_note") or "")
            if note:
                st["last_policy_note"] = note if note.startswith("策略补丁被拒") else ""
            attention.save_state(cache, st)
            out = {"ok": True, "l0": r.get("l0") or {}}
            out["l1"] = {"ran": bool(r.get("ran")), "why": r.get("why"),
                         "wake_reason": r.get("wake_reason"),
                         "policy_note": r.get("policy_note"),
                         "text": str(r.get("text") or "")[:400]}
            out["policy"] = {"mode": (r.get("policy") or {}).get("mode"),
                             "why": (r.get("policy") or {}).get("why"),
                             "mute": (r.get("policy") or {}).get("mute"),
                             "watch": (r.get("policy") or {}).get("watch")}
            out["budget"] = budgets.stats(now)
            return json.dumps(out, ensure_ascii=False)
        except Exception as e:                                    # noqa: BLE001
            return json.dumps({"ok": False, "err": "%s: %s" % (type(e).__name__, e)},
                              ensure_ascii=False)


def activity_lines(data_dir: str, n: str = "80") -> List[str]:
    """★ **它最近做了什么**（活动记录，**按需翻看，不推送**）。

    ★ 这是设计稿 §5 那条「**不可见 ≠ 不可查**」的界面入口：
      只在这里读，⛔ 不发任何通知 —— 否则"自主"就变成了打扰。
    """
    try:
        from memory.activity import ActivityLog
        rows = ActivityLog(cache_dir(data_dir)).recent(int(n or 80))
    except Exception as e:                                        # noqa: BLE001
        return ["读不出活动记录：%s" % e]
    if not rows:
        return ["（它还没做过什么）"]
    kind_cn = {"looked": "看了一眼", "budget_out": "想看一眼，但今天的额度用完了",
               "ask_failed": "看了一眼，调用失败了"}
    out = []
    for r in reversed(rows):
        ts = time.strftime("%m-%d %H:%M", time.localtime(int(r.get("ts") or 0) / 1000.0))
        kind = kind_cn.get(str(r.get("kind")), str(r.get("kind")))
        ch = r.get("changed") or []
        extra = "（有变化：%s）" % "、".join(str(x) for x in ch) if ch else ""
        thought = str(r.get("thought") or "").strip()
        out.append("%s  %s%s%s" % (ts, kind, extra, ("　→ " + thought) if thought else ""))
    return out


def deliver_reply(data_dir: str, did: str, text: str) -> str:
    """★ 通知上的回话（2026-10-05）：打勾 + 存下回话内容。

    ★ 为什么只做这两件：回话要**接成正常对话的一个回合** —— 那是界面的事（预置输入框）。
      在这里顺手调一次模型，会让"它记得的"和"用户看到的"对不上。
    ★ 存的回话有意义：「我看到了」和「我在忙」对下一次开口的意义完全不同。
    """
    import json
    try:
        from memory.deliver import DeliveryLog
        log = DeliveryLog(cache_dir(data_dir))
        ok = log.mark_replied(str(did), str(text or ""))
        ctx = log.context_of(str(did)) or {}
        return json.dumps({"ok": bool(ok), "id": str(did),
                           "said": str(ctx.get("text") or "")[:200],
                           "sources": ctx.get("sources") or []}, ensure_ascii=False)
    except Exception as e:                                        # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(e).__name__, e)},
                          ensure_ascii=False)


def chat_open(data_dir: str) -> str:
    """开一场。**有在途的就接着那一场**（手机上 App 被杀是常态）。"""
    import json
    if not has_cred(data_dir):
        return json.dumps({"ok": False, "err": "还没配 API Key —— 去「设置」里填"},
                          ensure_ascii=False)
    try:
        from memory.session import Session, waiting_for_review

        eng = _open_engine(data_dir)
        rec = eng.pending.load() if eng.has_pending() else None

        # ★★ 在途的那一场其实有**三种**，必须分开处理：
        #   · 没有 `ts_end` ⇒ 聊到一半被杀 ⇒ **接回来继续聊**；
        #   · 有 `ts_end` 且**草案已经出来、还没落地** ⇒ **审阅期被杀** ⇒
        #     **接着审**：把草案与那几句对话还给界面，⛔ **绝不替它落地**
        #     （用户口径「最后归档完成之后完成按钮用户点」⇒ 没按完成就一直搁着、
        #      池子一字不动。2026-10-07 补的：老代码在这儿会**自动落地**）；
        #   · 有 `ts_end` 且没有可落地的草案 ⇒ 归档跑到一半被打断 ⇒ **补做完**。
        #   判据就用现成的字段：`Session.archive_draft()` 在调归档**之前**写下 `ts_end`
        #   （见 memory/session.py），所以有它就是"已收场"。
        #   ⛔ 少了这个区分，"收场时被杀"的那一场会**永远停在未归档状态**，
        #     而且越聊越长 —— 实测踩到：杀了之后重开，场次仍是 2、pending 还是 10 轮。
        archived = False
        review = None
        if rec and rec.get("ts_end"):
            if waiting_for_review(rec):
                review = _review_payload(rec)
            else:
                from memory.transcript import Transcript
                tr = Transcript("mobile", out_dir=cache_dir(data_dir))
                try:
                    eng.resume_pending(transcript=tr)
                    archived = True
                finally:
                    tr.close()
                rec = eng.pending.load() if eng.has_pending() else None

        if rec:
            sess = Session.restore(eng, rec)
            resumed = True
        else:
            sess = Session(eng, eng.sessions_done + 1, opener=eng.last_tail)
            resumed = False
        sid = "s%d.%d" % (sess.k, len(_SESS) + 1)
        _SESS[sid] = sess
        _g = _图摘要(data_dir)
        return json.dumps({
            "ok": True, "sid": sid, "k": sess.k, "resumed": resumed,
            "turns": len(sess.rec.get("turns") or []),
            # ★ 原来这里是"长期池/临时池条数" —— 池子没了，换成新线的实体（件 / 底片场数）。
            "件": _g.get("件"), "标记": _g.get("标记"), "底片": _g.get("场"),
            "clock": float(eng.clock),
            # 上一场是"收工时被杀"、这次启动把它补归档了 —— 界面该说一声。
            "archived_pending": archived,
            # ★ 上一场归档**你还没按「完成」** ⇒ 把草案还回去，界面接着审（池子一字没动）。
            "review": review,
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)



def graph_view_json(data_dir: str) -> str:
    """★ **件浏览**（记忆页的新数据源，2026-10-17）：图里的件 + 它们锚住的原文。

    ⛔ 旧的四档池子（短期/中期/悬着/长期）在新线里**不存在**了：那边的 `pool_view_json` 读的
      是已经不再更新的旧池子。这一屏改成显示**件**：每个件 = 一组 cell（场id + 轮 + 原文区间），
      强度来自标记集；点开能看到它由哪几段原文组成。

    ★ 只读：不建、不删、不改图。
    """
    import json
    try:
        from memory.film import 读底片, 切回原文
        from memory.graph import 图

        g = 图.载入(cache_dir(data_dir))
        底片们 = {}

        def 取原文(场, 轮, 哪句):
            if 场 not in 底片们:
                底片们[场] = 读底片(os.path.join(cache_dir(data_dir), "底片", 场 + ".json.gz")) or \
                    读底片(os.path.join(cache_dir(data_dir), "底片", 场 + ".json"))
            return 切回原文(底片们[场] or {}, int(轮), 哪句)

        件们 = []
        for 件id, it in g.件.items():
            成员 = []
            for cid in (it.get("成员") or []):
                c = next((x for x in g.cell if x["id"] == cid), None)
                if c is None:
                    continue
                文本 = (取原文(c["场id"], c["轮"], "刺激") + 取原文(c["场id"], c["轮"], "她说")).strip()
                成员.append({"场id": c["场id"], "轮": int(c["轮"]), "文本": 文本,
                             "点亮": cid in (it.get("点亮") or [])})
            件们.append({"件id": 件id, "强度": float(g.标记集.get(件id, 0.0)),
                         "在标记集": 件id in g.标记集, "成员": 成员,
                         "代表": str(it.get("代表") or "")})
        件们.sort(key=lambda x: (-x["强度"], x["件id"]))
        return json.dumps({
            "ok": True,
            "版本": int(g.版本),
            "统计": {"cell": len(g.cell), "件": len(g.件), "标记集": len(g.标记集),
                     "待观察池": len(g.待观察池)},
            "件": 件们,
            "待观察池": [{"单元": str(x.get("单元") or ""), "为什么进来": str(x.get("为什么进来") or "")}
                        for x in g.待观察池],
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)



def graph_action_json(data_dir: str, 件id: str, action: str) -> str:
    """★ **件上的"删改"**（2026-10-17 新线）。作者口径：「删改的功能可以留着的」——
    留着，但对象从"池子条目"换成**件**。

    · `剪出`：掉出标记集、进待观察池 —— **件还在图里**（下次被碰到还能被拉起）；
    · `删`  ：从图里把件移除，并在 `图编辑.jsonl` 记一笔（⛔ 不悄悄删，事后要能查）。

    ⛔ 底片/原文**一个字都不动**：原文是根，件只是"读法的产物"。
    """
    import json
    from memory.graph import 图

    try:
        g = 图.载入(cache_dir(data_dir))
        件id = str(件id or "")
        if 件id not in g.件:
            return json.dumps({"ok": False, "err": "没有这个件：%s" % 件id}, ensure_ascii=False)
        if action == "剪出":
            g.剪出(件id, "手动剪出（记忆页）")
        elif action == "删":
            g.件.pop(件id, None)
            g.标记集.pop(件id, None)
        else:
            return json.dumps({"ok": False, "err": "不认识的动件：%s" % action}, ensure_ascii=False)
        g.落盘()
        try:
            import time
            with open(os.path.join(cache_dir(data_dir), "图编辑.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"件id": 件id, "动作": action, "时间": time.time()},
                                    ensure_ascii=False) + "\n")
        except Exception:                        # noqa: BLE001
            pass
        return json.dumps({"ok": True, "件id": 件id, "动作": action}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)



def chat_turns_json(data_dir: str, sid: str) -> str:
    """把这一场**已经发生的轮次**原样交给界面（返回 JSON 字符串）。

    ★ 为什么要它：`chat_open` 只说"接着上一场、有 N 轮"，但**轮次内容是恢复出来的**
      （`Session.restore` 从 `pending.json` 的 `msgs` / `turns` 接回来）。
      界面若只显示一句"接着上一场"，用户就看不见刚才聊到哪儿了 ——
      而手机上 App 被杀是常态，这条路是**主路径**，不是边角情况。

    ★ 每轮带上 `push`（当时想起的那几条）。
      这**纯粹是给用户看的交互信息**，与"模型看到什么"无关 —— 后者另有一套已经定好的
      设计。摆法：每轮一个**默认折叠**的块、位置在回复**上方**（类似 CoT），
      不点开就一个字都不显示。想起几条是每条消息各不相同的，它本身就是信息。
    """
    import json
    sess = _SESS.get(str(sid))
    if sess is None:
        return json.dumps({"ok": False, "err": "这一场不在了"}, ensure_ascii=False)
    try:
        turns = []
        for t in (sess.rec.get("turns") or []):
            turns.append({
                "user": str(t.get("user") or ""),
                "ai": str(t.get("ai") or ""),
                # ★★ 那一轮注入的**那段回忆**（重启之后「怎么编的」也点得开）
                "回忆": t.get("回忆") or None,
                # ★★ **这一轮的 CoT**（2026-10-07）：用户口径「聊天的 COT 呢？最好也记下来吧？」
                #   —— 它原先只活在界面的内存里，App 一被回收就没了。
                #   核心侧现在把它记进轮次（`Session.ask` 的 `item["reasoning"]`）⇒ 这里原样带上，
                #   界面恢复出来就能"点开看这一轮它在想什么"。
                "reasoning": str(t.get("reasoning") or ""),
                "push": [str(x) for x in (t.get("push_items") or [])],
                "push_n": int(t.get("push_n") or 0),
                "件数": int(t.get("件数") or 0),
                # 给用户看的两个量：这一轮花了多少 token、模型自己"再查一次"查了什么。
                "usage": {k: int(v) for k, v in (t.get("usage") or {}).items()},
                "pulls": [{"query": str(p.get("query") or ""),
                           "got": [str(x) for x in (p.get("got") or [])]}
                          for p in (t.get("pulls") or [])],
            })
        return json.dumps({
            "ok": True,
            "k": int(sess.k),
            "turns": turns,
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)


def _mk_progress(sink, collect=None):
    """把宿主那个 sink 包成 Python 回调（2026-10-07，流式 / CoT 可见）。

    `sink` 是 Kotlin 侧传进来的对象，有 `onDelta(kind, text)`：
    `kind` ∈ {`phase`, `reasoning`, `content`}。
    ⛔ **显示失败绝不许打断这一场**（回调里 try 住）——
      它是"看得见"，不是业务；为了看而把一场归档弄挂，那是本末倒置。

    ★ `collect`（可选）：把进展**照样记一份下来**，供"审阅期被杀"时把 CoT 还回界面。
      为什么必须记：那几十秒的推理原先**只活在界面内存里** ⇒ App 一被回收，
      「归档 · 共 N 步」整块就没了（用户 2026-10-07 真机指出：「**COT 不见了**」）。
      草案本来就落盘（`pending.json`）⇒ 步骤塞进**同一份 rec** 才是配套的。
      ⚠️ 分段规则必须与界面一致（界面在 `AppViewModel.applyDeltas` 里做同一件事）：
        `phase` 开一段、`reasoning` 追加到**当前这一段**。
    """
    if sink is None and collect is None:
        return None

    def cb(kind: str, text: str) -> None:
        k, t = str(kind), str(text)
        if collect is not None:
            try:
                if k == "phase":
                    if len(collect) < _STEP_MAX:
                        collect.append({"phase": t, "text": ""})
                elif k == "reasoning":
                    if not collect:
                        collect.append({"phase": "", "text": ""})
                    # ★ 有界：一段留 `_STEP_TEXT_MAX` 字（界面上是折叠行，看的是预览）
                    collect[-1]["text"] = (collect[-1]["text"] + t)[:_STEP_TEXT_MAX]
            except Exception:  # noqa: BLE001
                pass
        if sink is not None:
            try:
                sink.onDelta(k, t)
            except Exception:  # noqa: BLE001
                pass

    return cb


# ★ 归档步骤落盘的上界：12 轮 × 每轮几千字 ⇒ 实测一场 60 KB 上下（`pending.json` 本来
#   就存着 msgs，同一量级）。这两条只是**兜底**，防止哪次模型话特别多把盘写爆。
_STEP_MAX = 48
_STEP_TEXT_MAX = 20000


def _stash_steps(sess, steps) -> None:
    """把这一场归档的**步骤**（`phase` + 推理原文）记进 rec 并落盘。

    ⛔ 只在"有可审的草案"时记（`land == "yes"`）：没有草案就没人会回来审，
      记了反而是给 `pending.json` 添垃圾。
    ★ **追加**而不是覆盖 —— 归档期里用户每说一句就是新的一段（与界面一致）。
    """
    if sess is None or not steps:
        return
    d = sess.rec.get("archive_draft")
    if not isinstance(d, dict) or str(d.get("land") or "") != "yes":
        return
    try:
        d["steps"] = list(d.get("steps") or []) + list(steps)
        sess.engine.pending.save(sess.rec)
    except Exception:  # noqa: BLE001
        pass          # ⛔ 记步骤失败绝不许影响这一场归档本身


def chat_ask(data_dir: str, sid: str, text: str, sink=None) -> str:
    """问一句、拿回复。⚠️ **要跑模型** ⇒ 宿主必须离开主线程调用。

    ★ `sink`（2026-10-07）：给了就**流式** —— 推理（CoT）与正文边生成边推给界面。
    """
    import json
    sess = _SESS.get(str(sid))
    if sess is None:
        return json.dumps({"ok": False, "err": "这一场不在了（重开一场）"},
                          ensure_ascii=False)
    try:
        res = sess.ask(str(text), progress=_mk_progress(sink))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "reply": res["reply"],
        # ★ 注入内容**一起回传**，但界面**默认不许显示**：
        #   打字的人一旦看见"它想起了什么"，就会不自觉被带着走 —— 那会污染数据。
        #   事后可看（桌面版是 `/push`）。
        "push": res["push"],
        # ★ 这一轮取到**几件**（件层；旧池子那套 `k_raw` / `eff_n` 已经删了）。
        "k": res["k"],
        # ★ 注入冷却掩膜：这一轮被掩掉几条（刚说过的原文不再注入）。
        "cd_blocked": res.get("cd_blocked", 0),
        "pulls": res["pulls"],
        # ★★ 「那段回忆」：编写出的那一段 + 用了哪几条 + 编写版本/耗时（界面默认不显示，点开才看）
        "回忆": (res.get("回忆") or ((_sess().rec.get("turns") or [{}])[-1].get("回忆"))
                  if _sess is not None else None),
        # 这一轮花了多少 token（服务端给的，可能缺）；界面把它显示在回复下面。
        "usage": res.get("usage") or {},
    }, ensure_ascii=False)


def chat_withdraw(data_dir: str, sid: str) -> str:
    """★ **撤回这一场的最后一轮**（2026-10-06）——「撤回上一次对话，重新编辑」。

    ⛔ **不跑模型**（只回退内存与落盘）⇒ 很快，但仍然走 Python 那条独占线程：
       `_SESS` 与 `Engine` 的状态都在那一侧。

    ★ 为什么这件事**必须由业务包做**（而不是宿主自己删两条消息）：一轮不只是"一问一答"，
      它还带着**送进模型的上下文**、**逐轮记账**、**注入冷却掩膜** —— 四处一起退才叫撤回。
      宿主看不见 `msgs` 与 `cd_mask`，自己删只会在下一次生成时接错话。

    ★ 业务侧的账（`withdraw_last` 的注释里逐条写了）：撤掉的那一版**不进记忆**
      （池子只在归档时变），原文进 `rec["withdraws"]` 留痕、跟着转写那一行走。
    """
    import json
    sess = _SESS.get(str(sid))
    if sess is None:
        return json.dumps({"ok": False, "err": "这一场不在了（重开一场）"},
                          ensure_ascii=False)
    try:
        r = sess.withdraw_last()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)
    if not r.get("ok"):
        # ★ 撤不回来的**理由**要原样交给界面（旧版本的轮次 / 长度对不上 / 没得撤），
        #   界面照它显示 —— ⛔ 不许在这儿换成一句笼统的"失败"。
        return json.dumps({"ok": False, "err": r.get("err") or "撤不回来"},
                          ensure_ascii=False)
    return json.dumps({
        "ok": True,
        # ★ 撤回的那一句原文 + 它的回复：界面用前者**填回输入框**（让他改），
        #   后者用来确认撤掉的是哪一对。
        "user": r.get("user") or "",
        "ai": r.get("ai") or "",
        "turns_left": int(r.get("turns_left") or 0),
    }, ensure_ascii=False)


def _finish_json(rec: dict) -> str:
    """收场/落地之后给界面的那份 JSON（`chat_finish` 与 `chat_archive_commit` 共用一份）。"""
    import json
    return json.dumps({
        "ok": True,
        # 归档失败**不是异常**：它按空归档落地、把失败写进转写、场次照样 +1
        # （`archive_step` 那边）。界面该把这件事显示出来，而不是假装成功。
        "failed": int(rec.get("dream_rounds", 0)) < 0,
        "facts_added": rec.get("facts_added", 0),
        "kept_added": rec.get("kept_added", 0),
        "dream_rounds": rec.get("dream_rounds", 0),
        "note": (rec.get("archive") or {}).get("note") or "",
    }, ensure_ascii=False)


def _draft_brief(rec) -> dict:
    """草案给界面看的样子（**只给"它打算记什么"，不给模型那些内部字段**）。

    ★ 从 `rec` 取而不是从 `sess` 取：**审阅期被杀之后**要还给界面的那份草案，
      是从 `pending.json` 里读出来的（那一刻还没有 session，见 `_review_payload`）。
    """
    d = (rec.get("archive_draft") or {}) if isinstance(rec, dict) else {}
    # ★★ 新线（2026-10-17）：草案是「这一场认出哪几处 → 归进件X / 新建件Y」，形状完全不同
    if isinstance(d.get("草"), dict):
        草 = d["草"]
        return {
            "形状": "新线",
            "land": d.get("land"),
            "摘要": str(草.get("摘要") or ""),
            "标记": [{"轮": int(m.get("轮") or 0), "误差": float(m.get("误差") or 0.0),
                      "阈值": float(m.get("阈值") or 0.0)} for m in (草.get("标记") or [])],
            "判": [{"轮": int(p.get("轮") or 0), "归进": p.get("归进"),
                    "新建": bool(p.get("新建")), "说法": str(p.get("说法") or ""),
                    "跳过": bool(p.get("跳过"))} for p in (草.get("判") or [])],
            # 老字段留空，让界面那几条旧分支自然不显示
            "facts": [], "kept": [], "again": [], "done": [], "promote": [], "note": "",
        }
    ar = d.get("ar") or {}
    return {
        "land": d.get("land"),
        "facts": [str(f.get("text", "")) for f in (ar.get("facts") or [])],
        "kept": [str(k.get("text", "")) for k in (ar.get("kept") or [])],
        "again": [int(x) for x in (ar.get("again") or [])],
        "done": [int(x) for x in (ar.get("done") or [])],
        "promote": [int(x) for x in (ar.get("promote") or [])],
        "note": str(ar.get("note") or ""),
    }


def _archive_brief(sess) -> dict:
    """草案给界面看的样子（会话那条路；实现见 `_draft_brief`）。"""
    return _draft_brief(sess.rec if sess is not None else {})


def _review_payload(rec) -> dict:
    """★★ **审阅期被杀之后，还给界面的那一份**（2026-10-07）。

    用户设计里归档是一段**特殊的对话**（草案 + 他对草案说的那几句），
    所以还给界面的也必须**两样都在** —— 只还草案的话，那几句就成了凭空消失的话。

    ★ 判据"该不该走这条路"在**业务包**里（`session.waiting_for_review`），
      这里只负责**摆盘**。
    ★ `talk` 里存的是**发给模型的原话**，其中 `role=="user"` 的**就是用户自己那几句**
      （那段"这是你刚给出的草案…"的包装是每次现拼的，不进 `talk`）⇒ 直接挑出来。
    """
    d = rec.get("archive_draft") or {}
    said = [str(m.get("content") or "")
            for m in (d.get("talk") or [])
            if isinstance(m, dict) and str(m.get("role")) == "user"]
    # ★ 这一段归档的**步骤（CoT）**也要还回去 —— 用户 2026-10-07 真机指出：
    #   「**COT 不见了**」（他正看着那几行，App 被回收一次就整块没了）。
    steps = [{"phase": str(s.get("phase") or ""), "text": str(s.get("text") or "")}
             for s in (d.get("steps") or []) if isinstance(s, dict)]
    return {"draft": _draft_brief(rec), "said": [s for s in said if s.strip()],
            "steps": steps, "k": int(rec.get("sid") or 0)}


def chat_finish(data_dir: str, sid: str, sink=None) -> str:
    """收场并归档（**一步到底**：出草案 + 立刻落地）。⚠️ **一次 25–60 秒**。

    ★ 界面现在走**两步**（`chat_archive_open` → 审 → `chat_archive_commit`）——
      见下面那三个入口；这条留着给"不需要审"的场合（工具、回归）。
    """
    import json
    sess = _SESS.pop(str(sid), None)
    if sess is None:
        return json.dumps({"ok": False, "err": "这一场不在了"}, ensure_ascii=False)
    from memory.transcript import Transcript

    tr = Transcript("mobile", out_dir=cache_dir(data_dir))
    try:
        rec = sess.finish(transcript=tr, progress=_mk_progress(sink))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)
    finally:
        tr.close()
    return _finish_json(rec)


# --------------------------------------------------------------------------- 归档期（2026-10-07）
# 用户设计：「把归档看作**接着刚才对话的特殊对话** —— 模型发完消息之后用户可以接着发消息，
# 最后归档完成之后**完成按钮用户点**」。
# ⇒ 三个入口：出草案 → （可以反复）按你说的改 → 按「完成」才落地。
# ⛔ 归档期里**会话不许 pop**（`chat_finish` 那种 pop 掉就再也说不上话了）；
#    只有 `chat_archive_commit` 才 pop。
def chat_archive_open(data_dir: str, sid: str, sink=None) -> str:
    """★ **出草案**：池子一字不动，只把"打算记什么"拿出来给用户看。⚠️ 25–60 秒。"""
    import json
    sess = _SESS.get(str(sid))
    if sess is None:
        return json.dumps({"ok": False, "err": "这一场不在了（重开一场）"}, ensure_ascii=False)
    # ★ 一边流给界面看，一边**记进 rec**（审阅期被杀之后要把这段 CoT 还回去）
    steps: List[dict] = []
    try:
        d = sess.archive_draft(progress=_mk_progress(sink, steps))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)
    _stash_steps(sess, steps)
    ok = str((d or {}).get("land")) == "yes"
    # ⚠️ 这句话**不能说"按空的记下了"** —— 出草案这条路上**什么都没落地**（池子一字未动）。
    #   真落地失败是「完成」那一步的事（`_finish_json` 的 `failed`）。
    return json.dumps({"ok": ok, "draft": _archive_brief(sess),
                       "err": "" if ok else "这次没出成草案（什么都没落地，池子没变）"},
                      ensure_ascii=False)


def chat_archive_ask(data_dir: str, sid: str, text: str, sink=None) -> str:
    """★ 归档期里用户又说了一句 ⇒ 让它在**同一场归档的上下文**里改草案。

    ⛔ 改的是草案，**池子仍然一字不动**；模型给不出合法 JSON ⇒ **草案保持原样**并如实报。
    """
    import json
    sess = _SESS.get(str(sid))
    if sess is None:
        return json.dumps({"ok": False, "err": "这一场不在了（重开一场）"}, ensure_ascii=False)
    steps: List[dict] = []
    try:
        r = sess.archive_revise(str(text), progress=_mk_progress(sink, steps))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)
    _stash_steps(sess, steps)          # ★ 追加：说一句就是新的一段（与界面一致）
    return json.dumps({"ok": bool(r.get("ok")), "err": str(r.get("err") or ""),
                       "draft": _archive_brief(sess)}, ensure_ascii=False)


def chat_archive_commit(data_dir: str, sid: str, sink=None) -> str:
    """★ **「完成」**：把草案落地（这一步才写池子 / 写底片 / 场次 +1）。

    ★ 核对（`_precheck`）在这里跑 ⇒ 它那一段的流式也走 `sink`（按完成之后还能看得见）。
    """
    import json
    sess = _SESS.pop(str(sid), None)
    if sess is None:
        return json.dumps({"ok": False, "err": "这一场不在了"}, ensure_ascii=False)
    from memory.transcript import Transcript

    tr = Transcript("mobile", out_dir=cache_dir(data_dir))
    try:
        rec = sess.archive_commit(transcript=tr, progress=_mk_progress(sink))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)
    finally:
        tr.close()
    return _finish_json(rec)


# --------------------------------------------------------------------------- 设置
def ledger_lines(data_dir: str = "") -> List[str]:
    """**新线的参数账**（`memory/knobs.py` 那 19 项），拼成人读的行 —— **只读**。

    ★ 为什么只读：这些数按 `docs/参数账.md` 的规矩各带依据等级（【实测】【推导】【假设】），
      改一个要「改前 → 改后 → 为什么」留一行记录。⛔ 不是滑块 ——
      作者口径：「⛔ 不许散落在代码里凭手感调」。
    ★ 为什么放在设置页：旧池子那一堆旋钮（`reader.*` / `time.*` / `dream.*`）
      已经删了（它们拧了什么都不变），页面上需要**真的在控制这台机器的数**。
    """
    from memory.knobs import 参数账

    rows = 参数账()
    out = ["新线参数账：%d 项（参考值，改要留记录，不是滑块）" % len(rows), ""]
    for name, one in rows.items():
        依据 = str(one.get("依据") or "")
        说明 = str(one.get("说明") or "")
        out.append("%s = %s  %s" % (name, one.get("值"), 依据))
        if 说明:
            # 说明可能很长：按 40 字折行，前端只负责显示
            line = ""
            for ch in 说明:
                line += ch
                if len(line) >= 40:
                    out.append("    " + line)
                    line = ""
            if line:
                out.append("    " + line)
    return out


def knobs_json(data_dir: str) -> str:
    """可改参数的清单 + 当前生效值。

    ★ **每一项都带上它的依据等级**（`grade`），界面要显示出来：
      标着【实测】的项是量出来的，动它要先想清楚；标着【假设】的本来就是未知数。
      **不许把这两类混在一起让人随便拖** —— 这正是本仓库参数表的用意。
    """
    import json
    try:
        from memory.settings import KNOBS, SettingsStore, knob_by_path

        cfg = _cfg_now(data_dir)
        store = SettingsStore()
        changed = store.load(data_dir)
        items = []
        for k in KNOBS:
            cur = cfg
            for part in k.path.split("."):
                cur = getattr(cur, part)
            bits = {"path": k.path, "label": k.label, "kind": k.kind, "grade": k.grade,
                    "note": k.note, "unit": k.unit, "low": k.low, "high": k.high,
                    "choices": k.choices, "value": cur,
                    "changed": k.path in changed}
            items.append(bits)
        return json.dumps({"ok": True, "knobs": items,
                           "settings_path": str(store.resolve(data_dir))},
                          ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)


def _cfg_now(data_dir: str):
    from memory.settings import load_config
    return load_config(data_dir=data_dir)


def settings_save(data_dir: str, overrides_json: str) -> str:
    """写回用户设置（只写传进来的项）。返回**重新读一遍**的生效值。"""
    import json
    try:
        from memory.settings import SettingsStore, knob_by_path

        raw = json.loads(overrides_json or "{}")
        # ★ 先把表外的键剔掉，再合并。
        #   只在 save() 里过滤是不够的：那样它仍留在**内存里的 merged** 中，
        #   紧接着回读生效值就会 `AttributeError`（实测踩到过）。
        known = {p: v for p, v in raw.items() if knob_by_path(p) is not None}
        bad = sorted(p for p in raw if p not in known)
        store = SettingsStore()
        # ★ 合并而不是覆盖：界面只传"这次动过的项"，
        #   整份覆盖会把用户之前改过的其它项悄悄清掉。
        merged = dict(store.load(data_dir))
        merged.update(known)
        store.save(merged, data_dir)
        cfg = _cfg_now(data_dir)
        return json.dumps({"ok": True, "ignored": bad,
                           "effective": {p: _dig(cfg, p) for p in merged}},
                          ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)


def _dig(cfg, path: str):
    cur = cfg
    for part in path.split("."):
        cur = getattr(cur, part)
    return cur


# --------------------------------------------------------------------------- 提示词
def prompt_json(data_dir: str) -> str:
    """会话前缀（system①②）的**当前生效值** + 它是不是被改过。

    ★ 默认值（冻结稿）也一起给出：界面上要能"一键恢复默认"，
      而恢复默认的前提是**知道默认长什么样**，不能靠"删掉覆盖再从别处读"。
    """
    import json
    try:
        from memory.prompts import SYS1, SYS2, load_session_prompts, prompt_path

        s1, s2 = load_session_prompts()
        p = prompt_path()
        return json.dumps({
            "ok": True,
            "sys1": s1, "sys2": s2,
            "default_sys1": SYS1, "default_sys2": SYS2,
            "edited": (s1 != SYS1) or (s2 != SYS2),
            "path": str(p),
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)


def prompt_save(data_dir: str, sys1: str, sys2: str) -> str:
    """存提示词覆盖。两条都空 ⇒ 等同于"恢复默认"（删掉覆盖文件）。"""
    import json
    try:
        from memory.prompts import clear_session_prompts, save_session_prompts

        s1, s2 = str(sys1 or "").strip(), str(sys2 or "").strip()
        if not s1 and not s2:
            clear_session_prompts()
        else:
            if not s1 or not s2:
                return json.dumps(
                    {"ok": False, "err": "两段都要填；想恢复默认就两边都清空"},
                    ensure_ascii=False)
            save_session_prompts(s1, s2)
        return json.dumps({"ok": True}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)


def creds_state_json(data_dir: str) -> str:
    """凭证的**状态**（**绝不回显密钥本身**）。

    界面只需要知道"填没填、指向哪儿"；把密钥读回 Kotlin 只会让它多一份
    可能被日志/崩溃报告带出去的存在。要换就整条覆盖。
    """
    import json
    try:
        from memory.config import Credentials, CredentialsError

        try:
            c = Credentials.load()
        except CredentialsError as e:
            return json.dumps({"ok": True, "configured": False,
                               "err": str(e).splitlines()[0], "path": _cred_path_str()},
                              ensure_ascii=False)
        return json.dumps({
            "ok": True, "configured": True, "path": _cred_path_str(),
            "embed_base_url": c.embed_base_url,
            "chat_url": c.chat_url,
            # 只给"有没有"，不给值
            "has_embed_key": bool(c.embed_api_key),
            "has_chat_key": bool(c.chat_api_key),
        }, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)


def _cred_path_str() -> str:
    from memory.config import cred_path
    return str(cred_path())


def creds_save(data_dir: str, embed_base_url: str, embed_api_key: str,
               chat_api_key: str, chat_url: str = "") -> str:
    """整条覆盖凭证。**空字符串表示"这一项不动"** —— 界面拿不到旧密钥，
    所以"只改 base_url"这种操作必须能把密钥栏留空。"""
    import json
    try:
        from memory.config import Credentials, CredentialsError

        old = None
        try:
            old = Credentials.load()
        except CredentialsError:
            old = None
        url = str(embed_base_url or "").strip() or (old.embed_base_url if old else "")
        ek = str(embed_api_key or "").strip() or (old.embed_api_key if old else "")
        ck = str(chat_api_key or "").strip() or (old.chat_api_key if old else "")
        cu = str(chat_url or "").strip() or (old.chat_url if old else
                                             "https://api.deepseek.com/chat/completions")
        if not (url and ek and ck):
            return json.dumps({"ok": False, "err": "还缺：%s"
                               % "、".join(n for n, v in
                                           (("接入地址", url), ("编码器 Key", ek),
                                            ("对话 Key", ck)) if not v)},
                              ensure_ascii=False)
        Credentials(embed_base_url=url.rstrip("/"), embed_api_key=ek,
                    chat_api_key=ck, chat_url=cu).save()
        return json.dumps({"ok": True, "path": _cred_path_str()}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                          ensure_ascii=False)
