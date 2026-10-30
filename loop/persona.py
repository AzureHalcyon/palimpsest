"""她自己写的自我描述：落在 `<data>/persona.md`，隔一段时间**自动重写一次**。

为什么不让她在每一步直接改：persona 挂在 system 上，改一次会把前缀缓存整段作废。
所以这里的默认分工是——**内容归她，时机归定时**：平时不提（她每步都看得到当前这版），
等到闲下来（`_maintain` 里）且距上次改写超过 `persona_hours` 小时，才用一个后台
调用把最近这段记录里"对自己更清楚的部分"沉淀进去。

★ 2026-10-30：这一版之后，入口那一侧可以给她一个自己改的入口（把 `write()` 接出去，
配自己的频率限制）。核心不参与、也不知道——对这里来说那只是另一处调用 `write()` +
`touch_stamp()` 的地方，和网页设置里那一行没有区别。

三份文件，职责分开：
- `persona.md`：当前这版（她自己写的，非空才拼进 system）
- `persona.log`：改写历史（时间 + 全文，只追加；给自己看的是当前那版，这是给**人**回溯的）
- `persona.stamp`：上次改写（或尝试）的时间，用来判"到点了吗"

写坏了不会卡住循环：失败就退避十分钟再试，当前那版原样保留。
"""

from __future__ import annotations

import io
import json
import os
import time

from . import prompt_files

DEFAULT_CHARS = 1200
DEFAULT_HOURS = 24.0
TUNE_FLOOR = 0.75            # 微调之后跟原来至少这么像，否则当成重写、不收
MATERIAL_CHARS = 30000       # 喂给整理任务的上限（可被 persona_feed_chars 覆盖，0 = 全部）


def _path(config, name: str) -> str:
    folder = str(getattr(config, "data_dir", ".") or ".")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return os.path.join(folder, name)


def path_of(config) -> str:
    return _path(config, "persona.md")


def read(config) -> str:
    """当前这版；没有就是空串。"""
    try:
        with io.open(path_of(config), encoding="utf-8") as handle:
            return str(handle.read() or "").strip()
    except OSError:
        return ""


def limit_of(config) -> int:
    try:
        value = int(getattr(config, "persona_chars", DEFAULT_CHARS) or DEFAULT_CHARS)
    except (TypeError, ValueError):
        value = DEFAULT_CHARS
    return max(200, min(value, 8000))


def write(config, text: str) -> str:
    """整版替换（超上限就截断），顺手记一笔历史。返回真正落盘的文本。"""
    body = str(text or "").strip()
    cap = limit_of(config)
    if len(body) > cap:
        body = body[:cap].rstrip()
    target = path_of(config)
    with io.open(target, "w", encoding="utf-8", newline="") as handle:
        handle.write(body + "\n" if body else "")
    try:
        with io.open(_path(config, "persona.log"), "a", encoding="utf-8") as handle:
            handle.write("\n===== %s（%d 字）=====\n%s\n"
                         % (time.strftime("%Y-%m-%d %H:%M:%S"), len(body), body))
    except OSError:
        pass
    return body


