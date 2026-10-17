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


# --------------------------------------------------------------------------- 读取器
@dataclass
class ReaderConfig:
    tau: float = 0.02
    """范围旋钮（softmax 温度）。
    【实测】在 **1156 条真实查询**上做 τ×mass 网格，按"上限被绑定的比例最低"排序：
    `τ=0.02 / mass=0.70` 这一格**触顶占比最低（7.8%）**；τ≥0.03 触顶冲到 12–40%，
    τ≤0.015 则要 44–58% 的轮次靠下限兜。"""

    mass: float = 0.70
    """累计质量。【实测】同一网格：取 0.9 时中位条数从 4 跳到 **12**、触顶 40%；
    取 0.5 时触底 68%。"""

    min_items: int = 1
    """下限。**1 = 不设下限**。
    【推导】已定的原则是「**读取的输出是连续表示，条数本来就该由分布自己决定**」
    ⇒ **下限本身就是个旋钮，与这条原则冲突**。
    【实测】原始条数分布：24% 的轮次本来就该给 1 条、39% ≤2 条；设成 3 会把这 39% 全兜成 3 条
    （80 轮里 70% 实际给 3 条 ⇒ 名义上连续表示，实际是固定条数）。"""

    max_items: int = 20
    """上限（**成本截断**，不是设计旋钮）。
    【实测】取 20 时触顶 7.8%、最坏 20 条 ≈ 480 字；放到 40 只把触顶从 7.8% 降到 0.3%，
    代价是每次请求的文本翻倍；放到 8 则触顶 26%。"""

    gate_enabled: bool = False
    """★★ **阈值门**（2026-10-06 接）：开 = 相关度低于 `gate_tau` 的条目**直接不要** ⇒ **允许一条都不给**。
    没有它，"我根本没货"永远表现为"给你几条最不像样的" —— 模型分不出"查到了"与"没查到"，
    就会拿**它自己的行为准则**当**关于他的事实**用（2026-10-06 真机上抓到的现场）。

    ⚠️ **默认关**，理由是**量出来的**（完整推导见 `docs/参数与依据.md` §1）：
      ① **它救不了这一类**：问"关于他的事"而池子里没有时，分数**反而更高**
         （实测三例 top-1 = 0.588/0.595/0.660，而 200 条真实中文问法的**最高**才 0.621）
         ⇒ 要筛掉它们，得把 96% 的正常轮次一起筛掉；
      ② **它依赖编码器与池子**：τ 是**余弦上的绝对量**，换编码器/换池子立刻作废
         （离线自检用替身编码器，量纲完全不同 ⇒ 开着会让自检整体失真）。
    ⇒ 所以它按**用户可调项**给（设计里原本就把它列在可调项里），由人决定开不开。

    ⚠️ **用两个旋钮（`enabled` + `tau`）而不是拿 0 当"关"** —— 与冷却掩膜同一个先例：
    拿 0 当哨兵值迟早会被读成"设成 0 会怎样"，而"关"和"门槛取 0"是两件事。
    """

    gate_tau: float = 0.345
    """阈值门的下界（**只在 `gate_enabled` 为真时生效**）。
    ★ **本池子 + `qwen3.7-text-embedding@1024` 的标定值：0.345**。
      来法：200 条真实中文问法（`ds-export`，确定性抽样）的 top-1 分布 **p10 = 0.345**
      ⇒ 约 **10% 的轮次变成"没想起"**。与旧语料上那个甜点是**同一个操作点**
      （第十轮实测：覆盖率 0.898 那一格，精度 0.657→0.733、每命中代价 1.51→1.42）。
    ⚠️ **换编码器 / 换池子之后这个数立刻作废**，必须重新标定（方法见 `docs/参数与依据.md` §1）。
    """

    @property
    def clip(self) -> tuple:
        return (self.min_items, self.max_items)


