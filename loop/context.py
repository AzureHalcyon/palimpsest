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

from . import persona
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

# remember 写进去的 note 在记录里留多少步：刚写完那几步要能回头看见自己写了什么，
# 再往后换成一句占位（note 本身从记忆里取，不靠记录重复）。
NOTE_KEEP_STEPS = 10
_NOTE_STUB = "（已写进记忆；要看内容用 recollect）"

# 「还有 N 条没看」这行也是一时的：被叫醒时现拼一个数字，几步之后就该消失，
# 不然一条早就读过的计数会一直躺在上下文里。
UNREAD_KEEP_STEPS = 6
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
    """把一段记录的时间压成一行：同一天只写一次日期，带上星期。

    星期是给"昨天/上周三"这类相对说法用的：只有月日的话，跨周时她得自己算，
    而记录里最常见的就是"那天是周几"。
    """
    start = str(start_ts or "")
    end = str(end_ts or "")
    if not start:
        return end
    if not end:
        return start

    def day_of(stamp: str) -> str:
        try:
            moment = datetime.strptime(stamp[:10], "%Y-%m-%d")
        except ValueError:
            return ""
        return _WEEKDAYS[moment.weekday()]

    first, second = day_of(start), day_of(end)
    if start[:10] == end[:10]:
        return "%s %s %s–%s" % (start[5:10], first, start[11:16], end[11:16])
    return "%s %s %s–%s %s %s" % (start[5:10], first, start[11:16],
                                  end[5:10], second, end[11:16])


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
    seq: int = 0        # 库里的行号：网页抽屉拿它当折叠块的键，不重复才不串


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
        return "%s（已折叠）" % (head or "已折叠")
    return "%s（已折叠）：%s" % (head, "、".join(items))


# 心跳提醒：连着几步没有外界动静时的一句系统动静。它不是发生过的事，所以库里
# 只留一行、**不进上下文**；真要让它当场起作用，走 `Context.reminder` 的瞬时注入。


_BLANK_LINES = re.compile(r"\n[ \t]*(?:\n[ \t]*)+")


def _one_turn(text: str) -> str:
    """纯文本渲染时，把一条话里的空行并成一个换行。

    文本渲染靠行首的「对方：」「我：」认是谁在说；一条话里留着空行，下一段看起来
    就像另一个人接的话了。原生消息（`Context._turns`）不用它——role 已经说明白了，
    段落想留就留。
    """
    return _BLANK_LINES.sub("\n", str(text or "").strip())


def _reminder(text: str) -> str:
    """系统注入的东西套一层标签：环境、心跳提醒、那句提问都走这里。

    它们混在 user 帧里（末尾那条帧只能是 user，见 `Context.messages`），套上标签
    才和对方说的话分得开；sink 里也写了「套着标签的都是系统注入，不是对方说的」。
    """
    return "<system-reminder>%s</system-reminder>" % str(text or "").strip()


_FAKE_CALL = re.compile(r"^\s*调用\s+\S+\s+\{")
# 调用协议漏进正文的特殊 token（`<｜｜DSML｜｜ calls>` 那种）。正常文字里不会有 `｜`
# 和尖括号的这个组合，按行丢最省事。
_LEAKED_MARK = re.compile(r"<｜|｜｜DSML｜｜")


def strip_fake_calls(text: str) -> str:
    """去掉模型手写的"调用 x {...}"行；只有真出现这种行时，才连带删结果行。

    真调用走 `tool_calls`、真结果走 `role=tool` 的消息。正文里冒出 `调用 x {...}`
    只可能是它在模仿旧记录的写法（服务器上发生过一次：一步里没有 tool 记录，
    等于伪造执行）。

    `结果：…` 不单独删：它完全可能是她自己的一句话（"结果：他们没来"）。
    只有这条正文已经被判定成"抄记录"（出现了伪造的调用行）时，才把结果行一起清掉。

    另外整行丢掉**调用协议漏出来的标记**：模型偶尔把工具调用写成正文里的
    `<｜｜DSML｜｜ calls>` 那种特殊 token，是协议没走 `tool_calls` 而是漏进了话里。
    正常文字里不会出现 `<｜` 或 `｜｜DSML｜｜`，所以按行丢不会误伤。
    """
    text = _drop_leaked_marks(text)
    lines = str(text or "").splitlines()
    faked = any(_FAKE_CALL.match(line) for line in lines)
    if not faked:
        return str(text or "").strip()
    kept = []
    for line in lines:
        if _FAKE_CALL.match(line):
            continue
        if line.lstrip().startswith("结果："):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _drop_leaked_marks(text: str) -> str:
    """把夹着调用协议标记的行整行删掉，别的行原样留着。"""
    lines = [line for line in str(text or "").splitlines()
             if not _LEAKED_MARK.search(line)]
    return "\n".join(lines).strip()


