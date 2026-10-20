"""One-message-at-a-time HTTP entry for an external agent.

Start it once, then POST /send with one user message per call; the process
keeps the conversation state. POST /close runs consolidation for the session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .chat.loop import EventChat
from .consolidate.replay import consolidate
from .core.config import ChatConfig, Credentials, CredentialsError
from .core.embed import Embedder
from .core.llm import ChatClient
from .core.prompts import system_messages
from .store.graph import Graph, GraphError
from .store.session import SessionStore, SessionSealed, next_session_id


class ChatService:
    def __init__(self, data_dir: str, session: str, credentials: str):
        config = ChatConfig()
        creds = Credentials.load(credentials)
        self.data_dir = data_dir
        self.client = ChatClient(config, creds)
        try:
            self.embedder = Embedder(config, creds)
        except CredentialsError:
            self.embedder = None
        self.store = SessionStore(data_dir, session)
        self.chat = EventChat(
            self.client, self.store, Graph.open(data_dir), data_dir,
            self.store.session_id, system_messages(), embedder=self.embedder,
            echo=lambda text: print(text, file=sys.stderr, flush=True),
        )
        self.lock = threading.Lock()

    def send(self, text: str) -> dict:
        with self.lock:
            reply = self.chat.send(text)
            turns = self.store.turns()
            last = turns[-1] if turns else {}
            return {
                "reply": reply,
                "session": self.store.session_id,
                "turn": last.get("turn"),
                "event": last.get("event"),
                "event_id": last.get("event_id"),
                "mode": last.get("mode"),
                "events": last.get("events") or [],
            }

    def state(self) -> dict:
        turns = []
        for turn in self.store.turns():
            turns.append({
                "turn": turn.get("turn"),
                "event": turn.get("event"),
                "event_id": turn.get("event_id"),
                "mode": turn.get("mode"),
                "events": turn.get("events") or [],
            })
        return {"session": self.store.session_id, "turns": turns}

    def close(self) -> dict:
        with self.lock:
            return consolidate(self.chat.graph, self.data_dir, self.store.session_id,
                              embedder=self.embedder)


class Handler(BaseHTTPRequestHandler):
    service: ChatService = None

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True, "session": self.service.store.session_id})
        elif self.path == "/state":
            self._json(200, self.service.state())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json(400, {"error": "bad json"})
            return
        if self.path == "/send":
            text = str(payload.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "empty text"})
                return
            try:
                self._json(200, self.service.send(text))
            except SessionSealed as exc:
                self._json(409, {"error": str(exc)})
            except Exception as exc:
                print("[serve] send failed: %s" % exc, file=sys.stderr, flush=True)
                self._json(500, {"error": str(exc)})
        elif self.path == "/close":
            try:
                self._json(200, self.service.close())
            except Exception as exc:
                print("[serve] close failed: %s" % exc, file=sys.stderr, flush=True)
                self._json(500, {"error": str(exc)})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:
        print("[serve] " + (fmt % args), file=sys.stderr, flush=True)


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="memory_v2.serve")
    parser.add_argument("--data", required=True)
    parser.add_argument("--session", default="")
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data)
    session = args.session or next_session_id(data_dir)
    try:
        service = ChatService(data_dir, session, args.credentials)
    except (GraphError, SessionSealed) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1
    Handler.service = service
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("listening http://127.0.0.1:%d  session=%s" % (args.port, session), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
