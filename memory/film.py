# -*- coding: utf-8 -*-
"""底片 · 逐轮记录 —— 真数据的**存档**（按 `palimpsest` 的口径，2026-10-17）。

═══════════════════════════════════════════════════════════════════════
**底片是存档：结构化存储，全流程全记录。可以多存，不能少存。**（作者口径）
═══════════════════════════════════════════════════════════════════════

## 为什么必须现在存

`palimpsest` 里有两样是**存储**（＝不可再生成）：`底片`（一场）与 `逐轮记录`（一轮）。
其中**每轮 token 级 `logprobs`** 只有官方端点在那一次调用里给，`温度/模型/提示词` 一变就变
⇒ 不存就永久丢。`参数账` 记着一条教训：`temperature = 0` 会把分布压成 one-hot、`logprob` 恒 0。

## 存什么（宁多不少）

    <cache>/底片/<场id>.json        一场一条；每轮之后整条重写（**内容只增**）
    <cache>/逐轮记录/<场id>.jsonl   一轮一行；**只追加**

一场底片：

    场id · 原文(整场逐字) · 逐轮[] · 指纹
      · 撤回记录[]                 ← 撤回**不改原文**，只在旁边记一笔

逐轮一条（**"全流程"就在这里**）：

    轮 · 刺激 · 她说 · 刺激区间 · 她说区间
    · 调用[]       ← 这一轮里的**每一次模型往返**（她可能先查一次再回答）：
                       序 · 模型 · temperature · effort · max_tokens · 请求(首条含完整上下文)
                       · 输出{content, reasoning, tool_calls} · logprobs(推理/正文两串)
                       · usage · 耗时ms
    · 取用 · 旧池取用(过渡) · 参数 · 成本 · 撤回

★ `原文` 的拼法：`Σ(刺激 + 她说)`，**不加分隔符、不加说话人标记**；谁说的、哪一段，
  全靠 `刺激区间` / `她说区间` 指回去（区间坐标是唯一验收方式：切回来必须逐字相同）。
  ⛔ 工具往返的中间正文、注入的回忆、系统提示词**都不进 `原文`**（原文只记"实际说出口的话"），
  但它们在 `调用[]` 里一个字都不少。

## `logprobs` 为什么分两串

官方 `api.deepseek.com` 把概率**分成两串给**（2026-10-17 实测）：`logprobs.reasoning_content[]`
与 `logprobs.content[]`。所以这里存成 `{"推理": [...], "正文": [...]}`，每条带 `ord`（整轮生成顺序）
⇒ 分开读、也能按 `ord` 拼回一条完整序列。`bytes` 丢掉（token 能重建）。
"""
import gzip
import io
import json
import os
from typing import Dict, List, Optional

# 指纹里必须有的几项（`流程框架 §3.1.1`）
指纹字段 = ("模型", "temperature", "effort", "max_tokens", "top_logprobs", "提示词哈希")


