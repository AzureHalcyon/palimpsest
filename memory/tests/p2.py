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

    print("[2] ③配对 / ⑤判（第一批：标记集为空 ⇒ **不问模型**）")
    tmp = tempfile.mkdtemp(prefix="palm-p2-")
    g = 图(tmp)
    llm = ScriptedLLM([{"role": "assistant", "content": '{"归进": null, "新建": true, "说法": "第一批"}'}])
    草1 = archive_step.草案(底, g, llm, "2026-10-17-01", 嵌入=嵌入)
    断言("★★ 标记集为空 ⇒ 没有候选 ⇒ **一次模型都没调**（⛔ 不白花钱）",
         len(llm.calls) == 0, len(llm.calls))
    断言("★ 草案里给了「新建」的判", 草1["判"] and 草1["判"][0]["新建"] is True, 草1["判"])
    断言("★★ 草案**不动图**（版本/件/标记集一个都没动）",
         g.版本 == 0 and not g.件 and not g.标记集 and not g.cell, g.摘要())

    print("[3] 按「完成」才写：④ 层1（新建）+ ④ 层2（衰减）")
    r1 = archive_step.完成(g, 草1)
    断言("★★ 完成之后才有件、才有置信度（新建 = 初值 1.0，再被层2衰减一次 ⇒ 0.9）",
         len(g.件) == 1 and abs(list(g.标记集.values())[0] - 0.9) < 1e-9,
         (len(g.件), g.标记集))
    断言("★ 版本 +1、图落盘了", g.版本 == 1 and os.path.exists(g._路径()), (g.版本, os.path.exists(g._路径())))
    断言("★ `判/` 落盘了（模型生成过的东西必须留档）",
         os.path.isdir(os.path.join(tmp, "判"))
         and os.path.getsize(os.path.join(tmp, "判", "2026-10-17-01.jsonl")) > 0)
    断言("★ cell 建出来了、锚在原文区间上", len(g.cell) == 1 and g.cell[0]["止"] > g.cell[0]["起"], g.cell)

    print("[4] 第二场：有候选 ⇒ ⑤ 真问模型 ⇒ 归进 + 强化")
    g2 = 图载入(tmp)
    件id = list(g2.件.keys())[0]
    llm2 = ScriptedLLM([{"role": "assistant",
                         "content": '{"归进": "%s", "新建": false, "说法": "同一件事"}' % 件id}])
    底2 = 造底片([("嗯", "在。", 0.5)] * 6 + [("又搬了", "又搬？", 4.0)])
    底2["场id"] = "2026-10-17-02"
    草2 = archive_step.草案(底2, g2, llm2, "2026-10-17-02", 嵌入=嵌入)
    断言("★ 有候选时**真问了模型**一次", len(llm2.calls) == 1, len(llm2.calls))
    断言("★ 候选里含那个已有的件",
         草2["候选"] and 件id in [c[0] for c in 草2["候选"][草2["标记"][0]["轮"]]],
        草2["候选"])
    断言("★ 判它归进", 草2["判"][0]["归进"] == 件id, 草2["判"][0])
    n0 = len(g2.件)
    更前 = dict(g2.标记集)
    r2 = archive_step.完成(g2, 草2)
    断言("★★ 归进之后**没有新建**、成员多了那个 cell",
         len(g2.件) == n0 and len(g2.件[件id]["成员"]) >= 2, (len(g2.件), g2.件[件id]["成员"]))
    # ⚠️ 它上一场结束已经是 0.9（新建 1.0 被层2衰减过一次）⇒ 归进 +0.05 = 0.95 ⇒ 再衰减 −0.1 = 0.85
    断言("★ 强化 +0.05 之后再被层2衰减：上一场 0.9 → 0.95 → 0.85",
         abs(g2.标记集[件id] - 0.85) < 1e-9, (更前, g2.标记集))

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

    print("[7] 新线归档入口（Session 层）：草案是新形状 · 改按字面 · 完成才写")
    import tempfile as _tf
    from memory.config import Config as _Cfg
    from memory.embedder import HashingEmbedder as _Emb
    from memory.engine import Engine as _Eng
    from memory.pool import TempPool as _TP
    from memory.session import Session as _S

    tmp3 = _tf.mkdtemp(prefix="palm-p2s-")
    cfg3 = _Cfg()
    emb3 = _Emb(dim=64)
    llm3 = ScriptedLLM([{"role": "assistant", "content": "嗯，我在。"}] * 8,
                      fallback={"role": "assistant", "content": "嗯"})
    eng3 = _Eng(cfg3, emb3, llm3, _Eng.build_pool(cfg3, emb3, ["底色一条"], [], ["他养了只猫"]),
                _TP(cfg3.time), tmp3)
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
    池子前 = (len(eng3.long_pool.texts), len(eng3.temp_pool.items))
    s3.archive_commit()
    断言("★★ 按「完成」⇒ 场次 +1、pending 清掉（收尾走的是旧线那条幂等路）",
         eng3.sessions_done == v0 + 1 and not eng3.pending.exists(),
         (eng3.sessions_done, eng3.pending.exists()))
    断言("★ 图版本 +1（新线真的写了图）", 图.载入(tmp3).版本 == 1, 图.载入(tmp3).版本)
    断言("★ 旧的池子**一分没动**（借「池子不动」那条收尾）",
         (len(eng3.long_pool.texts), len(eng3.temp_pool.items)) == 池子前,
         (池子前, len(eng3.long_pool.texts), len(eng3.temp_pool.items)))

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
