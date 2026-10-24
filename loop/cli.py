"""命令行入口：把循环跑起来。

    python -m loop --name 林晚 --max-steps 20

步骤 0 只有想、说、挂起；记忆与真摘要还没接上（见 docs/持续在场-API实现.md 第 7 节）。
"""

from __future__ import annotations

import argparse
import os
import sys

from memory_v2.core.config import ChatConfig as MemoryChatConfig
from memory_v2.core.config import Credentials as MemoryCredentials
from memory_v2.core.embed import Embedder
from memory_v2.core.llm import ChatError
from memory_v2.read.write import write_memory

from .compress import Compressor
from .config import LoopConfig, LoopConfigError, load_credentials
from .driver import Loop
from .memory_bridge import MemoryBridge
from .store import LoopStore


def _k(tokens: int) -> str:
    return "%.1fk" % (tokens / 1000.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loop", description="持续在线的循环运行时")
    parser.add_argument("--credentials", default="", help="凭证 YAML（默认 ~/.dsh/.credentials.yaml）")
    parser.add_argument("--data", default="data/loop", help="上下文数据目录（SQLite 落在这里）")
    parser.add_argument("--memory-data", default="data/memory",
                        help="memory_v2 的图目录")
    parser.add_argument("--name", required=True, help="sink 第一句的名字")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--max-steps", type=int, default=20, help="0 表示不设上限")
    parser.add_argument("--block-tokens", type=int, default=20000,
                        help="当前块攒到多少 token 就压（调试时调小）")
    parser.add_argument("--summary-model", default="deepseek-flash",
                        help="摘要模型；填 qwen-plus 就走凭证里的百炼端点")
    parser.add_argument("--summary-provider", default="auto",
                        choices=("auto", "deepseek", "bailian"),
                        help="摘要端点；auto 按模型名判断")
    parser.add_argument("--lexical", action="store_true",
                        help="打开词面检索；默认只用嵌入")
    parser.add_argument("--recent-steps", type=int, default=0,
                        help="最近 N 步写的事件暂不召回；0=关（默认）")
    parser.add_argument("--context-budget", type=int, default=80000)
    parser.add_argument("--park-seconds", type=float, default=0.0,
                        help="测试用：挂起后等多少秒再继续；0 表示挂起就停"
                             "（正常运行时等真实事件，不要空转）")
    parser.add_argument("--say", action="append", default=[],
                        help="开场塞进记录的用户消息，可重复")
    return parser


def _utf8_streams() -> None:
    """管道输出按 UTF-8 写，别让控制台代码页把中文弄坏。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def main(argv=None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)
    try:
        credentials = load_credentials(args.credentials)
    except LoopConfigError as exc:
        print("凭证不可用：%s" % exc, file=sys.stderr)
        return 1

    try:
        memory_credentials = MemoryCredentials.load(args.credentials)
        embedder = Embedder(MemoryChatConfig(), memory_credentials)
    except Exception as exc:
        print("记忆嵌入不可用：%s" % exc, file=sys.stderr)
        embedder = None

    config = LoopConfig(name=args.name, model=args.model,
                        temperature=args.temperature, max_tokens=args.max_tokens,
                        context_budget=args.context_budget,
                        block_tokens=args.block_tokens,
                        summary_model=args.summary_model,
                        summary_provider=args.summary_provider,
                        lexical=args.lexical,
                        recent_steps=args.recent_steps,
                        park_seconds=args.park_seconds,
                        max_steps=args.max_steps,
                        data_dir=args.data)
    store = LoopStore(config.db_path())
    memory = MemoryBridge(args.memory_data, embedder=embedder,
                          lexical=config.lexical,
                          recent_steps=config.recent_steps)

    def on_speak(text: str) -> None:
        print("[说] %s" % text, flush=True)

    def on_step(info: dict) -> None:
        print("[%d] %s · 当前块 %s/%s · 上下文 %s/%s" % (
            info["step"], info["kind"],
            _k(info["current_tokens"]), _k(config.block_tokens),
            _k(info["prompt_tokens"]), _k(config.context_budget)), flush=True)

    loop = Loop(config, credentials, store, Compressor(config, credentials), memory,
                on_speak=on_speak, on_step=on_step,
                echo=lambda text: print(text, flush=True))
    memory.writer = lambda material: write_memory(loop.client, material)

    print("名字：%s · 模型：%s · 摘要：%s · 嵌入：%s · 词面：%s · 预算 %s · 块 %s · 库 %s · 记忆 %s" % (
        config.name, config.model, config.summary_model,
        "百炼" if embedder is not None and getattr(embedder, "available", False) else "不可用",
        "开" if config.lexical else "关",
        _k(config.context_budget), _k(config.block_tokens),
        os.path.abspath(config.db_path()), os.path.abspath(args.memory_data)), flush=True)

    for text in args.say:
        loop.inject_user(text)

    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n[中断]", flush=True)
    except ChatError as exc:
        print("模型调用失败：%s" % exc, file=sys.stderr)
        return 1
    print("[结束] %d 步，%d 次压缩" % (loop.steps, loop.compressions), flush=True)
    store.close()
    return 0
