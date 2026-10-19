#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量 LongMemEval 的真值密度：它的历史里，到底有多少轮是"要用的"。

为什么需要它：
  标注这件事的第一道关是**取舍**——哪些位置值得进图。我们自己凑不出这条判据，
  只能拿别人的真值当尺子。LongMemEval 每一轮带 `has_answer`，是全数据集里
  唯一现成的**轮级**真值。

量出来的三个数各有各的用法（**不要混着看**）：
  轮级密度   含答案的轮数 ÷ 总轮数         —— "一次读取要从多稀疏的东西里挑"
  会话级密度 至少含一轮答案的会话 ÷ 总会话 —— 它的检索是按会话算的，看这一层更贴近它的用法
  每题条数   每道题平均有几轮含答案       —— 一轮都不含的题有没有（真值缺失）

用法：
    python tools/真值密度.py [--material docs/语料/LongMemEval/longmemeval_s_cleaned.json]
                             [--limit N] [--dump 输出.json]

★ 数据不进版本控制，这个脚本只读本地副本。没有副本时它会说清楚去哪取（见 docs/语料评估.md）。
"""
import argparse
import io
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # tools/对照/ → 仓库根
DEFAULT = os.path.join(ROOT, "docs", "语料", "LongMemEval", "longmemeval_s_cleaned.json")


def load(path):
    """整份读进来。264 MB 的 JSON 在 Python 里约占几倍内存，实测能过。

    ★ 读不动就明说，不要"跳过一部分继续算"——那样算出来的密度是假的。
    """
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def survey(rows, limit=None):
    rows = rows[:limit] if limit else rows
    st = {"题": 0, "轮": 0, "含答案轮": 0, "会话": 0, "含答案会话": 0,
          "无真值题": 0, "无真值且拒答": 0, "拒答但有真值": 0,
          "类型": {}, "拒答": 0, "每题含答案轮": [], "字数": []}
    for q in rows:
        st["题"] += 1
        abst = str(q.get("question_id", "")).endswith("_abs")
        if abst:
            st["拒答"] += 1
        t = q.get("question_type") or q.get("type") or "未标"
        st["类型"][t] = st["类型"].get(t, 0) + 1

        hit_here = 0
        seen = set()
        for si, sess in enumerate(q.get("haystack_sessions") or []):
            st["会话"] += 1
            has = False
            for turn in sess:
                st["轮"] += 1
                st["字数"].append(len(turn.get("content") or ""))
                if turn.get("has_answer") is True:
                    st["含答案轮"] += 1
                    hit_here += 1
                    has = True
            if has and si not in seen:
                seen.add(si)
                st["含答案会话"] += 1
        if hit_here == 0:
            st["无真值题"] += 1
            if abst:
                st["无真值且拒答"] += 1
        elif abst:
            st["拒答但有真值"] += 1
        st["每题含答案轮"].append(hit_here)
    return st


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--material", default=DEFAULT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dump", default=None)
    args = ap.parse_args()

    if not os.path.exists(args.material):
        sys.stdout.write(
            "没有本地副本：%s\n取法见 docs/语料评估.md 第 6 条"
            "（ModelScope 镜像 evalscope/longmemeval-cleaned，要带命名空间）。\n"
            % args.material)
        return 1

    rows = load(args.material)
    st = survey(rows, args.limit)

    sys.stdout.write("材料：%s（%d 题%s）\n"
                     % (os.path.basename(args.material), st["题"],
                        "，用了 --limit" if args.limit else ""))
    sys.stdout.write(
        "\n轮级：含答案 %d / 总轮 %d = %.3f%%（约 1/%.0f）\n"
        % (st["含答案轮"], st["轮"], pct(st["含答案轮"], st["轮"]),
           (st["轮"] / st["含答案轮"]) if st["含答案轮"] else 0))
    sys.stdout.write(
        "会话级：含答案 %d / 总会话 %d = %.3f%%（约 1/%.0f）\n"
        % (st["含答案会话"], st["会话"], pct(st["含答案会话"], st["会话"]),
           (st["会话"] / st["含答案会话"]) if st["含答案会话"] else 0))

    per = st["每题含答案轮"]
    if per:
        sys.stdout.write(
            "每题含答案轮数：平均 %.2f · 最少 %d · 最多 %d"
            " ⇒ ★ **%d 道题一轮都不含答案（%.1f%%）**\n"
            % (sum(per) / len(per), min(per), max(per),
               st["无真值题"], pct(st["无真值题"], len(per))))
    if st["字数"]:
        zs = sorted(st["字数"])
        sys.stdout.write("每轮字数：中位 %d · 平均 %.0f · 最长 %d\n"
                         % (zs[len(zs) // 2], sum(zs) / len(zs), zs[-1]))

    sys.stdout.write("\n类型分布：%s\n"
                     % " · ".join("%s %d" % kv for kv in
                                  sorted(st["类型"].items(), key=lambda kv: -kv[1])))
    sys.stdout.write("拒答题（question_id 以 _abs 结尾）：%d\n" % st["拒答"])
    sys.stdout.write(
        "  ⇒ 其中**真的没有真值**的 %d 道，**有真值**的 %d 道"
        "；另有 %d 道零真值题不是拒答题\n"
        "  ★ 这条要看清：\"拒答题一律没有真值位置\"这句话在本副本上**不成立**，\n"
        "    按它去跳过题目会连真值一起丢掉。\n"
        % (st["无真值且拒答"], st["拒答但有真值"],
           st["无真值题"] - st["无真值且拒答"]))

    if args.dump:
        io.open(args.dump, "w", encoding="utf-8", newline="\n").write(
            json.dumps({k: v for k, v in st.items() if k not in ("字数", "每题含答案轮")},
                       ensure_ascii=False, indent=2))
        sys.stdout.write("汇总写到 %s\n" % args.dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
