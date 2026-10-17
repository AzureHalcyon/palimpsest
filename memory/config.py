# -*- coding: utf-8 -*-
"""配置与凭证。**本仓库不携带任何具体数据** —— 底色、情境、记忆、语料、
运行产物、密钥，全部从外部路径或凭证文件读。

★ 一条工程规矩：**每个参数都要能回答"为什么是这个数字"。**
  所以下面逐项标了依据等级：

    【实测】跑过对照并量出数（数写在旁边）
    【推导】从一条已经定下的原则或实测数**算式推**出来（算式写在旁边）
    【假设】没有数据，只能假设 —— **必须显式写出来，不许藏**
    【护栏】不是调出来的值，是成本/稳定性上限；**允许"从没被碰到"**

  数值的完整依据与复算方式见 `docs/参数与依据.md`；**改这里就要同步改那里**。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import yaml

# 凭证位置：**密钥绝不写进仓库**，仓库里只留模型名与维度。
CRED_PATH = Path(os.environ.get("COMPANION_CRED_PATH",
                                os.path.expanduser("~/.dsh/.credentials.yaml")))
"""⚠️ 这个常量**只在导入时算一次** —— 别拿它当"当前凭证位置"用。

【实测 2026-10-05，宿主（Android）把坑踩出来了】宿主是在**启动之后**才知道自己私有目录
在哪的，于是会去设 `COMPANION_CRED_PATH`；但如果那之前已经有谁 `import memory.config`
了，这个常量就**已经定死**，覆盖**静默失效** —— 表现为"凭证明明配了却读不到"，而且报错
会指向一个跟宿主毫不相干的路径。

⚠️ 注意：这**不是打包/线程的问题**，是模块顶层语句只执行一次这条 Python 语义；
   同一个 bug 在 PC 上原样复现（先跑自检再 set_paths ⇒ 读到的仍是 `~/.dsh/...`）。
