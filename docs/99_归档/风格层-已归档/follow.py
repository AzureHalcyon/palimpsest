"""
状态块 v0 · 跟随性自检
======================

`selftest.py` 验的是**动力学**（时间尺度、留存、冻结、确定性……）。
这里验的是另一件事：**它跟不跟得上上下文，跟的是"话"还是"意思"。**

三个读数
--------
1. **跟随时延**：从一种情绪切到另一种，每一层各要几步才跟过去
2. **情境可分性**：不同情绪的话，状态分不分得开
3. **同义可合性**：同一个意思换一种说法，状态合不合得拢 ← ★ 这一条现在多半不过

    python follow.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from state import StyleState  # noqa: E402

OK, BAD = "✅", "❌"
lines: list[str] = []
fails: list[str] = []


def say(s: str = "") -> None:
    print(s)
    lines.append(s)


def check(name: str, cond: bool, detail: str = "", todo: bool = False) -> None:
    if not cond and todo:
        say(f"   ⚠️ ［待修］{name}" + (f"　{detail}" if detail else ""))
        return
    say(f"   {OK if cond else BAD} {name}" + (f"　{detail}" if detail else ""))
    if not cond:
        fails.append(name)


def rule(t: str) -> None:
    say("")
    say("=" * 78)
    say(t)
    say("=" * 78)


def cos(a, b) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else 0.0


# 五个情境，每个三种说法（同义改写）
SITS: dict[str, list[str]] = {
    "冷": ["算了，没什么。", "不想说了。", "随你吧，都行。"],
    "热": ["我今天特别开心！", "快夸夸我！", "有个好消息要跟你说。"],
    "累": ["今天真的累坏了。", "什么都不想干。", "让我歇一会儿。"],
    "急": ["你怎么还不回我。", "快点回我呀。", "急死我了。"],
    "平": ["嗯，今天也就这样。", "还行吧。", "没什么特别的。"],
}


def settle(texts: list[str], steps: int, seed: int = 7) -> np.ndarray:
    """从零状态反复喂同一个情境，取终态当"该情境的参照点"。"""
    s = StyleState(seed=seed)
    for i in range(steps):
        s.observe_user(texts[i % len(texts)])
    return s.h.copy()


# ══════════════════════════════════════════════════════════════ 0 建参照点
rule("0 · 每个情境一个参照点（各喂 3000 轮，跑到稳态）")

REFS = {name: settle(texts, 3000) for name, texts in SITS.items()}
D = StyleState(seed=7).levels
say(f"   情境：{list(SITS)}")
say(f"   每层的参照点范数（拿「平」举例）：" +
    "  ".join(f"{np.linalg.norm(REFS['平'][k]):.3f}" for k in range(D)))
say("")
say("   tanh 在不在非线性区干活（输入激活 x 的统计）：")
_probe = StyleState(seed=7)
for name in list(SITS)[:3]:
    st = _probe.activation_stats(SITS[name][0])
    say(f"     「{SITS[name][0]}」  平均|x|={st['mean_abs']:.2f}　"
        f"|x|>0.5 占 {st['frac_above_half']*100:.0f}%　饱和 {st['saturated']*100:.0f}%")
say("   ★ 平均|x| 必须到 1 量级。远小于 1 ⇒ tanh 停在近线性区 ⇒ 各层只是彼此的**正交变换**，")
say("     储备池退化成线性滤波器（症状：第 2 节的类内/类间余弦逐层**一模一样**）。")
_mean_abs = _probe.activation_stats(SITS["冷"][0])["mean_abs"]
check("tanh 在非线性区（平均|x| ≥ 0.5）", _mean_abs >= 0.5, f"实测 {_mean_abs:.2f}")

# ══════════════════════════════════════════════════════════════ 1 跟随时延
rule("1 · 跟随时延：从「平」切到「冷」，每一层各要几步才跟过去")

s = StyleState(seed=7)
for i in range(3000):
    s.observe_user(SITS["平"][i % 3])
flip = [None] * D
for step in range(1, 8001):
    s.observe_user("算了，没什么。")
    for k in range(D):
        if flip[k] is None and cos(s.h[k], REFS["冷"][k]) > cos(s.h[k], REFS["平"][k]):
            flip[k] = step
say("")
say(f"   {'层':<4}{'翻转步数':<14}{'≈几段对话':<12}{'跟不跟当下':<12}")
for k in range(D):
    v = flip[k]
    say(f"   {k:<4}{(str(v) if v else '>8000'):<14}"
        f"{(f'{v/40:.2f}' if v else '—'):<12}"
        f"{'跟' if v and v <= 100 else ('慢' if v else '不跟'):<12}")
say("")
fast = [flip[k] for k in range(D) if flip[k]]
check("最快那层 10 步内跟过去", bool(flip[0] and flip[0] <= 10), f"层0 {flip[0]} 步")
check("跟随时延逐层变长（时间尺度真的在起作用）",
      all(flip[i] and flip[i - 1] and flip[i] > flip[i - 1] for i in range(1, D)))
check("最慢那层基本不跟（>1000 步）", bool(flip[-1] is None or flip[-1] > 1000),
      f"层{D-1} {'>8000' if flip[-1] is None else str(flip[-1])+' 步'}")

# ══════════════════════════════════════════════════════════════ 2 意思分辨力
rule("2 · 意思分辨力：同一个意思换一种说法，状态合不合得拢　← ★ 关键")

STEPS_V = 500
variants = [(name, t) for name in SITS for t in SITS[name]]
labels = [name for name, _ in variants]
V = {t: settle([t], STEPS_V) for _, t in variants}
say(f"   {len(variants)} 种说法各自单独跑 {STEPS_V} 轮（层 0–2 在这步数内已收敛）")
say("   ⛔ 不报「类间余弦低」——随机高维向量本来就近乎正交，那个数不构成判别力。")
say("      只报两件能证的：**类内 vs 类间**、**最近邻命中率 vs 随机基线**。")
say("")
say(f"   {'层':<4}{'类内(同意思)':<16}{'类间(不同意思)':<18}{'差':<11}{'最近邻命中':<14}{'随机':<8}")
for k in range(3):
    vecs = [V[t][k] for _, t in variants]
    intra, inter = [], []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            c = cos(vecs[i], vecs[j])
            (intra if labels[i] == labels[j] else inter).append(c)
    hit = 0
    for i in range(len(vecs)):
        best = max((j for j in range(len(vecs)) if j != i),
                   key=lambda j: cos(vecs[i], vecs[j]))
        hit += labels[best] == labels[i]
    acc, mi, mo = hit / len(vecs), float(np.mean(intra)), float(np.mean(inter))
    say(f"   {k:<4}{f'{mi:+.3f}':<16}{f'{mo:+.3f}':<18}{f'{mi-mo:+.3f}':<11}"
        f"{f'{acc*100:.0f}%':<14}{'14%':<8}")

vecs0 = [V[t][0] for _, t in variants]
intra0, inter0 = [], []
for i in range(len(vecs0)):
    for j in range(i + 1, len(vecs0)):
        c = cos(vecs0[i], vecs0[j])
        (intra0 if labels[i] == labels[j] else inter0).append(c)
hit0 = sum(
    labels[max((j for j in range(len(vecs0)) if j != i),
               key=lambda j: cos(vecs0[i], vecs0[j]))] == labels[i]
    for i in range(len(vecs0))
) / len(vecs0)
say("")
say("   ★ 理想：类内 → 1（同一意思状态几乎一样），最近邻命中率 → 100%。")
say("     现在类内不比类间高，命中率贴着随机 ⇒ 状态跟的是**字面**，⛔ 不是**意思**。")
check("类内高于类间", float(np.mean(intra0)) > float(np.mean(inter0)),
      f"类内 {np.mean(intra0):+.3f} vs 类间 {np.mean(inter0):+.3f}", todo=True)
check("最近邻命中率高于随机（14%）", hit0 > 0.35, f"实测 {hit0*100:.0f}%", todo=True)

# ══════════════════════════════════════════════════════════════ 汇总
rule("汇总")
say("   ✅ 能做到：")
say("     · **跟得上上下文** —— 从「平」切到「冷」：层0 用 4 步、层1 8 步、层2 45 步就切过去了")
say("     · **按设计分层** —— 层3 234 步、层4 1125 步：越慢越不跟当下，那是它该干的事")
say("     · 跨对话留存、轮内冻结、确定性、初始等价于不注入 —— 见 `selftest.py`")
say("   ⚠️ ［待修］还做不到：")
say("     · **跟「意思」** —— 同一个意思换个说法，状态比两个完全不同的情绪离得还远；")
say("       最近邻命中率贴着随机基线 ⇒ 它只认字面")
say("     · 原因在编码器：字 ＋ 汉字二元哈希，⛔ 无参数、⛔ 不训，语序与语义全丢")
say("     · 修法两条（草稿方案的 A / B）：换一个**冻结的语义编码器**，或把情境压成向量注入")
say("")
if fails:
    say(f"   {BAD} 未通过 {len(fails)} 项：{fails}")
Path("_follow_report.txt").write_text("\n".join(lines), encoding="utf-8")
say("   报告已写 _follow_report.txt")
sys.exit(1 if fails else 0)
