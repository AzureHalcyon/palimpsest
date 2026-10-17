# -*- coding: utf-8 -*-
"""companion v0.1 —— 一个有长期记忆的陪伴系统的运行时骨架。

本仓库**自洽**：它不依赖任何外部仓库、不携带任何具体数据（底色 / 记忆 / 语料 /
运行产物 / 密钥都不在里面），数据一律从外部路径或凭证文件读。

看这三份就够：
    README.md                 它是什么、怎么跑、边界在哪
    docs/架构与数据流.md        一条数据从哪进、经过谁、落到哪
    docs/参数与依据.md          每个数字为什么是这个数字
"""
__version__ = "0.1.0"

from .config import (ChatConfig, Config, Credentials, CredentialsError, DreamConfig,  # noqa: F401
                     EmbedConfig, ReaderConfig, SalienceConfig, SessionConfig, TimeConfig,
                     cred_path, dream_topk)
from .dreaming import dream, empty_archive  # noqa: F401
from .embedder import (Embedder, HashingEmbedder, frame_angle, l2norm,  # noqa: F401
                       topk_overlap)
from .engine import Engine  # noqa: F401
from .llm import BaseLLM, ScriptedLLM, parse_json  # noqa: F401
from .pending import PendingStore  # noqa: F401
from .pool import LongPool, TempItem, TempPool, sample_gap  # noqa: F401
from .reader import Hit, Reader, ReadResult, select_k, softmax_p  # noqa: F401
from .session import Session  # noqa: F401
from .transcript import Transcript, push_block  # noqa: F401
from .aggregate import (Bucket, MINUTE_MS, HOUR_MS, DAY_MS, bucketize, coverage,  # noqa: F401
                        percentile_of, render_reading, rollup, trend)  # noqa: F401
from .activity import ActivityLog  # noqa: F401
from .budgets import DELIVERY, LLM, BudgetStore  # noqa: F401
from .deliver import DeliveryLog, Gate  # noqa: F401
from .channels import Channel, ChannelCatalog  # noqa: F401
from .reading import ReadingBuilder  # noqa: F401
from .web import GRADE, Search, fence, may_support_about_him  # noqa: F401
from .wakeups import Wakeup, WakeupStore  # noqa: F401

__all__ = [
    "__version__", "Config", "EmbedConfig", "ChatConfig", "ReaderConfig", "TimeConfig",
    "DreamConfig", "SessionConfig", "SalienceConfig", "Credentials", "CredentialsError",
    "dream_topk", "cred_path", "Engine", "Session", "Reader", "ReadResult", "Hit", "select_k",
    "softmax_p", "LongPool", "TempPool", "TempItem", "sample_gap", "Embedder", "HashingEmbedder",
    "l2norm", "frame_angle", "topk_overlap", "BaseLLM", "ScriptedLLM", "parse_json", "dream",
    "empty_archive", "Transcript", "push_block", "PendingStore", "Wakeup", "WakeupStore", "Channel", "ChannelCatalog", "ReadingBuilder", "BudgetStore", "LLM", "DELIVERY", "ActivityLog", "Gate", "DeliveryLog", "Search", "fence", "may_support_about_him", "GRADE",
    "Bucket", "bucketize", "rollup", "coverage", "trend", "percentile_of", "render_reading",
]
