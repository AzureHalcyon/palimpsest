"""离线自测共用的临时目录。

临时库建在仓库的 `data/` 下（已 gitignore）；系统临时目录在受限环境里不一定可写。
两个自测入口（`loop.selfcheck`、`memory_v2/tests/check.py`）都走这里。
"""

from __future__ import annotations

import os
import uuid

ROOT = os.path.dirname(os.path.abspath(__file__))


def repo_root() -> str:
    """仓库根目录；不依赖当前工作目录。"""
    return ROOT


def tempdir(prefix: str) -> str:
    """在仓库 `data/` 下建一个空的临时目录，返回绝对路径。

    跑完不自动清：失败时留下的现场比干净更有用，`data/` 也不进版本控制。
    """
    parent = os.path.join(ROOT, "data")
    os.makedirs(parent, exist_ok=True)
    path = os.path.join(parent, prefix + uuid.uuid4().hex[:8])
    os.makedirs(path)
    return path
