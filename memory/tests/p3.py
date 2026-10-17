# -*- coding: utf-8 -*-
"""P3 自检：回忆 `[R]` —— 起点 · 传播（α/β/γ/T）· 取整组原文 · 只读。

跑：`python memory/tests/p3.py`
"""
import gzip
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from memory import recall                                   # noqa: E402
from memory.film import 提示词哈希                          # noqa: E402
from memory.graph import 图                                  # noqa: E402
from memory.knobs import 回忆 as K                          # noqa: E402

坏 = []


def 断言(名, 条件, 实际=None):
    if 条件:
        print("  ok   %s" % 名)
    else:
        坏.append(名)
        print("  FAIL %s   实际=%r" % (名, 实际))


def 造底片(dir_, 场id, 轮们):
    """轮们 = [(刺激, 她说), …] —— 写一份**真的 `.json.gz` 底片**，让"取整组原文"真去切区间。"""
    逐轮, 原文 = [], ""
    for i, (刺, 说) in enumerate(轮们, start=1):
        s0 = len(原文)
        原文 += 刺 + 说
        逐轮.append({"轮": i, "刺激": 刺, "她说": 说,
                     "刺激区间": [s0, s0 + len(刺)], "她说区间": [s0 + len(刺), len(原文)],
                     "调用": [], "取用": None})
    d = {"场id": 场id, "系统前缀": ["s1", "s2"], "原文": 原文, "逐轮": 逐轮,
         "撤回记录": [], "指纹": {"提示词哈希": 提示词哈希("a", "b")}}
    os.makedirs(os.path.join(dir_, "底片"), exist_ok=True)
    with gzip.GzipFile(os.path.join(dir_, "底片", 场id + ".json.gz"), "wb", compresslevel=6,
                       mtime=0) as fh:
        fh.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode())


def 嵌入(文本们):
    """假嵌入：按首字散成确定的三维向量。（只用来验证"排序/传播"，不比语义质量。）"""
    out = []
    for t in 文本们:
        x = sum(ord(c) for c in str(t)) or 1
        out.append([((x * 7) % 13) / 13.0, ((x * 11) % 17) / 17.0, ((x * 13) % 19) / 19.0])
    return out