# --------------------------------------------------------------------------- 时间与临时池
@dataclass
class TimeConfig:
    tau_state: float = 5.0
    """临时条目的衰减常数，**单位 = 天**。
    【推导】要求「**他下次再来时应该还记得**」⇒ **一次典型间隔后存活度 w ≥ 0.5**
    ⇒ `tau ≥ 中位间隔 / ln2 = 3 / 0.693 = 4.3` 天，取 5.0 留余量（此时一次典型间隔后 w = 0.55）。"""

    eps: float = 0.14
    """存活度低于它就移除。
    【推导】要求「**只漏一次还活着、连漏两次就淡掉**」⇒ 存活 = 2 个中位间隔
    ⇒ `eps = exp(-2) = 0.135`，取 0.14 ⇒ 存活 `5.0 × ln(1/0.14) = 9.8 天`。"""

    tag_mult: Dict[str, float] = field(default_factory=lambda: {
        "他当时的状态": 0.5,
        "悬着的提问": 0.75,
        "我的分寸提醒": 1.5,
        "悬着的事": 2.0,
    })
    """按条目的 tag 分档：`该条的 tau = tau_state × 倍数`。
    【推导】+语义判断：倍数**是语义推的、不是量出来的** ——"状态"的有效期就是这几天；
    "悬着的事"的有效期是事完之前。**陌生 tag 一律 1.0**（不许猜）。"""

    hanging_tags: tuple = ("悬着的事", "还没说完的", "没说完的")
    """★ 打了这些 tag 的临时条目 = **「悬着的事」**：**不按时间退**，只由 `done` 结束。

    用户 2026-10-05 裁："给'悬着的事'一类**不按时间退**（只由 `done` 结束）。"
    理由：这类事该在**"事办完了"**时结束，不该在**"88 天没提"**时结束 ——
    否则他会把"他让我给自己起个名"这种事忘掉。

    ⚠️ **按 tag 精确匹配**（不是模糊包含）：模型的 tag 是自由文本，模糊匹配会误伤。
    ⛔ **别把 `悬着的提问` 放进来** —— 它的 `tag_mult` 是 **0.75**（"淡得更快"），
      与"永不衰减"直接打架。一个 tag 要么走时间、要么走结案，**不能两头都要**。
    ⛔ 代价要认清：**tag 写歪了就不生效** ⇒ 提示词里必须把这个 tag 的含义说清楚
      （`DREAM_SYS` 里已写明"用这个 tag 的条目不会自己淡忘"）。
    ⚠️ 另一个代价：这些条目**永远不会自己消失** ⇒ 池子里会长期挂着几条。
      兜底是 `done`（模型每场都会看一眼清单），以及**它们仍在界面上看得见**。
    """

    tau_mid: float = 45.0
    """★★ **中期**的衰减常数（天）—— 升迁上来的条目走这一档（2026-10-05 新增）。

    **为什么要这一档**（用户原话）：
      · "把短期记忆和长期记忆之间再加一档" · "目前的长期就是永久的。所以不如顺着加一个中期，给一个慢的衰减"
      · "永久的这个要慎重"
    ⇒ 在"几天就淡"和"永远不淡"之间，需要一个**慢了但仍有下坡路**的档。

    【推导】要求「**反复出现过的事，留一个季度左右；一个季度不提就退出去**」
    ⇒ 存活 = `tau × ln(1/eps)` = `tau × 1.966` ⇒ `tau = 90 / 1.966 ≈ 45.8`，取 **45 天**
    （存活 **88.5 天**）。
    ⚠️ **【假设】** —— 与 `gap_med` 同一性质：**没有实测数据**（不知道用户多久提一次旧事）。
      写在这里就是要它露在明处。
    ⛔ **它跟 `long` 不是一回事**：`long` 只是"已升迁"的标记；**衰减看 `tau`**。
      （旧实现在 `w()` 里 `if it.long: return 1.0` 短路 ⇒ **那才是"长期事实上等于永久"的根源**。）"""

    gap_med: float = 3.0
    gap_sigma: float = 0.8
    gap_max: float = 30.0
    """两次见面间隔的中位数 / 对数正态 sigma / 上限（天）。
    ⚠️ **【假设】** —— **没有任何实测数据**（不知道用户多久来一次）。写在这里就是要它
    **露在明处**：中位 3 天 ⇒ 均值 4.1 / p75 5.1 / p90 8.4 / p99 19.4 天。
    ★ **真人在 `tools/talk.py` 里用真挂钟时间 ⇒ 这一项自然消失**（只在模拟里用）。"""

    promote_hits: int = 2
    """规则升迁门槛：被真实重复提起满几次就改成长期记住。
    【实测】各场结束时池内计数分布：**0:62 · 1:28 · 2:21 · 3:2**。
    设 1 ⇒ 再有 28 条升长期（合计 51/74，短期池名存实亡）；设 3 ⇒ 只有 2 条。
    **2 是唯一让"短期"还剩一半的分界**。"""


