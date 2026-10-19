# -*- coding: utf-8 -*-
"""落盘：人读版 `out.txt` + 机器版 `transcripts.jsonl`。

★ **字段刻意保持稳定**：下游的统计与人工判读脚本都按这个 jsonl 读
  （一次会话一行，含每轮的注入内容、条数、有效条目数、`read_z` 的调用与返回、归档结果）
  ⇒ 换实现不用换量具。
★ 真人使用时**同一套格式**，唯一差别是每轮带上**挂钟时间戳**（真人走真时间）。
"""
from __future__ import annotations

import json
import os
from typing import Optional, Sequence


class Transcript:
    def __init__(self, tag: str, out_dir: str = "."):
        self.tag = tag
        self.out_path = os.path.join(out_dir, tag + "_out.txt")
        self.jsonl_path = os.path.join(out_dir, tag + "_transcripts.jsonl")
        self._fh = open(self.out_path, "a", encoding="utf-8")
        self._fj = open(self.jsonl_path, "a", encoding="utf-8")

    def header(self, text: str) -> None:
        self._fh.write("\n" + "=" * 100 + "\n" + text + "\n" + "=" * 100 + "\n")
        self._fh.flush()

    def lines(self, lines: Sequence[str]) -> None:
        self._fh.write("\n".join(lines) + "\n")
        self._fh.flush()

    def session(self, rec: dict) -> None:
        self._fj.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fj.flush()

    def close(self) -> None:
        for f in (self._fh, self._fj):
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def push_block(header: str, texts: Sequence[str]) -> str:
    """注入内容的文本形态（**紧贴生成**的那条 system 消息）。"""
    return header + "\n" + "\n".join("- " + t for t in texts)


def format_archive_line(rounds: int, repairs: int, ar: dict, temp_n: int, alive_n: int) -> str:
    """人读版的一行：这一场归档产出了什么、池子变成什么样。

    ★ 2026-10-06：补上**收口方式** —— 交付判定归模型之后，"跑了几回合"不再说明问题，
      要能一眼看出是**它自己说交付了**，还是**被回合上限截断**、还是**卡住被收口**。
    """
    _d = ar.get("delivered")
    _how = ("★自己判交付" if _d is True
            else "⚠回合用尽截断" if ar.get("cut_short")
            else "⚠卡住收口" if ar.get("stuck")
            else "—")
    # ★ 2026-10-06：把**这一段花了多少秒**也写出来（逐次调用日志由 `engine._drain` 收集）。
    #   为什么要：查"为什么慢"时，`ts_start/ts_end` 只覆盖到收场那一刻，归档整段在它之外。
    _ts = ar.get("timings") or []
    _secs = sum(int(x.get("ms") or 0) for x in _ts) / 1000.0
    _cost = " · %d 次调用 %.1f 秒" % (len(_ts), _secs) if _ts else ""
    _todo = str(ar.get("todo") or "").strip()
    return ("  --- 归档（%d 回合 · %s%s%s）：facts %d · kept %d · again %s · done %s · promote %s\n"
            "%s      note: %s\n      临时池 %d 条（存活 %d）"
            % (rounds, _how, _cost, "·修 JSON %d 次" % repairs if repairs else "",
               len(ar.get("facts") or []), len(ar.get("kept") or []),
               ar.get("again"), ar.get("done"), ar.get("promote"),
               "      todo: %s\n" % _todo[:160] if _todo else "",
               str(ar.get("note"))[:160], temp_n, alive_n))
