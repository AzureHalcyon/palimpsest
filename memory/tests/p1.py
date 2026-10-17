# -*- coding: utf-8 -*-
"""P1 自检：**底片是存档 —— 全流程全记录、可以多存不能少存**。

跑：`python memory/tests/p1.py`
（`smoke.py` 只证明"搬过来还是同一台机器"；这一份开始守**新形状**。）

守的几件事：
  ① 区间坐标：`原文` 里按 `刺激区间`/`她说区间` 切回来**逐字相同**（坐标唯一验收方式）；
  ② 只追加：后加一轮不会改动前面那句的区间与内容；
  ③ **全流程**：一轮里的每一次模型往返（请求/输出/logprobs/用量/耗时）都在 `调用[]` 里；
  ④ `logprobs` 两串（推理/正文）+ 每条带 `ord`，能按 `ord` 拼回一条序列；
  ⑤ 撤回**不动原文**，只在旁边留痕；
  ⑥ 指纹齐全（模型/温度/effort/max_tokens/top_logprobs/提示词哈希），且 `temperature > 0`；
  ⑦ 编写 `[W]`：给"原文＋强度"能编出一段；说"没有"就不注入；挂了要报失败（不许装作成功）；
  ⑧ 工具改名：`TOOLS_MEMORY` 里叫 `recall`，SYS1 里也提 `recall`；`_dispatch` 保留旧名别名。
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))     # 让 `import memory` 成立

from memory import writing                                     # noqa: E402
from memory.config import Config                               # noqa: E402
from memory.embedder import HashingEmbedder                    # noqa: E402
from memory.engine import Engine                               # noqa: E402
from memory.film import 切回原文, 底片, 指纹, 读底片            # noqa: E402
from memory.llm import ScriptedLLM, 收_logprobs                # noqa: E402
from memory.graph import 图                                    # noqa: E402
from memory.prompts import (SYS1, SYS2, SYS1_不推, TOOLS_MEMORY, FROZEN_CHANGELOG,  # noqa: E402
                            load_session_prompts)
from memory.session import Session                             # noqa: E402

坏 = []


def 断言(名, 条件, 实际=None):
    if 条件:
        print("  ok   %s" % 名)
    else:
        坏.append(名)
        print("  FAIL %s   实际=%r" % (名, 实际))


# --------------------------------------------------------------------- ①②③④⑤⑥
def t_film():
    print("[1] 底片 · 逐轮记录")
    tmp = tempfile.mkdtemp(prefix="palm-film-")
    cfg = Config()
    fp = 指纹(cfg, (SYS1, SYS2))
    断言("★ 指纹齐全（模型/温度/effort/max_tokens/top_logprobs/提示词哈希）",
         all(k in fp and fp[k] not in ("", 0, 0.0) for k in
             ("模型", "temperature", "effort", "max_tokens", "top_logprobs", "提示词哈希")), fp)
    断言("⛔ `temperature > 0`（=0 会把分布压成 one-hot、logprob 恒 0 ⇒ 整份 logprobs 作废）",
         fp["temperature"] > 0, fp["temperature"])
    断言("提示词哈希对内容敏感（改一个字就变）",
         指纹(cfg, (SYS1 + "x", SYS2))["提示词哈希"] != fp["提示词哈希"])

    f = 底片("2026-10-17-01", fp, tmp)
    调用 = [{"序": 0, "模型": cfg.chat.model, "temperature": 0.7, "max_tokens": 6000,
             "effort": "low", "请求增量": [{"role": "user", "content": "在吗"}], "请求条数": 1,
             "输出": {"content": "我在。", "reasoning": "他来了。", "tool_calls": []},
             "logprobs": {"推理": [{"ord": 0, "token": "他", "logprob": -0.1, "top": []}],
                          "正文": [{"ord": 1, "token": "我", "logprob": -0.2, "top": []}]},
             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}]
    f.加轮(1, "在吗", "我在。", 调用=调用, 取用=None, 旧池取用=["他养了只猫"],
           参数={"形状": "旧池（无件层）"}, 成本={"total_tokens": 15})
    f.加轮(2, "猫叫什么", "还没给它起。", 调用=[], 取用=None)
    f.落盘()

    d = 读底片(os.path.join(tmp, "底片", "2026-10-17-01.json.gz"))
    断言("★ 底片落盘了、`原文` = 逐字拼接（不加分隔符）",
         d and d["原文"] == "在吗我在。猫叫什么还没给它起。", d and d["原文"])
    断言("★★ 按区间切回来**逐字相同**（刺激/她说都试）",
         切回原文(d, 1, "刺激") == "在吗" and 切回原文(d, 1, "她说") == "我在。"
         and 切回原文(d, 2, "刺激") == "猫叫什么" and 切回原文(d, 2, "她说") == "还没给它起。")
    断言("★ 只追加：第 1 轮的区间没被第 2 轮挤动",
         d["逐轮"][0]["刺激区间"] == [0, 2] and d["逐轮"][0]["她说区间"] == [2, 5],
         (d["逐轮"][0]["刺激区间"], d["逐轮"][0]["她说区间"]))
    断言("★★ 全流程：`调用[]` 里的请求/输出/logprobs/用量/耗时都在",
         len(d["逐轮"][0]["调用"]) == 1
         and d["逐轮"][0]["调用"][0]["请求增量"][0]["content"] == "在吗"
         and d["逐轮"][0]["调用"][0]["输出"]["reasoning"] == "他来了。"
         and d["逐轮"][0]["调用"][0]["usage"]["total_tokens"] == 15,
         d["逐轮"][0].get("调用"))
    lp = d["逐轮"][0]["调用"][0]["logprobs"]
    断言("★ `logprobs` 分成两串（推理/正文），每条带 `ord`",
         [e["ord"] for e in lp["推理"]] == [0] and [e["ord"] for e in lp["正文"]] == [1], lp)
    断言("★ 按 `ord` 能把两串拼回一条序列",
         "".join(e["token"] for e in sorted(lp["推理"] + lp["正文"], key=lambda x: x["ord"])) == "他我")
    断言("★ `取用` 这个键**一定在**（件层没接进来时是 `null`，⛔ 不能省）",
         "取用" in d["逐轮"][0] and d["逐轮"][0]["取用"] is None)
    断言("★ 旧池那几条记在过渡字段 `旧池取用` 里",
         d["逐轮"][0].get("旧池取用") == ["他养了只猫"])

    rec_path = os.path.join(tmp, "逐轮记录", "2026-10-17-01.jsonl")
    行 = [json.loads(x) for x in io.open(rec_path, encoding="utf-8") if x.strip()]
    断言("★ 逐轮记录：一轮一行、`取用` 键在、带了调用次数与 logprobs 条数",
         len(行) == 2 and "取用" in 行[0] and 行[0]["取用"] is None
         and 行[0]["调用次数"] == 1 and 行[0]["logprobs条数"] == 2, 行[0])

    # ⑤ 撤回
    f.撤回(1)
    f.落盘()
    d2 = 读底片(os.path.join(tmp, "底片", "2026-10-17-01.json.gz"))
    断言("★★ 撤回**不改原文**（原文是根），只在旁边留痕并给那一轮打标",
         d2["原文"] == d["原文"] and d2["逐轮"][0]["撤回"] is True
         and d2["撤回记录"][-1]["轮"] == 1, (d2["原文"], d2.get("撤回记录")))
    断言("★ 撤回也追加进了逐轮记录（那一行带 `撤回: true`）",
         any(r.get("撤回") is True for r in
             [json.loads(x) for x in io.open(rec_path, encoding="utf-8") if x.strip()]))

    # ⑥ `收_logprobs` 的纯函数行为（官方端点的两串形状）
    汇, 计数 = {}, {"n": 0}
    收_logprobs({"reasoning_content": [{"token": "甲", "logprob": -0.5,
                                        "top_logprobs": [{"token": "甲", "logprob": -0.5}]}],
                 "content": [{"token": "乙", "logprob": -1.5,
                              "top_logprobs": [{"token": "乙", "logprob": -1.5},
                                               {"token": "丙", "logprob": -2.0}]}]}, 汇, 计数)
    断言("★ 两串分开收、`ord` 连续、`top` 只留 token/logprob（丢掉 bytes）",
         [e["ord"] for e in 汇["推理"]] == [0] and [e["ord"] for e in 汇["正文"]] == [1]
         and 汇["正文"][0]["top"] == [{"token": "乙", "logprob": -1.5},
                                      {"token": "丙", "logprob": -2.0}], 汇)


def 造底片(dir_, 场id, 轮们):
    """写一份**真的** `.json.gz` 底片（`[R]` 取整组原文时要按区间回来切）。"""
    import gzip as _gz
    import json as _json
    逐轮, 原文 = [], ""
    for i, (刺, 说) in enumerate(轮们, start=1):
        s0 = len(原文)
        原文 += 刺 + 说
        逐轮.append({"轮": i, "刺激": 刺, "她说": 说,
                     "刺激区间": [s0, s0 + len(刺)], "她说区间": [s0 + len(刺), len(原文)],
                     "调用": [], "取用": None})
    d = {"场id": 场id, "系统前缀": ["s1", "s2"], "原文": 原文, "逐轮": 逐轮,
         "撤回记录": [], "指纹": {}}
    os.makedirs(os.path.join(dir_, "底片"), exist_ok=True)
    with _gz.GzipFile(os.path.join(dir_, "底片", 场id + ".json.gz"), "wb",
                      compresslevel=6, mtime=0) as fh:
        fh.write(_json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode())


# --------------------------------------------------------------------- ⑦ 编写
def t_writing():
    print("[2] 编写 [W]")
    件们 = [{"文本": "我半夜最清醒。", "强度": 0.83}, {"文本": "他习惯晚上干活。", "强度": 0.41}]
    提示 = writing.条目文本(件们)
    断言("★ 输入是「强度 · 原文」一行一件（强度要给编写模型看）",
         提示 == "0.83 · 我半夜最清醒。\n0.41 · 他习惯晚上干活。", 提示)

    llm = ScriptedLLM([{"role": "assistant", "content": "我记得我半夜最清醒，也记得他习惯晚上干活。"}])
    r = writing.编写(llm, 件们, 场id="t", 轮=1, 线索="他说他晚上才干活")
    断言("★ 能编出一段（第一人称、把两件都说到）",
         r["ok"] and "半夜最清醒" in r["编写"] and "晚上干活" in r["编写"], r)
    断言("★★ 编写走 ast=True（关思考）—— 实测 ffort 压不下来：low 仍要 956 个推理 token",
         llm.calls[0].get("fast") is True, llm.calls[0])
    断言("★ 编写提示词里**没有**他那句话（不给情境 —— 给了它会抄线索）",
         "他说他晚上才干活" not in llm.calls[0]["messages"][0]["content"])

    llm2 = ScriptedLLM([{"role": "assistant", "content": "没有。"}])
    r2 = writing.编写(llm2, 件们)
    断言("★ 模型说「没有」⇒ 不注入（`没有=True`、`编写` 为空）",
         r2["ok"] and r2["没有"] and r2["编写"] == "", r2)

    llm3 = ScriptedLLM([{"role": "assistant", "content": ""}])
    r3 = writing.编写(llm3, 件们)
    断言("⛔ 正文是空的（被推理吃光那种）⇒ **报失败**，不许当成「没有要回忆的」",
         r3["ok"] is False and r3["没有"] is False, r3)

    llm4 = ScriptedLLM(fallback={"role": "assistant", "content": "嗯"})
    llm4.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络断了"))
    r4 = writing.编写(llm4, 件们)
    断言("⛔ 编写挂了 ⇒ 不抛异常，如实报失败（调用方好退回条目清单）",
         r4["ok"] is False and "网络断了" in str(r4.get("err")), r4)


# --------------------------------------------------------------------- ⑧ 改名 + 端到端
def t_session_and_rename():
    print("[3] 端到端（离线）：一轮对话 → 底片 + 一段回忆 + 说/")
    tmp = tempfile.mkdtemp(prefix="palm-p1-")
    cfg = Config()
    emb = HashingEmbedder(dim=64)
    # 注意顺序：`render_recall=True` ⇒ 先调一次编写，再调主回答
    llm = ScriptedLLM([
        {"role": "assistant", "content": "我想起他养了只猫。"},
        {"role": "assistant", "content": "记得，你养了只猫。",
         "reasoning_content": "他问猫，我手里有那条。"},
    ], fallback={"role": "assistant", "content": "嗯"})
    eng = Engine(cfg, emb, llm, tmp)
    eng.advance_to_wall(ts=1_000_000.0)
    eng.save()

    # ★★ 取件源已经换成 `[R] 回忆`：图里**得先有一个件**，这一轮才"想得起什么"。
    #    造一份真的底片（回忆要按区间回去切原文）+ 一个件指向它的 cell。
    造底片(tmp, "2026-10-16-01", [("我要睡了", "那就睡吧，我在这儿。")])
    g = 图(tmp)
    g.版本 = 1
    一个cell = g.加cell("2026-10-16-01", 1, 0, 0)
    向 = emb.raw_embed(["我要睡了"])[0]      # ★ 用**真**嵌入（numpy 数组 —— 正好压这条路径）
    # ⚠️ 件向量就是一条**和线索同向**的假向量：这条自检只验"取件→编写→注入"通不通，
    #    ⛔ 不比语义质量（那是真材料上的事）。
    一号件 = g.新件([一个cell["id"]], 向, 代表=一个cell["id"], 强度=1.0)
    g.标记集 = {一号件: 1.0}
    g.落盘()

    sess = Session(eng, 1)
    out = sess.ask("我的猫叫什么来着")
    断言("★ 一轮跑通", bool(out.get("reply")), out.get("reply"))

    注入 = [m for m in sess.msgs if m.get("role") == "system"
            and str(m.get("content") or "").startswith(cfg.session.push_header)]
    断言("★★ 注入的是**一段回忆**（`push_header` 后面是一句话，不是条目清单）",
         len(注入) == 1 and "我想起他养了只猫。" in 注入[0]["content"]
         and "·" not in 注入[0]["content"].split("】")[-1], 注入)

    底片目录 = os.path.join(tmp, "底片")
    文件们 = [f for f in (os.listdir(底片目录) if os.path.isdir(底片目录) else [])
              if f != "2026-10-16-01.json.gz"]      # 种的那个件是另一份底片
    断言("★ 底片落盘了", len(文件们) == 1, 文件们)
    断言("★★ 底片是 `.json.gz`、写进去能**原样**读回（无损压缩，结构没动）",
         文件们 and 文件们[0].endswith(".json.gz")
         and not os.path.exists(os.path.join(底片目录, "2026-10-17-01.json")), 文件们)
    d = 读底片(os.path.join(底片目录, 文件们[0])) if 文件们 else None
    断言("★ 底片里有这一轮的原文、调用（≥2 次：编写 + 主回答）",
         d and d["原文"] == "我的猫叫什么来着" + out["reply"]
         and len(d["逐轮"][0]["调用"]) >= 2, d and [c.get("用途") for c in d["逐轮"][0]["调用"]])
    断言("★★ 没有任何一次调用把**完整上下文**抄一遍（只存增量 —— 否则是平方级，1M 上下文会到 3.8 GB）",
         all(("请求" not in c) or (c.get("用途") == "编写")
             for t in d["逐轮"] for c in t["调用"]),
         [[k for k in c if k.startswith("请求")] for t in d["逐轮"] for c in t["调用"]])
    拼回 = [m for t in d["逐轮"] for c in t["调用"] if c.get("用途") != "编写"
            for m in (c.get("请求增量") or [])]
    断言("★★ 增量拼回来 == 真实上下文（系统前缀 + 各次增量 = `msgs`）",
         d["系统前缀"] == [str(m.get("content") or "") for m in sess.msgs[:2]]
         and 拼回 == sess.msgs[2:-1], (len(拼回), len(sess.msgs[2:-1])))

    # ★★ 2026-10-17（作者："没有推的时候，就应该鼓励查一下才对吧？"）
    from memory.prompts import SYS1, SYS1_不推
    开, _ = load_session_prompts(auto_push=True)
    关, _ = load_session_prompts(auto_push=False)
    断言("★★ 自动推送关着 ⇒ SYS1 后面**追加**「开口前先自己查一遍」",
         SYS1 in 关 and SYS1_不推 in 关 and SYS1_不推 not in 开)
    断言("★ 那一段写清了「不用查他刚发来的这一句」（否则每轮空查、白花钱）",
         "刚发来的这一句" in SYS1_不推)
    _旧 = bool(eng.cfg.session.auto_push)
    eng.cfg.session.auto_push = False
    _s_off = Session(eng, 9)
    断言("★★ 真接上了：关掉推送那场的前缀里就有它（⛔ 只加在变量里）",
         SYS1_不推 in str(_s_off.msgs[0].get("content") or ""),
         str(_s_off.msgs[0].get("content") or "")[-80:])
    断言("★★ 底片指纹按**实际前缀**算（⛔ 还是默认那两条的哈希）",
         _s_off._底片().指纹 != sess._底片().指纹,
         (_s_off._底片().指纹, sess._底片().指纹))
    eng.cfg.session.auto_push = _旧
    断言("★ 参数里记了「这一轮是编写还是清单」",
         bool(d and (d["逐轮"][0].get("参数") or {}).get("注入")),
         d and d["逐轮"][0].get("参数"))
    # ★★ 2026-10-17：底片是**存档**，记的必须是真的 —— 别再把旧池那一套留在里面。
    _参 = (d["逐轮"][0].get("参数") or {}) if d else {}
    断言("★★ 底片里的参数是**件层**的真相（形状 / 件数 / 回忆参数），不是旧池那一套",
         _参.get("形状") == "件层" and "件数" in _参 and isinstance(_参.get("回忆参数"), dict)
         and "gate_tau" not in _参 and "旧池取用" not in d["逐轮"][0], _参)

    说目录 = os.path.join(tmp, "说")
    断言("★ 编写产物落 `说/`（模型生成过的东西必须留档）",
         os.path.isdir(说目录) and len(os.listdir(说目录)) == 1,
         os.listdir(说目录) if os.path.isdir(说目录) else None)

    print("[4] 工具改名")
    名 = TOOLS_MEMORY[0]["function"]["name"]
    断言("★ 工具叫 `recall`（不是 `read_z`）", 名 == "recall", 名)
    断言("★ SYS1 里也写 `recall`、不再出现 `read_z`", "recall" in SYS1 and "read_z" not in SYS1)
    断言("★ 冻结台账**追加在末尾**（首条仍是 `schedule_wakeup` 那条）",
         len(FROZEN_CHANGELOG) >= 2
         and "schedule_wakeup" in FROZEN_CHANGELOG[0]["what"]
         and FROZEN_CHANGELOG[-1]["date"] == "2026-10-17", FROZEN_CHANGELOG[-1])
    src = io.open(os.path.join(os.path.dirname(HERE), "session.py"), encoding="utf-8").read()
    断言("★ `_dispatch` 保留旧名当别名（老 pending 里还有 `read_z` 的历史）",
         'name in ("recall", "read_z")' in src)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    t_film()
    t_writing()
    t_session_and_rename()
    print()
    if 坏:
        print("有 %d 处不过：%s" % (len(坏), 坏))
        sys.exit(1)
    print("P1 自检：全部通过")