# --------------------------------------------------------------------------- 归档（dreaming）
@dataclass
class DreamConfig:
    turns: int = 12
    """归档对话最多几个模型回合。**★ 2026-10-06 从 5 放宽到 12**（用户口径：
    「**不要把 dreaming 压得那么急迫**，可以慢慢来，可以多轮思考、调用工具」）。
    【实测·旧】原来的 5 是照"实际最大用到 4 再留余量"定的 —— 但那个读数是**它被 5 卡着**跑出来的，
    不能用来论证 5 够用（**上限自己会造出"刚好用完"的分布**）。
    ⛔ 放宽不等于无限：**收口权交给模型自己**（见 `require_deliver`），这个数只是**硬护栏**。"""

    require_deliver: bool = True
    """★★ **交付判定权归模型**：它可以在 JSON 里写 `"deliver": false` + `"todo": "还差什么"`，
    表示"我还没弄干净" ⇒ **不交付**，带着它自己的 `todo` 再给一轮（可以继续查、继续改）。
    只有它写 `deliver: true`（或缺省）才算交付。
    ★ 为什么要这样：归档原来**跑满回合就强制收口**（"给出最终 JSON（不要再查了）"），
    于是"改干净了没有"这件事**没有机会被检查** —— 实测两次模拟里都留下了没改净的条目。
    ⛔ 关掉它（`False`）= 回到旧行为，留作**开/关对照**的入口。
    ⚠️ 与 `companion/deliver.py` 的"投递"**不是一回事**：那个是"要不要对他开口"，这个是"归档做完了没有"。"""

    temperature: float = 0.3
    json_mode_retries: int = 3
    """解析不出 JSON 时，不给工具、强制 JSON 再试几次。
    【实测】8 场里 4 场第一次解析失败，**全部在 1 次修补内救回**（0 场需要第 2 次）⇒ 取 3 留余量。"""

    resume_attempts: int = 2
    """★ 一场归档**最多试几次**（含首次）—— 崩在中途、或模型给不出合法 JSON 时，
    下次启动会补做（见 `engine.archive` / `pending.py`）。
    【护栏】取 2 = "首次 + 补做一次"：每次尝试是一次真金白银的模型调用
    （一次真实的模型调用；见 `docs/参数与依据.md`），
    而"给不出合法 JSON"这类失败是**半确定性**的，重试的期望收益低。
    ⇒ 试满就按空归档落地，**并把失败写进转写**（可见，不许静默）。
    ⛔ 不取无限：手机上每次启动都重试一遍，会变成看不见的持续扣费。"""

    topk: int = 0
    """归档里"查池子"给回几条。**0 = 跟随 `max_items`**。
    【推导】归档走的是**同一个读取器** ⇒ 截断上限应与正常读取一致，**不另造一个数**。"""


# --------------------------------------------------------------------------- 会话
@dataclass
class SessionConfig:
    max_pull: int = 2
    """每轮最多几次"再查一次"。【护栏】防模型反复查。
    【实测】80 轮里只触发 2 次、都在 1 次往返内完成 ⇒ **这个上限从没被碰到**。"""

    push_header: str = "【这一轮想起的】"
    """注入内容的呈现头。⚠️ 措辞属于"呈现形态"，改动要留痕。"""


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
    "重要的事更靠前 / 更不易忘"是**深刻度**（`SalienceConfig`）的事 —— 两者别混
    （这个混法在 `00_项目文档.md §8.3` 错题集里一天犯过四次）。
    """

    use_recall: bool = True
    """★ 取件走**新路**（`[R] 回忆`：件/标记集/传播）还是旧读取器（池子 + 余弦 + softmax）。
    `True`（默认）= 新路；`False` = 兜底回旧路（真机出问题时的开关，⛔ 不是长期选项）。
    ⚠️ 旧路早晚要删（作者口径：「删掉，反正旧仓库有完整的」）；留着只为"新路还没上过真机"。"""

    render_recall: bool = True
    """★ 注入要不要走 `[W] 编写`（把件整理成**一段回忆**）—— 2026-10-17 作者口径：
    「原来的条目变成一段」。
    · `True`（默认）：取件 → 编写 → 一段；编写产物落 `<cache>/说/`
    · `False`：退回旧样子（直接铺条目清单，省一次调用）
    ⚠️ 编写要多一次模型调用 ⇒ 那一轮首字延迟会多 1–3 s；先按 True 跑，量过再定默认。"""
    enabled: bool = True
    """★ 总开关。开着 = 每次 push 之后，把这一轮**真正注进去的**那几条冷却掉。

    ⚠️ 关掉它的用途只有两个：① 跑"开 / 关"前后对照（**还没跑**，见
    `docs/未接线与已知缺口.md`）；② 万一它把 push 削得太狠，先关掉止血。
    """

    turns: int = 3
    """一条被注入后，**接下来几轮之内不再注入它**（轮 = 一次 `ask`）。

    ⚠️ **【假设】—— 没有实测依据**。与 `TimeConfig.gap_med` 同一性质，
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


