#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从语料里取一段材料，写成标注能吃的形态。

为什么要有这个脚本：
  材料是手抄的，就没法回答"换了语料输出会变成什么样"——而那正是最要紧的变量。
  取样必须**可重算、可指名**（哪一份语料、第几条），否则两遍试跑之间说不清差在哪。

统一的材料形态（annotate.py 认这个）：

    # 测试材料（<语料> · <位置>）

    > 源：<文件>（<许可>）
    > <做了哪些变换、去掉了什么，以及为什么>

    ---

    <正文>

对照实验的一条纪律：**不同语料之间要控住长度**。
  长度是最大的混淆项——短材料本来就没有关系可标。所以取样按"字数最接近目标"来挑，
  而不是随便取前 N 条。见 `pick()`。

用法：
    python tools/取材料.py kdconv --domain film --split train --index 33
    python tools/取材料.py kdconv --domain film --split train --longest 5
    python tools/取材料.py hcot --index 0
    python tools/取材料.py dulemon --part both --split test --index 0
    python tools/取材料.py douban --index 0
    python tools/取材料.py list                       # 各语料能取多少、多长

默认写到 `.local/材料/`；`--out -` 只打印不落盘。
数据本身不进版本控制（见 docs/语料评估.md 第四节）。
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # tools/材料/ → 仓库根
YU = os.path.join(ROOT, "docs", "语料")
DEFAULT_OUT = os.path.join(ROOT, ".local", "材料")

# 语料自带的分词空格。**只对"分词过的"那两种语料用**（DuLeMon · Douban）。
#
# ★ 这里必须去干净，不能只去"汉字与汉字之间"的：
#   实测踩过——只去汉字之间时，材料里还剩 `还可以 ， 长肉`（标点前的空格留着），
#   而模型写的是正常中文 `还可以，长肉`，于是**写对了的模型反而锚不住**，
#   12 个节点被判成"丢节点"，差一点被记成"这个模型不合规"。
#   ⇒ 去不干净 = 给核验器埋一个假阳性。
SEG_WS = re.compile(r"\s+")
# HCoT 正文里嵌着策略标注，形如 `支持者[Restatement]：`。
HCT_TAG = re.compile(r"\[([A-Za-z][A-Za-z ]*)\]")
# DuLeMon 每轮末尾挂着人设下标，形如 `\tU4,B4`。
DLM_MARK = re.compile(r"\t([UB]\d[^\t]*)\s*$")


def deseg(text):
    """去掉分词空格：**所有**空白都去掉。

    ⚠️ 只对分词过的语料用（DuLeMon · Douban）。KdConv 没分词，
    而且它的标题里 `《 狮子王》` 那种空格是原文的一部分——**不要动它**。
    ⇒ 每个语料用哪种处理，都写在它自己那份材料的说明块里。
    """
    return SEG_WS.sub("", text)


# ------------------------------------------------------------------ KdConv
def kdconv(domain="film", split="train"):
    p = os.path.join(YU, "KdConv", "data", domain, split + ".json")
    return json.load(io.open(p, encoding="utf-8"))


def kdconv_build(rows, i, domain="film", split="train", **_):
    c = rows[i]
    ms = [(m.get("message") or "").strip() for m in (c.get("messages") or [])]
    body = "\n".join("%s：%s" % ("甲" if k % 2 == 0 else "乙", m)
                     for k, m in enumerate(ms))
    head = ("# 测试材料（KdConv · %s · 第 %d 段：%s）\n\n"
            "> 源：`KdConv/data/%s/%s.json` 第 %d 段（Apache-2.0）。\n"
            "> 去掉了 `attrs`（知识三元组是外部知识，不在对话里），另存为判据。\n"
            "> 说话人按次序甲/乙交替——KdConv 原数据没标说话人。\n"
            % (domain, i, c.get("name") or "", domain, split, i))
    crit = {"语料": "kdconv", "域": domain, "下标": i, "名称": c.get("name"),
            "三元组": [{"轮": k, "attrs": m.get("attrs")}
                       for k, m in enumerate(c.get("messages") or []) if m.get("attrs")]}
    return head, body, crit


def kdconv_candidates(rows):
    return [{"i": i, "chars": sum(len(m.get("message") or "") for m in (c.get("messages") or [])),
             "turns": len(c.get("messages") or []),
             "name": c.get("name") or ""} for i, c in enumerate(rows)]


# -------------------------------------------------------------------- HCoT
def hcot():
    p = os.path.join(YU, "HCoT", "All_merged_data.json")
    return json.load(io.open(p, encoding="utf-8"))


