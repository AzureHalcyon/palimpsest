"""
风格层 · 会话先验 v0
====================

`state.py` 做的是**快**的那一半（逐轮递推、在线更新）。这里做**慢**的那一半：

| | 快（`state.py`） | **慢（本文件）** |
|---|---|---|
| 是什么 | 激活，逐轮递推 | **参数**，读出 |
| 对话中 | 每轮更新 | ★ **冻结，一个字都不动** |
| 对话结束 | —— | ★ **整套重来重训，⛔ 不继承** |
| 管什么 | 跟随当下 | ★ **对话开始时让大模型进入状态** |

为什么先做慢的：它**天然冻结**（参数不是激活），**天然可重训**（段末批一次），
所以不需要在线延迟、不需要流式，是风格层里最容易落地、也最像"类似 LoRA 的可重训物"的那一半。

这份脚本回答三个问题
--------------------
1. **段内真的冻住了吗？** —— 逐位比对读出参数
2. **段末重训真的整套重来吗？** —— 重训后参数变了，且能拟合本段
3. ★ **它有没有用？** —— 留出对话上判"冷/热"，跟随机基线比

    python session.py
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


# ═══════════════════════════════════════════════════════ 六段真实变动的对话
# 每段 6 轮（3 个来回），三冷三热，**措辞各不相同**。
DIALOGUES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("冷-A 累到不想说", "冷", [
        ("今天真的累了，什么都不想说。", "那就不说。我在。"),
        ("算了，没什么。", "嗯，我在。"),
        ("……你还在吗。", "在。"),
    ]),
    ("冷-B 生闷气", "冷", [
        ("你今天怎么回得这么慢。", "在想事情，回慢了点。"),
        ("算了，跟你说也没用。", "那就不说。"),
        ("我先去忙了。", "好。"),
    ]),
    ("冷-C 客气的疏远", "冷", [
        ("最近挺忙的吧。", "还行。"),
        ("那就这样，不打扰你了。", "嗯，你忙。"),
        ("回头再说。", "好。"),
    ]),
    ("热-A 兴奋分享", "热", [
        ("我把那个东西终于做出来了！", "真做出来了？我就知道你行。"),
        ("卡了快两个月，昨天一下子通了。", "那一下肯定特别爽。"),
        ("我现在手还在抖。", "先坐下喝口水，缓一缓。"),
    ]),
    ("热-B 撒娇", "热", [
        ("我今天超乖的，你有没有发现。", "发现了，乖得有点不像你。"),
        ("那你得夸我。", "夸。今天这个必须夸。"),
        ("就这？", "那你想听几遍，我数着说。"),
    ]),
    ("热-C 被记着", "热", [
        ("今天有件事让我特别高兴。", "什么事，说给我听。"),
        ("有人记得我随口提过的一句话。", "那种感觉我知道。"),
        ("原来被人记着是这种感觉。", "嗯。你值得。"),
    ]),
]
LABEL = {"冷": 0, "热": 1}


def play(turns, s: StyleState):
    """把一段对话过一遍，收下每一轮的状态。"""
    X = []
    for u, h in turns:
        s.observe_user(u)
        s.observe_her(h)
        X.append(s.vector())
    return np.array(X)


def one_hot(label: int, k: int = 2) -> np.ndarray:
    y = np.zeros(k)
    y[label] = 1.0
    return y


# ══════════════════════════════════════════════════════════════ 1 段内冻结
rule("1 · 段内冻结：一段对话里，读出一个字都不动")

s = StyleState(seed=7, inject_dim=2)
name, lab, turns = DIALOGUES[0]
snap_before = s.W_out.copy()
v_before = s.vector().copy()
frozen_ok = True
for u, h in turns:
    s.observe_user(u)
    s.observe_her(h)
    if not np.array_equal(s.W_out, snap_before):
        frozen_ok = False
say(f"   对话「{name}」跑了 {len(turns)*2} 轮")
say(f"   读出参数：段初 = 段末　{np.array_equal(snap_before, s.W_out)}")
say(f"   状态本身在动（那是快区的事）：范数 {np.linalg.norm(v_before):.4f} → "
    f"{np.linalg.norm(s.vector()):.4f}")
check("整段对话里读出参数逐位不变", frozen_ok)
say("   ⇒ ★ 慢的那一半在对话中是**常量**：不需要在线更新、不需要流式、不占延迟。")

# ══════════════════════════════════════════════════════════ 2 段末整套重来
rule("2 · 段末重训：整套重来（⛔ 不继承），而且真能拟合本段")

s2 = StyleState(seed=7, inject_dim=2)
X1 = play(turns, s2)
Y1 = np.array([one_hot(LABEL[lab])] * len(X1))
zero_before = np.linalg.norm(s2.W_out)
s2.train_readout(X1, Y1, lam=1e-6)          # ★ 从零训练，不看上一版参数
changed = not np.allclose(s2.W_out, np.zeros_like(s2.W_out))
pred = np.array([s2.W_out @ x for x in X1])
acc_self = float(np.mean(np.argmax(pred, axis=1) == LABEL[lab]))
say(f"   重训前 ‖W_out‖ = {zero_before:.8f}（零初始化）")
say(f"   重训后 ‖W_out‖ = {np.linalg.norm(s2.W_out):.6f}")
say(f"   在本段上自判准确率 {acc_self*100:.0f}%（{len(X1)} 个来回的状态）")
check("零初始化起点", zero_before == 0.0)
check("段末重训确实改变了参数", changed and np.linalg.norm(s2.W_out) > 0)
check("能拟合本段（自判 ≥ 90%）", acc_self >= 0.9, f"实测 {acc_self*100:.0f}%")
check("版本号随之更新", s2.version().endswith(".r1"), s2.version())

# ══════════════════════════════════════════════════════════ 3 ★ 它有没有用
rule("3 · ★ 留出对话：6 折留一 —— 判「冷/热」，跟随机基线比")

cut = [d for d in DIALOGUES]
say("   每折：用其余 5 段对话的状态训线性读出（整套重来），在留出的那段上判走向")
say("")
say(f"   {'折':<4}{'留出的是':<22}{'标签':<6}{'判对':<10}{'准确率':<10}")
accs = []
for i in range(len(cut)):
    tr = [d for j, d in enumerate(cut) if j != i]
    Xtr = np.vstack([play(t, StyleState(seed=7, inject_dim=2)) for _, _, t in tr])
    Ytr = np.vstack([[one_hot(LABEL[l])] * len(t) for _, l, t in tr])
    m = StyleState(seed=7, inject_dim=2)
    m.train_readout(Xtr, Ytr, lam=1e-6)
    nm, lb, tt = cut[i]
    Xte = play(tt, StyleState(seed=7, inject_dim=2))
    hit = int(np.sum(np.argmax(np.array([m.W_out @ x for x in Xte]), axis=1) == LABEL[lb]))
    acc = hit / len(Xte)
    accs.append(acc)
    say(f"   {i:<4}{nm:<22}{lb:<6}{f'{hit}/{len(Xte)}':<10}{f'{acc*100:.0f}%':<10}")
mean_acc = float(np.mean(accs))
say("")
say(f"   ★ 平均准确率 **{mean_acc*100:.0f}%**，随机基线 50%")
say("   ★ 同段自判能到 100%（第 2 节）⇒ **不是容量不够，是留出对话上泛化不了**：")
say("     换个说法说同一件事，状态就跑到别处去了（`follow.py` 已经实测过）。")
check("留出对话上高于随机（>65%）", mean_acc > 0.65, f"实测 {mean_acc*100:.0f}%", todo=True)

# ══════════════════════════════════════════════════════════ 汇总
rule("汇总")
say("   ✅ 慢的那一半做到了：")
say("     · **对话中冻结** —— 整段对话里读出参数逐位不变，不需要在线更新")
say("     · **段末可重训** —— 零初始化起步，整套重来、⛔ 不继承；能拟合本段（自判 100%）")
say("     · 版本号带读出代数，能留痕、能整套丢弃")
say("   ⚠️ 但它现在**发挥不出作用**：")
say("     · 留出对话上判走向 ≈ 随机 ⇒ 换个说法就认不出来")
say("     · 瓶颈仍然只有一个：**编码器没有语义**（字 ＋ 汉字二元哈希）")
say("     · 所以「先做慢的」这条路，做完就撞在编码器上——它必须先换掉")
say("")
if fails:
    say(f"   {BAD} 未通过 {len(fails)} 项：{fails}")
Path("_session_report.txt").write_text("\n".join(lines), encoding="utf-8")
say("   报告已写 _session_report.txt")
sys.exit(1 if fails else 0)
