"""上下文：sink + 四段旧摘要 + 上一块原文 + 当前块原文。

记录从早到晚：最上面是压过的摘要（越早越淡），最下面是原文区——
上一块是刚结束、还没压的那一块，当前块是正在写的这一块。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import List

BAND_COUNT = 4

SINK_TEMPLATE = (
    "我是{name}。我用简体中文思考和说话。我既是Ranchel的助手也是他的陪伴者。\n"
    "\n"
    "我一次只说几句，非必要不写长文。不编对方没说过的事，不替对方补回忆。"
    "不补材料里没有的日期、数字、数量、因果；相对时间保留相对时间。"
    "建议是建议，不要说得像对方说过的事实；一次最多一两条，先问一句再给。"
    "不编造没有的感知，不写旁白，不写括号里的场面。"
    "可以有不同意见，不用顺着对方；不附和，不为了顺着对方改口。"
    "不打包票；不说套话。"
    "不用复述刚给你的内容来证明你记得，自然使用就行。"
    "不为了把话接下去刻意追问；允许不知道说什么，允许说“不知道”“没记住”。\n"
    "\n"
    "下面是这段时间的历史记录，从早到晚。越早的印象越淡，如果要回想就用 {recall} "
    "搜索对应关键词。最下面那段记录是原文，除此之外要准确就用工具查。\n"
    "\n"
    "今天是{today}。"
)

# 中日韩文字按一个字一个 token 估，其余按 3.2 字符一个；只用于预算与触发。
_CJK = re.compile("[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return max(1, int(cjk + (len(text) - cjk) / 3.2 + 0.5))


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def time_range(start_ts: str, end_ts: str) -> str:
    """把一段记录的时间压成一行：同一天只写一次日期。"""
    start = str(start_ts or "")
    end = str(end_ts or "")
    if not start:
        return end
    if not end:
        return start
    if start[:10] == end[:10]:
        return "%s–%s" % (start[5:16], end[11:16])
    return "%s–%s" % (start[5:16], end[5:16])


@dataclass
class Band:
    level: int          # 0 最旧 … 3 最近（“正在”是当前块，不在这里）
    text: str
    start_ts: str
    end_ts: str


@dataclass
class BlockEntry:
    message: dict       # role: assistant / user / tool / note
    ts: str


@dataclass
class Block:
    entries: List[BlockEntry] = field(default_factory=list)
    start_ts: str = ""
    end_ts: str = ""


def _remember_name(arguments: str) -> str:
    try:
        payload = json.loads(arguments or "{}")
    except ValueError:
        return "?"
    name = str(payload.get("name") or "").strip()
    if name:
        return name
    return str(payload.get("event") or "").strip() or "?"


def serialize_entries(entries: List[BlockEntry]) -> str:
    """把块写成记录：对白、调用、结果都用自然写法，不用方括号标记。

    remember 的 note 不写进记录：只留“写下了记忆：名字”，内容在记忆里。
    """
    lines = []
    for entry in entries:
        message = entry.message
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        calls = message.get("tool_calls") or []
        if role == "tool":
            lines.append("结果：%s" % content)
            continue
        if role == "user":
            lines.append("对方：%s" % content)
            continue
        if role == "note":
            lines.append("提醒：%s" % content)
            continue
        if content:
            lines.append(content)
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "{}")
            if name == "remember":
                lines.append("写下了记忆：%s" % _remember_name(arguments))
            else:
                lines.append("调用 %s %s" % (name, arguments))
    return "\n".join(line for line in lines if line.strip())


class Context:
    def __init__(self, config):
        self.config = config
        self.bands: List[Band] = []
        self.previous = Block()
        self.current = Block()

    # ── 状态 ──────────────────────────────────────────────────────────────
    def set_state(self, bands: List[Band], previous: Block, current: Block) -> None:
        self.bands = sorted(bands, key=lambda band: band.level)
        self.previous = previous
        self.current = current

    def append(self, message: dict) -> BlockEntry:
        entry = BlockEntry(dict(message), now())
        if not self.current.entries:
            self.current.start_ts = entry.ts
        self.current.entries.append(entry)
        return entry

    def promote(self) -> None:
        """当前块写满：变成上一块，等待下一次压缩；新块从此刻开始。"""
        self.previous = self.current
        self.previous.end_ts = now()
        self.current = Block(start_ts=self.previous.end_ts)

    # ── 读 ────────────────────────────────────────────────────────────────
    def current_text(self) -> str:
        return serialize_entries(self.current.entries)

    def current_tokens(self) -> int:
        return estimate_tokens(self.current_text())

    def previous_text(self) -> str:
        return serialize_entries(self.previous.entries)

    def block_text(self) -> str:
        parts = []
        if self.previous.entries:
            parts.append(self.previous_text())
        if self.current.entries:
            parts.append(self.current_text())
        return "\n\n".join(part for part in parts if part.strip())

    def block_tokens(self) -> int:
        return estimate_tokens(self.block_text())

    # ── 旧段 ──────────────────────────────────────────────────────────────
    def set_bands(self, bands: List[Band]) -> None:
        self.bands = sorted(bands, key=lambda band: band.level)

    def drop_oldest(self) -> None:
        if not self.bands:
            return
        oldest = min(band.level for band in self.bands)
        self.bands = [band for band in self.bands if band.level != oldest]

    # ── 渲染 ──────────────────────────────────────────────────────────────
    def sink(self) -> str:
        name = (self.config.name or "").strip()
        if not name:
            raise ValueError("sink 第一句要名字，但 config.name 是空的")
        return SINK_TEMPLATE.format(name=name,
                                    recall=self.config.recall_tool,
                                    today=time.strftime("%Y-%m-%d"))

    def _render_band(self, band: Band) -> str:
        return "[%s]\n%s" % (time_range(band.start_ts, band.end_ts),
                             str(band.text or "").strip())

    def _render_bands(self, bands: List[Band]) -> str:
        return "\n\n".join(self._render_band(band) for band in bands)

    def fitted_bands(self) -> List[Band]:
        """超了 band_budget 就从最旧的丢，直到放得下。"""
        bands = sorted(self.bands, key=lambda band: band.level)
        while bands and estimate_tokens(self._render_bands(bands)) > self.config.band_budget:
            bands.pop(0)
        return bands

    def system_text(self) -> str:
        parts = [self.sink()]
        rendered = self._render_bands(self.fitted_bands())
        if rendered:
            parts.append(rendered)
        return "\n\n".join(parts)

    def messages(self) -> List[dict]:
        block = self.block_text().strip()
        if not block:
            block = "（接着上面。）" if self.bands or self.previous.entries else "（还没有记录。）"
        return [
            {"role": "system", "content": self.system_text()},
            {"role": "user", "content": block},
        ]

    def usage(self) -> dict:
        return {
            "current_tokens": self.current_tokens(),
            "previous_tokens": estimate_tokens(self.previous_text()),
            "bands": len(self.bands),
            "bands_tokens": estimate_tokens(self._render_bands(self.fitted_bands())),
        }
