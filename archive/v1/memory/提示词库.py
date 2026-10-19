# -*- coding: utf-8 -*-
"""提示词库 —— **所有给模型看的正文都从这里取**（⛔ 不再硬编码在 `.py` 里）。

## 目录

    memory/提示词/            默认正文（随仓库、打进 APK 的只读资源）
    缓存/提示词覆盖/<id>.txt  手机上改过的那一份（有就盖过默认，没有就用默认）

★ 为什么默认在仓库里：改默认 = 改仓库，能进 git、能被 review；
★ 为什么改过的另外存：手机上 APK 里的文件是只读的，改不动；而且「默认是什么」和
  「我现在用的是哪份」必须分得开 —— 否则一键恢复默认就成了猜测。

## 占位符

用 `$名字`（⛔ 不用 `{}`）：正文里有 JSON（`{"归进": …}`），`str.format` 会把这些花括号
当成占位符，逼着每处都写成 `{{ }}` —— 人在手机上敲一个 `{` 就把整段提示词弄坏。
`$名字` 只认 `$`，花括号可以随便写。名字用中文没有关系（见 `_占位` 那条正则）。

## 一处一个文件

改错不会牵连别的提示词；界面上一项一屏。
"""
from __future__ import annotations

import io
import os
import re
from typing import Dict, List, Optional

默认目录 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "提示词")

# 占位符：`$` + 一个非数字开头的标识符（中文也算）。`$$` 是字面 `$`。
_占位 = re.compile(r"\$(?:(\$)|([^\W\d]\w*)|\{([^}]+)\})", re.UNICODE)

条目们: List[Dict] = [
    # ★ 2026-10-18（作者：提示词按**流程顺序**排）：开场 → 每轮（对话/读取/编写）→ 归档。
    {"id": "常驻-设定", "组": "常驻", "名称": "开场 · 常驻记忆（手写设定）",
     "占位符": ["我"],
     "说明": "一次对话开头只给一次的那一段里，手写的那部分：她是谁、谁做的记忆、坏了谁修、"
             "工具从哪来。留空 = 不做手写设定。"},
    {"id": "对话-系统1", "组": "对话", "名称": "每轮 · 系统一（工具与基本设定）",
     "占位符": ["我"],
     "说明": "每一轮最前面那一段。改它等于改她每一轮的行为底线。"},
    {"id": "对话-系统2", "组": "对话", "名称": "每轮 · 系统二（分寸）",
     "占位符": ["我"],
     "说明": "紧跟在系统一后面的一串分寸（不派活、不附和、不套话）。"},
    {"id": "对话-补充", "组": "对话", "名称": "每轮 · 补充（第三条 system）",
     "占位符": ["用户"],
     "说明": "每轮最后一条 system：默认写「这是和谁的私聊」，也可以拿它写别的（补充设定、提醒、场合）。"},

    {"id": "写-判定", "组": "编写", "名称": "读取 · 判定（旧记录要不要并进这一句）",
     "占位符": ["素材", "现在", "用户"],
     "说明": "温度 0，只回一行「判断：有 / 无」。只认指回。"},
    {"id": "写-编写", "组": "编写", "名称": "读取 · 编写（把取到的写成一段回忆）",
     "占位符": ["条目", "用户"],
     "说明": "第一人称一段话。只写最清楚的几处，按日期分先后。"},
    {"id": "判-自问", "组": "归档", "名称": "归档 · 问她这一场有没有想记牢的",
     "占位符": ["条目", "上限", "用户", "我"],
     "说明": "归档时主动问她一句（不靠她临场想起去调 remember）。"},
    {"id": "判-判", "组": "归档", "名称": "归档 · 判断这一处（归进 / 新建 / 连 / 改口）",
     "占位符": ["轮", "怎么来的", "这一处", "候选", "找件次数", "块", "用户", "我"],
     "说明": "归档五步里「⑤ 判」那一次调用。它决定一件事被记成几件、连到谁、算不算改口。"},
    {"id": "判-分堆", "组": "归档", "名称": "归档 · 本批分堆（哪几句是同一件事）",
     "占位符": ["名单"],
     "说明": "没归进已有件的那些格，彼此是不是同一件事。判错会让「一件」变成两件。"},
]

_按id = {x["id"]: x for x in 条目们}


# --------------------------------------------------------------------------- 路径
def 覆盖目录() -> str:
    """改过的那几份放哪儿。

    顺序：环境变量 `COMPANION_PROMPT_DIR` → 凭证同级目录下的 `提示词覆盖/`
    （桌面上是 `~/.dsh`，手机上是 `files/`，与 `prompt.yaml` 同一个地方）。
    """
    env = os.environ.get("COMPANION_PROMPT_DIR")
    if env:
        return str(env)
    try:
        from .config import cred_path
        return str(cred_path().parent / "提示词覆盖")
    except Exception:                                    # noqa: BLE001
        return os.path.join(默认目录, "..", "提示词覆盖")


def 默认文件(id: str) -> str:
    return os.path.join(默认目录, str(id) + ".txt")


def 覆盖文件(id: str) -> str:
    return os.path.join(覆盖目录(), str(id) + ".txt")


