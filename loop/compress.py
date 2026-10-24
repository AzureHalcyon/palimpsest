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

from .context import Band, Block, now, serialize_entries, time_range


class CompressError(RuntimeError):
    """摘要不可用；调用方应保留原文，不要假装压过。"""


SYSTEM_PROMPT = ("你是记录压缩器。只输出一个 JSON 对象，从 { 开始、到 } 结束；"
                 "不要解释、不要 markdown、不要代码围栏。")


def truncate(text: str, limit: int) -> str:
    text = str(text or "")
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def build_prompt(text: str, start: str, end: str) -> str:
    lines = [
        "你在整理刚结束的一段记录，用来替代原文留在历史里。",
        "",
        "要求：",
        "1. 越早的部分越略，越靠近结尾越具体；结尾写“现在到哪了”。",
        "2. 保留专名、数字、决定、未完成；不编造。",
        "3. 时间在段头，正文里不必再写日期。",
        "4. 不要写“压缩”“摘要”这类过程词。",
        "",
        "记录（%s–%s）：" % (start[5:16], end[11:16]),
        str(text or "").strip() or "（空）",
        "",
        "只输出 JSON（不要代码围栏）：",
        '{"summary": "...", "keywords": ["..."], "unfinished": "..."}',
    ]
    return "\n".join(lines)


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


class Compressor:
    def __init__(self, config, credentials=None,
                 summarizer: Optional[Callable[[str], dict]] = None):
        self.config = config
        self.summarizer = summarizer or self._model_summarizer(credentials)

    def _model_summarizer(self, credentials):
        if credentials is None:
            raise ValueError("没有凭证，调不了摘要模型")
        provider = self.config.summary_provider
        if provider == "auto":
            provider = ("bailian" if "qwen" in self.config.summary_model.lower()
                        else "deepseek")
        if provider == "bailian":
            key = credentials.summary_key or credentials.chat_key
            url = credentials.summary_url or credentials.chat_url
        else:
            key = credentials.chat_key
            url = credentials.chat_url
        client = ChatClient(
            ChatConfig(model=self.config.summary_model,
                       temperature=0.2,
                       max_tokens=self.config.summary_max_tokens,
                       timeout=self.config.timeout,
                       retries=self.config.retries),
            Credentials(api_key=key, chat_url=url))

        def call(prompt: str) -> dict:
            try:
                message = client.chat([{"role": "system", "content": SYSTEM_PROMPT},
                                       {"role": "user", "content": prompt}])
            except ChatError as exc:
                raise CompressError("摘要调用失败：%s" % exc) from exc
            return parse_summary(str(message.get("content") or ""))

        return call

    def compress_block(self, block: Block) -> dict:
        text = serialize_entries(block.entries)
        start = block.start_ts or now()
        end = block.end_ts or now()
        prompt = build_prompt(text, start, end)

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
