"""上下文：sink + 四段旧摘要 + 上一块原文 + 当前块原文。

记录从早到晚：最上面是压过的摘要（越早越淡），最下面是原文区——
上一块是刚结束、还没压的那一块，当前块是正在写的这一块。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from . import prompt_files

# 工具结果的保留模板。同一个结果，越旧留得越少：
#   新鲜的（最近 tool_fold_keep 条）：全文，含写给模型的提示语；
#   更早的：去掉提示语——那是对「正在用的那一步」说的，历史里只剩噪声。
# 然后再按工具分：
#   recall    随时能重取，压成一行，id、名字、日期留着；
#   recollect 不压，那是记忆本身，重取要再花一次编写调用；
#   其他（以后加的）：太长就留头尾、中间标「中略」，看一眼知道是个大结果，要细节再调一次。
# 短于 _FOLD_MIN_CHARS 的结果不压：回执、报错本来就一行。
_FOLD_MIN_CHARS = 60
_FOLDABLE_TOOLS = ("recall",)
_KEEP_FULL_TOOLS = ("recollect",)
_HINT_LINES = ("看具体用", "先 recall 再 recollect", "（嵌入")
_HINT_INLINE = ("换个关键词再试",)

# 不认识的工具：超过 _TRIM_OVER 字符就留头 _TRIM_HEAD + 尾 _TRIM_TAIL。
# 参照 DSH 的工具结果剪枝（它按 8192 字符剪、留头 4096 尾 1024）；我们的块只有 20k
# token，所以整套数都压小。
_TRIM_OVER = 1500
_TRIM_HEAD = 1000
_TRIM_TAIL = 400


def _strip_hints(content: str) -> str:
    """去掉只有「当下那一步」才有用的话，其余原样留着。"""
    kept = []
    for line in str(content or "").splitlines():
        text = line.strip()
        if not text:
            continue
        if any(text.startswith(prefix) for prefix in _HINT_LINES):
            continue
        for marker in _HINT_INLINE:
            if marker in text:
                text = text.split(marker)[0].strip()
        if text:
            kept.append(text)
    return "\n".join(kept)


def _trim_keep(content: str) -> str:
    """头尾留着，中间标一句；让人知道这里有过一大段，要细节就再调一次工具。"""
    text = str(content or "")
    if len(text) <= _TRIM_OVER:
        return text
    omitted = len(text) - _TRIM_HEAD - _TRIM_TAIL
    return "%s\n…（中略 %d 字）…\n%s" % (text[:_TRIM_HEAD], omitted,
                                       text[-_TRIM_TAIL:])


def _result_text(tool: str, content: str, fresh: bool, folded: bool) -> str:
    """一条工具结果按新旧和工具类型渲染。"""
    if fresh:
        return content
    if folded:
        return _recall_stub(_strip_hints(content))
    body = _strip_hints(content)
    if tool in _KEEP_FULL_TOOLS:
        return body
    return _trim_keep(body)

# 估算只用在"发请求之前"的判断上：真实用量要等服务端 usage。常数是实测的
# （2026-10-25，deepseek-flash）：中文 528 字＝402 token（0.76 token/字），
# 英文 1212 字符＝295 token（4.11 字符/token），混排 1740 字符＝667 token。
_CJK = re.compile("[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]")
_CJK_COST = 0.76          # token / 汉字
_OTHER_PER_TOKEN = 4.11   # 字符 / token


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return max(1, int(round(cjk * _CJK_COST + (len(text) - cjk) / _OTHER_PER_TOKEN)))


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


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def humanize(delta) -> str:
    """一段时长说成人话：刚刚 / 12 分钟 / 3 小时 12 分 / 2 天 5 小时。"""
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return "%d 分钟" % minutes
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "%d 小时 %d 分" % (hours, minutes) if minutes else "%d 小时" % hours
    days, hours = divmod(hours, 24)
    return "%d 天 %d 小时" % (days, hours) if hours else "%d 天" % days


def _stamp_of(stamp: str, moment: datetime):
    try:
        when = datetime.strptime(str(stamp or ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    delta = moment - when
    return delta if delta.total_seconds() >= 0 else None


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
    meta: dict = field(default_factory=dict)   # kind 之类，不进渲染，只给规则用


def _last_stamp(entries: List[BlockEntry], role: str, spoken: bool) -> str:
    """最近一条（对方说的 / 我开口的）时间；空的 assistant 只是工具调用，不算开口。"""
    for entry in reversed(entries):
        message = entry.message
        if str(message.get("role") or "") != role:
            continue
        if spoken and not str(message.get("content") or "").strip():
            continue
        return str(entry.ts or "")
    return ""


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


def _tool_names(entries: List["BlockEntry"]) -> dict:
    """call_id → 工具名：靠它判断这条结果出自哪个工具。"""
    names = {}
    for entry in entries:
        for call in entry.message.get("tool_calls") or []:
            call_id = str(call.get("id") or "")
            if call_id:
                names[call_id] = str((call.get("function") or {}).get("name") or "")
    return names


def _recall_stub(content: str) -> str:
    """recall 的结果折成一行，但把「哪几件、什么时候」留下：id、名字、日期还要用。"""
    head = ""
    items = []
    for line in content.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("找到"):
            head = line.rstrip("：: ")
            continue
        if line.startswith("看具体用") or line.startswith("（嵌入"):
            continue
        parts = line.split(" · ")
        label = parts[0].strip()
        if not label:
            continue
        date = parts[1].strip() if len(parts) > 1 else ""
        if len(date) >= 10:
            label = "%s（%s）" % (label, date[5:10])
        items.append(label)
    if not items:
        return "结果：%s（已折叠）" % (head or "已折叠")
    return "结果：%s（已折叠）：%s" % (head, "、".join(items))


# 心跳提醒：连着几步没有外界动静时问一句「还在做事吗」。它不是发生过的事，
# 所以只留最近两条，更早的直接不渲染（数据还在，只是不进上下文）。
_ALARM_KEEP = 2


def serialize_entries(entries: List[BlockEntry], fold_keep: int = 0) -> str:
    """把块写成记录：对白、调用、结果都用自然写法，不用方括号标记。

    remember 的 note 不写进记录：只留“写下了记忆：名字”，内容在记忆里。
    `fold_keep` > 0 时按上面的保留模板处理工具结果（越旧越短）；调用行一律不动，
    所以还看得出当时问的是什么。压缩走默认值，读的是原文。
    """
    folded = set()
    fresh = set()
    names = _tool_names(entries)
    if fold_keep > 0:
        results = [index for index, entry in enumerate(entries)
                   if str(entry.message.get("role") or "") == "tool"]
        fresh = set(results[-fold_keep:])
        for index in results:
            if index in fresh:
                continue
            message = entries[index].message
            tool = names.get(str(message.get("tool_call_id") or ""))
            body = _strip_hints(str(message.get("content") or ""))
            if tool in _FOLDABLE_TOOLS and len(body) > _FOLD_MIN_CHARS:
                folded.add(index)

    alarms = [index for index, entry in enumerate(entries)
              if str(entry.meta.get("kind") or "") == "watchdog"]
    quiet = set(alarms[:max(0, len(alarms) - _ALARM_KEEP)])

    lines = []
    for index, entry in enumerate(entries):
        message = entry.message
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        calls = message.get("tool_calls") or []
        if role == "tool":
            tool = names.get(str(message.get("tool_call_id") or ""), "")
            lines.append("结果：%s" % _result_text(
                tool, content, index in fresh or fold_keep <= 0, index in folded))
            continue
        if role == "user":
            lines.append("对方：%s" % content)
            continue
        if role == "note":
            if index in quiet:
                continue
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

    def append(self, message: dict, meta: dict = None) -> BlockEntry:
        entry = BlockEntry(dict(message), now(), dict(meta or {}))
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
        return serialize_entries(self.current.entries,
                                 fold_keep=self.config.tool_fold_keep)

    def current_tokens(self) -> int:
        return estimate_tokens(self.current_text())

    def previous_text(self) -> str:
        return serialize_entries(self.previous.entries,
                                 fold_keep=self.config.tool_fold_keep)

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
        return prompt_files.render("sink.txt", name=name,
                                   recall=self.config.recall_tool,
                                   today=time.strftime("%Y-%m-%d")).strip()

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

    def clock_text(self, moment: datetime = None) -> str:
        """现在几点、距上两句话多久。

        只拼进这一步的输入，不落库：写进记录里会变成"她说过现在是几点"，
        还会被压进摘要；放进 sink 又会让前缀缓存每步作废。
        """
        moment = moment or datetime.now()
        parts = ["现在 %s %s %s" % (moment.strftime("%m-%d"),
                                    _WEEKDAYS[moment.weekday()],
                                    moment.strftime("%H:%M"))]
        entries = self.previous.entries + self.current.entries
        for role, spoken, label in (("user", False, "距上次对方说话"),
                                    ("assistant", True, "距我上次开口")):
            delta = _stamp_of(_last_stamp(entries, role, spoken), moment)
            if delta is not None:
                parts.append("%s %s" % (label, humanize(delta)))
        return "（%s）" % "；".join(parts)

    def messages(self) -> List[dict]:
        block = self.block_text().strip()
        if not block:
            block = "（接着上面。）" if self.bands or self.previous.entries else "（还没有记录。）"
        block = "%s\n\n%s" % (block, self.clock_text())
        return [
            {"role": "system", "content": self.system_text()},
            {"role": "user", "content": block},
        ]