def _读(p: str) -> str:
    with io.open(p, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- 取 / 存
def 默认(id: str) -> str:
    """仓库里那一份（⛔ 不看覆盖）。文件缺失时返回空串并吵一声 —— 不抛。"""
    try:
        return _读(默认文件(id))
    except Exception as exc:                             # noqa: BLE001
        import sys
        print("[提示词库] 读不到默认正文 %s（%s: %s）"
              % (默认文件(id), type(exc).__name__, exc), file=sys.stderr)
        return ""


def 取(id: str) -> str:
    """生效的那一份：覆盖 → 默认。⛔ 两处都读不到就是空串（调用方各自兜底）。"""
    if id not in _按id:
        raise KeyError("没有这个提示词：%s" % id)
    p = 覆盖文件(id)
    try:
        if os.path.exists(p):
            文 = _读(p)
            if 文.strip():
                return 文
    except Exception as exc:                             # noqa: BLE001
        import sys
        print("[提示词库] 覆盖读不出来，改用默认：%s（%s）" % (p, type(exc).__name__),
              file=sys.stderr)
    return 默认(id)


def 已改(id: str) -> bool:
    return os.path.exists(覆盖文件(id))


def 存(id: str, 文本: str) -> str:
    """存一份覆盖。**原子写**（手机上进程随时会被杀，半截文件下次读就是坏的）。"""
    import tempfile
    p = 覆盖文件(id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    文本 = str(文本)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".提示词-", suffix=".tmp")
    try:
        with io.open(fd, "w", encoding="utf-8") as f:
            f.write(文本)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return p


def 恢复(id: str) -> bool:
    """删掉覆盖 ⇒ 回到仓库默认。返回「原本有没有覆盖」。"""
    p = 覆盖文件(id)
    if os.path.exists(p):
        os.unlink(p)
        return True
    return False


# --------------------------------------------------------------------------- 填 / 验
def 占位符们(文本: str) -> List[str]:
    """正文里出现的占位符名（去重、保序）。"""
    出: List[str] = []
    for m in _占位.finditer(str(文本 or "")):
        名 = m.group(2) or m.group(3)
        if 名 and 名 not in 出:
            出.append(名)
    return 出


def 填文(文本: str, **值) -> str:
    """给一段**已经有**的文本填占位符（`填` 是"先取再填"，这个是"只填"）。"""

    def sub(m):
        if m.group(1):
            return "$"
        k = m.group(2) or m.group(3)
        if k not in 值:
            raise KeyError("缺占位符：%s" % k)
        return str(值[k])

    return _占位.sub(sub, str(文本 or ""))


def 填(id: str, **值) -> str:
    """取生效正文并按 `$名字` 填。缺哪个名字就抛 `KeyError`（⛔ 静默留个 `$x` 给模型看）。"""
    文 = 取(id)

    def sub(m):
        if m.group(1):                       # `$$` → 字面 `$`
            return "$"
        k = m.group(2) or m.group(3)
        if k not in 值:
            raise KeyError("提示词 %s 缺占位符：%s（正文里有 $%s，调用没给）" % (id, k, k))
        return str(值[k])

    return _占位.sub(sub, 文)


def 校验(id: str, 文本: str) -> Dict[str, object]:
    """给界面用：这段正文能不能替换默认。

    · 空 ⇒ 不行（空提示词会让模型失去全部约束）；
    · **少占位符** ⇒ 不行（原来要填的东西没了，那一处会变成空白）；
    · 多出来的 `$x` ⇒ 不行（调用方不会给它，`填` 会抛）。
    """
    条 = _按id.get(id) or {}
    要 = list(条.get("占位符") or [])
    有 = 占位符们(文本)
    少 = [x for x in 要 if x not in 有]
    多 = [x for x in 有 if x not in 要]
    if not str(文本 or "").strip():
        return {"ok": False, "err": "正文是空的", "少": 少, "多": 多}
    if 少:
        return {"ok": False, "err": "少了占位符：%s" % "、".join("$" + x for x in 少),
                "少": 少, "多": 多}
    if 多:
        return {"ok": False, "err": "多了没处填的占位符：%s" % "、".join("$" + x for x in 多),
                "少": 少, "多": 多}
    return {"ok": True, "err": "", "少": [], "多": []}


def 列表() -> List[Dict]:
    """界面上那份清单（只读信息 + 现在是不是改过）。"""
    出 = []
    for x in 条目们:
        出.append({"id": x["id"], "组": x["组"], "名称": x["名称"],
                   "占位符": list(x["占位符"]), "说明": x.get("说明") or "",
                   "已改": 已改(x["id"]), "字数": len(取(x["id"]))})
    return 出


def 一屏(id: str) -> Dict:
    """一项的完整内容（给编辑页）。"""
    条 = _按id.get(id)
    if not 条:
        raise KeyError("没有这个提示词：%s" % id)
    return {"id": id, "组": 条["组"], "名称": 条["名称"],
            "占位符": list(条["占位符"]), "说明": 条.get("说明") or "",
            "已改": 已改(id), "正文": 取(id), "默认": 默认(id)}
