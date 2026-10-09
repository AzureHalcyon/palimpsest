#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑一遍标注：把材料交给标注提示词，核验产出。

用法：
    python tools/annotate.py <材料文件> [--model 模型名] [--tag 标签]

做的事：
  1. 从 `docs/标注提示词.md` 取当前草稿（```text 块里最长的那段）；
  2. 把材料填进占位符（★ 没有占位符就报错——否则模型收到空材料，
     会返回一个合法的空结果，看起来一切正常）；
  3. **流式**调用（非流式在长生成上会被中间层掐断，实测稳定复现）；
  4. 核验引文能不能在材料里唯一定位；把产出与原始回复都写到材料旁边。

产出（与材料同目录，用 --tag 区分版本）：
    <材料>. <tag>. 标注.json    归一化后的产出
    <材料>. <tag>. 回复.txt     原始回复
"""
import argparse
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import llm_client as llm                                     # noqa: E402

PROMPT_DOC = os.path.join(ROOT, "docs", "标注提示词.md")


def load_prompt(path=PROMPT_DOC):
    """取文档里当前那版草稿。

    ★ 围栏用通用写法：外层允许用四个反引号（````text … ````），
      这样提示词内部可以放三反引号的例子代码块而不会被提前截断。
      实测踩过：外层用三反引号 + 内部有 ```json 时，
      "匹配到第一个 ``` 就收尾"会把 `# 输出` 后面的东西整段丢掉——
      而丢掉之后**表面上一切正常**（照样能调用，只是提示词少了一截）。
    """
    text = io.open(path, encoding="utf-8").read()
    blocks = re.findall(r"^(`{3,})text[ \t]*\n(.*?)^\1[ \t]*$", text,
                        re.S | re.M)
    if not blocks:
        raise SystemExit("提示词文档里没找到 ```text 草稿块：%s" % path)
    # 取最长的那个（文档里可能留着旧版）
    body = max((b for _, b in blocks), key=len)
    return body


def load_material(path):
    text = io.open(path, encoding="utf-8").read()
    # 材料文件顶部的说明块（到第一个 --- 为止）不算材料
    if "\n---\n" in text:
        text = text.split("\n---\n", 1)[1].strip()
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("material")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--max-tokens", type=int, default=20000)
    ap.add_argument("--prompt", default=PROMPT_DOC)
    args = ap.parse_args()

    material = load_material(args.material)
    prompt = llm.fill_material(load_prompt(args.prompt), material)

    sys.stdout.write("  材料：%s（%d 字）\n" % (
        os.path.basename(args.material), len(material)))
    sys.stdout.write("  模型：%s   提示词：%d 字   max_tokens=%d   流式\n"
                     % (args.model, len(prompt), args.max_tokens))

    r = llm.chat_stream([{"role": "user", "content": prompt}], model=args.model,
                        max_tokens=args.max_tokens)
    base = os.path.splitext(args.material)[0] + "." + args.tag
    io.open(base + ".回复.txt", "w", encoding="utf-8", newline="\n").write(
        r["content"])

    # ★ 报数：正文与思考分开报。正文为空才是"空回复"。
    sys.stdout.write(
        "  耗时 %.1fs · finish=%s · 首字=%s · 稳态≈%.0f tok/s\n"
        "  ★ 正文 %d tok · 思考 %s · 空回复数：%d\n"
        % (r["elapsed"], r["finish"],
           ("%.1fs" % r["first_s"]) if r["first_s"] else "未出",
           r["rate"], r["content_tokens"], r["reasoning_tokens"],
           1 if not r["content"].strip() else 0))

    got = llm.parse_json(r["content"])
    if got is None:
        sys.stdout.write("  ⚠️ 回复里取不到 JSON，原文见 %s.回复.txt\n" % base)
        return 1
    got = llm.normalize_annotation(got)
    io.open(base + ".标注.json", "w", encoding="utf-8", newline="\n").write(
        json.dumps(got, ensure_ascii=False, indent=2))

    nodes, groups, edges = got["节点"], got["组"], got["边"]
    sys.stdout.write("  产出：节点 %d · 组 %d · 边 %d\n" % (
        len(nodes), len(groups), len(edges)))

    ok = bad = amb = 0
    for i, n in enumerate(nodes):
        q = (n.get("引文") or "").strip()
        c = material.count(q) if q else 0
        if c == 1:
            ok += 1
        elif c == 0:
            bad += 1
            sys.stdout.write("    ✗ [%2d] 材料里找不到：%r\n" % (i, q[:50]))
        else:
            amb += 1
            sys.stdout.write("    ? [%2d] 出现 %d 次：%r\n" % (i, c, q[:50]))
    sys.stdout.write("  引文定位：唯一 %d · 找不到 %d · 多义 %d\n" % (ok, bad, amb))

    kinds = [e.get("类型") for e in edges]
    sys.stdout.write("  边类型：%s\n" % kinds)
    sys.stdout.write("  ⇒ 去重后 %d 种（%d 条边）—— 复用得越多越好\n"
                     % (len(set(kinds)), len(kinds)))
    allm = set()
    for g in groups:
        allm.update(g.get("成员") or [])
    sys.stdout.write("  被划进组的节点：%d / %d\n" % (len(allm), len(nodes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
