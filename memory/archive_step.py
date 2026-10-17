# -*- coding: utf-8 -*-
"""归档五步（`①切cell → ②误差 → ③配对 → ⑤判 → ④层1/层2`）—— **唯一会改图的环节**。

═══════════════════════════════════════════════════════════════════════
按 `docs/流程框架.md` 的流水线写，执行顺序是 **① ② ③ ⑤ ④**（⑤ 的输出是 ④ 的输入）。
═══════════════════════════════════════════════════════════════════════

| 步 | 谁做 | 这里怎么落 |
|---|---|---|
| **① 切 cell** | 机械 | **一场天然一段、粒度是「轮」**：cell = 这一轮的原文区间（`刺激起 … 她说止`） |
| **② 误差** | 机械 | 从**底片里那一轮最后一次调用的 `logprobs`** 算：`误差 = −mean(logprob)`；标记 = `误差 > μ_{t−τ} + γ·σ_{t−τ}` |
| **③ 配对** | 机械 | 候选 = `标记集.提候选件`（余弦，只从标记集里挑）—— ⛔ 不问模型 |
| **⑤ 判** | **模型** | 归进哪件 / 新建。**候选为空就不问模型**（第一批必然如此 ⇒ 全走新建） |
| **④ 层1** | 机械 | 归进 `+增量` · 新建 `=初值` · 冷成员拉到 `max(当前, 拉起)` |
| **④ 层2** | 机械 | 每窗口 `−衰减率`；掉到门槛以下 ⇒ **剪出标记集**、进待观察池（⛔ 不删件） |

★★ **分两段**（与界面上的「出草案 → 审 → 按完成」对齐）：
- `草案(...)`：只**算**（含 ⑤ 那次模型调用），⛔ **不动图**、不动标记集；
- `完成(图, 草案)`：**这一下才写**（④ + 版本 +1 + 落盘）。
  ⇒ 用户没按「完成」之前，池子/标记集一字不动（这条从旧线继承下来，见 `docs/决策记录.md`）。

⛔ 本模块不写底片、不写逐轮记录（那是 `film.py` 的事）；⑤ 的输入输出落 `判/`。
"""
import io
import json
import os
import time
from typing import Callable, Dict, List, Optional, Sequence

from . import markset
from .embedder import 取向量
from .film import 切回原文, 提示词哈希
from .graph import 图
from .knobs import 件 as K件, 标记 as K标记, 判 as K判, 版本 as K版本

# --------------------------------------------------------------------------- ⑤ 的提示词（新线，尚未冻结）

判提示词 = """下面是这一场里被标出来的一处（它比上下文更"意外"，所以值得记）：

{这一处}

你记忆里已经有的几件（挑最像的看，可能都不像）：
{候选}

判断这一处，**问三件事**：

【一】它该**并进已有的某一件**，还是**单独建一件**？
    ——「同一件事」的意思很窄：说的是**同一件具体的事**，不是同一个话题。

【二】它和候选里的某一件，是不是**因为某件事连在一起**？
    —— 这一问和【一】**完全分开**：两件事可以**不是同一件**，却**都关于同一件事**
      （比如都关于"那只猫"、都在"工作"上、都是"那种沉"）。
    —— 连上了就给它一个**短名字**（三五个字，你自己起：像"猫的事""工作上的事""那种沉"），
      ⛔ 别用"同一类事""情绪"这种套话，要具体到是什么。
    —— 一件事可以和**好几件**连上（不同名字），也可以一件都不连。

只回 JSON，不要别的话：
{{"归进": "件-0003" 或 null, "新建": true 或 false,
  "连": [{{"件": "件-0003", "因为": "猫的事"}}],
  "说法": "一句话，为什么"}}

· 归进与新建**只能选一个**；
· 候选里没有真正同一件事的就新建 —— ⛔ 别硬塞进去；
· 「连」里只填**候选里真有的件号**，⛔ 别编；「因为」要短、要具体、可以多个；
· 「说法」是给你自己以后回看用的，别写给用户看。"""