def hcot_build(rows, i, **_):
    r = rows[i]
    tags, lines = [], []
    for ln in (r.get("multi_turn_dialogue") or "").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        for t in HCT_TAG.findall(ln):
            tags.append(t)
        lines.append(HCT_TAG.sub("", ln))
    head = ("# 测试材料（HCoT-Corpus · 第 %d 条）\n\n"
            "> 源：`HCoT/All_merged_data.json` 第 %d 条（论文侧 CC BY 4.0）。\n"
            "> ⚠️ 内容是**真实心理求助**，只做本地测试，不抄进仓库。\n"
            "> 去掉了嵌在正文里的策略标注（`[Restatement]` 等），另存为判据。\n"
            % (i, i))
    crit = {"语料": "hcot", "下标": i, "策略序列": tags,
            "original_question": (r.get("original_question") or "")[:200]}
    return head, "\n".join(lines), crit


def hcot_candidates(rows):
    return [{"i": i, "chars": len(r.get("multi_turn_dialogue") or ""),
             "turns": len([x for x in (r.get("multi_turn_dialogue") or "").splitlines() if x.strip()]),
             "name": (r.get("original_question") or "")[:24]} for i, r in enumerate(rows)]


# ----------------------------------------------------------------- DuLeMon
def dulemon(part="both", split="test"):
    z = os.path.join(YU, "DuLeMon", "DuLeMon.zip")
    name = "DuLeMon/%s/%s.json" % (part, split)
    with zipfile.ZipFile(z) as f:
        txt = f.read(name).decode("utf-8")
    return [json.loads(ln) for ln in txt.splitlines() if ln.strip()]


def dulemon_build(rows, i, part="both", split="test", **_):
    r = rows[i]
    marks, lines = [], []
    for k, t in enumerate(r.get("conversation") or []):
        m = DLM_MARK.search(t)
        if m:
            marks.append({"轮": k, "人设下标": m.group(1)})
            t = t[:m.start()]
        lines.append(deseg(t.strip()).replace("Usr:", "用户：").replace("Bot:", "机器人："))
    persona = {k: v for k, v in r.items() if k != "conversation"}
    head = ("# 测试材料（DuLeMon · %s · %s · 第 %d 条）\n\n"
            "> 源：`DuLeMon.zip!DuLeMon/%s/%s.json` 第 %d 条"
            "（仓库侧 Apache-2.0，数据包条款未探）。\n"
            "> 去掉了每轮末尾的人设下标（`\\tU4,B4`），另存为判据。\n"
            "> 去掉了语料自带的分词空格（`我 喜欢 运动` → `我喜欢运动`）"
            "——不去掉就跟\"一字不差\"那条约束打架。\n"
            % (part, split, i, part, split, i))
    crit = {"语料": "dulemon", "部分": part, "下标": i, "人设兑现": marks,
            "人设": persona}
    return head, "\n".join(lines), crit


def dulemon_candidates(rows):
    out = []
    for i, r in enumerate(rows):
        cs = r.get("conversation") or []
        out.append({"i": i,
                    "chars": sum(len(deseg(DLM_MARK.sub("", t).strip())) for t in cs),
                    "turns": len(cs), "name": (cs[0][:20] if cs else "")})
    return out


# ------------------------------------------------------------------ Douban
def douban(split="test"):
    p = os.path.join(YU, "Douban", "DoubanConversaionCorpus", split + ".txt")
    rows = []
    with io.open(p, encoding="utf-8") as f:
        for ln in f:
            cols = ln.rstrip("\n").split("\t")
            if len(cols) >= 3:
                rows.append(cols)
    return rows


def douban_build(rows, i, split="test", **_):
    cols = rows[i]
    # 实测一行 8 列：[0] 标签 · [1..n-2] session 各轮 · [n-1] 候选回复。
    turns = [deseg(c.strip()) for c in cols[1:-1] if c.strip()]
    body = "\n".join("%s：%s" % ("甲" if k % 2 == 0 else "乙", t)
                     for k, t in enumerate(turns))
    head = ("# 测试材料（Douban Conversation Corpus · 第 %d 行）\n\n"
            "> 源：`Douban/DoubanConversaionCorpus/%s.txt` 第 %d 行（⚠️ **无许可证**，只做本地测试）。\n"
            "> 取了 session 那几轮，去掉了候选回复与人工判断（另存为判据）；\n"
            "> 去掉了语料自带的分词空格；说话人原数据没标，按次序甲/乙交替。\n"
            % (i, split, i))
    crit = {"语料": "douban", "下标": i, "候选回复": cols[-1], "标签": cols[0],
            "列数": len(cols)}
    return head, body, crit