class 底片:
    """一场的底片 + 逐轮记录。**只追加，永不改写已有内容。**"""

    def __init__(self, 场id: str, 指纹: Dict, cache_dir: str,
                 系统前缀: Optional[List[str]] = None):
        self.场id = str(场id)
        self.指纹 = dict(指纹 or {})
        self.cache_dir = cache_dir
        # ★ 系统前缀**只存一次**（2026-10-17）：每轮都把完整请求抄一份是**平方级**的——
        #   上下文 1M token 时每轮多存 3.9 MB、1000 轮就是 3.8 GB（实测推算）。
        #   而前面那部分本来就是重复的 ⇒ 这里留一次前缀，逐轮只留**增量**，拼回来完全等价。
        self.系统前缀: List[str] = list(系统前缀 or [])
        self.逐轮: List[Dict] = []
        self.撤回记录: List[Dict] = []
        self._载入()

    # ------------------------------------------------------------------ 路径
    def _底片路径(self) -> str:
        """★ 底片写成 **`.json.gz`**（2026-10-17）：结构一个字段都没动，只是无损压一层。

        作者口径：「尽量不去在结构化存储上动刀，那样可能会让数据不稳，尽量在无损压缩上把体积压小」。
        ⇒ 实测 1,177,096 B → 134,269 B（8.8×）；手机上 toybox 有 `gzip -dc` 能直接看，
          桌面 `gzip.open` 直接读（`读底片` 两种都认）。
        """
        return os.path.join(self.cache_dir, "底片", self.场id + ".json.gz")

    def _旧路径(self) -> str:
        """没压缩过的旧文件（P1 早期写的那几份）—— 读的时候仍然要认。"""
        return os.path.join(self.cache_dir, "底片", self.场id + ".json")

    def _记录路径(self) -> str:
        # ⚠️ 逐轮记录**保持明文 JSONL**：它是"一轮一行、只追加"，压了就没法追加了。
        return os.path.join(self.cache_dir, "逐轮记录", self.场id + ".jsonl")

    def _载入(self) -> None:
        """接着聊（手机上 App 被杀是常态）⇒ 把这一场已有的接回来再往后追加。"""
        d = 读底片(self._底片路径()) or 读底片(self._旧路径())
        if not d:
            return
        try:
            self.逐轮 = list(d.get("逐轮") or [])
            self.撤回记录 = list(d.get("撤回记录") or [])
            if not self.系统前缀 and d.get("系统前缀"):
                self.系统前缀 = list(d["系统前缀"])
            if not self.指纹 and d.get("指纹"):
                self.指纹 = dict(d["指纹"])          # ★ 以先写下的那份为准
        except Exception:                            # noqa: BLE001
            self.逐轮, self.撤回记录 = [], []

    # ------------------------------------------------------------------ 写
    def 加轮(self, 轮: int, 刺激: str, 她说: str,
             调用: Optional[List[Dict]] = None,
             取用: Optional[List[Dict]] = None,
             旧池取用: Optional[List[str]] = None,
             参数: Optional[Dict] = None, 成本: Optional[Dict] = None) -> Dict:
        """加一轮。区间**只在这里算** ⇒ 永远与 `原文` 对得上。"""
        刺激 = str(刺激 or "")
        她说 = str(她说 or "")
        已用 = sum(len(t.get("刺激") or "") + len(t.get("她说") or "") for t in self.逐轮)
        s0, s1 = 已用, 已用 + len(刺激)
        a0, a1 = s1, s1 + len(她说)
        row = {
            "轮": int(轮),
            "刺激": 刺激, "她说": 她说,
            "刺激区间": [s0, s1], "她说区间": [a0, a1],
            "调用": list(调用 or []),
            "取用": 取用,                     # ⚠️ 件层没接进来之前就是 None（规范里的合法值）
            "参数": dict(参数 or {}),
            "成本": dict(成本 or {}),
            "撤回": False,
        }
        if 旧池取用 is not None:
            row["旧池取用"] = list(旧池取用)   # 过渡字段：件层接上就删
        self.逐轮 = [t for t in self.逐轮 if int(t.get("轮") or 0) != int(轮)] + [row]
        self.逐轮.sort(key=lambda t: int(t.get("轮") or 0))
        try:
            os.makedirs(os.path.dirname(self._记录路径()), exist_ok=True)
            行 = {"场id": self.场id, "轮": int(轮), "刺激": [s0, s1], "取用": 取用,
                  "她说": 她说, "参数": dict(参数 or {}), "成本": dict(成本 or {}),
                  # ★ 调用次数与 logprobs 的 token 数也进逐轮记录：不打开底片也能看出
                  #   "这一轮她往返了几次、概率收了多长"（多存不碍事）
                  "调用次数": len(row["调用"]),
                  "logprobs条数": sum(len((c.get("logprobs") or {}).get(k) or [])
                                      for c in row["调用"] for k in ("推理", "正文")),
                  }
            with io.open(self._记录路径(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(行, ensure_ascii=False) + "\n")
        except Exception:                            # noqa: BLE001
            pass          # ⛔ 记数据失败不许打断这一场对话
        return row

    def 撤回(self, 轮: int) -> None:
        """★ 撤回**不改原文、不删轮次**：只在旁边记一笔，并给那一轮打标。

        作者口径：底片是存档 ⇒ "撤回"本身也是**发生过的事**，它得留下来。
        """
        n = int(轮)
        for t in self.逐轮:
            if int(t.get("轮") or 0) == n:
                t["撤回"] = True
        self.撤回记录.append({"轮": n})
        try:
            with io.open(self._记录路径(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"场id": self.场id, "轮": n, "撤回": True},
                                    ensure_ascii=False) + "\n")
        except Exception:                            # noqa: BLE001
            pass

    def 原文(self) -> str:
        """整场逐字：所有轮的 `刺激 + 她说` 直接拼起来（⛔ 不加任何分隔符）。"""
        return "".join((t.get("刺激") or "") + (t.get("她说") or "") for t in self.逐轮)

    def 落盘(self) -> None:
        """整条重写底片（内容只增），**无损压成 `.json.gz`**。

        ★ 结构一字未动 —— 作者口径：「尽量不去在结构化存储上动刀，那样可能会让数据不稳，
          尽量在无损压缩上把体积压小」。实测 1,177,096 B → 134,269 B（8.8×）。
        ★ `mtime=0`：同样的内容压出**同样的字节**（不然每次 gzip 头里带时间戳，对照/去重都难受）。
        """
        try:
            os.makedirs(os.path.dirname(self._底片路径()), exist_ok=True)
            d = {"场id": self.场id, "系统前缀": self.系统前缀, "原文": self.原文(),
                 "逐轮": self.逐轮, "撤回记录": self.撤回记录, "指纹": self.指纹}
            raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            with gzip.GzipFile(self._底片路径(), "wb", compresslevel=6, mtime=0) as fh:
                fh.write(raw)
            # ★ 旧的**未压缩**文件（P1 早期写过的那几份）删掉：不留两份"真源"。
            try:
                if os.path.exists(self._旧路径()):
                    os.remove(self._旧路径())
            except Exception:                        # noqa: BLE001
                pass
        except Exception:                            # noqa: BLE001
            pass


