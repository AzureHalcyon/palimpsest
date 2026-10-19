# -*- coding: utf-8 -*-
"""`channel_state.json` —— **管道的目录**：有哪些渠道、各自**有没有变化**。

它解决的是哪个问题
------------------
设计稿 §2.1：**模型不会想到去查它不知道存在的东西。** 把整张工具表常驻进提示太贵，
不常驻它就不知道能查什么。⇒ 分成两层：

· **L1 目录**（就是这里）—— 便宜（≈100 token），一行一个渠道，**并标明有没有变化**
· **L2 读数** —— 贵（≤400 token），要它**自己决定**去查哪个

★★ 关键分工：**"有没有变化"在这里算，不花模型的钱。**

L0 机械检查时，宿主对每个 metric 各问一次 `read()`（**进程内调用，不进上下文**），
跟上次记住的值比一比，只把**结论**（一行字）交给模型。
⇒ 于是"无变化 ⇒ 不进 L1"是**免费**的，这正是设计稿 §3.1 的前提。

★ 为什么不改插件契约（不让插件自己报"有没有变化"）
----------------------------------------------
那样每个插件都要自己维护一份"上次的值"，而**"变化"是相对谁的变化**只有宿主知道
（宿主才知道自己上次看到的是什么）。放宿主这边，插件保持无状态、可被任意重启。
（设计稿 §6 待办第 18 项记的是"如果将来插件自己报更方便"，但**默认走宿主算**。）
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional

# 「变化」的判定：默认相对变化超过 10% 才算。
# ⚠️ 这是**启发式**，不是定理：光照明暗、加速度噪声都会抖。
#   所以它只用来决定"要不要唤起模型看一眼"（看错了的代价＝多花一次模型钱），
#   ⛔ **不用来下结论**（下结论靠读数本身）。
DEFAULT_CHANGE_FRAC = 0.10

ReadFn = Callable[[str, str, int, int], Dict[str, object]]


@dataclass
class Channel:
    plugin: str                  # 插件包名（或 id）
    plugin_name: str             # 插件自称的名字（如"传感器"）
    metric: str                  # metric id（如 `accel`）
    name: str                    # 人读的名字（如"加速度模长"）
    unit: str = ""
    tool_name: str = ""          # ★ 给模型看的工具名
    use_when: str = ""           # ★ 给模型看的"什么时候该用"
    grain_ms: int = 60000
    max_window_ms: int = 2 * 60 * 60 * 1000
    change_frac: float = DEFAULT_CHANGE_FRAC
    # ★★ 读数的**性质**（由**插件**声明，因为只有它知道这个数是怎么来的）：
    #   `事件型` / `单调累加` / `滚动窗口` / `瞬时噪声` / `均值慢变` / `触发派生`
    #   ⛔ 这**不是判断**，是**事实**：告诉 AI"这个数为什么会变"，让**它自己**决定这算不算事。
    #   （2026-10-06 用户："规则开放给 AI，让 AI 自己决定，我们只给好工具。"）
    nature: str = ""
    # ---- 运行时状态（也落盘）----
    last_value: Optional[float] = None
    prev_value: Optional[float] = None   # ★ 上一次的值：变化要能给出"旧值→新值"，让 AI 自己判幅度
    last_at: Optional[int] = None
    last_coverage: Optional[float] = None
    changed: Optional[bool] = None       # None = 还不知道（第一次看）
    note: str = ""


@dataclass
class ChannelState:
    channels: Dict[str, Channel] = field(default_factory=dict)   # key = "<plugin>|<metric>"

    @staticmethod
    def key(plugin: str, metric: str) -> str:
        return "%s|%s" % (plugin, metric)


class ChannelCatalog:
    """渠道目录。**纯逻辑、不碰模型** ⇒ 可离线自检（假插件塞个 `read_fn` 就行）。"""

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir
        self.path = os.path.join(cache_dir, "channel_state.json") if cache_dir else None
        self.state = ChannelState()
        self.load()

    # ------------------------------------------------------------------ 落盘
    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception:
            self.state = ChannelState()      # ★ 读坏当空：丢的是"上次的值"，不影响读数本身
            return
        chans: Dict[str, Channel] = {}
        for k, d in (raw.get("channels") or {}).items():
            try:
                chans[str(k)] = Channel(**d)
            except Exception:
                continue
        self.state = ChannelState(channels=chans)

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(self.cache_dir, exist_ok=True)
        # ★★ **唯一 tmp 名**（2026-10-05 手机实测抓到的真 bug）：
        #   L0 可能有**两次并发**（WorkManager 周期任务 + 被广播/开机踢的那一次），
        #   两条都写同一个 `path + ".tmp"` ⇒ 一个 rename 成功、另一个找不到文件，
        #   报 `FileNotFoundError: …channel_state.json.tmp -> …channel_state.json`。
        #   ⇒ 带上 pid 就互不干扰；`os.replace` 抢输了也**不算错**（另一个写的是同一份内容）。
        tmp = "%s.tmp.%d" % (self.path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"channels": {k: asdict(c) for k, c in self.state.channels.items()}},
                      f, ensure_ascii=False, indent=1)
        try:
            os.replace(tmp, self.path)           # ★ 原子替换
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ------------------------------------------------------------------ 建目录
    def register(self, plugin: str, plugin_name: str, metrics: List[Dict[str, object]]) -> int:
        """把某个插件的 `describe()` 结果登记进来。返回新登记了几条。

        ★ 已存在的**保留运行状态**（`last_value` 等）—— 否则每次重连都忘了"上次是多少"，
          "有没有变化"就永远算不出来。
        """
        n = 0
        for m in metrics or []:
            mid = str(m.get("id") or "").strip()
            if not mid:
                continue
            k = ChannelState.key(plugin, mid)
            old = self.state.channels.get(k)
            c = Channel(
                plugin=plugin, plugin_name=plugin_name, metric=mid,
                name=str(m.get("name") or mid), unit=str(m.get("unit") or ""),
                tool_name=str(m.get("toolName") or ""), use_when=str(m.get("useWhen") or ""),
                grain_ms=int(m.get("grainMs") or 60000),
                max_window_ms=int(m.get("maxWindowMs") or 2 * 60 * 60 * 1000),
                nature=str(m.get("nature") or ""),
                last_value=(old.last_value if old else None),
                prev_value=(old.prev_value if old else None),
                last_at=(old.last_at if old else None),
                last_coverage=(old.last_coverage if old else None),
                changed=(old.changed if old else None),
                note=(old.note if old else ""),
            )
            if not old:
                n += 1
            self.state.channels[k] = c
        return n

    def forget_missing(self, alive_keys: List[str]) -> int:
        """插件没装/不可达时，把它名下的渠道**从目录里撤掉**（设计稿 §2.5：静默降级）。

        ⛔ 不是报错 —— 工具表里不出现，就是"这个渠道现在没有"。
        """
        drop = [k for k in self.state.channels if k not in set(alive_keys)]
        for k in drop:
            del self.state.channels[k]
        return len(drop)

    # ------------------------------------------------------------------ L0：算变化
    def refresh(self, now_ms: int, read_fn: ReadFn, window_ms: int = 24 * 60 * 60 * 1000,
                change_frac: Optional[float] = None) -> Dict[str, int]:
        """逐个渠道问一次读数，**与上次记住的值比**，更新 `changed`。

        ★ 这些 `read_fn` 调用发生在**进程内**，文本不进上下文 ⇒ **不花模型的钱**。
        ⛔ 插件报错/没数据不许抛：这一趟只是"看一眼"，坏一个渠道不影响别的。
        """
        to_ms = int(now_ms)
        stats = {"checked": 0, "changed": 0, "unknown": 0, "failed": 0}
        for k, c in self.state.channels.items():
            from_ms = to_ms - int(min(window_ms, c.max_window_ms))
            try:
                r = read_fn(c.plugin, c.metric, from_ms, to_ms) or {}
            except Exception as e:                                   # noqa: BLE001
                c.note = "读数失败：%s" % e
                c.changed = None
                stats["failed"] += 1
                continue
            stats["checked"] += 1
            c.note = str(r.get("note") or "")
            if not r.get("ok"):
                # ⛔ 没数据 ≠ 没变化：如实记成"不知道"，别说是"无变化"
                c.changed = None
                stats["unknown"] += 1
                continue
            val = r.get("value")
            try:
                val = float(val) if val is not None else None
            except Exception:                                        # noqa: BLE001
                val = None
            frac = float(change_frac if change_frac is not None else c.change_frac)
            if val is None:
                c.changed = None
                stats["unknown"] += 1
            elif c.last_value is None:
                c.changed = None            # ★ 第一次看 ⇒ **不知道**（不假装"有变化"）
                stats["unknown"] += 1
            else:
                denom = max(abs(c.last_value) * frac, 1e-9)
                c.changed = abs(val - c.last_value) > denom
                if c.changed:
                    stats["changed"] += 1
            if val is not None:
                c.prev_value = c.last_value        # ★ 先留住旧值（给 AI 看"旧→新"，它自己判幅度）
                c.last_value = val
            c.last_at = int(r.get("at") or to_ms)
            try:
                c.last_coverage = float(r.get("coverage")) if r.get("coverage") is not None else None
            except Exception:                                        # noqa: BLE001
                c.last_coverage = None
        self.save()
        return stats

    # ------------------------------------------------------------------ 给模型看的
    def changed_channels(self) -> List[Channel]:
        """★ **L0 → L1 的闸门**：只有这些值得唤起模型。

        返回**明确已知变化**的渠道；`changed is None`（第一次看 / 没数据）**不算** ——
        否则每次重启都要唤起一次模型，等于把"有变化才唤醒"变成"每次都唤醒"。
        """
        return [c for c in self.state.channels.values() if c.changed is True]

    def has_any_change(self) -> bool:
        return any(c.changed is True for c in self.state.channels.values())

    def changed_digest(self) -> List[Dict[str, object]]:
        """★ **给 AI 判断用的变化明细**（2026-10-06：规则交给 AI，所以把原料给全）。

        每条给：渠道名 · **性质**（这个数为什么会变）· 旧值→新值 · 绝对差 · 相对差 · 单位。
        ⛔ 这里**不打分、不排序、不说"重要"** —— 判断归 AI。
        """
        out: List[Dict[str, object]] = []
        for c in self.state.channels.values():
            if c.changed is not True:
                continue
            d: Dict[str, object] = {
                "metric": c.metric, "name": c.name, "unit": c.unit,
                "nature": c.nature or "（插件没声明）",
                "prev": c.prev_value, "now": c.last_value,
            }
            if c.prev_value is not None and c.last_value is not None:
                delta = float(c.last_value) - float(c.prev_value)
                d["delta"] = delta
                base = abs(float(c.prev_value))
                d["rel"] = (delta / base) if base > 1e-9 else None
            out.append(d)
        return out

    def alias_map(self) -> Dict[str, str]:
        """★ **名字 → metric id** 的对照表（给"AI 自己写规则"用）。

        为什么需要：AI 在提示词里同时看到**人读的名字**和 **metric id**，
        实测它会把规则里的渠道写成名字（`"最近一小时屏幕亮着多久（分钟）"`），
        而机械层是按 **id** 比对的 ⇒ 不做翻译的话规则**静默失效**
        （2026-10-06 真机实测抓到的"静默空转"）。
        ⛔ 这里只做**翻译**，不做判断：翻不出来的键由 `attention.validate` **明确拒绝**。
        """
        m: Dict[str, str] = {}
        for c in self.state.channels.values():
            m[c.metric] = c.metric
            if c.name:
                m[c.name] = c.metric
                if c.unit:
                    m["%s（%s）" % (c.name, c.unit)] = c.metric
                    m["%s(%s)" % (c.name, c.unit)] = c.metric
        return m

    def keys_for_policy(self) -> str:
        """给 AI 看的**可用渠道键**（写规则时照抄，别有错别字）。"""
        return "、".join("`%s`" % c.metric for c in self.state.channels.values())

    def render(self, max_channels: int = 24) -> str:
        """L1 目录的**文本**（≈100 token）。★ 这就是"它看到的能查什么"。

        每行：`· 名字（插件）—— 有变化 / 无变化 / 还不知道（没数据）`
        ★ 2026-10-06：有变化的那几条**带上旧→新与性质** ——
          否则 AI 只看到"有变化"，等于把"这算不算事"的判断权又推回给我们。
        """
        if not self.state.channels:
            return "可查的渠道：（暂时一个都没有）"
        lines = ["可查的渠道："]
        for c in list(self.state.channels.values())[:max_channels]:
            unit = ("（%s）" % c.unit) if c.unit else ""
            if c.changed is True:
                detail = ""
                if c.prev_value is not None and c.last_value is not None:
                    detail = "  %s → %s" % (_num(c.prev_value), _num(c.last_value))
                    if c.nature:
                        detail += " · 这个数的性质：%s" % c.nature
                mark = "★ 有变化%s" % detail
            elif c.changed is False:
                mark = "无变化"
            else:
                mark = "还不知道（%s）" % (c.note or "没有数据")[:24]
            lines.append("· %s%s（%s）—— %s" % (c.name, unit, c.plugin_name or c.plugin, mark))
        n = len(self.state.channels)
        if n > max_channels:
            lines.append("…（还有 %d 个渠道没列出来）" % (n - max_channels))
        return "\n".join(lines)

    def tools(self) -> List[Dict[str, object]]:
        """★ **给模型看的工具描述**（设计稿 §2.6）：由插件声明、宿主转交，绝不在这里硬编码。

        只列**有工具名**的那些 —— 没声明的渠道只出现在目录里，不进工具表。
        ★ 2026-10-05 起还带上 `unit` / `maxWindowMs` / `grainMs`：
          宿主渲染和"窗口上限"都要用，而它们同样是**插件声明的**。
        """
        out: List[Dict[str, object]] = []
        for c in self.state.channels.values():
            if c.tool_name:
                out.append({"name": c.tool_name, "metric": c.metric, "plugin": c.plugin,
                            "desc": c.name, "unit": c.unit, "useWhen": c.use_when,
                            "maxWindowMs": c.max_window_ms, "grainMs": c.grain_ms})
        return out


def _num(v: object) -> str:
    """给 AI 看的数字：别甩一串浮点尾巴（`47.548233333333336` 那种没人读得出信息）。"""
    try:
        f = float(v)                                              # type: ignore[arg-type]
    except Exception:                                            # noqa: BLE001
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return ("%.2f" % f).rstrip("0").rstrip(".")


def apply_l0(catalog: "ChannelCatalog", payload: Dict[str, object],
             now_ms: int) -> Dict[str, object]:
    """★★ **L0 机械检查**的纯逻辑（2026-10-05）：收插件读数 → 算"有没有变化"。

    ★★ 这一步**绝不许调模型** —— 两级唤醒里 L0 就是"不花钱的那一级"（设计稿 §3.1）：
      · 没变化 ⇒ 到此结束（成本 0）；
      · 有变化 ⇒ 才由宿主去唤起 L1（模型）。

    `payload` 的形状（由宿主侧把插件问一遍之后交上来）：
        {"plugins": [{"pkg":…, "name":…, "metrics":[{MetricInfo…}]}],
         "reads":   {"<pkg>|<metric>": <Reading json>}}

    ⚠️ **读数由宿主去调插件**（provider 调用），不是这里发起的 ——
      于是这一层不用知道 Android 的任何东西，**可以离线自检**。
    """
    plugins = payload.get("plugins") or []
    reads = payload.get("reads") or {}
    for p in plugins:
        catalog.register(str(p.get("pkg") or ""), str(p.get("name") or ""),
                         list(p.get("metrics") or []))
    alive = ["%s|%s" % (str(p.get("pkg") or ""), str(m.get("id") or ""))
             for p in plugins for m in (p.get("metrics") or [])]
    dropped = catalog.forget_missing(alive)      # 插件没了 ⇒ 静默降级

    def read_fn(plugin: str, metric: str, _a: int, _b: int) -> Dict[str, object]:
        r = reads.get(ChannelState.key(plugin, metric))
        return r if isinstance(r, dict) else {"ok": False, "note": "这次没取到"}

    stats = catalog.refresh(now_ms, read_fn)
    changed = catalog.changed_channels()
    return {"ok": True, "changed_n": len(changed),
            "changed": [c.metric for c in changed], "stats": stats, "dropped": dropped,
            # ★ 给 AI 的原料：变化明细（旧→新、性质），判断权在它那边
            "changed_digest": catalog.changed_digest(),
            # ★ 给 AI 写规则用的**可用渠道键**（它有名字也有 id，规则里必须能对上）
            "channel_keys": catalog.keys_for_policy(),
            "text": catalog.render()}
