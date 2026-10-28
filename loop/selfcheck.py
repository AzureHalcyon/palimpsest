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
from . import persona
from . import prompt_files
from .config import LoopConfig, LoopCredentials
from .context import (strip_fake_calls, Band, Block, BlockEntry, Context, estimate_tokens,
                      serialize_entries, time_range)
from .driver import Loop, _ring_text, _timeout_text, _unread_text, _woke_text, parse_until
from .store import LoopStore
from .usage import collect, render
from memory_v2.api import Memory
from memory_v2.core.llm import ChatError

CHECKS = [0]


def check(condition, message: str) -> None:
    CHECKS[0] += 1
    if not condition:
        raise AssertionError(message)


def _loop_config(**kwargs) -> LoopConfig:
    """自检默认不打盹：这些用例没有外部输入，打盹会把它们提前停掉。"""
    kwargs.setdefault("watchdog_idle", 0)
    kwargs.setdefault("user_name", "")      # 占位「对方：」，名字的用例单独传
    return LoopConfig(**kwargs)


class FakeClient:
    """一次说一句、一次调工具、一次空回复；usage 按消息内容估。"""

    def __init__(self):
        self.last_usage = {}
        self.calls = 0
        self.prompts = []

    def chat(self, messages, tools=None, on_delta=None):
        # 原生 tool_calls 也要算进 prompt_tokens（跟真端点一致）：调用参数不在 content 里
        prompt = "\n".join(str(message.get("content") or "") for message in messages)
        prompt += "\n".join(str(message.get("tool_calls") or "") for message in messages)
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


class _StubTools:
    """自检用的假工具集：一个叫 `remember` 的工具，回一句固定的话。

    核心没有真工具了，但「一轮里同一个工具连调」这类规则要能测——用假的正合适。
    """

    NAMES = ("remember",)
    ANSWER = "已执行"

    def __init__(self):
        self.calls = 0

    def owns(self, name: str) -> bool:
        return name in self.NAMES

    def tools(self) -> list:
        return [{"type": "function",
                 "function": {"name": "remember", "description": "自检用",
                              "parameters": {"type": "object", "properties": {}}}}]

    def call(self, name: str, arguments: str) -> str:
        self.calls += 1
        return self.ANSWER



# 声音（voice）属于落地侧：核心自检自己注册一份，sink 第一句才有「我是谁」。
# 注册一次、全局生效；文件放在仓库 data/ 下的临时目录里。
_VOICE_DIR = tempdir("voice-fixture-")
with open(os.path.join(_VOICE_DIR, "voice.txt"), "w", encoding="utf-8") as _handle:
    _handle.write("我是$name。我既是$user的助手也是他的陪伴者。"
                  "我用简体中文思考和说话。")
prompt_files.register_prompts(
    {"voice": ("voice · 自检", "自检用的声音", "voice.txt")}, _VOICE_DIR)


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
        # 80 是配着 FakeClient 那套脚本手调的：8 步正好滚一次、不触发压缩
        # （原生 tool_calls 也计入 prompt_tokens，比早先的纯文本少一点，所以要调大）
        config = _loop_config(name="自检", block_tokens=80, max_steps=8,
                            park_seconds=0.001)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FakeClient())
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
    # 这里走的是真时钟，日期得跟着今天走——写死 10-25 过一晚就红
    today = datetime.now().strftime("%m-%d")
    check("现在 %s" % today in context.messages()[-1]["content"],
          "墙钟该在最后一条输入里（今天 %s）" % today)

    # 不带墙钟的那些步：末尾只放一句边界，不念时间也不催
    context.show_clock = False
    quiet = context.messages()[-1]["content"]
    check("现在" not in quiet, "关了就不该带时间：%r" % quiet)
    check(quiet.strip() == "<system-reminder>（接着上面。）</system-reminder>",
          "不带墙钟时只留一句边界：%r" % quiet)
    context.asking = True
    asked = context.messages()[-1]["content"]
    check("还要继续吗" in asked and "现在" not in asked,
          "提问可以不带时间：%r" % asked)
    context.asking = False
    context.show_clock = True

    # 真跑两步：第一步带墙钟，紧接着的第二步不重复念（120 秒内），只留边界
    workdir = tempdir("loop-clock-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=2)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        client = FakeClient()
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=client)
        loop.echo = lambda line: None
        loop.on_speak = lambda text: None
        loop.inject_user("在吗")
        loop.step()
        loop.step()
        check("现在" in client.prompts[0], "第一步该带墙钟：%r" % client.prompts[0][-80:])
        check("现在" not in client.prompts[1],
              "120 秒内不重复念时间：%r" % client.prompts[1][-80:])
        check("接着上面" in client.prompts[1], "不带墙钟时要有边界那句")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_loop() -> None:
    workdir = tempdir("loop-selfcheck-")
    try:
        config = _loop_config(name="自检", block_tokens=50, context_budget=80000,
                            band_budget=3000, band_chars=(300, 600, 1200, 2400),
                            park_seconds=0.001, max_steps=24)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FakeClient())
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
                     memory=Memory.disabled(), client=FakeClient())
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
                    memory=Memory.disabled(), client=FakeClient(),
                    echo=logs.append)
        loop.run()
        check(loop.compressions == 0, "摘要失败不能假装压过")
        check(len(loop.context.previous.entries) > 0, "摘要失败后上一块应保留")
        check(any("压缩失败" in line for line in logs), "失败要报给运行时输出")
        store.close()
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
                    memory=Memory.disabled(), client=FakeClient())
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


