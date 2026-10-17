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
from typing import Dict, List, Optional, Sequence


class 图:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.版本 = 0
        self.cell: List[Dict] = []          # {id, 场id, 轮, 起, 止}
        self.件: Dict[str, Dict] = {}       # {id: {id, 成员[], 点亮[], 代表, 强度, 表示[]}}
        self.标记集: Dict[str, float] = {}  # {件id: confidence}
        self.待观察池: List[Dict] = []      # [{单元, 为什么进来, 批号}]
        self._下一个件 = 1

    # ------------------------------------------------------------------ 路径
    def _路径(self) -> str:
        return os.path.join(self.cache_dir, "图.json.gz")

    # ------------------------------------------------------------------ 存 / 取
    def 落盘(self) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            d = {"版本": self.版本, "cell": self.cell, "件": self.件,
                 "标记集": self.标记集, "待观察池": self.待观察池,
                 "_下一个件": self._下一个件}
            raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            with gzip.GzipFile(self._路径(), "wb", compresslevel=6, mtime=0) as fh:
                fh.write(raw)
        except Exception:                        # noqa: BLE001
            pass          # ⛔ 记图失败不许打断归档

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
            g._下一个件 = int(d.get("_下一个件") or (len(g.件) + 1))
        except Exception:                        # noqa: BLE001
            pass          # 坏图当空图：它是缓存，能从底片重建
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
             代表: Optional[str] = None, 强度: Optional[float] = None) -> str:
        from .knobs import 件 as K件
        件id = "件-%04d" % self._下一个件
        self._下一个件 += 1
        成员 = [str(m) for m in 成员]
        self.件[件id] = {"id": 件id, "成员": 成员, "点亮": list(成员),
                         "代表": 代表 or (成员[0] if 成员 else ""),
                         "强度": float(强度 if 强度 is not None else K件["初始置信度"]["值"]),
                         # ⛔ 不能写 `表示 or []`：向量可能是 numpy 数组，真值判定会抛
                         #    `ValueError: truth value of an array is ambiguous`（自检抓到过）
                         "表示": [float(x) for x in (表示 if 表示 is not None else [])]}
        return 件id

    def 归进(self, 件id: str, cell_id: str, 表示: Optional[Sequence[float]] = None) -> None:
        """把一个 cell 并进已有的件（成员追加；`点亮` 也记上 —— 那是**这次**被注意到的）。"""
        it = self.件.get(件id)
        if it is None:
            return
        if cell_id not in it["成员"]:
            it["成员"].append(cell_id)
        if cell_id not in it["点亮"]:
            it["点亮"].append(cell_id)
        if 表示 is not None and not it.get("表示"):
            it["表示"] = [float(x) for x in 表示]

    def 件向量表(self) -> Dict[str, List[float]]:
        return {i: list(v.get("表示") or []) for i, v in self.件.items() if v.get("表示")}

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
