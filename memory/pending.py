# -*- coding: utf-8 -*-
"""`pending.json` —— "这一场还没归档完"的落盘标记。

★★ 这是**宿主搬上手机时唯一必须改业务逻辑的地方**，所以单独成文件、单独写清规矩。

为什么需要它
------------
归档（dreaming）要跑 **3–4 回合模型往返**。桌面上进程被杀是意外；
手机上是常态（用户随手划掉 App、系统回收、HyperOS 直接 `signal 9`）。
而**归档失败 = 这一场的记忆整段丢掉**；更糟的是：归档失败时 `apply_archive` 仍会
照常执行死亡集清理，所以"丢一场"并不等于"什么都没发生"。

三条规矩
--------
1. **一场只有一份在途**。归档落地用的是临时池的**序号**（`again/done/promote` 都填序号），
   两场并行会串号 ⇒ 在途时不许开新的一场（由入口先补做）。
2. **标记先落盘，再动模型**。写是原子的（临时文件 + `os.replace`）：
   写坏一半的文件不会留在那个位置上。
3. **落地成功之后才清**。清了之前崩掉 ⇒ 下次启动补做；补做靠 `applied_sids` 幂等
   （见 `Engine.archive`），所以**重复补做不会重复落地**。

★ 续做用的是**那一场结束时的时钟**（`rec["clock_at_end"]`），不是现在的时钟：
  归档是"那场结束后它自己待着做的回顾"，时钟往前跳会让它当时看得见的临时条目先淡掉、
  序号跟着错位。补完之后当前时钟不动（`Engine.resume_pending` 里说清了）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional


class PendingStore:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.path = os.path.join(cache_dir, "pending.json")

    # ------------------------------------------------------------------ 写
    def save(self, rec: dict) -> None:
        """原子写。**必须在动模型之前调用。**"""
        os.makedirs(self.cache_dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------ 读
    def exists(self) -> bool:
        return os.path.exists(self.path)

    def load(self) -> Optional[dict]:
        """读回在途的那一场；没有就返回 `None`。

        ★ 文件坏了**不静默吞掉**：改名留档（`pending.bad-<时间戳>.json`）并**大声报**，
          然后返回 `None`。规矩是"什么都不许删"，所以是改名不是删除。
        """
        if not self.path_exists():
            return None
        try:
            with open(self.path, encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                raise ValueError("pending.json 不是对象")
            return rec
        except Exception as exc:  # noqa: BLE001
            bad = self.path.replace(".json", ".bad-%d.json" % int(time.time()))
            try:
                os.replace(self.path, bad)
            except Exception:  # noqa: BLE001
                bad = "(改名也失败了)"
            sys.stderr.write(
                "!! pending.json 读不出来（%s: %s）—— 已留档到 %s。\n"
                "   这一场的归档补不上了；**别当它已经归档**。\n"
                % (type(exc).__name__, exc, bad))
            return None

    def path_exists(self) -> bool:
        return os.path.exists(self.path)

    # ------------------------------------------------------------------ 清
    def clear(self) -> None:
        """落地成功之后清标记。清不掉不算致命（下次靠幂等跳过）。"""
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("!! pending.json 清不掉（%s）—— 下次会再做一遍幂等检查。\n"
                             % type(exc).__name__)
