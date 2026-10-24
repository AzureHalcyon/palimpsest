"""进程级开关：网页上点了「重启」，循环在下一个安全点自己退出，交给 systemd 拉起来。

为什么不在网页里直接杀进程：SQLite 开着 WAL，硬杀虽然也不会坏，但会留下没写完的
一步（说了一半的正文、没有结果的调用）。让它走到步与步之间再退，这些都不会出现。
"""

from __future__ import annotations

import threading

class StopRequested(Exception):
    """要重启了：从正在做的事里直接退出去，什么都不写进记录。"""


_STOP = threading.Event()
_RELOAD = threading.Event()


def request_stop() -> None:
    _STOP.set()


def stop_requested() -> bool:
    return _STOP.is_set()


def reset() -> None:
    _STOP.clear()

def request_reload() -> None:
    """记录被网页改过（删了一条）：让循环从库里重读两块原文。"""
    _RELOAD.set()


def reload_requested() -> bool:
    return _RELOAD.is_set()


def clear_reload() -> None:
    _RELOAD.clear()