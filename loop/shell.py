"""在宿主机上跑一条命令：只跑白名单里的程序，不经过 shell。

说明文字在 `prompts/tools.txt` 的 `## run` 段里，这里只管闸门和分派。和记忆、上网
工具一样，出错不是抛给循环，而是写进工具结果——模型下一步能看见「不给跑」，而不是
整步挂掉。

★ 这是**闸门，不是沙箱**。判据是第一段的程序名，而且不经过 shell：管道、重定向、
  `$(...)`、`sh -c` 全都进不来（`shlex.split` 切不开的写法直接拒）。命令在
  `<data>/workspace` 里跑，带超时（超时连进程组一起杀），输出截断。
★ 它以服务用户的身份跑，那个用户读得到的文件它就读得到——所以说明里写着别把密钥
  读出来写进记录。真要把「碰得到什么」也管住，得另起低权限用户或者 bwrap，
  那是另一件事，这里先不做。
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import time
from typing import List

from . import prompt_files

NAMES = ("run",)

# 少数程序里还要再挑子命令：git 只给读的那几个。
SUBCOMMANDS = {"git": ("status", "log", "diff", "show", "branch", "remote", "tag")}

# 敏感文件：命令照跑，但参数里点到它就拒。它不是沙箱——源码、数据、日志随便读，
# 只有凭证/密钥这一类不许进记录（读出来会变成"她说过密钥"，还可能被写成笔记）。
SENSITIVE_DIRS = ("/etc/palimpsest", "/etc/shadow", "/etc/sudoers", "/root/")
SENSITIVE_NAMES = ("credentials.yaml", "credentials.yml", "web-token",
                   "id_rsa", "id_ed25519", "id_ecdsa", "authorized_keys")
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")


def _sensitive(token: str) -> str:
    """这个参数像不像敏感文件；像就返回它，不像返回空。"""
    text = str(token or "").strip().strip("\"'")
    if not text:
        return ""
    lowered = text.lower()
    for folder in SENSITIVE_DIRS:
        if folder in lowered:
            return text
    name = os.path.basename(lowered)
    if name in SENSITIVE_NAMES:
        return text
    if lowered.endswith(SENSITIVE_SUFFIXES):
        return text
    if "/.ssh/" in lowered or lowered.startswith("~/.ssh"):
        return text
    return ""


def workspace_of(config) -> str:
    """命令在哪儿跑：数据目录下的 workspace，不是仓库也不是家目录。"""
    return os.path.join(str(getattr(config, "data_dir", ".") or "."), "workspace")


def allowed(config) -> List[str]:
    """白名单：设置里那一行字，逗号或空格都认。"""
    raw = str(getattr(config, "run_allow", "") or "")
    return [item for item in raw.replace(",", " ").split() if item]


def tools(config=None) -> List[dict]:
    """没开就给空表：工具面里连 `run` 都不出现。"""
    if not bool(getattr(config, "run_enabled", False)):
        return []
    text = prompt_files.tool_texts()
    return [
        {"type": "function", "function": {
            "name": "run",
            "description": text["run"],
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": text["run.command"]},
                },
                "required": ["command"],
            },
        }},
    ]


def call(name: str, arguments: str, config) -> str:
    if name not in NAMES:
        return "没有这个工具：%s" % name
    if not bool(getattr(config, "run_enabled", False)):
        return "命令没开：这是宿主上的能力，默认关；要开在设置里打开「命令」。"
    try:
        payload = json.loads(arguments or "{}")
    except ValueError:
        return "参数不是 JSON。"
    if not isinstance(payload, dict):
        return "参数不是对象。"
    command = str(payload.get("command") or "").strip()
    if not command:
        return "要给一条命令。"
    return execute(command, config)


def execute(command: str, config) -> str:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return "这条命令切不开（引号没配平？）：%s" % exc
    if not argv:
        return "要给一条命令。"
    names = allowed(config)
    program = os.path.basename(argv[0])
    if program not in names:
        return ("不给跑：%s 不在白名单里。现在能跑的是 %s。"
                "要加就在设置里改「命令白名单」。" % (program, " / ".join(names)))
    for token in argv[1:]:
        hit = _sensitive(token)
        if hit:
            return ("不给读：%s 像凭证或密钥，那类东西不进记录。源码、数据、日志随便看。"
                    % hit)
    subs = SUBCOMMANDS.get(program)
    if subs:
        picked = next((part for part in argv[1:] if not part.startswith("-")), "")
        if picked not in subs:
            return ("%s 只给 %s 这几个。要写东西/换分支得先加白名单，或者让对方来做。"
                    % (program, " / ".join(subs)))

    workdir = workspace_of(config)
    try:
        os.makedirs(workdir, exist_ok=True)
    except OSError as exc:
        return "工作目录建不出来：%s" % exc
    try:
        timeout = float(getattr(config, "run_timeout", 20.0) or 20.0)
    except (TypeError, ValueError):
        timeout = 20.0
    try:
        limit = int(getattr(config, "run_output_chars", 4000) or 4000)
    except (TypeError, ValueError):
        limit = 4000

    started = time.time()
    try:
        proc = subprocess.Popen(  # noqa: S603 - 白名单 + 不经 shell，就是这里的闸门
            argv, cwd=workdir, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", start_new_session=True)
    except OSError as exc:
        return "起不来：%s" % exc

    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out = ""
    cost = time.time() - started
    body = _clip(str(out or ""), limit)
    head = ("超时 %g 秒，已经杀掉" % timeout if timed_out
            else "退出码 %s" % proc.returncode)
    return "（%s，用时 %.1f 秒）%s%s" % (head, cost, "\n" if body else "",
                                        body or "没有输出")


def _kill(proc) -> None:
    """连它自己起的东西一起杀：超时之后不能留孤儿进程。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (AttributeError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _clip(text: str, limit: int) -> str:
    """超长输出留头又留尾：头是它说了什么，尾是最后那句结果。"""
    if limit <= 0 or len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = max(0, limit - head)
    return "%s\n…（省略 %d 字）…\n%s" % (text[:head], len(text) - limit,
                                        text[-tail:] if tail else "")
