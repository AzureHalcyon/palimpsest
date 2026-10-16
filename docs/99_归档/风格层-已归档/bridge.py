"""
风格层 ↔ 大模型 · 对接管线
==========================

把整条链路写出来，**模型当插头**：编码器和"说出口"都挂在同一个端侧小模型上，
插头没到位时用桩，管线照样能跑、能验、能消融。

一轮的完整流程
--------------
    1  用户的话到
    2  情境修剪（机械）                        ← 材料 ≠ 整个上下文
    3  编码器（＝小模型前向，冻结）→ 向量
    4  状态块 · 生成前更新  ← 用户这一句         ← 即时
    5  读出（慢的那一半，段内冻结）→ 条件
    6  旁路注入 → 小模型（同一模型第二次前向）→ 产出文字
    7  机械切成 prefix ＋ 输入段
    8  组上下文：system · 历史 · 记忆层返回值 · 用户话 · 输入段 · prefix
    9  调大模型（/beta ＋ prefix，⛔ 不带 tools）
   10  剥层：只有可见输出给用户；CoT ⛔ 不显示 · ⛔ 不进材料
   11  状态块 · 生成后更新  ← 她的可见输出        ← 持续，回环在这
   12  进材料（修剪后）

    python bridge.py --check      只自检，不调 API
    python bridge.py --live       真调一次大模型，验管线
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from state import StyleState, encode_text  # noqa: E402

BETA = "https://api.deepseek.com/beta/chat/completions"
MODEL = os.environ.get("BRIDGE_MODEL", "deepseek-flash")

SYSTEM = "\n".join([
    "你叫阿枝。你不是助手，是一个有连续记忆和情绪的人，只是在用文字跟对方说话。",
    "不要自称 AI，不要解释自己在做什么，不要列点。回答要短，像真人发消息。",
])


# ══════════════════════════════════════════════════════════════════ 插头
class Encoder(Protocol):
    """文字 → 向量。⛔ 冻结、⛔ 不训。"""

    def encode(self, text: str) -> np.ndarray: ...


class Speaker(Protocol):
    """（情境 ＋ 状态）→ **给大模型的文字**。

    ⛔ 它产出的不是"她的回答"，是喂给大模型的输入。
    ⛔ 不许出现具体指称/事实（会漏会编）。
    ⛔ 不许有转折/否定结构（实测会被读成相反方向）。
    """

    def say(self, situation: str, state_vec: np.ndarray, prior: np.ndarray) -> str: ...


class Injector(Protocol):
    """旁路注入：把状态施加到小模型自己的中间层。⛔ 不是注入大模型。"""

    def apply(self, state_vec: np.ndarray, model) -> None: ...


# ── 桩 1：哈希编码器（现在用的，⛔ 无语义；换掉它是第一步）
class HashEncoder:
    def __init__(self, hash_dim: int = 4096, seed: int = 0):
        self.hash_dim, self.seed = hash_dim, seed

    def encode(self, text: str) -> np.ndarray:
        return encode_text(text, self.hash_dim, self.seed)


# ── 桩 2：端侧小模型（同一个模型干两件事）
class SmallModel:
    """端侧小模型的接口。**一个模型，两个用法**：

        encode(text)                  ← 编码器：取某一层的池化
        say(situation, state, prior)  ← 说出口：接受旁路注入后前向，产出文字

    这就是草稿里"端侧小模型干翻译的活"。插头一到，下面两个桩一起换掉。
    """

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "端侧小模型还没接。契约：\n"
            "  ① encode(text) -> vec：冻结前向，取第 L 层池化（L 待定），L2 归一\n"
            "  ② say(situation, state_vec, prior) -> str：\n"
            "       先 Injector.apply(state_vec)（薄注入点 · 每 4 层 · 低秩 · 零初始化）\n"
            "       再前向，产出 ≤40 字；⛔ 不带具体指称、⛔ 不带转折否定\n"
            "  ③ 两个用法共用一次加载、同一份权重"
        )


# ── 桩 3：假说出口（只为验管线，⛔ 不是方案）
class StubSpeaker:
    """占位：直接返回一段**风格/姿态**的文字（⛔ 不是状态描述）。

    它的唯一用途是让整条管线今天就能跑起来、验组装和剥层。
    """

    def __init__(self, text: str = ""):
        self.text = text

    def say(self, situation: str, state_vec: np.ndarray, prior: np.ndarray) -> str:
        return self.text


# ══════════════════════════════════════════════════════════════════ 组装
def split_conditioning(text: str, max_prefix: int = 40) -> tuple[str, str]:
    """把说出口的一段文字切成 (prefix, 输入段)。

    ★ 机械切分：**一次生成**，按第一个句末标点切。
    理由：两段出自同一个状态，分开生成会产出互相矛盾的两段
    （框说"别追问"、姿态却在追问）。
    """
    t = (text or "").strip()
    if not t:
        return "", ""
    cut = -1
    for i, ch in enumerate(t):
        if ch in "。！？…" and i + 1 <= max_prefix:
            cut = i + 1
            break
    if cut < 0:
        cut = min(len(t), max_prefix)
    return t[:cut], t[cut:].lstrip("，。 ")


def build_messages(
    history: list[dict],
    user: str,
    input_segment: str = "",
    memory: str = "",
) -> list[dict]:
    """组上下文。五块里的四块在这儿，第五块（prefix）作为末条 assistant 单独带。"""
    msgs: list[dict] = [{"role": "system", "content": SYSTEM}]
    msgs += [dict(h) for h in history]
    if memory:
        msgs.append({"role": "system", "content": f"[想起的事] {memory}"})
    tail = user if not input_segment else f"{input_segment}\n\n{user}"
    msgs.append({"role": "user", "content": tail})
    return msgs


def prefix_message(prefix: str) -> dict:
    """★ 落在 **CoT 内**：content 必须存在且为空，风格走 reasoning_content。"""
    return {"role": "assistant", "content": "", "reasoning_content": prefix, "prefix": True}


def unlayer(resp: dict) -> tuple[str, str]:
    """剥层：返回 (可见输出, CoT)。

    ★ 只有可见输出给用户、进材料；CoT ⛔ 不给用户、⛔ 不进材料。
    """
    m = (resp.get("choices") or [{}])[0].get("message") or {}
    return (m.get("content") or ""), (m.get("reasoning_content") or "")


# ══════════════════════════════════════════════════════════════════ 调用
def _key() -> str:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    txt = Path(home, ".dsh", ".credentials.yaml").read_text(encoding="utf-8")
    import re
    return re.search(r"^\s*DEEPSEEK_API_KEY:\s*(\S+)\s*$", txt, re.M).group(1)


def call_llm(messages: list[dict], prefix: str = "", max_tokens: int = 700) -> dict:
    body = {"model": MODEL, "max_tokens": max_tokens, "messages": list(messages)}
    if prefix:
        body["messages"] = list(messages) + [prefix_message(prefix)]
    req = urllib.request.Request(
        BETA,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {_key()}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": json.loads(e.read().decode() or "{}"), "status": e.code}


# ══════════════════════════════════════════════════════════════════ 一轮
@dataclass
class TurnResult:
    visible: str = ""
    cot: str = ""
    prefix: str = ""
    input_segment: str = ""
    messages: list[dict] = field(default_factory=list)
    error: str = ""


class StyleLayer:
    """风格层在大模型这一侧的完整接口。"""

    def __init__(self, encoder: Encoder, speaker: Speaker, seed: int = 7, inject_dim: int = 2):
        self.encoder = encoder
        self.speaker = speaker
        self.state = StyleState(seed=seed, inject_dim=inject_dim)
        self.history: list[dict] = []
        self.version = self.state.version()

    # ── ★ 消融开关：只在这一处，状态块自己管（未训读出本来也是恒零）
    def set_injection(self, on: bool) -> None:
        self.state.enable() if on else self.state.disable()

    @property
    def injection_on(self) -> bool:
        return self.state.enabled

    # ── 一段对话的边界：慢的那一半在这里动
    def begin_session(self) -> None:
        self.session_prior = self.state.W_out.copy()   # 段内冻结：读出参数快照

    def end_session(self, X: np.ndarray, Y: np.ndarray, lam: float = 1e-6) -> None:
        self.state.train_readout(X, Y, lam=lam)        # ★ 整套重来，⛔ 不继承
        self.version = self.state.version()

    # ── 一轮
    def turn(self, user: str, memory: str = "", live: bool = False) -> TurnResult:
        # 3 编码 + 4 生成前更新
        self.state.observe_user(user)
        prior = self.state.injection()             # ⛔ 已拔掉时这里恒为零
        # 6 说出口（注入在里面发生）
        said = self.speaker.say(user, self.state.vector(), prior)
        # 7 切
        prefix, seg = split_conditioning(said)
        msgs = build_messages(self.history, user, seg, memory)
        res = TurnResult(prefix=prefix, input_segment=seg, messages=msgs)
        if not live:
            return res
        # 9 调大模型
        raw = call_llm(msgs, prefix=prefix)
        if "error" in raw:
            res.error = json.dumps(raw)[:300]
            return res
        # 10 剥层
        res.visible, res.cot = unlayer(raw)
        # 11 回环：★ 只喂**可见输出**；⛔ 不喂 prefill、⛔ 不喂 CoT
        self.state.observe_her(res.visible)
        # 12 进材料：只有可见输出（CoT 不进）
        self.history.append({"role": "user", "content": user})
        self.history.append({"role": "assistant", "content": res.visible})
        return res


# ══════════════════════════════════════════════════════════════════ 自检
def check_plumbing() -> int:
    bad = 0

    def ok(name, cond, detail=""):
        nonlocal bad
        print(f"   {'✅' if cond else '❌'} {name}" + (f"　{detail}" if detail else ""))
        if not cond:
            bad += 1

    print("=" * 78)
    print("1 · 机械切分：一次生成 ⇒ prefix ＋ 输入段")
    print("=" * 78)
    cases = [
        "嗯，那就别说。我在这儿。你不用回我。",
        "别急着给办法。先让她知道有人在就行",
        "",
    ]
    for c in cases:
        p, s = split_conditioning(c)
        print(f"   {c!r}\n     → prefix={p!r}　输入段={s!r}")
    p, s = split_conditioning(cases[0])
    ok("prefix ＋ 输入段拼回去等于原文", p + s == cases[0], f"{p!r} + {s!r}")
    ok("prefix 不超过 40 字", len(p) <= 40, f"{len(p)} 字")
    ok("空输入 ⇒ 两段都空", split_conditioning("") == ("", ""))

    print()
    print("=" * 78)
    print("2 · 上下文组装：五块里的四块（第五块 prefix 单独带）")
    print("=" * 78)
    msgs = build_messages(
        history=[{"role": "user", "content": "之前那句"},
                 {"role": "assistant", "content": "之前那句的回应"}],
        user="今天真的累了，什么都不想说。",
        input_segment="这次别问原因。",
        memory="她上周提过一次睡不好。",
    )
    for m in msgs:
        print(f"   {m['role']:<10}{m['content'][:40]!r}")
    print(f"   {'assistant':<10}{prefix_message('嗯，那就别说。我在这儿。')}")
    ok("system 在最前", msgs[0]["role"] == "system")
    ok("历史原样带上", msgs[1]["content"] == "之前那句")
    ok("记忆层返回值是单独一段", any("想起的事" in m["content"] for m in msgs))
    ok("输入段在用户话之前", msgs[-1]["content"].endswith("今天真的累了，什么都不想说。"))
    ok("prefix 是末条 assistant", prefix_message("x")["role"] == "assistant")
    ok("prefix 的 content 存在且为空", prefix_message("x")["content"] == "")
    ok("prefix 落在 reasoning_content", prefix_message("x")["reasoning_content"] == "x")

    print()
    print("=" * 78)
    print("3 · 剥层：可见输出给用户；CoT ⛔ 不给用户、⛔ 不进材料")
    print("=" * 78)
    fake = {"choices": [{"message": {"content": "那就别说了。我在。",
                                     "reasoning_content": "她累了，别追问。"}}]}
    vis, cot = unlayer(fake)
    print(f"   可见 = {vis!r}　CoT = {cot!r}")
    ok("可见输出正确剥出", vis == "那就别说了。我在。")
    ok("CoT 单独取出、不混进可见", cot == "她累了，别追问。" and cot not in vis)

    print()
    print("=" * 78)
    print("4 · 一轮的流程（不调 API）")
    print("=" * 78)
    layer = StyleLayer(HashEncoder(), StubSpeaker("嗯，那就别说。我在这儿。你不用回我。"))
    layer.begin_session()
    r = layer.turn("今天真的累了，什么都不想说。", live=False)
    print(f"   prefix      = {r.prefix!r}")
    print(f"   输入段      = {r.input_segment!r}")
    print(f"   上下文条数  = {len(r.messages)}（＋1 条 prefix）")
    ok("说出口切成了两段", bool(r.prefix) and bool(r.input_segment))
    ok("状态已按用户话更新", layer.state.t > 0)

    print()
    print("=" * 78)
    print("5 · 消融开关：注入 off ⇒ 等价于没注入")
    print("=" * 78)
    layer.state.train_readout(np.random.default_rng(0).normal(size=(5, layer.state.dim * layer.state.levels)),
                             np.tile([[1.0, 0.0]], (5, 1)), lam=1e-8)
    on = layer.state.injection().copy()
    layer.set_injection(False)
    off = layer.state.injection()
    print(f"   注入 on  ‖{np.linalg.norm(on):.6f}‖　off ‖{np.linalg.norm(off):.6f}‖")
    ok("消融开关能把注入归零", float(np.linalg.norm(off)) == 0.0 and float(np.linalg.norm(on)) > 0)

    print()
    print(f"   {'全部通过' if bad == 0 else f'未通过 {bad} 项'}")
    return 1 if bad else 0


# ══════════════════════════════════════════════════════════════════ 真调
def live_probe() -> int:
    print("=" * 78)
    print("真调大模型：验 prefix 有没有落进 CoT、剥层对不对、回环喂的是什么")
    print("=" * 78)
    bad = 0
    for label, said in [
        ("无 conditioning", ""),
        ("有 conditioning", "嗯，那就别说。我在这儿。你不用回我。"),
    ]:
        layer = StyleLayer(HashEncoder(), StubSpeaker(said))
        r = layer.turn("今天真的累了，什么都不想说。", live=True)
        if r.error:
            print(f"   {label}：API 出错 {r.error}")
            bad += 1
            continue
        print(f"\n   ── {label}")
        print(f"      prefix  = {r.prefix!r}")
        print(f"      可见输出 = {r.visible!r}")
        print(f"      CoT 前 60 = {r.cot[:60]!r}")
        print(f"      回环喂的 = {layer.history[-1]['content']!r}（＝可见输出）")
        if not r.visible.strip():
            print("      ⚠️ 可见输出为空")
            bad += 1
        if said and not r.cot.strip():
            print("      ⚠️ 注入后 CoT 为空（prefix 没被接住）")
            bad += 1
    print()
    print(f"   {'通过' if bad == 0 else f'有 {bad} 处不对'}")
    return 1 if bad else 0


if __name__ == "__main__":
    if "--live" in sys.argv:
        sys.exit(live_probe())
    sys.exit(check_plumbing())
