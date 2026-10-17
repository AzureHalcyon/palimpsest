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
from .film import 切回原文, 提示词哈希
from .graph import 图
from .knobs import 件 as K件, 标记 as K标记, 判 as K判

# --------------------------------------------------------------------------- ⑤ 的提示词（新线，尚未冻结）

判提示词 = """下面是这一场里被标出来的一处（它比上下文更"意外"，所以值得记）：

{这一处}

你记忆里已经有的几件（挑最像的看，可能都不像）：
{候选}

判断这一处该并进已有的某一件，还是**单独建一件**。
只回 JSON，不要别的话：
{{"归进": "件-0003" 或 null, "新建": true 或 false, "说法": "一句话，为什么"}}

· 归进与新建**只能选一个**；
· 候选里没有真正同一件事的就新建 —— ⛔ 别硬塞进去；
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
    """`②`：每轮一个数 —— 从**那一轮最后一次调用的 `logprobs`** 算 `−mean(logprob)`。

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
         其他底片: Optional[Dict[str, Dict]] = None, 进度: Optional[Callable] = None) -> Dict:
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
    序列 = 误差序列(底片数据)
    标 = 标记(序列)

    # ---- ③ 配对（候选件）＋ ⑤ 判
    候选表: Dict[int, List[tuple]] = {}
    判表: List[Dict] = []
    向量表 = 图.件向量表()
    for m in 标:
        轮 = int(m["轮"])
        文本 = _这一处的原文(底片数据, 轮)
        vec: List[float] = []
        if 嵌入 is not None and 文本.strip():
            try:
                vec = list((嵌入([文本]) or [[]])[0])
            except Exception:                    # noqa: BLE001
                vec = []
        候选 = markset.提候选件(vec, 向量表, 图.标记集, k=int(K判["候选件数"]["值"])) if vec else []
        候选表[轮] = 候选
        if not 候选:
            # ★ 第一批归档时标记集是空的 ⇒ 必然没候选 ⇒ **不问模型**，直接新建
            判表.append({"轮": 轮, "归进": None, "新建": True,
                       "说法": "没有候选件（标记集里还没有东西）", "向量": vec,
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
        if 归进 and 归进 not in 图.件:
            归进, 新建 = None, True                  # 它报了一个不存在的件 ⇒ 当新建（并留痕）
            说法 = (说法 + "（它报的件不存在，按新建）").strip()
        if not 归进 and not 新建:
            新建 = True                              # 两样都没给 ⇒ 不许丢
            说法 = (说法 + "（没给归进也没说新建，按新建）").strip()
        判表.append({"轮": 轮, "归进": 归进, "新建": bool(新建), "说法": 说法,
                     "向量": vec, "提示词哈希": 提示词哈希(提示),
                     "候选": [c[0] for c in 候选],
                     "起": _区间(草案cell, 轮)[0], "止": _区间(草案cell, 轮)[1]})

    return {"场id": str(场id), "cell": 草案cell, "误差": 序列, "标记": 标,
            "候选": 候选表, "判": 判表,
            "摘要": _摘要(标, 判表)}


def _摘要(标: List[Dict], 判表: List[Dict]) -> str:
    if not 标:
        return "这一场没有值得记的地方（没有标记）。"
    行 = ["这一场认出 %d 处：" % len(标)]
    for p in 判表:
        if p.get("归进"):
            行.append("- 第 %d 轮 → 归进 %s" % (p["轮"], p["归进"]))
        else:
            行.append("- 第 %d 轮 → 新建一件" % p["轮"])
    return "\n".join(行)


def 完成(图: 图, 草案: Dict, 记判目录: Optional[str] = None) -> Dict:
    """**这一下才写**：`④ 层1`（强化/新建/冷成员）＋ `④ 层2`（衰减/剪出）＋ 版本 +1 + 落盘。"""
    from .knobs import 件 as K

    层1: List[Dict] = []
    for p in (草案.get("判") or []):
        轮 = int(p["轮"])
        cell = (图.按区间找cell(草案["场id"], 轮)
                or 图.加cell(草案["场id"], 轮, int(p.get("起") or 0), int(p.get("止") or 0)))
        vec = p.get("向量") or []
        if p.get("归进"):
            件id = p["归进"]
            图.归进(件id, cell["id"], vec)
            # ⚠️ 「冷成员被拉起」这一条**暂时传空**：标记集 的键是**件**，而「成员」是 **cell**，
            #    两者不是一个东西。等做出「多 cell 一组的层级」（组/件分层）再把 cell 级置信度
            #    引进来；markset.强化 的能力保留，并在自检里用件 id 直接验过。
            markset.强化(图.标记集, 归进的件=件id)
            层1.append({"轮": 轮, "动作": "归进", "件": 件id})
        else:
            件id = 图.新件([cell["id"]], vec, 代表=cell["id"])
            markset.强化(图.标记集, 新建的件=件id)
            层1.append({"轮": 轮, "动作": "新建", "件": 件id})

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
    return {"场id": 草案["场id"], "版本": 图.版本, "层1": 层1,
            "层2": {"衰减": 旧, "现在": dict(图.标记集), "剪出": 剪出},
            "摘要": 草案.get("摘要") or ""}


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
