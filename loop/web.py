"""网页面：说、听、看。

    POST /say       {"text": "..."}       → 进 inbox，立刻返回
    GET  /feed?since=3:12&step=40         → 增量原文 + 增量用量
    GET  /context                         → 当前上下文（sink / 旧段 / 两块原文）
    GET  /state                           → 它现在在干什么、今天用了多少
    GET  /                                → 单页界面

★ 这一层**只读库、只往 inbox 写**，从不碰 blocks/entries/bands 的写路径：
  单写者仍然是那个循环进程。所以网页崩了顶多是看不见，不会把记录弄坏。
★ 每条连接各自开一个 LoopStore（SQLite 连接不能跨线程共享）；库开了 WAL，
  读不挡写。
★ 鉴权是 Bearer token：没有令牌一律 401，界面本身也走同一道门。
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import control, prompt_files, settings
from .context import Context, estimate_tokens, serialize_entries
from .store import LoopStore

_INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")
_local = threading.local()


def _store_of(server) -> LoopStore:
    """每线程一条连接：SQLite 的连接不能跨线程用。"""
    store = getattr(_local, "store", None)
    path = getattr(_local, "path", "")
    if store is None or path != server.db_path:
        store = LoopStore(server.db_path)
        _local.store = store
        _local.path = server.db_path
    return store


def _entry_view(entry) -> dict:
    message = entry.message
    return {"role": str(message.get("role") or ""),
            "text": str(message.get("content") or ""),
            "calls": list(message.get("tool_calls") or []),
            "call_id": str(message.get("tool_call_id") or ""),
            "ts": str(entry.ts),
            "meta": dict(entry.meta or {})}


def _block_view(block, fold_keep: int) -> dict:
    rendered = serialize_entries(block.entries, fold_keep=fold_keep)
    return {"start_ts": str(block.start_ts), "end_ts": str(block.end_ts),
            "count": len(block.entries),
            "chars": len(rendered),
            "tokens": estimate_tokens(rendered),
            "entries": [_entry_view(entry) for entry in block.entries]}


class _Handler(BaseHTTPRequestHandler):
    server_version = "palimpsest"
    protocol_version = "HTTP/1.1"

    # 命令行里不要刷访问日志：它跟聊天记录混在一起看不清。
    def log_message(self, format, *args):  # noqa: A002
        return

    # ── 工具 ──────────────────────────────────────────────────────────────
    def _send(self, code: int, payload, content_type="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else \
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            # 浏览器关页、刷新、点走：这条连接已经没了，没什么可做的。
            return

    def _authorized(self) -> bool:
        token = str(self.server.token or "")
        if not token:
            return True
        given = str(self.headers.get("Authorization") or "")
        prefix = "Bearer "
        if not given.startswith(prefix):
            return False
        return hmac.compare_digest(given[len(prefix):].strip(), token)

    def _guard(self) -> bool:
        if self._authorized():
            return True
        self._send(401, {"error": "令牌不对"})
        return False

    # ── GET ───────────────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802
        route = urlsplit(self.path)
        if route.path in ("/", "/index.html"):
            try:
                with open(_INDEX, "rb") as handle:
                    body = handle.read()
            except OSError:
                return self._send(500, {"error": "界面文件不见了"})
            return self._send(200, body, "text/html; charset=utf-8")
        if route.path == "/favicon.ico":
            return self._send(204, b"")
        if not self._guard():
            return
        try:
            if route.path == "/state":
                return self._send(200, self._state())
            if route.path == "/context":
                return self._send(200, self._context())
            if route.path == "/settings":
                return self._send(200, self._settings_payload())
            if route.path == "/feed":
                return self._send(200, self._feed(parse_qs(route.query)))
        except Exception as exc:                     # 页面不该因为一条查询挂掉
            return self._send(500, {"error": "内部出错：%s" % exc})
        return self._send(404, {"error": "没有这个地址"})

    def do_POST(self):  # noqa: N802
        if not self._guard():
            return
        route = urlsplit(self.path)
        try:
            if route.path == "/say":
                return self._say()
            if route.path == "/restart":
                return self._restart()
        except Exception as exc:
            return self._send(500, {"error": "内部出错：%s" % exc})
        return self._send(404, {"error": "没有这个地址"})

    def do_DELETE(self):  # noqa: N802
        if not self._guard():
            return
        route = urlsplit(self.path)
        if route.path != "/entries":
            return self._send(404, {"error": "没有这个地址"})
        query = parse_qs(route.query)
        try:
            block = int((query.get("block") or ["0"])[0])
            seq = int((query.get("seq") or ["0"])[0])
            removed = _store_of(self.server).delete_entry(block, seq)
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        # 正在跑的那个进程手里还留着旧的块：让它重读一遍，不用重启。
        control.request_reload()
        return self._send(200, {"ok": True, "removed": removed, "restart": False})

    def do_PUT(self):  # noqa: N802
        if not self._guard():
            return
        route = urlsplit(self.path)
        try:
            if route.path == "/settings":
                return self._save_settings()
            if route.path == "/prompts":
                return self._save_prompt()
        except Exception as exc:
            return self._send(500, {"error": "内部出错：%s" % exc})
        return self._send(404, {"error": "没有这个地址"})

    # ── 写 ────────────────────────────────────────────────────────────────
    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(length, 512 * 1024)) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("要一个对象")
        return data

    def _say(self):
        payload = self._body()
        text = str(payload.get("text") or "").strip()
        if not text:
            return self._send(400, {"error": "没有正文"})
        item_id = _store_of(self.server).push("user", text)
        return self._send(200, {"ok": True, "id": item_id})

    def _save_settings(self):
        payload = self._body()
        try:
            stored = settings.save(self.server.data_dir, payload.get("values") or {})
        except settings.SettingsError as exc:
            return self._send(400, {"error": str(exc)})
        self._apply_live(stored)
        return self._send(200, {"ok": True, "stored": stored, "restart": False})

    def _apply_live(self, stored: dict) -> None:
        """旋钮改了立刻生效：这几项都是每步现读的，不用等重启。

        只有模型名藏在客户端自己的配置里，要单独推一把。
        """
        config = self.server.config
        settings.apply(config, stored)
        loop = getattr(self.server, "loop", None)
        client_config = getattr(getattr(loop, "client", None), "config", None)
        if client_config is not None:
            client_config.model = config.model
            client_config.temperature = config.temperature
            client_config.max_tokens = config.max_tokens

    def _save_prompt(self):
        payload = self._body()
        key = str(payload.get("key") or "")
        try:
            prompt_files.write_override(self.server.data_dir, key,
                                        str(payload.get("text") or ""))
        except prompt_files.PromptError as exc:
            return self._send(400, {"error": str(exc)})
        return self._send(200, {"ok": True})

    def _restart(self):
        control.request_stop()
        return self._send(200, {"ok": True})

    # ── 设置视图 ──────────────────────────────────────────────────────────
    def _settings_payload(self) -> dict:
        config = self.server.config
        data_dir = self.server.data_dir
        prompts = []
        current = prompt_files.overrides()
        for key, (label, desc, filename) in prompt_files.META.items():
            try:
                default = prompt_files.default_text(key)
            except Exception:
                default = ""
            override = str(current.get(key) or "")
            prompts.append({"key": key, "label": label, "desc": desc,
                            "file": filename, "default": default,
                            "override": override, "text": override or default})
        return {"values": settings.effective(config),
                "stored": settings.load(data_dir),
                "defaults": settings.defaults(),
                "fields": settings.FIELDS,
                "groups": list(settings.GROUPS),
                "prompts": prompts,
                "history": settings.history(data_dir),
                "data_dir": data_dir}

    # ── 三个视图 ──────────────────────────────────────────────────────────
    def _state(self) -> dict:
        store = _store_of(self.server)
        config = self.server.config
        state = store.load_state()
        context = Context(config)
        context.set_state(state["bands"], state["previous"], state["current"])
        fold_keep = int(getattr(config, "tool_fold_keep", 0) or 0)
        current = serialize_entries(state["current"].entries, fold_keep=fold_keep)
        previous = serialize_entries(state["previous"].entries, fold_keep=fold_keep)
        totals = store.usage_totals()
        return {"name": getattr(config, "name", ""),
                "model": getattr(config, "model", ""),
                "status": store.status(),
                "steps": totals["steps"],
                "compressions": self._compressions(),
                "current_tokens": estimate_tokens(current),
                "context_tokens": (estimate_tokens(context.system_text())
                                   + estimate_tokens(previous)
                                   + estimate_tokens(current)),
                "block_tokens": int(getattr(config, "block_tokens", 0)),
                "context_budget": int(getattr(config, "context_budget", 0)),
                "today": store.usage_today(),
                "totals": totals}

    def _compressions(self) -> int:
        return sum(1 for block in _store_of(self.server).blocks()
                   if block["state"] == "compressed")

    def _context(self) -> dict:
        store = _store_of(self.server)
        config = self.server.config
        fold_keep = int(getattr(config, "tool_fold_keep", 0) or 0)
        state = store.load_state()
        context = Context(config)
        context.set_state(state["bands"], state["previous"], state["current"])
        bands = [{"level": band.level, "start_ts": band.start_ts,
                  "end_ts": band.end_ts, "text": band.text,
                  "chars": len(str(band.text or "")),
                  "tokens": estimate_tokens(str(band.text or ""))}
                 for band in state["bands"]]
        previous = _block_view(state["previous"], fold_keep)
        current = _block_view(state["current"], fold_keep)
        sink = context.sink()
        system_tokens = estimate_tokens(context.system_text())
        return {"sink": {"text": sink, "chars": len(sink),
                         "tokens": estimate_tokens(sink)},
                "bands": bands,
                "previous": previous,
                "current": current,
                "block_tokens": previous["tokens"] + current["tokens"],
                "context_tokens": system_tokens + previous["tokens"] + current["tokens"],
                "limits": {"block_tokens": int(getattr(config, "block_tokens", 0)),
                           "context_budget": int(getattr(config, "context_budget", 0)),
                           "band_budget": int(getattr(config, "band_budget", 0)),
                           "band_count": len(getattr(config, "band_chars", ()) or ()),
                           "tool_fold_keep": fold_keep}}

    def _feed(self, query: dict) -> dict:
        store = _store_of(self.server)
        since = str((query.get("since") or ["0:0"])[0])
        try:
            block, seq = [int(part) for part in since.split(":")]
        except ValueError:
            block, seq = 0, 0
        try:
            since_step = int((query.get("step") or ["0"])[0])
        except ValueError:
            since_step = 0
        entries = store.feed(block, seq)
        steps = store.usage_since(since_step)
        cursor = {"block": block, "seq": seq}
        if entries:
            cursor = {"block": entries[-1]["block"], "seq": entries[-1]["seq"]}
        return {"entries": entries, "steps": steps, "cursor": cursor,
                "more": len(entries) >= 300}


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, BrokenPipeError)):
            return                    # 浏览器刷新、关页：不值得记一行
        super().handle_error(request, client_address)


def serve(config, path: str, token: str = "",
          host: str = "127.0.0.1", port: int = 8080,
          data_dir: str = "", loop=None) -> ThreadingHTTPServer:
    """起一个后台线程；返回的 httpd 交给调用方（自检里用来取端口）。"""
    httpd = _Server((host, int(port)), _Handler)
    httpd.config = config
    httpd.db_path = path
    httpd.token = token
    httpd.data_dir = data_dir or os.path.dirname(os.path.abspath(path))
    httpd.loop = loop
    threading.Thread(target=httpd.serve_forever, daemon=True, name="web").start()
    return httpd


def make_token(path: str) -> str:
    """第一次起服务时生成一个令牌，写进数据目录（600），下次直接用。"""
    import secrets
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                token = handle.read().strip()
            if token:
                return token
        except OSError:
            pass
    token = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(token)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token
