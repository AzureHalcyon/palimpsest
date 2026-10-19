# -*- coding: utf-8 -*-
"""语料 → 原文文件。**这是唯一知道语料长相的地方。**

★ 为什么要单独一层（`docs/训练.md` §10.1）：
  "唯一的约束是兼容归档。任何语料都只要喂进同一条路，**不需要为它写适配**。"
  ⇒ 所以归档那头（`tools/归档/跑归档.py`）**只认逐字原文**，
    语料的名字、字段、切分方式**一律不许渗进去**。
  ⇒ 为每份语料写适配器 = 按来源分流，只是分流点从流程挪到了数据集。**这一层就是那个分流点**，
    所以它必须**独立、可替换、可以有很多个**——换语料只换这一层，归档那头一个字不改。

产出（喂给归档）：

  <输出目录>/<slug>.原文.txt    逐字原文（含"来源软标记"那几行头部）
  <输出目录>/清单.json          每份的来源软标记（**只给核验**，⛔ 不作训练特征）

用法：
  python tools/转换/语料转原文.py --语料 kdconv --域 film --下标 19,144,241
  python tools/转换/语料转原文.py --语料 hcot --扫 200 --取 20
"""
import argparse
import io
import json
import os
import sys

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "材料"))
import importlib
qu = importlib.import_module("取材料")


def 参数(语料, a):
    """★ 语料各自的构造参数**只在这一层出现**——这是这一层存在的理由。"""
    if 语料 == "kdconv":
        return {"domain": a.域, "split": a.划分}
    if 语料 == "dulemon":
        return {"part": a.部分, "split": a.划分}
    if 语料 == "douban":
        return {"split": a.划分}
    return {}                                                # hcot 不吃参数


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--语料", required=True)
    ap.add_argument("--输出", required=True)
    ap.add_argument("--下标", default="", help="逗号分隔；给了就不用 --取")
    ap.add_argument("--取", type=int, default=0, help="不写下标时：按步长取几份")
    ap.add_argument("--域", default="film")
    ap.add_argument("--部分", default="both")
    ap.add_argument("--划分", default="test")
    a = ap.parse_args()

    os.makedirs(a.输出, exist_ok=True)
    rows = qu.load(a.语料, **参数(a.语料, a))
    if a.下标:
        idx = [int(x) for x in a.下标.split(",") if x.strip()]
    else:
        step = max(1, len(rows) // max(a.取, 1))
        idx = list(range(0, len(rows), step))[:a.取]

    清单 = []
    for i in idx:
        # ★ 下标越界要**明确报出来并跳过**，不要整批崩掉——
        #   实测：拿 train 的下标去 test 上取，`IndexError: list index out of range`，
        #   一句"越界"比一句 traceback 有用（而且挂机时不该因为一份坏输入全停）。
        if i >= len(rows):
            print("  ⛔ 跳过下标 %d（这个划分只有 %d 条）" % (i, len(rows)))
            continue
        text, crit = qu.build(a.语料, i, **参数(a.语料, a))
        slug = qu.slug(a.语料, i, **参数(a.语料, a))
        io.open(os.path.join(a.输出, slug + ".原文.txt"), "w", encoding="utf-8",
                newline="\n").write(text)
        清单.append({"doc_id": slug, "语料": a.语料, "下标": i,
                     "参数": 参数(a.语料, a), "字数": len(text),
                     "轮数": crit.get("轮数")})
    p = os.path.join(a.输出, "清单.json")
    old = []
    if os.path.exists(p):
        try:
            old = json.load(io.open(p, encoding="utf-8")).get("文档") or []
        except Exception:                                    # noqa: BLE001
            old = []
    have = {x["doc_id"] for x in 清单}
    合并 = 清单 + [x for x in old if x.get("doc_id") not in have]
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"语料": a.语料, "文档": 合并}, ensure_ascii=False, indent=2))
    print("写出 %d 份原文（累计 %d）→ %s" % (len(清单), len(合并), a.输出))
    return 0


if __name__ == "__main__":
    sys.exit(main())