# --------------------------------------------------------------------------- 深刻度（已设计、**未落地**）
@dataclass
class SalienceConfig:
    enabled: bool = False
    """★ **默认关**。

    设计上已定：条目除了"相关度"（与当前情境像不像）之外，还有一个**正交**的量叫
    **深刻度**（这条在系统心里有多重，由"被真实重复提起过几次"累积而成），
    它应该 ① 让条目**不易被遗忘**、② 让条目在**同一批被想起的东西里更靠前**。
    ⚠️ **但前后对照实验没跑** ⇒ 本仓库**只把接法备好、默认不生效**，不当已实现的成果。

    接法：`w = 1 + n_d + n_p`（`n_d` = 实际被提起的次数；`n_p` = 外部材料带来的伪计数，
    **每份材料最多 +1、用过即退出**）。开启后：**条数仍只由相关度定**（⛔ 深刻度不许参与入选），
    只把这批的**顺序**改成 `相关度 + κ·log w`。"""

    kappa: Optional[float] = None
    """深刻度在排序里的权重。⚠️ **未标定**：相关度是余弦 ∈ [-1,1]，而 `log w` 的幅度可能更大
    ⇒ **κ 不定会让深刻度盖过相关度**。⇒ 未标定就保持 `None`（= 不生效）。"""

    log_w_cap: float = 6.0
    """【护栏】`n_d` 无界 ⇒ `κ·log w` 无界 ⇒ 反复被强化的条目会**压倒相关度** ⇒ 必须封顶。"""


# --------------------------------------------------------------------------- 汇总
@dataclass
class MergeConfig:
    """**落档前核对**（`MERGE_SYS`）的旋钮 —— 2026-10-05 新增。

    用户口径："加一个环节，在写新条目之前，先算一下和已有条目的关联，要能把互相打架的、
    重复的等找出来，然后要求 AI 重新想，或者更新旧条目。"
    "所有新条目都要这样算一下，而且**不同层之间的也是**。"
    """

    enabled: bool = True
    """★ **总开关**。开着 = 每次归档**多一次模型调用**做落档前核对。

    ⚠️ 关掉它的两个用途：① 离线自检里 `ScriptedLLM` 是**按顺序吐的队列**
    （多一次调用会把脚本吃错位）；② 万一它误判太多，先关掉止血。"""

    top_k_per_layer: int = 3
    """★ **每层各取几条**最近邻给模型看。

    ⛔ **不能全池混着取** —— 实测：一条 fact 在全池里最近的是**底色**（0.655），
    而真正相关的 fact 只排第二（0.600）⇒ 混着取会让**底色淹没信号**；
    更糟的是模型会想去改底色，而那是**手写层**。
    ★ 也**不用阈值**：实测打架的对落在 **0.497–0.742**，而不相关的对能到 **0.794**
    ⇒ **全局阈值不存在**。所以机械这步只"端候选"，判定权交给模型。"""

    max_updates_per_session: int = 3
    """一场最多**改写**几条旧条目。
    用户："更新旧条目这个也要约束，**不能因为能更新就让旧条目无限膨胀**。"
    ⚠️ 防膨胀的**根本**是"就地改写 ⇒ 条目数不涨"；这个上限防的是"一场大改一通"。"""

    merge_max_len: int = 60
    """`merge` 之后允许的最大长度（字）。超了要求模型改用 `replace`
    —— 否则一条会**越合越长**，那也是一种膨胀。"""

    temperature: float = 0.2
    json_mode_retries: int = 2
    """给不出合法 JSON 时再试几次（与归档同一套判据：半确定性失败，重试期望收益低）。"""


@dataclass
class Config:
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    reader: ReaderConfig = field(default_factory=ReaderConfig)
    time: TimeConfig = field(default_factory=TimeConfig)
    dream: DreamConfig = field(default_factory=DreamConfig)
    merge: "MergeConfig" = field(default_factory=lambda: MergeConfig())
    session: SessionConfig = field(default_factory=SessionConfig)
    salience: SalienceConfig = field(default_factory=SalienceConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)

    def check(self) -> None:
        """自检：把"互相关联、必须一致"的约束钉在这里（启动时跑一次）。"""
        r = self.reader
        assert 0.0 < r.mass <= 1.0, "mass 必须在 (0,1]"
        assert r.tau > 0, "tau 必须 > 0"
        assert r.min_items >= 1 and r.max_items >= r.min_items, "clip 区间不合法"
        if r.gate_enabled:
            assert 0.0 < r.gate_tau < 1.0, (
                "阈值门开着时，下界必须落在 (0,1)：取 0 等于门不存在"
                "（正余弦条目都会过门），取 ≥1 等于永远不注入")
        assert 0.0 < self.time.eps < 1.0, "eps 必须在 (0,1)"
        assert self.time.tau_state > 0, "tau_state 必须 > 0（单位：天）"
        assert self.dream.json_mode_retries >= 1
        assert self.cooldown.turns >= 1, "冷却轮数必须 ≥ 1（0 会让冷却变成「本轮也挡」）"
        if self.salience.enabled:
            assert self.salience.kappa is not None, (
                "深刻度打开了但 kappa 没标定 ⇒ 深刻度会盖过相关度；要么标定，要么关掉")


def dream_topk(cfg: Config) -> int:
    """`dream.topk = 0` ⇒ 跟随 `max_items`（不另造一个数）。"""
    return cfg.dream.topk or cfg.reader.max_items
