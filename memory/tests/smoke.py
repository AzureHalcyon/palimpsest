# -*- coding: utf-8 -*-
"""搬迁冒烟：**只证明"搬过来还是同一台机器"，不证明效果好不好。**

为什么单开这一份（⛔ 不搬旧仓那 539 条断言）：`分支声明 §4` 说过 ——
旧断言里相当一部分守的是**旧形状**（比如逐字钉住 `DREAM_SYS` 里"条目要独立包含完整意思"那句），
而新方向（锚回原文）正要推翻它。把旧断言混进来，最后会变成"为了让断言过而改设计"。
⇒ 新仓要自己的自检；这一份是**搬迁期的最低保证**，`P2` 换上 ①–⑤ 之后它会自然变薄。

跑：`python memory/tests/smoke.py`
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # 让 `import memory` 成立

坏 = []


def 断言(名, 条件, 实际=None):
    if 条件:
        print("  ok   %s" % 名)
    else:
        坏.append(名)
        print("  FAIL %s   实际=%r" % (名, 实际))


def main():
    print("[1] 包与模块")
    import memory
    断言("`import memory` 成立（包名已从 companion 改掉）", hasattr(memory, "__file__"), memory)
    from memory import (activity, budgets, channels, config, deliver, embedder, engine,  # noqa: F401
                        layout, llm, pool, prompts, reader, session, settings, transcript)

    print("[2] 冻结提示词")
    from memory.prompts import FROZEN_CHANGELOG, SYS1, SYS2
    断言("SYS1 / SYS2 都在且非空", bool(SYS1.strip()) and bool(SYS2.strip()))
    断言("冻结记录还在（改提示词必须追加一条）", isinstance(FROZEN_CHANGELOG, list)
         and len(FROZEN_CHANGELOG) >= 1, FROZEN_CHANGELOG)

    print("[3] 一次离线对话（脚本替身，无网络）")
    import tempfile
    from memory.config import Config
    from memory.embedder import HashingEmbedder
    from memory.engine import Engine
    from memory.llm import ScriptedLLM
    from memory.pool import TempPool

    tmp = tempfile.mkdtemp(prefix="palm-smoke-")
    cfg = Config()
    emb = HashingEmbedder(dim=64)
    pool = Engine.build_pool(cfg, emb, ["底色一条"], ["情境一条"], ["他养了只猫"])
    llm = ScriptedLLM([{"role": "assistant", "content": "嗯，我在。"}])
    eng = Engine(cfg, emb, llm, pool, TempPool(cfg.time), tmp)
    eng.advance_to_wall(ts=1_000_000.0)
    eng.save()
    sess = session.Session(eng, 1)
    out = sess.ask("在吗")
    断言("一轮对话跑通（有回复）", bool(out.get("reply")), out.get("reply"))
    断言("这一轮记进了 `rec['turns']`", len(sess.rec.get("turns") or []) == 1)

    print("[4] 一次离线归档（脚本替身）")
    ar = ('{"facts":[{"text":"他搬到城东了","changes":false,"src":[1]}],'
          '"kept":[],"again":[],"done":[],"promote":[],"note":"ok"}')
    eng.llm = ScriptedLLM(fallback={"role": "assistant", "content": ar})
    rec = {"sid": 2, "turns": [{"user": "我搬城东了", "ai": "嗯"}], "msgs": []}
    # ⚠️ rchive() 返回的是**收场后的 rec**（不是 draft/land 那两个的返回）；
    #    要看三态得单独调 rchive_draft。这条搬迁冒烟只求"落地没落地"。
    rec2 = eng.archive(rec)
    断言("归档落定了：新事实 1 条且进了长期池",
         int((rec2 or {}).get("facts_added") or 0) == 1
         and any("城东" in t for t in eng.long_pool.texts),
         ((rec2 or {}).get("facts_added"), eng.long_pool.texts[-2:]))
    断言("落地之后场次 +1、pending 清掉",
         eng.sessions_done == 1 and not eng.pending.exists(), eng.sessions_done)

    print("[5] 宿主胶水能编译（语法级）")
    glue = os.path.join(HERE, "..", "..", "app", "app", "src", "main", "python", "host_glue.py")
    glue = os.path.normpath(glue)
    if os.path.exists(glue):
        import py_compile
        try:
            py_compile.compile(glue, doraise=True, cfile=os.path.join(tmp, "glue.pyc"))
            断言("host_glue.py 语法过、且已改成 `import memory`",
                 "from memory" in open(glue, encoding="utf-8").read())
        except Exception as e:                                # noqa: BLE001
            断言("host_glue.py 能编译", False, e)
    else:
        print("  (跳过：找不到 %s)" % glue)

    print()
    if 坏:
        print("有 %d 处不过：%s" % (len(坏), 坏))
        return 1
    print("搬迁冒烟：全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
