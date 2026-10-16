#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑一遍标注：把材料交给标注提示词，核验产出。

用法：
    python tools/annotate.py <材料文件> [--model 模型名] [--tag 标签] [--via worker|http]

做的事：
  1. 从 `docs/标注提示词.md` 取当前草稿（```text 块里最长的那段）；
  2. 把材料填进占位符（★ 没有占位符就报错——否则模型收到空材料，
     会返回一个合法的空结果，看起来一切正常）；
  3. 取回复（默认经 dsh-llm-call 的进程接口；`--via http` 才直连端点）；
  4. 核验引文能不能在材料里唯一定位；把产出与原始回复都写到材料旁边。

产出（与材料同目录，用 --tag 区分版本）：
    <材料>. <tag>. 标注.json    归一化后的产出
    <材料>. <tag>. 回复.txt     原始回复
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time

# ★ 输出固定 UTF-8，并且**行缓冲**：控制台默认 GBK 时，正文里的 `⇒` 这类字符会在最后一步抛
#   UnicodeEncodeError——**文件都已经写完了**，却报 exit 1，看起来像整遍失败。
#   行缓冲是为了在终端里跑批量时能看见进度（管道下默认是块缓冲，跑的时候什么都看不到）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)                          # tools/标注/ → 仓库根
# ★ 工具分了目录，跨目录 import 要靠这个：把需要的目录都挂上路径。
for _d in (HERE, os.path.join(TOOLS, "取数"), os.path.join(TOOLS, "材料"),
           os.path.join(TOOLS, "标注"), os.path.join(TOOLS, "对照")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import llm_client as llm                                     # noqa: E402

PROMPT_DOC = os.path.join(ROOT, "docs", "标注提示词.md")

# 标完之后接着问的那几句。★ **必须在同一个上下文里问**（把它刚交的产出接在中间）：
#   另起一次问答时，模型没做过这份活，只能泛泛猜"什么会难"；
#   而顺着上下文问，它能指到具体那一处（"这里我在两种做法之间犹豫"）。
#   实测：另起问答时多数模型思考量为 0，给的是通用建议。
FOLLOWUP = """你刚才交出了上面这份产出。**不要改它**，只如实回答四件事——
照着你刚才真实的想法说，不要给漂亮话，也不要补充"更好的做法"。

1. **摘节点**时，哪一处你**没有把握**？把那一处的引文原样写出来，
   并说你在哪两种做法之间犹豫。
2. **划组**时，哪一组你**没有把握**？写出它的成员编号，并说为什么。
3. 有没有**为了凑答案而写**出来的东西（一个组、一句说明、一个节点）？
   有就指出来，并说当时是什么让你觉得"得写点什么"。
4. 提示词里**哪一句**让你为难（不知道该怎么做，或者跟另一句打架）？
   **照抄那一句原文**，再说为难在哪。

如果某一条你确实没有可说的，就写"没有"。"""

# 追问的第二副面孔：**专门评提示词**。
# 上面那份问的是"你哪里没把握"（从产出反推提示词）；这份直接问
# "哪一句没用 / 哪一句起反作用"——用户要的是这个角度：
# **不是删到多少字算好，是把没用的和起反作用的去掉、把有用的留下。**
FOLLOWUP_评 = """你刚才按提示词交了产出。现在**不评产出，只评提示词本身**。
照实说，不要客气，也不要泛泛而谈。

1. **哪一句起了反作用？**（照抄那一句）——它让你做了本来不该做的事，
   或者让你在两种做法之间白费力气。说清它具体怎么误导了你。
2. **哪一句是没用的？**（照抄那一句）——去掉它你也不会做得不同。
3. **哪一处该说而没说？**——你因为在提示词里找不到依据，只好自己猜的那个地方。
4. 如果要你**删掉一句**、**改写一句**，各选哪一句，改成什么？

每条都要**照抄提示词里的原句**。说不出就写"没有"。"""

# 第三副面孔：**逐个组做内容不变性自查**（内聚度的可数版本）。
# 依据：判据里那句"把两个成员说的话对调，这个说法还成立吗"——
#   光靠读产出看不出来，**得让交出它的模型自己逐个回答**。
#   靠内容的组 = 真类；只靠动作/语气的组 = 偷懒的筐。
FOLLOWUP_内聚 = """你刚才交出的每一个组，**逐个自查**：

测法：**把这一组里两个成员说的话对调，你那句 `组说明` 还成立吗？**

对每个组输出一行，格式照抄：

组0 · 成员[0,2] · 说明"……" ⇒ 【靠内容】或【只靠动作或语气】 + 一句话理由
组1 · ……

判定标准：
  · **靠内容**：两个成员的内容一换，这个说法就不对了 ⇒ 它真的在说这几处的**内容**。
  · **只靠动作或语气**：换个内容照样成立（"在给建议""在安慰""在重述""都有情绪"）
    ⇒ 那只是按"在干什么 / 什么语气"分的，**等于没筛**。

最后再写一行统计：靠内容的几个、只靠动作或语气的几个。
**照实说**，不要替自己圆。"""

# 第四副面孔：**标完之后就着这次对话接着问出题**（⛔ 不另起一次调用）。
#
# ★★ 为什么必须"接着问"（三条，任一都够）：
#   1. `训练.md` §7 说这一路出题测的是"**记录与当时意图的偏离**"——
#      "当时"只在**同一次对话**里成立；另起一次调用时，模型已经不在那个上下文里了；
#   2. §10.7 要求 `留档窗口` 是"**归档那次对话窗口**的句柄"——另起一次就没有窗口可留；
#   3. ★ **接着问吃得到前缀缓存**（前面那段原文和提示词原样不动），
#      另起一次等于把缓存和上下文一起扔掉；实测那次还因为"把整本当题干"直接 HTTP 400。
#
# ★ 题数**不设限**：模型觉得几道够就几道。
出题追问 = """刚才那份产出，就是你标出来的记忆。现在**接着这份对话**回答一件事，
⛔ 不要改上面的产出，也不要重新标一遍。

照着**你刚才真实想抓住的东西**出题——**几道都行，你觉得几道够就几道**。
每题给一句 `问`，再给一段 `答案`：答案必须是**能在上面那段原文里一字不差找到的连续片段**。

⛔ 答案不许改写、不许概括、不许拼接不相邻的句子。
⛔ 不要出"这句话在原文哪里"这种题；要出**你自己当时确实在意的那一点**。

只输出 JSON：
{"题":[{"问":"…","答案":"原文片段"}]}"""


def 接着问出题(msgs, raw, model, via=None, thinking="默认", max_tokens=16000, retries=2):
    """在**同一次对话里**接着问出题。返回 `(题, 续上的消息表, 原始回复, 错误)`。

    `msgs` 用**这次标注已经用过的消息表**（里面就是那段原文和提示词）；
    `raw` 是这次标注交出来的正文。⇒ 这一次调用**前缀完全一样**，缓存吃得到。

    ⛔ 出错**不许静默**：返回的 `错误` 由调用方记进 `②标注记录`，并进校验 9。
    """
    续 = [dict(m) for m in (msgs or [])]
    if not (续 and 续[-1].get("role") == "assistant"
            and (续[-1].get("content") or "") == raw):
        续.append({"role": "assistant", "content": raw})
    续.append({"role": "user", "content": 出题追问})
    extra = None
    if thinking in ("开", "enabled", "on"):
        extra = {"thinking": {"type": "enabled"}}
    elif thinking in ("关", "disabled", "off"):
        extra = {"thinking": {"type": "disabled"}}

    错误, r = None, None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep([5, 20, 60][min(attempt - 1, 2)])
        try:
            r = llm.call_model(续, model=model, max_tokens=max_tokens, via=via,
                               extra=extra)
        except RuntimeError as e:                            # noqa: PERF203
            错误 = str(e)
            if attempt < retries and looks_transient(错误):
                continue
            break
        got = llm.parse_json(r.get("content") or "")
        题 = got.get("题") if isinstance(got, dict) else got
        if isinstance(题, list):
            续.append({"role": "assistant", "content": r.get("content") or ""})
            return 题, 续, r, None
        错误 = ("出题那一轮取不到 JSON（finish=%s，正文 %d 字）"
                % ((r or {}).get("finish"), len((r or {}).get("content") or "")))
    续.append({"role": "assistant", "content": ((r or {}).get("content") or "")})
    return [], 续, r, 错误



def load_blocks(path=PROMPT_DOC):
    """取文档里所有**带名字**的 ```text 块，返回 {块名: 正文}。

    ★ 围栏用通用写法：外层允许四个反引号（````text 名字 … ````），
      这样提示词内部可以放三反引号的例子代码块而不会被提前截断。
      实测踩过：外层用三反引号 + 内部有 ```json 时，
      "匹配到第一个 ``` 就收尾"会把 `# 输出` 后面的东西整段丢掉——
      而丢掉之后**表面上一切正常**（照样能调用，只是提示词少了一截）。

    ★ 块**必须带名字**（```text 公共 / ```text 组-同一件事）。
      早先的写法是"取最长的那块"，那是**静默选错**的温床：文档里留一版旧草稿，
      哪天新的写短了，跑的就是旧的那版，而且没有任何提示。
    """
    text = io.open(path, encoding="utf-8").read()
    blocks = {}
    for _, name, body in re.findall(
            r"^(`{3,})text[ \t]+(\S+)[ \t]*\n(.*?)^\1[ \t]*$", text, re.S | re.M):
        if name in blocks:
            raise SystemExit("提示词文档里有两个同名块 `%s`：%s" % (name, path))
        blocks[name] = body
    if not blocks:
        raise SystemExit("提示词文档里没找到带名字的 ```text 块：%s" % path)
    return blocks


def views(path=PROMPT_DOC):
    """文档里有哪些视角（`组-<视角>` 那些块）。"""
    return [k[len("组-"):] for k in load_blocks(path) if k.startswith("组-")]


def load_prompt(path=PROMPT_DOC, view="同一件事"):
    """拼出某个视角的提示词：公共块 + 该视角的组判据 + 该视角的例子。

    ★ 缺块、缺占位符都**报错**，不许静默少一段——本项目栽过三次的坑
      （占位符丢了 / 键名不认识 / 围栏截断）。
    """
    blocks = load_blocks(path)
    need = ["公共", "组-" + view, "例子-" + view]
    missing = [n for n in need if n not in blocks]
    if missing:
        raise SystemExit("提示词缺块 %s（文档里有：%s）"
                         % (missing, sorted(blocks)))
    text = blocks["公共"]
    for ph, name in (("<组判据>", "组-" + view), ("<例子>", "例子-" + view)):
        if ph not in text:
            raise SystemExit("公共块里没有占位符 %s —— %s 根本拼不进去" % (ph, name))
        text = text.replace(ph, blocks[name].strip())
    # 模板里不该再留下没换掉的占位符：留着就是"少了一段而看起来正常"。
    left = re.findall(r"<[^>\n]{1,12}>", text)
    left = [x for x in left
                if x != "<材料原文>"]  # 占位符只认 <材料原文>；<主人> 已作废（v16）
    if left:
        raise SystemExit("拼完之后还剩没换掉的占位符 %s" % left)
    return text


def fingerprint(template):
    """提示词模板的指纹。★ 要的是**模板**的指纹，不是"模板+材料"的长度。

    实测踩过：记录里那个叫"提示词字数"的字段其实记的是**填完材料之后**的长度，
    随材料变——于是它当不了版本指纹，而**一批里混了两版提示词也看不出来**。
    """
    return hashlib.sha1(template.encode("utf-8")).hexdigest()[:10]


def 图标识(材料标识, out):
    """一张图的标识。`设计 3.5` 要求"图带版本标识，用于指出某次激活发生在哪张图上"。

    ★ 它是**多视角能并存的前提**：三个视角是对同一份原文的三张**不同的图**（`3.5`），
      而"不得跨视角投票"（`3.5.1`）要求随时分得清"这份产出属于哪张图"。
      ⚠️ 早先只有散落的 json 文件、靠**人起的文件名 tag** 暗示视角
      ⇒ 大批量一跑就理不清。**这个字段就是给多视角留的位置。**
    """
    return "%s · %s · 模板%s · 思考%s" % (
        材料标识, out.get("视角") or "?", out.get("指纹") or "?",
        out.get("思考开关") or "默认")


# 看起来是瞬时的失败：重试有意义。判据**窄**，宁可少重试也不要重试一个真错。
TRANSIENT = ("timed out", "timeout", "connection", "terminated", "aborted",
             "temporarily", "503", "502", "429", "overloaded")


def looks_transient(msg):
    low = (msg or "").lower()
    return any(w in low for w in TRANSIENT)




def body(text):
    """从材料文件里取出正文：去掉顶部那段出处说明（到第一个 `---` 为止）。

    ★ 必须去掉，而且**不能喂给模型**：那段是我们自己写的旁注
      （出处、许可、"去掉了什么"），有时还带着材料的标题——
      实测 KdConv 的标题里直接写着对话的主题人名。**旁注不是原文。**
    """
    if "\n---\n" in text:
        return text.split("\n---\n", 1)[1].strip()
    return text.strip()


def load_material(path):
    return body(io.open(path, encoding="utf-8").read())



def run(material, model="deepseek-v4.1-flash", max_tokens=200000,
        prompt_path=PROMPT_DOC, via=None, view="同一件事", retries=3, extra=None,
        thinking=None, 出题=False):
    """把一段材料按**一个视角**标一遍。返回 dict，不打印、不落盘——批量跑要用它。

    ★ 三条静默失败都挡在返回之前：
      占位符丢了（`fill_material` 报错）· 键名不认识（`normalize_annotation` 报错）
      · 回复里没有 JSON（`error` 非空）。**不要把这些降级成"0 个节点"**。

    ★ **瞬时故障要重试**：实测遇到过一个几分钟的窗口，那个窗口里进程接口这条路
      每一次调用都秒失败（`PROVIDER_ERROR: Request timed out`），而直连 HTTP 那条没事。
      进程接口原先**没有重试**（HTTP 那条有），于是一个瞬时窗口吃掉了 5 次运行。
      ⇒ 只对"看起来是瞬时"的失败重试（超时 / 连接错 / 被中断）；
        请求本身有问题的（模型名不对之类）不重试，直接报出来。
    """
    template = load_prompt(prompt_path, view)
    prompt = llm.fill_material(template, material)
    # ★ 思考开关要**显式**带上：实测两条路的默认不一样
    #   （daapsaak 直连默认不思考、官方默认思考、进程接口会思考），
    #   不显式设，同一格子的重复跑就不是同一个条件。
    if thinking in ("开", "enabled", "on"):
        extra = dict(extra or {}, thinking={"type": "enabled"})
    elif thinking in ("关", "disabled", "off"):
        extra = dict(extra or {}, thinking={"type": "disabled"})
    out = {"视角": view, "模板字数": len(template), "指纹": fingerprint(template),
           "思考开关": thinking or "默认", "请求额外字段": extra or None,
           "prompt": prompt, "prompt_chars": len(prompt), "got": None,
           "raw": "", "reply": None, "error": None, "重试次数": 0,
           "题": [], "对话": None, "出题错误": None}
    msgs = [{"role": "user", "content": prompt}]
    for attempt in range(retries + 1):
        if attempt:
            out["重试次数"] = attempt
            # 退避拉长：实测遇到过一个几分钟的窗口，窗口内**连着几次**都失败，
            # 5/10 秒这种短退避不够，会整批撞在同一个窗口里。
            time.sleep([5, 20, 60, 120][min(attempt - 1, 3)])
        try:
            r = llm.call_model(msgs, model=model, max_tokens=max_tokens, via=via,
                               extra=extra)
        except RuntimeError as e:
            out["error"] = str(e)
            out["本轮失败"] = "传输层"
            if attempt < retries and looks_transient(str(e)):
                out["raw"] = ""
                continue
            return out
        out["reply"] = r
        out["raw"] = r["content"]
        # ★ 回复里取不到 JSON **也要重试**：实测那多半是**一次性的**——
        #   同一条材料重跑就好了（截断、少一个括号、半个字符串）。
        #   原先只重试传输层错误，于是这种"模型这一遍没写全"直接判成整条失败。
        got = llm.parse_json(out["raw"])
        if got is None:
            out["error"] = "回复里取不到 JSON"
            out["本轮失败"] = "取不到 JSON（finish=%s，正文 %d 字）" % (
                (r or {}).get("finish"), len(out["raw"]))
            if attempt < retries:
                out["raw"] = ""
                continue
            out["raw"] = (r or {}).get("content") or ""
            return out
        try:
            out["got"] = llm.normalize_annotation(got)
        except ValueError as e:
            out["error"] = str(e)
        # ★★ 标完就着**这次对话**接着问出题（⛔ 不另起一次调用）：
        #   ①"当时想抓住什么"只在同一次对话里成立；②留档窗口＝这次对话；
        #   ③前缀一样，缓存吃得到。见 `接着问出题` 上面那段。
        if 出题 and out.get("got") is not None:
            题, 对话, _r2, e2 = 接着问出题(
                msgs, out["raw"], model, via=via, thinking=thinking or "默认")
            out["题"], out["对话"], out["出题错误"] = 题, 对话, e2
        return out
    return out


def anchor(material, quote):
    """核一条引文能不能锚在材料上。返回 (状态, 说明)。

    状态四种：`唯一` · `唯一（只差空白）` · `找不到` · `多义`。

    ★★ 为什么要单独分出"只差空白"这一种：
      实测踩过——DuLeMon 的材料带着语料自带的分词空格（`还可以 ， 长肉是不可能的哦`），
      而模型写的是正常中文（`还可以，长肉是不可能的哦`）。于是**写对了的模型反而锚不住**，
      12 个节点被判成"丢节点"，差一点被记成"这个模型不合规"。
      ⇒ **核验器自己错了，比没有核验更坏**：它给出的是一个像结论的东西。
      ⇒ 所以核验要能分清"编了一句原文里没有的话"与"只差空白/标点"——
        前者的处置是丢，后者要去看材料是不是没洗干净。
    """
    q = (quote or "").strip()
    if not q:
        return "找不到", "空引文"
    c = material.count(q)
    if c == 1:
        return "唯一", ""
    if c > 1:
        return "多义", "材料里出现 %d 次" % c
    flat_m = re.sub(r"\s+", "", material)
    flat_q = re.sub(r"\s+", "", q)
    if flat_q:
        c2 = flat_m.count(flat_q)
        if c2 == 1:
            return "唯一（只差空白）", ""
        if c2 > 1:
            return "多义", "去掉空白后出现 %d 次" % c2
    return "找不到", ""


def locate(got, material):
    """核验每条引文。返回 (计数, 明细)，计数按上面那四种状态分。"""
    counts = {"唯一": 0, "唯一（只差空白）": 0, "找不到": 0, "多义": 0}
    detail = []
    for i, n in enumerate(got["节点"]):
        q = (n.get("引文") or "").strip()
        st, why = anchor(material, q)
        counts[st] += 1
        if st in ("找不到", "多义"):
            detail.append((st, i, ("%s " % why) + q[:50]))
    return counts, detail


def group_stats(got):
    """组的规模分布。**单成员组与多成员组分开报**。

    ★ 这不是在判对错：材料本身就可能是"一处说全一件事"，那时单成员组是忠实的。
      分开报是为了让"组数"这个数字不至于被单成员组撑大——那是代理指标，
      不是要看的东西。
    """
    sizes = [len(g.get("成员") or []) for g in got["组"]]
    return {"组数": len(sizes), "单成员": sizes.count(1),
            "多成员": sum(1 for s in sizes if s >= 2),
            "最大": max(sizes) if sizes else 0,
            "规模分布": sizes}


# 形状核验与处置。**机械的归机械，而且机械这一层最容易出大问题，所以分三层写。**
#
#   ① 硬缺陷 —— 形状上不可能成立（成员不成位置、边少于两个成员、边没类型…）。
#      每一条都有**确定的处置**，处置要记进标注记录，**不许静默丢**。
#   ② 疑点   —— 只是"值得看一眼"的信号（宽边、枢纽）。
#      ★ **不是指标，不要拿它优化**：一个中心事件本来就该连很多处。
#      把它做成一个分数，就会有人去把这个分数压下来，而目标根本不是它。
#   ③ 计数   —— 原始计数（成员数分布等）。给人读，不给程序判。
#
# ★★ 一个必须按顺序做的地方：**丢掉一个节点，会让后面所有下标错位。**
#    所以处置的铁序是：
#      ① 核引文 → 定下哪些节点进图
#      ② 建"旧下标 → 新下标"的映射
#      ③ 用映射重算组与边的成员
#      ④ 再判空、判少于两个成员、判重复
#      ⑤ 出图，同时出处置清单
#    顺序反了，图会**静静地连错**（原本连 B 的连到了 C），而且看起来完全正常。
#    实测踩过第四类静默失败：成员编号越界，汇总里"定位失败 0"把它盖住了。


def settle(got, material):
    """把一份标注处置成"可以进图的图"。返回 (图, 处置清单, 统计, 记录层)。

    ★ 处置的语义照设计走：**锚不住的节点不进入图**（`3.1` 准入约束）。
      这里的"处置"只做这一件事，不做语义判断、不修补、不猜测。

    ★ 第四个返回值是**记录层**：`组说明`（模型凭什么把这几处划在一起）。
      它**不进图**（`3.3` 含义只作标注），但要对齐到进图之后的组上，
      所以必须在这里一起处理——否则组一被丢，"哪句说明对应哪个组"就错位了。
    """
    nodes_in = got.get("节点") or []
    names_in = got.get("组说明") or []
    act = []
    if names_in and len(names_in) != len(got.get("组") or []):
        act.append({"对象": "组说明", "原因": "`组说明` 与 `组` 个数不一致（%d 对 %d）"
                    % (len(names_in), len(got.get("组") or [])),
                    "处置": "只保留能对上的那几个，其余当没有"})

    # ① 核引文
    keep = []                                  # [(旧下标, 节点)]
    for i, n in enumerate(nodes_in):
        q = (n.get("引文") or "").strip()
        st, why = anchor(material, q)
        if st in ("唯一", "唯一（只差空白）"):
            keep.append((i, n))
            if st != "唯一":
                act.append({"对象": "节点", "旧下标": i,
                            "原因": "引文与原文**只差空白**，去掉空白后唯一定位",
                            "内容": q[:80], "处置": "收下（并去查材料是不是没洗掉分词空格）"})
        else:
            act.append({"对象": "节点", "旧下标": i,
                        "原因": ("引文在材料里找不到" if st == "找不到"
                                else "引文%s，定位不唯一" % (why or "出现多次")),
                        "内容": q[:80], "处置": "该节点不进图"})

    # ② 旧 → 新
    old2new = {o: k for k, (o, _) in enumerate(keep)}

    def remap(ms, where, k):
        out, seen = [], set()
        for m in ms:
            if not isinstance(m, int) or m not in old2new:
                act.append({"对象": where, "旧下标": k, "成员": m,
                            "原因": "成员编号不成位置（越界、非整数，或指向已被丢掉的节点）",
                            "处置": "该成员不进图"})
                continue
            if old2new[m] in seen:
                act.append({"对象": where, "旧下标": k, "成员": m,
                            "原因": "同一成员重复", "处置": "去重"})
                continue
            seen.add(old2new[m])
            out.append(old2new[m])
        return out

    # ③④ 组
    groups_out, notes = [], []
    for k, g in enumerate(got.get("组") or []):
        ms = remap(g.get("成员") or [], "组", k)
        if not ms:
            act.append({"对象": "组", "旧下标": k, "原因": "重算后没有成员",
                        "处置": "该组不进图"})
            continue
        groups_out.append({"成员": ms})
        notes.append({"组": len(groups_out) - 1,
                      "说明": (names_in[k] if k < len(names_in) else "")})

    # ③④ 边
    edges_out = []
    for k, e in enumerate(got.get("边") or []):
        typ = (e.get("类型") or "").strip()
        ms = remap(e.get("成员") or [], "边", k)
        if not typ:
            act.append({"对象": "边", "旧下标": k, "原因": "边没有类型",
                        "处置": "该边不进图"})
            continue
        if len(ms) < 2:
            act.append({"对象": "边", "旧下标": k,
                        "原因": "两端以上才算关系，重算后只剩 %d 个成员" % len(ms),
                        "处置": "该边不进图"})
            continue
        edges_out.append({"成员": ms, "类型": typ})

    graph = {"节点": [n for _, n in keep], "组": groups_out, "边": edges_out}

    # ⑤ 计数（不是分数）
    sizes = [len(g["成员"]) for g in groups_out]
    ends = {}
    for e in edges_out:
        for m in e["成员"]:
            ends[m] = ends.get(m, 0) + 1
    stat = {
        "节点收/丢": [len(keep), len(nodes_in) - len(keep)],
        "组收/丢": [len(groups_out), len(got.get("组") or []) - len(groups_out)],
        "边收/丢": [len(edges_out), len(got.get("边") or []) - len(edges_out)],
        "组规模": sizes, "单成员组": sizes.count(1),
        "边成员数": [len(e["成员"]) for e in edges_out],
        "最大枢纽": max(ends.values()) if ends else 0,
        "类型数": len(set(e["类型"] for e in edges_out)),
    }
    return graph, act, stat, notes


def soft_signs(stat, material_chars):
    """疑点。★ 这些**不是指标**，只用来决定"要不要去看一眼"。"""
    out = []
    if stat["最大枢纽"] >= 3 and stat["最大枢纽"] == len(stat["边成员数"]):
        out.append("所有边共用同一个端点（%d 条）——去看一眼是不是把"
                   "\"同一个话题\"写成了关系" % stat["最大枢纽"])
    big = [x for x in stat["边成员数"] if x >= 4]
    if big:
        out.append("%d 条边挂了 %d 个以上成员（最宽 %d）——去看一眼"
                   "是不是把组贴了个类型名当成边（设计 3.6）"
                   % (len(big), 4, max(big)))
    if material_chars and stat["节点收/丢"][0]:
        pass
    return out


def self_test(verbose=True):
    """核验器的自测。**核验器自己也要被核验**，否则它静默放过的东西没人知道。

    用编好的形状当输入，断言它挑出的正是该挑的——特别是那处下标错位。
    """
    mat = "甲：一。\n乙：二。\n甲：三。\n"
    got = {
        "节点": [{"引文": "一。"}, {"引文": "材料里没有这句"}, {"引文": "三。"}],
        "组": [{"成员": [0, 1]},                       # 含被丢的节点 ⇒ 变单成员
               {"成员": [9]}],                          # 越界 ⇒ 空 ⇒ 丢
        "边": [{"成员": [1, 2], "类型": "起因"},        # 含被丢的 ⇒ 只剩一个 ⇒ 丢
               {"成员": [0, 2], "类型": "起因"},        # 好边
               {"成员": [0, 2], "类型": "  "},          # 没类型 ⇒ 丢
               {"成员": [0, 0], "类型": "重复"}],       # 去重后一个 ⇒ 丢
        "组说明": ["第一条说明", "第二条说明"],           # 记录层；第二个组会被丢掉
    }
    graph, act, stat, notes = settle(got, mat)
    cases = [
        ("丢掉锚不住的节点", stat["节点收/丢"] == [2, 1]),
        ("丢掉之后重算下标（三。→ 新下标 1）",
         [n["引文"] for n in graph["节点"]] == ["一。", "三。"]),
        ("组里被丢的成员也去掉，剩一个就留单成员组（不替模型判对错）",
         [g["成员"] for g in graph["组"]] == [[0]]),
        ("边少了成员就整条不进图", [e["成员"] for e in graph["边"]] == [[0, 1]]),
        ("越界成员被点名", any("不成位置" in a["原因"] for a in act)),
        ("处置清单不静默（有节点、组、边三类）",
         {"节点", "组", "边"} <= {a["对象"] for a in act}),
        ("组说明进记录层，且跟着组一起挪（丢掉的组不留说明）",
         [n["说明"] for n in notes] == ["第一条说明"]),
        ("组说明不进图", "组说明" not in graph),
    ]
    ok = True
    for name, passed in cases:
        ok = ok and passed
        if verbose:
            sys.stdout.write("   %s %s\n" % ("✓" if passed else "✗", name))
    return ok


def check_shape(got):
    """只看形状，不看语义。返回 (缺陷清单, 指标)。

    ★ 这是**只读**的粗查（`settle` 才是带处置的那条路）。批量跑里用它把
      "这份产出的形状有没有问题"一眼看出来，不改动产出本身。
    """
    n = len(got["节点"])
    bad = []

    for k, g in enumerate(got["组"]):
        ms = g.get("成员") or []
        if not ms:
            bad.append("组%d 没有成员" % k)
        for m in ms:
            if not isinstance(m, int) or not (0 <= m < n):
                bad.append("组%d 成员编号不成位置：%r" % (k, m))
        if len(set(ms)) != len(ms):
            bad.append("组%d 成员重复：%r" % (k, ms))

    for k, e in enumerate(got["边"]):
        ms = e.get("成员") or []
        if len(ms) < 2:
            bad.append("边%d 成员少于两个：%r" % (k, ms))
        if len(set(ms)) != len(ms):
            bad.append("边%d 成员重复：%r" % (k, ms))
        for m in ms:
            if not isinstance(m, int) or not (0 <= m < n):
                bad.append("边%d 成员编号不成位置：%r" % (k, m))
        if not (e.get("类型") or "").strip():
            bad.append("边%d 没有类型" % k)

    wide = [len(e.get("成员") or []) for e in got["边"]
            if len(e.get("成员") or []) >= 4]
    ends = {}
    for e in got["边"]:
        for m in e.get("成员") or []:
            if isinstance(m, int):
                ends[m] = ends.get(m, 0) + 1
    hub = max(ends.values()) if ends else 0
    return bad, {"最大枢纽": hub, "边数": len(got["边"]),
                 "宽边": len(wide), "最宽边": max(wide) if wide else 0,
                 "枢纽占比": (hub / len(got["边"])) if got["边"] else None}



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("material", nargs="?", default=None)
    ap.add_argument("--自测", action="store_true", help="只跑核验器的自测，不调用模型")
    ap.add_argument("--model", default="deepseek-v4.1-flash")
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--max-tokens", type=int, default=200000)
    ap.add_argument("--prompt", default=PROMPT_DOC)
    ap.add_argument("--视角", default="同一件事",
                    help="组的判据用哪一个视角（见 --视角列表）")
    ap.add_argument("--视角列表", action="store_true", help="列出可用的视角后退出")
    ap.add_argument("--重试", type=int, default=3,
                    help="瞬时失败重试几次（这条路由实测时好时坏，见 .local A6）")
    ap.add_argument("--思考", default="默认", choices=["默认", "开", "关"],
                    help="显式设思考开关。★ 两条路的默认不一样（daapsaak 默认不思考、"
                         "官方默认思考），不设的话同一格子重复跑不是同一个条件")
    ap.add_argument("--追问", action="store_true",
                    help="标完之后，**在同一个上下文里**接着问它哪里没把握、哪里是凑的")
    ap.add_argument("--评", action="store_true",
                    help="标完之后接着问它**评提示词**：哪一句起反作用、哪一句没用、哪处该说没说")
    ap.add_argument("--内聚", action="store_true",
                    help="标完之后让它**逐个组做内容不变性自查**（靠内容 / 只靠动作或语气）")
    ap.add_argument("--via", default=None,
                    help="worker（进程接口）· http（直连 daapsaak）· official（直连 DeepSeek 官方）")
    args = ap.parse_args()

    if args.视角列表:
        for v in views(args.prompt):
            sys.stdout.write("  %s（模板 %d 字 · 指纹 %s）\n"
                             % (v, len(load_prompt(args.prompt, v)),
                                fingerprint(load_prompt(args.prompt, v))))
        return 0

    if args.自测:
        sys.stdout.write("核验器自测：\n")
        ok = self_test()
        sys.stdout.write("⇒ %s\n" % ("全部通过" if ok else "⛔ 有没通过的，别用这个核验器"))
        return 0 if ok else 1

    if not args.material:
        ap.error("要给材料文件（或 --自测）")

    material = load_material(args.material)
    sys.stdout.write("  材料：%s（%d 字）\n" % (
        os.path.basename(args.material), len(material)))

    out = run(material, model=args.model, max_tokens=args.max_tokens,
              prompt_path=args.prompt, via=args.via, view=args.视角,
              retries=args.重试, thinking=args.思考)
    base = os.path.splitext(args.material)[0] + "." + args.tag
    if out["raw"]:
        io.open(base + ".回复.txt", "w", encoding="utf-8", newline="\n").write(
            out["raw"])
    # ★ 思考也要落盘。它是**排查提示词的主要材料**：
    #   同一任务思考量从 0 到两万多字，光看结果分不清"它没想"与"它想岔了"。
    #   只存正文不存思考，等于把最有用的一份证据扔了。
    if (out.get("reply") or {}).get("reasoning"):
        io.open(base + ".思考.txt", "w", encoding="utf-8", newline="\n").write(
            out["reply"]["reasoning"])
    if out["error"]:
        sys.stdout.write("  ⛔ %s\n" % out["error"])
        if out["raw"]:
            sys.stdout.write("     原始回复见 %s.回复.txt\n" % base)
        return 2

    # ★ 三个量分开报，**名字各自说清**（这里曾经把"正文字符数"贴成"计费输出（含思考）"）：
    #   正文 X 字 · 思考 Y 字 · 计费输出 = 正文 tok + 思考 tok（服务端的 completion_tokens 含思考）
    r = out["reply"]
    sys.stdout.write("  模型：%s   视角：%s   取数：%s   模板 %d 字（指纹 %s）"
                     "   max_tokens=%d\n"
                     % (args.model, out["视角"], r["via"], out["模板字数"],
                        out["指纹"], args.max_tokens))
    sys.stdout.write(
        "  耗时 %.1fs · finish=%s · 首字=%s · 速率=%s\n"
        "  ★ 正文 %d 字（%s tok）· 思考 %d 字（%s tok）· 计费输出 %s tok · 输入 %s"
        " · 空回复数：%d%s\n"
        % (r["elapsed"], r["finish"],
           ("%.1fs" % r["first_s"]) if r["first_s"] else "量不到（进程接口不给增量）",
           ("%.0f tok/s（%s）" % (r["rate"], r.get("rate_kind"))) if r["rate"] else "量不到",
           len(r["content"]), r.get("content_tokens"),
           len(r["reasoning"] or ""), r.get("reasoning_tokens"),
           r.get("billed_tokens") if r.get("billed_tokens") is not None else "—",
           r.get("prompt_tokens") if r.get("prompt_tokens") is not None else "—",
           1 if not r["content"].strip() else 0,
           "（★ 服务端没给计费用量，正文那列是按字数估的）" if r.get("estimated") else ""))
    # ★ 截断要显式报出来：finish=length 时正文可能被砍掉一半，
    #   而它**看起来像正常结束**（`.local` 里那条 M1 事故就是这么来的）。
    if r["finish"] == "length":
        sys.stdout.write(
            "  ⛔ finish=length：输出被截断了（max_tokens=%d 不够）。\n"
            "     这一遍的结果不可用——加大 --max-tokens 再跑。\n" % args.max_tokens)

    got = out["got"]
    io.open(base + ".标注.json", "w", encoding="utf-8", newline="\n").write(
        json.dumps(got, ensure_ascii=False, indent=2))

    nodes, edges = got["节点"], got["边"]
    gs = group_stats(got)
    sys.stdout.write("  产出：节点 %d · 组 %d（单成员 %d / 多成员 %d）· 边 %d\n"
                     % (len(nodes), gs["组数"], gs["单成员"], gs["多成员"],
                        len(edges)))

    graph, act, stat, notes = settle(got, material)
    sys.stdout.write("  进图：节点 %d/%d · 组 %d/%d · 边 %d/%d"
                     "（单成员组 %d）\n"
                     % (stat["节点收/丢"][0], len(nodes),
                        stat["组收/丢"][0], len(got["组"]),
                        stat["边收/丢"][0], len(edges), stat["单成员组"]))
    if act:
        sys.stdout.write("  ★ 处置 %d 条（都记在产出里，没有静默丢）：\n" % len(act))
        for a in act[:8]:
            sys.stdout.write("      %s[%s] %s ⇒ %s\n"
                             % (a["对象"], a.get("旧下标"), a["原因"], a["处置"]))
    for s in soft_signs(stat, len(material)):
        sys.stdout.write("  ⚠️ 疑点（不是指标，只是值得看一眼）：%s\n" % s)
    io.open(base + ".图.json", "w", encoding="utf-8", newline="\n").write(
        json.dumps({"图标识": 图标识(os.path.basename(args.material), out),
                    "材料标识": os.path.basename(args.material),
                    "视角": out["视角"], "模板指纹": out["指纹"],
                    "思考设置": out["思考开关"], "模型": args.model,
                    "取数": r["via"], "重试次数": out["重试次数"],
                    "图": graph, "处置": act, "统计": stat,
                    "组说明（记录层，不进图）": notes},
                   ensure_ascii=False, indent=2))
    for n in notes:
        if n["说明"]:
            sys.stdout.write("  组%d 凭的是：%s\n" % (n["组"], n["说明"][:70]))

    counts, detail = locate(got, material)
    for kind, i, what in detail:
        sys.stdout.write("    %s [%2d] %s\n"
                         % ("?" if kind == "多义" else "✗", i, what))
    sys.stdout.write("  引文定位：唯一 %d · **只差空白 %d** · 找不到 %d · 多义 %d\n"
                     % (counts["唯一"], counts["唯一（只差空白）"],
                        counts["找不到"], counts["多义"]))
    if counts["唯一（只差空白）"]:
        sys.stdout.write("  ⚠️ 有引文只差空白 ⇒ 先查材料是不是没洗掉分词空格，"
                         "**别当成模型不合规**\n")

    kinds = [e.get("类型") for e in edges]
    sys.stdout.write("  边类型：%s\n" % kinds)
    sys.stdout.write("  ⇒ 去重后 %d 种（%d 条边）—— 复用得越多越好\n"
                     % (len(set(kinds)), len(kinds)))
    allm = set()
    for g in got["组"]:
        allm.update(g.get("成员") or [])
    sys.stdout.write("  被划进组的节点：%d / %d\n" % (len(allm), len(nodes)))

    # ★ 追问：**在同一个上下文里**接着问（把它刚交的产出接在中间），它才指得到具体那一处。
    #   另起一次问答时它没做过这份活，只能泛泛猜"什么会难"——实测那种问法多数模型思考量为 0。
    if args.追问 or args.评 or args.内聚:
        if args.内聚:
            ask, suffix, label = FOLLOWUP_内聚, ".内聚.txt", "内聚自查"
        elif args.评:
            ask, suffix, label = FOLLOWUP_评, ".评.txt", "评提示词"
        else:
            ask, suffix, label = FOLLOWUP, ".追问.txt", "追问"
        msgs = [{"role": "user", "content": out["prompt"]},
                {"role": "assistant", "content": out["raw"]},
                {"role": "user", "content": ask}]
        try:
            # ★ 追问必须**跟标注用同一个思考设置**——否则"追问质量"这一栏
            #   比的就不是思考开关，而是默认值（实测漏传过一次：六份追问思考全 0）。
            r2 = llm.call_model(msgs, model=args.model, max_tokens=args.max_tokens,
                                via=args.via, extra=out.get("请求额外字段"))
        except RuntimeError as e:
            sys.stdout.write("  ⛔ 追问失败：%s\n" % e)
            return 0
        io.open(base + suffix, "w", encoding="utf-8", newline="\n").write(
            r2["content"])
        sys.stdout.write("  ★ %s（同上下文）%d 字 · 思考 %d 字 ⇒ %s%s\n"
                         % (label, len(r2["content"]),
                            len(r2["reasoning"] or ""), base, suffix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