def check_user_name() -> None:
    """压缩文本里用对方的名字代替「对方：」，sink 第一句也跟着换。"""
    entry = BlockEntry({"role": "user", "content": "预算二十八万。"},
                       "2026-10-25 10:00:00")
    named = serialize_entries([entry], user_name="岚")
    check(named == "岚：预算二十八万。", "该用名字：%r" % named)
    check("对方：" not in named, "名字在的时候不该再出现占位")
    plain = serialize_entries([entry])
    check(plain.startswith("对方："), "不给名字就退回占位：%r" % plain)
    config = _loop_config(name="自检", user_name="岚")
    sink = Context(config).sink()
    check("岚" in sink and "$user" not in sink,
          "sink 第一句该用名字：%r" % sink[:60])


def check_tool_fold() -> None:
    """工具是持续调的：只折能重取的（recall），记忆本身（recollect）不折。"""
    recall_result = "找到 3 件：\n" + "\n".join(
        ["ev%d 某件旧事 · 2026-10-2%d · %d 字" % (index, index, index * 10)
         for index in range(3)]) + \
        "\n看具体用 recollect；连关系用 link。"
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
    check(folded.count("我用了：recall") == 5, "调用行不能被折：当时问的还在")
    # 压缩文本按角色标前缀：动作和结果不能长得像她的话
    check("我用了：" in folded and "工具回：" in folded,
          "调用/结果要有自己的前缀：%s" % folded[:120])
    check("调用 recall" not in folded and "结果：" not in folded,
          "旧的「调用 x / 结果：」记号不该再出现在压缩文本里")
    # 写给模型的提示语只留最新两条，历史里的去掉。
    check(folded.count("看具体用 recollect") == 2, "提示语只该留最新两条，实际 %d"
          % folded.count("看具体用 recollect"))
    check(serialize_entries(items, fold_keep=0) == raw, "fold_keep=0 就是不折")
    # 墙钟不进压缩文本：时间只靠段头那行（不然"现在几点"会被压成"她说过现在几点"）
    check("现在 " not in folded, "压缩文本里不该有墙钟")

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
                    memory=Memory.disabled(), client=BothClient())
        spoken = []
        loop.on_speak = spoken.append
        loop.run()
        check(spoken == ["我先查一下。"], "带工具调用的正文也要投递：%r" % spoken)
        text = loop.context.block_text()
        check("我先查一下。" in text, "正文该进记录")
        check("我用了：recall" in text, "调用也该进记录（标成动作，不是她的话）")
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
                    memory=Memory.disabled(), client=FakeClient())

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
                    memory=Memory.disabled(), client=FakeClient())
        loop._append({"role": "assistant", "content": "",
                      "tool_calls": [{"id": "w1", "type": "function",
                                      "function": {"name": "wait",
                                                   "arguments": "{\"until\": \"5s\"}"}}]})
        store.push("user", "还在吗")

        loop._resume_waits()
        text = loop.context.current_text()
        check("工具回：等了" in text, "重启该补上等待结果：%s" % text)
        check("对方：还在吗" in text, "醒来的消息要进记录：%s" % text)

        # 已经有过结果的 wait 不该重跑。
        loop._resume_waits()
        check(loop.context.current_text().count("工具回：等了") == 1,
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
                    memory=Memory.disabled(), client=TwoWaitsClient())
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


