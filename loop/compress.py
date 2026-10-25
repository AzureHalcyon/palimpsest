"""压缩：一块一次，只压刚结束的那一块。

旧摘要不跟着重写；新摘要进最近一档，旧段随档位截短。
压缩只写上下文，不写 memory_v2。失败就保留原文，等块再长一点。
"""

from __future__ import annotations

import json
import re
from typing import Callable, List, Optional

from memory_v2.core.config import ChatConfig, Credentials
from memory_v2.core.llm import ChatClient, ChatError

from . import prompt_files
from .context import Band, Block, now, serialize_entries


class CompressError(RuntimeError):
    """摘要不可用；调用方应保留原文，不要假装压过。"""


def truncate(text: str, limit: int) -> str:
    text = str(text or "")
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def build_prompt(text: str, start: str, end: str, user_name: str = "") -> str:
    """整段提示在 prompts/compress.txt；这里只给时间、对方的名字和原文。"""
    return prompt_files.render("compress.txt",
                               user=str(user_name or "").strip() or "对方",
                               start=start[5:16], end=end[11:16],
                               text=str(text or "").strip() or "（空）")


def parse_summary(text: str) -> dict:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except ValueError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise CompressError("摘要不是 JSON（前 200 字：%r）" % raw[:200])
        try:
            payload = json.loads(raw[start:end + 1])
        except ValueError as exc:
            raise CompressError("摘要不是 JSON（前 200 字：%r）" % raw[:200]) from exc
    if not isinstance(payload, dict):
        raise CompressError("摘要不是对象")
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise CompressError("缺少 summary")
    keywords = [str(item).strip() for item in (payload.get("keywords") or [])
                if str(item).strip()]
    return {"summary": summary,
            "keywords": keywords[:8],
            "unfinished": str(payload.get("unfinished") or "").strip()}


def add_band(bands: List[Band], summary: str, start_ts: str, end_ts: str,
             limits, decay: float) -> List[Band]:
    """新摘要进最近一档；旧段下移，按档位截短，最旧的挤出。"""
    items = [(band.text, band.start_ts, band.end_ts)
             for band in sorted(bands, key=lambda band: band.level)]
    items.append((summary, start_ts, end_ts))
    if len(items) > len(limits):
        items = items[-len(limits):]

    shaped = [None] * len(items)
    newer_length = None
    for index in reversed(range(len(items))):
        level = len(limits) - 1 - (len(items) - 1 - index)
        limit = limits[level]
        if newer_length is not None:
            limit = min(limit, int(newer_length * decay))
        text = truncate(items[index][0], max(1, limit))
        shaped[index] = Band(level, text, items[index][1], items[index][2])
        newer_length = len(text)
    return shaped


def make_client(config, credentials, model: str, temperature: float = 0.2,
                max_tokens: Optional[int] = None) -> ChatClient:
    """给某个模型造一个客户端：渠道按 summary_provider（auto 时看模型名）。"""
    if credentials is None:
        raise ValueError("没有凭证，调不了模型")
    provider = str(getattr(config, "summary_provider", "auto") or "auto")
    if provider == "auto":
        provider = "bailian" if "qwen" in str(model).lower() else "deepseek"
    if provider == "bailian":
        key = credentials.summary_key or credentials.chat_key
        url = credentials.summary_url or credentials.chat_url
    else:
        key = credentials.chat_key
        url = credentials.chat_url
    return ChatClient(
        ChatConfig(model=str(model), temperature=temperature,
                   max_tokens=max_tokens or config.max_tokens,
                   timeout=config.timeout, retries=config.retries,
                   reasoning_effort=str(getattr(config, "reasoning_effort", "") or "")),
        Credentials(api_key=key, chat_url=url))


class Compressor:
    def __init__(self, config, credentials=None,
                 summarizer: Optional[Callable[[str], dict]] = None):
        self.config = config
        self.credentials = credentials
        # 自检塞进来的假摘要器：网页改模型时不许被换掉。
        self._custom = summarizer is not None
        self.summarizer = summarizer or self._model_summarizer(credentials)

    def rebuild(self) -> None:
        """换了摘要模型 / 端点之后重建客户端（网页上改参数时调，不用重启）。"""
        if self._custom or self.credentials is None:
            return
        self.summarizer = self._model_summarizer(self.credentials)

    def _model_summarizer(self, credentials):
        client = make_client(self.config, credentials, self.config.summary_model,
                             temperature=0.2,
                             max_tokens=self.config.summary_max_tokens)

        def call(prompt: str) -> dict:
            try:
                message = client.chat(
                    [{"role": "system",
                      "content": prompt_files.text("compress-system.txt")},
                     {"role": "user", "content": prompt}])
            except ChatError as exc:
                raise CompressError("摘要调用失败：%s" % exc) from exc
            return parse_summary(str(message.get("content") or ""))

        return call

    def compress_block(self, block: Block) -> dict:
        text = serialize_entries(block.entries,
                                 user_name=getattr(self.config, "user_name", ""))
        start = block.start_ts or now()
        end = block.end_ts or now()
        prompt = build_prompt(text, start, end,
                              user_name=getattr(self.config, "user_name", ""))

        payload = None
        last = None
        for _attempt in range(2):
            try:
                payload = self.summarizer(prompt)
                break
            except CompressError as exc:
                last = exc
                continue
        if payload is None:
            raise CompressError(str(last))
        return {"summary": payload["summary"],
                "keywords": payload["keywords"],
                "unfinished": payload["unfinished"],
                "start_ts": start, "end_ts": end}
