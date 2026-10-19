#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一份标注读成人能看的样子：节点带引文、组带成员、边带两头的原话。

为什么要有它：
  汇总里的数字（节点数、组数、边数、边类型）**不能替代看**。
  "起因 × 21"这种统计，只有把每条边两头的原话并排读一遍，
  才知道那 21 条是真关系还是套话。数字是代理，原话是目标。

用法：
    python tools/看标注.py <标注.json> [--材料 <材料.md>]
    python tools/看标注.py <目录> [--材料扩展 .md]        # 一个目录里的全看
    python tools/看标注.py <标注.json> --只边               # 只看边
"""
import argparse
import glob
import io
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def load_material(path):
    if not path or not os.path.exists(path):
        return None
    text = io.open(path, encoding="utf-8").read()
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1].strip()
    return text


def side(material, quote, width=60):
    """核一下这条引文在材料里找不找得到，并把状态带在引文前面。

    ★ 核验不许省：标出来的东西"看起来对"和"锚得住"是两件事。
    """
    q = (quote or "").strip()
    if material is None:
        return "  ", q[:width]
    c = material.count(q)
    tag = "✓ " if c == 1 else ("✗ " if c == 0 else "? ")
    return tag, (q[:width] + ("…" if len(q) > width else ""))


def nearest(material, quote, topn=2):
    """引文定位不到时，找出材料里最像的那几行。

    ★ 这一步是为了**分清两种失败**：
      是"编了一句原文里没有的话"，还是"只差标点/空格/几个字"。
      两者的处置完全不同——前者是模型不合规，后者是核验太脆。
      实测踩过：不去分辨就下结论，会把一个标点问题写成"模型在编"。
    """
    import difflib
    q = (quote or "").strip()
    lines = [ln.strip() for ln in material.splitlines() if ln.strip()]
    best = difflib.get_close_matches(q, lines, n=topn, cutoff=0.0)
    return best


def show(path, material_path, only_edges=False, width=60):
    got = json.load(io.open(path, encoding="utf-8"))
    material = load_material(material_path)
    nodes = got.get("节点") or []
    print("=" * 96)
    print("%s   （%d 节点 · %d 组 · %d 边）"
          % (os.path.basename(path), len(nodes), len(got.get("组") or []),
             len(got.get("边") or [])))

    def q(i):
        if not isinstance(i, int) or not (0 <= i < len(nodes)):
            return "（编号 %r 越界）" % (i,)
        return side(material, nodes[i].get("引文"))[1]

    if not only_edges:
        print("\n—— 节点 ——")
        for i, n in enumerate(nodes):
            q = n.get("引文") or ""
            tag, text = side(material, q, width)
            print("  [%2d] %s%s" % (i, tag, text))
            # ★ 定位不到就把"最像的原文"放在旁边：不摆在一起，
            #   就分不清是"编的"还是"只差几个字"。
            if tag.startswith("✗") and material is not None:
                for cand in nearest(material, q):
                    print("        最像的原文：%s" % cand[:width + 24])
        print("\n—— 组 ——")
        for k, g in enumerate(got.get("组") or []):
            ms = g.get("成员") or []
            mark = "单成员" if len(ms) == 1 else "%d 个" % len(ms)
            print("  组%d（%s）：%s" % (k, mark, " | ".join(q(m) for m in ms)))

    print("\n—— 边 ——")
    for k, e in enumerate(got.get("边") or []):
        ms = e.get("成员") or []
        print("  边%d［%s］" % (k, e.get("类型")))
        for m in ms:
            print("        %s" % q(m))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--材料", default=None, help="材料文件；不给就按同名规则找")
    ap.add_argument("--只边", action="store_true")
    ap.add_argument("--宽", type=int, default=60)
    args = ap.parse_args()

    paths = ([args.target] if os.path.isfile(args.target)
             else sorted(glob.glob(os.path.join(args.target, "*.标注.json"))))
    if not paths:
        sys.stdout.write("没找到标注文件：%s\n" % args.target)
        return 1
    for p in paths:
        mp = args.材料
        if mp is None:
            cand = p.replace(".标注.json", ".md")
            mp = cand if os.path.exists(cand) else None
        show(p, mp, args.只边, args.宽)
    return 0


if __name__ == "__main__":
    sys.exit(main())