def check_retry() -> None:
    """模型调不通就一直试，循环不能因此停。"""
    workdir = tempdir("loop-retry-")
    try:
        config = _loop_config(name="自检", block_tokens=100000, max_steps=1,
                              retry_delay=0.01)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FlakyClient(1))
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
                    memory=Memory.disabled(), client=client)
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
        bridge = Memory(data_dir, lexical=True)
        bridge.remember(note="这件事只留一天。后一句不该留。",
                        name="过期的短事", scope="short", days=-1)
        event_id = next(iter(bridge.graph.events))

        bridge.archiver = lambda material: {event_id: "压成一行了。"}
        check(bridge.archive_expired() == 1, "该归档一件")
        check(event_id not in bridge.graph.events, "归档之后图里不该再有它")
        found = bridge.recall(keywords=["过期的短事"])
        check(found["reason"] == "not_found", "归档之后不该再召回：%s" % found["reason"])
        check(bridge.archive_expired() == 0, "归档要幂等")

        archive = os.path.join(data_dir, "archive.jsonl")
        check(os.path.exists(archive), "要写出 archive.jsonl")
        with open(archive, encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
        check(len(lines) == 1, "归档文件该有一行：%r" % lines)
        check(json.loads(lines[0])["text"] == "压成一行了。", "归档要留压好的那一行")

        # 压不动的时候用规则压缩：留第一句，别丢。
        bridge.remember(note="结论是二十八万。第二句不要。",
                        name="另一件短事", scope="short", days=-1)
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
    """提示词：注册表要覆盖所有文件；覆盖文件优先于仓库默认。

    工具说明一份一个文件（`prompts/tools/<工具>.txt`）：目录里的文件名就是键，
    多个文件少一个、多一个都要当场看出来。
    """
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
    found = [name[:-4] for name in os.listdir(folder) if name.endswith(".txt")]
    tools_dir = os.path.join(folder, "tools")
    if os.path.isdir(tools_dir):
        found += [name[:-4] for name in os.listdir(tools_dir) if name.endswith(".txt")]
    on_disk = sorted(found)
    # 目录里的每一份都要登记；反过来不要求——工具描述与声音由落地侧登记，文件在别处。
    missing = sorted(set(on_disk) - set(prompt_files.META))
    check(not missing, "prompts/ 里的文件没登记：%r" % missing)
    for key in prompt_files.META:
        check(prompt_files.text(key).strip(), "每份提示词都要有内容：%s" % key)
    texts = prompt_files.tool_texts()      # 缺段、多段、重复段都在这里报
    check(sorted(texts) == sorted(prompt_files.tool_keys()),
          "工具面要正好覆盖登记过的那些键")

    # 同一份里两次出现 `## 键`：后一段会静默盖掉前一段（run.command 就这么写过，
    # 结果模型拿到的是"第一段必须是白名单里的程序名"那版过期说明）。
    for key in prompt_files.META:
        body = prompt_files.default_text(key)
        heads = prompt_files._SECTION.findall(body)
        repeated = sorted({head for head in heads if heads.count(head) > 1})
        check(not repeated, "%s 里有重复的段落：%s" % (key, "、".join(repeated)))
    for tool in prompt_files.tool_names():
        check("白名单" not in prompt_files.default_text(tool),
              "run 已经不用程序白名单了（走 Landlock），%s 的说明里别再写白名单" % tool)
    # 每个工具文件开头那段就是工具描述：不能只剩一堆 `## 参数`
    for tool in prompt_files.tool_names():
        head, _parts = prompt_files.file_parts(tool)
        check(len(head) >= 10, "%s 开头要有工具描述：%r" % (tool, head))

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
                    memory=Memory.disabled(), client=FakeClient())
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
                         memory=Memory.disabled(), client=FakeClient())
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
                    memory=Memory.disabled(), client=FakeClient())

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

        # 入口起了就替人塞一句（`--say`）：这时候不该再"等动静"，
        # 否则那句话躺在记录里，人得再发一条才会被看见。
        store = LoopStore(os.path.join(workdir, "boot2.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FakeClient())
        loop.inject_user("我先说一句")
        started = time.time()
        loop.run(max_steps=1)
        waited = time.time() - started
        check(loop.steps == 1, "有人先开了口，就该直接走一步：%d" % loop.steps)
        check(waited < 0.2, "不该再等动静（等了 %.2f 秒）" % waited)
        check("我先说一句" in loop.context.current_text(), "那句要进记录")
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
                    memory=Memory.disabled(), client=client)
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
                    memory=Memory.disabled(), client=client)
        loop.echo = lambda line: None
        loop.on_speak = lambda text: None
        loop.inject_user("在吗")
        loop.step()                       # 说了话
        loop.step()                       # 被问一句 → 它调 wait(1s) 停下
        check(client.asked == 1, "该问它一次：%d" % client.asked)
        check("我用了：wait" in loop.context.block_text(), "停下这个决定要进记录")
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
                    memory=Memory.disabled(), client=client)
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
    check(roles.count("assistant") >= 1 and "在。" in messages[2]["content"],
          "自己的话是 assistant：%r" % messages[2])
    calls = messages[2].get("tool_calls") or []
    check(len(calls) == 1 and calls[0]["function"]["name"] == "wait",
          "调用要以原生 tool_calls 挂在它自己那条上：%r" % messages[2])
    check(roles[3] == "tool" and "好" in messages[3]["content"],
          "结果要走 role=tool 的消息：%r" % messages[3])
    check(messages[3].get("tool_call_id") == "c1", "结果要带上对应的 call id")
    check(roles[4] == "system" and "任务回来了" in messages[4]["content"],
          "系统动静走 system：%r" % messages[4])
    check(messages[4]["content"].startswith("<system-reminder>"),
          "系统注入要套标签：%r" % messages[4]["content"])
    # 记录里不能再有"调用 x / 结果：…"这种可抄的文字行：
    # 服务器上真出过——她照着抄，把调用和结果当正文写出来（一步里没有 tool 记录）。
    flat = "\n".join(str(m.get("content") or "") for m in messages)
    check(not re.search(r"^调用 \S+ \{", flat, re.M),
          "原生消息里不该有可抄的「调用 x {...}」行：%r" % flat[:120])
    check(not any(line.lstrip().startswith("结果：") for line in flat.splitlines()),
          "原生消息里不该有「结果：」前缀行")
    # 清洗：模型手写的调用/结果行会被删掉，别的话留着
    dirty = ("我看了下。\n调用 run {\"command\": \"ls\"}\n"
             "结果：（退出码 0）文件在这儿\n好，就这样。")
    clean = strip_fake_calls(dirty)
    check("调用 run" not in clean and "结果：" not in clean,
          "抄记录的那条正文里，调用和结果行都该删掉：%r" % clean)
    check(clean.startswith("我看了下。") and clean.endswith("好，就这样。"),
          "别的话要留着：%r" % clean)
    # 没有伪造调用行时，「结果：」是她自己的一句话，不许动
    prose = "我问了一圈。\n结果：他们还是没来。"
    check(strip_fake_calls(prose) == prose, "正常的「结果：」不该被吃掉：%r"
          % strip_fake_calls(prose))
    # 调用协议漏进正文（线上真出现过）：带特殊标记的那几行整行丢，别的话留着
    leaked = ("我这边其实不用睡，等你走了我就停下来歇着。\n\n"
              "<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name=\"wait\">\n"
              "<｜｜DSML｜｜ parameter name=\"kinds\">[\"user\"]</｜｜DSML｜｜ parameter>")
    fixed = strip_fake_calls(leaked)
    check("｜" not in fixed and "DSML" not in fixed,
          "漏出来的调用标记要删干净：%r" % fixed)
    check(fixed == "我这边其实不用睡，等你走了我就停下来歇着。",
          "标记旁边的话要原样留着：%r" % fixed)
    check(strip_fake_calls("<｜｜DSML｜｜ calls>") == "",
          "整条都是标记时，正文就该是空的")
    check(not any("没有外界的动静" in str(m["content"]) for m in messages),
          "心跳提醒不该进上下文（它是当时的提醒，不是发生过的事）")
    joined = "\n".join(str(m["content"]) for m in messages[1:])
    check("我：" not in joined and "对方：" not in joined,
          "原生消息里不该有前缀：%r" % joined[:80])
    check(messages[-1]["role"] == "user"
          and "现在 %s" % datetime.now().strftime("%m-%d") in messages[-1]["content"],
          "墙钟是末尾单独那一帧（带 tools 时端点不接受末尾 system）")
    check(messages[-1]["content"].startswith("<system-reminder>"),
          "末尾那帧也要套标签：%r" % messages[-1]["content"])
    check(roles.count("user") == 2, "user 只有两条：对方的话 + 末尾那帧：%r" % roles)

    # 纯文本渲染（压缩模型看的）还是要标：它没有 role 可读。
    text = serialize_entries(entries)
    check("对方：在吗" in text and "我：在。" in text, "纯文本要标两边：%r" % text)
    check("提醒：" in text, "纯文本要标系统提醒：%r" % text)
    check("\n\n" not in text, "纯文本里一条话不留空行，免得下一段像别人接的")

    check_note_fade()