def douban_candidates(rows):
    out = []
    for i, cols in enumerate(rows):
        ts = [deseg(c.strip()) for c in cols[1:-1] if c.strip()]
        out.append({"i": i, "chars": sum(len(t) for t in ts), "turns": len(ts),
                    "name": (ts[0][:20] if ts else "")})
    return out


# --------------------------------------------------------------------- 骨架
SPECS = {
    "kdconv":  {"load": kdconv,  "build": kdconv_build,  "cand": kdconv_candidates,
                "note": "属性问答，轮与轮之间没有承接"},
    "hcot":    {"load": hcot,    "build": hcot_build,    "cand": hcot_candidates,
                "note": "咨询式支持，每轮带策略标注"},
    "dulemon": {"load": dulemon, "build": dulemon_build, "cand": dulemon_candidates,
                "note": "16 轮，人设的兑现位置有标注"},
    "douban":  {"load": douban,  "build": douban_build,  "cand": douban_candidates,
                "note": "生活化、短、有噪音"},
}

# 每种语料一份缓存：批量取材料时不要反复读 109 MB 的文件。
_CACHE = {}


def load(corpus, **kw):
    key = (corpus, tuple(sorted(kw.items())))
    if key not in _CACHE:
        _CACHE[key] = SPECS[corpus]["load"](**kw)
    return _CACHE[key]


def candidates(corpus, **kw):
    return SPECS[corpus]["cand"](load(corpus, **kw))


def pick(corpus, n, target=700, **kw):
    """按"字数最接近 target"挑 n 条，并让它们在下标上散开。

    ★ 为什么按字数挑：跨语料的对照里，长度是最大的混淆项。
      随机取会让"长语料"看起来更像有话可说，而那只是长度。
    """
    cs = candidates(corpus, **kw)
    cs = [c for c in cs if c["chars"] > 0]
    cs.sort(key=lambda c: abs(c["chars"] - target))
    chosen = cs[:max(n * 3, n)]
    chosen.sort(key=lambda c: c["i"])
    step = max(1, len(chosen) // n)
    return chosen[::step][:n]


def build(corpus, i, **kw):
    head, body, crit = SPECS[corpus]["build"](load(corpus, **kw), i, **kw)
    return head + "\n---\n\n" + body + "\n", crit


def slug(corpus, i, **kw):
    extra = "-".join(str(v) for v in kw.values() if v)
    return ("%s-%s-%d" % (corpus, extra, i)).replace("--", "-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", nargs="?", choices=sorted(SPECS) + ["list"])
    ap.add_argument("--domain", default="film")
    ap.add_argument("--split", default="train")
    ap.add_argument("--part", default="both")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--longest", type=int, default=0, help="只列最长的 N 条，不写盘")
    ap.add_argument("--target", type=int, default=700, help="挑材料时的目标字数")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    if args.corpus in (None, "list"):
        sys.stdout.write("各语料能取的量：\n")
        for name, spec in SPECS.items():
            kw = {"kdconv": {"domain": args.domain, "split": args.split},
                  "hcot": {}, "dulemon": {"part": args.part, "split": args.split},
                  "douban": {"split": args.split}}.get(name, {})
            try:
                cs = candidates(name, **kw)
            except Exception as e:                      # noqa: BLE001
                sys.stdout.write("  %-8s 取不到：%s\n" % (name, e))
                continue
            lens = sorted(c["chars"] for c in cs)
            sys.stdout.write("  %-8s %5d 条 · 字数 中位 %d · 最长 %d · %s\n"
                             % (name, len(cs), lens[len(lens) // 2], lens[-1],
                                spec["note"]))
        return 0

    kw = {"kdconv": {"domain": args.domain, "split": args.split},
          "hcot": {}, "dulemon": {"part": args.part, "split": args.split},
          "douban": {"split": args.split}}.get(args.corpus, {})

    if args.longest:
        cs = sorted(candidates(args.corpus, **kw), key=lambda c: -c["chars"])
        sys.stdout.write("最长的 %d 条：\n" % args.longest)
        for c in cs[:args.longest]:
            sys.stdout.write("  下标 %5d · %4d 字 · %2d 轮 · %s\n"
                             % (c["i"], c["chars"], c["turns"], c["name"]))
        return 0

    text, crit = build(args.corpus, args.index, **kw)
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, slug(args.corpus, args.index, **kw))
    io.open(base + ".md", "w", encoding="utf-8", newline="\n").write(text)
    io.open(base + ".判据.json", "w", encoding="utf-8", newline="\n").write(
        json.dumps(crit, ensure_ascii=False, indent=2))
    sys.stdout.write("材料：%s.md（%d 字）\n判据：%s.判据.json\n"
                     % (base, len(text), base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
