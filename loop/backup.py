"""每天留一份底：记录、记忆、自我描述。

为什么要有：这几样东西只有一份。记录被她自己写坏、记忆被一句"忘掉以前"删干净、
自我描述被改得不像她——没有底就只能认。归档导出（`archive/`）不是底：那份是
她判断过要留的历史，判断本身就可能被带歪。

放在 `<数据>/snapshots/<日期>/`，**在工作区外面**：`run` 的写权限只到工作区，
所以就算哪天真被牵着跑了一条 `rm -rf`，这份底也删不到。

一天一份，留 `KEEP_DAYS` 天。跑在 `_maintain` 里（十分钟一次的那趟闲活），
失败只记日志——备份坏了不该影响在场。
"""

from __future__ import annotations

import io
import os
import shutil
import sqlite3
import time
from typing import List

KEEP_DAYS = 7
NAME = "snapshots"

# 只收"独一份"的东西：记录库、记忆、自我描述、设置。工作区里除了日记一概不要
# ——那里是她随手放东西的地方，可能有几十兆，也可能是垃圾。
DB_NAME = "loop.sqlite3"
FILES = ("persona.md", "persona.log", "persona.stamp",
         "settings.json", "prompts.json")
MEMORY_DIR = "memory"
DIARY_DIR = os.path.join("workspace", "日记")
# 图片库：收进来的图片都在这儿（和工作区平级，`run` 碰不到）。记录里只存路径，
# 文件本身要是丢了就再也找不回来——所以跟记录库一起备。
IMAGES_DIR = "图片"
STAMP = "manifest.json"


def folder_of(config) -> str:
    return os.path.join(str(getattr(config, "data_dir", ".") or "."), NAME)


def path_of(config, date: str) -> str:
    return os.path.join(folder_of(config), date)


def today(moment: float = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(moment or time.time()))


def _say(log, text: str) -> None:
    if log:
        try:
            log(text)
        except Exception:
            pass


def _copy_db(source: str, target: str) -> bool:
    """用 sqlite 自己的备份接口：WAL 里还没落盘的也一起带走，比 `cp` 稳。"""
    if not os.path.isfile(source):
        return False
    src = sqlite3.connect(source, timeout=10)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return True


def _copy_tree(source: str, target: str) -> bool:
    if not os.path.isdir(source):
        return False
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("*.tmp", "tmp"))
    return True


def run(config, log=None, moment: float = None) -> str:
    """写一份今天的快照，顺手清掉过期的。返回目录；没有可备份的东西就是空串。"""
    data = str(getattr(config, "data_dir", ".") or ".")
    date = today(moment)
    target = path_of(config, date)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError as exc:
        _say(log, "[快照] 目录建不出来：%s" % exc)
        return ""
    items: List[str] = []
    try:
        if _copy_db(os.path.join(data, DB_NAME), os.path.join(target, DB_NAME)):
            items.append(DB_NAME)
    except Exception as exc:                      # 库正忙也不该让整趟失败
        _say(log, "[快照] 记录库没备上：%s" % exc)
    for name in FILES:
        source = os.path.join(data, name)
        if not os.path.isfile(source):
            continue
        try:
            shutil.copy2(source, os.path.join(target, name))
            items.append(name)
        except OSError as exc:
            _say(log, "[快照] %s 没备上：%s" % (name, exc))
    if _copy_tree(os.path.join(data, MEMORY_DIR), os.path.join(target, MEMORY_DIR)):
        items.append(MEMORY_DIR + "/")
    if _copy_tree(os.path.join(data, DIARY_DIR), os.path.join(target, "日记")):
        items.append("日记/")
    if _copy_tree(os.path.join(data, IMAGES_DIR), os.path.join(target, IMAGES_DIR)):
        items.append(IMAGES_DIR + "/")
    if not items:
        try:
            os.rmdir(target)
        except OSError:
            pass
        return ""
    try:
        with io.open(os.path.join(target, STAMP), "w", encoding="utf-8") as handle:
            handle.write('{"date": "%s", "items": [%s]}\n'
                         % (date, ", ".join('"%s"' % item for item in items)))
    except OSError:
        pass
    prune(config, log, moment)
    return target


def _stamps(config) -> List[str]:
    folder = folder_of(config)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(name for name in names
                  if len(name) == 10 and name[4] == "-" and name[7] == "-"
                  and name.replace("-", "").isdigit())


def prune(config, log=None, moment: float = None) -> int:
    """只删自己写的日期目录，别的一概不碰。"""
    keep = _stamps(config)
    if len(keep) <= KEEP_DAYS:
        return 0
    removed = 0
    for date in keep[:len(keep) - KEEP_DAYS]:
        target = path_of(config, date)
        if os.path.dirname(os.path.abspath(target)) != \
                os.path.abspath(folder_of(config)):
            continue                              # 只在快照目录里动手
        try:
            shutil.rmtree(target)
            removed += 1
        except OSError as exc:
            _say(log, "[快照] 旧的删不掉 %s：%s" % (date, exc))
    return removed


def due(config, moment: float = None) -> bool:
    """今天那份还没写。"""
    return not os.path.isdir(path_of(config, today(moment)))


def maybe(config, log=None, moment: float = None) -> str:
    if not due(config, moment):
        return ""
    made = run(config, log, moment)
    if made:
        _say(log, "[快照] 留了一份：%s" % os.path.basename(made))
    return made