def _区间(cells: Sequence[Dict], 轮: int) -> tuple:
    """从草案的 cell 列表里取那一轮的 `(起, 止)`；没有就 `(0, 0)`。"""
    for c in cells:
        if int(c.get("轮") or 0) == int(轮):
            return int(c.get("起") or 0), int(c.get("止") or 0)
    return 0, 0


def _均值(xs: Sequence[float]) -> float:
    return (sum(xs) / len(xs)) if xs else 0.0


def _标准差(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _均值(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def 误差序列(底片数据: Dict) -> List[Dict]:
    """**旧口径（占位）**：每轮一个数 —— 从**那一轮最后一次调用的 `logprobs`** 算 `−mean(logprob)`。

    ⛔ 这不是设计要的那个量（设计要 `预测误差`：用读法预测每一格）。
      留在这里当**对照**：它一直算、一直存，但不参与标记 ——
      真机实测它就是"她怎么写、说了多长"的副产物（r 与 token 数 = 0.685）。


    ★ 只认"出正文"的那次调用（`用途 != 编写`）：编写那一步是**为了给她看**而另做的一次生成，
      它的概率不属于"这一轮她说得有多意外"。
    ★ 分项也留着（推理 / 正文）：用哪一个当"事件的意外"**还没定**（`docs/待决 §13/§20`），
      先都记下来，别等以后想换口径时才发现没存。
    """
    out: List[Dict] = []
    for t in (底片数据.get("逐轮") or []):
        调用 = [c for c in (t.get("调用") or []) if c.get("用途") != "编写"]
        lp = (调用[-1].get("logprobs") or {}) if 调用 else {}
        推理 = [float(e.get("logprob") or 0.0) for e in (lp.get("推理") or [])]
        正文 = [float(e.get("logprob") or 0.0) for e in (lp.get("正文") or [])]
        全部 = 推理 + 正文
        def 负均值(xs):
            return -_均值(xs) if xs else None
        out.append({"轮": int(t.get("轮") or 0),
                    "误差": 负均值(全部),
                    "误差_推理": 负均值(推理),
                    "误差_正文": 负均值(正文),
                    "token数": len(全部)})
    return out


# 预测误差（2026-10-17 删）：它走 prefix:true 让模型沿真实开头"接着往下写"，读自己的 logprobs。
# ⛔ 那不是"她的意外" —— 作者原话：「我们要的是记忆，一个 prefix 啥也测不出来。」
# 现在 ② 直接用底片里**她那一次生成**的 logprobs（见 草案() 的 ② 段）；
# 用哪个分项由 knobs["②分项"] 定（默认 误差_推理）。


def 标记(序列: List[Dict], γ: Optional[float] = None, τ: Optional[int] = None) -> List[Dict]:
    """`②`：`误差 > μ_{t−τ..t−1} + γ·σ` ⇒ 标记。**前 τ 轮不判**（没有窗口）。"""
    γ = float(K标记["γ"]["值"] if γ is None else γ)
    τ = int(K标记["τ"]["值"] if τ is None else τ)
    out: List[Dict] = []
    for i, e in enumerate(序列):
        x = e.get("误差")
        if x is None or i < τ:
            continue
        窗口 = [序列[j]["误差"] for j in range(max(0, i - τ), i) if 序列[j].get("误差") is not None]
        if not 窗口:
            continue
        阈值 = _均值(窗口) + γ * _标准差(窗口)
        if float(x) > 阈值:
            out.append({"轮": e["轮"], "误差": float(x), "阈值": 阈值,
                        "超出": float(x) - 阈值})
    return out


def _这一处的原文(底片数据: Dict, 轮: int) -> str:
    return 切回原文(底片数据, 轮, "刺激") + 切回原文(底片数据, 轮, "她说")


def _上一句(底片数据: Dict, 轮: int) -> str:
    return 切回原文(底片数据, 轮 - 1, "她说") if 轮 > 1 else ""


def _候选行(图: 图, 候选: List[tuple], 底片们: Dict[str, Dict]) -> str:
    行 = []
    for 件id, 分 in 候选:
        it = 图.件.get(件id) or {}
        rep = str(it.get("代表") or "")
        文本 = ""
        for c in 图.cell:
            if c["id"] == rep:
                底 = 底片们.get(c["场id"])
                文本 = _这一处的原文(底, c["轮"]) if 底 else ""
                break
        行.append("%s（像 %.2f）：「%s」" % (件id, float(分), 文本[:120]))
    return "\n".join(行) if 行 else "（一件都没有）"


def 草案(底片数据: Dict, 图: 图, llm, 场id: str,
         嵌入: Optional[Callable[[Sequence[str]], List[List[float]]]] = None,
         其他底片: Optional[Dict[str, Dict]] = None, 进度: Optional[Callable] = None,
         ) -> Dict:
    """只**算**，⛔ 不动图（用户按「完成」之前，池子一字不动）。"""
    底片们 = dict(其他底片 or {})
    底片们[str(场id)] = 底片数据

    # ---- ① 切 cell（一场天然一段，粒度是轮）
    草案cell = []
    for t in (底片数据.get("逐轮") or []):
        起 = int((t.get("刺激区间") or [0, 0])[0])
        止 = int((t.get("她说区间") or [0, 0])[1])
        草案cell.append({"轮": int(t.get("轮") or 0), "起": 起, "止": 止})
        图.按区间找cell(str(场id), t.get("轮") or 0)   # 只为幂等检查，不改图

    # ---- ② 误差 → 标记
    # ★★★ 2026-10-17（作者定）：**误差 ＝ 她自己的意外**，⛔ "模型续写有多顺"。
    #   作者原话：「我们要的是记忆，一个 prefix 啥也测不出来。」
    #   ⇒ **不再调 API 预测**（`预测误差` 已删）；直接读底片里**她那一次生成**的 `logprobs`。
    #
    # ★ 分项（`knobs["②分项"]`）：
    #   · `误差_推理` —— 她**想**的时候的不确定。**默认用这一项**：
    #     "她卡了一下"的直接信号是推理里的犹豫/反复，⛔ 正文措辞。
    #   · `误差_正文` —— 她说出口的措辞。⚠️ 实测 `r(误差, token 数) = 0.685`（混着"说了多长"）。
    #   · `误差`      —— 推理＋正文合起来的旧口径，留作对照。
    #   ⚠️ 没开思考的场次没有推理 ⇒ 自动退回 `误差_正文`，并在底片里能看出来（该分项为 None）。
    旧序列 = 误差序列(底片数据)
    分项 = str(K标记["②分项"]["值"])
    序列 = [{"轮": r["轮"],
             "误差": (r.get(分项) if r.get(分项) is not None else r.get("误差")),
             "分项": 分项 if r.get(分项) is not None else "误差",
             "token数": r["token数"]} for r in 旧序列]
    # 定标记 —— **照 `流程框架 [8]`**：`误差 > μ_{t−τ} + γ·σ_{t−τ}` ⇒ 标记。
    标 = 标记(序列)

    # ---- ③ 配对（候选件）＋ ⑤ 判
    候选表: Dict[int, List[tuple]] = {}
    判表: List[Dict] = []
    向量表 = 图.件向量表()
    for m in 标:
        轮 = int(m["轮"])
        文本 = _这一处的原文(底片数据, 轮)
        vec: List[float] = []
        vec错 = None
        if 嵌入 is not None and 文本.strip():
            try:
                # ★★ 2026-10-17 真对话第 2 场抓到的：这里原来写的是 `嵌入([文本])`，
                #   而 `Embedder` **不可调用**（方法是 `raw_embed`）⇒ 抛 TypeError
                #   ⇒ 被下面的裸 `except` 吞成 `vec=[]` ⇒ **每一件的向量从来都是空的**
                #   ⇒ `提候选件` 只从"有向量的件"里取 ⇒ **候选恒空** ⇒ 永远没有"归进"，
                #   而且**死锁**：`图.归进` 只能在归进时补写向量，而补写的前提是先成为候选。
                #   ⛔ 别再写成 `嵌入(...)`。
                _e = 取向量(嵌入, [文本])
                vec = [float(x) for x in (_e[0] if len(_e) else [])]
            except Exception as e:                # noqa: BLE001
                # ⛔ 不许再静默：向量拿不到 ⇒ 这一格当不上候选 ⇒"归进"路径整条是死的。
                #   记下来，让 `草案` 能把它报出来（图上/摘要里看得见，不是默默正常）。
                vec = []
                vec错 = "%s: %s" % (type(e).__name__, e)
        候选 = markset.提候选件(vec, 向量表, 图.标记集, k=int(K判["候选件数"]["值"])) if vec else []
        候选表[轮] = 候选
        if not 候选:
            # ★★ 2026-10-17（真对话第 2 场）：**"没有候选"有两种成因，⛔ 不许混成一句话**
            #   · 标记集是空的（第一批）—— 正常；
            #   · 标记集里有件，但我们**没有向量**去比（嵌入失败 / 件本身没向量）
            #     —— 这是**故障**，写成"还没有东西"会让"全是新建"这个读数彻底不可信
            #     （分不清是"模型觉得不像"还是"根本没给它看"）。
            if vec错:
                因 = "本格拿不到向量（%s）⇒ 没得比" % vec错
            elif not 图.标记集:
                因 = "标记集是空的（还没有件）"
            elif not 向量表:
                因 = "标记集有 %d 件，但**没有一件有向量**（嵌入从没成功过）" % len(图.标记集)
            else:
                因 = "标记集有 %d 件、可比的 %d 件，但一件都没提出来" % (
                    len(图.标记集), len(向量表))
            判表.append({"轮": 轮, "归进": None, "新建": True,
                       "说法": 因, "向量": vec, "向量错误": vec错,
                       "没问模型的因": 因,
                       "起": _区间(草案cell, 轮)[0], "止": _区间(草案cell, 轮)[1]})
            continue
        提示 = 判提示词.format(
            这一处="〔第 %d 轮〕「%s」%s" % (轮, 文本,
                                          ("\n上一句是：「%s」" % _上一句(底片数据, 轮))
                                          if _上一句(底片数据, 轮) else ""),
            候选=_候选行(图, 候选, 底片们))
        if 进度:
            进度("phase", "归档：判第 %d 轮" % 轮)
        try:
            msg = llm.chat([{"role": "user", "content": 提示}], fast=True, logprobs=True)
            from .llm import parse_json
            d = parse_json(str(msg.get("content") or ""))
        except Exception as e:                   # noqa: BLE001
            d = None
            判表.append({"轮": 轮, "归进": None, "新建": True,
                       "说法": "判失败，先单独建一件（%s: %s）" % (type(e).__name__, e),
                       "向量": vec, "提示词哈希": 提示词哈希(提示),
                       "起": _区间(草案cell, 轮)[0], "止": _区间(草案cell, 轮)[1]})
            continue
        归进 = d.get("归进") if isinstance(d, dict) else None
        新建 = bool(d.get("新建")) if isinstance(d, dict) else False
        说法 = str((d or {}).get("说法") or "") if isinstance(d, dict) else ""
        # ★★ 2026-10-17（作者定）：**【二】因为什么连在一起** —— 和"是不是同一件事"**分开判**。
        #   两件事可以不是同一件、却都关于"那只猫"。⇒ 输出是一串 {件, 因为}，
        #   而"因为"是**模型自己起的短名字**（自由文本，⛔ 写死的槽）。
        #   ⚠️ 只认**候选里真有的件号**：编出来的号当没给（⛔ 别让幻觉污染块）。
        候选号 = {str(c[0]) for c in 候选}
        连 = []
        for x in ((d.get("连") if isinstance(d, dict) else None) or []):
            if not isinstance(x, dict):
                continue
            他 = str(x.get("件") or "").strip()
            因 = str(x.get("因为") or "").strip()
            if 他 in 候选号 and 因:
                连.append({"件": 他, "因为": 因})
        if 归进 and 归进 not in 图.件:
            归进, 新建 = None, True                  # 它报了一个不存在的件 ⇒ 当新建（并留痕）
            说法 = (说法 + "（它报的件不存在，按新建）").strip()
        if not 归进 and not 新建:
            新建 = True                              # 两样都没给 ⇒ 不许丢
            说法 = (说法 + "（没给归进也没说新建，按新建）").strip()
        判表.append({"轮": 轮, "归进": 归进, "新建": bool(新建), "说法": 说法,
                     "连": 连,
                     "向量": vec, "提示词哈希": 提示词哈希(提示),
                     "候选": [c[0] for c in 候选],
                     "起": _区间(草案cell, 轮)[0], "止": _区间(草案cell, 轮)[1]})

    # ---- `[10]` 的第二问 (b)：**本批没归进的那些，哪些彼此属于一起**
    _本批分堆(llm, 底片数据, 判表, 进度=进度)

    return {"场id": str(场id), "cell": 草案cell, "误差": 序列, "标记": 标,
            "候选": 候选表, "判": 判表,
            # ★ 两份读数都存：`误差` ＝ 设计口径（预测）；`旧误差` ＝ 占位口径（她生成时）
            "误差": 序列, "旧误差": 旧序列,
            # ★ 版本对要它（`§3.1.3`）：底片那一场的指纹原样带上来
            "指纹": dict(底片数据.get("指纹") or {}),
            "摘要": _摘要(标, 判表)}


def _摘要(标: List[Dict], 判表: List[Dict]) -> str:
    if not 标:
        return "这一场没有值得记的地方（没有标记）。"
    行 = ["这一场认出 %d 处：" % len(标)]
    # ★ 同组的合成一行：`一件含 N 格` 才看得出"一件事"的粒度有没有对。
    组数: Dict[str, List[int]] = {}
    for p in 判表:
        if p.get("归进"):
            行.append("- 第 %d 轮 → 归进 %s" % (p["轮"], p["归进"]))
        else:
            组数.setdefault(str(p.get("组", "u%d" % int(p["轮"]))), []).append(int(p["轮"]))
    for 号, 轮们 in 组数.items():
        if len(轮们) == 1:
            行.append("- 第 %d 轮 → 新建一件" % 轮们[0])
        else:
            行.append("- 第 %s 轮 → 新建**一件（含 %d 格）**"
                      % ("、".join(str(r) for r in 轮们), len(轮们)))
    return "\n".join(行)


def _本批分堆(llm, 底片数据: Dict, 判表: List[Dict], 进度=None) -> None:
    """`[10]` 的**第二问 (b)**：本批没归进的那些 cell 里，**哪些彼此属于一起** ⇒ 写 `组`。

    ★★ 为什么必须做（真机第 1 场实测抓到的）：草案期间 `图.标记集` **不动**，
      所以"归进"(a) 的候选**在第一批必然全空** ⇒ 若不做 (b)，5 个标记会各自新建一件
      ⇒ **一件 ≠ 一件事**（`流程框架 §四` 那条：一件只有一个 cell），
      `[R]` 的组内 α / 组间 β 全部退化成摆设。
    ★ 判失败 ⇒ **每个单独成组**（保守，⛔ 不猜、也不崩）。

    写进 `判表[*]["组"]`：同组号 ⇒ `完成()` 会把它们合成**一个含多个成员的件**。
    """
    新 = [p for p in 判表 if p.get("新建") and not p.get("归进")]
    for p in 新:
        p.pop("组", None)
    if len(新) <= 1:
        return                              # 只有一处 ⇒ 它自己就是一件事，不用问

    行 = []
    for p in 新:
        轮 = int(p["轮"])
        文本 = _这一处的原文(底片数据, 轮)
        上 = _上一句(底片数据, 轮)
        行.append("第 %d 轮：%s%s" % (轮, ("（上一句：「%s」）" % 上) if 上 else "", 文本))
    提示 = ("下面是我**这一场**说过的几句话（都没归进任何已有的件）。\n\n%s\n\n"
            "请你判断：**这些话里，哪些在讲同一件事**，把它们归成一组。\n"
            "· 用【轮号】列成员；\n"
            "· ★ 允许有些话自成一件事（不属于任何组）——⛔ 不要硬把每一条都塞进某组；\n"
            "· 每组一句话说清凭的是哪一条。\n\n"
            "只输出 JSON：{\"组\":[{\"成员\":[轮号…],\"说法\":\"…\"}],\"单独成事的\":[轮号…]}"
            % "\n".join(行))
    if 进度:
        进度("phase", "归档：本批分堆（%d 处）" % len(新))
    try:
        msg = llm.chat([{"role": "user", "content": 提示}], fast=True, logprobs=True)
        from .llm import parse_json
        d = parse_json(str(msg.get("content") or ""))
    except Exception as e:                   # noqa: BLE001
        for k, p in enumerate(新):
            p["组"] = k                      # 失败 ⇒ 各自成组
            p["分堆说法"] = "分堆失败（%s: %s），各自成件" % (type(e).__name__, e)
        return
    轮到项 = {int(p["轮"]): p for p in 新}
    组 = 0
    if isinstance(d, dict):
        for g in (d.get("组") or []):
            员 = g.get("成员") if isinstance(g, dict) else g
            说 = str((g or {}).get("说法") or "") if isinstance(g, dict) else ""
            真 = [int(m) for m in (员 or []) if str(m).lstrip("-").isdigit() and int(m) in 轮到项]
            if len(真) < 2:
                continue                     # 一组的成员少于 2 ⇒ 不算一组
            for m in 真:
                轮到项[m]["组"] = 组
                轮到项[m]["分堆说法"] = 说
            组 += 1
    for p in 新:                             # 没被分进任何组的 ⇒ 各自成组
        if "组" not in p:
            p["组"] = 组
            组 += 1


def 完成(图: 图, 草案: Dict, 记判目录: Optional[str] = None) -> Dict:
    """**这一下才写**：`④ 层1`（强化/新建/冷成员）＋ `④ 层2`（衰减/剪出）＋ 版本 +1 + 落盘。"""
    场id = str(草案.get("场id") or "")
    if not 场id:
        # ⛔ 没有场id 就没法锚 cell（cell 的身份是原文区间 + 场）。宁可如实报，不建孤儿。
        return {"场id": "", "版本": 图.版本, "层1": [], "层2": None,
                "err": "草案里没有 场id，没写图"}

    层1: List[Dict] = []
    # ★★ 2026-10-17（真机跑出来的）：**这一场的 cell 无条件落图**。
    #   原来 cell 只在 `判` 的循环里顺手建 ⇒ 一场"没有标记"的对话在**图里一点痕迹都没有**
    #   （真机实测：9 轮、含「他上个月把工作辞了」这种明显意外，图版本 +1 但 `cell = 0`）。
    #   cell 是 ①切cell 的**机械产物**（原文区间），不是记忆判断 ⇒ 它进图不改变
    #   "记住了什么"；可缺了它，**以后任何要引用那一场原文的东西都做不到**
    #   （件的成员是 cell；cell 不在，那段原文就永远锚不上）。
    #   ⇒ 机械归机械：① 在 `完成` 时无条件落图，②③⑤④ 才决定"记成什么"。
    for c in (草案.get("cell") or []):
        图.加cell(str(c.get("场id") or 场id), int(c.get("轮") or 0),
                 int(c.get("起") or 0), int(c.get("止") or 0))

    # ★★ `[10]` 的两个输出分别落：**(a) 归进已有件** · **(b) 新建** —— 而 (b) 里**同组的合成一个件**。
    #   真机第 1 场实测：不做 (b) 的话 5 个标记各自新建 ⇒ 每件只有一个 cell
    #   ⇒ "一件 ≠ 一件事"，组内 α / 组间 β 全退化。
    判s = list(草案.get("判") or [])
    组池: Dict[str, List[Dict]] = {}
    for p in 判s:
        if p.get("归进"):
            continue
        # 没有组号的（分堆失败 / 只有一处）⇒ 按轮号自成一组
        组池.setdefault(str(p.get("组", "u%d" % int(p["轮"]))), []).append(p)

    for p in 判s:
        轮 = int(p["轮"])
        cell = (图.按区间找cell(场id, 轮)
                or 图.加cell(场id, 轮, int(p.get("起") or 0), int(p.get("止") or 0)))
        if not p.get("归进"):
            continue
        vec = p.get("向量") or []
        件id = p["归进"]
        图.归进(件id, cell["id"], vec)
        # ⚠️ 「冷成员被拉起」这一条**暂时传空**：标记集 的键是**件**，而「成员」是 **cell**，
        #    两者不是一个东西。等做出「多 cell 一组的层级」（组/件分层）再把 cell 级置信度
        #    引进来；markset.强化 的能力保留，并在自检里用件 id 直接验过。
        markset.强化(图.标记集, 归进的件=件id)
        层1.append({"轮": 轮, "动作": "归进", "件": 件id})

    for 组号, ps in 组池.items():
        ids: List[str] = []
        vec首: List[float] = []
        for p in ps:
            轮 = int(p["轮"])
            cell = (图.按区间找cell(场id, 轮)
                    or 图.加cell(场id, 轮, int(p.get("起") or 0), int(p.get("止") or 0)))
            ids.append(cell["id"])
            if not vec首:
                vec首 = list(p.get("向量") or [])
        件id = 图.新件(ids, vec首, 代表=ids[0])
        markset.强化(图.标记集, 新建的件=件id)
        for p in ps:
            层1.append({"轮": int(p["轮"]), "动作": "新建", "件": 件id,
                        "本件成员数": len(ids)})

    # ---- 块（多归属）：【二】同类 /【三】同情绪 —— 和"是不是同一件事"**分开**
    #   ★★ 2026-10-17（作者定 A）：两件事可以**不是同一件、却是同一类**（"猫的两件事"）。
    #      只靠"件"必然二选一 ⇒ 所以要有这一层。每个视角**只一张网**（连通块）。
    轮件 = {int(x["轮"]): x["件"] for x in 层1}
    for p in 判s:
        本 = 轮件.get(int(p["轮"]))
        if not 本:
            continue
        for 边 in (p.get("连") or []):
            他 = str(边.get("件") or "")
            因 = str(边.get("因为") or "").strip()
            if 因 and 他 in 图.件 and 他 != 本:
                图.并块(因, [本, 他], 说法=str(p.get("说法") or "")[:60])

    # ④ 层2：一场 = 一个窗口
    旧 = dict(图.标记集)
    新, 剪出 = markset.衰减(旧, 窗口数=1)
    图.标记集 = 新
    for i in 剪出:
        图.剪出(i, "衰减到门槛以下")

    # ★ `判/` **总是**落盘（不是可选项）：温度 >0 ⇒ 重跑拿不到同一份
    _记判(记判目录 or os.path.join(图.cache_dir, "判"), 草案)

    图.版本 += 1
    图.落盘()

    # ★★ `版本对`（`流程框架 §3.1.3`：**存储 · 一场一次归档** · 必填）。
    #   为什么必须有：没有它，**"变了"和"记录错了"分不开**（`设计 §10`）。
    #   ⛔ 不写时间戳：冻结、可比较 —— 同一份内容每次写出来要一样。
    _记版本对(图.cache_dir, {
        "场id": 场id, "图版本": int(图.版本),
        "读法版本": int(K版本["读法版本"]["值"]),
        "指纹": dict(草案.get("指纹") or {}),
    })
    return {"场id": 场id, "版本": 图.版本, "层1": 层1,
            "层2": {"衰减": 旧, "现在": dict(图.标记集), "剪出": 剪出},
            "摘要": 草案.get("摘要") or ""}


def _记版本对(目录: str, 行: Dict) -> None:
    """`版本对.jsonl`：一场一次归档写一行（**只追加**，`§3.1.5` 铁律一）。"""
    try:
        os.makedirs(目录, exist_ok=True)
        with io.open(os.path.join(目录, "版本对.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(行, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:                                        # noqa: BLE001
        # ⛔ 写不了版本对不许把一次归档弄挂 —— 但也不静默：调用方有 `判/` 可比对
        pass


def _记判(目录: str, 草案: Dict) -> None:
    """`判/`：⑤ 的每一次判（输入 → 输出 → 说法）。⛔ 模型生成过的东西必须落盘。"""
    try:
        os.makedirs(目录, exist_ok=True)
        p = os.path.join(目录, str(草案["场id"]) + ".jsonl")
        with io.open(p, "a", encoding="utf-8") as fh:
            for one in (草案.get("判") or []):
                行 = {k: v for k, v in one.items() if k != "向量"}
                行["时间"] = time.time()
                fh.write(json.dumps(行, ensure_ascii=False) + "\n")
    except Exception:                            # noqa: BLE001
        pass          # ⛔ 记数据失败不许打断归档
