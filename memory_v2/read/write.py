"""Compose a memory from reconstructed text."""

from __future__ import annotations

from ..core.prompts import writer_prompt


def write_memory(client, text: str) -> str:
    messages = [
        {"role": "system", "content": writer_prompt()},
        {"role": "user", "content": text},
    ]
    message = client.chat(messages, temperature=0.4)
    content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("writer returned empty")
    return content
