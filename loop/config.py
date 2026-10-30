"""循环的设置与凭证。

单独一份，不套 `memory_v2` 的 `ChatConfig`：这里多了预算、五段、块大小这些运行参数。
凭证格式与 `memory_v2` 一样（`refs:` 段），同一个 YAML 两边都能用。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_CREDENTIALS = os.path.join("~", ".dsh", ".credentials.yaml")
DEFAULT_CHAT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_SUMMARY_MODEL = "deepseek-flash"   # 摘要走对话那家，默认跟主模型同一族


class LoopConfigError(RuntimeError):
    """配置或凭证不可用。"""


@dataclass
class LoopCredentials:
    chat_key: str
    chat_url: str = DEFAULT_CHAT_URL
    # 百炼（阿里云 MAAS）那一组：语音转写的键、嵌入地址的提示用它。
    # 摘要不走这里——它跟上面的对话那家（换端点就是换对话地址）。
    maas_key: str = ""
    maas_url: str = ""
    # 搜索用的 key（Tavily）；没有就用免费的 Bing RSS，质量差一截。
    tavily_key: str = ""


def chat_url_of(value) -> str:
    """把渠道地址归一成 `…/chat/completions`：写 `…/v1` 也认。"""
    url = str(value or "").strip().rstrip("/")
    if not url:
        return DEFAULT_CHAT_URL
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def load_credentials(path: str = "") -> LoopCredentials:
    target = Path(os.path.expanduser(
        path or os.environ.get("LOOP_CREDENTIALS") or DEFAULT_CREDENTIALS))
    if not target.exists():
        raise LoopConfigError("凭证文件不存在：%s" % target)
    try:
        refs = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}).get("refs") or {}
    except Exception as exc:
        raise LoopConfigError("凭证文件读不了：%s" % type(exc).__name__) from exc
    # 换渠道不用改代码：认 LOOP_CHAT_KEY / LOOP_CHAT_URL，没写才退回 DEEPSEEK_*。
    # URL 写 `…/v1` 或整条 `…/v1/chat/completions` 都行。
    key = str(refs.get("LOOP_CHAT_KEY") or refs.get("DEEPSEEK_API_KEY") or "")
    if not key:
        raise LoopConfigError("凭证里没有 LOOP_CHAT_KEY / DEEPSEEK_API_KEY：%s" % target)
    base = str(refs.get("ALIYUN_MAAS_BASE_URL") or "").rstrip("/")
    return LoopCredentials(
        chat_key=key,
        chat_url=chat_url_of(refs.get("LOOP_CHAT_URL") or refs.get("DEEPSEEK_CHAT_URL")),
        maas_key=str(refs.get("ALIYUN_MAAS_API_KEY") or ""),
        maas_url=(base + "/chat/completions") if base else "",
        tavily_key=str(refs.get("TAVILY_API_KEY") or ""),
    )


@dataclass
class LoopConfig:
    name: str = ""                    # sink 第一句的名字；空就是没定 persona
    user_name: str = "对方"          # 对方的称呼：压缩文本里"它："代替"对方："，sink 第一句也用它
    recall_tool: str = "recall"       # sink 里写的搜索工具名
    wait_tool: str = "wait"           # sink 里写的「停下等」工具名
    # 对方发来的每一条，进上下文时前面拼一个【标记】：名字会改、会重名，
    # 这个方括号才是「这句是我的人说的」的凭据。空 = 用 user_name。
    owner_mark: str = ""
    lexical: bool = False             # 词面检索；默认关，只用嵌入
    recent_steps: int = 0             # 最近 N 步不入召回；0=关
    model: str = "deepseek-flash"
    temperature: float = 0.8
    max_tokens: int = 4000
    stream: bool = True               # 走 SSE：网页能看到她边想边说（关掉就整段等回来）
    reasoning_effort: str = "low"     # 思考强度：默认 low；空=端点默认；high / max 更费
    timeout: float = 600.0
    retries: int = 3

    # 预算都按 token 估（见 context.estimate_tokens）；真实用量以服务端 usage 为准。
    # 两块原文最多 40k + 四段摘要 30k + sink ≈ 71k，所以 80k 起。
    context_budget: int = 200000      # 一次请求最多多少 token（官方 V4.1 有 1M，先吃 200k）
    band_budget: int = 30000          # 四段旧摘要合计
    block_tokens: int = 50000         # 当前块攒到这么多就攒块
    summary_model: str = DEFAULT_SUMMARY_MODEL
    # 0 = 跟主模型同一份预算（config.max_tokens）。别给摘要单独卡一个小额度：
    # 这个模型会先把 token 花在思考上，小额度的结果是"想了 4000 token、正文一个字没有"，
    # 压缩每次都失败、块永远压不下去（2026-10-30 线上就这么卡住的）。
    summary_max_tokens: int = 0

    # 嵌入（memory_v2 的向量身份）：模型与维度要配对，换一个就得连另一个一起对，
    # 不然缓存里旧向量按 (model, dim) 认不出来，召回会静默变差。
    embed_model: str = "qwen3.7-text-embedding"
    embed_dim: int = 1024

    # 旧记录压缩成的"那一层"：**只启用一档**（四档那套代码还在，不启用而已）。
    # 一档就是一个上限——这块摘要压成多长；`add_band` 见到只有一个数就只留一条。
    # 哪天想回到"多层"，把这个元组写成 (1200, 2400, 4800, 9600) 就行。
    band_chars: tuple = (9600,)
    # 往老一格至少缩到这个比例（只有一档时它不起作用，留着给多档那套）。
    band_decay: float = 0.8

    # 每块留最近几条超长工具结果原文，更早的折成一行；<=0 关。工具是持续调的，
    # 结果不折会把块塞满，把对白挤出去。
    tool_fold_keep: int = 2
    # 图的保质期（步）：最近这么多步里的带图消息才真把图发出去。更早的只留一行
    # 「（一张 … 的图：收件/…）」，要看她自己 look。0 = 一步之后就不带图了。
    image_keep_steps: int = 10
    # `remember` 那声 note 在上下文里留几步；过了换成一句占位（内容在记忆里）。
    note_keep_steps: int = 10
    # 「还有 N 条没看的消息」这类提醒留几步。
    unread_keep_steps: int = 6

    # 命令工具：跑在宿主机（云实例）上，不用沙盒，只加一条规则——第一段程序名必须在
    # 白名单里，而且不经过 shell（管道、重定向、$() 进不来）。默认关，默认只给读的。
    run_enabled: bool = False
    run_sandbox: bool = True          # 关进 Landlock：只有工作区能写；关掉等于把它放出来
    run_file_mb: int = 64             # 单个文件最大多少 MB（RLIMIT_FSIZE）
    # 她自己写的自我描述（<data>/persona.md）：平时不提，隔一阵子自动重写一次。
    # 改一次会让 system 的前缀缓存整段作废，所以内容归她、时机归定时。
    persona_enabled: bool = True
    persona_chars: int = 1200         # 这段描述最多多少字
    persona_hours: float = 24.0       # 隔多久重写一次；0 = 不自动重写
    persona_min_chars: int = 20000    # 上次改写之后至少攒这么多字才重写
    persona_feed_chars: int = 30000   # 一次整理最多喂多少字；0 = 全部（含旧段摘要）
    persona_with_bands: bool = True   # 旧段摘要要不要一起喂（长程连续性）
    persona_model: str = ""           # 整理自我描述用哪个模型；空 = 跟主模型走
    # 归档：把旧段摘要 + 上一块原文补录进事件图，写一段日记，重写自我描述，跑一次重放。
    # 手动点「归档」触发；当前块不动——归档不改变她眼前的现场。
    archive_enabled: bool = True
    archive_max_events: int = 8       # 一次补录最多几件（克制：宁可少记）
    archive_feed_chars: int = 40000   # 一次最多喂多少字材料
    archive_short_days: int = 3       # scope=short 的默认有效期（天）
    # 墙钟多久刷一次（秒）：有人说话、刚醒来一定给；其余步按这个间隔给，
    # 中间那些步只放一句"接着上面"。0 = 每步都给。
    clock_refresh: int = 120
    run_timeout: float = 20.0         # 单条命令最多跑多久；超时连进程组一起杀
    # 工作区写入口（`<data>/workspace` 下）：只写相对路径，绝对路径和 `..` 都拒。
    # 不并进 run 的白名单——`<data>/` 服务用户可写，那样等于把改库的权限也交出去。
    write_enabled: bool = True
    write_max_chars: int = 100000     # 单次最多写多少字
    run_output_chars: int = 4000      # 输出最多留多少字：头 60% + 尾 40%

    watchdog_idle: int = 6            # 连续几步没有外部输入就问一句「还在做事吗」（0=关）
    retry_delay: float = 5.0          # 模型调不通时的退避起点（秒），之后翻倍
    park_seconds: float = 0.0         # >0 才空转等待；0 表示挂起就停
    standby: bool = False             # 有输入通道（inbox 有人往里写）才真等
    max_steps: int = 0                # 0 表示不设上限，由调用方控制

    data_dir: str = "data/loop"
    db_name: str = "loop.sqlite3"

    # 成本上限：按天算真实 usage（输入 + 输出）。0 = 不限。
    # 到 cap 就等到第二天零点，期间消息照收、不发新请求；到 warn 只在输出里提一句。
    daily_token_cap: int = 0
    daily_token_warn: int = 0

    def db_path(self) -> str:
        return os.path.join(self.data_dir, self.db_name)
