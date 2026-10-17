# -*- coding: utf-8 -*-
"""图（**缓存**，可从底片重建）：cell · 件 · 标记集 · 待观察池。

═══════════════════════════════════════════════════════════════════════
作者口径 / 文档依据
═══════════════════════════════════════════════════════════════════════
- 「图是缓存」：能整套从底片重跑出来 ⇒ 换算法就**重算**、版本 +1，⛔ 不做"迁移旧库"（`流程框架 §3.1.4`）。
- cell 的粒度是**轮**，不是场：「一场 = 第 1..N 轮的 cell」（`流程框架 §3.1` 那处纠正）。
- cell 的身份是**原文区间**（不是分段编号、不是哈希）—— 所以它永远指得回底片的原文。
- 组：`件` 就是"一组 cell"（依赖簇的落点）；件不带类型。
- 标记集 = `{件id: confidence}` —— 当前"活着的件"；掉出标记集的进**待观察池**（不删）。

落盘：`<cache>/图.json.gz`（`mtime=0`，同内容同字节）。一期就把整张图放一个文件 ——
场数上去再说分片；能重建的东西不必现在优化。

⛔ 边界：这个模块**只动图**，不碰底片（底片是存档、只追加）。谁要写底片请去 `film.py`。
"""
import gzip
import io
import json
import os
import sys
import time
from typing import Dict, List, Optional, Sequence


