"""步循环：拼上下文 → 主模型 → 执行 → 继续。

两块原文：当前块写满，不压它；把它变成“上一块”，等下一块写满时再压。
工具走原生 tool_calls；正文是“说”，空回复是“停”。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from memory_v2.core.config import ChatConfig, Credentials
from memory_v2.core.llm import ChatClient

from .compress import CompressError, Compressor, add_band
from .context import Context
from .store import LoopStore


class Loop:
    def __init__(self, config, credentials, store: LoopStore,
                 compressor: Compressor, memory,
                 client=None,
                 on_speak: Optional[Callable[[str], None]] = None,
                 on_step: Optional[Callable[[dict], None]] = None,
                 echo: Optional[Callable[[str], None]] = None):
        self.config = config
        self.store = store
        self.compressor = compressor
        self.memory = memory
        self.on_speak = on_speak or (lambda text: None)
        self.on_step = on_step or (lambda info: None)
        self.echo = echo or (lambda text: None)
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
        # 摘要失败后别每步都重试：等当前块再长一点。
        self._compress_after = 0

    # ── 一步 ──────────────────────────────────────────────────────────────
    def step(self) -> str:
        self.turn = self.store.next_turn()
        if hasattr(self.memory, "step"):
            self.memory.step = self.turn
        self._compress_if_needed()
        tools = self.memory.tools()
        message = self.client.chat(self.context.messages(), tools=tools or None)
        usage = getattr(self.client, "last_usage", None) or {}
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or 0)

        content = str(message.get("content") or "").strip()
        calls = list(message.get("tool_calls") or [])
        self._append(message)
        if calls:
            self._dispatch_calls(calls)
            kind = "call"
        elif content:
            self.on_speak(content)
            kind = "speak"
        else:
            # 没有内容也没有调用：这一步没有动作，停住。
            self._park("没有动作")
            kind = "park"

        self.steps += 1
        self.on_step({"step": self.steps, "kind": kind, "text": content,
                      "current_tokens": self.context.current_tokens(),
                      "prompt_tokens": self.last_prompt_tokens,
                      "compressions": self.compressions})
        return kind

    def run(self, max_steps: Optional[int] = None) -> int:
        limit = self.config.max_steps if max_steps is None else max_steps
        while not (limit and self.steps >= limit):
            self.step()
            if self.parked:
                break
        return self.steps

    def inject_user(self, text: str) -> None:
        """外部消息进记录：下一次请求就带着它。"""
        self._append({"role": "user", "content": str(text)})

    def _append(self, message: dict):
        entry = self.context.append(message)
        self.store.append_entry(self.current_id, entry)
        return entry

    # ── 工具 ──────────────────────────────────────────────────────────────
    def _dispatch_calls(self, calls) -> None:
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            arguments = str(function.get("arguments") or "{}")
            self._append({"role": "tool", "tool_call_id": call.get("id") or "",
                          "content": self._call_tool(name, arguments)})

    def _call_tool(self, name: str, arguments: str) -> str:
        try:
            return self.memory.call(name, arguments)
        except Exception as exc:
            # 工具坏了不能让循环停：把原因写进结果，模型下一步能看到。
            return "工具出错：%s" % exc

    # ── 压缩 ──────────────────────────────────────────────────────────────
    def _compress_if_needed(self) -> None:
        threshold = max(self.config.block_tokens, self._compress_after)
        if self.context.current_tokens() >= threshold:
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
                self._compress_after = self.context.current_tokens() + 1000
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

    # ── 挂起 ──────────────────────────────────────────────────────────────
    def _park(self, note: str) -> None:
        self.echo("[挂起] %s" % (note or "没有动作"))
        if self.config.park_seconds > 0:
            time.sleep(self.config.park_seconds)
            self.parked = False
        else:
            self.parked = True