⇒ 所以 `Credentials.load()` **不看这个常量**，走下面那个调用时解析的函数。
"""


def cred_path() -> Path:
    """**调用时**解析凭证路径（环境变量优先，其次 `~/.dsh/.credentials.yaml`）。"""
    return Path(os.environ.get("COMPANION_CRED_PATH")
                or os.path.expanduser("~/.dsh/.credentials.yaml"))


class CredentialsError(RuntimeError):
    """凭证缺失/不可读。**报错信息里绝不回显密钥本身。**"""


@dataclass
class Credentials:
    embed_base_url: str
    embed_api_key: str
    chat_api_key: str
    chat_url: str = "https://api.deepseek.com/chat/completions"

    @classmethod
    def load(cls, path: Optional[Path] = None, chat_url: Optional[str] = None) -> "Credentials":
        """★ `path` 缺省时**在调用这一刻**解析（见 `cred_path()` 那条实测记录），
        不用导入时定死的 `CRED_PATH` —— 否则宿主设的环境变量会被静默忽略。"""
        path = Path(path) if path else cred_path()
        if not path.exists():
            raise CredentialsError(
                "找不到凭证文件：%s\n"
                "它需要包含 refs.ALIYUN_MAAS_BASE_URL / ALIYUN_MAAS_API_KEY / DEEPSEEK_API_KEY。\n"
                "（也可用环境变量 COMPANION_CRED_PATH 指向别处。）" % path)
        try:
            refs = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("refs") or {}
        except Exception as exc:  # noqa: BLE001
            raise CredentialsError("凭证文件读不出来（%s）：%s"
                                   % (path, type(exc).__name__)) from exc
        missing = [k for k in ("ALIYUN_MAAS_BASE_URL", "ALIYUN_MAAS_API_KEY",
                               "DEEPSEEK_API_KEY") if not refs.get(k)]
        if missing:
            raise CredentialsError("凭证文件里缺这些键（不列值）：%s" % ", ".join(missing))
        return cls(embed_base_url=str(refs["ALIYUN_MAAS_BASE_URL"]).rstrip("/"),
                   embed_api_key=str(refs["ALIYUN_MAAS_API_KEY"]),
                   chat_api_key=str(refs["DEEPSEEK_API_KEY"]),
                   chat_url=chat_url or cls.chat_url)

    def save(self, path: Optional[Path] = None) -> Path:
        """把凭证写回 yaml（宿主界面里"填 API Key"走这条路）。

        ★ 三条硬要求，缺一条都会咬人：
          1. **只动 `refs` 这一段**，文件里其它键原样保留 —— 那份 yaml 是用户自己的
             文件，不是我们的数据库，整份重写等于把他的东西删了。
          2. **原子写**（同目录临时文件 + `os.replace`）。手机上进程随时可能被杀，
             写到一半就留下半截 yaml，下次启动直接"凭证读不出来"。
          3. **不把密钥写进日志/报错**（与 `CredentialsError` 同一条规矩）。
        """
        import os
        import tempfile

        p = Path(path) if path else cred_path()
        p.parent.mkdir(parents=True, exist_ok=True)

        data: dict = {}
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                # 坏文件不阻止保存：用户此刻的意图就是把凭证写对。
                data = {}
        if not isinstance(data, dict):
            data = {}
        refs = data.get("refs")
        if not isinstance(refs, dict):
            refs = {}
        refs.update({
            "ALIYUN_MAAS_BASE_URL": self.embed_base_url,
            "ALIYUN_MAAS_API_KEY": self.embed_api_key,
            "DEEPSEEK_API_KEY": self.chat_api_key,
        })
        data["refs"] = refs

        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".cred-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            # 桌面上收一下权限；Android 的 filesDir 本来就是应用私有，chmod 无副作用
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return p


# --------------------------------------------------------------------------- 编码器
@dataclass
class EmbedConfig:
    model: str = "qwen3.7-text-embedding"
    dim: int = 1024
    """【实测】维度 512/1536/2560 都可用，**2560 相对 1024 无改善**
    （层内余弦与最近邻几乎不变）⇒ 用默认 1024 省内存。"""

    batch: int = 20
    """【实测】单请求 20 条通过、24 条被拒（更高会 400，自适应降批到 12 才通）
    ⇒ 真实上限在 12–24 之间，**稳妥取 ≤20**。"""

    retries: int = 5
    """【实测】长跑里一次 TLS 瞬断（`SSL: UNEXPECTED_EOF`）就把整跑打断过 ⇒ 网络层必须重试。"""

    backoff: float = 1.5
    """退避：`backoff × (尝试次数)` 秒。"""

    timeout: float = 300.0


# --------------------------------------------------------------------------- 底座
@dataclass
class ChatConfig:
    model: str = "deepseek-flash"
    effort: str = "low"
    temperature: float = 0.7
    max_tokens: int = 6000
    """【实测】这个模型是**推理模型**：`max_tokens=2000` 会被内部推理吃光、返回**空回复**
    （128 轮里 20 轮空）⇒ **起始必须 ≥6000**，且空回复时逐级放大重试。"""

    retries: int = 4
    timeout: float = 600.0
    escalate: bool = True
    """空回复时 `max_tokens` ×2 / ×4 再试（空回复是**真问题**，不是偶发）。"""

    top_logprobs: int = 5
    """每轮要几个候选的 `logprobs`（**底片要存档**，2026-10-17）。
    【实测 2026-10-17】官方 `api.deepseek.com` 非流式与**流式每个 chunk** 都给；
    形状是**两串**（`logprobs.reasoning_content[]` / `logprobs.content[]`）。
    ⛔ `temperature` 必须 > 0：`temperature = 0` 会把分布压成 one-hot、`logprob` 恒 0 ⇒ 整份作废。
    ⚠️ 只增大响应体积，不多收 token 钱。"""


# --------------------------------------------------------------------------- 会话
@dataclass
class SessionConfig:
    max_pull: int = 2
    """每轮最多几次"再查一次"。【护栏】防模型反复查。
    【实测】80 轮里只触发 2 次、都在 1 次往返内完成 ⇒ **这个上限从没被碰到**。"""

    push_header: str = "【这一轮想起的】"
    """注入内容的呈现头。⚠️ 措辞属于"呈现形态"，改动要留痕。"""

    render_recall: bool = True
    """★ 注入要不要走 `[W] 编写`（把件整理成**一段回忆**）—— 2026-10-17 作者口径：
    「原来的条目变成一段」。
    · `True`（默认）：取件 → 编写 → 一段；编写产物落 `<cache>/说/`
    · `False`：退回旧样子（直接铺条目清单，省一次调用）
    ⚠️ 编写要多一次模型调用 ⇒ 那一轮首字延迟会多 1–3 s；先按 True 跑，量过再定默认。
    ⚠️ 2026-10-17 修的：它原来被写进了 `CooldownConfig`（冷却那一节），而读它的地方
      写成 `getattr(cfg.session, "render_recall", True)` ⇒ **开关一直是"默认值"**，
      在界面上拧它一点用都没有。现在它归位了，读的地方也不许再兜底。
    """


# --------------------------------------------------------------------------- 注入冷却
@dataclass
class CooldownConfig:
    """★★ **注入冷却**（2026-10-06 加 · **用户口径**）：

    > 「如果已经 push，那么**后续几轮就过滤掉不 push，等 CD 过了再 push**。」

    **出处**：`03_研究/参考项目与记忆层-调研.md` §7 动作 **A1**（SillyTavern 的 `cooldown`）。
    那条当时留着一句「⚠️ **必须先定接口**：我们现在的评测是"一条对话 = 一次查询"，
    **没有 turn 的概念** ⇒ 要先定义 tick（建议：一次检索 = 一轮）」
    ⇒ **现在接口有了**：一次 `Session.ask` = 一轮（见 `session.py`）。

    **要治的现象**（用户原话）：他分几条消息连着发，**每一轮都想起同一批、于是每一轮都回类似的话**。

    **只作用于「自动注入」那一条路**：

    | 走不走冷却 | 为什么 |
    |---|---|
    | **push**（`Session.ask` 自动注入） | ✅ 走。这就是"刚想起来的事，别马上又想起来" |
    | **`read_z`**（模型主动再查） | ⛔ **不走**。那是"我要查"，不是"我又被提醒了一遍" |
    | 归档里的 `search_memory` | ⛔ **不走**。同上 |

    ⛔ **为什么后两条必须豁免**：三者**共用同一个读取器**。若一起冷却，池子小的时候
    （实测主池 252 条）会出现"全部候选都在冷却里 ⇒ **查也查不到**"，
    而冷却的目的是"别自动重复念叨"，**不是**"不许它去查"。

    ⚠️ **冷却与重要性正交**：它管的是"这句刚说过没有"，**不是**"这句重不重要"。
    "重要的事更靠前 / 更不易忘"曾经归"深刻度"管（那个配置类已随旧读取器下线）—— 两者别混
    （这个混法在 `00_项目文档.md §8.3` 错题集里一天犯过四次）。
    """

    enabled: bool = True
    """★ 总开关。开着 = 每次 push 之后，把这一轮**真正注进去的**那几条冷却掉。

    ⚠️ 关掉它的用途只有两个：① 跑"开 / 关"前后对照（**还没跑**，见
    `docs/未接线与已知缺口.md`）；② 万一它把 push 削得太狠，先关掉止血。
    """

    turns: int = 3
    """一条被注入后，**接下来几轮之内不再注入它**（轮 = 一次 `ask`）。

    ⚠️ **【假设】—— 没有实测依据**。与已删的 `TimeConfig` 那批时间常数同一性质，
       **写在这里就是要它露在明处**。取 3 的理由：
       ① 要覆盖"他连发几条消息"的那一串（连发时每轮都会检索、query 又互相近似
          ⇒ 最容易重复想起同一批）。⚠️ 而**正常轮流**下，实测相邻两轮 push 的
          Jaccard 重合中位只有 **0.00 / 0.05**（`04_实验记录/第三十轮`）
          ⇒ **这条 CD 治的主要是连发，不是普通轮次**；
       ② 与调研 A1 里"三条消息内别再提这件事"的举例一致。

    ⚠️ **它是一张掩膜，不是过滤器**（用户 2026-10-06 定："就做最简单的，一个掩膜，
       放在**最后**决定给不给 AI 看就行"）：
       · 读取器**原样跑**（`τ`/`mass`/`clip`/排序一律不动）⇒ 它不改"想起几条"这件事本身；
       · 掩膜作用在**最后一步**：这一轮取回来的那几条里，还在冷却期的**不注入**；
       · ⛔ **不做替补**（这是"最简单"的代价，已认）：若取回的前 k 条全被掩掉，
         这一轮 push 就是**空的** —— 不会去捞第 k+1 名。空 push 的下游规则见 `session.ask`。
    """


@dataclass
class Config:
    """全系统的参数根。★ 2026-10-17：只剩**还在起作用**的四节。

    ⛔ 删掉的五节（`reader` / `time` / `dream` / `merge` / `salience`）分别属于
      旧池子的读取器、临时池的衰减、旧归档那条对话、落档前核对、深刻度（从未落地）——
      那四条路都不存在了，留着只会让 `Config` 长得像还有那些功能。
      ★ 新线真正在调的数在 `memory/knobs.py`（19 项，逐项带依据等级），
      ⛔ 不放在这里：这里管的是"跑起来必须一致"的底座配置。
    """

    embed: EmbedConfig = field(default_factory=EmbedConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)

    def check(self) -> None:
        """自检：把"互相关联、必须一致"的约束钉在这里（启动时跑一次）。

        ★ 这些断言的对象只有**还在起作用**的那几格 —— 断言已经不存在的字段，
          等于给自己留一串永远为真的假护栏。
        """
        assert self.chat.max_tokens >= 1000, (
            "回复上限太小：这个模型是**推理模型**，取 2000 会被内部推理吃光、回空")
        assert self.chat.temperature >= 0.0, "温度不许为负（`logprobs` 还要求它 > 0）"
        assert self.embed.dim > 0, "嵌入维度必须 > 0"
        assert self.session.max_pull >= 0, "「再查几次」的上限不许为负"
        assert self.cooldown.turns >= 1, "冷却轮数必须 ≥ 1（0 会让冷却变成「本轮也挡」）"
