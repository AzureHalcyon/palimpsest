# -*- coding: utf-8 -*-
"""P2 自检：**归档五步**（①切 cell → ②误差 → ③配对 → ⑤判 → ④层1/层2）。

跑：`python memory/tests/p2.py`
（`smoke.py` 守"搬过来还是同一台机器"；`p1.py` 守底片/编写；这一份守**图与归档**。）

★ 全部离线：`⑤` 用 `ScriptedLLM` 替身，`②` 用**手写的 logprobs**（不联网）。
★ 钉住的三条硬承诺：
  ① **草案不动图** —— 算得再多，`标记集`/`件`/`版本` 一个都不许动；
  ② **标记为空就不问模型** —— 第一批归档必然没候选，⛔ 不该白花一次调用；
  ③ **按完成才写** —— ④ 层1/层2 + 版本 +1 只发生在 `完成()`。
"""
import contextlib
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from memory import archive_step, markset                      # noqa: E402
from memory.graph import 图                                   # noqa: E402
from memory.knobs import 件 as K件, 标记 as K标记, 判 as K判    # noqa: E402
from memory.llm import ScriptedLLM                            # noqa: E402

坏 = []


def 断言(名, 条件, 实际=None):
    if 条件:
        print("  ok   %s" % 名)
    else:
        坏.append(名)
        print("  FAIL %s   实际=%r" % (名, 实际))


def _lp(平, n):
    """造一段 logprobs：`平` = 每个 token 的 −log p（越大越意外）。"""
    return {"推理": [], "正文": [{"ord": i, "token": "字", "logprob": -平,
                                  "top": [{"token": "字", "logprob": -平}]} for i in range(n)]}


def 造底片(轮们):
    """轮们 = [(刺激, 她说, −log p), …] —— 只造 ①② 要用的那几格。"""
    逐轮 = []
    for i, (刺, 说, 平) in enumerate(轮们, start=1):
        逐轮.append({"轮": i, "刺激": 刺, "她说": 说, "刺激区间": [0, len(刺)],
                     "她说区间": [len(刺), len(刺) + len(说)],
                     "调用": [{"序": 0, "用途": "编写", "logprobs": _lp(0.1, 2)},
                              {"序": 1, "用途": None, "logprobs": _lp(平, 6)}],
                     "取用": None})
    原文 = "".join(t["刺激"] + t["她说"] for t in 逐轮)
    return {"场id": "2026-10-17-01", "原文": 原文, "逐轮": 逐轮, "撤回记录": [], "指纹": {}}


def 嵌入(文本们):
    """假的嵌入：给每条一个确定向量（首字的三维哈希，够用且可复现）。"""
    out = []
    for t in 文本们:
        x = sum(ord(c) for c in str(t)) or 1
        out.append([((x * 7) % 13) / 13.0, ((x * 11) % 17) / 17.0, ((x * 13) % 19) / 19.0])
    return out


def 预测替身(n: int, 平: float = 0.6, 高在第几格: int = 7, 高: float = 2.5):
    """② 的替身：**有起伏**的一段 logprobs。

    ⚠️ 别都写成同一个值：`标记()` 用的是 `x > μ+γσ`，全平时 σ=0 ⇒
       一条标记都不会有 ⇒ 后面"配对/判/新建"全塌（这条踩过，写在这里免得再踩）。
    ★ 起伏的位置就是"读法预测得最差的那一格" —— 这正是 `②` 该找的东西。
    """
    from memory.llm import ScriptedLLM
    回 = []
    for i in range(n):
        p = 高 if (i + 1) == 高在第几格 else 平
        回.append({"role": "assistant", "content": "嗯",
                   "logprobs": {"content": [
                       {"token": "嗯", "logprob": -p},
                       {"token": "。", "logprob": -p}]}})
    return ScriptedLLM(回, fallback={"role": "assistant", "content": "嗯",
                                    "logprobs": {"content": [
                                        {"token": "嗯", "logprob": -平}]}})