def stamp_of(config) -> float:
    """上次改写（或尝试）的时刻；没记过就是 0。"""
    try:
        with io.open(_path(config, "persona.stamp"), encoding="utf-8") as handle:
            return float(str(handle.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0.0


def touch_stamp(config, moment: float = None) -> None:
    try:
        with io.open(_path(config, "persona.stamp"), "w", encoding="utf-8") as handle:
            handle.write("%.0f" % (moment if moment is not None else time.time()))
    except OSError:
        pass


def hours_of(config) -> float:
    try:
        value = float(getattr(config, "persona_hours", DEFAULT_HOURS) or 0)
    except (TypeError, ValueError):
        value = DEFAULT_HOURS
    return max(0.0, value)


def due(config, moment: float = None, floor: float = 0.0) -> bool:
    """到点了吗：开着、距上次超过 persona_hours 小时、而且不在退避里。"""
    if not bool(getattr(config, "persona_enabled", False)):
        return False
    hours = hours_of(config)
    if hours <= 0:
        return False
    now = moment if moment is not None else time.time()
    if now < float(floor or 0):
        return False
    return (now - stamp_of(config)) >= hours * 3600.0


def feed_chars_of(config) -> int:
    try:
        value = int(getattr(config, "persona_feed_chars", MATERIAL_CHARS) or 0)
    except (TypeError, ValueError):
        value = MATERIAL_CHARS
    return max(0, value)


def new_material(context, config, budget: int = 0) -> str:
    """上次改写**之后**才有的记录。

    只喂新材料有两个原因：一段旧事不必反复喂（会越写越像复述），而且"有没有
    足够的新东西"正好就是触发的判据——聊得不够多就先不写。
    """
    stamp = stamp_of(config)
    entries = list(context.previous.entries) + list(context.current.entries)
    if stamp > 0:
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
        entries = [entry for entry in entries
                   if str(getattr(entry, "ts", "") or "") >= cutoff]
    if not entries:
        return ""
    from .context import serialize_entries

    text = serialize_entries(entries, getattr(config, "tool_fold_keep", 0),
                             user_name=getattr(config, "user_name", ""))
    # 旧段摘要是长程连续性：只给摘要，不给已压过的原文（重复喂会越写越像复述）
    if bool(getattr(config, "persona_with_bands", True)):
        bands = str(context.bands_text() or "").strip() if hasattr(context, "bands_text") else ""
        if bands:
            text = "旧段摘要：\n%s\n\n上次改写之后：\n%s" % (bands, text or "（还没有）")
    if budget and len(text) > budget:
        text = "…\n" + text[-budget:]
    return text


def min_chars_of(config) -> int:
    try:
        value = int(getattr(config, "persona_min_chars", 8000) or 0)
    except (TypeError, ValueError):
        value = 8000
    return max(0, value)


def enough(context, config, budget: int = 0) -> str:
    """够不够写一版：返回要喂的文本（够长才返回，不够就是空串）。

    门槛按**未截断**的长度算，喂的时候才截到 feed_chars：不然门槛比喂养上限大，
    就永远触发不了。
    """
    full = new_material(context, config)
    if len(full) < min_chars_of(config):
        return ""
    cap = budget or feed_chars_of(config)
    return new_material(context, config, cap)


def tune(chat, material: str, config) -> str:
    """微调一版：只改一两句。改得跟重写一样（跟原来不像了）就不收，返回空串。

    为什么要这道门：提示词里写"只做微调"是软约束，模型经常一整篇重写。persona 挂在
    system 上，重写一次就是一个人的口吻换了一遍——宁可这次不改，等「整理」到点整版写。
    """
    current = read(config)
    body = str(material or "").strip()
    if not current or not body:
        return ""                     # 还没写过 / 没材料：微调没有对象
    prompt = prompt_files.render("persona-tune.txt", limit=limit_of(config),
                                 current=current)
    reply = chat([{"role": "system", "content": prompt},
                  {"role": "user", "content": body}]) or {}
    text = str(reply.get("content") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    text = text.strip()
    if not text or text == current:
        return ""
    if len(text) > int(len(current) * 1.3) + 200:
        return ""                     # 长了一大截：这不是微调
    import difflib
    if difflib.SequenceMatcher(None, current, text).ratio() < TUNE_FLOOR:
        return ""                     # 改得跟重写一样：不收
    return write(config, text)


def rewrite(chat, context, config) -> str:
    """调一次模型重写自我描述，落盘并返回新文本。

    `chat` 是 `callable(messages) -> dict`（测试里塞假的）。失败抛出去，由调用方
    决定退避——这里不吞异常，免得"以为写成了"。
    """
    current = read(config)
    text = enough(context, config)
    if not text:
        text = new_material(context, config, feed_chars_of(config))
    prompt = prompt_files.render("persona.txt",
                                limit=limit_of(config),
                                current=current or "（还没写过）")
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": text or "（这段时间没有记录）"}]
    reply = chat(messages) or {}
    body = str(reply.get("content") or "").strip()
    if not body:
        raise ValueError("整理任务没有给出内容")
    # 有些模型会包一层代码围栏，剥掉
    if body.startswith("```"):
        body = body.strip("`")
        if "\n" in body:
            body = body.split("\n", 1)[1]
    return write(config, body)
