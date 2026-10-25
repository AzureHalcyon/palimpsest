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
DEFAULT_SUMMARY_MODEL = "qwen-plus"


class LoopConfigError(RuntimeError):
    """配置或凭证不可用。"""


@dataclass
class LoopCredentials:
    chat_key: str
    chat_url: str = DEFAULT_CHAT_URL
    # 摘要走百炼的 compatible-mode；步骤 0 用桩，还没用到这里。
    summary_key: str = ""
    summary_url: str = ""
    summary_model: str = DEFAULT_SUMMARY_MODEL
    # 搜索用的 key（Tavily）；没有就用免费的 Bing RSS，质量差一截。
    tavily_key: str = ""

    def can_summarize(self) -> bool:
        return bool(self.summary_key and self.summary_url)


def load_credentials(path: str = "") -> LoopCredentials:
    target = Path(os.path.expanduser(
        path or os.environ.get("LOOP_CREDENTIALS") or DEFAULT_CREDENTIALS))
    if not target.exists():
        raise LoopConfigError("凭证文件不存在：%s" % target)
    try:
        refs = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}).get("refs") or {}
    except Exception as exc:
        raise LoopConfigError("凭证文件读不了：%s" % type(exc).__name__) from exc
    key = str(refs.get("DEEPSEEK_API_KEY") or "")
    if not key:
        raise LoopConfigError("凭证里没有 DEEPSEEK_API_KEY：%s" % target)
    base = str(refs.get("ALIYUN_MAAS_BASE_URL") or "").rstrip("/")
    return LoopCredentials(
        chat_key=key,
        chat_url=str(refs.get("DEEPSEEK_CHAT_URL") or DEFAULT_CHAT_URL),
        summary_key=str(refs.get("ALIYUN_MAAS_API_KEY") or ""),
        summary_url=(base + "/chat/completions") if base else "",
        summary_model=str(refs.get("LOOP_SUMMARY_MODEL") or DEFAULT_SUMMARY_MODEL),
        tavily_key=str(refs.get("TAVILY_API_KEY") or ""),
    )


@dataclass
class LoopConfig:
    name: str = ""                    # sink 第一句的名字；空就是没定 persona
    recall_tool: str = "recall"       # sink 里写的搜索工具名
    wait_tool: str = "wait"           # sink 里写的「停下等」工具名
    lexical: bool = False             # 词面检索；默认关，只用嵌入
    recent_steps: int = 0             # 最近 N 步不入召回；0=关
    model: str = "deepseek-flash"
    temperature: float = 0.8
    max_tokens: int = 4000
    timeout: float = 600.0
    retries: int = 3

    # 预算都按 token 估（见 context.estimate_tokens）；真实用量以服务端 usage 为准。
    # 两块原文最多 40k + 四段摘要 30k + sink ≈ 71k，所以 80k 起。
    context_budget: int = 80000       # 一次请求最多多少 token
    band_budget: int = 30000          # 四段旧摘要合计
    block_tokens: int = 20000         # 当前块攒到这么多就攒块
    summary_model: str = "deepseek-flash"
    # auto：模型名带 qwen 就走百炼端点，其余走主模型端点。
    summary_provider: str = "auto"
    summary_max_tokens: int = 4000

    # 嵌入（memory_v2 的向量身份）：模型与维度要配对，换一个就得连另一个一起对，
    # 不然缓存里旧向量按 (model, dim) 认不出来，召回会静默变差。
    embed_model: str = "qwen3.7-text-embedding"
    embed_dim: int = 1024

    # 四段旧记录的长度上限（字符）：0 最旧，末位最近。每老一格再截短。
    band_chars: tuple = (1200, 2400, 4800, 9600)
    # 往老一格至少缩到这个比例，保证分辨率从后往前单调下降。
    band_decay: float = 0.8

    # 每块留最近几条超长工具结果原文，更早的折成一行；<=0 关。工具是持续调的，
    # 结果不折会把块塞满，把对白挤出去。
    tool_fold_keep: int = 2

    # 命令工具：跑在宿主机（云实例）上，不用沙盒，只加一条规则——第一段程序名必须在
    # 白名单里，而且不经过 shell（管道、重定向、$() 进不来）。默认关，默认只给读的。
    run_enabled: bool = False
    run_allow: str = ("ls cat head tail wc grep rg file stat du df date echo"
                      " uname uptime which pwd git")
    run_timeout: float = 20.0         # 单条命令最多跑多久；超时连进程组一起杀
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