def main():
    print("[1] 空图 / 空标记集 ⇒ 什么都取不到（不是崩，也不是编一个）")
    tmp = tempfile.mkdtemp(prefix="palm-p3-")
    g = 图(tmp)
    r0 = recall.回忆("随便问问", g, os.path.join(tmp, "底片"), 嵌入=嵌入)
    断言("★ 标记集空 ⇒ 返回空 + 一句为什么", r0["件"] == [] and "为什么" in r0, r0)

    print("[2] 起点 = 相似度 × 权重（置信度先顶着）")
    造底片(tmp, "2026-10-17-01", [("我半夜最清醒", "嗯，我知道。"), ("他习惯晚上干活", "记下了。")])
    g = 图(tmp)
    g.版本 = 1
    # 两个件：件A 的原文最像线索；件B 不像
    cellA = g.加cell("2026-10-17-01", 1, 0, 0)
    cellB = g.加cell("2026-10-17-01", 2, 0, 0)
    向A = 嵌入(["我半夜最清醒"])[0]
    向B = 嵌入(["他习惯晚上干活"])[0]
    件A = g.新件([cellA["id"]], 向A, 代表=cellA["id"], 强度=1.0)
    件B = g.新件([cellB["id"]], 向B, 代表=cellB["id"], 强度=1.0)
    g.标记集 = {件A: 1.0, 件B: 1.0}
    前 = (g.摘要(), dict(g.标记集), [dict(c) for c in g.cell],
          {k: dict(v) for k, v in g.件.items()})
    r = recall.回忆("我半夜最清醒", g, os.path.join(tmp, "底片"), 嵌入=嵌入)
    后 = (g.摘要(), dict(g.标记集), [dict(c) for c in g.cell],
          {k: dict(v) for k, v in g.件.items()})
    断言("★ 最像的那件排第一", r["件"] and r["件"][0]["件id"] == 件A, r["件"])
    # ★★ 2026-10-17：原文现在**带说话人前缀**（`他：` / `她：`）——
    #   审查抓到：原来把"用户的话"和"她的话"无标记拼成一行，而编写提示词断言
    #   「都是你自己说过的话」⇒ 用户的自述被当成她的第一人称记忆，**不报错**。
    原文0 = r["件"][0]["原文们"] or [""]
    断言("★ 每件带**整组原文**（从底片按区间切回来，带说话人前缀）",
         len(原文0) == 1 and "我半夜最清醒" in 原文0[0] and "嗯，我知道。" in 原文0[0]
         and 原文0[0].startswith("他：") and "她：" in 原文0[0], 原文0)
    断言("★★ 只读：回忆前后**图一个字没动**（摘要/标记集/cell/件 全量比对）",
         前 == 后, (前[0], 后[0]))

    print("[3] 权重参与：同一个相似度，置信度高的排前面")
    g.标记集 = {件A: 0.15, 件B: 1.0}      # 故意把像的那件压低
    r2 = recall.回忆("我半夜最清醒", g, os.path.join(tmp, "底片"), 嵌入=嵌入)
    断言("★ 置信度被压到 0.15 ⇒ 像的那件掉到后面", r2["件"][0]["件id"] == 件B, r2["件"])

    print("[4] 传播：组间共现（β）能把不带边的件抬起来")
    g.标记集 = {件A: 1.0, 件B: 0.0}
    边 = recall.共现边(g)
    断言("★ 同场共现 ⇒ 两个件之间有边", 边.get(件A, {}).get(件B, 0) > 0, 边)
    起点 = {件A: 1.0, 件B: 0.0}
    传 = recall.传播(起点, {}, {件A: [], 件B: []}, {件A: {件B: 1.0}, 件B: {件A: 1.0}},
                    α=float(K["α"]["值"]), β=float(K["β"]["值"]),
                    γ=float(K["γ_传播"]["值"]), T=int(K["T"]["值"]))
    断言("★★ β 沿共现边把亮度传过去（件B 从 0 抬起来）",
         传[件B] > 0 and abs(传[件B] - 起点[件B]) > 0, 传)
    断言("★ T 轮会累积（T=4 ⇒ 件B 比只传 1 轮大）",
         传[件B] > recall.传播(起点, {}, {}, {件A: {件B: 1.0}, 件B: {件A: 1.0}},
                               β=float(K["β"]["值"]), T=1)[件B], 传)

    print("[5] 组内：α 给成员加成、γ 回灌给组（用合成的 cell 向量验数学）")
    合成 = {"组": 0.2, "m1": 0.0, "m2": 0.0}
    传2 = recall.传播(合成, {}, {"组": ["m1", "m2"]}, {}, α=0.6, γ=0.5, T=1)
    # ★★ 2026-10-17：**传播现在每轮按最大值归一化到 [0,1]** ——
    #   原来只累加不归一化，是纯正反馈，T=4 轮**指数爆炸**（实测一场 32 轮单件 +70.9，
    #   被 `min(1.0,…)` 削平 ⇒ 所有件满格 ⇒「提得多记得牢」变成「什么都记牢」）。
    #   ⇒ 断言改成**比值**（归一化后天然是比值），⛔ 写死绝对值。
    #   未归一化时：m1 = 0.12 · 组 = 0.30（峰）· m2 = 0.12
    #   归一化后：  组 = 1.0 · m1/m2 = 0.12/0.30 = 0.4
    断言("★ 归一化：峰值（组）被压到 1.0", abs(传2["组"] - 1.0) < 1e-9, 传2)
    断言("★ 成员拿到 α×组亮 ⇒ 与组的比 = 0.12/0.30 = 0.4",
         abs(传2["m1"] / 传2["组"] - 0.4) < 1e-9, 传2)
    断言("★ 软加成、不硬补齐（成员没有被拉成和组一样亮）", 传2["m1"] < 传2["组"], 传2)

    print("[6] 参数账：k / α / β / γ_传播 / T 都来自 `knobs`")
    断言("★ 参考值 4 / 0.60 / 0.35 / 0.50 / 4",
         (K["k"]["值"], K["α"]["值"], K["β"]["值"], K["γ_传播"]["值"], K["T"]["值"])
         == (4, 0.60, 0.35, 0.50, 4),
         (K["k"]["值"], K["α"]["值"], K["β"]["值"], K["γ_传播"]["值"], K["T"]["值"]))
    断言("★ 每次回忆都回一份「这次用的参数」（要能进逐轮记录）",
         r["参数"]["k"] == 4 and "β" in r["参数"], r["参数"])

    print("[7] 参数账的**形状**：每一项都得能回答「凭什么」")
    from memory.knobs import 参数账
    _账 = 参数账()
    # ★ 2026-10-17：`②` 换成"用读法预测每一格"之后多了两个旋钮（预测前缀字数 / 预测上下文轮数）
    # ★ 2026-10-17（审查后）：编写温度原来是 `writing.py` 里的裸 0.7，现在提到账上 ⇒ 26 项。
    #   ⚠️ 这个数**只是"账没被悄悄改小"的警报**，改账就该改它 —— 但别拿它当"账目正确"的证据。
    断言("★ 新线参数账 26 项", len(_账) == 26, len(_账))
    断言("★★ 每一项都有 值 / 依据（⛔ 裸值不许出现）",
         all(("值" in v and str(v.get("依据") or "").strip()) for v in _账.values()),
         [k for k, v in _账.items() if not str(v.get("依据") or "").strip()])
    # ★★ 2026-10-17 加：设置页的每一条旋钮都必须**真的存在**于 Config 上。
    #   这条抓的是一类真出过的错：`render_recall` 被写错了类（挂在 `CooldownConfig` 上），
    #   而读它的地方用 `getattr(cfg.session, ..., True)` 兜着 ⇒ 界面上拧它**一点用都没有**。
    from memory.config import Config
    from memory.settings import KNOBS
    _c = Config()
    _坏路 = []
    for _k in KNOBS:
        _cur = _c
        try:
            for _part in _k.path.split("."):
                _cur = getattr(_cur, _part)
        except AttributeError:
            _坏路.append(_k.path)
    断言("★★ 设置页每条旋钮的路径都能在 Config 上解析到（⛔ 不许靠 getattr 兜底）",
         not _坏路, _坏路)
    # ★★ 2026-10-17（审查抓到）：账上有几个数**没人读**（拧了不会有任何变化，且不报错）。
    #   它们⛔ 删（"当初为什么要这个数"要留着），但必须**登记成无读者** ⇒ 自检盯着两边对齐。
    from memory.knobs import 死旋钮
    _缺 = [k for k in 死旋钮 if k not in _账]
    _没标 = [k for k in 死旋钮 if k in _账 and not _账[k].get("无读者")]
    _空说 = [k for k, v in 死旋钮.items() if len(str(v).strip()) < 10]
    断言("★★ 死旋钮都还在账上、并且都打了「无读者」标记", not _缺 and not _没标,
         (_缺, _没标))
    断言("★ 每个死旋钮都写清了「为什么没人读」", not _空说, _空说)

    print()
    if 坏:
        print("有 %d 处不过：%s" % (len(坏), 坏))
        return 1
    print("P3 自检：全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