def main():
    print("[1] ①cell · ②误差 · 标记")
    # 7 轮：前 6 轮很平（−log p = 0.5），第 7 轮突然很意外（4.0）⇒ 只有它该被标
    轮们 = [("嗯", "在。", 0.5)] * 6 + [("我搬了", "搬哪儿了？", 4.0)]
    底 = 造底片(轮们)
    序列 = archive_step.误差序列(底)
    断言("★ 每轮一个误差数（取「出正文」那次调用的 logprobs）",
         len(序列) == 7 and abs(序列[0]["误差"] - 0.5) < 1e-9 and abs(序列[-1]["误差"] - 4.0) < 1e-9,
         序列)
    断言("★ `编写` 那次调用**不**参与误差（它是为了给她看另做的生成）",
         all(e["token数"] == 6 for e in 序列), [e["token数"] for e in 序列])
    标 = archive_step.标记(序列)
    断言("★★ 只有那一处意外被标出（前 τ 轮不判 —— 没有窗口）",
         [m["轮"] for m in 标] == [7], 标)
    断言("★ 阈值用的是 `μ_{t−τ..t−1} + γ·σ`（窗口里全是 0.5 ⇒ 阈值 0.5）",
         abs(标[0]["阈值"] - 0.5) < 1e-9, 标[0]["阈值"])
    # ★★ 2026-10-17（作者定）：② 的取数**换回她自己的意外** —— 作者原话
    #   「我们要的是记忆，一个 prefix 啥也测不出来。」⇒ 预测误差（让模型接着写）已删。
    #   取哪一项由 knobs["②分项"] 定；默认 误差_推理（她"想"时的不确定）。
    分项 = K标记["②分项"]["值"]
    断言("★★ ②分项 = 推理（『她卡了一下』的直信号，⛔ 正文措辞）",
         分项 == "误差_推理", 分项)
    _无推理 = archive_step.误差序列({"逐轮": [{"轮": 1, "调用": [{"用途": "出正文",
        "logprobs": {"正文": [{"logprob": -0.4}, {"logprob": -0.6}]}}]}]})
    断言("★ 没推理时不崩、误差_推理 为 None、正文有值",
         _无推理[0]["误差_推理"] is None and abs(_无推理[0]["误差_正文"] - 0.5) < 1e-9, _无推理[0])

    # ---------------------------------------------------------------- [1b] 全场兜底
    # ★★★ 【作者定 2026-10-17】3b：滑动窗口**一个意外都没标**时，回头看整场。
    #   依据：场 7（8 轮）窗口 0 个、最接近的差 0.05（`待决 §24`）。
    print("\n[1b] 全场兜底（3b）：窗口一个都没标 ⇒ 回头看整场")
    _序 = [{"轮": i + 1, "误差": x} for i, x in enumerate([0.5, 3.0, 0.5, 3.0, 0.5, 0.8, 0.8, 0.8])]
    断言("★ 这一组滑动窗口确实一个都没标（⚠️ 先证明前提成立）",
         not archive_step.标记(_序), archive_step.标记(_序))
    _标全 = archive_step.全场标记(_序, γ=1.0)
    断言("★★ 全场兜底标出了那两处尖峰（轮 2 / 轮 4）",
         [m["轮"] for m in _标全] == [2, 4], _标全)
    断言("★ 兜底标出来的来源仍是「意外」（⛔ 另造一个来源），另带 `判法=全场` 好追溯",
         all(m.get("来源") == "意外" and m.get("判法") == "全场" for m in _标全), _标全)
    # ★ 接线（⛔ 只测纯函数）：真走一遍 `草案`，看它有没有把兜底接进去
    _底8 = 造底片([("嗯", "在。", x) for x in [0.5, 3.0, 0.5, 3.0, 0.5, 0.8, 0.8, 0.8]])
    _底8["场id"] = "2026-10-17-01b"
    _g8 = 图(tempfile.mkdtemp(prefix="palm-p2b-"))
    _llm8 = ScriptedLLM([{"role": "assistant", "content": '{"记": []}'}],
                        fallback={"role": "assistant", "content": '{"记": []}'})
    _草8 = archive_step.草案(_底8, _g8, _llm8, "2026-10-17-01b", 嵌入=嵌入)
    _轮8 = [int(m["轮"]) for m in (_草8.get("标记") or [])]
    断言("★★★ 接上了：`草案` 自己在窗口落空时用全场兜底（轮 2 / 轮 4）",
         _轮8 == [2, 4], _轮8)
    断言("★ 兜底出来的那两处也带 `判法=全场`（事后分得清是哪条判法标的）",
         all(str(m.get("判法")) == "全场" for m in (_草8.get("标记") or [])), _草8.get("标记"))

    print("[2] ③配对 / ⑤判（第一批：标记集为空 ⇒ ⑤ **不问模型**）")
    tmp = tempfile.mkdtemp(prefix="palm-p2-")
    g = 图(tmp)
    # ★ 2026-10-17：归档时**多了一次**「问她要不要标」（`archive_step._问她要不要标`）——
    #   那是第三条途径，和 ⑤ 不是一件事。脚本第一句回它，后面才轮到 ⑤。
    llm = ScriptedLLM([{"role": "assistant", "content": '{"记": []}'}],
                      fallback={"role": "assistant", "content": '{"归进": null, "新建": true, "说法": "第一批"}'})
    草1 = archive_step.草案(底, g, llm, "2026-10-17-01", 嵌入=嵌入)
    _判调 = [c for c in llm.calls if "下面是这一场里的一处" in str(c.get("messages"))]
    断言("★★ 标记集为空 ⇒ ⑤ 一次都没调（⛔ 不白花钱）；只调了「问她要不要标」那一次",
         len(_判调) == 0 and len(llm.calls) == 1, (len(_判调), len(llm.calls)))
    断言("★ 她的自问自答进了草案（`自标`），且这一场她一处都没报",
         草1.get("自标") is not None and 草1["自标"]["列表"] == [], 草1.get("自标"))
    断言("★ 草案里给了「新建」的判", 草1["判"] and 草1["判"][0]["新建"] is True, 草1["判"])
    断言("★★ 草案**不动图**（版本/件/标记集一个都没动）",
         g.版本 == 0 and not g.件 and not g.标记集 and not g.cell, g.摘要())

    print("[3] 按「完成」才写：④ 层1（新建）+ ④ 层2（衰减）")
    r1 = archive_step.完成(g, 草1)
    # ★ 2026-10-17：定标记改成**排名取前 k**（`决策记录 2026-10-16`）⇒ 一场可能 2 处，
    #   所以件数从草案里数，⛔ 不写死 1。
    # 阈值判据（流程框架 [8]）下，这份材料只标第 7 轮 ⇒ 一件
    断言("★★ 完成之后才有件、才有置信度（新建 = 初值 1.0，再被层2衰减一次 ⇒ 0.9）",
         len(g.件) == 1 and abs(list(g.标记集.values())[0] - 0.9) < 1e-9,
         (len(g.件), g.标记集))
    断言("★ 版本 +1、图落盘了", g.版本 == 1 and os.path.exists(g._路径()), (g.版本, os.path.exists(g._路径())))
    # ★★ `版本对`（`流程框架 §3.1.3`：存储 · 一场一次归档 · 必填）——
    #   "变"和"记录错了"分不开，就是缺它。⛔ 也不许带时间戳（冻结、可比较）。
    _vp = os.path.join(tmp, "版本对.jsonl")
    _行 = [json.loads(x) for x in io.open(_vp, encoding="utf-8") if x.strip()] \
        if os.path.exists(_vp) else []
    断言("★★ 「完成」写出 `版本对`（场id + 图版本 + 读法版本 + 指纹，且不带时间戳）",
         len(_行) == 1 and _行[0].get("场id") == "2026-10-17-01"
         and _行[0].get("图版本") == 1 and isinstance(_行[0].get("读法版本"), int)
         and isinstance(_行[0].get("指纹"), dict)
         and not any(k in _行[0] for k in ("ts", "时间", "落盘时间")), _行)
    断言("★ `判/` 落盘了（模型生成过的东西必须留档）",
         os.path.isdir(os.path.join(tmp, "判"))
         and os.path.getsize(os.path.join(tmp, "判", "2026-10-17-01.jsonl")) > 0)
    断言("★ cell 建出来了、锚在原文区间上",
         len(g.cell) == len(草1.get("cell") or []) and all(c["止"] > c["起"] for c in g.cell),
         (len(g.cell), g.cell))
    # ★★ 2026-10-17（真机跑出来的）：cell 是 ①切cell 的**机械产物** ⇒ 这一场**所有**轮
    #   都要落图，哪怕一条标记都没有。真机上真出过：9 轮、含「他辞职了」这种明显意外，
    #   图版本 +1 但 `cell = 0` ⇒ 那 9 轮在图里一点痕迹都没有，
    #   以后任何"引用那一场原文"的东西都锚不上（件的成员就是 cell）。
    断言("★★ 没标记的轮也落 cell（机械归机械，⛔ 不因没判就丢掉原文区间）",
         len(g.cell) == 7, len(g.cell))

    print("[4] 第二场：有候选 ⇒ ⑤ 真问模型 ⇒ 归进 + 强化")
    g2 = 图载入(tmp)
    件id = list(g2.件.keys())[0]
    _归进回 = {"role": "assistant",
              "content": '{"归进": "%s", "新建": false, "说法": "同一件事"}' % 件id}
    # ★ 一场可能判 2 处（排名取前 k）⇒ 第二处也要回「归进」，否则会走 fallback 去新建
    # ★ 第一句是回「问她要不要标」的那一问（她一处都不标）
    llm2 = ScriptedLLM([{"role": "assistant", "content": '{"记": []}'}, dict(_归进回)],
                       fallback=dict(_归进回))
    底2 = 造底片([("嗯", "在。", 0.5)] * 6 + [("又搬了", "又搬？", 4.0)])
    底2["场id"] = "2026-10-17-02"
    # ★ 这一段测的是 ⑤ 的 (a)「归进」路径。★ 2026-10-17 加了**第二个入口**
    #   （很像但不意外的格子也送去判）⇒ 这里把它关掉，否则会多判一次、把 scripted 的回复吃掉。
    from memory.knobs import 判 as _K判
    _旧阈 = _K判["重复候选相似度"]["值"]
    _K判["重复候选相似度"]["值"] = 1.01          # 余弦 ≤ 1 ⇒ 永不触发
    try:
        草2 = archive_step.草案(底2, g2, llm2, "2026-10-17-02", 嵌入=嵌入)
    finally:
        _K判["重复候选相似度"]["值"] = _旧阈
    _判调2 = [c for c in llm2.calls if "下面是这一场里的一处" in str(c.get("messages"))]
    断言("★ 有候选时**真问了模型**一次", len(_判调2) == 1, (len(_判调2), len(llm2.calls)))
    断言("★ 候选里含那个已有的件",
         草2["候选"] and 件id in [c[0] for c in 草2["候选"][草2["标记"][0]["轮"]]],
        草2["候选"])
    断言("★ 判它归进", 草2["判"][0]["归进"] == 件id, 草2["判"][0])
    n0 = len(g2.件)
    更前 = dict(g2.标记集)
    r2 = archive_step.完成(g2, 草2)
    断言("★★ 归进之后**没有新建**、成员多了那个 cell",
         len(g2.件) == n0 and len(g2.件[件id]["成员"]) >= 2, (len(g2.件), g2.件[件id]["成员"]))
    # ★★ 2026-10-17（作者口径）改了：**归进⛔ 再 +0.05** ——
    #   归档只管**结构**（这格并进哪件），**强度归激活管**（重提强化那一段），
    #   否则同一件会被强化两次。⇒ 这里只剩层2的衰减：0.9 → 0.8。
    #   ⚠️ "重提强化"要 `草案["激活们"]` 有东西才生效 —— 这个材料的激活是空的。
    断言("★ 归进⛔再+0.05（强度归激活）：上一场 0.9 ⇒ 只过层2衰减 ⇒ 0.8",
         abs(g2.标记集[件id] - 0.8) < 1e-9, (更前, g2.标记集))

    print("[5] ④ 层2 的剪出（掉到门槛以下 ⇒ 进待观察池，⛔ 不删件）")
    集 = {件id: float(K件["遍历门槛"]["值"])}     # 刚好在门槛上
    新, 剪 = markset.衰减(集, 窗口数=1)
    断言("★ 衰减到门槛以下 ⇒ 被剪出", 剪 == [件id], (新, 剪))
    g3 = 图载入(tmp)
    g3.标记集 = dict(集)
    g3.剪出(件id, "测试")
    断言("★★ 剪出**不删件**，只进待观察池（件还在图里）",
         件id not in g3.标记集 and 件id in g3.件
         and any(x["单元"] == 件id for x in g3.待观察池), g3.摘要())

    print("[6] 旋钮与文档一致（这几条一改，上面所有读数都要重标）")
    断言("★ γ=1 / τ=5 / 门槛=0.2 / 拉起=0.25 取自 `knobs`（不是代码里写死）",
         K标记["γ"]["值"] == 1.0 and K标记["τ"]["值"] == 5
         and K件["遍历门槛"]["值"] == 0.2 and K件["冷成员拉起"]["值"] == 0.25)
    断言("★ ⑤ 的候选件数 = 8（区间 5–10）", K判["候选件数"]["值"] == 8)
    # ★★★ 2026-10-17（9 场读数）：**顶亮那件必须能站住** —— 否则「重提 / repetition」这条路
    #   等于不存在（意外档全是临时的，4–6 场掉出门槛）。
    #   强化按**这一场的平均激活**算（∈ [0,1]，⛔ Σ —— Σ 随场次长短线性涨、差 6 倍），
    #   而衰减是按"一场"扣固定值 ⇒ 同一个量纲上：**系数 ≤ 衰减 ⇒ 顶亮那件也永远在掉**。
    #   ⚠️ 这条抓的正是系数被随手调小（它原来是 0.005，顶亮那件一场还净掉 0.05）。
    断言("★★ 重提顶得住衰减：系数 > 每窗口衰减（否则顶亮那件也永远在掉）",
         float(K件["激活强化系数"]["值"]) > float(K件["每窗口衰减"]["值"]),
         (K件["激活强化系数"]["值"], K件["每窗口衰减"]["值"]))

    print("[7] 新线归档入口（Session 层）：草案是新形状 · 改按字面 · 完成才写")
    import tempfile as _tf
    from memory.config import Config as _Cfg
    from memory.embedder import HashingEmbedder as _Emb
    from memory.engine import Engine as _Eng
    from memory.session import Session as _S

    tmp3 = _tf.mkdtemp(prefix="palm-p2s-")
    cfg3 = _Cfg()
    emb3 = _Emb(dim=64)
    llm3 = ScriptedLLM([{"role": "assistant", "content": "嗯，我在。"}] * 8,
                      fallback={"role": "assistant", "content": "嗯"})
    eng3 = _Eng(cfg3, emb3, llm3, tmp3)
    eng3.advance_to_wall(ts=1_000_000.0)
    eng3.save()
    s3 = _S(eng3, 1)
    s3.ask("在吗")
    草 = s3.archive_draft()
    断言("★ 草案是新形状：`land=yes` + `草` + 一句摘要",
         草.get("land") == "yes" and isinstance(草.get("草"), dict)
         and bool(str(草["草"].get("摘要") or "")), 草.get("land"))
    断言("★ 没标记 ⇒ 摘要如实说「没有值得记的地方」（⛔ 不编）",
         "没有值得记的地方" in str(草["草"].get("摘要") or ""), 草["草"].get("摘要"))

    # 手写一份有判的草案，验「第 N 轮别记」
    s3.rec["archive_draft"] = {"land": "yes", "talk": [],
                               "草": {"摘要": "这一场认出 2 处：",
                                     "判": [{"轮": 3, "归进": None, "新建": True, "说法": "a"},
                                           {"轮": 5, "归进": "件-0001", "新建": False, "说法": "b"}]}}
    r = s3.archive_revise("第 3 轮别记")
    断言("★★ 「第 3 轮别记」⇒ 那一处标成 `跳过`，另一处不动",
         r.get("改了") == [3]
         and s3.rec["archive_draft"]["草"]["判"][0].get("跳过") is True
         and not s3.rec["archive_draft"]["草"]["判"][1].get("跳过"), r)
    r2 = s3.archive_revise("随便说点什么")
    from memory.session import waiting_for_review as _wfr
    断言("★★ 审阅期判定**认新草案**（否则重启会被当「归档跑一半」补做、写进旧池子）",
         _wfr({"ts_end": 1.0, "archive_draft": {"land": "yes", "草": {"判": []}}}) is True
         and _wfr({"ts_end": 1.0, "archive_draft": {"land": "yes", "ar": {"facts": []}}}) is True,
         _wfr({"ts_end": 1.0, "archive_draft": {"land": "yes", "草": {"判": []}}}))
    断言("★ 认不出要改哪一处 ⇒ **如实说**没认出（⛔ 不假装改过）",
         "没认出" in str(r2.get("err") or ""), r2.get("err"))

    v0 = eng3.sessions_done
    s3.archive_commit()
    断言("★★ 按「完成」⇒ 场次 +1、pending 清掉（收尾只做收尾，不再碰记忆）",
         eng3.sessions_done == v0 + 1 and not eng3.pending.exists(),
         (eng3.sessions_done, eng3.pending.exists()))
    断言("★ 图版本 +1（新线真的写了图）", 图.载入(tmp3).版本 == 1, 图.载入(tmp3).版本)
    断言("★ 幂等账记下了这一场（`applied_sids`，守「转写永远只写一行」）",
         int(s3.k) in eng3.applied_sids, (s3.k, eng3.applied_sids))
    # ★★ 2026-10-17（审查抓到）：`_settle(transcript=None)` 会**不留转写行**地收尾，
    #   而 `applied_sids` 记上了 ⇒ 以后带转写再来也补不上。⇒ 必须能被看见。
    断言("★★ 没给转写 ⇒ 记进 `缺转写`（⛔ 悄悄漏掉那一行）",
         int(s3.k) in eng3.缺转写, (s3.k, eng3.缺转写))
    断言("★ `缺转写` 落进 state.json（重启后还看得见）",
         int(s3.k) in [int(x) for x in (json.loads(io.open(
             os.path.join(tmp3, "state.json"), encoding="utf-8").read()).get("缺转写") or [])],
         json.loads(io.open(os.path.join(tmp3, "state.json"), encoding="utf-8").read()).get("缺转写"))
    断言("★ 引擎自己不装记忆：state.json 里**没有** `temp` 那一格",
         "temp" not in json.loads(io.open(os.path.join(tmp3, "state.json"),
                                         encoding="utf-8").read()))

    # ---------------------------------------------------------------- [8] 坏图
    # ★ 2026-10-17（审查抓到）：坏图**当空图且不报** ⇒ 她"什么都不记得了"，而用户找不到原因。
    #   现在：stderr 报 + 坏档改名留档 + 回到空图（⛔ 半份图）。
    print("\n[8] 坏图：要报、要留档、⛔ 半份图")
    tmp8 = tempfile.mkdtemp(prefix="p2g")
    g8 = 图载入(tmp8)
    g8.新件(["cell-0001"], [0.1], "代表")
    g8.落盘()
    p8 = g8._路径()
    with io.open(p8, "wb") as fh:
        fh.write(b"not gzip at all")
    _err = io.StringIO()
    with contextlib.redirect_stderr(_err):
        g9 = 图.载入(tmp8)
    断言("★★ 坏档 ⇒ 空图 + 记下原因（⛔ 不静默）",
         bool(g9.载入错误) and not g9.件 and not g9.标记集 and not g9.边,
         (g9.载入错误, g9.件, g9.标记集, g9.边))
    断言("★ 坏档改名留档（⛔ 覆盖原文 ⇒ 也⛔ 每次都坏）",
         any(".坏-" in f for f in os.listdir(tmp8)) and not os.path.exists(p8),
         os.listdir(tmp8))
    断言("★ 报的那句话里带上了路径与原因", p8 in _err.getvalue() and "坏档" in _err.getvalue(),
         _err.getvalue())

    # ---------------------------------------------------------------- [9] 底片按需读
    # ★★★ 2026-10-17（"几十场还顶不顶得住"）：归档原来把底片目录**全读**（只为拿候选件的
    #   代表原文）⇒ 每归档一次 O(全部历史) 解压。现在按需读一场。
    print("\n[9] 归档读底片：**按需**一场，⛔ 全读")
    import gzip as _gz
    _dir = tempfile.mkdtemp(prefix="p2film-")
    for _场 in ("2026-10-17-71", "2026-10-17-72"):
        with _gz.open(os.path.join(_dir, _场 + ".json.gz"), "wt", encoding="utf-8") as _fh:
            _fh.write(json.dumps({"场id": _场, "原文": "x", "逐轮": [
                {"轮": 1, "刺激": "嗯", "她说": "在", "刺激区间": [0, 1], "她说区间": [1, 2]}]}))
    _表 = archive_step.底片表(_dir)
    断言("★★ 建表时**一张都没读**（⛔ 全读）", len(_表) == 0, len(_表))
    _一 = _表.get("2026-10-17-71")
    断言("★ 问哪场读哪场", bool(_一) and _一.get("场id") == "2026-10-17-71" and len(_表) == 1,
         (bool(_一), len(_表)))
    断言("★ 问一个不存在的场 ⇒ 返回 None（⛔ 崩），而且记住别再试",
         _表.get("2026-10-17-99") is None and _表.get("2026-10-17-99") is None,
         sorted(_表))
    断言("★ 已经读过的那场第二次不再读盘（还在表里）", _表.get("2026-10-17-71") is _一)

    print()
    if 坏:
        print("有 %d 处不过：%s" % (len(坏), 坏))
        return 1
    print("P2 自检：全部通过")
    return 0


def 图载入(tmp):
    return 图.载入(tmp)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
