"""步循环：拼上下文 → 主模型 → 执行 → 继续。

两块原文：当前块写满，不压它；把它变成“上一块”，等下一块写满时再压。
工具走原生 tool_calls；正文是“说”，空回复是“停”。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from memory_v2.core.config import ChatConfig, Credentials
from memory_v2.core.llm import ChatClient, ChatError

from . import control
from . import prompt_files
from . import shell
from . import webtools
from . import workspace
from .compress import CompressError, Compressor, add_band
from .context import Context, estimate_tokens
from .store import LoopStore

# 一个回复里只允许调一个的工具：阻塞的、必须独占一步的。多出来的不执行，直接
# 告诉模型为什么——两个 wait 不是「或者」，是「等完这个再等那个」，会一直挂着。
_BLOCKING_TOOLS = ("wait",)

# 等的时候多久看一次：对方一句话最多晚这么久才被读到。
_POLL_SECONDS = 1.0

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


def _inbox_entry(item: dict) -> dict:
    """一条进件写成记录：对方说的话是 `对方：`，别的动静是 `提醒：`。"""
    kind = str(item.get("kind") or "user")
    text = str(item.get("text") or "").strip()
    if kind == "user":
        return {"role": "user", "content": text}
    return {"role": "note", "content": "%s：%s" % (kind, text or "（没有内容）")}


class Loop:
    def __init__(self, config, credentials, store: LoopStore,
                 compressor: Compressor, memory,
                 client=None,
                 on_speak: Optional[Callable[[str], None]] = None,
                 on_step: Optional[Callable[[dict], None]] = None,
                 echo: Optional[Callable[[str], None]] = None,
                 log: Optional[Callable[[str], None]] = None):
        self.config = config
        self.store = store
        self.compressor = compressor
        self.memory = memory
        self.on_speak = on_speak or (lambda text: None)
        self.on_step = on_step or (lambda info: None)
        self.echo = echo or (lambda text: None)
        self.log = log or (lambda text: None)
        self.client = client or ChatClient(
            ChatConfig(model=config.model, temperature=config.temperature,
                       max_tokens=config.max_tokens, timeout=config.timeout,
                       retries=config.retries),
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
        # 距上一次外部输入过了几步：用来拦「自己在跟自己转」。
        self.since_external = 0
        self.retries = 0
        self._next_archive = 0.0
        self._cap_day = ""
        self._warn_day = ""
        # 摘要失败后别每步都重试：等当前块再长一点。
        self._compress_after = 0
        # 连着几步「只说了话、没调工具」：第 1 次之后在末尾问一句还要不要继续，
        # 问了还是只说话就停下（不再问第二遍，免得提问本身变成复读机）。
        self._quiet = 0

    # ── 工具面 ────────────────────────────────────────────────────────────
    def tools(self) -> List[dict]:
        tools = list(self.memory.tools())
        text = prompt_files.tool_texts()
        tools.append(_wait_tool(text))
        tools.extend(webtools.tools())
        tools.extend(shell.tools(self.config))
        tools.extend(workspace.tools(self.config))
        return tools

    # ── 一步 ──────────────────────────────────────────────────────────────
    def step(self) -> str:
        if control.stop_requested():
            raise control.StopRequested()
        self._maybe_reload()
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
        idle = int(getattr(self.config, "watchdog_idle", 0) or 0)
        if idle and self.since_external >= idle:
            self._nudge()
        if self._budget_guard():
            return "park"
        self._compress_if_needed()
        tools = self.tools()
        # 上一轮说了话、没调工具：这一步末尾问一句「还要继续吗」，问得中性、
        # 不带"该做什么"的提示——它自己决定调工具继续还是 wait 停下。
        self.context.asking = self._quiet == 1
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
        resumed = self._resume_waits()
        # 刚起来：没挂着的等、也没有人说话，就先等着。不然模型会对着块的最后
        # 一句再应一遍——每次重启都白说两句，还把记录弄脏。
        if self.standby and not resumed and not self.store.has_pending():
            self._park("刚起来，没有动静")
        while not (limit and self.steps >= limit):
            self.step()
            if self.parked:
                break
        return self.steps

    def inject_user(self, text: str) -> None:
        """外部消息进记录：下一次请求就带着它。"""
        self._append({"role": "user", "content": str(text)})
        self.since_external = 0

    # ── 调模型 ────────────────────────────────────────────────────────────
    def _chat(self, messages, tools) -> dict:
        """调不通就一直试：长跑里一次抖动不该把循环打死。

        `ChatClient` 自己会重试 `retries` 次；这里兜的是「重试完还是不行」——退避
        往后翻倍，封顶五分钟，一直试到通为止。试不通的时候等一等也不花调用。
        """
        delay = max(1.0, float(getattr(self.config, "retry_delay", 5.0) or 5.0))
        while True:
            try:
                return self.client.chat(messages, tools=tools or None)
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
        think = str(message.get("reasoning_content") or "").strip()
        if think:
            meta["think"] = think
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
            if name in _BLOCKING_TOOLS:
                if name in used:
                    content = ("一次只能调一个 %s：这个回复里已经调过了，这条没执行。"
                               "要同时等几类动静，用 %s 的 kinds。" % (name, name))
                used.add(name)
            text = content or self._call_tool(name, arguments)
            self._append({"role": "tool", "tool_call_id": call.get("id") or "",
                          "content": text})
            self.log("[调用] %s %s" % (name, arguments))
            self.log("[结果] %s" % str(text)[:300].replace("\n", " / "))
        self._flush_after_tool()

    def _flush_after_tool(self) -> None:
        """等在工具调用之后才补的记录（醒来的那句话、回来的结果）。"""
        for message in self._after_tool:
            self._append(message)
        self._after_tool = []

    def _call_tool(self, name: str, arguments: str) -> str:
        try:
            if name == "wait":
                return self._wait(arguments)
            if name in webtools.NAMES:
                return webtools.call(name, arguments)
            if name in shell.NAMES:
                return shell.call(name, arguments, self.config)
            if name in workspace.NAMES:
                return workspace.call(name, arguments, self.config)
            return self.memory.call(name, arguments)
        except control.StopRequested:
            raise
        except Exception as exc:
            # 工具坏了不能让循环停：把原因写进结果，模型下一步能看到。
            return "工具出错：%s" % exc

    # ── 等 ────────────────────────────────────────────────────────────────
    def _drain_inbox(self) -> int:
        """每步开始先把攒着的消息读进来：人在你说话的时候插一句也得看到。"""
        taken = 0
        while True:
            item = self.store.take()
            if item is None:
                return taken
            self._append(_inbox_entry(item))
            taken += 1
            self.since_external = 0

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
            item = self.store.take(kinds or None)
            if item is not None:
                self._after_tool.append(_inbox_entry(item))
                self.since_external = 0
                self.store.set_status("thinking")
                return _woke_text(str(item["kind"]), time.time() - started)
            if deadline is not None and time.time() >= deadline:
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
        """等的时候顺手做维护：块满了就压，过期的短期记忆归档。"""
        self._maintain()
        self._maybe_reload()
        span = _POLL_SECONDS
        if deadline is not None:
            span = min(span, max(0.01, deadline - time.time()))
        if self.current_real >= self.config.block_tokens and self.context.current.entries:
            self._compress_if_needed()
            if self.parked:
                return
        time.sleep(span)

    def _maintain(self) -> None:
        """闲时才做的活：归档到期的短期记忆、清进件，别每个轮询都扫一遍。"""
        stamp = time.time()
        if stamp < self._next_archive:
            return
        self._next_archive = stamp + 600.0
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

    def _roll(self, reason: str) -> None:
        """当前块写满：先压上一块，再把当前块升成上一块。"""
        if not self.context.current.entries:
            return
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