def check_note_fade() -> None:
    """remember 的 note 在记录里留十来步：太早摘掉她会怀疑"note 没传、是系统补的"。"""
    def build(steps: int):
        rows = [BlockEntry({"role": "user", "content": "记一下"}, "2026-10-25 15:00:00"),
                BlockEntry({"role": "assistant", "content": "",
                            "tool_calls": [{"id": "c1", "type": "function",
                                            "function": {
                                                "name": "remember",
                                                "arguments": json.dumps(
                                                    {"name": "丙 记了一件事",
                                                     "note": "这一条本来写的是什么"},
                                                    ensure_ascii=False)}}]},
                           "2026-10-25 15:00:01"),
                BlockEntry({"role": "tool", "tool_call_id": "c1",
                            "content": "已记（新建）：丙 记了一件事（e-1，long，9 字）"},
                           "2026-10-25 15:00:02")]
        for index in range(steps):
            rows.append(BlockEntry({"role": "assistant", "content": "第 %d 步" % index},
                                   "2026-10-25 15:00:0%d" % (index % 10)))
        context = Context(_loop_config(name="自检"))
        context.current = Block(rows, "2026-10-25 15:00:00", "2026-10-25 15:01:00")
        return json.dumps(context.messages(), ensure_ascii=False)

    fresh = build(0)
    check("这一条本来写的是什么" in fresh, "刚写完的 note 要看得见：%r" % fresh[-200:])
    stale = build(12)
    check("这一条本来写的是什么" not in stale,
          "过了 %d 步就不该再占上下文" % 12)
    check("已写进记忆" in stale, "摘掉后要留一句占位：%r" % stale[-200:])



def check_inbox_channels() -> None:
    """通道来的只响铃（正文走 messages），本机对话（没 source）直接进记录。"""
    workdir = tempdir("loop-inbox-")
    try:
        config = _loop_config(name="自检", data_dir=workdir, block_tokens=100000)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FakeClient())

        # 本机对话：没有 source，正文直接进记录
        store.push("user", "本机这边的一句话")
        loop._drain_inbox()
        check("本机这边的一句话" in loop.context.current_text(),
              "本机对话直接进记录")

        # 通道来的：只响铃，正文不进记录
        store.push("user", "在吗", source="外部", chat_type="group", chat_id="g1",
                   chat_name="群甲", sender_name="甲")
        loop._drain_inbox()
        check("在吗" not in loop.context.current_text(),
              "通道来的正文不该直接进记录：%r" % loop.context.current_text()[-60:])
        loop._unread_note()
        tail = loop.context.current_text()
        check("还有 1 条没看的消息" in tail, "要响一声：%r" % tail[-90:])

        # 同一个铃不重复响，但没读走就还算未读
        loop._drain_inbox()
        check(store.unread_batches()["total"] == 1, "响过铃、没读，仍算未读")

        # 读回来：带发言人，读完就没未读了
        read = loop._read_messages(json.dumps({"chat": "g1"}))
        check("外部·群「群甲」 甲：在吗" in read, "读回来要带发言人：%r" % read)
        check(store.unread_batches()["total"] == 0, "读完就没未读了")
        # 读完的正文是作为那条工具结果进记录的：编号要记在 meta 里，
        # 否则重启对账会当成"丢了"，又插一条 user 消息（窜到网页对话里）
        check(loop._tool_meta.get("inbox_ids"), "读回来的编号要跟着工具结果走：%r"
              % loop._tool_meta)
        loop._append({"role": "tool", "tool_call_id": "c1", "content": read},
                     meta=loop._tool_meta)
        loop._tool_meta = {}
        check(store.inbox_unappended() == [],
              "读进工具结果也算落库，对账不该再报它：%r" % store.inbox_unappended())
        # 本机直投的不用对账：它是"先落记录再标已读"，漏了也轮不到补插一条 user
        store.push("user", "网页这边的一句", source="网页", direct=True)
        store.consume(store.inbox_recent(1)[0]["id"])
        check(store.inbox_unappended() == [],
              "本机直投不该出现在对账清单里：%r" % store.inbox_unappended())

        # batch：不响铃，只计数
        store.set_policy("g1", "batch")
        store.push("user", "晚上吃啥", source="外部", chat_type="group", chat_id="g1",
                   chat_name="群甲", sender_name="乙")
        loop._drain_inbox()
        check("晚上吃啥" not in loop.context.current_text(), "batch 不进上下文")
        check(store.unread_batches()["total"] == 1, "batch 记为未读")
        check("乙" in json.dumps(store.unread_batches(), ensure_ascii=False),
              "计数要带谁说的")

        # mute：不提示，但点名那个会话仍然读得到（"不提示"不是"锁起来"）
        # 新会话的默认策略按类型分：群 batch（自己去看），私聊 wake
        store.push("user", "私聊的第一句", source="QQ", chat_type="private",
                   chat_id="p9", chat_name="某甲", sender_name="甲")
        store.push("user", "群里的第一句", source="QQ", chat_type="group",
                   chat_id="g9", chat_name="群乙", sender_name="丁")
        rows = {item["chat_id"]: item for item in store.channels()}
        check(rows["p9"]["policy"] == "wake", "私聊默认 wake：%r" % rows["p9"])
        check(rows["g9"]["policy"] == "batch", "群默认 batch：%r" % rows["g9"])
        # 被 @ 的那条明确 wake：跟同一会话里攒着的分开
        store.push("user", "@ 她的一句", source="QQ", chat_type="group",
                   chat_id="g9", chat_name="群乙", sender_name="丁", policy="wake")
        got = {item["text"]: item["policy"] for item in store.messages("g9")}
        check(got.get("群里的第一句") == "batch", "没 @ 的攒着：%r" % got)
        check(got.get("@ 她的一句") == "wake", "被 @ 的叫醒：%r" % got)

        store.set_policy("g1", "mute")
        store.push("user", "mute 之后的一句", source="外部", chat_type="group",
                   chat_id="g1", chat_name="群甲", sender_name="丙")
        read = loop._read_messages(json.dumps({"chat": "g1"}))
        check("mute 之后的一句" in read, "点名会话时 mute 也读得到：%r" % read)
        row = [item for item in store.channels() if item["chat_id"] == "g1"]
        check(row and row[0]["unread"] == 0, "mute 的不该算未读：%r" % row)

        # 改策略只影响未读，也要能把"压根没这个会话"和"改了 0 条"分开
        raised = False
        try:
            store.set_policy("没有这个会话", "wake")
        except KeyError:
            raised = True
        check(raised, "没有这个会话要抛 KeyError（回「改了 0 条」会让人以为失败了）")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_unread_fade() -> None:
    """「还有 N 条」这行有保质期：刚拼上看得见，过几步就不进上下文了。"""
    config = _loop_config(name="自检")
    line = {"role": "note", "content": "还有 3 条没看的消息，Ranchel 3 条。"}
    stamp = "2026-10-28 20:00:00"

    fresh_rows = [BlockEntry(line, stamp, {"kind": "unread", "total": 3})]
    for index in range(2):
        fresh_rows.append(BlockEntry({"role": "assistant", "content": "第 %d 步" % index},
                                     "2026-10-28 20:00:0%d" % index))
    context = Context(config)
    context.current = Block(fresh_rows, stamp, "2026-10-28 20:01:00")
    fresh = json.dumps(context.messages(), ensure_ascii=False)
    check("还有 3 条没看的消息" in fresh,
          "刚拼上要看得到：%r" % fresh[-120:])

    stale_rows = [BlockEntry(line, stamp, {"kind": "unread", "total": 3})]
    for index in range(8):
        stale_rows.append(BlockEntry({"role": "assistant", "content": "第 %d 步" % index},
                                     "2026-10-28 20:00:0%d" % (index % 10)))
    context = Context(config)
    context.current = Block(stale_rows, stamp, "2026-10-28 20:01:00")
    stale = json.dumps(context.messages(), ensure_ascii=False)
    check("还有 3 条没看的消息" not in stale,
          "过了保质期不该再出现：%r" % stale[-120:])


