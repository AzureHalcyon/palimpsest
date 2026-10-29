"""归档一次：把这段上下文补录进事件图，写日记，重写自我描述，再跑一次重放。

为什么是"补录"而不是"再压一层摘要"：上下文里只留两块原文 + 一层摘要，再往前的东西
要么在图里（`recall` 得到），要么在日记和归档文件里（人能翻）。所以这一段记录在离开
上下文之前，先让她自己说一遍"这里面什么值得留"——用她本来就有的 `remember` /
`link` 落地，不另开一条写库的路（查重、id 解析、scope 校验全照旧）。

顺序是定死的：

1. 材料：旧段摘要 + 上一块原文。**当前块不动**——归档不改变她眼前的现场。
2. 快照：先把材料和图（改动前）写到 `<data>/archive/<时间戳>/`，出事能照它回滚。
3. 补录：一次模型调用 → `events` / `links` / `diary`。
4. 重放：种子就是这次补录碰到的事件。"这一批"替代了原来的"这一场"。
5. 自我描述：调 `persona.rewrite`（它本来就只喂"上次改写之后的记录"）。
6. 日记：追加进 `<data>/workspace/日记/<日期>.md`——真文件，不注入任何提示。

失败不推进任何一步：补录解析不出来就原样返回 `bad_json`，图没动过，下次还能重来。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import time

from . import persona, prompt_files
from .context import serialize_entries

MAX_EVENTS = 8
MAX_LINKS = 12
FEED_CHARS = 40000
SHORT_DAYS = 3


def _int(config, name: str, fallback: int) -> int:
    try:
        return int(getattr(config, name, fallback) or 0)
    except (TypeError, ValueError):
        return fallback


def material_text(context, config) -> str:
    """要送出去的材料：旧段摘要 + 上一块原文。当前块不进——她还在用。"""
    fold = _int(config, "tool_fold_keep", 0)
    user = str(getattr(config, "user_name", "") or "")
    body = serialize_entries(list(context.previous.entries), fold, user_name=user)
    bands = str(context.bands_text() or "").strip()
    parts = []
    if bands:
        parts.append("旧段摘要：\n%s" % bands)
    parts.append("上一块原文：\n%s" % (body or "（还没有）"))
    text = "\n\n".join(parts)
    cap = _int(config, "archive_feed_chars", FEED_CHARS)
    if cap and len(text) > cap:
        text = "…（前略）\n" + text[-cap:]
    return text


def parse_reply(text: str) -> dict:
    """模型回的 JSON：剥掉代码围栏，取第一个 `{` 到最后一个 `}`。"""
    body = str(text or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        if "\n" in body:
            body = body.split("\n", 1)[1]
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回复里没有 JSON")
    data = json.loads(body[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 不是对象")
    return data


def apply(memory, payload: dict, config, log=None) -> dict:
    """把 events / links 落到图上；返回计数与这次碰到的事件 id。"""
    max_events = _int(config, "archive_max_events", MAX_EVENTS)
    days = _int(config, "archive_short_days", SHORT_DAYS)
    touched, created, updated, skipped = [], 0, 0, 0
    for item in (payload.get("events") or [])[:max_events]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        note = str(item.get("note") or "").strip()
        if not note:
            skipped += 1
            continue
        result = memory.remember(note=note,
                                 name=str(item.get("name") or "").strip(),
                                 event=str(item.get("merge_into") or "").strip(),
                                 scope=str(item.get("scope") or "long"),
                                 days=days)
        reason = str(result.get("reason") or "")
        data = result.get("data") or {}
        if reason in ("created", "updated"):
            event_id = str(data.get("event_id") or "")
            if event_id:
                touched.append(event_id)
            created += 1 if reason == "created" else 0
            updated += 1 if reason == "updated" else 0
        else:
            skipped += 1
            if log:
                log("[归档] 这条没记上（%s）：%s"
                    % (reason, str(item.get("name") or note[:20])))
    linked = 0
    for item in (payload.get("links") or [])[:MAX_LINKS]:
        if not isinstance(item, dict):
            continue
        result = memory.link(a=str(item.get("a") or ""),
                             b=str(item.get("b") or ""),
                             relation=str(item.get("relation") or "相关"))
        if result.get("ok"):
            linked += 1
    return {"created": created, "updated": updated, "skipped": skipped,
            "linked": linked, "touched": touched}


def roll_material(store, config, target_id: int, previous_id: int) -> str:
    """滚块那一刻给模型的两段：**要补录**的那块原文 + 次新那块（只作上下文）。

    为什么要带上次新那块：只给一块的话，模型分不清哪句是"刚要发生"、哪句是"已经过去"，
    连关系时会连反；多给一块又不许它从这一段里记，它就知道边界在哪。
    """
    fold = _int(config, "tool_fold_keep", 0)
    user = str(getattr(config, "user_name", "") or "")

    def dump(block_id: int) -> str:
        block = store.load_block(int(block_id))
        return serialize_entries(block.entries, fold, user_name=user)

    parts = []
    if target_id:
        parts.append("【要补录】块 %d 的原文：\n%s" % (target_id, dump(target_id) or "（空）"))
    if previous_id:
        parts.append("【只作上下文】块 %d 的原文（这一段不要从里面记）：\n%s"
                     % (previous_id, dump(previous_id) or "（空）"))
    return "\n\n".join(parts)


def export_raw(store, config, folder: str, keep_ids=(), only_ids=None) -> dict:
    """把滚出原文窗口的块，按块结构化搬进归档，再从库里删。

    存成 `原文/<块 id>.json`：`{id, start_ts, end_ts, summary, keywords, entries:[…]}`，
    外加一份 `原文/index.json` 清单。不用一段 markdown：md 一改格式就读不回来，
    JSON 能原样重建那一块，也能 jq / grep。

    为什么删库：那些原文运行时一个字都不读（`load_state` 只认 `previous` 与 `open`），
    留着只是给网页往回翻。块行留着——摘要、时间、关键词都还在。
    """
    keep = {int(item) for item in (keep_ids or ())}
    wanted = {int(item) for item in (only_ids or ())}
    rows_out, removed = [], 0
    blocks_dir = os.path.join(folder, "原文")
    for row in store.blocks():
        block_id = int(row["id"])
        if block_id in keep or str(row.get("state") or "") == "open":
            continue
        if wanted and block_id not in wanted:
            continue
        block = store.load_block(block_id)
        if not block.entries:
            continue
        entries = []
        for entry in block.entries:
            message = entry.message or {}
            entries.append({"seq": int(getattr(entry, "seq", 0) or 0),
                            "ts": str(getattr(entry, "ts", "") or ""),
                            "role": str(message.get("role") or ""),
                            "content": str(message.get("content") or ""),
                            "tool_calls": list(message.get("tool_calls") or []),
                            "tool_call_id": str(message.get("tool_call_id") or ""),
                            "meta": dict(getattr(entry, "meta", None) or {})})
        os.makedirs(blocks_dir, exist_ok=True)
        name = "%04d.json" % block_id
        payload = {"id": block_id,
                   "start_ts": str(row.get("start_ts") or ""),
                   "end_ts": str(row.get("end_ts") or ""),
                   "summary": str(row.get("summary") or ""),
                   "keywords": list(row.get("keywords") or []),
                   "entries": entries}
        with io.open(os.path.join(blocks_dir, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        rows_out.append({"id": block_id, "entries": len(entries), "file": name})
        removed += store.purge_block_entries(block_id)
    if not rows_out:
        return {"blocks": 0, "entries": 0}
    index = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
             "blocks": rows_out, "entries": removed}
    with io.open(os.path.join(blocks_dir, "index.json"), "w", encoding="utf-8") as handle:
        json.dump(index, handle, ensure_ascii=False, indent=1)
    return {"blocks": len(rows_out), "entries": removed, "index": "原文/index.json"}


def write_diary(config, text: str, moment: float = None) -> str:
    """日记：真文件，追加到 `<data>/workspace/日记/<日期>.md`。不进任何提示。"""
    body = str(text or "").strip()
    if not body:
        return ""
    stamp = moment if moment is not None else time.time()
    folder = os.path.join(str(getattr(config, "data_dir", ".") or "."),
                          "workspace", "日记")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("%Y-%m-%d", time.localtime(stamp)) + ".md")
    fresh = not os.path.exists(path)
    with io.open(path, "a", encoding="utf-8") as handle:
        if fresh:
            handle.write("# %s\n" % time.strftime("%Y-%m-%d", time.localtime(stamp)))
        handle.write("\n## %s\n%s\n" % (time.strftime("%H:%M", time.localtime(stamp)), body))
    return path


def snapshot(config, material: str, payload: dict, report: dict) -> str:
    """这次归档的现场：材料、模型回的 JSON、结果、改动前的图。"""
    folder = os.path.join(str(getattr(config, "data_dir", ".") or "."), "archive",
                          time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    with io.open(os.path.join(folder, "material.md"), "w", encoding="utf-8") as handle:
        handle.write(material)
    with io.open(os.path.join(folder, "payload.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    with io.open(os.path.join(folder, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return folder


def _keep_graph(config, folder: str, graph=None) -> None:
    """改动前的图抄一份进快照目录（图还没落过盘就先让写一次）。"""
    source = os.path.join(str(getattr(config, "data_dir", ".") or "."), "memory",
                          "graph.json")
    try:
        if not os.path.exists(source) and graph is not None:
            graph.save()
        shutil.copy2(source, os.path.join(folder, "graph-before.json"))
    except OSError:
        pass


def run(config, context, memory, chat, log=None, usage=None, store=None,
        keep_ids=(), material: str = "", target_id: int = 0) -> dict:
    """跑一次归档。返回报告（`ok` 为假时图与 persona 都没动过）。

    `material` 给了就用它（滚块时是"要补录那块 + 次新那块"两段），否则用
    "旧段摘要 + 上一块原文"。`target_id` 给了就只把那一块的原文搬走（滚块用），
    否则把所有滚出窗口的块一起搬。
    """
    started = time.time()
    if not material:
        if not list(context.previous.entries):
            # 当前块不动，所以"上一块"还是空的时候就没什么可归档的。别拿当前块凑数：
            # 那是她正在用的现场，归档只处理已经滚过去的那一块。
            return {"ok": False, "reason": "no_previous_block"}
        material = material_text(context, config)
    if not material.strip() or material.strip().endswith("（还没有）"):
        return {"ok": False, "reason": "no_material"}
    max_events = _int(config, "archive_max_events", MAX_EVENTS)
    prompt = prompt_files.render("archive.txt", max_events=max_events)
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": material}]
    report = {"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "material_chars": len(material)}
    try:
        reply = chat(messages) or {}
    except Exception as exc:                    # 调不通就当这次没发生
        return {"ok": False, "reason": "chat_failed", "error": str(exc)}
    if usage:
        report["cost"] = dict(usage() or {})
    try:
        payload = parse_reply(str(reply.get("content") or ""))
    except (ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "bad_json", "error": str(exc),
                "ts": report["ts"]}

    folder = ""
    try:
        folder = snapshot(config, material, payload, report)
        _keep_graph(config, folder, getattr(memory, "graph", None))
    except OSError as exc:
        report["snapshot_error"] = str(exc)

    counts = apply(memory, payload, config, log=log)
    report.update(counts)

    if store is not None and folder:
        try:
            raw = export_raw(store, config, folder, keep_ids,
                             only_ids=[target_id] if target_id else None)
            if raw.get("blocks"):
                report["raw"] = raw
        except Exception as exc:            # 搬原文坏了不该把补录也退掉
            report["raw_error"] = str(exc)

    if counts["touched"] and getattr(memory, "graph", None) is not None:
        try:
            from memory_v2.consolidate.replay import consolidate
            report["replay"] = consolidate(memory.graph, memory.data_dir,
                                           seeds=counts["touched"],
                                           scope_ids=counts["touched"],
                                           embedder=getattr(memory, "embedder", None))
        except Exception as exc:                # 重放坏了不该把补录也退掉
            report["replay_error"] = str(exc)

    try:
        tuned = persona.tune(chat, material, config)
        report["persona_changed"] = bool(tuned)
        if tuned:
            report["persona_chars"] = len(tuned)
    except Exception as exc:
        report["persona_error"] = str(exc)

    try:
        diary = write_diary(config, str(payload.get("diary") or ""), started)
        if diary:
            report["diary"] = os.path.basename(diary)
    except OSError as exc:
        report["diary_error"] = str(exc)

    report["seconds"] = round(time.time() - started, 1)
    if folder:
        try:
            with io.open(os.path.join(folder, "report.json"), "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
        except OSError:
            pass
        report["folder"] = folder
    return report


def summary(report: dict) -> str:
    """一行给人（和她自己）看的结论。"""
    if not report.get("ok"):
        reasons = {
            "no_previous_block": "当前这块还没滚过，没有上一块可归档",
            "no_material": "没有可归档的材料",
            "bad_json": "模型没按 JSON 回",
            "chat_failed": "模型没调通",
            "crashed": "出错了",
        }
        reason = str(report.get("reason") or "未知")
        return "归档没做成（%s）" % reasons.get(reason, reason)
    bits = ["补录 %d 件（新建 %d、更新 %d）"
            % (int(report.get("created") or 0) + int(report.get("updated") or 0),
               int(report.get("created") or 0), int(report.get("updated") or 0))]
    if report.get("skipped"):
        bits.append("跳过 %d" % report["skipped"])
    if report.get("linked") is not None:
        bits.append("连 %d 条" % report["linked"])
    replay = report.get("replay") or {}
    if replay:
        bits.append("重放：点亮 %d、新建 %d、剪掉 %d"
                    % (replay.get("lit", 0), replay.get("new_edges", 0),
                       replay.get("pruned", 0)))
    raw = report.get("raw") or {}
    if raw.get("blocks"):
        bits.append("原文归档 %d 块（%d 条）"
                    % (raw.get("blocks"), raw.get("entries", 0)))
    if report.get("persona_chars"):
        bits.append("自我描述微调 %d 字" % report["persona_chars"])
    elif report.get("persona_changed") is False:
        bits.append("自我描述没动")
    if report.get("diary"):
        bits.append("日记 %s" % report["diary"])
    return "；".join(bits)
