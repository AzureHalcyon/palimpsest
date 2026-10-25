"""全局冻结版：归档 / `freeze` 时把 live 图与向量复制成 `冻结/`。

只作下一版的起点（`freeze`、旧数据升级、`loop` 启动时并入旧事件）；
本场回忆不读它——recall 直读 live 图，见 `docs/事件状态与读取.md` 的“回忆”一节。
"""

from __future__ import annotations

import os
import shutil

GLOBAL_NAME = "冻结"


def global_dir(data_dir: str) -> str:
    return os.path.join(os.path.abspath(data_dir), GLOBAL_NAME)


def _copy_file(source: str, target: str) -> None:
    if os.path.exists(source):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + ".tmp"
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    elif os.path.exists(target):
        os.remove(target)


def update_global(data_dir: str) -> str:
    """Copy the current live graph/vectors to the global frozen version."""
    source_dir = os.path.abspath(data_dir)
    target_dir = global_dir(source_dir)
    os.makedirs(target_dir, exist_ok=True)
    for name in ("graph.json", "vectors.json"):
        _copy_file(os.path.join(source_dir, name), os.path.join(target_dir, name))
    return target_dir