# ---------------------------------------------------------------------- 指纹 / 读回

def 提示词哈希(*文本: str) -> str:
    """提示词算成一个短哈希。温度/模型/提示词任一变化，同一句话的 `logprobs` 就不一样
    ⇒ 没有它，两份底片分不清是"它变了"还是"条件变了"。"""
    import hashlib
    h = hashlib.sha256()
    for t in 文本:
        h.update((t or "").encode("utf-8"))
        h.update(b"\x00")            # 分隔符：免得 ("ab","c") 与 ("a","bc") 撞
    return h.hexdigest()[:16]


def 指纹(cfg, 提示词: tuple = ("", ""), 读法版本: int = 0) -> Dict:
    chat = getattr(cfg, "chat", None)
    return {
        "模型": str(getattr(chat, "model", "") or ""),
        "temperature": float(getattr(chat, "temperature", 0.0) or 0.0),
        "effort": str(getattr(chat, "effort", "") or ""),
        "max_tokens": int(getattr(chat, "max_tokens", 0) or 0),
        "top_logprobs": int(getattr(chat, "top_logprobs", 0) or 0),
        "提示词哈希": 提示词哈希(*提示词),
        "读法版本": int(读法版本 or 0),
    }


def 读底片(path: str) -> Optional[Dict]:
    """读一份底片：`.json.gz`（现在的）与旧 `.json` 都认。读不出来 ⇒ None，⛔ 不抛。"""
    try:
        if str(path).endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                # noqa: BLE001
        return None


def 切回原文(底片数据: Dict, 轮: int, 哪一句: str = "刺激") -> str:
    """按区间从 `原文` 逐字取回那一轮的那一句 —— 区间坐标的唯一验收方式。"""
    for t in (底片数据.get("逐轮") or []):
        if int(t.get("轮") or 0) == int(轮):
            rng = t.get(哪一句 + "区间")
            if not rng:
                return ""
            a, b = int(rng[0]), int(rng[1])
            return (底片数据.get("原文") or "")[a:b]
    return ""
