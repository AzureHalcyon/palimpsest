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
    )


@dataclass
class LoopConfig:
    name: str = ""                    # sink 第一句的名字；空就是没定 persona
    recall_tool: str = "recall"       # sink 里写的搜索工具名
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

    # 四段旧记录的长度上限（字符）：0 最旧，末位最近。每老一格再截短。
    band_chars: tuple = (1200, 2400, 4800, 9600)
    # 往老一格至少缩到这个比例，保证分辨率从后往前单调下降。
    band_decay: float = 0.8

    watchdog_idle: int = 6            # 连续几步没有动作就提醒
    max_nudges: int = 2
    park_seconds: float = 0.0         # >0 才空转等待；0 表示挂起就停
    max_steps: int = 0                # 0 表示不设上限，由调用方控制

    data_dir: str = "data/loop"
    db_name: str = "loop.sqlite3"

    def db_path(self) -> str:
        return os.path.join(self.data_dir, self.db_name)