def check_inbox_repair() -> None:
    """进件对账：已读但记录里没有的，要能查出来并补回去。

    只对通道转进来的（本机直投是"先落记录再标已读"，不会只剩进件）。
    """
    workdir = tempdir("loop-repair-")
    try:
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        item_id = store.push("user", "这条丢了", source="QQ", chat_id="c1",
                            sender_name="甲")
        store.consume(item_id)               # 模拟"标了已读但没落库"
        missing = store.inbox_unappended()
        check(len(missing) == 1 and missing[0]["text"] == "这条丢了",
              "该查出没落库的那条：%r" % missing)

        config = _loop_config(name="自检", data_dir=workdir, block_tokens=100000)
        loop = Loop(config, LoopCredentials(chat_key="k"), store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FakeClient())
        loop._repair_inbox()
        check("这条丢了" in loop.context.current_text(), "重启后要补进记录")
        check(store.inbox_unappended() == [], "补过之后对账要干净")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_ring_text() -> None:
    """叫醒她那句话：谁、在哪儿，只陈述事实——不写"请调用某某工具"。

    把工具名塞进别人的话里，读起来像系统提示；工具怎么用归工具自己的说明。
    """
    private = {"source": "QQ", "chat_type": "private", "chat_id": "2189761707",
               "chat_name": "Ranchel", "sender_name": "Ranchel"}
    ring = _ring_text(private)
    check(ring == "Ranchel 在 QQ 上私聊你。", "私聊那句话：%r" % ring)
    check("messages" not in ring and "工具" not in ring,
          "叫醒那句话不该提工具名：%r" % ring)

    group = {"source": "QQ", "chat_type": "group", "chat_id": "g1",
             "chat_name": "群甲", "sender_name": "甲"}
    ring = _ring_text(group)
    check(ring == "甲 在群「群甲」里 @ 你。", "群里那句话：%r" % ring)
    check("messages" not in ring, "群里那句也不该提工具名：%r" % ring)

    unknown = {"source": "QQ", "chat_type": "private", "chat_id": "9"}
    check(_ring_text(unknown) == "有人在 QQ 上私聊你。",
          "没名字时退到「有人」：%r" % _ring_text(unknown))

    note = _unread_text({"total": 3, "chat_total": 2, "chats": [
        {"chat_id": "a", "chat_name": "Ranchel", "count": 2,
         "senders": ["Ranchel 2"]},
        {"chat_id": "g1", "chat_name": "群甲", "count": 1, "senders": []}]})
    check("还有 3 条没看的消息" in note, "计数那行要留着：%r" % note)
    check("messages" not in note, "计数那行也不该提工具名：%r" % note)


