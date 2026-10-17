# -*- coding: utf-8 -*-
"""数据装载（**数据契约的落点**）—— 本仓库不带数据，数据从这些文件读进来。

支持三种写法（同一个装载器都吃）：

  `.jsonl`  一行一条：`{"text": "……"}`（缺 `text` 键则把整行当文本）
  `.json`   `[{"text": …}, …]` 或 `{"items": [...]}`（兼容 `{"situational": {"items": [...]}}`）
  `.txt` / `.md`   一行一条；自动剥掉行首的 `-` / `*` / `1.` `2)` 这类列表符号，
                   跳过空行与 `#` 开头的行

⇒ 这样"底色"可以直接用一份手写的 markdown 清单，"记忆"可以用导出的 jsonl，
   两者都不需要改格式。
"""
from __future__ import annotations

import json
import os
import re
from typing import List

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s*")


def load_texts(path: str) -> List[str]:
    if not path:
        return []
    if not os.path.exists(path):
        raise FileNotFoundError("数据文件不存在：%s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".jsonl":
        return _from_jsonl(path)
    if ext == ".json":
        return _from_json(path)
    return _from_text(path)


def _from_jsonl(path: str) -> List[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:  # noqa: BLE001
                out.append(line)
                continue
            if isinstance(obj, dict):
                t = obj.get("text") or obj.get("content") or obj.get("statement")
                if t:
                    out.append(str(t).strip())
            elif isinstance(obj, str):
                out.append(obj.strip())
    return [t for t in out if t]


def _from_json(path: str) -> List[str]:
    obj = json.loads(open(path, encoding="utf-8").read())
    items = obj
    if isinstance(obj, dict):
        if isinstance(obj.get("situational"), dict):
            items = obj["situational"].get("items") or []
        else:
            items = obj.get("items") or obj.get("data") or []
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append(it.strip())
        elif isinstance(it, dict):
            t = it.get("text") or it.get("content") or it.get("statement")
            if t:
                out.append(str(t).strip())
    return [t for t in out if t]


def _from_text(path: str) -> List[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            # 跳过：空行 · `#` 标题 · `>` 引用块 · `//` 注释（后三者是说明，不是条目）
            if not s or s[0] in "#>" or s.startswith("//"):
                continue
            s = _BULLET.sub("", s).strip()
            if s:
                out.append(s)
    return out


def load_layers(baseline: str = "", situational: str = "", memories: str = ""):
    """按三层装载。返回 `(底色, 情境, 记忆)` 三个字符串列表。"""
    return load_texts(baseline), load_texts(situational), load_texts(memories)
