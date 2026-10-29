"""步循环：拼上下文 → 主模型 → 执行 → 继续。

两块原文：当前块写满，不压它；把它变成“上一块”，等下一块写满时再压。
工具走原生 tool_calls；正文是“说”，空回复是“停”。
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from memory_v2.core.config import ChatConfig, Credentials
from memory_v2.core.llm import ChatClient, ChatError

from . import archive
from . import control
from . import persona
from . import prompt_files
from .compress import CompressError, Compressor, add_band
from .context import Context, estimate_tokens, strip_fake_calls
from .store import LoopStore

# 一个回复里只允许调一个的工具：阻塞的、必须独占一步的。多出来的不执行，直接
# 告诉模型为什么——两个 wait 不是「或者」，是「等完这个再等那个」，会一直挂着。
_BLOCKING_TOOLS = ("wait",)

# 等的时候多久看一次：对方一句话最多晚这么久才被读到。
_POLL_SECONDS = 1.0
_TURN_CALL_CAP = 3
_REPAIR_WINDOW = 7200        # 对账只看最近两小时的进件        # 同一轮里同一个工具最多调几次（防止写同一段记忆打转）

_RELATIVE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|m|h|秒|分钟|分|小时)?\s*$")
_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _wait_tool(text: dict) -> dict:
    """等，是运行时的动作，不是记忆工具；schema 里的字在 prompts/tools.txt。"""
    return {
        "type": "function",
        "function": {
            "name": "wait",
            "description": text["wait"],
            "parameters": {
                "type": "object",
                "properties": {
                    "until": {"type": "string", "description": text["wait.until"]},
                    "kinds": {"type": "array", "items": {"type": "string"},
                              "description": text["wait.kinds"]},
                },
            },
        },
    }


def _human(seconds: float) -> str:
    """说等了多久：秒级就到秒，一分钟以上到分。"""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "%d 秒" % int(round(seconds))
    minutes = seconds / 60.0
    if minutes < 60:
        return "%d 分钟" % int(round(minutes))
    hours = int(minutes // 60)
    rest = int(round(minutes - hours * 60))
    return "%d 小时 %d 分钟" % (hours, rest) if rest else "%d 小时" % hours


def parse_until(spec: str, base: Optional[float] = None) -> Optional[float]:
    """把 until 解析成 deadline（epoch 秒）；空串返回 None（无上界）。

    收两种写法：`10m` / `90s` / `2h` 这种相对，或者 `14:30` 这种钟点（已经过了就算明天）。
    """
    text = str(spec or "").strip()
    if not text:
        return None
    start = time.time() if base is None else float(base)

    clock = _CLOCK.match(text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("钟点不对：%s" % text)
        moment = datetime.fromtimestamp(start).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        target = moment.timestamp()
        if target <= start:
            target = (moment + timedelta(days=1)).timestamp()
        return target

    relative = _RELATIVE.match(text)
    if relative:
        amount = float(relative.group(1))
        unit = relative.group(2) or "s"
        scale = {"s": 1, "秒": 1, "m": 60, "分": 60, "分钟": 60,
                 "h": 3600, "小时": 3600}[unit]
        return start + amount * scale

    raise ValueError("看不懂的时间：%s（用 10m、90s、2h 或 14:30）" % text)


def _timeout_text(spec: str, elapsed: float) -> str:
    """到点没等到：钟点写法就用钟点回，相对写法就用「等了多久」回。"""
    if spec and _CLOCK.match(spec):
        return "等到 %s，到点了，没等到。" % spec
    return "等了 %s，到点了，没等到。" % _human(elapsed)


def _woke_text(kind: str, elapsed: float) -> str:
    reason = "对方说话了" if kind == "user" else "%s 回来了" % kind
    return "等了 %s，%s。" % (_human(elapsed), reason)


def _human_tokens(count: int) -> str:
    """报用量用：小于一千给原数，之后用 k / M。"""
    count = int(count or 0)
    if count < 1000:
        return str(count)
    if count < 1000 * 1000:
        return "%.1fk" % (count / 1000.0)
    return "%.2fM" % (count / 1000000.0)


def _next_midnight(now: Optional[float] = None) -> float:
    """第二天零点多一点：成本到顶时等到这里。"""
    stamp = datetime.fromtimestamp(time.time() if now is None else float(now))
    tomorrow = (stamp + timedelta(days=1)).replace(hour=0, minute=1,
                                                   second=0, microsecond=0)
    return tomorrow.timestamp()


def in_meta(item: dict) -> dict:
    """进件的身份：记进记录里，用来对账「有没有落库」。"""
    try:
        return {"inbox_id": int(item.get("id") or 0)}
    except (TypeError, ValueError):
        return {}


def _source_label(item: dict) -> str:
    """进件的来源标签：`外部·群「群甲」 甲`、`外部 甲`；没有来源就是空串。"""
    source = str(item.get("source") or "").strip()
    sender = str(item.get("sender_name") or "").strip()
    chat_type = str(item.get("chat_type") or "private")
    chat_name = str(item.get("chat_name") or "").strip()
    chat_id = str(item.get("chat_id") or "").strip()
    if not source and not sender:
        return ""
    head = source or "外部"
    if chat_type == "group" and (chat_name or chat_id):
        head += "·群「%s」" % (chat_name or chat_id)
    return ("%s %s" % (head, sender)).strip() if sender else head


def via_of(item: dict) -> dict:
    """这条外部消息是**谁说给她的**：她说的话就打上这个标记，回给同一个会话。

    本机直投的（`direct`：人就在这一头跟她说话，网页或命令行）没有"回给谁"可言，
    返回空字典——没有这个标记，通道适配器就不会把话复述到别处去。
    """
    source = str(item.get("source") or "").strip()
    if not source or item.get("direct"):
        return {}
    return {"source": source,
            "chat_type": str(item.get("chat_type") or "private"),
            "chat_id": str(item.get("chat_id") or ""),
            "chat_name": str(item.get("chat_name") or "")}



def _inbox_entry(item: dict) -> dict:
    """一条进件写成记录：外部来的带来源前缀（群里连发言人），本机直投的原样进。"""
    kind = str(item.get("kind") or "user")
    text = str(item.get("text") or "").strip()
    if kind != "user":
        return {"role": "note", "content": "%s：%s" % (kind, text or "（没有内容）")}
    if item.get("direct"):
        # 本机对话：来源（网页 / 命令行）只是给仪表盘看的标签，不该每句都盖在她面前
        return {"role": "user", "content": text}
    label = _source_label(item)
    if not label:
        return {"role": "user", "content": text}
    return {"role": "user", "content": "%s：%s" % (label, text)}


def _unread_text(summary: dict) -> str:
    """攒着的那批：一行数字 + 每个会话几条 + 谁说得最多。

    只陈述事实（几条、谁说的），不写"请调用某工具"——能不能读、怎么读，
    在 `messages` 自己的说明里；把工具名塞进别人的话里，读起来像系统提示。
    """
    parts = []
    for chat in summary.get("chats") or []:
        name = chat.get("chat_name") or chat.get("chat_id") or "某个会话"
        who = "、".join(chat.get("senders") or [])
        parts.append("%s %d 条%s" % (name, int(chat.get("count") or 0),
                                     ("（%s）" % who) if who else ""))
    head = "还有 %d 条没看的消息" % int(summary.get("total") or 0)
    chat_total = int(summary.get("chat_total") or 0)
    if chat_total > len(summary.get("chats") or []):
        head += "（共 %d 个会话）" % chat_total
    return "，".join([head] + parts) + "。"


def _one_via(came: dict) -> dict:
    """这一步只有**一个**外部会话在叫她时，才立"回给谁"；几个一起就立不了。"""
    real = {key: via for key, via in (came or {}).items()
            if via and via.get("chat_id")}
    if len(real) == 1:
        return dict(next(iter(real.values())))
    return {}


def _ring_text(item: dict) -> str:
    """有人来找她：谁、在哪儿。一句话就够。

    群里的唤醒只有一种来路——被 @：别的群消息默认不叫醒（会话策略是 batch），
    所以那儿写「@你」。
    """
    sender = str(item.get("sender_name") or "").strip()
    chat_name = (str(item.get("chat_name") or "").strip()
                 or str(item.get("chat_id") or "").strip() or "?")
    source = str(item.get("source") or "").strip() or "外面"
    who = (sender + " ") if sender else "有人"
    if str(item.get("chat_type") or "private") == "group":
        return "%s在群「%s」里 @ 你。" % (who, chat_name)
    return "%s在 %s 上私聊你。" % (who, source)


def _inbox_tools(text: dict) -> List[dict]:
    """收件箱的三个工具：看会话、改策略、读攒着的。"""
    return [
        {"type": "function",
         "function": {"name": "channels", "description": text["channels"],
                      "parameters": {"type": "object", "properties": {}}}},
        {"type": "function",
         "function": {"name": "channels_set", "description": text["channels_set"],
                      "parameters": {"type": "object", "properties": {
                          "chat": {"type": "string",
                                   "description": text["channels_set.chat"]},
                          "policy": {"type": "string",
                                     "enum": ["wake", "batch", "mute"],
                                     "description": text["channels_set.policy"]},
                      }, "required": ["chat", "policy"]}}},
        {"type": "function",
         "function": {"name": "messages", "description": text["messages"],
                      "parameters": {"type": "object", "properties": {
                          "chat": {"type": "string",
                                   "description": text["messages.chat"]},
                          "limit": {"type": "number",
                                    "description": text["messages.limit"]},
                          "before": {"type": "number",
                                     "description": text["messages.before"]},
                      }}}},
    ]


def _message_line(item: dict) -> str:
    label = _source_label(item)
    text = str(item.get("text") or "").strip().replace("\n", " ")
    return ("%s：%s" % (label, text)) if label else text


class Loop:
    def __init__(self, config, credentials, store: LoopStore,
                 compressor: Compressor, memory,
                 client=None,
                 on_speak: Optional[Callable[[str], None]] = None,
                 on_step: Optional[Callable[[dict], None]] = None,
                 echo: Optional[Callable[[str], None]] = None,
                 log: Optional[Callable[[str], None]] = None,
                 toolsets: Optional[List] = None):
        self.config = config
        self.credentials = credentials
        self._persona_client = None
        self.store = store
        self.compressor = compressor
        self.memory = memory
        self.on_speak = on_speak or (lambda text: None)
        self.on_step = on_step or (lambda info: None)
        self.echo = echo or (lambda text: None)
        self.log = log or (lambda text: None)
        # 工具面归入口：引擎只认「有哪些工具」和「按名字调哪个」，不认具体是谁。
        # 核心自己一个工具都不带（`wait` 除外），工具表全由入口递进来。
        self.toolsets = list(toolsets or ())
        self.client = client or ChatClient(
            ChatConfig(model=config.model, temperature=config.temperature,
                       max_tokens=config.max_tokens, timeout=config.timeout,
                       retries=config.retries,
                       reasoning_effort=getattr(config, "reasoning_effort", "")),
            Credentials(api_key=credentials.chat_key, chat_url=credentials.chat_url))

        state = store.load_state()
        self.context = Context(config)
        self.context.set_state(state["bands"], state["previous"], state["current"])
        self.previous_id = state["previous_id"]
        self.current_id = state["current_id"]

        self.steps = 0
        self.turn = 0
        self.compressions = 0
        self.parked = False
        self.last_prompt_tokens = 0
        self.last_usage = {}
        # 当前块的真实大小：上一次请求的 prompt_tokens 减去固定部分（sink、旧段、
        # 工具 schema）。没有真数可用时（刚重启、刚压完）先用估算顶上。
        self.current_real = self.context.current_tokens()
        # 进件要跟在工具结果后面（协议上结果紧跟调用，所以先攒着）。
        self._after_tool: List[dict] = []
        # 有没有人能把消息送进来：没有就只等时间，无上界的等就直接停。
        self.standby = bool(getattr(config, "standby", False))
        # 刚起来就有人说话（`--say` 或入口先塞一句）：这时候不该再"等动静"，
        # 否则那句话躺在记录里，人得再发一条才会被看见。
        self._fresh_input = False
        # 上一次报给她的「攒了多少条」；同一个数字不重复拼，变了才拼。
        self._unread_total = 0
        # 距上一次外部输入过了几步：用来拦「自己在跟自己转」。
        self.since_external = 0
        # 这一串话是回给谁的：外部消息把她叫醒时记下来（`via_of`），她说的话
        # 就带着这个标记落库，通道那边照它定向回发；网页里说的话不带，也就不会
        # 被复述到别的通道上去。等回去没事（wait 到点）就清掉。
        self.via: dict = {}
        self.retries = 0
        self._next_archive = 0.0
        self.archive_last = None       # 上一次「归档」的报告（手动或层满触发）
        self._cap_day = ""
        self._warn_day = ""
        # 摘要失败后别每步都重试：等当前块再长一点。
        self._compress_after = 0
        # 墙钟：上一次带上它的时刻；`_woke` 记着"上一个工具是不是等到动静了"。
        self._clock_at = 0.0
        self._turn_calls = {}          # 连着各工具调了几次（外部一动就清零）
        self._turn_confirm = set()     # 连着调超量时已经回过一句的工具
        # 这一次工具调用的 meta：`messages` 靠它记下"读走的是哪几条进件"，
        # 对账才不会把"她已经读进记录"的消息当成丢了、又插成一条 user
        self._tool_meta: dict = {}
        self._next_persona = 0.0
        # 流式的草稿：只给界面看，不进记录（记录只认最后那条完整消息）
        self.draft_text = ""
        self.draft_think = ""
        self._woke = False
        # 连着几步「只说了话、没调工具」：第 1 次之后在末尾问一句还要不要继续，
        # 问了还是只说话就停下（不再问第二遍，免得提问本身变成复读机）。
        self._quiet = 0

    # ── 工具面 ────────────────────────────────────────────────────────────
    def tools(self) -> List[dict]:
        tools = list(self.memory.tools())
        text = prompt_files.tool_texts()
        tools.append(_wait_tool(text))
        tools.extend(_inbox_tools(text))
        for toolset in self.toolsets:
            tools.extend(toolset.tools())
        return tools

    # ── 一步 ──────────────────────────────────────────────────────────────
    def step(self) -> str:
        if control.stop_requested():
            raise control.StopRequested()
        self._maybe_reload()
        self._apply_edits()
        self.turn = self.store.next_turn()
        if hasattr(self.memory, "step"):
            self.memory.step = self.turn
        got = self._drain_inbox()
        if got:
            self.since_external = 0
            # 有新的话进来：这一步该接话，不是问它要不要继续。
            self._quiet = 0
        else:
            self.since_external += 1
        self._unread_note()
        self._archive_if_asked(got)
        idle = int(getattr(self.config, "watchdog_idle", 0) or 0)
        if idle and self.since_external >= idle:
            self._nudge()
        if self._budget_guard():
            return "park"
        self._compress_if_needed()
        tools = self.tools()
        # 上一轮说了话、没调工具：这一步末尾问一句「还要继续吗」，问得中性、
        # 不带"该做什么"的提示——它自己决定调工具继续还是 wait 停下。
        self.clear_draft()
        self.context.asking = self._quiet == 1
        # 墙钟多久刷一次：有人说话 / 刚醒来一定给；其余步按间隔给，中间只放边界帧。
        # 时间在末尾那一帧上，不动前缀缓存；这里限的是"别每步都念一遍几点"。
        try:
            refresh = int(getattr(self.config, "clock_refresh", 120) or 0)
        except (TypeError, ValueError):
            refresh = 120
        want_clock = (refresh <= 0) or bool(got) or self._woke
        if not want_clock:
            want_clock = (time.time() - self._clock_at) >= refresh
        self.context.show_clock = want_clock
        if want_clock:
            self._clock_at = time.time()
        self._woke = False
        messages = self.context.messages()
        # 心跳那句只在注入的那一步出现一次：它说完就清掉，不进历史。
        self.context.reminder = ""
        self.store.set_status("thinking")
        message = self._chat(messages, tools)
        usage = dict(getattr(self.client, "last_usage", None) or {})
        self.last_usage = usage
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)
        self._measure_block(tools)
        self.store.add_usage(self.turn, usage)

        content = str(message.get("content") or "").strip()
        calls = list(message.get("tool_calls") or [])
        self._append(message)
        self.clear_draft()
        if content:
            # 正文就是说：有工具调用也照说，不能吞掉。
            self.on_speak(content)
        if calls:
            self._dispatch_calls(calls)
            kind = "call"
            self._quiet = 0
        elif content:
            if self._quiet >= 1:
                # 问过了「还要继续吗」，它还是只说话：那就当它说完了，停下等。
                self._quiet = 0
                kind = "speak"
                self._park("问过了，没有要接着做的事")
            else:
                # 说了话、没调工具：下一步问一次要不要继续，不直接替它续。
                self._quiet = 1
                kind = "speak"
        else:
            # 没有内容也没有调用：不说了，等有动静。
            self._quiet = 0
            self._park("没有动作")
            kind = "park"

        self.steps += 1
        self.on_step({"step": self.steps, "kind": kind, "text": content,
                      "current_tokens": self.current_real,
                      "prompt_tokens": self.last_prompt_tokens,
                      "cache_hit": int(usage.get("prompt_cache_hit_tokens") or 0),
                      "compressions": self.compressions})
        if not self.parked:
            self.store.set_status("idle")
        return kind

    def run(self, max_steps: Optional[int] = None) -> int:
        limit = self.config.max_steps if max_steps is None else max_steps
        self._maintain()
        self._repair_inbox()
        resumed = self._resume_waits()
        # 刚起来：没挂着的等、也没有人说话，就先等着。不然模型会对着块的最后
        # 一句再应一遍——每次重启都白说两句，还把记录弄脏。
        if (self.standby and not resumed and not self.store.has_pending()
                and not self._fresh_input):
            self._park("刚起来，没有动静")
        while not (limit and self.steps >= limit):
            self.step()
            self._fresh_input = False
            if self.parked:
                break
        return self.steps

    def inject_user(self, text: str) -> None:
        """外部消息进记录：下一次请求就带着它。"""
        self._append({"role": "user", "content": str(text)})
        self.since_external = 0
        self._fresh_input = True

    # ── 调模型 ────────────────────────────────────────────────────────────
    def _on_delta(self, kind: str, text: str) -> None:
        """流式增量：先攒成草稿，网页那边能看到她边想边说；落库只认最后那条消息。"""
        piece = str(text or "")
        if not piece:
            return
        if not self.draft_text and not self.draft_think:
            self.log("[流] 收到第一个增量")
        if str(kind) == "reasoning":
            self.draft_think = (self.draft_think + piece)[-4000:]
        else:
            self.draft_text = (self.draft_text + piece)[-4000:]

    def clear_draft(self) -> None:
        self.draft_text = ""
        self.draft_think = ""

    def _chat(self, messages, tools) -> dict:
        """调不通就一直试：长跑里一次抖动不该把循环打死。

        `ChatClient` 自己会重试 `retries` 次；这里兜的是「重试完还是不行」——退避
        往后翻倍，封顶五分钟，一直试到通为止。试不通的时候等一等也不花调用。
        """
        delay = max(1.0, float(getattr(self.config, "retry_delay", 5.0) or 5.0))
        while True:
            try:
                stream = bool(getattr(self.config, "stream", True))
                return self.client.chat(messages, tools=tools or None,
                                        on_delta=self._on_delta if stream else None)
            except ChatError as exc:
                self.retries += 1
                self.store.set_status("retry", "%s（第 %d 次）" % (exc, self.retries))
                self.echo("[重试] 第 %d 次没通（%s），%d 秒后再试"
                          % (self.retries, exc, int(delay)))
                time.sleep(delay)
                delay = min(delay * 2, 300.0)

    # ── 成本 ──────────────────────────────────────────────────────────────
    def _budget_guard(self) -> bool:
        """今天烧到上限就不发新请求：等到第二天零点。返回 True 表示这步别做了。"""
        cap = int(getattr(self.config, "daily_token_cap", 0) or 0)
        warn = int(getattr(self.config, "daily_token_warn", 0) or 0)
        if cap <= 0 and warn <= 0:
            return False
        used = self.store.usage_today()
        total = int(used["total"])
        if warn and total >= warn and self._warn_day != used["day"]:
            self._warn_day = used["day"]
            self.echo("[用量] 今天已经 %s token，过了提醒线 %s"
                      % (_human_tokens(total), _human_tokens(warn)))
        if cap <= 0 or total < cap:
            return False

        if self._cap_day != used["day"]:
            self._cap_day = used["day"]
            self.echo("[上限] 今天已经 %s token，超过上限 %s：等到明天"
                      % (_human_tokens(total), _human_tokens(cap)))
            self._append({"role": "note", "meta": {"kind": "budget"},
                          "content": "今天的额度用完了（%s / %s）。先不再说，等明天。"
                                     % (_human_tokens(total), _human_tokens(cap))})
        if not self.standby:
            self.parked = True
            return True

        deadline = _next_midnight()
        while time.time() < deadline:
            # 上限随时可能被人在设置里调高或者关掉（0 = 不限）。每一轮都重读一次：
            # 以前这里一路睡到天亮，改完设置还得重启才生效——"改了还是没反应"就是这么来的。
            cap_now = int(getattr(self.config, "daily_token_cap", 0) or 0)
            if cap_now <= 0 or int(self.store.usage_today()["total"]) < cap_now:
                self.echo("[上限] 额度改成 %s，接着干"
                          % (_human_tokens(cap_now) if cap_now > 0 else "不限"))
                return False
            self._breathe(deadline)
        return False

    # ── 提醒 ──────────────────────────────────────────────────────────────
    def _nudge(self) -> None:
        """连着几步没有外界动静：问一句「还在做事吗」，不硬停。

        外部输入只有 inbox 进来的东西，工具返回不算——所以联网、查资料这种多步
        任务也会触发这条。触发只是塞一句提醒、把计数清零：模型回一句就接着做，
        真没事它会自己 wait。提醒本身只留最近两条，更早的不进上下文。
        """
        steps = self.since_external
        self.echo("[提醒] 连续 %d 步没有外界动静" % steps)
        text = prompt_files.render("watchdog.txt", steps=steps)
        # 库里留一行备查（渲染时会被跳过），真正起作用的是这一步的瞬时注入。
        self._append({"role": "note", "content": text}, meta={"kind": "watchdog"})
        self.context.reminder = text
        self.since_external = 0

    # ── 校准 ──────────────────────────────────────────────────────────────
    def _measure_block(self, tools) -> None:
        """当前块的真实大小：真数减去固定部分。

        请求 = 固定部分（sink、旧段、工具 schema）+ 上一块 + 当前块。
        固定部分和上一块在两次压缩之间不变，所以减掉它们剩下的就是当前块；
        那两块只能用估算，但它们小、而且不增长，误差是常数，不是累积的。
        """
        if self.last_prompt_tokens <= 0:
            return
        fixed = estimate_tokens(self.context.system_text())
        fixed += estimate_tokens(self.context.previous_text())
        if tools:
            fixed += estimate_tokens(json.dumps(tools, ensure_ascii=False))
        self.current_real = max(0, self.last_prompt_tokens - fixed)

    def _maybe_reload(self) -> None:
        """网页刚改过记录（删了一条）：把两块原文按库里的重读一遍。

        不重启也能生效——这正是「删一条消息」该有的代价。
        """
        if not control.reload_requested():
            return
        control.clear_reload()
        state = self.store.load_state()
        self.context.set_state(state["bands"], state["previous"], state["current"])
        self.previous_id = state["previous_id"]
        self.current_id = state["current_id"]
        self.current_real = self.context.current_tokens()
        self.last_prompt_tokens = 0
        self.echo("[重读] 记录被改过，已按库里的重来")

    def _append(self, message: dict, meta: dict = None):
        # 思考内容不写进记录（它不进下一轮的上下文），但留在 meta 里：
        # 网页要把「思考」画成一行可展开的东西，靠的就是它。
        meta = dict(meta or {})
        if str(message.get("role") or "") == "assistant":
            # 模型手写的“调用 x {...}”“结果：…”一律去掉：真调用走 tool_calls，
            # 真结果走 role=tool 的消息；正文里这两种行只能是它照着旧格式仿的。
            cleaned = strip_fake_calls(message.get("content") or "")
            if cleaned != str(message.get("content") or "").strip():
                self.log("[修] 正文里有手写的调用/结果行或协议标记，已去掉")
            message = dict(message)
            message["content"] = cleaned
        think = str(message.get("reasoning_content") or "").strip()
        if think:
            meta["think"] = think
        elif str(message.get("role") or "") == "assistant":
            # 有就存，没有就记一笔——"这一步为什么没有思考行"能查
            self.log("[思考] 这一步模型没给 reasoning")
        if str(message.get("role") or "") == "assistant" and self.via:
            # 这句话是回给谁的：通道那边照它定向回发（网页也照它画「→ 去哪儿」）
            meta["via"] = dict(self.via)
        entry = self.context.append(message, meta)
        self.store.append_entry(self.current_id, entry, step=self.turn)
        return entry

    # ── 工具 ──────────────────────────────────────────────────────────────
    def _dispatch_calls(self, calls) -> None:
        used = set()
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "{}")
            content = ""
            # 同一个工具连着调太多次（中间没有外界输入）：说明它在打转。
            # 不硬挡：第 4 次先回一句事实，它再调就照跑、计数重新起算——人叫它做事的
            # 时候不该被次数卡死（挡死过一次，它想重试都不行）。话只说事实，不指路：
            # 停是她自己的动作（`wait`），不是被这句提示教出来的。
            count = self._turn_calls.get(name, 0) + 1
            self._turn_calls[name] = count
            ask = ""
            if count > _TURN_CALL_CAP:
                if name in self._turn_confirm:
                    self._turn_confirm.discard(name)
                    self._turn_calls[name] = 1
                    self.log("[确认] %s 连着第 %d 次照跑，计数重新起算" % (name, count))
                else:
                    ask = ("（%s 已经连着调了 %d 次。再调一次照跑。）"
                           % (name, _TURN_CALL_CAP))
            if name in _BLOCKING_TOOLS:
                if name in used:
                    content = ("一次只能调一个 %s：这个回复里已经调过了，这条没执行。"
                               "要同时等几类动静，用 %s 的 kinds。" % (name, name))
                used.add(name)
            if ask and not content:
                content = ask
                self._turn_confirm.add(name)
            self._tool_meta = {}
            text = content or self._call_tool(name, arguments)
            self._append({"role": "tool", "tool_call_id": call.get("id") or "",
                          "content": text}, meta=self._tool_meta or None)
            self._tool_meta = {}
            self.log("[调用] %s %s" % (name, arguments))
            self.log("[结果] %s" % str(text)[:300].replace("\n", " / "))
        self._flush_after_tool()

    def _flush_after_tool(self) -> None:
        """等在工具调用之后才补的记录（醒来的那句话、回来的结果）。

        写完才把对应进件标已读：反过来的话，中途重启就会"人说了话但记录里没有"。

        对方说话也算这一轮到此为止：调用计数跟着清零。否则"等对方说话"是常态，
        计数永远清不掉——人叫她重试一次，工具还会拿上一轮的次数挡回去。
        """
        for entry in self._after_tool:
            inbox_id = int(entry.get("inbox_id") or 0)
            if inbox_id and self.store.inbox_in_record(inbox_id):
                # 记录里已经有这条了（崩在"落记录"和"标已读"之间、重启又走到这
                # 一步）：只把已读补上，别把同一句话再插一遍。
                self.store.consume(inbox_id)
                continue
            self._append(entry["message"], meta=entry.get("meta") or {})
            if inbox_id:
                self.store.consume(inbox_id)
                self._turn_calls = {}
                self._turn_confirm = set()
        self._after_tool = []

    def _unread_note(self) -> None:
        """被叫醒时把「攒了多少」拼一行进记录；同一批只拼一次，随后按步数过期。"""
        summary = self.store.unread_batches()
        total = int(summary.get("total") or 0)
        if not total:
            self._unread_total = 0
            return
        if total == self._unread_total:
            return
        self._unread_total = total
        self._append({"role": "note", "content": _unread_text(summary)},
                     meta={"kind": "unread", "total": total})

    def _call_tool(self, name: str, arguments: str) -> str:
        try:
            if name == "wait":
                return self._wait(arguments)
            if name == "channels":
                return self._show_channels()
            if name == "channels_set":
                return self._set_channel(arguments)
            if name == "messages":
                return self._read_messages(arguments)
            for toolset in self.toolsets:
                if toolset.owns(name):
                    return toolset.call(name, arguments)
            return self.memory.call(name, arguments)
        except control.StopRequested:
            raise
        except Exception as exc:
            # 工具坏了不能让循环停：把原因写进结果，模型下一步能看到。
            return "工具出错：%s" % exc

    # ── 等 ────────────────────────────────────────────────────────────────
    def _drain_inbox(self) -> int:
        """每步开始先把攒着的消息读进来：人在你说话的时候插一句也得看到。

        `via`（这句话是回给谁的）只在**一个**会话叫她的时候才立：几个会话同时
        说话的时候挑不出来，宁可不立——立错了会把回答发给另一个人。
        """
        taken = 0
        came: dict = {}              # chat_id -> via；本机直投记成空
        while True:
            item = self.store.peek(policies=["wake"], unnotified=True)
            if item is None:
                break
            # 带来源的（通道转进来的）只响铃：正文留在表里，等她的 messages 来取。
            # 本机直投的（网页 / 命令行，`direct`）正文直接进记录。
            if item.get("direct"):
                if not self.store.inbox_in_record(item["id"]):
                    self._append(_inbox_entry(item), meta=in_meta(item))
                self.store.consume(item["id"])
                came[str(item.get("chat_id") or "")] = {}
            else:
                self.store.mark_notified(item["id"])
                came[str(item.get("chat_id") or "")] = via_of(item)
            self._turn_calls = {}
            self._turn_confirm = set()
            taken += 1
            self.since_external = 0
        if taken:
            self.via = _one_via(came)
        return taken

    def _show_channels(self) -> str:
        """会话台账：谁来过、几条没读、什么策略。只列事实。"""
        rows = self.store.channels()
        if not rows:
            return "还没有任何会话给你发过消息。"
        lines = ["%d 个会话：" % len(rows)]
        for row in rows:
            name = row.get("chat_name") or row.get("chat_id") or "?"
            source = row.get("source") or "外部"
            kind = "群" if row.get("chat_type") == "group" else "私聊"
            lines.append("%s · %s %s「%s」· 未读 %d · 策略 %s" % (
                row.get("chat_id"), source, kind, name,
                int(row.get("unread") or 0), row.get("policy") or "wake"))
        return "\n".join(lines)

    def _set_channel(self, arguments: str) -> str:
        try:
            payload = json.loads(arguments or "{}")
        except ValueError:
            return "channels_set 的参数不是 JSON。"
        chat = str(payload.get("chat") or "").strip()
        policy = str(payload.get("policy") or "").strip().lower()
        if not chat or not policy:
            return "channels_set 需要 chat 和 policy。"
        try:
            changed = self.store.set_policy(chat, policy)
        except KeyError:
            return "没有这个会话：%s。" % chat
        except ValueError as exc:
            return "改策略失败：%s" % exc
        if policy == "wake":
            self._unread_total = 0        # 策略变了，下一行数字重新拼
        if not changed:
            return "把 %s 改成 %s 了（它现在没有未读的消息，往后进来的按新策略）。" % (
                chat, policy)
        return "把 %s 改成 %s 了，影响到 %d 条没读的。" % (chat, policy, changed)

    def _read_messages(self, arguments: str) -> str:
        try:
            payload = json.loads(arguments or "{}")
        except ValueError:
            return "messages 的参数不是 JSON。"
        chat = str(payload.get("chat") or "").strip()
        try:
            limit = int(payload.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        try:
            before = int(payload.get("before") or 0)
        except (TypeError, ValueError):
            before = 0
        rows = self.store.messages(chat, limit=min(50, max(1, limit)), before=before)
        if not rows:
            return "没有攒着的消息（或者这个会话没有）。"
        self.store.consume_many([row["id"] for row in rows])
        # 这几条的正文是作为**这条工具结果**进记录的：记下编号，对账时才不会
        # 把它们当成"丢了"，从而又插一条 user 消息（2026-10-28 就这么窜过一次）。
        self._tool_meta = {"inbox_ids": [int(row["id"]) for row in rows]}
        self._unread_total = 0
        # 读完在跟谁说话：这批同属一个外部会话时，她接下来的话就该回到那儿
        # （不然 batch 积压读回来再回答，回答根本发不出去）。
        chats = {str(row.get("chat_id") or "") for row in rows
                 if not row.get("direct") and str(row.get("source") or "")}
        chats.discard("")
        self.via = via_of(rows[-1]) if len(chats) == 1 else {}
        lines = ["读到 %d 条：" % len(rows)]
        lines.extend(_message_line(row) for row in rows)
        if len(rows) >= limit:
            lines.append("（还有更早的；要往前翻把 before 设成 %d）" % rows[0]["id"])
        return "\n".join(lines)

    def _wait(self, arguments: str) -> str:
        """等到有动静或者到点；返回一行结果，动静本身跟在结果后面进记录。"""
        try:
            payload = json.loads(arguments or "{}")
        except ValueError:
            return "wait 的参数不是 JSON。"
        if not isinstance(payload, dict):
            return "wait 的参数不是对象。"
        kinds = [str(kind).strip() for kind in (payload.get("kinds") or [])
                 if str(kind).strip()]
        spec = str(payload.get("until") or "").strip()
        try:
            deadline = parse_until(spec)
        except ValueError as exc:
            return str(exc)

        started = time.time()
        self.store.set_status("waiting", spec or "、".join(kinds) or "任何动静")
        while True:
            if control.stop_requested():
                # 不再编一句「要重启，先停。」塞进记录：那会让模型以为有人跟它说话，
                # 于是留下两句道别。直接退，没答完的调用留给下次起来接着等。
                raise control.StopRequested()
            # 只有 wake 的能把她叫醒；batch / mute 的留在表里，不打断这次等。
            item = self.store.peek(kinds or None, policies=["wake"], unnotified=True)
            if item is not None:
                if not item.get("direct"):
                    # 通道来的：响铃，正文等她自己读。
                    self.store.mark_notified(item["id"])
                    self.via = via_of(item)
                    self.since_external = 0
                    self._woke = True
                    self.store.set_status("thinking")
                    return _ring_text(item)
                self._after_tool.append({"message": _inbox_entry(item),
                                         "meta": in_meta(item),
                                         "inbox_id": item["id"]})
                self.via = {}
                self.since_external = 0
                self._woke = True
                self.store.set_status("thinking")
                return _woke_text(str(item["kind"]), time.time() - started)
            if deadline is not None and time.time() >= deadline:
                # 等回去没事：上一句外部消息的回复窗口到此为止。否则过一阵她
                # 自己想说的话，还会被当成在回那个会话。
                self.via = {}
                self._woke = True
                self.store.set_status("thinking")
                return _timeout_text(spec, time.time() - started)
            if deadline is None and not self.standby:
                # 没人能把消息送进来：无上界的等没有意义，直接停。
                self.parked = True
                self.store.set_status("parked", "没有输入通道")
                self.echo("[等待] 没有输入通道，先停住")
                return "没有动静。"
            self._breathe(deadline)

    def _breathe(self, deadline: Optional[float]) -> None:
        """等的时候顺手做维护：块满了就压，过期的短期记忆归档，页面上的改动落地。"""
        self._maintain()
        self._apply_edits()
        self._maybe_reload()
        if control.archive_requested():
            # 网页点了「归档」而她在睡：等的时候正好是跑它的时机（没人在等她回话）。
            self._archive_if_asked([])
            self.store.set_status("waiting")
        span = _POLL_SECONDS
        if deadline is not None:
            span = min(span, max(0.01, deadline - time.time()))
        if self.current_real >= self.config.block_tokens and self.context.current.entries:
            self._compress_if_needed()
            if self.parked:
                return
        time.sleep(span)

    def _maintain(self) -> None:
        """闲时才做的活：归档到期的短期记忆、清进件、隔一阵重写自我描述。"""
        stamp = time.time()
        if stamp < self._next_archive:
            return
        self._next_archive = stamp + 600.0
        self._maybe_rewrite_persona()
        try:
            count = self.memory.archive_expired()
        except Exception as exc:      # 归档坏了不该影响在场
            self.log("[归档失败] %s" % exc)
            count = 0
        if count:
            self.echo("[归档] %d 件过期的短期记忆压成一行搬进归档" % count)
        try:
            self.store.trim_inbox()
        except Exception as exc:
            self.log("[清进件失败] %s" % exc)

    # ── 归档 ──────────────────────────────────────────────────────────────
    def _archive_chat(self, messages):
        """归档那次调用不走 `_chat`：那条会把增量写进草稿，网页上就看着她"在说" JSON。"""
        return self.client.chat(messages, tools=None)

    def _archive_if_asked(self, got) -> None:
        """网页点了「提前清」：把当前这块**提前封口**（不等它攒满）。

        走的就是滚块那条路：压缩上一块 → 补录正要失去原文的那块 → 新开一块。
        所以它和"块满自动触发"是同一件事，只是时机由人点。
        """
        if not control.archive_requested():
            return
        if got:
            return
        control.clear_archive()
        if not bool(getattr(self.config, "archive_enabled", True)):
            self.echo("[提前清] 关着（设置里的 archive_enabled）")
            return
        # 额度不够就先不跑：这里只做一次便宜的判断，不叫 `_budget_guard`（它会睡到明天）
        cap = int(getattr(self.config, "daily_token_cap", 0) or 0)
        if cap > 0 and int(self.store.usage_today()["total"]) >= cap:
            self.echo("[提前清] 今天的额度用完了，先不跑")
            return
        if not self.context.current.entries:
            self.echo("[提前清] 当前这块是空的，没什么可清")
            return
        self.store.set_status("thinking", "提前清：把当前这块封口")
        self.echo("[提前清] 开始：压缩 → 补录 → 新开一块")
        self._roll("手动提前")
        self.store.set_status("idle")

    def _apply_edits(self) -> None:
        """把页面「存到服务器」的改动落到图上。

        网页那边是只读的（图只有循环这一个写者），所以它只把清单写进
        `<数据>/edits/*.json`；这里在两步之间（和等的时候）取出来应用，落完把文件
        挪进 `edits/applied/`，失败就留在原地下次再试。
        """
        folder = os.path.join(str(self.config.data_dir or ""), "edits")
        if not os.path.isdir(folder) or self.memory.graph is None:
            return
        from memory_v2 import edits as memory_edits

        done = os.path.join(folder, "applied")
        targets = sorted(name for name in os.listdir(folder) if name.endswith(".json"))
        for name in targets:
            path = os.path.join(folder, name)
            try:
                payload = memory_edits.load(path)
                if not payload["ops"]:
                    raise memory_edits.EditError("清单里没有 ops")
                result = memory_edits.apply(self.memory.graph, payload["ops"])
                if result["changed"]:
                    saved = memory_edits.backup(self.memory.graph.path)
                    self.memory.graph.save()
                    self.log("[页面改动] %d 处生效（备份 %s）" % (result["changed"],
                                                                os.path.basename(saved)))
                for line in result["log"]:
                    self.log("[页面改动] %s" % line)
                for line in result["skipped"]:
                    self.log("[页面改动] 跳过：%s" % line)
                os.makedirs(done, exist_ok=True)
                os.replace(path, os.path.join(done, name))
            except Exception as exc:       # 一条清单坏了不能把循环带下去
                self.log("[页面改动] 这次没落成：%s（%s）" % (exc, name))
                return

    def _maybe_rewrite_persona(self) -> None:
        """到点就重写一次自我描述：内容归她，时机归这里。

        不放在每一步：persona 在 system 上，改一次前缀缓存整段作废。
        失败就退避十分钟再试，当前那版原样留着。
        """
        if not persona.due(self.config, floor=self._next_persona):
            return
        if not self.context.current.entries:
            return
        # 聊得不够多就先不写：凑够 persona_min_chars 才值得动一次缓存
        if not persona.enough(self.context, self.config):
            return
        try:
            body = persona.rewrite(self._persona_chat, self.context, self.config)
        except Exception as exc:          # 整理坏了不该影响在场
            self._next_persona = time.time() + 600.0
            self.log("[自我] 重写失败：%s" % exc)
            return
        persona.touch_stamp(self.config)
        self._next_persona = 0.0
        self.log("[自我] 重写了自己的描述（%d 字）" % len(body))

    def _persona_chat(self, messages) -> dict:
        """整理任务跑一次；不进记录，也不影响这一步的用量统计。

        模型由 persona_model 决定：空就跟主模型走，填了就用它单独跑（便宜的就省额度）。
        """
        model = str(getattr(self.config, "persona_model", "") or "").strip()
        if not model or model == str(getattr(self.config, "model", "")):
            return self.client.chat(messages)
        return self._persona_client_for(model).chat(messages)

    def _persona_client_for(self, model: str):
        """整理任务用的客户端：按模型名造一个并缓存，换了模型就重建。"""
        cached = getattr(self, "_persona_client", None)
        if cached is not None and cached[0] == model:
            return cached[1]
        from .compress import make_client

        client = make_client(self.config, self.credentials, model,
                            temperature=0.6,
                            max_tokens=min(self.config.max_tokens, 2000))
        self._persona_client = (model, client)
        return client

    def _repair_inbox(self) -> None:
        """对账：进件里标了已读、记录里却没有的，补回去。

        `_wait` 是"先落记录再标已读"，正常不会丢；但重启、异常或者旧版本留下的
        洞会让某条消息只剩进件。这里按 `meta.inbox_id` 核一遍，缺的就补进记录，
        并记一条日志——补得回来的就不要再丢第二次。
        """
        try:
            # 只看最近两小时的：重启时的洞就在眼前，别把傍晚的老消息翻出来重插
            cutoff = time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(time.time() - _REPAIR_WINDOW))
            missing = self.store.inbox_unappended(since=cutoff)
        except Exception as exc:
            self.log("[对账] 查不了进件：%s" % exc)
            return
        # 按内容去重：老数据没有 inbox_id，同一句话可能早就在记录里了
        existing = {str(entry.message.get("content") or "").strip()
                    for entry in self.context.current.entries + self.context.previous.entries
                    if str(entry.message.get("role") or "") == "user"}
        for item in missing:
            text = str(item.get("text") or "").strip()
            if text and text in existing:
                # 已经在记录里：补一个身份标记就行，别重复插一条
                self.log("[对账] 这条已在记录里，跳过（%d 字）" % len(text))
                continue
            try:
                self._append(_inbox_entry(item), meta=in_meta(item))
                existing.add(text)
                self._fresh_input = True      # 补回来的话也要有一步，别只躺着等
                self.log("[对账] 补回一条没落库的消息（%d 字）" % len(str(item.get("text") or "")))
            except Exception as exc:
                self.log("[对账] 补不回去：%s" % exc)

    def _resume_waits(self) -> bool:
        """上次是在等的时候断的：块里有没有结果的 wait 调用，就接着等。

        返回 True 表示接过一次——那这一次的记录变了，起来之后该说话，不该干等。
        """
        if not self.standby:
            return False
        resumed = False
        entries = self.context.current.entries
        answered = {str(entry.message.get("tool_call_id") or "")
                    for entry in entries
                    if str(entry.message.get("role") or "") == "tool"}
        for entry in entries:
            for call in entry.message.get("tool_calls") or []:
                function = call.get("function") or {}
                call_id = str(call.get("id") or "")
                if str(function.get("name") or "") != "wait" or call_id in answered:
                    continue
                self.echo("[接着等] 上次等到一半断了")
                self._append({"role": "tool", "tool_call_id": call_id,
                              "content": self._wait(str(function.get("arguments") or "{}"))})
                self._flush_after_tool()
                resumed = True
        return resumed

    # ── 压缩 ──────────────────────────────────────────────────────────────
    def _compress_if_needed(self) -> None:
        threshold = max(self.config.block_tokens, self._compress_after)
        if self.current_real >= threshold:
            self._roll("块满")
            return
        if self.last_prompt_tokens > self.config.context_budget:
            if self.context.current.entries and self.context.previous.entries:
                self._roll("超出上下文预算")
            elif self.context.bands:
                self.context.drop_oldest()
                self.store.replace_bands(self.context.bands)
                self.last_prompt_tokens = 0
                self.echo("[预算] 丢最旧的一段")

    def _backfill_target(self) -> int:
        """这一轮补录谁：最新一块"已经压过、原文还在库里"的块。

        滚块时它正要失去原文，是最后的机会；搬走之后就只剩那一条摘要了。
        """
        for row in sorted(self.store.blocks(), key=lambda item: int(item["id"]),
                          reverse=True):
            block_id = int(row["id"])
            if str(row.get("state") or "") != "compressed":
                continue
            if self.store.entries(block_id):
                return block_id
        return 0

    def _roll(self, reason: str) -> None:
        """当前块写满：先压上一块，再把当前块升成上一块。"""
        if not self.context.current.entries:
            return
        # 滚块就是归档的时机：次新那块马上被压成摘要，第三新那块马上失去原文
        target_id = self._backfill_target()
        if target_id and self.context.previous.entries:
            try:
                material = archive.roll_material(self.store, self.config, target_id,
                                                 self.previous_id)
                report = archive.run(self.config, self.context, self.memory,
                                     self._archive_chat, log=self.log,
                                     store=self.store,
                                     keep_ids={self.previous_id, self.current_id},
                                     material=material, target_id=target_id)
            except Exception as exc:            # 归档坏了不该挡住滚块
                report = {"ok": False, "reason": "crashed", "error": str(exc)}
            self.archive_last = report
            if report.get("ok"):
                self.echo("[归档] %s" % archive.summary(report))
            else:
                self.echo("[归档] 没做成：%s"
                          % (report.get("error") or report.get("reason")))
        if self.context.previous.entries:
            try:
                result = self.compressor.compress_block(self.context.previous)
            except CompressError as exc:
                self.echo("[压缩失败] %s；保留原文，等下一次" % exc)
                self._compress_after = self.current_real + 1000
                return
            bands = add_band(self.context.bands, result["summary"],
                             result["start_ts"], result["end_ts"],
                             self.config.band_chars, self.config.band_decay)
            self.context.set_bands(bands)
            self.store.replace_bands(bands)
            self.store.finish_previous(self.previous_id, result["summary"],
                                       result["keywords"], result["unfinished"])
            self.compressions += 1
            self.last_prompt_tokens = 0
            self.echo("[压缩] %s：%s–%s；现在 %d 段" % (
                reason, result["start_ts"], result["end_ts"], len(bands)))

        old_current = self.current_id
        self.context.promote()
        self.store.promote_block(old_current, self.context.previous.end_ts)
        self.previous_id = old_current
        self.current_id = self.store.open_block(self.context.current.start_ts)
        self._compress_after = 0
        # 新块是空的；下一步拿到真数再换掉这个估算。
        self.current_real = 0

    # ── 挂起 ──────────────────────────────────────────────────────────────
    def _park(self, note: str) -> None:
        """没有动作：不说了，等有动静。有输入通道就真等，没有就停住。"""
        self.echo("[挂起] %s" % (note or "没有动作"))
        self.store.set_status("parked", note or "没有动作")
        if self.standby:
            self._wait("{}")
            self._flush_after_tool()
            return
        if self.config.park_seconds > 0:
            time.sleep(self.config.park_seconds)
            self.parked = False
        else:
            self.parked = True
