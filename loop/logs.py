"""运行时日志：一个轮转文件，长跑回看用。

文件里记「发生了什么」——收消息、说、调用、结果、压缩、等待、重试、每步一行。
对白和工具结果的全文已经在 SQLite 的 entries 里，日志不重复存正文。
控制台只留「说」和每步一行，不刷屏。
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s %(levelname)s %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"


def setup(data_dir: str, name: str = "loop",
          max_bytes: int = 5 * 1024 * 1024,
          backups: int = 5) -> logging.Logger:
    """在 data_dir 下开一个 loop.log；同一个名字只装一次 handler。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    os.makedirs(data_dir or ".", exist_ok=True)
    handler = RotatingFileHandler(os.path.join(data_dir, "loop.log"),
                                  maxBytes=max_bytes, backupCount=backups,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT, _DATE))
    logger.addHandler(handler)
    return logger
