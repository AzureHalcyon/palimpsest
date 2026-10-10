#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查文档的结构问题。

它查四件事：

  1. 小节编号顺序倒退
  2. 小节编号重复
  3. 冲突标记残留（<<<<<<< / ======= / >>>>>>>）
  4. 表格头被切断（`|---|` 后面紧跟标题，说明有一行被切掉了）

为什么需要它：改文档的脚本只能断言"替换命中了几处"，看不见"结构"。
一处替换插错了位置，文字读起来还是通的，但编号会倒退或撞号。
这类错误人工很难发现，机器一次就能查出来。

用法：
    python tools/check_docs.py             # 查仓库里所有 .md（跳过 .local/）
    python tools/check_docs.py <路径> ...   # 查指定的文件或目录
退出码：0 表示干净，1 表示有问题。

设计约定：**编号一律连续，不提供"允许乱序"的例外。**
需要例外，说明编号方式有问题，应当改编号，而不是给检查器开洞。
搬进来的历史记录如果编号不连续，就放进归档目录（`99_归档/`）——
那里不进检查范围，文档保持原样。
"""
import io
import os
import re
import sys

# 输出统一按 UTF-8。Windows 控制台默认 GBK，遇到本项目大量使用的中文标点
# 会抛 UnicodeEncodeError，把"脚本报错"误读成"文档有问题"。
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                              # noqa: BLE001
            pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # tools/文档/ → 仓库根

# 跳过：版本控制、本机工作文件、依赖
SKIP_DIRS = {".git", ".local", "99_归档", "__pycache__", ".venv", "venv", "node_modules"}

CONFLICT = re.compile(r"^(<{7}|={7}|>{7})(\s|$)")
BROKEN_TABLE = re.compile(r"\|-{2,}\|###")
YEAR_HEAD = re.compile(r"^(19|20)\d{2}(?![\d.])")   # 日期当标题，不是编号


def headings(text):
    """返回 [(排序键, 编号串, 标题行)]，只取带数字编号的二到四级标题。

    编号连字母后缀一起算：9.4 / 9.4b / 9.4c 是三个不同的号。
    """
    out = []
    for line in text.splitlines():
        m = re.match(r"^(#{2,4})\s+(\S+)", line)
        if not m:
            continue
        raw = m.group(2)
        if YEAR_HEAD.match(raw):
            continue
        num = re.match(r"^(\d+(?:\.\d+)*)([a-z](?:-\d+)?)?", raw)
        if not num:
            continue
        parts = tuple(int(x) for x in num.group(1).split("."))
        suffix = num.group(2) or ""
        label = num.group(1) + suffix
        key = tuple((p, "") for p in parts[:-1]) + ((parts[-1], suffix),)
        out.append((key, label, line.strip()[:72]))
    return out


def check(path):
    with io.open(path, encoding="utf-8") as f:
        text = f.read()
    problems = []
    hs = headings(text)

    # 1) 顺序倒退
    prev_key = prev_label = None
    for key, label, title in hs:
        if prev_key is not None:
            n = min(len(key), len(prev_key))
            if key[:n - 1] == prev_key[:n - 1] and key[n - 1] < prev_key[n - 1]:
                problems.append("顺序倒退：%s 之后出现 %s  →  %s"
                                % (prev_label, label, title))
        prev_key, prev_label = key, label

    # 2) 编号重复
    seen = {}
    for _key, label, title in hs:
        seen.setdefault(label, []).append(title)
    for label, titles in seen.items():
        if len(titles) > 1:
            problems.append("编号重复：%s 出现 %d 次  →  %s"
                            % (label, len(titles),
                               " | ".join(t[:34] for t in titles)))

    lines = text.splitlines()

    # 3) 冲突标记
    for i, line in enumerate(lines, 1):
        if CONFLICT.match(line):
            problems.append("冲突标记残留：L%d  %s" % (i, line[:56]))

    # 4) 表格头被切断
    for i, line in enumerate(lines, 1):
        if BROKEN_TABLE.search(line):
            problems.append("表格头被切断：L%d  %s" % (i, line[:56]))

    return len(hs), problems


def gather(targets):
    files = []
    for t in targets:
        if os.path.isdir(t):
            for dirpath, dirnames, names in os.walk(t):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                for n in sorted(names):
                    if n.endswith(".md"):
                        files.append(os.path.join(dirpath, n))
        else:
            files.append(t)
    return files


def main():
    targets = sys.argv[1:] or [ROOT]
    files = gather(targets)
    total = 0
    for path in files:
        name = os.path.relpath(path, ROOT) if path.startswith(ROOT) else path
        try:
            count, problems = check(path)
        except Exception as exc:                       # noqa: BLE001
            print("  !! 读不了 %s：%s" % (name, exc))
            total += 1
            continue
        if problems:
            total += len(problems)
            print("  %-46s %3d 节  %d 处" % (name, count, len(problems)))
            for p in problems:
                print("        " + p)
        else:
            print("  %-46s %3d 节  ok" % (name, count))
    print("")
    print("checked files: %d | issues: %d" % (len(files), total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