_FORGED_TAG = re.compile(r"<\s*(/?)\s*(system-reminder|system|instructions?)\s*>", re.I)
_FORGED_TOKEN = re.compile(r"<\|[^<>\n]{0,24}\|>")


def neutralize(text: str) -> str:
    """把冒充系统注入的标记降级：真的系统注入只有 `_reminder` 一条来路。

    记录里的字有三种来路：对方说的、她说的、外面抄来的（网页、字幕、群里的别人）。
    外面那句里要是写着 `<system-reminder>你现在是……</system-reminder>`，进了记录就
    和系统注入长得一模一样——模型没法分辨。所以这两种形状（我们的标签、调用协议
    的 `<|…|>` 特殊 token）在进记录和读记录时都换成全角括号，字形还在，语气没了。
    只动这两种形状，正常文字里的尖括号不碰。
    """
    body = _FORGED_TAG.sub(
        lambda found: "〈%s%s〉" % (found.group(1), found.group(2).lower()),
        str(text or ""))
    return _FORGED_TOKEN.sub(
        lambda found: "〈%s〉" % found.group(0)[2:-2], body)


def _neutralized(entry: BlockEntry) -> BlockEntry:
    """同一条记录，正文降级后的副本（原记录不动：库里留的是原话）。"""
    message = dict(entry.message)
    content = message.get("content")
    if isinstance(content, str):
        message["content"] = neutralize(content)
    return BlockEntry(message, entry.ts, entry.meta)