def check_via_mark() -> None:
    """她说的话带着「回给谁」：外部叫醒的那几轮才有，本机聊的不带。

    通道适配器靠这个标记定向回发——没有它就不知道这句话该发给谁，
    有了它，网页里跟她聊的话就不会被复述到 QQ 上去。
    """
    workdir = tempdir("loop-via-")
    try:
        config = _loop_config(name="自检", data_dir=workdir, block_tokens=100000)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, credentials=None, store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FakeClient())

        # 本机这边说话：正文直接进记录，来源照实记（网页 / 命令行），但不盖前缀、不带 via
        store.push("user", "网页这边说的一句", source="网页", direct=True)
        loop._drain_inbox()
        check("网页这边说的一句" in loop.context.current_text(),
              "本机直投要直接进记录：%r" % loop.context.current_text()[-60:])
        check("网页：" not in loop.context.current_text(),
              "本机直投不该在记录里盖来源前缀：%r" % loop.context.current_text()[-60:])
        saved = store.inbox_recent(1)[0]
        check(saved["source"] == "网页" and saved["direct"] == 1,
              "来源要照实存下来（仪表盘要看）：%r" % saved)
        check(not saved["chat_id"], "本机直投没有会话")
        loop._append({"role": "assistant", "content": "本机这边回的一句"})
        check(not loop.context.current.entries[-1].meta.get("via"),
              "本机对话不该带 via：%r" % loop.context.current.entries[-1].meta)

        # 通道来的：叫醒她，她说的话要带上是回给哪个会话
        store.push("user", "在吗", source="QQ", chat_type="private",
                   chat_id="2189761707", chat_name="她的人", sender_name="甲")
        loop._drain_inbox()
        loop._append({"role": "assistant", "content": "在的"})
        via = loop.context.current.entries[-1].meta.get("via") or {}
        check(via.get("source") == "QQ" and via.get("chat_id") == "2189761707",
              "外部叫醒后要带 via：%r" % via)
        check(via.get("chat_name") == "她的人" and via.get("chat_type") == "private",
              "via 要能直接当回发地址用：%r" % via)

        # 网页那边又说话：回复窗口换地方，之后的话不再算回给那个会话
        store.push("user", "网页这边问一句")
        loop._drain_inbox()
        check(loop.via == {}, "本机说话要关掉回复窗口：%r" % loop.via)

        # 等回去没事（到点没等到）：窗口也关掉
        store.push("user", "再来一句", source="QQ", chat_type="private",
                   chat_id="2189761707", chat_name="她的人", sender_name="甲")
        loop._drain_inbox()
        check(loop.via.get("chat_id") == "2189761707", "外部消息要开窗口")
        loop.store.mark_notified(store.inbox_recent(1)[0]["id"])
        said = loop._wait(json.dumps({"until": "1s"}))
        check("到点了" in said, "应该等到点：%r" % said)
        check(loop.via == {}, "等回去没事要关窗口：%r" % loop.via)

        # 仪表盘那一份：读过的也列出来，数字对得上
        recent = store.inbox_recent(limit=20)
        check([row["text"] for row in recent][0] == "网页这边说的一句",
              "最近的进件要按时间正序：%r" % [row["text"] for row in recent])
        check(any(row["consumed"] for row in recent), "读过的也要列出来")
        counts = store.inbox_counts()
        check(counts["outside"] == 2, "外部来过几条要数对：%r" % counts)
        check(counts["waiting"] >= 1, "还没读的条数要数对：%r" % counts)

        # 读回积压（batch）也要立 via：不然她读完再答，回答根本发不出去
        store.push("user", "攒着的之一", source="QQ", chat_type="private",
                   chat_id="777", chat_name="某乙", sender_name="乙")
        store.push("user", "攒着的之二", source="QQ", chat_type="private",
                   chat_id="777", chat_name="某乙", sender_name="乙")
        loop._read_messages(json.dumps({"chat": "777"}))
        check(loop.via.get("chat_id") == "777", "读完积压要立 via：%r" % loop.via)

        # 一步里两个会话同时叫她：挑不出来就别立（立错了会把话发给另一个人）
        store.push("user", "甲这边", source="QQ", chat_type="private",
                   chat_id="111", chat_name="甲", sender_name="甲")
        store.push("user", "乙那边", source="QQ", chat_type="private",
                   chat_id="222", chat_name="乙", sender_name="乙")
        loop._drain_inbox()
        check(loop.via == {}, "两个会话一起叫，不该立 via：%r" % loop.via)

        # 外部编号：同一个编号只收一次（补拉历史、重连补漏都靠它）
        first = store.push("user", "带编号的一条", source="QQ", chat_id="c1",
                           ext_id="m-77")
        again = store.push("user", "带编号的一条", source="QQ", chat_id="c1",
                           ext_id="m-77")
        check(first == again, "同一个 ext_id 再推要回原来那条：%r / %r" % (first, again))
        check(store.inbox_has_ext("m-77"), "收过的编号要认得")
        check(not store.inbox_has_ext("m-78"), "没见过的编号不该认")
        check(store.inbox_recent(1)[0]["ext_id"] == "m-77",
              "仪表盘那份要带出编号：%r" % store.inbox_recent(1)[0])
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_call_cap() -> None:
    """同一个工具一轮里连调超过上限：问一句「还要接着用吗」，再调就照跑。"""
    workdir = tempdir("loop-cap-")
    try:
        config = _loop_config(name="自检", data_dir=workdir, block_tokens=100000)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        loop = Loop(config, LoopCredentials(chat_key="k"), store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=FakeClient(),
                    toolsets=[_StubTools()])

        def dispatch(times: int) -> list:
            before = len([entry for entry in loop.context.current.entries
                          if entry.message.get("role") == "tool"])
            calls = [{"id": "c%d-%d" % (len(loop.context.current.entries), i),
                      "type": "function",
                      "function": {"name": "remember",
                                   "arguments": json.dumps({"name": "同一段"})}}
                     for i in range(times)]
            loop._dispatch_calls(calls)
            texts = [entry.message.get("content") for entry in loop.context.current.entries
                     if entry.message.get("role") == "tool"]
            return texts[before:]

        texts = dispatch(3)
        check(all("再调一次" not in str(t) for t in texts), "前三次照跑：%r" % texts)
        check(loop._turn_calls["remember"] == 3, "计数要累加：%r" % loop._turn_calls)

        # 第 4 次不硬挡，只问一句；它再调（第 5 次）就照跑，计数收回 1
        texts = dispatch(2)
        asked = [t for t in texts if "再调一次" in str(t)]
        ran = [t for t in texts if _StubTools.ANSWER in str(t)]
        check(len(asked) == 1, "第 4 次该问一句（不执行）：%r" % texts)
        check("这次照跑" in str(asked[0]), "问话要说清再调就放行：%r" % asked[0])
        check(len(ran) == 1, "确认后的那次要真执行：%r" % texts)
        check(loop._turn_calls["remember"] == 1, "确认后计数重新起算：%r"
              % loop._turn_calls)
        check(loop._turn_confirm == set(), "确认完要把「问过了」清掉：%r"
              % loop._turn_confirm)

        # 重新起算之后照样三次一停：先两次照跑，第三次到点又问一遍（不会一直挡死）
        texts = dispatch(2)
        check(all("再调一次" not in str(t) for t in texts),
              "确认后还能接着用：%r" % texts)
        texts = dispatch(1)
        check(len([t for t in texts if "再调一次" in str(t)]) == 1,
              "再满三次还要问一遍：%r" % texts)

        # 对方说话之后计数清零：人叫她重试一次，不能还拿上一轮的次数挡回去。
        loop.store.push("user", "你试一次")
        check(loop._wait('{"kinds": ["user"]}') != "", "等到人的话要有个结果")
        loop._flush_after_tool()
        check(loop._turn_calls == {}, "对方说话后计数要清零：%r" % loop._turn_calls)
        check(loop._turn_confirm == set(), "「问过了」也要清掉：%r" % loop._turn_confirm)
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_effort() -> None:
    """思考强度：配置了就要进请求体，没配置就不发。"""
    from memory_v2.core.config import ChatConfig
    from memory_v2.core.llm import ChatClient as RealClient
    from memory_v2.core.config import Credentials as MemCreds

    body = RealClient(ChatConfig(model="m", reasoning_effort="high"),
                      MemCreds(api_key="k"))._body([], None, None, None, False)
    check(body.get("reasoning_effort") == "high", "high 要进请求体：%r" % body)
    body = RealClient(ChatConfig(model="m"), MemCreds(api_key="k"))._body(
        [], None, None, None, False)
    check("reasoning_effort" not in body, "空值不该发")
    # 「设置表里有这一项」归落地侧（rin/checks），这里只管客户端会不会发出去。


