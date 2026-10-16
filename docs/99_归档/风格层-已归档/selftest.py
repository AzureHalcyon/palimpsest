"""
状态块 v0 · 自检
================

只读状态块自己的性质，⛔ 不碰大模型、⛔ 不碰 API。跑的是一串真数字，不是论证。

    python selftest.py
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


def check(name: str, cond: bool, detail: str = "") -> None:
    say(f"   {OK if cond else BAD} {name}" + (f"　{detail}" if detail else ""))
    if not cond:
        fails.append(name)


def rule(t: str) -> None:
    say("")
    say("=" * 78)
    say(t)
    say("=" * 78)


np.set_printoptions(suppress=True)

# ══════════════════════════════════════════════════════════════════ 1 多尺度
rule("1 · 多尺度：各层的时间常数与实测记忆长度")

s = StyleState(seed=7)
say(f"   结构：{s.levels} 层 × {s.dim} 维，种子 {s.seed}，版本 {s.version()}")

T = 20000
s.observe_user("今天真的累了，什么都不想说。")
traj = np.zeros((T, s.levels))
for step in range(T):
    s._step("", "user")                        # 空输入 ⇒ 只剩泄漏与层间驱动
    traj[step] = [float(np.linalg.norm(s.h[k])) for k in range(s.levels)]

run_max = np.maximum.accumulate(traj, axis=0)  # 用行走最大值当基准，避免"先升后降"的层测错
half: list[int] = []
for k in range(s.levels):
    idx = np.where(traj[:, k] < 0.5 * run_max[:, k])[0]
    half.append(int(idx[0]) + 1 if len(idx) else -1)

say("")
say(f"   {'层':<4}{'α（泄漏率）':<13}{'名义 1/α':<10}{'实测半衰期(步)':<15}{'≈几段对话':<11}{'相邻倍数':<10}")
for k in range(s.levels):
    ratio = f"{half[k] / half[k-1]:.2f}×" if k and half[k] > 0 and half[k - 1] > 0 else "—"
    say(f"   {k:<4}{s.alphas[k]:<13.4g}{1/s.alphas[k]:<10.0f}{half[k]:<15}{half[k]/40:<11.1f}{ratio:<10}")
say("")
say("   （「几段对话」＝ 半衰期 ÷ 40 步；一段对话按 20 个来回估算）")
span = half[-1] / max(1, half[0])
say("")
say(f"   ★ 最快与最慢相差 **{span:.0f} 倍** ⇒ 快区/慢区分得开，且⛔ 没手列任何情绪分量")
say("      （慢区跨对话留存 ⇒ 管「对话开始时让大模型进入状态」；快区跟随当下 ⇒ 管「跟随聊天演化」）")
check("层数 ≥ 5", s.levels >= 5)
check("实测半衰期逐层递增", all(half[i] > half[i - 1] for i in range(1, s.levels)))
check("相邻层至少差 2 倍", all(half[i] >= 2 * half[i - 1] for i in range(1, s.levels)))
check("快慢跨度 ≥ 100 倍", span >= 100, f"实测 {span:.0f} 倍")

# ══════════════════════════════════════════════════════════════════ 2 跨对话留存
rule("2 · 跨对话留存：状态原样带过对话边界；而且带过去的是慢区")

NEUTRAL = "嗯，今天也就这样。"
EPISODE = "算了，没什么。"

s = StyleState(seed=7)
for _ in range(12000):                 # 先跑到中性基线（最慢那层要 ~7 个半衰期才稳住）
    s.observe_user(NEUTRAL)
base = s.h.copy()
say("   中性基线跑满 12000 轮，各层范数：" +
    "  ".join(f"{np.linalg.norm(base[k]):.3f}" for k in range(s.levels)))

for _ in range(300):                   # 一段持续的冷淡
    s.observe_user(EPISODE)
after_ep = s.h.copy()
peak = [float(np.linalg.norm(after_ep[k] - base[k]) / max(1e-12, np.linalg.norm(base[k])))
        for k in range(s.levels)]
say("   喂 300 轮「算了，没什么。」，各层偏离中性基线的幅度：" +
    "  ".join(f"{v:.3f}" for v in peak))

s.save("_state_roundtrip.npz")
s2 = StyleState.load("_state_roundtrip.npz")
s2.new_session()
flat_a, flat_b = after_ep.reshape(-1), s2.h.reshape(-1)
cos = float(flat_a @ flat_b / (np.linalg.norm(flat_a) * np.linalg.norm(flat_b)))
say(f"   ★ 存盘→读回：逐位相同 = {np.array_equal(after_ep, s2.h)}；换对话后余弦 = {cos:.6f}")
check("存盘→读回逐位一致", np.array_equal(after_ep, s2.h))
check("换对话后状态原样留存", abs(cos - 1.0) < 1e-12)

for _ in range(100):                   # 换回中性，看哪一层把那段冷淡留下了
    s2.observe_user(NEUTRAL)
after = [float(np.linalg.norm(s2.h[k] - base[k]) / max(1e-12, np.linalg.norm(base[k])))
         for k in range(s2.levels)]
say("   再喂 100 轮中性的话之后，各层还剩多少：" +
    "  ".join(f"{v:.3f}" for v in after))
say("   保留率（＞100% ＝ 那一层其实还没跟上，还在慢慢爬）：" +
    "  ".join(f"层{k}={after[k]/max(1e-12,peak[k])*100:.0f}%"
              for k in range(s2.levels)))
say("   ⚠️ 注意层4 的 peak 只有 0.09（300 轮下来它才动了这么点）——")
say("      它⛔ 不是「记住这一段」的那一层，它是「很久以来的底色」；这一段归记忆层。")
check("快区已经把那段换掉了", after[0] / max(1e-12, peak[0]) < 0.05,
      f"层0 剩 {after[0]/max(1e-12,peak[0])*100:.1f}%")
check("慢区把那段留住了", after[-1] / max(1e-12, peak[-1]) > 0.5,
      f"层4 剩 {after[-1]/max(1e-12,peak[-1])*100:.1f}%")
check("越慢越留得住", after[-1] > after[0] * 5, f"层4 {after[-1]:.3f} vs 层0 {after[0]:.3f}")
Path("_state_roundtrip.npz").unlink(missing_ok=True)

# ══════════════════════════════════════════════════════════════════ 3 轮内冻结
rule("3 · 轮内冻结：一轮之内 ⛔ 不许再更新（loop 多跳共享同一份）")

s = StyleState(seed=1)
s.observe_user("在想。")
s.freeze()
h_before = s.h.copy()
blocked = False
try:
    s.observe_her("嗯。")
except RuntimeError as e:
    blocked = True
    say(f"   冻结期间更新 → {type(e).__name__}: {e}")
check("冻结期间更新被拒", blocked)
check("冻结期间状态一字未动", np.array_equal(h_before, s.h))
s.unfreeze()
s.observe_her("嗯。")
check("解冻后可以继续", not np.array_equal(h_before, s.h))

# ══════════════════════════════════════════════════════════════════ 4 只吃真实交互
rule("4 · 只吃真实发生过的：接口只开两个口子")

api = [n for n in dir(StyleState) if not n.startswith("_")]
entry = sorted(n for n in api if n.startswith("observe_"))
say(f"   更新入口：{entry}")
say("   ⛔ 没有 observe_prefill / observe_cot —— 结构上就吃不到没发生的东西")
check("更新入口恰好两个且是 user/her", entry == ["observe_her", "observe_user"])

# ══════════════════════════════════════════════════════════════════ 5 确定性
rule("5 · 确定性：同种子同输入 ⇒ 逐位相同（留痕/可复现的前提）")

a, b = StyleState(seed=42), StyleState(seed=42)
for txt in ["第一句", "第二句", "第三句"]:
    a.observe_user(txt)
    b.observe_user(txt)
c = StyleState(seed=43)
for txt in ["第一句", "第二句", "第三句"]:
    c.observe_user(txt)
say(f"   版本号：{a.version()}")
check("两次逐位相同", np.array_equal(a.h, b.h))
check("换种子结果不同", not np.array_equal(a.h, c.h))

# ══════════════════════════════════════════════════════════════════ 6 稳定性
rule("6 · 稳定性：恒定输入下增量衰减、范数有界，⛔ 不发散（人格不漂）")

s = StyleState(seed=3)
marks, norms = [], []
for i in range(6000):
    s.observe_user("嗯。")
    if (i + 1) % 600 == 0:
        norms.append(float(np.linalg.norm(s.h)))
        marks.append(i + 1)
deltas = [norms[0]] + [norms[i] - norms[i - 1] for i in range(1, len(norms))]
say("   每 600 步的状态范数：" + " → ".join(f"{n:.2f}" for n in norms))
say(f"   末段增量 {deltas[-1]:+.5f}，首段增量 {deltas[1]:+.5f}")
check("范数有界（不发散）", max(norms) < 10 * s.dim ** 0.5, f"最大 {max(norms):.2f}")
check("增量在衰减（趋向稳态）", abs(deltas[-1]) < 0.1 * abs(deltas[1]),
      f"{abs(deltas[-1]):.5f} < {0.1*abs(deltas[1]):.5f}")
driven = s

# ══════════════════════════════════════════════════════════════════ 7 零初始化与消融
rule("7 · 零初始化 ⇒ 初始等价于不注入；消融开关必须能让它归零")

s = StyleState(seed=5)
s.observe_user("随便说点什么。")
say(f"   未训读出：|injection| = {np.linalg.norm(s.injection()):.8f}")
check("未训时注入恒为零", np.linalg.norm(s.injection()) == 0.0)
s.disable()
check("拔掉之后也恒为零", np.linalg.norm(s.injection()) == 0.0)
s.enable()

# ══════════════════════════════════════════════════════════════════ 8 读出可训
rule("8 · 读出可训：唯一可训的东西，而且很便宜（岭回归级）")

s = StyleState(seed=11)
X, Y = [], []
for i in range(60):
    s.observe_user(f"第 {i} 句，说点不一样的东西。")
    X.append(s.vector())
    y = np.zeros(s.inject_dim)
    y[i % s.inject_dim] = 1.0
    Y.append(y)
X, Y = np.array(X), np.array(Y)
s.train_readout(X, Y, lam=1e-6)
pred = np.array([s.W_out @ x for x in X])
err = float(np.mean((pred - Y) ** 2))
base = float(np.mean(Y ** 2))
say(f"   训在 {len(X)} 个样本上：读出均方误差 {err:.4f}（未训时是 {base:.4f}）")
say(f"   版本号随之更新 → {s.version()}（r 后缀是读出代数）")
check("训完注入非零", np.linalg.norm(s.injection()) > 0)
check("读出确实学到了（误差降到 10% 以下）", err < base * 0.1)

# ══════════════════════════════════════════════════════════════════ 汇总
rule("汇总")
say(f"   （拿跑过 6000 步的那个实例当样本）")
for k, v in driven.summary().items():
    say(f"   {k:<16}{v}")
say("")
if fails:
    say(f"   {BAD} 未通过 {len(fails)} 项：{fails}")
    Path("_selftest_report.txt").write_text("\n".join(lines), encoding="utf-8")
    sys.exit(1)
say(f"   {OK} 全部通过")
Path("_selftest_report.txt").write_text("\n".join(lines), encoding="utf-8")
say("   报告已写 _selftest_report.txt")