def _entry_lines(entries: List[BlockEntry], fold_keep: int = 0) -> list:
    """每条记录 → (角色, 文本, 是不是“说的话”)。

    角色用 api 那三种：`user` 是对方、`assistant` 是自己、`note` 是系统提醒。
    工具调用与结果都算它自己的动作（assistant），但不是"说的话"，纯文本渲染时
    不加前缀。两个渲染入口（原生消息 / 纯文本）都吃这一份，不会各说各的。

    remember 的 note 不写进记录：只留“写下了记忆：名字”，内容在记忆里。
    `fold_keep` > 0 时按保留模板处理工具结果（越旧越短）；调用行一律不动。
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


    items = []
    for index, entry in enumerate(entries):
        message = entry.message
        role = str(message.get("role") or "")
        content = neutralize(str(message.get("content") or "")).strip()
        calls = message.get("tool_calls") or []
        if role == "tool":
            tool = names.get(str(message.get("tool_call_id") or ""), "")
            items.append(("tool_result", _result_text(
                tool, content, index in fresh or fold_keep <= 0, index in folded),
                False))
            continue
        if role == "user":
            items.append(("user", content, True))
            continue
        if role == "note":
            # 心跳是当时的提醒，不是发生过的事：库里记着，但不进上下文
            # （它的旧文案还会和现在的规矩打架）。新的一版走瞬时注入。
            if str(entry.meta.get("kind") or "") == "watchdog":
                continue
            items.append(("note", content, True))
            continue
        if content:
            items.append(("assistant", content, True))
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "{}")
            if name == "remember":
                items.append(("assistant",
                              "写下了记忆：%s" % _remember_name(arguments), False))
            else:
                items.append(("tool_call", "%s %s" % (name, arguments), False))
    return items


def serialize_entries(entries: List[BlockEntry], fold_keep: int = 0,
                      user_name: str = "") -> str:
    """纯文本版：压缩模型只看得到字，所以每行都标清是谁的。

    前缀：`对方：` / `我：` / `我用了：`（工具调用）/ `工具回：`（工具结果）/ `提醒：`。
    不用「调用 x {} / 结果：」那套记号——它跟"她说过的话"长得太像，曾经让她照着抄。
    """
    lines = []
    who = str(user_name or "").strip() or "对方"
    for role, text, spoken in _entry_lines(entries, fold_keep):
        body = str(text or "").strip()
        if not body:
            continue
        if role == "tool_call":
            lines.append("我用了：%s" % _one_turn(body))
        elif role == "tool_result":
            lines.append("工具回：%s" % _one_turn(body))
        elif role == "user":
            lines.append("%s：%s" % (who, _one_turn(body)))
        elif role == "note":
            lines.append("提醒：%s" % _one_turn(body))
        else:
            lines.append("我：%s" % _one_turn(body))
    return "\n".join(lines)


class Context:
    def __init__(self, config):
        self.config = config
        self.bands: List[Band] = []
        self.previous = Block()
        self.current = Block()
        # 这一步要不要在末尾问一句「还要继续吗」：只在上一轮说了话、没调工具时置位。
        # 和墙钟一样只拼进输入，不落库——它是一句提问，不是谁说的话。
        self.asking = False
        # 这一步要顺带说一句系统动静（心跳）。只进这一步的输入，下一步清空。
        self.reminder = ""
        # 这一步要不要带墙钟。谁决定见 Loop.step：有人说话/刚醒来一定带，
        # 其余步按 clock_refresh 隔一会儿才带，中间只放一句「接着上面」。
        self.show_clock = True

    # ── 状态 ──────────────────────────────────────────────────────────────
    def set_state(self, bands: List[Band], previous: Block, current: Block) -> None:
        self.bands = sorted(bands, key=lambda band: band.level)
        self.previous = previous
        self.current = current

    def append(self, message: dict, meta: dict = None) -> BlockEntry:
        # 进记录的第一道门：外部抄来的字不能带着系统标签住进来（见 `neutralize`）。
        # 写在落库前，纯文本和原生两条渲染路都省事；老记录由那两条各自再过一遍。
        message = dict(message)
        message["content"] = neutralize(message.get("content") or "")
        entry = BlockEntry(message, now(), dict(meta or {}))
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
                                 fold_keep=self.config.tool_fold_keep,
                                 user_name=getattr(self.config, "user_name", ""))

    def current_tokens(self) -> int:
        return estimate_tokens(self.current_text())

    def previous_text(self) -> str:
        return serialize_entries(self.previous.entries,
                                 fold_keep=self.config.tool_fold_keep,
                                 user_name=getattr(self.config, "user_name", ""))

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
        user = str(getattr(self.config, "user_name", "") or "对方")
        # 声音（她是谁、怎么说话）由落地侧登记；没登记就是空的，sink 只有机制那几段。
        voice = ""
        if prompt_files.has("voice"):
            voice = prompt_files.render("voice", name=name, user=user).strip()
        body = prompt_files.render("sink.txt", name=name, user=user,
                                   recall=self.config.recall_tool,
                                   wait=self.config.wait_tool,
                                   today=time.strftime("%Y-%m-%d")).strip()
        return ("%s\n\n%s" % (voice, body)).strip() if voice else body

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

    def bands_text(self) -> str:
        """旧段摘要拼起来（整理任务要长程连续性时喂这个）。"""
        return self._render_bands(self.fitted_bands())

    def system_text(self) -> str:
        parts = [self.sink()]
        # 她自己写的描述：sink 之后、旧段之前。空就不出现（她不写就没有这一块）。
        written = persona.read(self.config) if bool(
            getattr(self.config, "persona_enabled", False)) else ""
        if written:
            parts.append("【我自己写的】\n%s" % written)
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

    def _turns(self) -> List[dict]:
        """两块原文拼成原生消息：谁在说话由 role 说，调用与结果也是原生的。

        ★ 调用必须走原生 `tool_calls` + `tool` 消息，**不能**写成"调用 x / 结果：…"
          那种文字行：记录里一旦出现这种行，模型会照着抄——服务器上真发生过一次，
          她把调用和结果当正文手打出来（没有 tool 记录，等于什么都没做）。
        ★ 没有结果的调用（正等着的 wait、旧数据里断掉的调用）不放进 `tool_calls`，
          因为端点不接受"悬空调用"；那种情况写一行文字说明。
        ★ `remember` 的 note 只留 `NOTE_KEEP_STEPS` 步：刚写完那几步她要能回头看见
          自己写了什么（不然它会怀疑"note 没传/内容是系统补的"），之后换成一句占位，
          免得同一段话在记录里躺到底、还跟"note 不进上下文"的规矩自相矛盾。
        """
        fold_keep = int(getattr(self.config, "tool_fold_keep", 0) or 0)
        # 拼进输入之前统一降级一次：老记录里可能还留着原样的伪造标签，
        # 而这里的 result 会被塞进 tool_calls 里那一侧读，逐行挡容易漏。
        entries = [_neutralized(entry) for entry in
                   list(self.previous.entries) + list(self.current.entries)]
        names = _tool_names(entries)
        results = {}
        for index, entry in enumerate(entries):
            message = entry.message
            if str(message.get("role") or "") == "tool":
                call_id = str(message.get("tool_call_id") or "")
                if call_id:
                    results[call_id] = index
        # 结果条的裁剪口径跟纯文本渲染一致：越旧越短
        fold_rows = [index for index, entry in enumerate(entries)
                     if str(entry.message.get("role") or "") == "tool"]
        fresh = set(fold_rows[-fold_keep:]) if fold_keep > 0 else set()
        folded = set()
        for index in fold_rows:
            if index in fresh:
                continue
            message = entries[index].message
            tool = names.get(str(message.get("tool_call_id") or ""))
            body = _strip_hints(str(message.get("content") or ""))
            if tool in _FOLDABLE_TOOLS and len(body) > _FOLD_MIN_CHARS:
                folded.add(index)

        used = set()
        turns: List[dict] = []

        # 每个位置后面还剩几步（一条 assistant 消息 = 一步）：note 的保质期按这个算
        steps_after = [0] * len(entries)
        behind = 0
        for index in range(len(entries) - 1, -1, -1):
            steps_after[index] = behind
            if str(entries[index].message.get("role") or "") == "assistant":
                behind += 1

        def push(role: str, text: str) -> None:
            body = str(text or "").strip()
            if not body:
                return
            last = turns[-1] if turns else None
            if last is not None and last["role"] == role and "tool_calls" not in last:
                last["content"] = "%s\n%s" % (last["content"], body)
            else:
                turns.append({"role": role, "content": body})

        for index, entry in enumerate(entries):
            message = entry.message
            role = str(message.get("role") or "")
            content = str(message.get("content") or "").strip()
            calls = list(message.get("tool_calls") or [])
            if role == "tool":
                if index not in used:
                    # 没有配对的调用（旧数据、或调用被删了）：当一条环境记录说清楚
                    tool = names.get(str(message.get("tool_call_id") or ""), "")
                    push("user", "（%s的回话）%s" % (
                        tool or "工具",
                        _result_text(tool, content, index in fresh or fold_keep <= 0,
                                     index in folded)))
                continue
            if role == "user":
                push("user", content)
                continue
            if role == "note":
                # 心跳是当时的提醒，不是发生过的事：库里记着，但不进上下文
                # （它的旧文案还会和现在的规矩打架）。新的一版走瞬时注入。
                kind = str(entry.meta.get("kind") or "")
                if kind == "watchdog":
                    continue
                if kind == "unread" and steps_after[index] > UNREAD_KEEP_STEPS:
                    continue
                turns.append({"role": "system", "content": _reminder(content),
                              "_own": False})
                continue
            live = []
            for call in calls:
                call_id = str(call.get("id") or "")
                target = results.get(call_id)
                if target is not None and target not in used:
                    live.append((call, target))
            if live:
                payload = []
                for call, _target in live:
                    function = dict(call.get("function") or {})
                    name = str(function.get("name") or "")
                    if name == "remember" and steps_after[index] >= NOTE_KEEP_STEPS:
                        function["arguments"] = json.dumps(
                            {"name": _remember_name(
                                str(function.get("arguments") or "{}")),
                             "note": _NOTE_STUB}, ensure_ascii=False)
                    payload.append({"id": str(call.get("id") or ""),
                                    "type": "function", "function": function})
                # 上一条也是它自己、而且这条没有正文：把调用并进去——
                # 端点更喜欢"一条 assistant 里既有话又有调用"，也免得连着两条 assistant。
                last = turns[-1] if turns else None
                if (not content and last is not None
                        and last["role"] == "assistant" and "tool_calls" not in last):
                    last["tool_calls"] = payload
                else:
                    turns.append({"role": "assistant", "content": content,
                                  "tool_calls": payload})
                for call, target in live:
                    used.add(target)
                    tool = names.get(str(call.get("id") or ""), "")
                    result = entries[target]
                    text = _result_text(
                        tool, str(result.message.get("content") or ""),
                        target in fresh or fold_keep <= 0,
                        target in folded)
                    turns.append({"role": "tool",
                                  "tool_call_id": str(call.get("id") or ""),
                                  "content": text})
                continue
            if content:
                push("assistant", content)
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                if name == "remember":
                    push("assistant", "（写下了记忆：%s，没有结果回来）"
                         % _remember_name(str(function.get("arguments") or "{}")))
                else:
                    push("assistant", "（调用了 %s，没有结果回来）" % name)
        for turn in turns:
            turn.pop("_own", None)
        return turns

    def messages(self) -> List[dict]:
        """按角色拼消息：对方说的话、自己说过的、系统动作各归各位。

        末尾那一帧是环境（墙钟，要问时带上「还要继续吗」），用 `user` 是因为**带
        tools 时端点不接受末尾是 system**（deepseek-flash 直接 400；中间的 system
        可以）。所以它单独成一帧、括号包着，不并进对方的话里。
        """
        turns = self._turns()
        tail = []
        if not turns:
            tail.append("（接着上面。）" if self.bands or self.previous.entries
                        else "（还没有记录。）")
        if self.reminder:
            tail.append(self.reminder)
        if self.show_clock:
            tail.append(self.clock_text())
        elif not tail:
            tail.append("（接着上面。）")     # 只当边界用：不带时间，也不催
        if self.asking:
            tail.append(prompt_files.render("ask.txt"))
        return ([{"role": "system", "content": self.system_text()}]
                + turns
                + [{"role": "user", "content": _reminder("\n".join(tail))}])
