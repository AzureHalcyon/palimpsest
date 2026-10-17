# -*- coding: utf-8 -*-
"""搬迁冒烟（P0 的守门人）：搬过来之后**还是同一台机器**、而且新线能跑起来。

跑：`python memory/tests/smoke.py`

★ 2026-10-17 改：旧池子（`pool`/`reader`/`dreaming`/`merging`）已经删掉，
  这一份不再验"旧归档把事实写进池子"，改成验**新线**：一轮聊得通、底片落盘、
  归档出草案、按「完成」写图。细节在 `p1/p2/p3`，这里只守最基本的一条路。
"""
import os
import sys
import tempfile

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
    from memory import archive_step, film, graph, markset, recall, writing   # noqa: F401
    from memory.config import Config
    from memory.embedder import HashingEmbedder
    from memory.engine import Engine
    from memory.llm import ScriptedLLM
    from memory.session import Session

    print("[1] 模块在不在")
    断言("★ 新线的模块都能 import",
         all(x is not None for x in (film, graph, markset, recall, writing, archive_step)))
    断言("★ 旧线的模块一个都不在了（reader / dreaming / merging / pool / 空间图 / 探针）",
         not any(os.path.exists(os.path.join(os.path.dirname(HERE), f))
                 for f in ("reader.py", "dreaming.py", "merging.py", "pool.py",
                           "layout.py", "axes.py", "precision.py")))
    # ★★ 2026-10-17：配置也剪了 —— 只剩还在起作用的四节。
    #   ⛔ 这条钉的是"删干净"：留着 `reader` / `dream` / `merge` / `salience`，
    #   会让 `Config` 看起来还有那些功能，别人照着它写代码就会写进空气里。
    断言("★★ `Config` 只剩四节（embed / chat / session / cooldown）",
         sorted(Config().__dataclass_fields__) == ["chat", "cooldown", "embed", "session"],
         sorted(Config().__dataclass_fields__))

    print("[2] 一台机器 + 一场对话")
    tmp = tempfile.mkdtemp(prefix="palm-smoke-")
    cfg = Config()
    emb = HashingEmbedder(dim=64)
    llm = ScriptedLLM([{"role": "assistant", "content": "嗯，我在。"}] * 8,
                      fallback={"role": "assistant", "content": "嗯"})
    # ★ 引擎**不再需要池子**：空目录就能开场（这正是新引擎与旧引擎最大的不同）。
    eng = Engine(cfg, emb, llm, tmp)
    eng.advance_to_wall(ts=1_000_000.0)
    eng.save()
    sess = Session(eng, 1)
    out = sess.ask("在吗")
    断言("★ 一轮聊得通", bool(out.get("reply")), out.get("reply"))
    断言("★ 底片落盘了（`.json.gz`）",
         os.path.isdir(os.path.join(tmp, "底片"))
         and len(os.listdir(os.path.join(tmp, "底片"))) == 1,
         os.listdir(os.path.join(tmp, "底片")) if os.path.isdir(os.path.join(tmp, "底片")) else None)

    print("[3] 归档：草案 → 完成 → 图落盘")
    草 = sess.archive_draft()
    断言("★ 草案是新形状（`草` + 摘要）",
         草.get("land") == "yes" and isinstance(草.get("草"), dict)
         and bool(str(草["草"].get("摘要") or "")), 草.get("land"))
    sess.archive_commit()
    断言("★ 图落盘了（`图.json.gz`）", os.path.exists(os.path.join(tmp, "图.json.gz")))
    断言("★ 场次 +1、pending 清掉", eng.sessions_done == 1 and not eng.pending.exists(),
         (eng.sessions_done, eng.pending.exists()))

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
