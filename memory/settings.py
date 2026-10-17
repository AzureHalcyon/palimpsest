# -*- coding: utf-8 -*-
"""用户可改的参数：一张**显式的表** + 一层**只存他改过那几项**的覆盖。

★ 为什么不直接把整个 `Config` 序列化成 yaml 再读回来：
  本仓库的每个默认值旁边都写着它的依据（【实测】/【推导】/【假设】/【护栏】）。
  整体序列化会让"默认值"和"他改过的值"混成一团 —— 于是
  ① 改了默认值传不到已经存过盘的人那儿；② 存盘文件里那些数**失去了依据**，
  变成一堆没有出处的数字。**依据链条是这套系统最贵的东西，不能这么弄断。**
  ⇒ 默认值**只留在代码里**；存盘的**只有他改过的项**。
  后果是好的：默认值改进立刻生效，而他明确改过的那几项不会被悄悄顶掉。

★ 谁该用 `load_config()`、谁不该 —— 这条不写清楚一定会踩：

  | 入口 | 用哪个 | 为什么 |
  |---|---|---|
  | Android 宿主（`host_glue.py`）、桌面交互（`tools/talk.py`） | **`load_config()`** | 这是"真正在跑的那套配置" |
  | （已删）跨平台探针 | **`Config()`** | 它是**参考仪器**：必须用固定默认值才能与桌面逐位对拍，读用户设置反而毁掉它 |
  | `tools/calibrate.py` / `prep_pool.py` / `simulate.py` | `Config()` | 离线分析/建池工具，要的是可复现的**设计默认值**，不是某个人的当前设置 |

★ 每一项都带 `grade`（依据等级）与 `note` —— **界面要把它们显示出来**。
  一个参数如果标着【实测】，那意味着"这个数是量出来的，动它要先想清楚"；
  标着【假设】的则本来就是未知数。**不许把这两类混在一起让人随便拖。**
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .config import Config


@dataclass(frozen=True)
class Knob:
    """一个可改参数。`path` 是 `Config` 里的点分路径。"""

    path: str
    label: str
    kind: str                 # float / int / bool / str
    grade: str                # 【实测】/【推导】/【假设】/【护栏】
    note: str = ""
    unit: str = ""
    low: Optional[float] = None
    high: Optional[float] = None
    choices: Optional[List[str]] = None

    def clamp(self, v: Any) -> Any:
        if self.kind == "bool":
            return bool(v)
        if self.kind in ("int", "float"):
            x = int(v) if self.kind == "int" else float(v)
            if self.low is not None:
                x = max(x, self.low)
            if self.high is not None:
                x = min(x, self.high)
            return x
        return str(v)


# --------------------------------------------------------------------------- 表
# ★ 排序 = 界面上的顺序：先"人天天会动的"，再"动之前该想一想的"。
KNOBS: List[Knob] = [
    # ---- 对话模型：天天可能动的那几个 ----
    Knob("chat.model", "对话模型", "str", "【实测】",
         note="换模型等于换底座；这个模型是推理模型，换之前先确认它吃不掉 max_tokens"),
    Knob("chat.temperature", "温度", "float", "【实测】",
         note="0.7 是用来跑对照的那一档", low=0.0, high=2.0),
    Knob("chat.max_tokens", "回复上限", "int", "【实测】",
         note="起始必须 ≥6000：取 2000 会被内部推理吃光、返回空回复（128 轮里 20 轮空）",
         unit="token", low=1000, high=32000),
    Knob("chat.effort", "思考档位", "str", "【实测】",
         note="花在内部推理上的算力。★ 编写那一步**不看它**：那一趟是关思考的（实测降档没用）",
         choices=["low", "medium", "high"]),

    # ---- [R] 回忆 / 注入（新线的两条活开关）----
    Knob("session.render_recall", "注入写成一段", "bool", "【推导】",
         note="开 = `[W] 编写` 把取到的原文编成**一段回忆**再注入；关 = 退回条目清单。"
              "这是同一份取件下「两种呈现」的对照入口"),
    Knob("session.max_pull", "她自己再查几次", "int", "【实测】",
         note="一轮里模型自己调 `recall` 的上限。【实测】旧线 80 轮只触发 2 次 ⇒ 上限不用大",
         unit="次", low=0, high=5),

    # ---- 注入冷却（掩膜）----
    Knob("cooldown.enabled", "注入冷却", "bool", "【推导】",
         note="开 = 刚注入过的原文，接下来几轮不再注入它。治的是"
              "「他连发几条消息时，每轮都想起同一批、于是每轮都回类似的话」。"
              "关掉它就是开/关对照的入口"),
    Knob("cooldown.turns", "冷却轮数", "int", "【实测】",
         note="★ 12 场真数据实测：取 3 时**场 7 以后 44 轮里 18 轮有注入（41%）、"
              "26 轮被挡空（59%）** ⇒ 实际效果是「同一段每 3 轮念一次」。"
              "挡住的是**刚说过的那段原文**（还在 `msgs` 里），⛔ 不是丢掉。"
              "调大 ⇒ 更少重复、更少出现；调 1 ⇒ 每轮都允许重念",
         unit="轮", low=1, high=20),
]

# ★★ 新线真正的参数账在 `memory/knobs.py`（19 项，逐项带依据等级与说明）。
#   ⛔ **它不是这里的可改旋钮**：那是【假设】占多数的参考值，改它要「改前 → 改后 → 为什么」
#     留一行记录，不是拖个滑块（作者口径：「⛔ 不许散落在代码里凭手感调」）。
#   ⇒ 界面只**只读地**列出来给人核对，见 `host_glue.ledger_lines`。

def knob_by_path(path: str) -> Optional[Knob]:
    for k in KNOBS:
        if k.path == path:
            return k
    return None


# --------------------------------------------------------------------------- 覆盖层
def _get(cfg: Any, path: str) -> Any:
    cur = cfg
    for part in path.split("."):
        cur = getattr(cur, part)
    return cur


def _set(cfg: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = getattr(cur, part)
    setattr(cur, parts[-1], value)


def default_path(data_dir: Optional[str] = None) -> Path:
    """覆盖层放哪儿：宿主给了 `data_dir` 就放它下面，否则跟凭证同级。"""
    if data_dir:
        return Path(data_dir) / "settings.yaml"
    from .config import cred_path
    return cred_path().parent / "settings.yaml"


class SettingsStore:
    """`settings.yaml` 的读写。**只存改过的项**，读不到 / 坏了都当"没改过"。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None

    def resolve(self, data_dir: Optional[str] = None) -> Path:
        return self.path or default_path(data_dir)

    # ------------------------------------------------------------------ 读
    def load(self, data_dir: Optional[str] = None) -> Dict[str, Any]:
        p = self.resolve(data_dir)
        if not p.exists():
            return {}
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            # ★ 读不出来**不抛**：一份坏掉的设置不该让整个 App 起不来。
            #   但要吵 —— 静默降级会让人以为"我改了却没生效"是玄学。
            import sys
            print("[settings] %s 读不出来（%s），按「没改过」处理"
                  % (p, type(exc).__name__), file=sys.stderr)
            return {}
        # ★★ 2026-10-17（审查抓到）：**顶层不是 dict** 时（手改成列表/字符串很常见），
        #   下面 `raw.get("overrides")` 会抛 AttributeError —— 而 `load()` 不捕获、
        #   `load_config()` 只捕获 AssertionError ⇒ **整个 App 起不来**。
        #   这与本函数承诺的"读不出来 / 坏了都当没改过"直接相反。
        if not isinstance(raw, dict):
            import sys
            print("[settings] %s 的顶层不是映射（是 %s），按「没改过」处理"
                  % (p, type(raw).__name__), file=sys.stderr)
            return {}
        got = raw.get("overrides") or {}
        if not isinstance(got, dict):
            return {}
        # 只认表里有的项，且过一遍 clamp —— 手改过的 yaml 也不该把系统带出界
        out: Dict[str, Any] = {}
        for path, v in got.items():
            k = knob_by_path(path)
            if k is None:
                continue
            try:
                out[path] = k.clamp(v)
            except Exception:  # noqa: BLE001
                continue
        return out

    # ------------------------------------------------------------------ 写
    def save(self, overrides: Dict[str, Any], data_dir: Optional[str] = None) -> Path:
        p = self.resolve(data_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        clean: Dict[str, Any] = {}
        for path, v in (overrides or {}).items():
            k = knob_by_path(path)
            if k is None:
                continue
            clean[path] = k.clamp(v)
        head = ("# companion 的用户设置（**只存改过的项**）\n"
                "# 没写的项 = 用代码里的默认值。默认值改了会立刻生效，\n"
                "# 这里写过的项则始终以这里为准。\n"
                "# 各项的依据等级见 memory/settings.py 的 KNOBS。\n")
        body = yaml.safe_dump({"overrides": clean}, allow_unicode=True, sort_keys=True)

        # 原子写：先写同目录临时文件再 rename。手机上进程随时可能被杀，
        # 半截文件会让下次启动读到一堆坏值。
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(head)
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return p


# --------------------------------------------------------------------------- 应用
def apply_overrides(cfg: Config, overrides: Dict[str, Any]) -> Config:
    """把覆盖值盖到一份**默认 `Config`** 上，并跑一次 `check()`。

    ⚠️ 不 `check()` 就返回，等于把"越界的设置"放进系统 —— 而越界往往不是崩，
       是**悄悄坏**：`mass>1` 会让缩尾失效，`eps>=1` 会让所有临时条目当场消失。
    """
    for path, v in (overrides or {}).items():
        if knob_by_path(path) is None:
            continue
        _set(cfg, path, v)
    cfg.check()
    return cfg


def load_config(store: Optional[SettingsStore] = None,
                data_dir: Optional[str] = None) -> Config:
    """默认值 + 覆盖层 ⇒ 真正生效的 `Config`。**这是全系统该走的那个入口。**"""
    cfg = Config()
    store = store or SettingsStore()
    try:
        cfg = apply_overrides(cfg, store.load(data_dir))
    except AssertionError as exc:
        # 覆盖值把约束撞坏了：退回默认值并吵一声，而不是让 App 起不来
        import sys
        print("[settings] 覆盖值不合约束（%s），本次改用默认值" % exc, file=sys.stderr)
        cfg = Config()
    return cfg
