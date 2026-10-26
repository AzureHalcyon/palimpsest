"""把页面导出的编辑清单应用到事件图。

页面（`python -m memory_v2 view`）是只读的；要改就先在页面上攒一份清单——改名、
删掉一件事、剪断一条边——点「存到服务器」，清单落进 `<数据>/edits/`。

★ **循环在跑的时候不用管它**：`loop/driver.py` 的 `_apply_edits` 会在两步之间（和等的
  时候）把清单取出来应用，落完挪进 `edits/applied/`。单写者仍然是循环进程。
★ 循环没在跑（或者离线改了图）才用这条路：`python -m memory_v2 apply --file …`。
★ 写之前自动备份成 `graph.json.bak-<时间>`；`--dry-run` 只说不写。
★ 只改图，不动状态记录：`ingest` / `ingest --rebuild` 会照状态记录把事件补回来，
  真要永久删掉，连状态记录里的那几轮也得处理（那是另一件事）。
★ 改名同理：只改 `graph.json` 里的名字，`状态/*.jsonl` 里的事件还是原名。

清单长这样（页面导出时自动带上 `data`）：

    {"data": "…/data/memory",
     "ops": [{"op": "rename", "id": "e-…", "name": "新名字"},
             {"op": "delete", "id": "e-…"},
             {"op": "unlink", "a": "e-…", "b": "e-…"}]}

`id` 也接受名字（走 `graph.resolve`）。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import time
from typing import Dict, List, Tuple


class EditError(RuntimeError):
    """清单本身不对（不是对象、缺字段之类）。"""


def load(path: str) -> dict:
    """读一份清单；只校验它是不是个对象，逐条的问题留给 apply。"""
    try:
        with io.open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise EditError("读不了清单：%s" % exc) from exc
    except ValueError as exc:
        raise EditError("清单不是合法 JSON：%s" % exc) from exc
    if not isinstance(payload, dict):
        raise EditError("清单要是一个对象：{\"ops\": [...]}")
    ops = payload.get("ops")
    if ops is None:
        ops = []
    if not isinstance(ops, list):
        raise EditError("ops 要是数组")
    return {"data": str(payload.get("data") or ""), "ops": ops}


def backup(graph_path: str) -> str:
    """把图备一份；返回备份路径。没有原图就不备（第一次写）。"""
    if not os.path.exists(graph_path):
        return ""
    target = graph_path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(graph_path, target)
    return target


def _pairs_with(graph, event_id: str) -> List[str]:
    """候选对里引用这件事件的键（删事件时一起清，别留悬空对）。"""
    keys = []
    for key, pair in graph.pairs.items():
        if not isinstance(pair, dict):
            continue
        if event_id in (str(pair.get("a") or ""), str(pair.get("b") or "")):
            keys.append(key)
    return keys


def _drop_event(graph, event_id: str) -> int:
    """删事件连同它的边与候选对；返回顺手删掉的边数。"""
    graph.events.pop(event_id, None)
    doomed = [key for key, edge in graph.edges.items()
              if event_id in (str(edge.get("a") or ""), str(edge.get("b") or ""))]
    for key in doomed:
        del graph.edges[key]
    for key in _pairs_with(graph, event_id):
        del graph.pairs[key]
    return len(doomed)


def apply(graph, ops: List[dict], dry_run: bool = False) -> Dict[str, object]:
    """逐条应用；返回 {changed, log, skipped}。不写盘——写不写由调用方决定。"""
    log: List[str] = []
    skipped: List[str] = []
    changed = 0

    for number, op in enumerate(ops, 1):
        if not isinstance(op, dict):
            skipped.append("第 %d 条不是对象" % number)
            continue
        kind = str(op.get("op") or "").strip()
        if kind == "rename":
            event_id = graph.resolve(str(op.get("id") or ""))
            if not event_id:
                skipped.append("改名：找不到 %s" % op.get("id"))
                continue
            name = str(op.get("name") or "").strip()
            if not name:
                skipped.append("改名：名字是空的（%s）" % event_id)
                continue
            taken = [other for other, event in graph.events.items()
                     if other != event_id and str(event.get("name") or "") == name]
            if taken:
                skipped.append("改名：已经有同名事件「%s」，没动 %s" % (name, event_id))
                continue
            old = str(graph.events[event_id].get("name") or "")
            if old == name:
                skipped.append("改名：%s 已经叫这个了" % event_id)
                continue
            graph.events[event_id]["name"] = name
            log.append("改名：%s → %s（%s）" % (old or event_id, name, event_id))
            changed += 1
        elif kind == "delete":
            event_id = graph.resolve(str(op.get("id") or ""))
            if not event_id:
                skipped.append("删除：找不到 %s" % op.get("id"))
                continue
            name = str(graph.events[event_id].get("name") or event_id)
            edges = _drop_event(graph, event_id)
            log.append("删除：%s（连带 %d 条边）" % (name, edges))
            changed += 1
        elif kind == "unlink":
            first = graph.resolve(str(op.get("a") or ""))
            second = graph.resolve(str(op.get("b") or ""))
            if not first or not second:
                skipped.append("剪边：找不到 %s / %s" % (op.get("a"), op.get("b")))
                continue
            keys = [key for key, edge in graph.edges.items()
                    if {str(edge.get("a") or ""), str(edge.get("b") or "")} == {first, second}]
            for key in keys:
                del graph.edges[key]
            if keys:
                log.append("剪边：%s × %s（%d 条）"
                           % (graph.events[first].get("name") or first,
                              graph.events[second].get("name") or second, len(keys)))
                changed += 1
            else:
                skipped.append("剪边：这两件之间没有边")
        else:
            skipped.append("第 %d 条不知道是什么操作：%r" % (number, kind))

    return {"changed": changed, "log": log, "skipped": skipped,
            "dry_run": bool(dry_run)}


def report(result: Dict[str, object], out=print, err=None) -> None:
    """把结果打出来：改了什么、跳过了什么。"""
    import sys

    err = err or sys.stderr
    for line in result["log"]:
        out(line)
    for line in result["skipped"]:
        print("跳过：%s" % line, file=err)
