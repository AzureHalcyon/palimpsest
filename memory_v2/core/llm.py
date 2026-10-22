"""OpenAI-compatible chat client (non-streaming + SSE streaming).

流式这条路只服务“看得见”：每个增量先交给 `on_delta(kind, text)`，
返回的仍然是**拼好的一条 assistant message**（含 `tool_calls` / `reasoning_content`），
所以上面的 `EventChat` 不需要知道流不流式。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import List, Optional

from .think import ThinkFilter, normalize_message


class ChatError(RuntimeError):
    pass


class ChatClient:
    def __init__(self, config, credentials):
        self.config = config
        self.credentials = credentials
        # 最近一次响应的 usage。`chat()` 的返回值是 message（不含 usage），
        # 而界面要把这一轮花了多少 token 显示出来，所以单独留一份。
        self.last_usage = {}
        # 这次响应有没有真的走流式（给宿主判断还要不要补一次整段）。
        self.last_streamed = False

    def _body(self, messages: List[dict], tools: Optional[list],
              temperature: Optional[float], model: Optional[str],
              stream: bool) -> dict:
        body = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens,
        }
        effort = str(getattr(self.config, "reasoning_effort", "") or "").strip()
        if effort and effort not in ("auto", "default"):
            body["reasoning_effort"] = effort
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if stream:
            body["stream"] = True
            # DeepSeek / OpenAI 兼容端点用它把 usage 放进最后一帧。
            body["stream_options"] = {"include_usage": True}
        return body

    def chat(self, messages: List[dict], tools: Optional[list] = None,
             temperature: Optional[float] = None, model: Optional[str] = None,
             on_delta=None) -> dict:
        if on_delta is not None:
            return self._chat_stream(messages, tools, temperature, model, on_delta)
        self.last_streamed = False
        body = self._body(messages, tools, temperature, model, False)
        data = json.dumps(body).encode("utf-8")
        last_error = None
        for attempt in range(self.config.retries):
            request = urllib.request.Request(self.credentials.chat_url, data=data, method="POST")
            request.add_header("Authorization", "Bearer " + self.credentials.api_key)
            request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.last_usage = payload.get("usage") or {}
                message = (payload.get("choices") or [{}])[0].get("message") or {}
                return normalize_message(message)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.config.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ChatError("HTTP %s" % exc.code) from exc
            except Exception as exc:
                last_error = exc
                if attempt < self.config.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ChatError(str(exc)) from exc
        raise ChatError(str(last_error))

    def _emit(self, on_delta, kind: str, text: str) -> None:
        if not text:
            return
        try:
            on_delta(str(kind), str(text))
        except Exception:
            # 显示失败绝不能打断这一轮（它是“看得见”，不是业务）。
            pass

    def _chat_stream(self, messages: List[dict], tools: Optional[list],
                     temperature: Optional[float], model: Optional[str],
                     on_delta) -> dict:
        self.last_streamed = False
        body = self._body(messages, tools, temperature, model, True)
        data = json.dumps(body).encode("utf-8")
        last_error = None
        for attempt in range(self.config.retries):
            request = urllib.request.Request(self.credentials.chat_url, data=data, method="POST")
            request.add_header("Authorization", "Bearer " + self.credentials.api_key)
            request.add_header("Content-Type", "application/json")
            request.add_header("Accept", "text/event-stream")
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    if "application/json" in content_type:
                        # 端点没按流式回：仍然按非流式解析一次，但把整段推给界面。
                        payload = json.loads(response.read().decode("utf-8"))
                        self.last_usage = payload.get("usage") or {}
                        message = (payload.get("choices") or [{}])[0].get("message") or {}
                        message = normalize_message(message)
                        reasoning = str(message.get("reasoning_content")
                                        or message.get("reasoning") or "")
                        self._emit(on_delta, "reasoning", reasoning)
                        self._emit(on_delta, "content", str(message.get("content") or ""))
                        return message
                    message, usage = self._read_sse(response, on_delta)
                    self.last_usage = usage
                    self.last_streamed = True
                    return message
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.config.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ChatError("HTTP %s" % exc.code) from exc
            except Exception as exc:
                last_error = exc
                if attempt < self.config.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise ChatError(str(exc)) from exc
        raise ChatError(str(last_error))

    def _read_sse(self, response, on_delta):
        content: List[str] = []
        reasoning: List[str] = []
        calls = {}
        usage = {}
        saw_data = False
        think = ThinkFilter()
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            payload_text = line[5:].strip()
            if payload_text == "[DONE]":
                break
            if not payload_text:
                continue
            saw_data = True
            try:
                payload = json.loads(payload_text)
            except ValueError:
                continue
            if payload.get("error"):
                raise ChatError(str(payload.get("error")))
            if payload.get("usage"):
                usage = payload.get("usage") or {}
            choices = payload.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("reasoning_content") or delta.get("reasoning") or ""
            if piece:
                reasoning.append(str(piece))
                self._emit(on_delta, "reasoning", str(piece))
            piece = delta.get("content")
            if piece:
                # 有的网关把思考内联在 content 里（`<think>…</think>`），先在流上分开。
                for kind, text in think.feed(str(piece)):
                    if kind == "reasoning":
                        reasoning.append(text)
                        self._emit(on_delta, "reasoning", text)
                    else:
                        content.append(text)
                        self._emit(on_delta, "content", text)
            for call in delta.get("tool_calls") or []:
                index = int(call.get("index") or 0)
                record = calls.setdefault(index, {
                    "id": "", "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if call.get("id"):
                    record["id"] = str(call["id"])
                if call.get("type"):
                    record["type"] = str(call["type"])
                function = call.get("function") or {}
                if function.get("name"):
                    record["function"]["name"] = (record["function"]["name"]
                                                  or str(function["name"]))
                if function.get("arguments"):
                    record["function"]["arguments"] += str(function["arguments"])
        if not saw_data:
            raise ChatError("流式响应里没有 data 帧")
        for kind, text in think.flush():
            if kind == "reasoning":
                reasoning.append(text)
                self._emit(on_delta, "reasoning", text)
            else:
                content.append(text)
                self._emit(on_delta, "content", text)
        message = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return message, usage
