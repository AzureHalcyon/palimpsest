"""OpenAI-compatible chat client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import List, Optional


class ChatError(RuntimeError):
    pass


class ChatClient:
    def __init__(self, config, credentials):
        self.config = config
        self.credentials = credentials

    def chat(self, messages: List[dict], tools: Optional[list] = None,
             temperature: Optional[float] = None, model: Optional[str] = None) -> dict:
        body = {
            "model": model or self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        data = json.dumps(body).encode("utf-8")
        last_error = None
        for attempt in range(self.config.retries):
            request = urllib.request.Request(self.credentials.chat_url, data=data, method="POST")
            request.add_header("Authorization", "Bearer " + self.credentials.api_key)
            request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return (payload.get("choices") or [{}])[0].get("message") or {}
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
