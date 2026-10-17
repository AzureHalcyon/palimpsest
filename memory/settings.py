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
  | `companion/precision.py` 的跨平台探针 | **`Config()`** | 它是**参考仪器**：必须用固定默认值才能与桌面逐位对拍，读用户设置反而毁掉它 |
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
    # ---- 日常会动的 ----
    Knob("chat.model", "对话模型", "str", "【实测】",
         note="换模型等于换底座；这个模型是推理模型，换之前先确认它吃不掉 max_tokens"),
    Knob("chat.temperature", "温度", "float", "【实测】",
         note="0.7 是用来跑对照的那一档", low=0.0, high=2.0),
    Knob("chat.max_tokens", "回复上限", "int", "【实测】",
         note="起始必须 ≥6000：取 2000 会被内部推理吃光、返回空回复（128 轮里 20 轮空）",
         unit="token", low=1000, high=32000),
    Knob("chat.effort", "思考档位", "str", "【实测】",
         note="花在内部推理上的算力", choices=["low", "medium", "high"]),
    Knob("dream.temperature", "归档温度", "float", "【实测】",
         note="归档比对话更需要稳定，所以更低", low=0.0, high=2.0),

    # ---- 读取器：这些是量出来的，动之前该想一想 ----
    Knob("reader.mass", "注入质量比", "float", "【实测】",
         note="1156 条真实查询上做的网格：0.9 会让中位条数从 4 跳到 12、触顶 40%",
         low=0.05, high=1.0),
    Knob("reader.tau", "范围温度 τ", "float", "【实测】",
         note="0.02/0.70 这一格触顶占比最低（7.8%）；τ≥0.03 触顶冲到 12–40%",
         low=0.001, high=1.0),
    Knob("reader.max_items", "单次注入上限", "int", "【护栏】",
         note="成本截断，不是设计旋钮：20 时触顶 7.8%，放到 40 只降到 0.3% 但文本翻倍",
         unit="条", low=1, high=60),
    Knob("reader.min_items", "单次注入下限", "int", "【推导】",
         note="★ 1 = 不设下限。设成 3 会把「本来就该只给 1 条」的 24% 轮次全兜成 3 条",
         unit="条", low=1, high=20),
    # ---- 阈值门（2026-10-06 接）----
    Knob("reader.gate_enabled", "阈值门", "bool", "【实测】",
         note="开 = 相关度低于下界的条目**直接不要**，允许一条都不给（这就是「想不起来」）。"
              "⚠️ 实测它救不了「问关于他自己的事」那一类（那种查询分反而更高）："
              "要筛掉「池子里没答案」的那一例，得把 96% 的正常轮次一起筛掉。"
              "换编码器/换池子后下界要重新标定"),
    Knob("reader.gate_tau", "阈值门下界 τ", "float", "【实测】",
         note="本题库标定值 0.345 = 200 条真实中文问法 top-1 分布的 p10 ⇒ 约 10% 轮次「没想起」。"
              "⚠️ 换编码器/换池子立刻作废",
         low=0.05, high=0.95),

    # ---- 时间与升迁 ----
    Knob("time.tau_state", "临时记忆半衰期", "float", "【推导】",
         note="要求「下次再来时还记得」⇒ τ ≥ 中位间隔/ln2 = 3/0.693 = 4.3 天，取 5.0 留余量",
         unit="天", low=0.5, high=60.0),
    Knob("time.eps", "淡出阈值", "float", "【推导】",
         note="exp(-2)=0.135 取 0.14 ⇒ 存活 9.8 天（「只漏一次还活着、连漏两次就淡掉」）",
         low=0.01, high=0.9),
    Knob("time.promote_hits", "升长期门槛", "int", "【实测】",
         note="池内计数分布 0:62·1:28·2:21·3:2 ⇒ 设 1 会让短期池名存实亡，2 是唯一分界",
         unit="次", low=1, high=10),

    # ---- 注入冷却（掩膜）----
    Knob("cooldown.enabled", "注入冷却", "bool", "【推导】",
         note="开 = 刚注入过的条目，接下来几轮不再注入它。治的是"
              "「他连发几条消息时，每轮都想起同一批、于是每轮都回类似的话」。"
              "关掉它就是开/关对照的入口"),
    Knob("cooldown.turns", "冷却轮数", "int", "【假设】",
         note="★ 没有实测依据。取 3 是为了覆盖「连发的那一串」；"
              "正常轮流下相邻两轮 push 的 Jaccard 中位只有 0.00/0.05 ⇒ 它主要治连发",
         unit="轮", low=1, high=20),

    # ---- 归档（要花钱的）----
    Knob("dream.turns", "归档回合上限", "int", "【实测】",
         note="★ 2026-10-06 从 5 放宽到 12：收口权交给模型自己（见下一项），这个数只是硬护栏。"
              "原来的 5 是照「实际最大用到 4」定的 —— 但那是**被 5 卡着**跑出来的分布，"
              "不能用它论证 5 够用", unit="回合", low=1, high=40),
    Knob("dream.require_deliver", "归档交付判定归模型", "bool", "【推导】",
         note="开 = 它可以在 JSON 里写 `deliver:false` + `todo`，表示「还没弄干净」 ⇒ 带着自己的 todo "
              "再给一轮（能继续查、继续改），**只有它说交付才算完**。"
              "关 = 回到旧行为（跑满回合就强制收口），留作开/关对照入口"),
    Knob("dream.resume_attempts", "归档最多试几次", "int", "【护栏】",
         note="★ 每次尝试都是一次真金白银的模型调用（要真花钱）。"
              "取 2 = 首次 + 补做一次；不取无限：手机每次启动都重试会变成看不见的持续扣费",
         unit="次", low=1, high=5),
]


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
                "# 各项的依据等级见 companion/settings.py 的 KNOBS。\n")
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
