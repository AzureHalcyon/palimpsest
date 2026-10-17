# -*- coding: utf-8 -*-
"""底座模型客户端（OpenAI 兼容）。

三条实测得来的硬规矩，缺一条就会"看起来能跑，实际丢东西"：

1. ★ **空回复是真问题**：`deepseek-flash` 是**推理模型**，`max_tokens` 小了会被 reasoning
   吃光 ⇒ 返回空 content（实测 128 轮里 20 轮空）⇒ **起始 ≥6000，且空回复时逐级放大重试**。
2. ★ **网络层必须带重试**：一次 `SSL: UNEXPECTED_EOF` 把整跑打断过。
3. ★ **归档的 JSON 必须能修**：模型偶尔给裸换行/尾逗号/代码块 ⇒ 见 `parse_json`。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional

from .config import ChatConfig, Credentials


# --------------------------------------------------------------------------- 流式（SSE）
# ★★ 2026-10-07（用户要求：「聊天和归档都可以做成流式，让 COT 也可见」）：
#   下面三个是**纯函数** —— 解析与累积都不碰网络 ⇒ 自检直接喂假数据就能验。
#   实测（本机探针）：`stream:true` 支持（`text/event-stream`），
#   且 delta 里**真的有 `reasoning_content`**（首字延迟 0.32s）⇒ CoT 拿得到。
def sse_deltas(lines) -> List[dict]:
    """SSE 原始行 ⇒ 一串 `delta` dict。`[DONE]` 收工；解析不了的块**跳过**（不当错误）。"""
    out: List[dict] = []
    for raw in lines:
        line = (raw or "").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            d = json.loads(payload)
        except Exception:  # noqa: BLE001  半截 JSON（少见）：跳过这一块，别整场崩
            continue
        ch = (d.get("choices") or [{}])[0]
        out.append(ch.get("delta") or {})
    return out


def delta_pieces(delta: dict) -> List[tuple]:
    """一个 delta ⇒ `[(kind, text)]`；kind ∈ {`reasoning`, `content`}。
    ⛔ 顺序**先 reasoning 后 content**（服务端本来就是这么发的，保持原样）。"""
    out: List[tuple] = []
    r = delta.get("reasoning_content")
    if r:
        out.append(("reasoning", str(r)))
    c = delta.get("content")
    if c:
        out.append(("content", str(c)))
    return out


def merge_delta(acc: dict, delta: dict) -> dict:
    """把流式 delta **累积**成一个 `message` dict（与非流式的返回形状一致）。

    ★ `tool_calls` 是**分片**来的（OpenAI 那套）：同一个 `index` 的 `function.arguments`
      要**拼起来**，`id`/`name` 只在第一片里。⛔ 不拼就会得到半个 JSON 参数
      —— 那正是"归档偶发空回复"的另一个来源。
    """
    acc = dict(acc or {})
    if delta.get("content"):
        acc["content"] = (acc.get("content") or "") + str(delta["content"])
    if delta.get("reasoning_content"):
        acc["reasoning_content"] = (acc.get("reasoning_content") or "") + str(delta["reasoning_content"])
    if delta.get("role"):
        acc["role"] = delta["role"]
    for tc in (delta.get("tool_calls") or []):
        idx = int(tc.get("index") or 0)
        calls = acc.setdefault("tool_calls", [])
        while len(calls) <= idx:
            calls.append({"id": None, "type": "function",
                          "function": {"name": None, "arguments": ""}})
        slot = calls[idx]
        if tc.get("id"):
            slot["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += str(fn["arguments"])
    return acc


class BaseLLM:
    def __init__(self, cfg: ChatConfig, creds: Credentials):
        self.cfg = cfg
        self.url = creds.chat_url
        self.key = creds.chat_api_key
        # ★ 最近一次调用的 token 用量（服务端顶层 `usage`）。
        #   放在实例上而不是改 `chat()` 的返回形状：`chat()` 的返回值是**消息本身**，
        #   到处都在按 `msg["tool_calls"]` / `msg["content"]` 用它，动它会牵一片。
        #   一轮里可能调多次（工具往返）⇒ 由调用方逐次累加。
        self.last_usage: dict = {}
        # ★★ 2026-10-06：**逐次调用日志**（耗时 + token + 有没有带工具 + 输入多大）。
        #   为什么要它：查"这次为什么慢"时仓库里**没有任何计时**（`ts_start/ts_end`
        #   只覆盖到收场那一刻，归档整段在外）⇒ 当时只能在探针里**临时 monkey-patch**
        #   才查出来。那不该是一次性的手艺。由调用方用 `engine._drain(llm)` 取走并清空。
        self.call_log: list = []

    def _log_call(self, t0: float, msg: dict, tools, messages) -> None:
        u = self.last_usage or {}
        try:
            chars = sum(len(str(m.get("content") or "")) for m in messages)
        except Exception:  # noqa: BLE001
            chars = 0
        self.call_log.append({
            "ms": int((time.time() - t0) * 1000),
            "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"),
            "tools": bool(tools), "tool_calls": bool(msg.get("tool_calls")),
            "msgs": len(messages), "chars": chars,
        })

    def chat(self, messages: List[dict], tools: Optional[list] = None, temp: Optional[float] = None,
             json_mode: bool = False, model: Optional[str] = None, progress=None) -> dict:
        """一次调用，返回 `message` dict（可能带 `tool_calls`）。

        ★ `progress(kind, text)`（2026-10-07）：给了就**走流式**，边收边回调 ——
          `kind` ∈ {`reasoning`, `content`}（推理与正文分开给，界面上分两块显示）。
          ⛔ 给了 `progress` 只是"看得见"，**返回值与非流式完全同形**
          （调用方一行都不用改）；不给就还是老的非流式路径。
        """
        t0 = time.time()
        budgets = [self.cfg.max_tokens]
        if self.cfg.escalate:
            budgets += [self.cfg.max_tokens * 2, self.cfg.max_tokens * 4]
        last: dict = {}
        for mt in budgets:
            body = {"model": model or self.cfg.model, "messages": messages,
                    "temperature": self.cfg.temperature if temp is None else temp,
                    "max_tokens": mt, "effort": self.cfg.effort}
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            if progress is not None:
                body["stream"] = True
                # ★ 要了 `include_usage` 才拿得到 token 数 ⇒ 记账（timings）不会因为
                #   改成流式而变空（那份账本是"这次为什么慢"的唯一凭据）。
                body["stream_options"] = {"include_usage": True}
                msg = self._post_stream(body, progress)
            else:
                d = self._post(body)
                self.last_usage = d.get("usage") or {}
                msg = (d.get("choices") or [{}])[0].get("message") or {}
            if msg.get("content") or msg.get("tool_calls"):
                self._log_call(t0, msg, tools, messages)
                return msg
            last = msg
        self._log_call(t0, last, tools, messages)
        return last

    def _post_stream(self, body: dict, progress) -> dict:
        """流式 POST：边读边回调，最后把分片**拼回**一个 `message`。

        ⚠️ 用 `readline()` 逐行读（SSE 是行协议）；`stream_options.include_usage`
          那一块 `choices` 是空的，只有 `usage` ⇒ 单独收下来。
        """
        data = json.dumps(body).encode()
        rq = urllib.request.Request(self.url, data=data, method="POST")
        rq.add_header("Authorization", "Bearer " + self.key)
        rq.add_header("Content-Type", "application/json")
        rq.add_header("Accept", "text/event-stream")
        acc: dict = {}
        with urllib.request.urlopen(rq, timeout=self.cfg.timeout) as resp:
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    d = json.loads(payload)
                except Exception:  # noqa: BLE001
                    continue
                if d.get("usage"):
                    self.last_usage = d["usage"]
                ch = (d.get("choices") or [{}])[0]
                delta = ch.get("delta") or {}
                for kind, text in delta_pieces(delta):
                    try:
                        progress(kind, text)
                    except Exception:  # noqa: BLE001  ⛔ 显示不出来不许打断这一场
                        pass
                acc = merge_delta(acc, delta)
        return acc

    def _post(self, body: dict) -> dict:
        data = json.dumps(body).encode()
        last = None
        for attempt in range(self.cfg.retries):
            try:
                rq = urllib.request.Request(self.url, data=data, method="POST")
                rq.add_header("Authorization", "Bearer " + self.key)
                rq.add_header("Content-Type", "application/json")
                with urllib.request.urlopen(rq, timeout=self.cfg.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 500, 502, 503, 504) and attempt < self.cfg.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except Exception as e:  # noqa: BLE001  网络层
                last = e
                if attempt < self.cfg.retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        raise last  # pragma: no cover


def parse_json(txt: str) -> dict:
    """把模型给的文本抠成 JSON。**先宽容，再修**（归档失败 = 这一场的记忆整段丢掉）。

    实测两种失败形态：① 空 content；② 字符串里留了**裸换行/控制符**，或有尾逗号。
    """
    s = (txt or "").strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        s = s.split("\n", 1)[1] if s.lower().startswith("json") else s
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return json.loads(re.sub(r",\s*([}\]])", r"\1",
                                 re.sub(r"[\x00-\x1f]", " ", s)))


class ScriptedLLM:
    """**离线替身**：按脚本回话，只给自检/单测用（无网络、无凭证）。

    `script` 是一串 `message` dict；每次 `chat()` 按顺序吐一个，用尽后吐 `fallback`。
    """

    def __init__(self, script: Optional[List[dict]] = None, fallback: Optional[dict] = None):
        self.script = list(script or [])
        self.fallback = fallback or {"role": "assistant", "content": "（脚本用尽）"}
        self.calls: List[dict] = []
        self.call_log: list = []          # ★ 与 `BaseLLM` 同形（自检要能断言"两段都记了"）

    def chat(self, messages, tools=None, temp=None, json_mode=False, model=None,
             progress=None) -> dict:
        """⚠️ 签名必须与 [`BaseLLM.chat`] **一致**（含 `progress`）。

        ★ 2026-10-07 踩到过：给 `BaseLLM.chat` 加了 `progress` 却漏了这个替身 ⇒
          `dream` / `session` / `merging` 全部抛
          `TypeError: ScriptedLLM.chat() got an unexpected keyword argument 'progress'`，
          **十几个自检一起红**。替身是"离线的那条路"，它跟不上真身就是自检失灵。
        ★ 给了 `progress` 时**按流式的样子回放**（脚本里写 `reasoning_content` 就先报推理）
          ⇒ "回调真的被调到了"这件事在自检里看得见，不用真联网。
        """
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode,
                           "progress": progress is not None})
        self.call_log.append({"ms": 0, "prompt_tokens": None, "completion_tokens": None,
                              "tools": bool(tools), "tool_calls": False,
                              "msgs": len(messages), "chars": 0})
        msg = self.script.pop(0) if self.script else self.fallback
        if progress is not None and isinstance(msg, dict):
            try:
                if msg.get("reasoning_content"):
                    progress("reasoning", str(msg["reasoning_content"]))
                if msg.get("content"):
                    progress("content", str(msg["content"]))
            except Exception:  # noqa: BLE001  与真身同规矩：显示失败不打断裂路
                pass
        return msg
