"""Command line.

Usage:

    python -m memory_v2 chat --credentials <yaml>
    python -m memory_v2 chat --script <one turn per line>
    python -m memory_v2 show --session <id>
    python -m memory_v2 recall --keywords "空调,预算"
    python -m memory_v2 replay --session <id>
    python -m memory_v2 events
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

from .chat.loop import EventChat
from .consolidate.replay import consolidate
from .core.config import ChatConfig, Credentials, CredentialsError
from .core.embed import Embedder
from .core.llm import ChatClient
from .core.prompts import system_messages
from .read.activate import activate
from .store import frozen as frozen_store
from .store import snapshot as snapshot_store
from .store.graph import Graph, GraphError
from .store.ingest import ingest
from .store.session import SessionStore, SessionSealed, next_session_id


DEFAULT_DATA_DIR = os.path.join(".local", "memory_v2")


def _client(credentials: str) -> ChatClient:
    return ChatClient(ChatConfig(), Credentials.load(credentials))


def _embedder(credentials: str):
    try:
        return Embedder(ChatConfig(), Credentials.load(credentials))
    except CredentialsError:
        return None


def _print_table(turns: List[dict]) -> None:
    print("轮\t事件\tid\t本轮回调")
    current_name = "—"
    current_id = "—"
    for turn in turns:
        if turn.get("event"):
            current_name = str(turn["event"])
            current_id = str(turn.get("event_id") or "—")
            modes = [str(call.get("mode")) for call in turn.get("calls") or [] if call.get("mode")]
            state_modes = [mode for mode in modes if mode != "关联"]
            called = state_modes[-1] if state_modes else (modes[-1] if modes else "")
            if "关联" in modes:
                called += "+关联"
            if len(modes) > 1:
                called += " ×%d" % len(modes)
            if not called:
                called = "新报"
        else:
            called = "沿用（没有调用）"
        print("%s\t%s\t%s\t%s" % (turn.get("turn"), current_name, current_id, called))


def _send(chat: EventChat, text: str) -> bool:
    print("你> %s" % text)
    try:
        reply = chat.send(text)
    except Exception as exc:  # noqa: BLE001
        print("[错误] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return False
    print("她> %s" % (reply or "（没有回复）"))
    return True


def _cmd_chat(args) -> int:
    data_dir = os.path.abspath(args.data)
    store = SessionStore(data_dir, args.session or next_session_id(data_dir))
    chat = EventChat(
        _client(args.credentials),
        store,
        Graph.open(data_dir),
        data_dir,
        store.session_id,
        system_messages(data_dir),
        embedder=_embedder(args.credentials),
        echo=lambda text: print(text, file=sys.stderr, flush=True),
    )
    print("场：%s" % store.session_id)

    if args.script:
        with open(args.script, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
        for text in lines:
            if not _send(chat, text):
                return 1
    else:
        print("输入 /quit 结束，/state 看事件。")
        while True:
            try:
                text = input("你> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            text = text.strip()
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            if text == "/state":
                _print_table(store.turns())
                continue
            if not _send(chat, text):
                continue

    print("状态记录：%s" % store.path)
    _print_table(store.turns())
    if args.replay_on_exit:
        stats = consolidate(chat.graph, data_dir, store.session_id,
                            embedder=_embedder(args.credentials))
        print("巩固：种子 %d，点亮 %d，感应 %d，加强 %d，新建 %d，剪掉 %d" % (
            stats["seeds"], stats["lit"], stats["content"], stats["reinforced"],
            stats["edges"], stats["pruned"]))
    return 0


def _cmd_show(args) -> int:
    store = SessionStore(args.data, args.session)
    turns = store.turns()
    if not turns:
        print("没有这一场：%s" % store.path, file=sys.stderr)
        return 2
    _print_table(turns)
    return 0


def _cmd_recall(args) -> int:
    data_dir = os.path.abspath(args.data)
    graph = Graph.open(data_dir)
    keywords = [item.strip() for item in str(args.keywords).split(",") if item.strip()]
    result = activate(graph, data_dir, keywords, embedder=_embedder(args.credentials),
                      after=str(args.after or ""), before=str(args.before or ""),
                      limit=int(args.limit))
    winner = result["winner"]
    if winner:
        print("最像：%s\t%s\t%s\t%s" % (winner["id"], winner["name"], winner["date"],
                                       "、".join(winner["hits"])))
    for item in result["candidates"][1:] if winner else result["near"]:
        print("备选：%s\t%s\t%s\t%s" % (item["id"], item["name"], item["date"],
                                       "、".join(item["hits"])))
    if not result["candidates"]:
        print("没有找到。")
    return 0


def _cmd_replay(args) -> int:
    data_dir = os.path.abspath(args.data)
    graph = Graph.open(data_dir)
    stats = consolidate(graph, data_dir, args.session, embedder=_embedder(args.credentials))
    print("种子 %d，点亮 %d，感应 %d，加强 %d，新建 %d，剪掉 %d" % (
        stats["seeds"], stats["lit"], stats["content"], stats["reinforced"],
        stats["edges"], stats["pruned"]))
    return 0


def _cmd_mark(args) -> int:
    data_dir = os.path.abspath(args.data)
    graph = Graph.open(data_dir)
    event_id = graph.resolve(args.event)
    if not event_id:
        print("没有这一件：%s" % args.event, file=sys.stderr)
        return 2
    graph.events[event_id]["reward"] = True
    graph.save()
    print("标了：%s %s" % (event_id, graph.events[event_id].get("name")))
    return 0


def _cmd_ingest(args) -> int:
    data_dir = os.path.abspath(args.data)
    graph = Graph.open(data_dir)
    stats = ingest(graph, data_dir, rebuild=bool(args.rebuild))
    if args.rebuild:
        print("读了 %d 场，重建 %d 件" % (stats["sessions"], stats["events"]))
        if stats["backup"]:
            print("旧图备份：%s" % stats["backup"])
    else:
        print("读了 %d 场，新增 %d 件" % (stats["sessions"], stats["events"]))
    return 0


def _cmd_freeze(args) -> int:
    graph = Graph.open(os.path.abspath(args.data))
    removed = graph.purge_expired()
    if removed:
        graph.save()
        print("清掉过期的短记忆：%d 件" % removed)
    path = frozen_store.update_global(args.data)
    print("冻结：%s" % path)
    return 0


def _cmd_snapshot(args) -> int:
    path = snapshot_store.create(args.data, args.out)
    print("快照：%s" % os.path.abspath(path))
    return 0


def _cmd_restore(args) -> int:
    path = snapshot_store.restore(args.snapshot, args.to)
    print("已解到：%s" % path)
    return 0


def _cmd_events(args) -> int:
    graph = Graph.open(os.path.abspath(args.data))
    for event_id, event in sorted(graph.events.items()):
        segments = event.get("segments") or []
        print("%s\t%s\t%d 段\t巩固 %.1f\t可及 %.1f%s" % (
            event_id, event.get("name"), len(segments),
            float(event.get("consolidation", 0)), float(event.get("access", 0)),
            " 奖励" if event.get("reward") else ""))
    return 0


def _cmd_edges(args) -> int:
    graph = Graph.open(os.path.abspath(args.data))
    rows = sorted(graph.edges.values(), key=lambda edge: -float(edge.get("strength", 0)))
    for edge in rows[:int(args.limit)]:
        first = graph.events.get(edge.get("a"), {}).get("name", edge.get("a"))
        second = graph.events.get(edge.get("b"), {}).get("name", edge.get("b"))
        print("%s\t%s\t%s\t%.2f" % (first, second, edge.get("relation"),
                                    float(edge.get("strength", 0))))
    print("共 %d 条边" % len(graph.edges))
    return 0


def _cmd_apply(args) -> int:
    """把页面导出的编辑清单落到 graph.json（先备份；--dry-run 只说不写）。"""
    from . import edits

    graph = Graph.open(os.path.abspath(args.data))
    payload = edits.load(args.file)
    if payload["data"] and os.path.abspath(payload["data"]) != os.path.abspath(args.data):
        print("注意：清单里的目录是 %s，这次按 --data %s 写。"
              % (payload["data"], args.data), file=sys.stderr)
    result = edits.apply(graph, payload["ops"], dry_run=args.dry_run)
    edits.report(result)
    if not result["changed"]:
        print("没有改动。")
        return 0
    if args.dry_run:
        print("演练：会改 %d 处，没写库。" % result["changed"])
        return 0
    saved = edits.backup(graph.path)
    graph.save()
    print("改了 %d 处。%s" % (result["changed"],
                             ("原图备份在 %s" % saved) if saved else "原来没有图，新建了。"))
    print("只改了图：状态记录没动，ingest / ingest --rebuild 会把事件按状态记录补回来。")
    return 0


def _cmd_view(args) -> int:
    """把事件图画成一页 HTML（自带数据，浏览器直接打开）。"""
    from . import view

    argv = ["--data", os.path.abspath(args.data), "--out", args.out,
            "--span", str(args.span)]
    if args.label:
        argv += ["--label", args.label]
    if args.png:
        argv += ["--png"]
    if args.seed:
        argv += ["--seed", args.seed]
    return view.main(argv)


def _cmd_unlink(args) -> int:
    graph = Graph.open(os.path.abspath(args.data))
    first = graph.resolve(args.first)
    second = graph.resolve(args.second)
    if not first or not second:
        print("找不到这两件。", file=sys.stderr)
        return 2
    keys = [key for key, edge in graph.edges.items()
            if {edge.get("a"), edge.get("b")} == {first, second}
            and (not args.relation or edge.get("relation") == args.relation)]
    for key in keys:
        del graph.edges[key]
    graph.save()
    print("剪了 %d 条。" % len(keys))
    return 0


def _cmd_detach(args) -> int:
    graph = Graph.open(os.path.abspath(args.data))
    event_id = graph.resolve(args.event)
    if not event_id:
        print("没有这一件：%s" % args.event, file=sys.stderr)
        return 2
    segments = list(graph.events[event_id].get("segments") or [])
    if args.last:
        if segments:
            segments.pop()
    elif args.session:
        segments = [segment for segment in segments
                    if not (segment.get("session") == args.session
                            and (not args.turn
                                 or int(segment.get("start", 0)) <= int(args.turn)
                                 <= int(segment.get("end", 0))))]
    else:
        print("要么 --last，要么 --session。", file=sys.stderr)
        return 2
    graph.events[event_id]["segments"] = segments
    graph.save()
    print("剩 %d 段。" % len(segments))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="memory_v2")
    sub = parser.add_subparsers(dest="command", required=True)

    chat_parser = sub.add_parser("chat", help="聊一场，每轮记下正在说的那件事")
    chat_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    chat_parser.add_argument("--session", default="")
    chat_parser.add_argument("--credentials", default="")
    chat_parser.add_argument("--script", default="", help="一轮一行的文件；给了就不进交互")
    chat_parser.add_argument("--replay-on-exit", action="store_true",
                             help="聊完对这场跑一次巩固")
    chat_parser.set_defaults(func=_cmd_chat)

    show_parser = sub.add_parser("show", help="打印一场的事件表")
    show_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    show_parser.add_argument("--session", required=True)
    show_parser.set_defaults(func=_cmd_show)

    recall_parser = sub.add_parser("recall", help="按关键词找候选")
    recall_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    recall_parser.add_argument("--keywords", required=True, help="逗号分隔的关键词")
    recall_parser.add_argument("--limit", default="5", help="返回几件候选")
    recall_parser.add_argument("--after", default="", help="只要这天之后（含）的，YYYY-MM-DD")
    recall_parser.add_argument("--before", default="", help="只要这天之前（含）的，YYYY-MM-DD")
    recall_parser.add_argument("--credentials", default="")
    recall_parser.set_defaults(func=_cmd_recall)

    replay_parser = sub.add_parser("replay", help="对一场做一次巩固")
    replay_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    replay_parser.add_argument("--session", required=True)
    replay_parser.add_argument("--credentials", default="")
    replay_parser.set_defaults(func=_cmd_replay)

    mark_parser = sub.add_parser("mark", help="手标一件事（奖励）")
    mark_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    mark_parser.add_argument("--event", required=True)
    mark_parser.set_defaults(func=_cmd_mark)

    detach_parser = sub.add_parser("detach", help="把认错的片段摘下来")
    detach_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    detach_parser.add_argument("--event", required=True)
    detach_parser.add_argument("--last", action="store_true")
    detach_parser.add_argument("--session", default="")
    detach_parser.add_argument("--turn", default="")
    detach_parser.set_defaults(func=_cmd_detach)

    unlink_parser = sub.add_parser("unlink", help="剪掉一条边")
    unlink_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    unlink_parser.add_argument("--first", required=True)
    unlink_parser.add_argument("--second", required=True)
    unlink_parser.add_argument("--relation", default="")
    unlink_parser.set_defaults(func=_cmd_unlink)

    ingest_parser = sub.add_parser("ingest", help="从状态记录补齐或整体重建事件图")
    ingest_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    ingest_parser.add_argument("--rebuild", action="store_true",
                               help="清空当前图后整体重建；旧图先备份")
    ingest_parser.set_defaults(func=_cmd_ingest)

    freeze_parser = sub.add_parser("freeze", help="把当前 live 图/向量写成全局冻结版")
    freeze_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    freeze_parser.set_defaults(func=_cmd_freeze)

    snapshot_parser = sub.add_parser("snapshot", help="把一个数据目录打成单文件 .psnap")
    snapshot_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    snapshot_parser.add_argument("--out", required=True)
    snapshot_parser.set_defaults(func=_cmd_snapshot)

    restore_parser = sub.add_parser("restore", help="把 .psnap 解回目录")
    restore_parser.add_argument("--snapshot", required=True)
    restore_parser.add_argument("--to", required=True)
    restore_parser.set_defaults(func=_cmd_restore)

    events_parser = sub.add_parser("events", help="列出事件")
    events_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    events_parser.set_defaults(func=_cmd_events)

    edges_parser = sub.add_parser("edges", help="列出边")
    edges_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    edges_parser.add_argument("--limit", default="20")
    edges_parser.set_defaults(func=_cmd_edges)

    apply_parser = sub.add_parser("apply", help="把页面导出的编辑清单落到 graph.json")
    apply_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    apply_parser.add_argument("--file", required=True, help="页面导出的 edits.json")
    apply_parser.add_argument("--dry-run", action="store_true", help="只说会改什么，不写库")
    apply_parser.set_defaults(func=_cmd_apply)

    view_parser = sub.add_parser("view", help="把事件图画成一页 HTML（浏览器里看）")
    view_parser.add_argument("--data", default=DEFAULT_DATA_DIR)
    view_parser.add_argument("--label", default="", help="页面上显示的名字；默认用目录名")
    view_parser.add_argument("--out", default=os.path.join(".local", "view"))
    view_parser.add_argument("--span", type=float, default=620.0)
    view_parser.add_argument("--png", action="store_true", help="同时出 PNG 快照")
    view_parser.add_argument("--seed", default="", help="PNG 激活快照的起点事件 id")
    view_parser.set_defaults(func=_cmd_view)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (GraphError, SessionSealed) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
