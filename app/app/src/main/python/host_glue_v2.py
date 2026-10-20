# -*- coding: utf-8 -*-
"""Android 宿主胶水：把 **v1 界面**接到 `memory_v2` 内核。

这一层只做三件事：
  1. 把 v1 界面用惯的 JSON 形状（轮次 / 件 / 参数账 / 旧场）翻译成 v2 的实体；
  2. 会话句柄、凭证、配置落盘放在这里（Kotlin 侧不碰业务对象）；
  3. 任何异常都不许穿过 Chaquopy 桥 —— 一律 `{"ok": false, "err": ...}`。

⛔ 这一层不是第二套内核。真正的记忆逻辑全在 `memory_v2/`。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

import yaml

_CHATS: Dict[str, object] = {}
_REVIEW: Dict[str, dict] = {}
_LOCK = threading.RLock()

# --------------------------------------------------------------------------- 小工具


def _ok(**fields) -> str:
    payload = {"ok": True}
    payload.update(fields)
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str, code: str = "") -> str:
    payload = {"ok": False, "err": str(message)}
    if code:
        payload["code"] = code
    return json.dumps(payload, ensure_ascii=False)


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _data_dir(path: str) -> str:
    path = os.path.abspath(str(path or ""))
    os.makedirs(path, exist_ok=True)
    return path


def _cache_dir(data_dir: str) -> str:
    path = os.path.join(_data_dir(data_dir), "cache")
    os.makedirs(path, exist_ok=True)
    return path


def _cred_path(data_dir: str) -> str:
    return (os.environ.get("COMPANION_CRED_PATH")
            or os.path.join(_data_dir(data_dir), "credentials.yaml"))


def _read_yaml(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _refs(path: str) -> dict:
    refs = _read_yaml(path).get("refs") or {}
    return refs if isinstance(refs, dict) else {}


def _write_yaml(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _key_hint(value: str) -> str:
    value = str(value or "")
    if len(value) <= 8:
        return "已配置" if value else ""
    return value[:5] + "…" + value[-4:]


# --------------------------------------------------------------------------- 凭证 / 路径


def set_paths(data_dir: str, cred_path: str = "") -> str:
    data_dir = _data_dir(data_dir)
    cred = str(cred_path) if cred_path else os.path.join(data_dir, "credentials.yaml")
    os.environ["COMPANION_CRED_PATH"] = cred
    os.environ["MEMORY_V2_DATA"] = data_dir
    return cred


def python_info() -> Dict[str, str]:
    import platform
    import sys
    return {"python": sys.version.split()[0],
            "impl": platform.python_implementation(),
            "machine": platform.machine()}


def python_info_str() -> str:
    info = python_info()
    return "%s %s @ %s" % (info["impl"], info["python"], info["machine"])


def has_cred(data_dir: str) -> bool:
    return creds_ok(data_dir)


def creds_ok(data_dir: str) -> bool:
    try:
        from memory_v2.core.config import Credentials
        Credentials.load(_cred_path(data_dir))
        return True
    except Exception:
        return False


def creds_state_json(data_dir: str) -> str:
    path = _cred_path(data_dir)
    refs = _refs(path)
    chat_key = str(refs.get("DEEPSEEK_API_KEY") or "")
    embed_key = str(refs.get("ALIYUN_MAAS_API_KEY") or "")
    return _ok(
        configured=bool(chat_key),
        path=path,
        embed_base_url=str(refs.get("ALIYUN_MAAS_BASE_URL") or ""),
        chat_url=str(refs.get("DEEPSEEK_CHAT_URL") or
                     "https://api.deepseek.com/chat/completions"),
        has_embed_key=bool(embed_key),
        has_chat_key=bool(chat_key),
        embed_key_hint=_key_hint(embed_key),
        chat_key_hint=_key_hint(chat_key),
        err="" if chat_key else "还没有配 DEEPSEEK_API_KEY",
    )


def creds_save(data_dir: str, embed_base_url: str, embed_api_key: str,
               chat_api_key: str, chat_url: str) -> str:
    """空字符串 = 这一项不动。保留文件里原有的其它字段（DSH 的 records 等）。"""
    try:
        path = _cred_path(data_dir)
        data = _read_yaml(path)
        refs = data.get("refs") or {}
        if not isinstance(refs, dict):
            refs = {}
        for name, value in (
            ("ALIYUN_MAAS_BASE_URL", embed_base_url),
            ("ALIYUN_MAAS_API_KEY", embed_api_key),
            ("DEEPSEEK_API_KEY", chat_api_key),
            ("DEEPSEEK_CHAT_URL", chat_url),
        ):
            value = str(value or "").strip()
            if value:
                refs[name] = value
        if not refs.get("DEEPSEEK_CHAT_URL"):
            refs["DEEPSEEK_CHAT_URL"] = "https://api.deepseek.com/chat/completions"
        data["refs"] = refs
        _write_yaml(path, data)
        return creds_state_json(data_dir)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "credentials")


# --------------------------------------------------------------------------- 配置
# v1 界面用 `chat.model` / `chat.temperature` 这种点分路径；v2 的 ChatConfig
# 是 dataclass 字段名。这里做一层薄映射，配置文件是 `<data>/config.json`。

_FIELDS = {
    "chat.model": "model",
    "chat.temperature": "temperature",
    "chat.max_tokens": "max_tokens",
    "chat.timeout": "timeout",
    "chat.retries": "retries",
    "embed.model": "embed_model",
    "embed.dim": "embed_dim",
    "embed.batch": "embed_batch",
    "embed.timeout": "embed_timeout",
}


def _config(data_dir: str):
    from memory_v2.core.config import ChatConfig
    cfg = ChatConfig()
    path = os.path.join(_data_dir(data_dir), "config.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle) or {}
        except Exception:
            data = {}
        for key, value in data.items():
            field = _FIELDS.get(key, key)
            if hasattr(cfg, field):
                try:
                    setattr(cfg, field, value)
                except Exception:
                    pass
    return cfg


def _save_config(data_dir: str, updates: dict) -> dict:
    path = os.path.join(_data_dir(data_dir), "config.json")
    current = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                current = json.load(handle) or {}
        except Exception:
            current = {}
    for key, value in updates.items():
        field = _FIELDS.get(key, key)
        if field in ("model", "embed_model"):
            value = str(value or "")
        elif field in ("max_tokens", "retries", "embed_dim", "embed_batch"):
            value = _as_int(value, 0)
        else:
            value = _as_float(value, 0.0)
        current[field] = value
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(current, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return current


def _knob_defs(data_dir: str):
    cfg = _config(data_dir)
    return [
        {"path": "chat.model", "field": "model", "label": "对话模型", "kind": "str",
         "grade": "【实测】", "note": "v2 的 ChatClient 直接用它。",
         "unit": "", "low": None, "high": None, "choices": []},
        {"path": "chat.temperature", "field": "temperature", "label": "温度", "kind": "float",
         "grade": "【实测】", "note": "越低越稳；v2 默认 0.7。",
         "unit": "", "low": 0.0, "high": 2.0, "choices": []},
        {"path": "chat.max_tokens", "field": "max_tokens", "label": "回复上限", "kind": "int",
         "grade": "【实测】", "note": "单次回复的 token 上限。",
         "unit": "token", "low": 256.0, "high": 32768.0, "choices": []},
        {"path": "chat.timeout", "field": "timeout", "label": "请求超时", "kind": "float",
         "grade": "【实测】", "note": "一次模型请求最多等多久。",
         "unit": "秒", "low": 30.0, "high": 1800.0, "choices": []},
        {"path": "chat.retries", "field": "retries", "label": "重试次数", "kind": "int",
         "grade": "【实测】", "note": "429/5xx 时的重试次数。",
         "unit": "次", "low": 1.0, "high": 8.0, "choices": []},
        {"path": "embed.model", "field": "embed_model", "label": "嵌入模型", "kind": "str",
         "grade": "【实测】", "note": "v2 固定 1024 维；换模型要同时改 dim。",
         "unit": "", "low": None, "high": None, "choices": []},
        {"path": "embed.dim", "field": "embed_dim", "label": "嵌入维度", "kind": "int",
         "grade": "【实测】", "note": "v2 的冻结向量按这个维度校验。",
         "unit": "维", "low": 256.0, "high": 4096.0, "choices": []},
        {"path": "embed.batch", "field": "embed_batch", "label": "嵌入批大小", "kind": "int",
         "grade": "【实测】", "note": "一次请求送几条文本。",
         "unit": "条", "low": 1.0, "high": 128.0, "choices": []},
        {"path": "embed.timeout", "field": "embed_timeout", "label": "嵌入超时", "kind": "float",
         "grade": "【实测】", "note": "嵌入请求的超时。",
         "unit": "秒", "low": 10.0, "high": 600.0, "choices": []},
    ]


def knobs_json(data_dir: str) -> str:
    try:
        cfg = _config(data_dir)
        path = os.path.join(_data_dir(data_dir), "config.json")
        changed = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    changed = json.load(handle) or {}
            except Exception:
                changed = {}
        rows = []
        for one in _knob_defs(data_dir):
            field = one["field"]
            value = getattr(cfg, field, "")
            rows.append({
                "path": one["path"], "label": one["label"], "kind": one["kind"],
                "grade": one["grade"], "note": one["note"], "unit": one["unit"],
                "low": one["low"], "high": one["high"], "choices": one["choices"],
                "value": value, "changed": field in changed,
            })
        return _ok(knobs=rows)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "knobs")


def settings_save(data_dir: str, overrides_json: str) -> str:
    try:
        updates = json.loads(overrides_json or "{}")
        if not isinstance(updates, dict):
            raise ValueError("settings 不是对象")
        _save_config(data_dir, updates)
        cfg = _config(data_dir)
        effective = {one["path"]: getattr(cfg, one["field"], "")
                     for one in _knob_defs(data_dir)}
        return _ok(effective=effective)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "settings")


# --------------------------------------------------------------------------- 提示词
# v2 的提示词是 `memory_v2/prompts/*.txt`；用户在 App 里改的覆盖放
# `<data>/prompts.json`。⛔ 不改仓库里的源文件（升级时不会被覆盖）。

_PROMPT_FILES = ("persona.txt", "event-system.txt", "sys2.txt", "date.txt",
                 "write-memory.txt")
_PROMPT_META = {
    "persona": ("系统① · 基本设定", "开场常驻：它是谁、谁做的记忆、工具从哪来。", "persona.txt"),
    "event-system": ("系统② · 每轮 recall / event 规则",
                     "每轮先 recall，最后 event；改坏会导致这一轮记不下来。", "event-system.txt"),
    "sys2": ("系统③ · 分寸", "会话的补充口径：不派活、不附和、不套话。", "sys2.txt"),
    "date": ("系统④ · 日期补充", "每条 system 里的“今天是 $today”那一段。", "date.txt"),
    "write-memory": ("记忆编写", "`remember` 取回原文时，用来压成一段回忆。", "write-memory.txt"),
}


def _prompt_defaults() -> dict:
    from memory_v2.core.prompts import _read
    out = {}
    for item, (_name, _desc, filename) in _PROMPT_META.items():
        try:
            out[item] = _read(filename)
        except Exception:
            out[item] = ""
    return out


def _prompt_overrides(data_dir: str) -> dict:
    path = os.path.join(_data_dir(data_dir), "prompts.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_prompt_overrides(data_dir: str, data: dict) -> None:
    path = os.path.join(_data_dir(data_dir), "prompts.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _system_messages(data_dir: str) -> List[dict]:
    from memory_v2.core.prompts import system_messages
    messages = system_messages()
    over = _prompt_overrides(data_dir)
    if over.get("persona"):
        messages[0]["content"] = str(over["persona"])
    if over.get("event-system"):
        messages[1]["content"] = str(over["event-system"])
    if over.get("sys2"):
        messages[2]["content"] = str(over["sys2"])
    if over.get("date"):
        messages[3]["content"] = str(over["date"]).replace(
            "$today", time.strftime("%Y-%m-%d"))
    return messages


def prompt_json(data_dir: str) -> str:
    try:
        defaults = _prompt_defaults()
        over = _prompt_overrides(data_dir)
        sys1 = str(over.get("persona") or defaults.get("persona") or "")
        sys2 = str(over.get("sys2") or defaults.get("sys2") or "")
        return _ok(sys1=sys1, sys2=sys2,
                   default_sys1=defaults.get("persona", ""),
                   default_sys2=defaults.get("sys2", ""),
                   edited=bool(over.get("persona") or over.get("sys2")))
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "prompt")


def prompt_save(data_dir: str, sys1: str, sys2: str) -> str:
    try:
        defaults = _prompt_defaults()
        over = _prompt_overrides(data_dir)
        if not str(sys1 or "").strip() and not str(sys2 or "").strip():
            over.pop("persona", None)
            over.pop("sys2", None)
        else:
            if str(sys1 or "").strip() and str(sys1) != defaults.get("persona"):
                over["persona"] = str(sys1)
            if str(sys2 or "").strip() and str(sys2) != defaults.get("sys2"):
                over["sys2"] = str(sys2)
        _save_prompt_overrides(data_dir, over)
        return prompt_json(data_dir)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "prompt")


def prompt_list_json(data_dir: str) -> str:
    try:
        defaults = _prompt_defaults()
        over = _prompt_overrides(data_dir)
        groups = []
        for group, items in (("系统提示词", ("persona", "event-system", "sys2", "date")),
                             ("记忆", ("write-memory",))):
            rows = []
            for item in items:
                name, desc, _filename = _PROMPT_META[item]
                body = str(over.get(item) or defaults.get(item) or "")
                rows.append({"id": item, "名称": name, "占位符": [],
                             "说明": desc, "已改": bool(over.get(item)),
                             "字数": len(body)})
            groups.append({"组": group, "项": rows})
        return _ok(组们=groups)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "prompt")


def prompt_get_json(data_dir: str, prompt_id: str) -> str:
    try:
        if prompt_id not in _PROMPT_META:
            return _err("没有这一项：%s" % prompt_id)
        defaults = _prompt_defaults()
        over = _prompt_overrides(data_dir)
        name, desc, _filename = _PROMPT_META[prompt_id]
        return _ok(id=prompt_id, 名称=name, 占位符=[], 说明=desc,
                   已改=bool(over.get(prompt_id)),
                   正文=str(over.get(prompt_id) or defaults.get(prompt_id) or ""),
                   默认=str(defaults.get(prompt_id) or ""))
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "prompt")


def prompt_save_one(data_dir: str, prompt_id: str, body: str) -> str:
    try:
        if prompt_id not in _PROMPT_META:
            return _err("没有这一项：%s" % prompt_id)
        defaults = _prompt_defaults()
        over = _prompt_overrides(data_dir)
        if str(body or "") == str(defaults.get(prompt_id) or ""):
            over.pop(prompt_id, None)
        else:
            over[prompt_id] = str(body or "")
        _save_prompt_overrides(data_dir, over)
        return _ok(id=prompt_id)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "prompt")


def prompt_reset_one(data_dir: str, prompt_id: str) -> str:
    try:
        over = _prompt_overrides(data_dir)
        over.pop(prompt_id, None)
        _save_prompt_overrides(data_dir, over)
        return _ok(id=prompt_id)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "prompt")


# --------------------------------------------------------------------------- 参数账


def ledger_json(data_dir: str) -> str:
    try:
        cfg = _config(data_dir)
        path = os.path.join(_data_dir(data_dir), "config.json")
        changed = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    changed = json.load(handle) or {}
            except Exception:
                changed = {}
        rows = []
        for one in _knob_defs(data_dir):
            field = one["field"]
            value = getattr(cfg, field, "")
            rows.append({
                "键": one["path"], "组": one["path"].split(".")[0],
                "组名": "对话" if one["path"].startswith("chat.") else "嵌入",
                "名": one["label"], "值": value,
                "类型": one["kind"], "依据": one["grade"],
                "说明": one["note"], "无读者": "",
                "可改": True, "可填": one["choices"],
                "提示": "直接填；v2 会在下一次请求时生效。",
            })
        return _ok(项=rows, 计数=len(rows), 改动=bool(changed))
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "ledger")


def ledger_set(data_dir: str, key: str, value: str) -> str:
    try:
        if key not in _FIELDS:
            return _err("v2 没有这个参数：%s" % key)
        _save_config(data_dir, {key: value})
        path = os.path.join(_data_dir(data_dir), "参数账改动.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "时间": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "键": key, "旧": "", "新": value,
            }, ensure_ascii=False) + "\n")
        cfg = _config(data_dir)
        return _ok(键=key, 值=getattr(cfg, _FIELDS[key], value))
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "ledger")


def ledger_changes(data_dir: str, n: str = "200") -> str:
    try:
        path = os.path.join(_data_dir(data_dir), "参数账改动.jsonl")
        rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()[-_as_int(n, 200):]
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        return _ok(记录=rows)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "ledger")


def ledger_lines(data_dir: str = "") -> List[str]:
    try:
        data = json.loads(ledger_json(data_dir))
    except Exception:
        return ["参数账读不出来"]
    if not data.get("ok"):
        return [str(data.get("err") or "参数账读不出来")]
    out = ["v2 参数账：%s 项" % data.get("计数", 0), ""]
    for item in data.get("项") or []:
        out.append("%s = %s  %s" % (item.get("键"), item.get("值"), item.get("依据") or ""))
        if item.get("说明"):
            out.append("    " + str(item["说明"]))
    return out


# --------------------------------------------------------------------------- 图 / 件 / 记忆页


def _turn_cache(data_dir: str):
    cache: Dict[str, Dict[int, dict]] = {}

    def get(session: str) -> Dict[int, dict]:
        if session not in cache:
            from memory_v2.store.session import SessionStore
            cache[session] = {int(t.get("turn") or 0): t
                              for t in SessionStore(data_dir, session).turns()}
        return cache[session]

    return get


def _turn_text(data_dir: str, get_turns, session: str, number: int) -> str:
    turn = get_turns(session).get(int(number)) or {}
    user = str(turn.get("user") or "")
    ai = str(turn.get("assistant") or "")
    return ("他：%s\n我：%s" % (user, ai)).strip()


def graph_view_json(data_dir: str) -> str:
    try:
        from memory_v2.store.graph import Graph
        data_dir = _data_dir(data_dir)
        graph = Graph.open(data_dir)
        get_turns = _turn_cache(data_dir)
        pieces = []
        cells = 0
        for event_id, event in graph.events.items():
            members = []
            for segment in event.get("segments") or []:
                session = str(segment.get("session") or "")
                start = _as_int(segment.get("start"), 0)
                end = _as_int(segment.get("end"), start)
                for number in range(start, end + 1):
                    text = _turn_text(data_dir, get_turns, session, number)
                    if not text:
                        continue
                    members.append({
                        "场id": session, "轮": number, "文本": text,
                        "点亮": number == start,
                    })
                    cells += 1
            strength = max(_as_float(event.get("consolidation")),
                           _as_float(event.get("access")))
            pieces.append({
                "件id": event_id,
                "强度": round(strength, 3),
                "在标记集": bool(event.get("segments")),
                "成员": members,
                "代表": (members[0]["文本"][:80] if members else ""),
            })
        pieces.sort(key=lambda item: (-item["强度"], item["件id"]))

        # 组：v2 没有 v1 那种“依赖簇”缓存。⛔ 不伪造；这一页先留空。
        groups: List[dict] = []

        # 势：v2 没有单独的势文件；把 access / consolidation 当成只读读数展示。
        forces = []
        for event_id, event in graph.events.items():
            access = _as_float(event.get("access"))
            consolidation = _as_float(event.get("consolidation"))
            if access <= 0 and consolidation <= 0:
                continue
            forces.append({
                "单元": event_id,
                "标签": "件 · " + str(event.get("name") or ""),
                "文本": str(event.get("name") or ""),
                "势": round(access, 3),
                "唤醒": round(consolidation, 3),
            })
        forces.sort(key=lambda item: (-item["势"], item["单元"]))

        sessions = []
        directory = os.path.join(data_dir, "状态")
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                if not name.endswith(".jsonl") or name.endswith(".messages.json"):
                    continue
                path = os.path.join(directory, name)
                sessions.append({
                    "sid": name[:-6],
                    "archived": os.path.exists(path + ".archived"),
                    "size": os.path.getsize(path),
                })
        sessions.sort(key=lambda item: item["sid"], reverse=True)

        return _ok(
            版本=1,
            统计={"cell": cells, "件": len(pieces), "组": len(groups),
                  "标记集": len(pieces), "势": len(forces), "待观察池": 0},
            件=pieces, 组=groups, 势=forces[:30], 待观察池=[],
            会话=sessions,
        )
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "graph")


def graph_action_json(data_dir: str, piece_id: str, action: str) -> str:
    try:
        from memory_v2.store.graph import Graph
        data_dir = _data_dir(data_dir)
        graph = Graph.open(data_dir)
        piece_id = str(piece_id or "")
        if piece_id not in graph.events:
            return _err("没有这个件：%s" % piece_id)
        if action == "删":
            graph.remove_event(piece_id)
            graph.save()
        elif action == "剪出":
            return _err("v2 没有“标记集”，不能剪出；可以删掉这个件，或留着不动。")
        else:
            return _err("不认识的动件：%s" % action)
        with open(os.path.join(data_dir, "图编辑.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "件id": piece_id, "动作": action,
                "时间": time.time(),
            }, ensure_ascii=False) + "\n")
        return _ok(件id=piece_id, 动作=action)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "graph")


# --------------------------------------------------------------------------- 会话


def _active_session(data_dir: str) -> str:
    directory = os.path.join(data_dir, "状态")
    if not os.path.isdir(directory):
        return ""
    candidates = []
    for name in os.listdir(directory):
        if not name.endswith(".jsonl") or name.endswith(".messages.json"):
            continue
        path = os.path.join(directory, name)
        if os.path.exists(path + ".archived"):
            continue
        candidates.append((os.path.getmtime(path), name[:-6]))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


def _session_key(session_id: str) -> int:
    match = re.search(r"-(\d+)$", str(session_id or ""))
    return int(match.group(1)) if match else 1


def _ensure_global(data_dir: str) -> None:
    """新场开始前，如果还没有全局冻结副本，就把当前 live 图冻结一份。"""
    from memory_v2.store.frozen import global_dir, update_global
    target = os.path.join(global_dir(data_dir), "graph.json")
    live = os.path.join(data_dir, "graph.json")
    if not os.path.exists(target) and os.path.exists(live):
        update_global(data_dir)


def _open(data_dir: str, cred_path: str, session_id: str = ""):
    from memory_v2.chat.loop import EventChat
    from memory_v2.core.embed import Embedder
    from memory_v2.core.llm import ChatClient
    from memory_v2.store.graph import Graph
    from memory_v2.store.session import SessionStore, next_session_id

    data_dir = _data_dir(data_dir)
    from memory_v2.core.config import Credentials
    credentials = Credentials.load(cred_path or _cred_path(data_dir))
    cfg = _config(data_dir)
    session_id = str(session_id or "").strip() or _active_session(data_dir) or next_session_id(data_dir)
    _ensure_global(data_dir)
    store = SessionStore(data_dir, session_id)
    if store.is_archived():
        raise RuntimeError("这一场已归档，只读：%s" % session_id)
    client = ChatClient(cfg, credentials)
    try:
        embedder = Embedder(cfg, credentials)
    except Exception:
        embedder = None
    chat = EventChat(
        client, store, Graph.open(data_dir), data_dir, session_id,
        _system_messages(data_dir), embedder=embedder,
        echo=lambda _text: None,
    )
    chat.config = cfg
    return chat


def _chat_for(data_dir: str, sid: str):
    sid = str(sid or "")
    chat = _CHATS.get(sid)
    if chat is not None:
        return chat
    chat = _open(data_dir, _cred_path(data_dir), sid)
    _CHATS[chat.session_id] = chat
    return chat


def _graph_counts(data_dir: str) -> dict:
    from memory_v2.store.graph import Graph
    graph = Graph.open(_data_dir(data_dir))
    sessions = 0
    directory = os.path.join(_data_dir(data_dir), "状态")
    if os.path.isdir(directory):
        sessions = sum(1 for name in os.listdir(directory)
                       if name.endswith(".jsonl") and not name.endswith(".messages.json")
                       and os.path.exists(os.path.join(directory, name + ".archived")))
    return {"件": len(graph.events),
            "标记": sum(1 for event in graph.events.values()
                       if event.get("segments")),
            "场": sessions}


def state_summary_lines(data_dir: str) -> List[str]:
    try:
        counts = _graph_counts(data_dir)
    except Exception as exc:  # noqa: BLE001
        return ["读状态失败：%s: %s" % (type(exc).__name__, exc)]
    return [
        "记忆：%s" % ("已有图" if counts["件"] else "还是空的（还没归档过一场）"),
        "件　　　：%s 个" % counts["件"],
        "有原文的件：%s 个" % counts["标记"],
        "已归档场次：%s" % counts["场"],
        "内核　　：memory_v2",
    ]


def state_summary(data_dir: str) -> Dict[str, object]:
    return {"inited": True, "clock": 0.0, "sessions": 0,
            "graph": _graph_counts(data_dir)}


def pending_lines(data_dir: str) -> List[str]:
    sid = _active_session(data_dir)
    if not sid:
        return ["没有在途的场次"]
    from memory_v2.store.session import SessionStore
    store = SessionStore(data_dir, sid)
    return ["在途场次：%s · %d 轮" % (sid, len(store.turns())),
            "v2 每一轮都落盘；进程被杀后重开会接回来"]


def resume_pending(data_dir: str) -> List[str]:
    return ["v2 没有“归档跑到一半”的在途状态；未归档的场会在下次打开时接回来"]


def chat_open(data_dir: str) -> str:
    try:
        if not os.path.exists(_cred_path(data_dir)):
            return _err("还没有配置凭证 —— 去「设置」里填", "credentials")
        with _LOCK:
            chat = _open(data_dir, _cred_path(data_dir))
            _CHATS[chat.session_id] = chat
            turns = chat.store.turns()
            counts = _graph_counts(data_dir)
            last = turns[-1] if turns else {}
            review = None
            review_state = _load_review(data_dir, chat.session_id)
            if review_state:
                review = {
                    "draft": _build_draft(chat, review_state),
                    "said": [str(item) for item in (review_state.get("said") or [])],
                    "steps": [],
                    "k": _session_key(chat.session_id),
                }
            return _ok(
                sid=chat.session_id,
                k=_session_key(chat.session_id),
                resumed=bool(turns),
                turns=len(turns),
                件=counts.get("件", 0),
                标记=counts.get("标记", 0),
                底片=counts.get("场", 0),
                clock=0.0,
                archived_pending=False,
                review=review,
                event=str(last.get("event") or ""),
                event_id=str(last.get("event_id") or ""),
            )
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "open")


def _last_turn(chat) -> dict:
    turns = chat.store.turns()
    return turns[-1] if turns else {}


def _push_from_turn(turn: dict) -> List[str]:
    out = []
    for recall in turn.get("recall") or []:
        for item in recall.get("candidates") or []:
            if item.get("current"):
                continue
            text = "%s %s" % (item.get("id") or "", item.get("name") or "")
            if text.strip() and text not in out:
                out.append(text.strip())
    return out


def _pulls_from_turn(turn: dict) -> List[dict]:
    out = []
    for recall in turn.get("recall") or []:
        keywords = " ".join(str(item) for item in (recall.get("keywords") or []))
        got = [str(item.get("id") or "") for item in (recall.get("candidates") or [])]
        out.append({"query": "recall " + keywords, "got": [g for g in got if g],
                    "回": "查记忆", "kind": "回忆"})
    for call in turn.get("calls") or []:
        event_id = str(call.get("id") or "")
        if not event_id:
            continue
        name = str(call.get("name") or "")
        is_new = bool(call.get("new"))
        links = [str(item) for item in
                 (call.get("link_names") or call.get("links") or []) if str(item)]
        query = ("新节点 " if is_new else "挂 ") + name
        got = [" ".join(item for item in (event_id, name) if item)]
        if links:
            got.append("连 " + "、".join(links))
        out.append({"query": query, "got": [g for g in got if g],
                    "回": "新节点" if is_new else "挂已有", "kind": "事件"})
    for item in turn.get("remember") or []:
        target = str(item.get("id") or "")
        if target:
            out.append({"query": "remember " + target, "got": [target],
                        "回": "取回原文", "kind": "回忆"})
    return out


def _turn_json(turn: dict) -> dict:
    return {
        "user": str(turn.get("user") or ""),
        "ai": str(turn.get("assistant") or ""),
        "回忆": None,
        "reasoning": str(turn.get("reasoning") or ""),
        "push": _push_from_turn(turn),
        "push_n": len(_push_from_turn(turn)),
        "件数": len(turn.get("events") or []),
        "usage": {str(k): _as_int(v) for k, v in (turn.get("usage") or {}).items()},
        "pulls": _pulls_from_turn(turn),
        "events": turn.get("events") or [],
    }


def chat_turns_json(data_dir: str, sid: str) -> str:
    try:
        with _LOCK:
            chat = _chat_for(data_dir, sid)
            turns = [_turn_json(turn) for turn in chat.store.turns()]
            return _ok(k=_session_key(chat.session_id), turns=turns)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "turns")


def _sink_call(sink, kind: str, text: str) -> None:
    if sink is None:
        return
    try:
        sink.onDelta(str(kind), str(text))
    except Exception:  # noqa: BLE001
        pass


def send(data_dir: str, sid: str, text: str, sink=None) -> str:
    try:
        with _LOCK:
            chat = _chat_for(data_dir, sid)
            _sink_call(sink, "phase", "等模型")
            on_delta = (lambda kind, text: _sink_call(sink, kind, text)) if sink else None
            reply = chat.send(str(text), on_delta=on_delta)
            turn = _last_turn(chat)
            reasoning = str(turn.get("reasoning") or "")
            return _ok(
                reply=reply,
                push=_push_from_turn(turn),
                k=len(turn.get("events") or []),
                cd_blocked=0,
                pulls=_pulls_from_turn(turn),
                reasoning=reasoning,
                回忆=None,
                usage={str(k): _as_int(v) for k, v in (turn.get("usage") or {}).items()},
                event=str(turn.get("event") or ""),
                event_id=str(turn.get("event_id") or ""),
                mode=str(turn.get("mode") or ""),
                events=turn.get("events") or [],
            )
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _CHATS.pop(str(sid), None)
        return _err("%s: %s" % (type(exc).__name__, exc), "send")


def chat_ask(data_dir: str, sid: str, text: str, sink=None) -> str:
    return send(data_dir, sid, text, sink)


def chat_remember(data_dir: str, sid: str, why: str = "") -> str:
    return _err("v2 里每一次 event 本身就是记忆；不需要再手工标记一轮。")


def _rewrite_turns(store, turns: List[dict]) -> None:
    os.makedirs(store.dir, exist_ok=True)
    tmp = store.path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for turn in turns:
            handle.write(json.dumps(turn, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, store.path)


def chat_withdraw(data_dir: str, sid: str) -> str:
    """撤回最后一轮：会话记录、messages、图里的本轮 segment 一起退。"""
    try:
        with _LOCK:
            chat = _chat_for(data_dir, sid)
            turns = chat.store.turns()
            if not turns:
                return _err("没有可撤的轮次")
            last = turns[-1]
            if _as_int(last.get("turn")) != _as_int(chat.turn):
                return _err("只能撤最近一轮")
            messages = chat.store.load_messages() or []
            cut = -1
            for index in range(len(messages) - 1, -1, -1):
                message = messages[index]
                if (message.get("role") == "user"
                        and str(message.get("content") or "") == str(last.get("user") or "")):
                    cut = index
                    break
            if cut < 0:
                return _err("messages 里找不到这一轮，撤不了")
            messages = messages[:cut]

            from memory_v2.store.graph import Graph
            graph = Graph.open(data_dir)
            for call in last.get("calls") or []:
                event_id = str(call.get("id") or "")
                event = graph.events.get(event_id)
                if not event:
                    continue
                kept = []
                for segment in event.get("segments") or []:
                    if str(segment.get("session") or "") != chat.session_id:
                        kept.append(segment)
                        continue
                    start = _as_int(segment.get("start"))
                    end = _as_int(segment.get("end"), start)
                    number = _as_int(last.get("turn"))
                    if number < start or number > end:
                        kept.append(segment)
                    elif start == end:
                        continue
                    elif number == end:
                        segment["end"] = end - 1
                        kept.append(segment)
                    elif number == start:
                        segment["start"] = start + 1
                        kept.append(segment)
                    else:
                        kept.append({"session": segment.get("session"),
                                     "start": start, "end": number - 1})
                        kept.append({"session": segment.get("session"),
                                     "start": number + 1, "end": end})
                event["segments"] = kept
                if not kept:
                    graph.remove_event(event_id)
            graph.save()

            _rewrite_turns(chat.store, turns[:-1])
            chat.store.save_messages(messages)
            chat.messages = messages
            chat.turn = max(0, _as_int(last.get("turn")) - 1)
            return _ok(user=str(last.get("user") or ""),
                       ai=str(last.get("assistant") or ""),
                       turns_left=len(turns) - 1)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "withdraw")


# --------------------------------------------------------------------------- 归档


def _review_path(data_dir: str, sid: str) -> str:
    return os.path.join(_data_dir(data_dir), "状态", str(sid) + ".review.json")


def _archive_record_path(data_dir: str, sid: str) -> str:
    return os.path.join(_data_dir(data_dir), "状态", str(sid) + ".archive.json")


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _load_review(data_dir: str, sid: str):
    state = _REVIEW.get(str(sid))
    if state is not None:
        return state
    state = _read_json(_review_path(data_dir, sid))
    if isinstance(state, dict):
        _REVIEW[str(sid)] = state
        return state
    return None


def _save_review(data_dir: str, sid: str, state: dict) -> None:
    _REVIEW[str(sid)] = state
    _write_json(_review_path(data_dir, sid), state)


def _drop_review(data_dir: str, sid: str) -> None:
    _REVIEW.pop(str(sid), None)
    try:
        os.remove(_review_path(data_dir, sid))
    except OSError:
        pass


def _build_draft(chat, state: dict) -> dict:
    turns = chat.store.turns()
    drop_turns = {_as_int(number) for number in (state.get("drop_turns") or [])}
    drop_events = {str(item) for item in (state.get("drop_events") or [])}
    items = []
    names = []
    for turn in turns:
        number = _as_int(turn.get("turn"))
        calls = [call for call in (turn.get("calls") or []) if call.get("id")]
        turn_names = []
        for call in calls:
            label = str(call.get("name") or "")
            if label and label not in turn_names:
                turn_names.append(label)
        if not turn_names and turn.get("event"):
            turn_names = [str(turn.get("event"))]
        ids = [str(call.get("id") or "") for call in calls] or [
            str(turn.get("event_id") or "")]
        skipped = number in drop_turns or any(item in drop_events for item in ids)
        for label in turn_names:
            if label and label not in names:
                names.append(label)
        label = "、".join(turn_names)
        items.append({
            "轮": number,
            "归进": label,
            "新建": any(bool(call.get("new")) for call in calls),
            "说法": ("这一轮记在「%s」" % label) if label else "这一轮没有事件",
            "跳过": skipped,
        })
    summary = "这一场 %d 轮，涉及 %d 件事：%s。" % (
        len(turns), len(names), "、".join(names) if names else "暂无")
    if drop_turns:
        summary += " 已跳过第 %s 轮。" % "、".join(str(n) for n in sorted(drop_turns))
    said = [str(item) for item in (state.get("said") or []) if str(item).strip()]
    if said:
        summary += " 你的建议已记下：%s。" % " / ".join(said[-3:])
    if state.get("_warn"):
        summary += " 这条建议我还不能自动执行；目前只能按轮跳过 / 恢复。"
    return {
        "形状": "v2", "land": "yes", "摘要": summary, "判": items,
        "标记": [], "自标": [], "facts": [], "kept": [], "again": [],
        "done": [], "promote": [], "note": "",
    }


def _parse_review_command(text: str, state: dict) -> str:
    """只认“第 N 轮别记 / 恢复”这类可执行的建议；其余如实记下但不假装改图。"""
    text = str(text or "")
    drop = {_as_int(number) for number in (state.get("drop_turns") or [])}
    changed = False
    for match in re.finditer(r"第\s*(\d+)\s*轮", text):
        number = int(match.group(1))
        window = text[max(0, match.start() - 8):match.end() + 8]
        if re.search(r"(恢复|留下|别删|要记)", window):
            if number in drop:
                drop.discard(number)
                changed = True
        elif re.search(r"(别记|不记|删|去掉|跳过)", window):
            if number not in drop:
                drop.add(number)
                changed = True
    if re.search(r"(这一场|整场|全部).{0,6}(别记|不记|删|跳过)", text):
        for number in state.get("_turn_numbers") or []:
            drop.add(_as_int(number))
        changed = True
    only = re.search(r"只记(?:住)?第\s*(\d+)\s*轮", text)
    if only:
        keep = int(only.group(1))
        for number in state.get("_turn_numbers") or []:
            if _as_int(number) != keep:
                drop.add(_as_int(number))
        drop.discard(keep)
        changed = True
    if re.search(r"(别记|不记|删|去掉|跳过)", text) and not re.search(r"第\s*\d+\s*轮", text):
        # “这一轮别记” = 最后一轮。
        turns = state.get("_turn_numbers") or []
        if turns:
            number = _as_int(turns[-1])
            if number not in drop:
                drop.add(number)
                changed = True
    state["drop_turns"] = sorted(drop)
    return "改了" if changed else "记下了"


def _drop_turn_from_graph(chat, number: int) -> None:
    graph = chat.graph
    target = None
    for turn in chat.store.turns():
        if _as_int(turn.get("turn")) == number:
            target = turn
            break
    if not target:
        return
    for call in target.get("calls") or []:
        event_id = str(call.get("id") or "")
        event = graph.events.get(event_id)
        if not event:
            continue
        kept = []
        for segment in event.get("segments") or []:
            if str(segment.get("session") or "") != chat.session_id:
                kept.append(segment)
                continue
            start = _as_int(segment.get("start"))
            end = _as_int(segment.get("end"), start)
            if number < start or number > end:
                kept.append(segment)
            elif start == end:
                continue
            elif number == end:
                segment["end"] = end - 1
                kept.append(segment)
            elif number == start:
                segment["start"] = start + 1
                kept.append(segment)
            else:
                kept.append({"session": segment.get("session"),
                             "start": start, "end": number - 1})
                kept.append({"session": segment.get("session"),
                             "start": number + 1, "end": end})
        event["segments"] = kept
        if not kept:
            graph.remove_event(event_id)
    graph.save()


def _archive(chat, sink=None, record: Optional[dict] = None) -> dict:
    from memory_v2.consolidate.replay import consolidate
    _sink_call(sink, "phase", "归档：整理图")
    stats = consolidate(chat.graph, chat.data_dir, chat.session_id,
                        embedder=chat.embedder)
    if record:
        _write_json(_archive_record_path(chat.data_dir, chat.session_id), record)
    _sink_call(sink, "phase", "归档：完成")
    return stats


def chat_finish(data_dir: str, sid: str, sink=None) -> str:
    try:
        with _LOCK:
            chat = _CHATS.pop(str(sid), None)
            if chat is None:
                chat = _open(data_dir, _cred_path(data_dir), sid)
            stats = _archive(chat, sink)
            _drop_review(data_dir, sid)
            return _ok(failed=False,
                       facts_added=stats.get("edges", 0),
                       kept_added=stats.get("reinforced", 0),
                       dream_rounds=stats.get("seeds", 0),
                       note="", stats=stats)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "finish")


def chat_archive_open(data_dir: str, sid: str, sink=None) -> str:
    try:
        with _LOCK:
            chat = _chat_for(data_dir, sid)
            turns = chat.store.turns()
            if not turns:
                return _err("这一场还没有可归档的轮次")
            existing = _load_review(data_dir, sid)
            if existing:
                _sink_call(sink, "phase", "归档：接着审")
                draft = _build_draft(chat, existing)
                existing["draft"] = draft
                return _ok(draft=draft)
            _sink_call(sink, "phase", "归档：出草案")
            state = {
                "drop_turns": [], "drop_events": [], "said": [],
                "_turn_numbers": [_as_int(turn.get("turn")) for turn in turns],
            }
            draft = _build_draft(chat, state)
            state["draft"] = draft
            _save_review(data_dir, sid, {k: v for k, v in state.items() if k != "draft"})
            _REVIEW[str(sid)] = state
            _sink_call(sink, "phase", "归档：草案好了")
            return _ok(draft=draft)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "archive")


def chat_archive_ask(data_dir: str, sid: str, text: str, sink=None) -> str:
    try:
        with _LOCK:
            state = _load_review(data_dir, sid)
            if state is None:
                return _err("没有正在审的草案；先点「归档」")
            chat = _chat_for(data_dir, sid)
            if state.get("_turn_numbers") is None:
                state["_turn_numbers"] = [_as_int(turn.get("turn"))
                                          for turn in chat.store.turns()]
            verdict = _parse_review_command(text, state)
            state["_warn"] = (verdict != "改了")
            said = [str(item) for item in (state.get("said") or [])]
            said.append(str(text))
            state["said"] = said[-12:]
            draft = _build_draft(chat, state)
            state["draft"] = draft
            _save_review(data_dir, sid, {k: v for k, v in state.items()
                                         if k not in ("draft", "_turn_numbers")})
            return _ok(draft=draft, note=verdict)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "archive")


def chat_archive_commit(data_dir: str, sid: str, sink=None) -> str:
    try:
        with _LOCK:
            state = _load_review(data_dir, sid)
            if state is None:
                return _err("没有正在审的草案；先点「归档」")
            chat = _CHATS.pop(str(sid), None) or _chat_for(data_dir, sid)
            draft = _build_draft(chat, state)
            for number in state.get("drop_turns") or []:
                _drop_turn_from_graph(chat, _as_int(number))
            record = {"判": draft.get("判") or [], "层1": [],
                      "摘要": draft.get("摘要") or "",
                      "建议": state.get("said") or []}
            stats = _archive(chat, sink, record=record)
            _drop_review(data_dir, sid)
            return _ok(failed=False,
                       facts_added=stats.get("edges", 0),
                       kept_added=stats.get("reinforced", 0),
                       dream_rounds=stats.get("seeds", 0),
                       note="", stats=stats)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "archive")


def close_session(data_dir: str, sid: str) -> str:
    return chat_finish(data_dir, sid, None)


# --------------------------------------------------------------------------- 活动 / 旧场 / 探针 / 模型


def activity_lines(data_dir: str, n: str = "80") -> List[str]:
    try:
        data_dir = _data_dir(data_dir)
        directory = os.path.join(data_dir, "状态")
        rows = []
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                if not name.endswith(".jsonl") or name.endswith(".messages.json"):
                    continue
                path = os.path.join(directory, name)
                sid = name[:-6]
                try:
                    with open(path, encoding="utf-8") as handle:
                        count = sum(1 for line in handle if line.strip())
                except Exception:
                    count = 0
                rows.append((os.path.getmtime(path), sid, count,
                             os.path.exists(path + ".archived")))
        rows.sort(reverse=True)
        out = ["最近 %d 场：" % min(len(rows), _as_int(n, 80))]
        for stamp, sid, count, archived in rows[: _as_int(n, 80)]:
            out.append("%s · %s · %d 轮%s" % (
                time.strftime("%m-%d %H:%M", time.localtime(stamp)),
                sid, count, " · 已归档" if archived else " · 在途"))
        if not rows:
            out = ["还没有场次"]
        return out
    except Exception as exc:  # noqa: BLE001
        return ["读不出活动记录：%s: %s" % (type(exc).__name__, exc)]


def past_list_json(data_dir: str) -> str:
    try:
        data_dir = _data_dir(data_dir)
        directory = os.path.join(data_dir, "状态")
        rows = []
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                if not name.endswith(".jsonl") or name.endswith(".messages.json"):
                    continue
                path = os.path.join(directory, name)
                if not os.path.exists(path + ".archived"):
                    continue
                from memory_v2.store.session import SessionStore
                sid = name[:-6]
                turns = SessionStore(data_dir, sid).turns()
                rows.append({"场id": sid, "轮数": len(turns),
                             "ts": os.path.getmtime(path)})
        rows.sort(key=lambda item: item["ts"], reverse=True)
        return _ok(场们=rows)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "past")


def past_get_json(data_dir: str, sid: str) -> str:
    try:
        from memory_v2.store.session import SessionStore
        data_dir = _data_dir(data_dir)
        store = SessionStore(data_dir, sid)
        turns = store.turns()
        stored = store.load_messages() or []
        prefix = [str(item.get("content") or "") for item in stored
                  if str(item.get("role") or "") == "system"]
        if not prefix:
            prefix = [str(item.get("content") or "")
                      for item in _system_messages(data_dir)]
        out_turns = []
        for turn in turns:
            segments = []
            reasoning = str(turn.get("reasoning") or "")
            if reasoning:
                segments.append({"类": "思考", "文本": reasoning, "回": ""})
            for recall in turn.get("recall") or []:
                keywords = " ".join(str(item) for item in (recall.get("keywords") or []))
                winner = recall.get("winner") or {}
                segments.append({"类": "回忆", "文本": "recall " + keywords,
                                 "回": str(winner.get("name") or "")})
            for item in turn.get("remember") or []:
                segments.append({"类": "回忆", "文本": "remember",
                                 "回": str(item.get("id") or "")})
            events = turn.get("events") or []
            if events:
                for event in events:
                    segments.append({"类": "事件",
                                     "文本": str(event.get("name") or ""),
                                     "回": str(event.get("id") or "")})
            elif turn.get("event"):
                segments.append({"类": "事件", "文本": str(turn.get("event")),
                                 "回": str(turn.get("event_id") or "")})
            out_turns.append({
                "轮": _as_int(turn.get("turn")),
                "user": str(turn.get("user") or ""),
                "ai": str(turn.get("assistant") or ""),
                "段": segments,
                "撤回": False,
            })
        last_event = ""
        if turns:
            last_event = str(turns[-1].get("event") or "")
        record = _read_json(_archive_record_path(data_dir, sid), {}) or {}
        return _ok(场id=sid,
                   系统前缀=prefix,
                   常驻="memory_v2 事件图",
                   在场=last_event,
                   轮=out_turns,
                   指纹="",
                   归档={"判": [item for item in (record.get("判") or [])
                               if not item.get("跳过")],
                        "层1": record.get("层1") or [],
                        "摘要": record.get("摘要") or "",
                        "建议": record.get("建议") or []})
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "past")


def recall_probe(data_dir: str, sid: str, clue: str) -> str:
    try:
        from memory_v2.read.activate import activate
        with _LOCK:
            chat = _chat_for(data_dir, sid)
            result = activate(chat.graph, data_dir, [str(clue or "")],
                              embedder=chat.embedder)
        items = result.get("candidates") or []
        pieces = []
        for item in items:
            event_id = str(item.get("id") or "")
            event = chat.graph.events.get(event_id) or {}
            pieces.append({"件id": event_id,
                           "最高": round(_as_float(item.get("score")), 3),
                           "段数": len(event.get("segments") or []),
                           "取用": False})
        return _ok(线索=str(clue or ""), 激活数=len(items), 格=[], 件=pieces)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "probe")


def models_json(data_dir: str, which: str = "chat") -> str:
    try:
        from memory_v2.core.config import Credentials
        cred = Credentials.load(_cred_path(data_dir))
        if which == "embed":
            endpoint = cred.embed_url.rstrip("/")
            key = cred.embed_key
        else:
            endpoint = cred.chat_url.split("/chat/completions")[0].rstrip("/")
            key = cred.api_key
        if not endpoint:
            return _err("这一家还没有配地址")
        request = urllib.request.Request(endpoint + "/models", method="GET")
        request.add_header("Authorization", "Bearer " + key)
        request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = []
        for item in payload.get("data") or []:
            model_id = str(item.get("id") or "")
            if model_id:
                models.append({"id": model_id, "own": model_id})
        return _ok(端点=endpoint, 模型=models)
    except Exception as exc:  # noqa: BLE001
        return _err("%s: %s" % (type(exc).__name__, exc), "models")


# --------------------------------------------------------------------------- 渠道 / 自检


def channel_tick(data_dir: str, payload_json: str) -> str:
    return _ok(skipped=True, note="memory_v2 未启用渠道后台；界面入口保留。")


def deliver_reply(data_dir: str, did: str, text: str) -> str:
    return _ok(skipped=True, note="memory_v2 未启用渠道投递。")


def selfcheck(data_dir: str = "") -> str:
    """离线自检：图能落盘/读回、冻结目录能写。⛔ 不碰用户数据。"""
    base = _cache_dir(data_dir) if data_dir else os.path.join(
        os.path.expanduser("~"), ".palimpsest-v2-selftest")
    tmp = os.path.join(base, "_自检")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    lines = []
    try:
        from memory_v2.store.graph import Graph
        graph = Graph.open(tmp)
        event_id = graph.add_event("自检")
        graph.attach(event_id, "自检场", 1)
        graph.save()
        loaded = Graph.open(tmp)
        lines.append("图：%d 个件 / %d 个 segment" % (
            len(loaded.events), len(loaded.events[event_id].get("segments") or [])))
        from memory_v2.store.frozen import update_global
        update_global(tmp)
        lines.append("冻结：已写入 %s" % os.path.join(tmp, "冻结"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    import numpy
    return ("memory_v2 %s · numpy %s · %s（临时样例，跑完已删）"
            % (getattr(__import__("memory_v2"), "__version__", "?"),
               numpy.__version__, " · ".join(lines)))
