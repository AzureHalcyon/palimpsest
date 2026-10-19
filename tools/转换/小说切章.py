# -*- coding: utf-8 -*-
"""小说 → 原文（整本）＋ 章边界。**这是唯一知道小说长相的地方。**

★ 为什么要"整本一个 doc、章只是窗"（用户提的办法，2026-10-10）：
  我们手上的语料（KdConv / HCoT / DuLeMon / Douban）**每份都是自足的短对话**，
  问的东西大多在最近两轮里就有 ⇒ **局部注意力就够答**，考不出"记录 / 图"。
  小说不一样：**跨章的回忆是真的**（第 20 章提一个人，首现在第 3 章）。
  ⇒ 一次解决三件事：
    1. **长距**：线索放后面、答案在前面的很远处 ⇒ 才考得出记忆层；
    2. **归档边界是现成的**：**一章 = 一次归档**，
       而 `设计 3.5`/§10.6 说的"调制 = 自上次归档以来"在真实数据上**有了对应物**；
    3. **出题有了机械来源**：实体**首现** vs 后文**重提** ⇒
       "关于 X，之前说过什么"，答案 = 早先那些位置。**不靠模型编题**。

⚠️ 因此 **doc 必须是整本**（位置要能指到很早的地方），**章只用来切归档窗**。
   建图一次只吃一个窗；节点区间要**加上窗的基准偏移**换成全文坐标，
   否则落盘校验 1（`取(doc,start,end)` 逐字取回）过不了。

产出：
  <输出>/<名>.原文.txt      整本（逐字）
  <输出>/<名>.章.json       [{章, 标题, start, end, 字数}]
  <输出>/清单.json          来源软标记（只给核验）

用法：python tools/转换/小说切章.py --输入 <小说.txt> --名 某小说 --输出 docs/语料/小说
"""
import argparse
import io
import json
import os
import re
import sys

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# 章标题的常见写法都要认：第X回 / 第X章 / 第十二回 ……
# ★ 但**光靠"第X回"开头会误判正文**：实测《红楼梦》行 163 是正文
#   「**第四回中**既将薛家母子在荣府中寄居等事略已表明，此回暂可不写了。」
#   ⇒ 回目行的三个特征一起用：① 以"第X回/章/卷"开头；② **很短**；③ **不含句读**。
标题 = re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百千两零〇]+\s*[回章节卷篇])\s*(.*)$")
句读 = re.compile(r"[。！？；，、：,.!?;:]")


def 是回目(line):
    s = line.strip()
    m = 标题.match(s)
    if not m:
        return None
    if len(s) > 60 or 句读.search(s):
        return None                                          # 太长/带句读 ⇒ 是正文
    return m


def 切(text):
    lines = text.splitlines(keepends=True)
    heads, pos = [], 0
    for i, ln in enumerate(lines):
        if 是回目(ln):
            m = 标题.match(ln.strip())
            heads.append({"标题": (m.group(1) + " " + m.group(2)).strip(),
                          "行": i, "start": pos})
        pos += len(ln)
    if not heads:
        return []
    # 每章到下一个标题之前；末尾那章到最后
    out = []
    for k, h in enumerate(heads):
        end = heads[k + 1]["start"] if k + 1 < len(heads) else len(text)
        out.append({"章": k + 1, "标题": h["标题"], "start": h["start"],
                    "end": end, "字数": end - h["start"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--输入", required=True)
    ap.add_argument("--名", required=True)
    ap.add_argument("--输出", required=True)
    ap.add_argument("--最小字数", type=int, default=30,
                    help="★ 只管**目录页/误切**那类垃圾（几十字）。"
                         "⛔ 别用它去吞'真正的短章'——实测《红楼梦》第三十一回在源 txt 里"
                         "被截断成 ~180 字（**采集故障**），静默合并会把缺口藏起来。"
                         "短章改为**记进清单**，让人看得见")
    ap.add_argument("--留头", action="store_true",
                    help="保留第一个回目**之前**的内容（默认丢掉：那多半是版权页与简介，"
                         "不是正文；丢掉前会把它记进 清单.json 供核验）")
    a = ap.parse_args()

    text = io.open(a.输入, encoding="utf-8", errors="replace").read()
    ch = 切(text)
    if not ch:
        print("⛔ 一个章标题都没认出来。看一眼文件头：%r" % text[:200])
        return 1
    # ★ 正文从**第一个回目**开始：挡在正文之外的东西（版权页 / 简介 / 目录）
    #   会让"整本一个 doc"的第一个位置落在一堆噪声上。
    前言 = ""
    if not a.留头 and ch[0]["start"] > 0:
        前言 = text[:ch[0]["start"]]
        基 = ch[0]["start"]
        text = text[基:]
        for c in ch:
            c["start"] -= 基
            c["end"] -= 基
    # 合并过短的章（目录页、误切）
    merged = []
    for c in ch:
        if merged and merged[-1]["字数"] < a.最小字数:
            merged[-1]["end"] = c["end"]
            merged[-1]["字数"] = merged[-1]["end"] - merged[-1]["start"]
            merged[-1]["标题"] += " + " + c["标题"]
        else:
            merged.append(dict(c))
    for k, c in enumerate(merged):
        c["章"] = k + 1

    os.makedirs(a.输出, exist_ok=True)
    io.open(os.path.join(a.输出, a.名 + ".原文.txt"), "w", encoding="utf-8",
            newline="\n").write(text)
    io.open(os.path.join(a.输出, a.名 + ".章.json"), "w", encoding="utf-8",
            newline="\n").write(json.dumps({"doc_id": a.名, "章": merged},
                                           ensure_ascii=False, indent=2))
    p = os.path.join(a.输出, "清单.json")
    old = {"文档": []}
    if os.path.exists(p):
        try:
            old = json.load(io.open(p, encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            pass
    docs = [d for d in old.get("文档") or [] if d.get("doc_id") != a.名]
    短章 = [{"章": c["章"], "标题": c["标题"], "字数": c["字数"]}
            for c in merged if c["字数"] < 300]
    docs.append({"doc_id": a.名, "体裁": "小说", "字数": len(text),
                 "章数": len(merged), "来源文件": os.path.basename(a.输入),
                 "丢掉的前言字数": len(前言),
                 "前言开头": 前言[:120].replace("\r", "").replace("\n", " / "),
                 "短章（疑似采集缺陷，⚠️ 不是切错了）": 短章})
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        json.dumps({"文档": docs}, ensure_ascii=False, indent=2))

    sizes = sorted(c["字数"] for c in merged)
    print("整本 %d 字 · 切出 %d 章 · 章字数 中位 %d · 最小 %d · 最大 %d"
          % (len(text), len(merged), sizes[len(sizes) // 2], sizes[0], sizes[-1]))
    print("前 3 章：", " / ".join(c["标题"] for c in merged[:3]))
    print("写到 %s" % a.输出)
    return 0


if __name__ == "__main__":
    sys.exit(main())
