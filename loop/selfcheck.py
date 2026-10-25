"""离线自检：假模型、假数据、临时库，不碰网络。

    python -m loop.selfcheck

覆盖：摘要解析、旧段下移与截短、预算、两块原文的攒块时机、压缩失败保留原文、
工具结果写回、空回复即停、重启后从库重建两块原文。
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime

from testsupport import tempdir
from .compress import CompressError, Compressor, add_band, parse_summary, truncate
from . import control
from . import prompt_files
from . import web_access
from . import webtools
from . import settings
from . import shell
from . import workspace
from . import web
from .cli import _clean_line
from .config import LoopConfig
from .context import (Band, Block, BlockEntry, Context, estimate_tokens,
                      serialize_entries, time_range)
from .driver import Loop, _timeout_text, _woke_text, parse_until
from .memory_bridge import MemoryBridge
from .store import LoopStore
from .usage import collect, render
from memory_v2.core.llm import ChatError

CHECKS = [0]


def check(condition, message: str) -> None:
    CHECKS[0] += 1
    if not condition:
        raise AssertionError(message)


def _loop_config(**kwargs) -> LoopConfig:
    """自检默认不打盹：这些用例没有外部输入，打盹会把它们提前停掉。"""
    kwargs.setdefault("watchdog_idle", 0)
    return LoopConfig(**kwargs)


class FakeClient:
    """一次说一句、一次调工具、一次空回复；usage 按消息内容估。"""

    def __init__(self):
        self.last_usage = {}
        self.calls = 0
        self.prompts = []

    def chat(self, messages, tools=None, on_delta=None):
        prompt = "\n".join(str(message.get("content") or "") for message in messages)
        self.prompts.append(prompt)
        number = self.calls
        self.calls += 1
        content, calls = self._reply(number)
        # 与真端点一样：工具 schema 也算进 prompt_tokens。
        sent = estimate_tokens(prompt)
        if tools:
            sent += estimate_tokens(json.dumps(tools, ensure_ascii=False))
        self.last_usage = {"prompt_tokens": sent,
                           "completion_tokens": estimate_tokens(content)}
        message = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = calls
        return message

    @staticmethod
    def _reply(number: int):
        slot = number % 4
        if slot == 0:
            return "第 %d 步：说一句。" % number, None
        if slot == 1:
            return "", [{"id": "call_%d" % number, "type": "function",
                         "function": {"name": "recall",
                                      "arguments": "{\"keywords\": [\"预算\"]}"}}]
        if slot == 2:
            return "", None
        return "第 %d 步：再说一句。" % number, None


class BothClient(FakeClient):
    """一条回复里既有正文又有工具调用——原生 tool_calls 允许这样。"""

    @staticmethod
    def _reply(number: int):
        if number == 0:
            return "我先查一下。", [{"id": "both-1", "type": "function",
                                     "function": {"name": "recall",
                                                  "arguments": "{\"keywords\": [\"预算\"]}"}}]
        return "", None


def fake_summarizer(prompt: str) -> dict:
    return {"summary": "现在：在自检。", "keywords": ["自检"], "unfinished": "等输入"}


class BadSummarizer:
    def __call__(self, prompt: str) -> dict:
        raise CompressError("故意失败")


def check_summary() -> None:
    payload = parse_summary(
        '{"summary": "b", "keywords": ["k"], "unfinished": "u"}')
    check(payload["summary"] == "b", "摘要要读到 summary")
    check(payload["keywords"] == ["k"], "摘要要读到 keywords")
    fenced = parse_summary('```json\n{"summary": "b"}\n```')
    check(fenced["summary"] == "b", "代码围栏里的 JSON 也要认")
    for bad in ("no json", '{"keywords": []}'):
        try:
            parse_summary(bad)
        except CompressError:
            continue
        raise AssertionError("坏摘要应报 CompressError：%r" % bad)


def check_compress() -> None:
    config = _loop_config(name="自检", band_chars=(100, 200, 400, 800), band_decay=0.8)
    bands = []
    for index in range(5):
        bands = add_band(bands, "摘要" * 200,
                         "2026-10-24 0%d:00:00" % index,
                         "2026-10-24 0%d:30:00" % index,
                         config.band_chars, config.band_decay)
    check(len(bands) == 4, "第五块之后应只剩四段")
    check([band.level for band in bands] == [0, 1, 2, 3],
          "段的 level 应连续并止于 3")
    lengths = [len(band.text) for band in bands]
    check(all(lengths[index] < lengths[index + 1]
              for index in range(len(lengths) - 1)),
          "越旧的段必须越短，实际 %r" % lengths)
    for band in bands:
        check(len(band.text) <= config.band_chars[band.level],
              "第 %d 段超过长度上限" % band.level)

    block = Block([BlockEntry({"role": "user", "content": "对方：预算二十八万。"},
                              "2026-10-24 21:00:00")],
                  "2026-10-24 21:00:00", "2026-10-24 21:10:00")
    result = Compressor(config, summarizer=fake_summarizer).compress_block(block)
    check(result["summary"] == "现在：在自检。", "压缩块应读回 summary")
    check(result["start_ts"] == block.start_ts, "压缩块应保留起始时间")
    check(truncate("abc", 10) == "abc", "没超长不该截断")
    check(time_range("2026-10-24 09:00:00", "2026-10-24 10:00:00") == "10-24 09:00–10:00",
          "同一天只写一次日期")


def check_context_limits() -> None:
    config = _loop_config(name="自检", band_chars=(100, 200, 400, 800), band_budget=400)
    context = Context(config)
    context.set_bands([Band(0, "a" * 400, "2026-10-24 09:00:00", "2026-10-24 10:00:00"),
                      Band(1, "b" * 400, "2026-10-24 10:00:00", "2026-10-24 11:00:00"),
                      Band(2, "c" * 400, "2026-10-24 11:00:00", "2026-10-24 12:00:00"),
                      Band(3, "d" * 400, "2026-10-24 12:00:00", "2026-10-24 13:00:00")])
    fitted = context.fitted_bands()
    check(len(fitted) < 4, "超预算应丢最旧的段")
    check(estimate_tokens(context.system_text())
          <= estimate_tokens(context.sink()) + 450,
          "丢掉之后旧段应放得下")
    context.drop_oldest()
    check(all(band.level != 0 for band in context.bands), "drop_oldest 应去掉 level 0")


def check_two_blocks() -> None:
    workdir = tempdir("loop-two-")
    try:
        config = _loop_config(name="自检", block_tokens=50, max_steps=8,
                            park_seconds=0.001)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        loop.run()
        check(loop.compressions == 0, "第一块满了先不压，实际压了 %d 次"
              % loop.compressions)
        check(len(loop.context.previous.entries) > 0, "第一块应变成上一块")
        check(loop.context.bands == [], "第一块不该产生摘要")
        states = {block["state"] for block in store.blocks()}
        check("previous" in states and "open" in states,
              "库里应有一块 previous 和一块 open")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_clock() -> None:
    """墙钟只拼进这一步的输入：不进记录、不落库，隔了多久用相对时长说。"""
    context = Context(_loop_config(name="自检"))
    context.current = Block([
        BlockEntry({"role": "user", "content": "预算二十八万。"}, "2026-10-25 14:12:00"),
        BlockEntry({"role": "assistant", "content": ""}, "2026-10-25 15:40:00"),
        BlockEntry({"role": "assistant", "content": "记下了。"}, "2026-10-25 15:36:00"),
    ], "2026-10-25 14:12:00", "2026-10-25 15:40:00")
    text = context.clock_text(datetime(2026, 10, 25, 15, 42))
    check(text == "（现在 10-25 周日 15:42；距上次对方说话 1 小时 30 分；"
                  "距我上次开口 6 分钟）", "墙钟写得不对：%s" % text)
    check("现在" not in context.block_text(), "墙钟不该进记录")
    check("现在 10-25" in context.messages()[-1]["content"], "墙钟该在最后一条输入里")


def check_loop() -> None:
    workdir = tempdir("loop-selfcheck-")
    try:
        config = _loop_config(name="自检", block_tokens=50, context_budget=80000,
                            band_budget=3000, band_chars=(300, 600, 1200, 2400),
                            park_seconds=0.001, max_steps=24)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        loop.on_speak = lambda text: None
        loop.run()

        check(loop.steps == 24, "应跑满 24 步，实际 %d" % loop.steps)
        check(loop.compressions >= 2, "24 步应至少压 2 次，实际 %d" % loop.compressions)

        compressed = [block for block in store.blocks()
                      if block["state"] == "compressed"]
        check(len(compressed) == loop.compressions, "压缩块条数应等于压缩次数")

        bands = store.bands()
        check(0 < len(bands) <= 4, "旧段最多四段")
        check([band.level for band in bands] == list(range(4 - len(bands), 4)),
              "段的 level 应连续并止于 3")
        for band in bands:
            check(len(band.text) <= config.band_chars[band.level],
                  "第 %d 段超过上限" % band.level)
        check(bands[-1].text.strip() != "", "最新一段不该是空的")

        final_prompt = loop.client.prompts[-1]
        check("压缩" not in final_prompt, "压缩过程不能出现在上下文里")
        check(estimate_tokens(final_prompt) <= config.context_budget + 2000,
              "上下文不应超出预算太多")

        # 重启：上一块与当前块的原文都要接上。
        store2 = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop2 = Loop(config, credentials=None, store=store2,
                     compressor=Compressor(config, summarizer=fake_summarizer),
                     memory=MemoryBridge.disabled(), client=FakeClient())
        check(len(loop2.context.bands) == len(bands), "重启后应恢复旧段")
        check(loop2.previous_id == loop.previous_id, "重启后上一块 id 应一致")
        check(loop2.current_id == loop.current_id, "重启后当前块 id 应一致")
        check(len(loop2.context.current.entries) == len(loop.context.current.entries),
              "重启后当前块原文应一致")
        check(len(store2.entries(loop2.current_id)) == len(loop.context.current.entries),
              "原文应按条落盘")
        store2.close()
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_compress_failure() -> None:
    workdir = tempdir("loop-failure-")
    try:
        config = _loop_config(name="自检", block_tokens=30, max_steps=12,
                            park_seconds=0.001)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        logs = []
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=BadSummarizer()),
                    memory=MemoryBridge.disabled(), client=FakeClient(),
                    echo=logs.append)
        loop.run()
        check(loop.compressions == 0, "摘要失败不能假装压过")
        check(len(loop.context.previous.entries) > 0, "摘要失败后上一块应保留")
        check(any("压缩失败" in line for line in logs), "失败要报给运行时输出")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_tools() -> None:
    workdir = tempdir("loop-tools-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=4,
                            park_seconds=0.001)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        loop.run()
        prompts = loop.client.prompts
        check(any("调用 recall" in prompt for prompt in prompts),
              "工具调用要写进记录")
        check(any("结果：记忆还没接上" in prompt for prompt in prompts),
              "工具结果要写进记录")
        check("[T]" not in loop.context.block_text()
              and "[S]" not in loop.context.block_text()
              and "[C]" not in loop.context.block_text(),
              "记录里不该再有方括号动作")
        check(len(store.entries(loop.current_id)) > 0, "原文应按条落盘")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_memory() -> None:
    workdir = tempdir("loop-memory-")
    try:
        bridge = MemoryBridge(
            os.path.join(workdir, "memory"),
            writer=lambda material: "我记得：" + material.replace("\n", " ")[:40],
            lexical=True)
        reply = bridge.call("remember", json.dumps(
            {"name": "预算二十八万", "note": "预算二十八万，已确认。",
             "scope": "long"}, ensure_ascii=False))
        check("已记" in reply, "remember 应成功：%s" % reply)
        found = bridge.call("recall", json.dumps(
            {"keywords": ["预算"]}, ensure_ascii=False))
        check("预算二十八万" in found, "recall 应找到名字：%s" % found)
        event_id = next(iter(bridge.graph.events))
        got = bridge.call("recollect", json.dumps({"event": event_id}))
        check(got.startswith("我记得："), "recollect 应走编写：%s" % got)

        bridge.call("remember", json.dumps(
            {"name": "临时事", "note": "只留一天", "scope": "short", "days": -1},
            ensure_ascii=False))
        again = bridge.call("recall", json.dumps(
            {"keywords": ["临时事"]}, ensure_ascii=False))
        check("临时事" not in again, "过期的 short 不该被 recall 到")

        # 最近 N 步：默认 0（关）；开了才挡新鲜事件。
        bridge.step = 5
        bridge.recent_steps = 3
        bridge.call("remember", json.dumps(
            {"name": "新鲜事", "note": "刚写下的。", "scope": "long"},
            ensure_ascii=False))
        fresh = bridge.call("recall", json.dumps(
            {"keywords": ["新鲜"]}, ensure_ascii=False))
        check("新鲜事" not in fresh, "最近 3 步内的事件应被挡：%s" % fresh)
        bridge.step = 10
        fresh = bridge.call("recall", json.dumps(
            {"keywords": ["新鲜"]}, ensure_ascii=False))
        check("新鲜事" in fresh, "过了 N 步应能 recall 到：%s" % fresh)
        bridge.recent_steps = 0

        # 词面默认关：没有嵌入时查不到，而不是偷偷退回词面。
        plain = MemoryBridge(os.path.join(workdir, "memory-plain"))
        plain.call("remember", json.dumps(
            {"name": "只按词面", "note": "这条只能靠词面找。"}, ensure_ascii=False))
        blocked = plain.call("recall", json.dumps(
            {"keywords": ["词面"]}, ensure_ascii=False))
        check("查不到" in blocked, "词面关且无嵌入时应查不到：%s" % blocked)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_usage() -> None:
    workdir = tempdir("loop-usage-")
    try:
        config = _loop_config(name="自检", block_tokens=50, max_steps=10,
                            park_seconds=0.001)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        loop.run()

        rows = store.usages()
        check(len(rows) == loop.steps,
              "用量应按步落盘：%d 行对 %d 步" % (len(rows), loop.steps))
        check(all(row["prompt"] > 0 for row in rows), "每步都应有真实 prompt_tokens")
        totals = store.usage_totals()
        check(totals["steps"] == loop.steps, "用量合计的步数应一致")
        check(totals["peak"] > 0, "应记下最大一次上下文")

        # 真数减掉固定部分，应该和当前块的估算同量级（差一步：测的是发请求前那块）。
        check(loop.context.current_tokens() - 60
              <= loop.current_real <= loop.context.current_tokens() + 60,
              "当前块的真数应贴着估算：%d 对 %d"
              % (loop.current_real, loop.context.current_tokens()))
        # 估算常数按实测校准：100 个汉字约 76 token。
        check(abs(estimate_tokens("中" * 100) - 76) <= 2,
              "汉字估算应接近 0.76 token/字：%d" % estimate_tokens("中" * 100))

        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_remember_hidden() -> None:
    entry = BlockEntry({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "remember", "arguments": json.dumps(
                            {"name": "甲", "note": "秘密正文"}, ensure_ascii=False)}}],
    }, "2026-10-25 00:00:00")
    text = serialize_entries([entry])
    check("秘密正文" not in text, "note 不该进记录")
    check("写下了记忆：甲" in text, "记录只留名字")


def check_tool_fold() -> None:
    """工具是持续调的：只折能重取的（recall），记忆本身（recollect）不折。"""
    recall_result = "找到 3 件：\n" + "\n".join(
        ["ev%d 某件旧事 · 2026-10-2%d" % (index, index) for index in range(3)]) + \
        "\n看具体用 recollect；连边用 remember 的 links。"
    memory_text = "我记得" + "那天很晚，" * 40

    def entries(count: int, tool: str, result: str) -> list:
        items = []
        for index in range(count):
            items.append(BlockEntry({
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "c%d" % index, "type": "function",
                                "function": {"name": tool,
                                             "arguments": "{\"keywords\": [\"旧事\"]}"}}],
            }, "2026-10-25 00:00:0%d" % (index % 10)))
            items.append(BlockEntry(
                {"role": "tool", "tool_call_id": "c%d" % index, "content": result},
                "2026-10-25 00:00:0%d" % (index % 10)))
        return items

    items = entries(5, "recall", recall_result)
    raw = serialize_entries(items)
    folded = serialize_entries(items, fold_keep=2)
    check(len(folded) < len(raw), "折叠后记录应更短")
    check(folded.count("（已折叠）") == 3, "5 条里应折 3 条，实际 %d"
          % folded.count("（已折叠）"))
    check(folded.count("找到 3 件：\n") == 2, "最近两条原文该留着")
    # 折了也要留下「哪几件、什么时候」：id、名字、日期。
    check("ev0 某件旧事（10-20）、ev1 某件旧事（10-21）、ev2 某件旧事（10-22）" in folded,
          "折起来的那行要留着 id、名字和日期：%s" % folded[:100])
    check(folded.count("调用 recall") == 5, "调用行不能被折：当时问的还在")
    # 写给模型的提示语只留最新两条，历史里的去掉。
    check(folded.count("看具体用 recollect") == 2, "提示语只该留最新两条，实际 %d"
          % folded.count("看具体用 recollect"))
    check(serialize_entries(items, fold_keep=0) == raw, "fold_keep=0 就是不折")

    # 短结果也该按新旧去提示语：「没找到」留着，后半句去掉。
    empty = "没有找到。换个关键词再试。"
    misses = entries(3, "recall", empty)
    miss_text = serialize_entries(misses, fold_keep=1)
    check("没有找到。" in miss_text, "没找到这件事要留着")
    check(miss_text.count("换个关键词再试") == 1, "换关键词那句只该留最新一条")

    # recollect 是记忆本身：一次都不折。
    memories = entries(5, "recollect", memory_text)
    check(serialize_entries(memories, fold_keep=1).count("（已折叠）") == 0,
          "recollect 的结果不该折")
    check(memory_text in serialize_entries(memories, fold_keep=1),
          "recollect 的结果要原样留着")

    short = entries(4, "remember", "已记：某事（ev1，long）")
    check(serialize_entries(short, fold_keep=1).count("（已折叠）") == 0,
          "短结果（回执、报错）不该折")

    # 压缩读的是原文，不受折叠影响。
    block = Block(entries(3, "recall", recall_result),
                  "2026-10-25 00:00:00", "2026-10-25 00:10:00")
    result = Compressor(_loop_config(name="自检"),
                        summarizer=fake_summarizer).compress_block(block)
    check(result["summary"] == "现在：在自检。", "压缩仍应正常工作")

    # 挂到上下文上：渲染时折叠，估算也跟着变小。
    config = _loop_config(name="自检", tool_fold_keep=1)
    context = Context(config)
    context.current.entries = entries(4, "recall", recall_result)
    check(context.current_text().count("（已折叠）") == 3, "上下文渲染应按设置折叠")
    plain = Context(_loop_config(name="自检", tool_fold_keep=0))
    plain.current.entries = entries(4, "recall", recall_result)
    check("（已折叠）" not in plain.current_text(), "关掉就不折")
    check(context.current_tokens() < plain.current_tokens(), "折了之后该更省")


def check_speak_with_calls() -> None:
    """有工具调用的那条回复里，正文也得投递——不然模型以为说了，人没听到。"""
    workdir = tempdir("loop-both-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=1,
                            park_seconds=0.001)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=BothClient())
        spoken = []
        loop.on_speak = spoken.append
        loop.run()
        check(spoken == ["我先查一下。"], "带工具调用的正文也要投递：%r" % spoken)
        text = loop.context.block_text()
        check("我先查一下。" in text, "正文该进记录")
        check("调用 recall" in text, "调用也该进记录")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_wait_text() -> None:
    """时间写法两种：相对用「等了多久」回，钟点用钟点回。"""
    moment = 1_000_000.0
    check(abs(parse_until("90s", moment) - (moment + 90)) < 0.01, "90s 应是 90 秒后")
    check(abs(parse_until("2h", moment) - (moment + 7200)) < 0.01, "2h 应是两小时后")
    check(abs(parse_until("10", moment) - (moment + 10)) < 0.01, "光数字按秒")
    check(parse_until("", moment) is None, "空串是无上界")
    for bad in ("明天", "10x", "25:00"):
        try:
            parse_until(bad, moment)
        except ValueError:
            continue
        raise AssertionError("看不懂的时间应报错：%r" % bad)

    clock = parse_until("14:30", moment)
    check(clock > moment, "钟点该落在将来")
    check(clock - moment <= 86400 + 1, "钟点最晚一天之内")

    check(_timeout_text("10m", 600) == "等了 10 分钟，到点了，没等到。",
          "相对写法回「等了多久」")
    check(_timeout_text("14:30", 600) == "等到 14:30，到点了，没等到。",
          "钟点写法回钟点")
    check(_woke_text("user", 3) == "等了 3 秒，对方说话了。", "被消息叫醒的写法")
    check(_woke_text("task", 90) == "等了 2 分钟，task 回来了。", "被任务叫醒的写法")


def check_wait_runtime() -> None:
    """真等：消息先到就醒，到点就报没等到。"""
    workdir = tempdir("loop-wait-")
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, standby=True,
                            data_dir=workdir)
        store = LoopStore(db_path)
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())

        def push():
            # 另开一条连接，像别的进程往 inbox 里写。
            writer = LoopStore(db_path)
            writer.push("user", "在吗")
            writer.close()

        timer = threading.Timer(0.05, push)
        timer.start()
        text = loop._wait('{"until": "5s"}')
        timer.join()
        check("对方说话了" in text, "该被消息叫醒：%r" % text)
        check(len(loop._after_tool) == 1, "醒来的消息要补在结果后面")
        loop._flush_after_tool()
        check("对方：在吗" in loop.context.current_text(), "消息该写成 `对方：`")

        timeout = loop._wait('{"until": "0s"}')
        check("到点了" in timeout, "到点要报没等到：%r" % timeout)

        kinds = [tool["function"]["name"] for tool in loop.tools()]
        check("wait" in kinds, "工具面里该有 wait：%r" % kinds)
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_wait_resume() -> None:
    """重启时块里挂着没结果的 wait：接着等，醒了补上结果。"""
    workdir = tempdir("loop-resume-")
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, standby=True,
                            data_dir=workdir)
        store = LoopStore(db_path)
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        loop._append({"role": "assistant", "content": "",
                      "tool_calls": [{"id": "w1", "type": "function",
                                      "function": {"name": "wait",
                                                   "arguments": "{\"until\": \"5s\"}"}}]})
        store.push("user", "还在吗")

        loop._resume_waits()
        text = loop.context.current_text()
        check("结果：等了" in text, "重启该补上等待结果：%s" % text)
        check("对方：还在吗" in text, "醒来的消息要进记录：%s" % text)

        # 已经有过结果的 wait 不该重跑。
        loop._resume_waits()
        check(loop.context.current_text().count("结果：等了") == 1,
              "有结果的 wait 不该重跑")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class TwoWaitsClient(FakeClient):
    """一个回复里调两次 wait：第二个不该执行，否则就一直挂着了。"""

    @staticmethod
    def _reply(number: int):
        if number == 0:
            return "", [
                {"id": "w1", "type": "function",
                 "function": {"name": "wait", "arguments": "{\"until\": \"5s\"}"}},
                {"id": "w2", "type": "function",
                 "function": {"name": "wait", "arguments": "{\"until\": \"5s\"}"}},
            ]
        return "", None


def check_one_blocking_call() -> None:
    """阻塞的工具一个回复只能调一个；多出来的拒掉，并说清怎么办。"""
    workdir = tempdir("loop-blocking-")
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, standby=True,
                            max_steps=1, data_dir=workdir)
        store = LoopStore(db_path)
        store.push("user", "在吗")
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=TwoWaitsClient())
        loop.on_speak = lambda text: None
        loop.run()
        results = [entry.message.get("content") for entry in loop.context.current.entries
                   if str(entry.message.get("role") or "") == "tool"]
        check(any(str(text).startswith("等了") for text in results),
              "第一个 wait 该执行：%r" % results)
        check(any("一次只能调一个 wait" in str(text) for text in results),
              "第二个 wait 该被拒：%r" % results)
        check(sum(1 for text in results if "一次只能调一个" in str(text)) == 1,
              "只该拒一次：%r" % results)
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_input_line() -> None:
    """管道里的 BOM、回车要去掉：PowerShell 的 UTF-8 管道会加 BOM。"""
    check(_clean_line("\ufeff第一条\r\n") == "第一条", "BOM 和回车要去掉")
    check(_clean_line("  \t") == "", "空白行不算消息")
    check(_clean_line(None) == "", "空值不炸")


class FlakyClient(FakeClient):
    """前几次连不上，之后正常：长跑里不该因此死掉。"""

    def __init__(self, fail_times: int = 1):
        super().__init__()
        self.fail_times = fail_times

    def chat(self, messages, tools=None, on_delta=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ChatError("连不上")
        return super().chat(messages, tools, on_delta)


def check_retry() -> None:
    """模型调不通就一直试，循环不能因此停。"""
    workdir = tempdir("loop-retry-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=1,
                              retry_delay=0.01)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FlakyClient(1))
        logs = []
        loop.echo = logs.append
        loop.on_speak = lambda text: None
        loop.run()
        check(loop.steps == 1, "重试之后该跑完这一步：%d" % loop.steps)
        check(loop.retries == 1, "该记一次重试：%d" % loop.retries)
        check(any("重试" in line for line in logs), "重试要报到运行时输出")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_watchdog() -> None:
    """连着几步没有外界动静：当场提一句，不硬停；它不进历史。"""
    workdir = tempdir("loop-watchdog-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, watchdog_idle=2,
                              max_steps=5, park_seconds=0.001)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        client = FakeClient()
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=client)
        logs = []
        loop.echo = logs.append
        loop.on_speak = lambda text: None
        loop.run()
        check(loop.steps == 5, "报警不该把循环停掉：%d 步" % loop.steps)
        check(any("提醒" in line for line in logs), "报警要报到输出")
        alarms = [entry for entry in loop.context.current.entries
                  if entry.meta.get("kind") == "watchdog"]
        check(len(alarms) >= 1, "报警要落成提醒记录")
        check(any("没有外界的动静" in prompt for prompt in client.prompts),
              "提醒要在注入的那一步出现在输入里")
        check("没有外界的动静" not in loop.context.block_text(),
              "提醒不该进历史（它是当时的提醒，不是发生过的事）")

        loop.since_external = 5
        loop.inject_user("在吗")
        check(loop.since_external == 0, "外部消息要把计数清零")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_archive() -> None:
    """短期记忆过期：压成一行搬进 archive.jsonl，图里删掉、召回不到。"""
    workdir = tempdir("loop-archive-")
    try:
        data_dir = os.path.join(workdir, "memory")
        bridge = MemoryBridge(data_dir, lexical=True)
        bridge.call("remember", json.dumps(
            {"name": "过期的短事", "note": "这件事只留一天。后一句不该留。",
             "scope": "short", "days": -1}, ensure_ascii=False))
        event_id = next(iter(bridge.graph.events))

        bridge.archiver = lambda material: {event_id: "压成一行了。"}
        check(bridge.archive_expired() == 1, "该归档一件")
        check(event_id not in bridge.graph.events, "归档之后图里不该再有它")
        gone = bridge.call("recall", json.dumps(
            {"keywords": ["过期的短事"]}, ensure_ascii=False))
        check("过期的短事" not in gone, "归档之后不该再召回：%s" % gone)
        check(bridge.archive_expired() == 0, "归档要幂等")

        archive = os.path.join(data_dir, "archive.jsonl")
        check(os.path.exists(archive), "要写出 archive.jsonl")
        with open(archive, encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        check(len(lines) == 1, "归档文件该有一行：%r" % lines)
        check(json.loads(lines[0])["text"] == "压成一行了。", "归档要留压好的那一行")

        # 压不动的时候用规则压缩：留第一句，别丢。
        bridge.call("remember", json.dumps(
            {"name": "另一件短事", "note": "结论是二十八万。第二句不要。",
             "scope": "short", "days": -1}, ensure_ascii=False))
        bridge.archiver = None
        check(bridge.archive_expired() == 1, "第二件也该归档")
        with open(archive, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        check(rows[-1]["text"] == "结论是二十八万",
              "规则压缩留第一句：%r" % rows[-1]["text"])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_usage_report() -> None:
    """仪表盘：按天汇总、命中率、合计。"""
    rows = [
        {"ts": "2026-10-24 10:00:00", "prompt": 1000, "completion": 100, "cache_hit": 800},
        {"ts": "2026-10-24 10:05:00", "prompt": 1200, "completion": 200, "cache_hit": 900},
        {"ts": "2026-10-25 09:00:00", "prompt": 2000, "completion": 300, "cache_hit": 1500},
    ]
    days = collect(rows)
    check(sorted(days) == ["2026-10-24", "2026-10-25"], "该按天分组：%r" % sorted(days))
    check(days["2026-10-24"]["steps"] == 2, "24 号两步")
    check(days["2026-10-24"]["prompt"] == 2200, "24 号输入要累加")
    check(days["2026-10-24"]["peak"] == 1200, "峰值取当天最大")
    check(abs(days["2026-10-25"]["hit_rate"] - 0.75) < 0.001, "命中率按输入算")

    text = render(days)
    check("2026-10-24" in text and "合计" in text, "表格要有日期和合计")
    check("75%" in text, "命中率要显示成百分比：%s" % text)
    check(render({}) == "还没有用量：库是空的。", "空库要有话说")
    check(render(days, limit=1).count("2026-10-24") == 0, "limit 只留最近几天")


def check_recall_log() -> None:
    """每次 recall 记一行：以后调检索质量要用。"""
    workdir = tempdir("loop-recalllog-")
    try:
        seen = []
        bridge = MemoryBridge(os.path.join(workdir, "memory"), lexical=True)
        bridge.on_recall = lambda question, ids: seen.append((question, ids))
        bridge.call("remember", json.dumps(
            {"name": "预算二十八万", "note": "定下了。"}, ensure_ascii=False))
        bridge.call("recall", json.dumps({"keywords": ["预算"]}, ensure_ascii=False))
        check(len(seen) == 1, "recall 该回调一次：%r" % seen)
        check(seen[0][0] == "预算", "问题要记下：%r" % (seen[0],))
        check(len(seen[0][1]) >= 1, "命中的 id 要记下：%r" % (seen[0],))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_inbox_trim() -> None:
    """进件表只留最近几条已读的，别一直涨。"""
    workdir = tempdir("loop-inbox-")
    try:
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        for index in range(5):
            store.push("user", "第 %d 条" % index)
            store.take()
        removed = store.trim_inbox(keep=1)
        check(removed == 4, "该删掉 4 条已读：%d" % removed)
        check(store.take() is None, "已读的都被删掉了，取不到东西")
        store.push("user", "还没读")
        store.trim_inbox(keep=0)
        item = store.take()
        check(item is not None and item["text"] == "还没读", "没读的要留着")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_prompts() -> None:
    """提示词：注册表要覆盖所有文件；覆盖文件优先于仓库默认。"""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
    on_disk = sorted(name[:-4] for name in os.listdir(folder) if name.endswith(".txt"))
    check(sorted(prompt_files.META) == on_disk,
          "prompts/ 和注册表要对得上：目录 %r，注册表 %r"
          % (on_disk, sorted(prompt_files.META)))
    for key in prompt_files.META:
        check(prompt_files.text(key).strip(), "每份提示词都要有内容：%s" % key)

    workdir = tempdir("loop-prompts-")
    try:
        path = os.path.join(workdir, "prompts.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"sink": "我是$name。覆盖版。"}, handle, ensure_ascii=False)
        prompt_files.use_overrides(workdir)
        check(prompt_files.text("sink") == "我是$name。覆盖版。", "覆盖要优先")
        check(prompt_files.render("sink", name="自检") == "我是自检。覆盖版。",
              "覆盖版也要能填占位符")
        check(prompt_files.text("compress").strip(), "没覆盖的仍读仓库默认")
        unknown = os.path.join(workdir, "prompts.json")
        with open(unknown, "w", encoding="utf-8") as handle:
            json.dump({"没有这个键": "xxx"}, handle, ensure_ascii=False)
        prompt_files.use_overrides(workdir)
        check(prompt_files.overrides() == {}, "不认识的键要忽略")
    finally:
        prompt_files.use_overrides("")
        shutil.rmtree(workdir, ignore_errors=True)


def check_budget() -> None:
    """成本上限：撞到上限就停（没有输入通道时），撞到提醒线只提一句。"""
    workdir = tempdir("loop-budget-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=5,
                              park_seconds=0.001, daily_token_cap=1)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        logs = []
        loop.echo = logs.append
        loop.on_speak = lambda text: None
        loop.run()
        check(loop.steps == 1, "第一步之后就该撞上限：%d 步" % loop.steps)
        check(any("上限" in line for line in logs), "撞上限要报到输出")
        check(loop.parked, "没有输入通道时撞上限就停住")
        used = store.usage_today()
        check(used["total"] > 0, "今天的用量要算得出来：%r" % used)

        warn_dir = tempdir("loop-warn-")
        try:
            config2 = _loop_config(name="自检", block_tokens=100000, max_steps=2,
                                   park_seconds=0.001, daily_token_warn=1)
            store2 = LoopStore(os.path.join(warn_dir, "loop.sqlite3"))
            loop2 = Loop(config2, credentials=None, store=store2,
                         compressor=Compressor(config2, summarizer=fake_summarizer),
                         memory=MemoryBridge.disabled(), client=FakeClient())
            logs2 = []
            loop2.echo = logs2.append
            loop2.on_speak = lambda text: None
            loop2.run()
            check(loop2.steps == 2, "提醒线不该停循环：%d 步" % loop2.steps)
            check(any("提醒线" in line for line in logs2), "过提醒线要提一句")
            store2.close()
        finally:
            shutil.rmtree(warn_dir, ignore_errors=True)
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_web() -> None:
    """网页面：没令牌就 401；说一句能进 inbox；流与上下文读得出东西。"""
    import urllib.error
    import urllib.request

    workdir = tempdir("loop-web-")
    httpd = None
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, standby=True,
                              data_dir=workdir)
        store = LoopStore(db_path)
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        httpd = web.serve(config, db_path, "tok", "127.0.0.1", 0)
        base = "http://127.0.0.1:%d" % httpd.server_address[1]

        def get(path, token="tok"):
            request = urllib.request.Request(base + path)
            if token:
                request.add_header("Authorization", "Bearer " + token)
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())

        def post(path, payload, token="tok"):
            data = json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(base + path, data=data, method="POST")
            request.add_header("Content-Type", "application/json")
            if token:
                request.add_header("Authorization", "Bearer " + token)
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())

        try:
            get("/state", token="")
        except urllib.error.HTTPError as exc:
            check(exc.code == 401, "没令牌该 401，拿到 %d" % exc.code)
        else:
            raise AssertionError("没令牌也放过去了")

        status, _ = post("/say", {"text": "在吗"})
        check(status == 200, "说一句该成功")
        loop.step()                      # 把 inbox 里那句读进记录
        _, state = get("/state")
        check(state["name"] == "自检", "状态里该有名字")
        check(state["steps"] >= 1, "状态里该有步数：%r" % state["steps"])
        check(state["block_tokens"] == 100000, "状态里该有块限额")
        check("kind" in state["status"], "状态里该有它现在在干什么")
        check(state["models"]["chat"] == config.model, "状态里该报对话模型")
        check(state["models"]["summary"] == config.summary_model, "状态里该报摘要模型")
        check(state["models"]["embed"] == config.embed_model, "状态里该报嵌入模型")

        with urllib.request.urlopen(base + "/", timeout=5) as response:
            page = response.read().decode("utf-8")
        check('id="gutter"' in page, "页面里该有行号槽")
        check("data-theme" in page and 'id="theme"' in page, "页面里该有主题切换")
        check('id="picker"' in page, "页面里该有选模型的弹窗")
        check('id="mirror"' in page, "页面里该有量行数的镜像墙")

        _, feed = get("/feed?since=0:0")
        check(any("在吗" in item["text"] for item in feed["entries"]),
              "流里该有刚说的话")
        check(any(item["step"] >= 1 for item in feed["entries"]),
              "流里每条要带步号")
        check(feed["cursor"]["seq"] >= 1, "游标要往前走")

        _, context = get("/context")
        check(context["current"]["count"] >= 1, "上下文里该有当前块")
        check(context["sink"]["chars"] > 0, "上下文里该有 sink")
        check(context["limits"]["block_tokens"] == 100000, "上下文里该有块限额")
        check("bands" in context and "previous" in context, "上下文里该有旧段与上一块")

        cursor = "%d:%d" % (feed["cursor"]["block"], feed["cursor"]["seq"])
        _, more = get("/feed?since=" + cursor)
        check(more["entries"] == [], "游标之后不该重复给同一条")
        store.close()
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


def check_settings() -> None:
    """设置页：能读、能写、越界拒绝；改提示词不重启就生效；重启是请求不是硬杀。"""
    import urllib.error
    import urllib.request

    workdir = tempdir("loop-set-")
    httpd = None
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, data_dir=workdir)
        LoopStore(db_path).close()
        prompt_files.use_overrides(workdir)
        httpd = web.serve(config, db_path, "tok", "127.0.0.1", 0, data_dir=workdir)
        base = "http://127.0.0.1:%d" % httpd.server_address[1]

        def call(path, payload=None, method="GET"):
            data = None if payload is None else json.dumps(payload).encode("utf-8")
            request = urllib.request.Request(base + path, data=data, method=method)
            request.add_header("Authorization", "Bearer tok")
            if data is not None:
                request.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read())

        payload = call("/settings")
        check(len(payload["fields"]) > 10, "设置里该有旋钮清单")
        check(payload["values"]["block_tokens"] == 100000, "设置里该有当前值")
        check(any(item["key"] == "sink" for item in payload["prompts"]),
              "设置里该有提示词")

        fields = {item["key"]: item for item in payload["fields"]}
        check("embed_model" in fields and fields["embed_model"]["group"] == "记忆",
              "设置里该有嵌入模型")
        check(fields["embed_model"].get("picker") == "embed", "嵌入模型该能从列表挑")
        check(fields["model"].get("picker") == "chat", "主模型该能从列表挑")
        check(fields["summary_provider"]["type"] == "select", "摘要端点该是下拉")
        check(payload["memory"]["embed_dim"] > 0, "设置里该报嵌入维度")
        check(call("/models?kind=chat")["ok"] is False,
              "没凭证时拉模型列表该老实说不行，而不是 500")

        call("/settings", {"values": {"summary_provider": "bailian"}}, "PUT")
        check(config.summary_provider == "bailian", "下拉的值要能存")
        try:
            call("/settings", {"values": {"summary_provider": "瞎写"}}, "PUT")
        except urllib.error.HTTPError as exc:
            check(exc.code == 400, "下拉的野值该 400，拿到 %d" % exc.code)
        else:
            raise AssertionError("下拉的野值也存进去了")

        # 换嵌入模型：得当场重建嵌入器（它是造好就带着模型名的）。
        import types
        made = []

        def factory():
            made.append(1)
            return types.SimpleNamespace(available=True)

        httpd.loop = types.SimpleNamespace(
            memory=types.SimpleNamespace(embedder=None), client=None)
        httpd.embed_factory = factory
        call("/settings", {"values": {"embed_model": "qwen3.7-text-embedding-flash",
                                      "embed_dim": 2048}}, "PUT")
        check(config.embed_model == "qwen3.7-text-embedding-flash",
              "嵌入模型该立刻生效")
        check(config.embed_dim == 2048, "嵌入维度该立刻生效")
        check(made and httpd.loop.memory.embedder is not None,
              "换了嵌入模型该当场换个嵌入器")

        call("/settings", {"values": {"block_tokens": 5000}}, "PUT")
        check(settings.load(workdir).get("block_tokens") == 5000, "改的值要落盘")
        check(config.block_tokens == 5000, "存了就该立刻生效，不用等重启")

        try:
            call("/settings", {"values": {"block_tokens": 1}}, "PUT")
        except urllib.error.HTTPError as exc:
            check(exc.code == 400, "越界该 400，拿到 %d" % exc.code)
        else:
            raise AssertionError("越界的值也存进去了")

        call("/prompts", {"key": "sink", "text": "我是测试。"}, "PUT")
        check(prompt_files.text("sink") == "我是测试。", "改提示词该立刻生效")
        call("/prompts", {"key": "sink", "text": ""}, "PUT")
        check("我是$name" in prompt_files.text("sink"), "清空该回到默认")

        call("/restart", {}, "POST")
        check(control.stop_requested(), "重启是给循环一个停止请求")
        control.reset()
        check(not control.stop_requested(), "重置之后不该还停着")
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        prompt_files.use_overrides("")
        shutil.rmtree(workdir, ignore_errors=True)


def check_boot_idle() -> None:
    """刚起来没人说话：先等着，不许自己开口；有人说了才走第一步。"""
    workdir = tempdir("loop-boot-")
    timer = None
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, standby=True,
                              data_dir=workdir)
        store = LoopStore(db_path)
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())

        def push():
            writer = LoopStore(db_path)
            writer.push("user", "在吗")
            writer.close()

        timer = threading.Timer(0.25, push)
        timer.start()
        started = time.time()
        loop.run(max_steps=1)
        elapsed = time.time() - started
        timer.join()

        check(loop.steps == 1, "起来之后只该走被叫醒的那一步：%d" % loop.steps)
        check(elapsed >= 0.2, "该先等到有人说话（等了 %.2f 秒）" % elapsed)
        check("对方：在吗" in loop.context.current_text(), "叫醒它的那句该进记录")
        store.close()
    finally:
        if timer is not None:
            timer.cancel()
        shutil.rmtree(workdir, ignore_errors=True)


def check_delete() -> None:
    """删一条原文：连它的工具结果一起删；压过的块不给删。"""
    workdir = tempdir("loop-del-")
    try:
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        block_id = store.open_block("2026-01-01 00:00:00")
        call = {"id": "c1", "type": "function",
                "function": {"name": "wait", "arguments": "{}"}}
        store.append_entry(block_id, BlockEntry(
            {"role": "user", "content": "在吗"}, "2026-01-01 00:00:01"), step=1)
        store.append_entry(block_id, BlockEntry(
            {"role": "assistant", "content": "", "tool_calls": [call]},
            "2026-01-01 00:00:02"), step=1)
        store.append_entry(block_id, BlockEntry(
            {"role": "tool", "tool_call_id": "c1", "content": "好"},
            "2026-01-01 00:00:03"), step=1)
        check(len(store.feed(0, 0)) == 3, "三条都该在")

        removed = store.delete_entry(block_id, 2)
        check(sorted(removed) == [2, 3], "删调用要连结果一起删：%r" % removed)
        left = store.feed(0, 0)
        check(len(left) == 1 and left[0]["text"] == "在吗", "只该剩用户那句")

        # 用量分两份：live 只算还挂在记录上的步（网页放在输入框下面，删了就退），
        # totals 是 usage 表全部（网页只在设置里当总账）。
        store.add_usage(1, {"prompt_tokens": 100, "completion_tokens": 10,
                            "prompt_cache_hit_tokens": 50})
        store.add_usage(2, {"prompt_tokens": 200, "completion_tokens": 20,
                            "prompt_cache_hit_tokens": 100})
        live = store.usage_live()
        check(live["steps"] == 1 and live["total"] == 110,
              "底下的数只算还挂着的那一步：%r" % live)
        check(store.usage_totals()["steps"] == 2, "总账里两步都得留着")
        store.append_entry(block_id, BlockEntry(
            {"role": "user", "content": "第二步"}, "2026-01-01 00:00:04"), step=2)
        check(store.usage_live()["steps"] == 2, "有了第二步的条目就该算上它")
        store.delete_entry(block_id, max(e["seq"] for e in store.feed(0, 0)))
        check(store.usage_live()["steps"] == 1, "把第二步删掉，底下的数要退回去")
        check(store.usage_totals()["total"] == 330, "总账不退：钱已经花了")

        with store.db:
            store.db.execute("UPDATE blocks SET state = 'compressed' WHERE id = ?",
                             (block_id,))
        try:
            store.delete_entry(block_id, 1)
        except ValueError:
            check(True, "")
        else:
            raise AssertionError("压过的块不该允许删原文")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class TalkOnlyClient(FakeClient):
    """只会说话、永远不调工具：问它「还要继续吗」也还是说话。"""

    def __init__(self):
        super().__init__()
        self.asked = []

    def chat(self, messages, tools=None, on_delta=None):
        tail = str(messages[-1].get("content") or "")
        self.asked.append("还要继续吗" in tail)
        return super().chat(messages, tools, on_delta)

    @staticmethod
    def _reply(number: int):
        return "第 %d 句。" % number, None


class WaitAfterAskClient(FakeClient):
    """说完一句，被问「还要继续吗」就调 wait 停下。"""

    def __init__(self):
        super().__init__()
        self.asked = 0

    def chat(self, messages, tools=None, on_delta=None):
        tail = str(messages[-1].get("content") or "")
        if "还要继续吗" in tail:
            self.asked += 1
            self.calls += 1
            self.last_usage = {"prompt_tokens": 10, "completion_tokens": 1}
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"id": "wait-1", "type": "function",
                                    "function": {"name": "wait",
                                                 "arguments": "{\"until\": \"1s\"}"}}]}
        return super().chat(messages, tools, on_delta)

    @staticmethod
    def _reply(number: int):
        return "说完了。", None


def check_ask() -> None:
    """说了话、没调工具：先问一次要不要继续；问了还只说话就停。"""
    context = Context(_loop_config(name="自检"))
    context.current = Block([
        BlockEntry({"role": "user", "content": "在吗"}, "2026-10-25 15:00:00"),
        BlockEntry({"role": "assistant", "content": "在。"}, "2026-10-25 15:00:05"),
    ], "2026-10-25 15:00:00", "2026-10-25 15:00:05")
    plain = context.messages()[-1]["content"]
    check("还要继续吗" not in plain, "不问的时候不该挂这句")
    context.asking = True
    asked = context.messages()[-1]["content"]
    check(asked.rstrip().endswith("</system-reminder>"),
          "问了就该在末尾那一帧里：%r" % asked[-60:])
    check("还要继续吗" in asked, "问的话要写清在问什么")
    check("还要继续吗" not in context.block_text(), "提问不该进记录")

    # 只会说话：说一句 → 问一次 → 还只说 → 停。
    workdir = tempdir("loop-ask-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=6)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        client = TalkOnlyClient()
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=client)
        loop.echo = lambda line: None
        loop.on_speak = lambda text: None
        loop.inject_user("在吗")
        loop.step()                       # 接话 → 说了，没调工具
        check(client.asked == [False], "头一步不该自问自答")
        loop.step()                       # 问了它一句
        check(client.asked == [False, True], "说了话没调工具，下一步该问")
        loop.step()                       # 还是只说话 → 停下
        check(client.asked.count(True) == 1, "只问一次，不连问：%r" % client.asked)
        check(loop.steps == 3, "三步之后该停：%d" % loop.steps)
        check(loop.parked, "问了还只说话就该挂起")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # 问它，它就调 wait：停得干净，记录里看得见这个决定。
    workdir = tempdir("loop-ask-wait-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, standby=True,
                              max_steps=6)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        client = WaitAfterAskClient()
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=client)
        loop.echo = lambda line: None
        loop.on_speak = lambda text: None
        loop.inject_user("在吗")
        loop.step()                       # 说了话
        loop.step()                       # 被问一句 → 它调 wait(1s) 停下
        check(client.asked == 1, "该问它一次：%d" % client.asked)
        check("调用 wait" in loop.context.block_text(), "停下这个决定要进记录")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # 问之前正好有人说话：这一步该接话，不该问要不要继续。
    workdir = tempdir("loop-ask-input-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=6)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        client = TalkOnlyClient()
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=client)
        loop.echo = lambda line: None
        loop.on_speak = lambda text: None
        loop.inject_user("在吗")
        loop.step()                       # 说了话
        store.push("user", "我再说一句")   # 下一步开始前就有新话
        loop.step()
        check(client.asked == [False, False], "有新话进来就不该问要不要继续")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_record_marks() -> None:
    """谁说的由角色分：对方是 user，自己是 assistant，系统提醒单立一条 user。

    这条是服务器上真出过的事：整段记录塞进一条 user 消息、只标「对方：」，自己那几段
    没标，它就把自己刚问的那句当成对方问的，顺着答了一遍。
    """
    entries = [
        BlockEntry({"role": "user", "content": "在吗"}, "2026-10-25 15:00:00"),
        BlockEntry({"role": "assistant", "content": "在。"}, "2026-10-25 15:00:05"),
        BlockEntry({"role": "assistant", "content": "",
                    "tool_calls": [{"id": "c1", "type": "function",
                                    "function": {"name": "wait",
                                                 "arguments": "{\"until\": \"10m\"}"}}]},
                   "2026-10-25 15:00:06"),
        BlockEntry({"role": "tool", "tool_call_id": "c1", "content": "好"},
                   "2026-10-25 15:00:07"),
        BlockEntry({"role": "note", "content": "任务回来了"},
                   "2026-10-25 15:00:08", {"kind": "task"}),
        BlockEntry({"role": "note", "content": "已经连着 7 步没有外界的动静了。"},
                   "2026-10-25 15:00:09", {"kind": "watchdog"}),
    ]
    context = Context(_loop_config(name="自检"))
    context.current = Block(entries, "2026-10-25 15:00:00", "2026-10-25 15:00:09")
    messages = context.messages()
    roles = [m["role"] for m in messages]
    check(roles[0] == "system", "第一条是 system")
    check(roles[1] == "user" and "在吗" in messages[1]["content"],
          "对方的话是 user：%r" % messages[1])
    check(roles[2] == "assistant" and "在。" in messages[2]["content"],
          "自己的话是 assistant：%r" % messages[2])
    check("调用 wait" in messages[2]["content"] and "结果：好" in messages[2]["content"],
          "调用和结果并进它自己那条：%r" % messages[2]["content"])
    check(roles[3] == "system" and "任务回来了" in messages[3]["content"],
          "系统动静走 system：%r" % messages[3])
    check(messages[3]["content"].startswith("<system-reminder>"),
          "系统注入要套标签：%r" % messages[3]["content"])
    check(not any("没有外界的动静" in str(m["content"]) for m in messages),
          "心跳提醒不该进上下文（它是当时的提醒，不是发生过的事）")
    joined = "\n".join(str(m["content"]) for m in messages[1:])
    check("我：" not in joined and "对方：" not in joined,
          "原生消息里不该有前缀：%r" % joined[:80])
    check(messages[-1]["role"] == "user" and "现在 10-25" in messages[-1]["content"],
          "墙钟是末尾单独那一帧（带 tools 时端点不接受末尾 system）")
    check(messages[-1]["content"].startswith("<system-reminder>"),
          "末尾那帧也要套标签：%r" % messages[-1]["content"])
    check(roles.count("user") == 2, "user 只有两条：对方的话 + 末尾那帧：%r" % roles)

    # 纯文本渲染（压缩模型看的）还是要标：它没有 role 可读。
    text = serialize_entries(entries)
    check("对方：在吗" in text and "我：在。" in text, "纯文本要标两边：%r" % text)
    check("提醒：" in text, "纯文本要标系统提醒：%r" % text)
    check("\n\n" not in text, "纯文本里一条话不留空行，免得下一段像别人接的")


def check_workspace() -> None:
    """工作区：write 只能写 <data>/workspace，绝对路径和 .. 都拒；run 拒读凭证。"""
    workdir = tempdir("loop-work-")
    try:
        cfg = _loop_config(name="自检", data_dir=workdir)
        cfg.write_enabled = False
        check(not workspace.tools(cfg), "关着就工具面里没有 write")
        cfg.write_enabled = True
        names = [tool["function"]["name"] for tool in workspace.tools(cfg)]
        check(names == ["write"], "开着才有 write：%r" % names)

        out = workspace.call("write", json.dumps(
            {"path": "草稿/a.md", "content": "你好"}), cfg)
        check("写好了" in out, "该写进去：%r" % out)
        target = os.path.join(workspace.root_of(cfg), "草稿", "a.md")
        check(os.path.exists(target), "文件该在：%s" % target)
        with io.open(target, encoding="utf-8") as handle:
            check(handle.read() == "你好", "内容要对")
        out = workspace.call("write", json.dumps(
            {"path": "草稿/a.md", "content": "再一句", "mode": "append"}), cfg)
        check("追加" in out, "append 该追加：%r" % out)
        with io.open(target, encoding="utf-8") as handle:
            check(handle.read() == "你好再一句", "追加内容要对")

        for bad in ("/etc/passwd", "../x", "a/../../b", "C:\\x", ""):
            out = workspace.call("write", json.dumps(
                {"path": bad, "content": "x"}), cfg)
            check(any(word in out for word in ("只写工作区", "不要有", "空的",
                                               "要给一个路径")),
                  "该拒：%r -> %r" % (bad, out))
        cfg.write_max_chars = 4
        out = workspace.call("write", json.dumps(
            {"path": "big.txt", "content": "12345"}), cfg)
        check("太长" in out, "超上限要拒：%r" % out)

        run_cfg = _loop_config(name="自检", data_dir=workdir)
        run_cfg.run_enabled = True
        run_cfg.run_allow = "cat ls"
        locked = shell.call("run", json.dumps(
            {"command": "cat /etc/palimpsest/credentials.yaml"}), run_cfg)
        check("不给读" in locked, "凭证该拒：%r" % locked)
        key = shell.call("run", json.dumps(
            {"command": "ls server.pem"}), run_cfg)
        check("不给读" in key, "密钥该拒：%r" % key)
        plain = shell.call("run", json.dumps(
            {"command": "cat loop/README.md"}), run_cfg)
        check("不给读" not in plain, "普通文件不该拒：%r" % plain)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_stop_raises() -> None:
    """要重启时直接在等里退出：记录里一个字都不留。"""
    workdir = tempdir("loop-stop-")
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, standby=True,
                              data_dir=workdir)
        store = LoopStore(db_path)
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        control.request_stop()
        try:
            loop._wait('{"until": "5s"}')
        except control.StopRequested:
            check(True, "")
        else:
            raise AssertionError("要重启时该直接退出，不该返回一句话")
        check(loop.context.current.entries == [], "停的时候不该往记录里写东西")
        control.reset()
        store.close()
    finally:
        control.reset()
        shutil.rmtree(workdir, ignore_errors=True)


def check_reload() -> None:
    """网页删了一条：不重启，循环在下个安全点自己重读。"""
    workdir = tempdir("loop-reload-")
    try:
        db_path = os.path.join(workdir, "loop.sqlite3")
        config = _loop_config(name="自检", block_tokens=100000, data_dir=workdir)
        store = LoopStore(db_path)
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=MemoryBridge.disabled(), client=FakeClient())
        loop.inject_user("在吗")
        check("在吗" in loop.context.current_text(), "先得有一条")
        store.delete_entry(loop.current_id, 1)
        control.request_reload()
        loop._maybe_reload()
        check("在吗" not in loop.context.current_text(), "重读之后记录里就不该有它")
        check(not control.reload_requested(), "重读一次之后开关要复位")
        store.close()
    finally:
        control.clear_reload()
        shutil.rmtree(workdir, ignore_errors=True)


def check_web_tools() -> None:
    """上网工具：闸门、解析、分页；真抓网络留给服务器上验。"""
    bad = ("file:///etc/passwd", "http://127.0.0.1/", "http://10.0.0.1/",
           "http://localhost:80/", "http://example.com:8080/",
           "http://user:pass@example.com/", "ftp://example.com/")
    for url in bad:
        try:
            web_access.validate_url(url)
        except web_access.WebAccessError:
            check(True, "")
        else:
            raise AssertionError("该拒绝的没拒绝：%s" % url)

    check(web_access.validate_url("http://93.184.216.34/a?b=1").endswith("?b=1"),
          "公开地址该放行，并保留查询串")

    rss = (b'<?xml version="1.0"?><rss><channel><item><title>A &amp; B</title>'
           b'<link>https://example.com/1</link><description>x</description></item>'
           b'<item><link>https://example.com/2</link></item></channel></rss>')
    hits = web_access.parse_bing_rss(rss)
    check(len(hits) == 2 and hits[0]["title"] == "A & B", "搜索 RSS 要解析得出来")
    check(hits[1]["title"] == "https://example.com/2", "没标题就拿链接顶上")

    page = web_access.extract_page(
        "<html><head><title>标题</title><style>x{}</style></head>"
        "<body><script>bad()</script><p>正文一</p><p>正文二</p></body></html>")
    check(page["title"] == "标题", "标题该抽出来：%r" % page["title"])
    check("正文一" in page["text"] and "bad()" not in page["text"],
          "正文要有、脚本要没：%r" % page["text"])

    body = "字" * 500
    part, total, more = web_access._slice(body, 0, 400)
    check(len(part) == 400 and total == 500 and more, "分页该按字切")
    tail, _, rest = web_access._slice(body, 400, 400)
    check(len(tail) == 100 and not rest, "末尾之后不该还有")

    check(web_access.needs_upgrade("短"), "抽出来太短就该走抓取接口")
    check(not web_access.needs_upgrade("字" * 400), "够长就不该花那个钱")

    ds = {"results": [
        {"title": "好", "url": "https://a.com", "summary": "摘要一", "rerank_score": 2.4},
        {"title": "差", "url": "https://b.com", "summary": "摘要二", "rerank_score": 0.2}]}
    ds_hits = web_access.parse_deepseek(ds)
    check(len(ds_hits) == 1 and ds_hits[0]["title"] == "好", "低分结果要被筛掉")
    check(len(web_access.parse_deepseek(
        {"results": [{"title": "差", "url": "https://b.com", "rerank_score": 0.2}]})) == 1,
        "全是低分时不要假装没搜到")
    check(ds_hits[0]["snippet"] == "摘要一", "摘要优先用 summary")

    tavily = {"results": [
        {"title": "备案须知", "url": "https://help.aliyun.com/x", "content": "需要申请备案"},
        {"title": "坏的", "url": "ftp://nope", "content": "x"}]}
    hits = web_access.parse_tavily(tavily)
    check(len(hits) == 1 and hits[0]["title"] == "备案须知", "Tavily 结果要解析得出来")
    check(hits[0]["snippet"] == "需要申请备案", "Tavily 片段要带上")

    check("上网失败" in webtools.call("fetch", '{"url": "http://127.0.0.1/"}'),
          "被拒的地址要变成一句给模型看的话")
    check(webtools.NAMES == ("fetch", "search"), "工具名别改")
    names = [tool["function"]["name"] for tool in webtools.tools()]
    check("fetch" in names and "search" in names, "工具面里该有这两个")


def check_shell() -> None:
    """命令工具：白名单、不经 shell、超时杀进程、输出截断。

    自检要在本机（可能是 Windows）跑，所以拿 `sys.executable` 当那条命令，
    `echo` / `ls` 这种在两边不通用。
    """
    workdir = tempdir("loop-shell-")
    try:
        python = sys.executable or "python3"
        program = os.path.basename(python)
        cfg = _loop_config(name="自检", data_dir=workdir)
        check(not shell.tools(cfg), "默认关：工具面里连 run 都不该有")
        check("没开" in shell.call("run", '{"command": "echo hi"}', cfg),
              "没开时调它要回一句，不该抛")
        cfg.run_enabled = True
        cfg.run_allow = program
        names = [tool["function"]["name"] for tool in shell.tools(cfg)]
        check(names == ["run"], "打开之后工具面里要有 run：%r" % names)

        out = shell.call("run", json.dumps(
            {"command": '%s -c "print(1 + 1)"' % program}), cfg)
        check("退出码 0" in out and "2" in out, "该跑起来并给出输出：%r" % out)

        refused = shell.call("run", json.dumps({"command": "rm -rf /"}), cfg)
        check("不给跑" in refused, "白名单外的程序要拒：%r" % refused)

        # 不经 shell：重定向只是个参数，不会真生成文件。
        marker = os.path.join(shell.workspace_of(cfg), "marker.txt")
        shell.call("run", json.dumps(
            {"command": '%s -c "print(3)" > marker.txt' % program}), cfg)
        check(not os.path.exists(marker), "重定向不该生效")

        # 工作目录是 <data>/workspace，不是家目录。
        where = shell.call("run", json.dumps(
            {"command": '%s -c "import os;print(os.getcwd())"' % program}), cfg)
        check(os.path.realpath(shell.workspace_of(cfg)) in
              os.path.realpath(where), "命令要在 workspace 里跑：%r" % where)

        bad = shell.call("run", json.dumps(
            {"command": '%s -c "raise SystemExit(3)"' % program}), cfg)
        check("退出码 3" in bad, "非零退出要如实说：%r" % bad)

        cfg.run_timeout = 0.6
        started = time.time()
        slow = shell.call("run", json.dumps(
            {"command": '%s -c "import time;time.sleep(9)"' % program}), cfg)
        check("超时" in slow, "跑过头要杀：%r" % slow)
        check(time.time() - started < 6, "超时之后不能还等在那儿")
        cfg.run_timeout = 20.0

        cfg.run_output_chars = 100
        long = shell.call("run", json.dumps(
            {"command": '%s -c "print(\\"x\\" * 600)"' % program}), cfg)
        check("省略" in long and len(long) < 400, "长输出要截断：%d 字" % len(long))
        marked = re.search(r"省略 (\d+) 字", long)
        check(marked and 400 < int(marked.group(1)) < 620,
              "要标出省了多少：%r" % long[:80])
        cfg.run_output_chars = 4000

        cfg.run_allow = program + " git"
        only_read = shell.call("run", json.dumps({"command": "git commit -m x"}), cfg)
        check("只给 status" in only_read, "git 只给读的子命令：%r" % only_read)

        called = shell.call("other", "{}", cfg)
        check("没有这个工具" in called, "别的名字要回「没有」")

        # 接到循环上：工具面里有它，分派也认它。
        loop_cfg = _loop_config(name="自检", data_dir=workdir,
                                block_tokens=100000)
        loop_cfg.run_enabled = True
        loop_cfg.run_allow = program
        wire = LoopStore(os.path.join(workdir, "wire.sqlite3"))
        try:
            loop = Loop(loop_cfg, credentials=None, store=wire,
                        compressor=Compressor(loop_cfg, summarizer=fake_summarizer),
                        memory=MemoryBridge.disabled(), client=FakeClient())
            names = [tool["function"]["name"] for tool in loop.tools()]
            check("run" in names, "循环的工具面里要有 run：%r" % names)
            wired = loop._call_tool("run", json.dumps(
                {"command": '%s -c "print(7)"' % program}))
            check("7" in wired, "循环分派要能跑通：%r" % wired)
        finally:
            wire.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    check_summary()
    check_compress()
    check_context_limits()
    check_two_blocks()
    check_clock()
    check_loop()
    check_compress_failure()
    check_tools()
    check_memory()
    check_usage()
    check_tool_fold()
    check_speak_with_calls()
    check_wait_text()
    check_wait_runtime()
    check_wait_resume()
    check_one_blocking_call()
    check_input_line()
    check_retry()
    check_watchdog()
    check_archive()
    check_usage_report()
    check_recall_log()
    check_inbox_trim()
    check_prompts()
    check_budget()
    check_remember_hidden()
    check_web()
    check_settings()
    check_boot_idle()
    check_delete()
    check_stop_raises()
    check_reload()
    check_web_tools()
    check_shell()
    check_ask()
    check_record_marks()
    check_workspace()
    print("ok: %d checks" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