def check_stream() -> None:
    """流式：开着就把 on_delta 交给客户端（草稿给界面看），关掉就整段等。"""
    workdir = tempdir("loop-stream-")
    try:
        seen = {}

        class WatchClient(FakeClient):
            def chat(self, messages, tools=None, on_delta=None):
                seen["on_delta"] = on_delta is not None
                if on_delta is not None:
                    on_delta("reasoning", "想一下")
                    on_delta("content", "在的")
                return {"role": "assistant", "content": "在的"}

        config = _loop_config(name="自检", block_tokens=100000, data_dir=workdir)
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        client = WatchClient()
        loop = Loop(config, LoopCredentials(chat_key="k"), store=store,
                    compressor=Compressor(config, summarizer=fake_summarizer),
                    memory=Memory.disabled(), client=client)
        loop._chat([{"role": "user", "content": "在吗"}], [])
        check(seen.get("on_delta") is True, "开着流式就该把 on_delta 交出去")
        check(loop.draft_text == "在的", "正文增量要攒成草稿：%r" % loop.draft_text)
        check(loop.draft_think == "想一下", "思考增量也留着：%r" % loop.draft_think)

        config.stream = False
        loop._chat([{"role": "user", "content": "在吗"}], [])
        check(seen.get("on_delta") is False, "关掉就不该传 on_delta")
        loop.clear_draft()
        check(loop.draft_text == "" and loop.draft_think == "", "清草稿要清干净")
        store.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_persona() -> None:
    """她自己写的自我描述：写盘、截断、进 system、到点才重写。"""
    workdir = tempdir("loop-persona-")
    try:
        # 上限有 200 字的地板（太短写不出东西），所以这里用 200
        config = _loop_config(name="自检", data_dir=workdir, persona_chars=200)
        check(persona.read(config) == "", "一开始是空的")
        check("【我自己写的】" not in Context(config).system_text(),
              "空的时候不该出现在 system 里")

        body = persona.write(config, "我说话慢，喜欢先看清楚再开口。" * 5)
        check(len(body) <= 200, "超上限要截断：%d 字" % len(body))
        check(persona.read(config) == body, "写进去的要能读回来")
        check(os.path.exists(os.path.join(workdir, "persona.log")), "改写历史要留一份")
        text = Context(config).system_text()
        check("【我自己写的】" in text and body in text, "非空时要拼进 system")
        check(text.index("【我自己写的】") > text.index("我是自检"),
              "要排在 sink 后面")

        config.persona_enabled = False
        check("【我自己写的】" not in Context(config).system_text(),
              "关掉就不出现")

        # 到点判定
        config.persona_enabled = True
        config.persona_hours = 24
        persona.touch_stamp(config, 1000.0)
        check(not persona.due(config, moment=1000.0 + 3600), "没到点不该重写")
        check(persona.due(config, moment=1000.0 + 25 * 3600), "过了间隔该重写")
        check(not persona.due(config, moment=1000.0 + 25 * 3600, floor=9e9),
              "退避期间不写")
        config.persona_hours = 0
        check(not persona.due(config, moment=1e9), "0 就是不自动重写")

        # 整理任务：一次调用，写回，截到上限
        config.persona_hours = 24
        context = Context(config)
        context.current = Block([BlockEntry({"role": "user", "content": "在吗"},
                                            "2026-10-25 10:00:00")],
                                "2026-10-25 10:00:00", "2026-10-25 10:01:00")
        seen = []

        def fake_chat(messages):
            seen.append(messages)
            return {"content": "我说话短。" * 30}

        # 聊得不够多就不触发：阈值设高一点，新材料不够就该返回空
        config.persona_min_chars = 2000
        persona.touch_stamp(config, 0.0)
        check(persona.enough(context, config) == "", "新材料不够时不该触发")
        config.persona_min_chars = 0
        check(persona.enough(context, config) != "", "阈值 0 就是有新材料就写")

        # 门槛按全长算、喂的时候才截断：门槛比喂养上限大也不能永远不触发
        config.persona_min_chars = 400
        context.current = Block([BlockEntry(
            {"role": "user", "content": "字" * 900}, "2026-10-25 10:00:00")],
            "2026-10-25 10:00:00", "2026-10-25 10:01:00")
        fed = persona.enough(context, config)
        check(bool(fed), "新材料超过门槛就该喂（哪怕要截断）")
        cap = persona.feed_chars_of(config)
        check(cap > 0 and len(fed) <= cap + 2,
              "喂进去的要截到上限 %d 字：%d" % (cap, len(fed)))
        config.persona_feed_chars = 0
        huge = persona.enough(context, config)
        check(len(huge) >= len(fed), "0 就是不截断，喂全量")
        config.persona_feed_chars = persona.MATERIAL_CHARS

        # 只看上次改写之后的新记录：旧条目不该被反复喂（否则越写越像复述）
        persona.touch_stamp(config, time.time())
        check(persona.enough(context, config) == "", "刚写过就不该再有新材料")
        context.current.entries.append(BlockEntry(
            {"role": "user", "content": "新的这句"}, "2099-01-01 00:00:00"))

        written = persona.rewrite(fake_chat, context, config)
        check(len(written) <= persona.limit_of(config), "重写也要截断")
        check(any("新的这句" in str(m.get("content")) for m in seen[-1]),
              "整理任务要看到新材料：%r" % seen[-1][-1])
        check(not any("在吗" in str(m.get("content")) for m in seen[-1]),
              "改写之前的老条目不必反复喂：%r" % seen[-1][-1])
        check("当前这版" in str(seen[-1][0].get("content")),
              "整理任务要看到当前这版")

        # 整理用的模型：空就跟主模型走（不进单独客户端）
        loop_cfg = _loop_config(name="自检", data_dir=workdir, block_tokens=100000)
        creds = LoopCredentials(chat_key="k-test",
                                chat_url="https://x.test/v1/chat/completions")
        wire = LoopStore(os.path.join(workdir, "persona-model.sqlite3"))
        try:
            loop = Loop(loop_cfg, creds, store=wire,
                        compressor=Compressor(loop_cfg, creds),
                        memory=Memory.disabled(), client=FakeClient())
            made = loop._persona_client_for("glm-5.3-flash")
            check(getattr(made.config, "model", "") == "glm-5.3-flash",
                  "整理用的模型要按名字造客户端：%r" % getattr(made.config, "model", ""))
            again = loop._persona_client_for("glm-5.3-flash")
            check(again is made, "同一个模型别重复造")
            other = loop._persona_client_for("deepseek-v4-pro")
            check(other is not made, "换了模型要重建")
        finally:
            wire.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_inbox() -> None:
    """进件：peek 不标已读、consume 才标——不然中途重启会丢消息。

    服务器上真丢过：人在 20:55 说的话进了进件、被 wait 取走标了已读，
    可记录要等这一步的模型调用之后才写，正好赶上重启，那条就没了。
    """
    workdir = tempdir("loop-inbox-")
    try:
        store = LoopStore(os.path.join(workdir, "loop.sqlite3"))
        store.push("user", "第一句")
        store.push("user", "第二句")
        first = store.peek()
        check(first is not None and first["text"] == "第一句", "peek 看到最早那条")
        again = store.peek()
        check(again is not None and again["id"] == first["id"],
              "没 consume 之前还看得到（重启也不会丢）")
        store.consume(first["id"])
        second = store.peek()
        check(second is not None and second["text"] == "第二句",
              "consume 之后才轮到下一条")
        got = store.take()
        check(got is not None and got["text"] == "第二句", "take 仍然可用（peek+consume）")
        check(store.peek() is None, "都标过已读就取不到了")
        store.close()
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
                    memory=Memory.disabled(), client=FakeClient())
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
                    memory=Memory.disabled(), client=FakeClient())
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


def main() -> int:
    check_summary()
    check_compress()
    check_context_limits()
    check_two_blocks()
    check_clock()
    check_loop()
    check_compress_failure()
    check_usage()
    check_user_name()
    check_tool_fold()
    check_speak_with_calls()
    check_wait_text()
    check_wait_runtime()
    check_wait_resume()
    check_one_blocking_call()
    check_retry()
    check_watchdog()
    check_archive()
    check_usage_report()
    check_inbox_trim()
    check_prompts()
    check_budget()
    check_remember_hidden()
    check_boot_idle()
    check_delete()
    check_stop_raises()
    check_reload()
    check_ask()
    check_record_marks()
    check_inbox()
    check_persona()
    check_stream()
    check_inbox_channels()
    check_unread_fade()
    check_inbox_repair()
    check_ring_text()
    check_via_mark()
    check_call_cap()
    check_effort()
    print("ok: %d checks" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