class 图:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.版本 = 0
        self.cell: List[Dict] = []          # {id, 场id, 轮, 起, 止}
        self.件: Dict[str, Dict] = {}       # {id: {id, 成员[], 点亮[], 代表, 强度, 表示[]}}
        self.标记集: Dict[str, float] = {}  # {件id: confidence}
        self.待观察池: List[Dict] = []      # [{单元, 为什么进来, 批号}]
        # ★★★ 2026-10-17（作者定）：**存「边」，⛔ 存「块」**。
        #   边 = {a: 件id, b: 件id, 关系: 自由文本, 说法} —— pairwise，原子。
        #   ★ 为什么：块是"某关系名下的连通块" ⇒ **它是视图**。存块会有一串无解的问题：
        #     名字漂（同一组件两个名字）· 块数抖 · 要不要去重 ——
        #     而"合并也行不合并也行"恰好说明**它本来就不是存储该有的东西**。
        #   ★★ 而这也正是作者最初那句：「一个节点-组可以归在**不同的边**里」。
        self.边: List[Dict] = []            # [{a, b, 关系, 说法}]
        self._下一个件 = 1
        self.落盘错误 = ""   # 上一次落盘失败的原因（空 = 成功）
        self.载入错误 = ""   # 上一次载入坏档的原因（空 = 正常）

    # ------------------------------------------------------------------ 路径
    def _路径(self) -> str:
        return os.path.join(self.cache_dir, "图.json.gz")

    # ------------------------------------------------------------------ 存 / 取
    def 落盘(self) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            d = {"版本": self.版本, "cell": self.cell, "件": self.件,
                 "标记集": self.标记集, "待观察池": self.待观察池,
                 "边": self.边, "_下一个件": self._下一个件}
            raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            with gzip.GzipFile(self._路径(), "wb", compresslevel=6, mtime=0) as fh:
                fh.write(raw)
        except Exception as e:                   # noqa: BLE001
            # ★★★ 2026-10-17（审查抓到）：原来这里 `pass` —— 而这正是**整场记忆的落盘**。
            #   磁盘满/权限/序列化失败 ⇒ **图根本没写**，可 `完成()` 照常返回、
            #   `archive_land` 写转写、场次 +1、`applied_sids` 记住、pending 清掉
            #   ⇒ **补做都不会补**，记忆静默丢失。
            #   "不许打断归档"⛔ "假装成功"：把失败**记在图对象上**，让调用方决定。
            self.落盘错误 = "%s: %s" % (type(e).__name__, e)
            sys.stderr.write("[图.落盘] 失败：%s\n" % self.落盘错误)

    @classmethod
    def 载入(cls, cache_dir: str) -> "图":
        g = cls(cache_dir)
        p = g._路径()
        if not os.path.exists(p):
            return g
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            g.版本 = int(d.get("版本") or 0)
            g.cell = list(d.get("cell") or [])
            g.件 = dict(d.get("件") or {})
            g.标记集 = {k: float(v) for k, v in (d.get("标记集") or {}).items()}
            g.待观察池 = list(d.get("待观察池") or [])
            g.边 = list(d.get("边") or [])
            # ★ 兼容旧图（那里存的是"块"）：把块的成员两两转成边，⛔ 丢东西
            for _bid, _it in (d.get("块") or {}).items():
                _员 = [str(x) for x in (_it.get("成员") or [])]
                for _i in range(len(_员)):
                    for _j in range(_i + 1, len(_员)):
                        g.加边(_员[_i], _员[_j], str(_it.get("关系") or "未命名"))
            g._下一个件 = int(d.get("_下一个件") or (len(g.件) + 1))
        except Exception as e:                   # noqa: BLE001
            # ★★ 2026-10-17（审查抓到）：坏图**当空图且不报** ⇒ 她"什么都不记得了"，
            #   而用户找不到任何原因；注释说"能从底片重建"，可**全仓没有重建入口**。
            #   ⇒ 坏档改名留档 + 写 stderr（还是当空图：缓存可重建，但得让人知道）。
            #   ★ 还必须**整个重来**：上面是边读边写 `g` 的，读到一半炸 ⇒ `g` 里是
            #     "半份图"（比如 `件` 有、`标记集` 没有）—— 那比空图更坏，照样不报错。
            _错 = "%s: %s" % (type(e).__name__, e)
            sys.stderr.write("[图.载入] 坏档，当空图处理：%s（%s）\n" % (p, _错))
            try:
                os.replace(p, p + ".坏-" + str(int(time.time())))
            except Exception:                # noqa: BLE001
                pass
            g.__init__(cache_dir)            # 回到空图
            g.载入错误 = _错
        return g

    # ------------------------------------------------------------------ cell
    def 加cell(self, 场id: str, 轮: int, 起: int, 止: int) -> Dict:
        """cell 的身份 = 原文区间。同一 (场,轮) 重复加 ⇒ 返回已有的那条（幂等）。"""
        for c in self.cell:
            if c["场id"] == 场id and int(c["轮"]) == int(轮):
                return c
        c = {"id": "cell-%04d" % (len(self.cell) + 1), "场id": str(场id),
             "轮": int(轮), "起": int(起), "止": int(止)}
        self.cell.append(c)
        return c

    def 按区间找cell(self, 场id: str, 轮: int) -> Optional[Dict]:
        for c in self.cell:
            if c["场id"] == 场id and int(c["轮"]) == int(轮):
                return c
        return None

    # ------------------------------------------------------------------ 件
    def 新件(self, 成员: Sequence[str], 表示: Sequence[float],
             代表: Optional[str] = None, 强度: Optional[float] = None,
             来源: str = "意外") -> str:
        from .knobs import 件 as K件
        件id = "件-%04d" % self._下一个件
        self._下一个件 += 1
        成员 = [str(m) for m in 成员]
        self.件[件id] = {"id": 件id, "成员": 成员, "点亮": list(成员),
                         "代表": 代表 or (成员[0] if 成员 else ""),
                         "强度": float(强度 if 强度 is not None else K件["初始置信度"]["值"]),
                         # ★★ 2026-10-17：件的**来源**决定衰减档（手工 ⇒ 更慢 + 地板 0.5 ⇒ 永不剪出）。
                         "来源": str(来源 or "意外"),
                         # ⛔ 不能写 `表示 or []`：向量可能是 numpy 数组，真值判定会抛
                         #    `ValueError: truth value of an array is ambiguous`（自检抓到过）
                         "表示": [float(x) for x in (表示 if 表示 is not None else [])]}
        return 件id

    def 归进(self, 件id: str, cell_id: str, 表示: Optional[Sequence[float]] = None,
             来源: str = "意外") -> None:
        """把一个 cell 并进已有的件（成员追加；`点亮` 也记上 —— 那是**这次**被注意到的）。

        ★★ 2026-10-17（作者口径）：「**合并的话，那么衰减速度也会变**」——
          一件本来普通的件，被并进一条**手工标记**的格子 ⇒ 它跟着变牢。
        ⚠️ **单向**：只会 意外 ⇒ 手工，**⛔ 手工 ⇒ 意外**（否则"记牢"会被后来一次意外冲掉）。
        """
        it = self.件.get(件id)
        if it is None:
            return
        if cell_id not in it["成员"]:
            it["成员"].append(cell_id)
        if cell_id not in it["点亮"]:
            it["点亮"].append(cell_id)
        if 表示 is not None and not it.get("表示"):
            it["表示"] = [float(x) for x in 表示]
        if str(来源 or "意外") == "手工":
            it["来源"] = "手工"

    def 件来源表(self) -> Dict[str, str]:
        """`{件id: "意外"|"手工"}` —— 衰减按它分档。"""
        return {i: str(v.get("来源") or "意外") for i, v in self.件.items()}

    def 件向量表(self) -> Dict[str, List[float]]:
        return {i: list(v.get("表示") or []) for i, v in self.件.items() if v.get("表示")}

    # ------------------------------------------------------------------ 边（多归属）
    # ★★★ 2026-10-17（作者定）：**存「边」，⛔ 存「块」**。
    #   边 = {a, b, 关系, 说法} —— pairwise，原子。块是**派生视图**（同关系名下的连通块）。
    #   ★ 于是"同一对件起了两个名字"不是冲突，是**两条不同关系的边**。

    def 加边(self, a: str, b: str, 关系: str, 说法: str = "") -> bool:
        """a—b 之间挂一条「关系」的边。同 (a, b, 关系) 只留一条。返回是否新加。"""
        a, b = str(a), str(b)
        if not a or not b or a == b:
            return False
        关系 = str(关系 or "").strip() or "未命名"
        for e in self.边:
            if {e.get("a"), e.get("b")} == {a, b} and str(e.get("关系")) == 关系:
                if 说法 and not e.get("说法"):
                    e["说法"] = str(说法)
                return False
        self.边.append({"a": a, "b": b, "关系": 关系, "说法": str(说法 or "")})
        return True

    def 件的边(self, 件id: str, 关系=None):
        """这个件挂着哪些边（给了关系名 ⇒ 只那个名字的）。"""
        件id = str(件id)
        return [e for e in self.边
                if (件id == str(e.get("a")) or 件id == str(e.get("b")))
                and (关系 is None or str(e.get("关系")) == str(关系))]

    def 关系名们(self):
        return sorted({str(e.get("关系")) for e in self.边 if e.get("关系")})

    def 块(self):
        """**派生视图**：每个关系名下的**连通块**（并查集）。⛔ 存，随时算。

        返回 `{块id: {id, 关系, 成员[件id]}}`。单点不成块（它已经有"件"了）。
        """
        出 = {}
        for 关系 in self.关系名们():
            父 = {}

            def 找(x):
                r = x
                while 父.get(r, r) != r:
                    r = 父.get(r, r)
                return r

            for e in self.边:
                if str(e.get("关系")) != 关系:
                    continue
                a, b = str(e.get("a")), str(e.get("b"))
                父.setdefault(a, a)
                父.setdefault(b, b)
                ra, rb = 找(a), 找(b)
                if ra != rb:
                    父[rb] = ra
            组 = {}
            for x in 父:
                组.setdefault(找(x), []).append(x)
            for n, (根, 员) in enumerate(sorted(组.items()), 1):
                if len(员) < 2:
                    continue
                出["块-%s-%02d" % (关系[:6], n)] = {
                    "id": "块-%s-%02d" % (关系[:6], n), "关系": 关系,
                    "成员": sorted(员)}
        return 出

    # ------------------------------------------------------------------ 标记集
    def 剪出(self, 件id: str, 为什么: str = "衰减到门槛以下") -> None:
        """剪出标记集 ⇒ 进待观察池（⛔ 不删件，它还在图里）。"""
        c = self.标记集.pop(件id, None)
        if c is None:
            return
        self.待观察池.append({"单元": 件id, "为什么进来": 为什么, "批号": self.版本})

    # ------------------------------------------------------------------ 视图
    def 摘要(self) -> Dict:
        return {"版本": self.版本, "cell": len(self.cell), "件": len(self.件),
                "标记集": len(self.标记集), "待观察池": len(self.待观察池)}
