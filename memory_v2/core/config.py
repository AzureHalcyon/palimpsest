"""Credentials and chat settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


DEFAULT_CREDENTIALS = os.path.join("~", ".dsh", ".credentials.yaml")
DEFAULT_CHAT_URL = "https://api.deepseek.com/chat/completions"


class CredentialsError(RuntimeError):
    pass


@dataclass
class Credentials:
    api_key: str
    chat_url: str = DEFAULT_CHAT_URL
    embed_url: str = ""
    embed_key: str = ""

    @classmethod
    def load(cls, path: str = "") -> "Credentials":
        target = Path(os.path.expanduser(
            path or os.environ.get("MEMORY_V2_CREDENTIALS") or DEFAULT_CREDENTIALS))
        if not target.exists():
            raise CredentialsError("credentials not found: %s" % target)
        try:
            refs = (yaml.safe_load(target.read_text(encoding="utf-8")) or {}).get("refs") or {}
        except Exception as exc:
            raise CredentialsError("credentials unreadable: %s" % type(exc).__name__) from exc
        api_key = refs.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise CredentialsError("missing DEEPSEEK_API_KEY in %s" % target)
        return cls(api_key=str(api_key),
                   chat_url=str(refs.get("DEEPSEEK_CHAT_URL") or DEFAULT_CHAT_URL),
                   embed_url=str(refs.get("ALIYUN_MAAS_BASE_URL") or "").rstrip("/"),
                   embed_key=str(refs.get("ALIYUN_MAAS_API_KEY") or ""))


@dataclass
class ChatConfig:
    model: str = "deepseek-flash"
    temperature: float = 0.7
    max_tokens: int = 6000
    # 有的端点支持这个字段（low/medium/high）；空或 auto 就不发。
    reasoning_effort: str = ""
    timeout: float = 600.0
    retries: int = 3
    embed_model: str = "qwen3.7-text-embedding"
    embed_dim: int = 1024
    embed_batch: int = 20
    embed_timeout: float = 120.0
