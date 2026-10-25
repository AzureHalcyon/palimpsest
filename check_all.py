"""跑全部离线自测：一条命令，两个入口都跑。

    python check_all.py            # 全部
    python check_all.py loop mv2   # 只跑名字/路径里带这些词的

入口都不联网、不用凭证（假模型、假数据、临时目录），CI 跑的就是这条命令。
"""

from __future__ import annotations

import subprocess
import sys
import time

from testsupport import repo_root

ENTRIES = (
    ("memory_v2 自查", ["memory_v2/tests/check.py"]),
    ("memory_v2 重放证据", ["memory_v2/tests/replay_check.py"]),
    ("loop 自检", ["-m", "loop.selfcheck"]),
    ("文档规范", ["tools/文档/check_docs.py"]),
)


def run(label: str, args: list[str], root: str) -> int:
    print("\n== %s ==" % label, flush=True)
    started = time.time()
    code = subprocess.call([sys.executable, *args], cwd=root)
    print("-- %s：%s（%.1f 秒）"
          % (label, "通过" if code == 0 else "失败", time.time() - started), flush=True)
    return code


def main(argv: list[str]) -> int:
    root = repo_root()
    wanted = [word for word in argv if not word.startswith("-")]
    entries = [entry for entry in ENTRIES
               if not wanted or any(word in entry[0] or word in entry[1][-1]
                                    for word in wanted)]
    if not entries:
        print("没有匹配的入口：%s" % "、".join(label for label, _ in ENTRIES))
        return 2
    failed = [label for label, args in entries if run(label, args, root) != 0]
    print("\n%d/%d 通过" % (len(entries) - len(failed), len(entries)), flush=True)
    if failed:
        print("失败：%s" % "、".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
