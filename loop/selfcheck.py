"""离线自检：假模型、假数据、临时库，不碰网络。

    python -m loop.selfcheck

覆盖：摘要解析、旧段下移与截短、预算、两块原文的攒块时机、压缩失败保留原文、
工具结果写回、空回复即停、重启后从库重建两块原文。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid

from .compress import CompressError, Compressor, add_band, parse_summary, truncate
from .config import LoopConfig
from .context import (Band, Block, BlockEntry, Context, estimate_tokens,
                      serialize_entries, time_range)
from .driver import Loop
from .memory_bridge import MemoryBridge
from .store import LoopStore

CHECKS = [0]


def check(condition, message: str) -> None:
    CHECKS[0] += 1
    if not condition:
        raise AssertionError(message)


def _tempdir(prefix: str) -> str:
    """临时库放在仓库的 data/ 里：系统临时目录可能不在可写范围内。

    不用 tempfile.mkdtemp：它按 0o700 建目录，受限令牌下连自己都写不进去。
    """
    parent = os.path.abspath("data")
    os.makedirs(parent, exist_ok=True)
    path = os.path.join(parent, prefix + uuid.uuid4().hex[:8])
    os.makedirs(path)
    return path


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
        self.last_usage = {"prompt_tokens": estimate_tokens(prompt),
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
    config = LoopConfig(name="自检", band_chars=(100, 200, 400, 800), band_decay=0.8)
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
    config = LoopConfig(name="自检", band_chars=(100, 200, 400, 800), band_budget=400)
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
    workdir = _tempdir("loop-two-")
    try:
        config = LoopConfig(name="自检", block_tokens=50, max_steps=8,
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


def check_loop() -> None:
    workdir = _tempdir("loop-selfcheck-")
    try:
        config = LoopConfig(name="自检", block_tokens=50, context_budget=80000,
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
    workdir = _tempdir("loop-failure-")
    try:
        config = LoopConfig(name="自检", block_tokens=30, max_steps=12,
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
    workdir = _tempdir("loop-tools-")
    try:
        config = LoopConfig(name="自检", block_tokens=100000, max_steps=4,
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
    workdir = _tempdir("loop-memory-")
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


def main() -> int:
    check_summary()
    check_compress()
    check_context_limits()
    check_two_blocks()
    check_loop()
    check_compress_failure()
    check_tools()
    check_memory()
    check_remember_hidden()
    print("ok: %d checks" % CHECKS[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
