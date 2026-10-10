# -*- coding: utf-8 -*-
"""取模型回复。两条路：本机 dsh-llm-call 的进程接口（默认），或直接连 OpenAI 兼容端点。

约定与纪律：
  · 每次调用都把 `max_tokens` 与**空回复数**报出来（推理模型配额被 reasoning 吃光时，
    正文会是空的，`finish_reason=length`）。
  · 不打印密钥。
  · 读到的响应原样返回，不做"尽力而为"的修补。

默认走进程接口，理由：
  · 路由与凭证由本机 DSH profile 决定，不在这里再抄一份；
  · 少一层 handler：同一个问题，裸调用约 39 个输入 token，走 agent 要 134 个新 token
    外加七千多个缓存 token 的系统提示与工具面。
  · 它自己就区分正文与思考、自己就报 `finishReason`、自己就拒绝把空回复当成功——
    这三件正是这个项目踩过三次的坑。

★ 进程接口不给增量（一次调用只在末尾吐一行 JSON），所以**首字延迟与稳态速率量不到**。
  `call_model` 会把它显式标成 None，不要拿平均速率冒充稳态速率。
"""
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.daapsaak.top/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"   # ★ 别再写回 "deepseek-chat"：那条网关已经不提供它了
                                        #   （实测报 model_not_found ⇒ 404，白查半天）
CRED = r"C:\Users\AzureHalcyon\.dsh\.credentials.yaml"
# 本机覆盖：格式 KEY=VALUE，用于指定某个端点的 key 与地址。不进版本控制。
# ★ 这里是 tools/取数/，所以仓库根要上溯三层。**工具一挪目录，这种相对层数就得跟着改**
#   ——挪完必须把每个脚本都真跑一遍（见 tools/README.md 末尾那条）。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_ENV = os.path.join(ROOT, ".local", "接入凭证.env")


def _local_env(path=LOCAL_ENV):
    out = {}
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def resolve_key(name="DEEPSEEK_API_KEY"):
    """优先用本机覆盖文件，其次凭证文件。都不在就报错。"""
    local = _local_env()
    if local.get(name):
        return local[name]
    # ★ 文件在、键也在，**只是值是空的**——换机器时最常见的那一步漏做。
    #   原来会一路掉到凭证文件，报"凭证文件不存在"（实测就吃过这个误导：
    #   真正的毛病是 key 那一行等号后面什么都没有）。
    if name in local:
        raise RuntimeError(
            "凭证文件里 %s 是空的 ⇒ 没法认证。\n"
            "  把 key 填在等号后面（不要引号、不要留空格）：%s\n"
            "  或者设同名环境变量。" % (name, LOCAL_ENV))
    return load_credential(name)


def resolve_base(default=DEFAULT_BASE):
    return _local_env().get("DEEPSEEK_BASE") or default


def load_credential(name="DEEPSEEK_API_KEY", path=CRED):
    """从凭证文件的 refs 段取值。不用 yaml，避免多一个依赖。"""
    refs, inside = {}, False
    for ln in open(path, encoding="utf-8").read().splitlines():
        if re.match(r"^refs:\s*$", ln):
            inside = True
            continue
        if inside:
            if not ln.strip():
                continue
            if not ln.startswith((" ", "\t")):
                break
            m = re.match(r"^\s+([A-Za-z_][\w\-]*)\s*:\s*(.*)$", ln)
            if m:
                refs[m.group(1)] = m.group(2).strip().strip("'\"")
    if name not in refs:
        raise KeyError("凭证里没有 %s（有：%s）" % (name, list(refs)))
    return refs[name]


# ★ 全局流式钩子：`callable(kind, text)`，kind ∈ {'思考','正文'}。
#   为什么要它：双击挂机时**第二个窗口**要能实时看到模型在想什么、在写什么。
#   原来只有在**一整遍跑完之后**才把正文写进日志 ⇒ 开思考一遍要 100–250 秒以上，
#   于是"等了十几分钟没见输出"（实测用户报的就是这个）。
#   钩子挂在最底层（chat_stream），所以 annotate / 带工具 / 出卷子**全都自动有**。
STREAM_TAP = None


def chat_stream(messages, model=DEFAULT_MODEL, base=None, key=None,
                max_tokens=4000, temperature=0.0, timeout=1800,
                retries=3, backoff=4, extra=None, on_delta=None, tools=None):
    """流式调用。**长请求必须用它。**

    为什么：非流式时整个生成期连接上没有任何数据流动，
    中间层会直接把连接掐掉（实测稳定复现 `SSL record layer failure`,
    连试三次都失败）。流式每个 chunk 都有数据，连接活着。

    返回一个 dict：
      content    正文（不含思考）
      reasoning  思考内容（有的端点会给；没有就是空串）
      finish     结束原因
      usage      服务端给的用量（流式时常缺；缺了就是 None）
      first_s    首字延迟（秒）；正文一个 chunk 都没收到时为 None
      chars      正文字数
      rate       稳态速率（正文 token/秒，按"思考结束后的那段"算）
      content_tokens / reasoning_tokens  服务端没给 usage 时的估算值

    ★ 报数时要分清「正文」与「思考」：正文为空才是空回复；
      思考占掉配额是另一回事。
    """
    key = key or resolve_key()
    base = base or resolve_base()
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
               "temperature": temperature, "stream": True}
    if tools:
        # ★ 函数名只能是 ASCII（`[a-zA-Z0-9_-]`）——中文名直接 400（实测）。
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if extra:
        payload.update(extra)
    url = base.rstrip("/") + "/chat/completions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json",
            "Accept": "text/event-stream"})
        t0 = time.time()
        content, reason, finish, usage = [], [], None, None
        tcs = []
        first, marks = None, []
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            while True:
                raw = r.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                piece = line[5:].strip()
                if piece == "[DONE]":
                    break
                try:
                    j = json.loads(piece)
                except Exception:
                    continue
                if j.get("usage"):
                    usage = j["usage"]
                ch = (j.get("choices") or [{}])[0]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                d = ch.get("delta") or {}
                # ★ 工具调用在流式下是**按 index 分片**吐的：第一片给 id/name，
                #   后面每片只给一段 arguments 字符串。不按 index 合并，
                #   一个调用会被读成十几个"调用"（实测 17 个）。
                for tc in d.get("tool_calls") or []:
                    i = tc.get("index") or 0
                    while len(tcs) <= i:
                        tcs.append({"id": None, "type": "function",
                                    "function": {"name": None, "arguments": ""}})
                    slot = tcs[i]
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]
                if d.get("reasoning_content"):
                    reason.append(d["reasoning_content"])
                    if STREAM_TAP:
                        STREAM_TAP("思考", d["reasoning_content"])
                if d.get("content"):
                    if STREAM_TAP:
                        STREAM_TAP("正文", d["content"])
                    if first is None:
                        first = time.time() - t0
                    content.append(d["content"])
                    marks.append(time.time() - t0)
                    if on_delta:
                        on_delta(d["content"])
            body = "".join(content)
            # 稳态速率：只看正文那一段（首字到末字），不含思考期
            if len(marks) > 10 and marks[-1] > marks[0]:
                rate = len(body) / (marks[-1] - marks[0])
            else:
                rate = len(body) / (time.time() - t0) if body else 0
            u = usage or {}
            # ★ 这里曾有一个**同名不同义**的坑：`content_tokens` 在直连这条路上赋的是
            #   **正文字符数**，而在进程接口那条路上赋的是**真 token 数**。
            #   报数时还给它贴了"计费输出（含思考）"的标签 ⇒ 于是出现
            #   "正文 1072 字 · 思考 26762 字 · 计费输出 1072 tok（含思考）"这种自相矛盾的行。
            #   ⇒ 现在三个量分开、名字各自说清：
            #     billed_tokens  服务端的 completion_tokens（**含思考**）
            #     reasoning_tokens 服务端分项（实测两条路都给：直连 daapsaak 与官方都在
            #                      completion_tokens_details.reasoning_tokens 里给）
            #     content_tokens  = billed − reasoning（换算不出来就按字数估，并标 estimated）
            billed = u.get("completion_tokens")
            rt = u.get("reasoning_tokens") or (
                (u.get("completion_tokens_details") or {}).get("reasoning_tokens"))
            if rt is None:
                rt = sum(len(x) for x in reason)          # 服务端没给：按思考字数估
            if billed is not None and rt is not None:
                ct = max(billed - rt, 0)
            elif billed is not None:
                ct = billed
            else:
                ct = len(body)
            return {"content": body, "reasoning": "".join(reason),
                    "finish": finish, "usage": usage, "first_s": first,
                    "chars": len(body), "rate": rate, "rate_kind": "稳态",
                    "tool_calls": [t for t in tcs if t.get("function")],
                    "content_tokens": ct, "billed_tokens": billed,
                    "reasoning_tokens": rt, "elapsed": time.time() - t0,
                    "prompt_tokens": u.get("prompt_tokens"),
                    "estimated": billed is None}
        except urllib.error.HTTPError as e:
            # ★ 错误体里有真正的原因，不能只留状态码。
            #   实测踩过：只报 "HTTP Error 404: Not Found"，
            #   而响应体里明明写着 `model_not_found: Model "deepseek-chat" is not available`
            #   ——缺省模型过期了。**光看状态码会一路查错方向。**
            body_txt = ""
            try:
                body_txt = e.read().decode("utf-8", "replace")[:300]
            except Exception:                                # noqa: BLE001
                pass
            last = "HTTP %s：%s" % (e.code, body_txt or e.reason)
            if 400 <= e.code < 500:
                # 4xx 是**永久错误**（模型名不对、请求不合法）——重试没有意义，
                # 而且会把一个立刻能看出的配置错误拖成三倍等待。
                raise RuntimeError(last) from None
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:200])
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise RuntimeError("重试 %d 次仍失败：%s" % (retries, last))


def chat(messages, model=DEFAULT_MODEL, base=None, key=None,
         max_tokens=4000, temperature=0.0, timeout=180, extra=None,
         retries=3, backoff=4):
    """一次对话调用。返回 (content, finish_reason, usage)。content 可能是空串。"""
    key = key or resolve_key()
    base = base or resolve_base()
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    if extra:
        payload.update(extra)
    url = base.rstrip("/") + "/chat/completions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # ★ 重试：长请求上实测到过对端直接断连（RemoteDisconnected / SSL record layer failure），
    #   重发一次通常就好。只重试传输层错误与 429/5xx；
    #   4xx（除 429）是请求本身有问题，重试没用，直接报出来。
    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            body = json.loads(r.read().decode("utf-8"))
            ch = body["choices"][0]
            return ((ch["message"].get("content") or ""), ch.get("finish_reason"),
                    body.get("usage"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code == 429 or e.code >= 500:
                last = "HTTP %s：%s" % (e.code, detail)
            else:
                raise RuntimeError("HTTP %s：%s" % (e.code, detail))
        except Exception as e:                       # URLError / SSLError / 断连
            last = "%s: %s" % (type(e).__name__, str(e)[:200])
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise RuntimeError("重试 %d 次仍失败：%s" % (retries, last))


# --------------------------------------------------------------------- 官方 API
#
# 第三条路，**不经过 pi-ai、也不经过 daapsaak**。两个用处：
#   ① 判"失败在谁身上"——上游、profile、还是 pi-ai 那一侧；
#   ② 本身是可用的备选上游（daapsaak 时好时坏时有得换）。
#
# ⚠️ **命名坑**：`.local/接入凭证.env` 里的 `DEEPSEEK_API_KEY` 其实是 **daapsaak 的 key**
#    （因为 `resolve_key` 优先读本地覆盖，而覆盖文件里的 BASE 指向 daapsaak）。
#    ⇒ 要官方 key，必须**绕开本地覆盖**，直接从 `.dsh/.credentials.yaml` 取。
OFFICIAL_BASE = "https://api.deepseek.com/v1"
OFFICIAL_MODEL = "deepseek-flash"          # 官方 /models 实测报：deepseek-flash、deepseek-v4-pro
# ★ 两条路的模型名**不通用**：中转站叫 deepseek-v4.1-flash，官方只认上面两个。
#   少了这张表，直连官方就是 400（实测）。
OFFICIAL_ALIAS = {
    "deepseek-v4.1-flash": "deepseek-flash",
    "deepseek-chat": "deepseek-flash",
    "deepseek-v4.1-pro": "deepseek-v4-pro",
}



def official_key(name="DEEPSEEK_API_KEY"):
    """官方 API 的 key：**只从凭证文件取**，不走 `.local` 覆盖（见上面的命名坑）。"""
    return load_credential(name)


# --------------------------------------------------------------------- 进程接口
#
# dsh-llm-call 的 `node lib/bin.js`：stdin 一行一个请求，stdout 一行一个回复，
# 诊断走 stderr。请求严格按顺序处理，所以可以起一个常驻进程喂很多行，
# 启动开销只付一次。
#
# 路径可以用环境变量 DSH_LLM_CALL_BIN 覆盖；profile 用 DSH_LLM_CALL_PROFILE。
# dsh-llm-call 与本仓库同级（都在工作区那一层），所以从 ROOT 再上一层找。
WORKER_BIN = os.environ.get("DSH_LLM_CALL_BIN") or os.path.join(
    os.path.dirname(ROOT), "dsh-llm-call", "lib", "bin.js")


class Worker(object):
    """常驻的模型调用进程。

    ★ 只认一行一个 JSON。stderr 那几行诊断（适配器回退之类）留着，
      进程异常退出时是唯一能说明原因的线索。
    """

    def __init__(self, bin_path=None, node=None, profile=None, stderr_keep=40):
        self.bin = bin_path or WORKER_BIN
        if not os.path.exists(self.bin):
            raise IOError("找不到进程接口：%s（用 DSH_LLM_CALL_BIN 指定）" % self.bin)
        env = dict(os.environ)
        if profile:
            env["DSH_LLM_CALL_PROFILE"] = profile
        self.proc = subprocess.Popen(
            [node or "node", self.bin],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=os.path.dirname(self.bin), env=env, bufsize=0)
        self._out = queue.Queue()
        self.stderr = []
        self._keep = stderr_keep
        for target, stream in ((self._pump_out, self.proc.stdout),
                               (self._pump_err, self.proc.stderr)):
            t = threading.Thread(target=target, args=(stream,))
            t.daemon = True
            t.start()

    def _pump_out(self, stream):
        for line in iter(stream.readline, b""):
            self._out.put(line.decode("utf-8", "replace"))
        self._out.put(None)                     # 收尾：让等待方知道进程没了

    def _pump_err(self, stream):
        for line in iter(stream.readline, b""):
            self.stderr.append(line.decode("utf-8", "replace").rstrip())
            del self.stderr[:-self._keep]

    def call(self, request, timeout=3600):
        """发一行，等一行。返回解析好的 dict。"""
        if self.proc.poll() is not None:
            raise RuntimeError("进程接口已退出（码 %s）。stderr 末尾：\n%s"
                               % (self.proc.returncode, self.stderr_tail()))
        line = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()
        try:
            got = self._out.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError("等回复超时（%.0fs）。stderr 末尾：\n%s"
                               % (timeout, self.stderr_tail()))
        if got is None:
            raise RuntimeError("进程接口在回复之前退出（码 %s）。stderr 末尾：\n%s"
                               % (self.proc.returncode, self.stderr_tail()))
        return json.loads(got)

    def stderr_tail(self, n=8):
        return "\n".join(self.stderr[-n:]) or "（无）"

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()


_LOCAL = threading.local()


def worker(bin_path=None, profile=None):
    """取本线程的常驻进程；死了就重起一个。

    ★ 按**线程**隔离，不是按进程：批量跑要真并行，就得一个线程一个进程。
      （进程接口自己按顺序处理一行一行，同一个进程并发喂料不会更快。）
    """
    w = getattr(_LOCAL, "worker", None)
    if w is None or w.proc.poll() is not None:
        if w is not None:
            w.close()
        w = Worker(bin_path=bin_path, profile=profile)
        _LOCAL.worker = w
    return w


def close_workers():
    """收掉本线程的进程。批量跑完要显式收，否则子进程会挂着。"""
    w = getattr(_LOCAL, "worker", None)
    if w is not None:
        w.close()
        _LOCAL.worker = None



def worker_stream(messages, model=DEFAULT_MODEL, max_tokens=200000,
                  temperature=0.0, system=None, provider=None,
                  timeout=3600, bin_path=None, profile=None, raise_on_error=True):
    """经进程接口调一次。返回的 dict 与 `chat_stream` 同形状，便于换路。

    ★ 量不到的字段显式留 None（`first_s`、`rate`），并置 `rate_kind='平均'`。
      拿平均值冒充实测的稳态速率，是这个项目里反复出现的那种"看起来正常"。
    """
    req = {"model": model, "messages": messages, "maxTokens": max_tokens,
           "temperature": temperature}
    if system is not None:
        req["system"] = system
    if provider is not None:
        req["provider"] = provider

    t0 = time.time()
    got = worker(bin_path=bin_path, profile=profile).call(req, timeout=timeout)
    elapsed = time.time() - t0

    usage = got.get("usage") or {}
    pt = usage.get("promptTokens")
    billed = usage.get("completionTokens")
    rt = usage.get("reasoningTokens")
    content = got.get("content") or ""
    reason = got.get("reasoning") or ""
    err = got.get("error")

    if err and raise_on_error:
        raise RuntimeError("模型调用失败［%s］：%s" % (got.get("code"), err))

    # ★ 进程接口这条路**给不出思考的分项**（`reasoningTokens` 恒 null）——
    #   实测那是 pi-ai 那一侧的产物（模型没声明推理档位，适配器就不填），
    #   **不是路由的性质**：同一条上游走直连时，这个字段是有的。
    #   ⇒ 所以这里不假装知道：`reasoning_tokens` 留 None，思考按**字数**报（那是实测的）。
    est = billed is None
    return {
        "content": content, "reasoning": reason,
        "finish": got.get("finishReason"), "usage": got.get("usage"),
        "first_s": None, "chars": len(content),
        "rate": (billed / elapsed) if (billed and elapsed) else None,
        "rate_kind": "平均" if billed else None,
        "content_tokens": (max(billed - rt, 0) if (billed is not None and rt is not None)
                           else billed),
        "billed_tokens": billed,
        # ★ 思考分两档记，**不要把 0 和"没量到"混成一个 None**：
        #   实测踩过——把 `len(reason) or None` 当"思考长度"用，
        #   于是"这次它没思考"和"这条链路不给思考"看起来一样。
        "reasoning_chars": len(reason),
        "reasoning_tokens": rt,
        "prompt_tokens": pt,
        "estimated": est,
        "elapsed": elapsed, "via": "worker", "error": err,
    }


def call_model(messages, model=DEFAULT_MODEL, max_tokens=200000, temperature=0.0,
               via=None, system=None, provider=None, timeout=3600, extra=None, **kw):
    """统一入口。`via` 为 `worker` · `http`（daapsaak）· `official`（DeepSeek 官方）。

    ★ **缺省是 `http`（直连 daapsaak）**，不是 `worker`。理由（2026-10-09 实测）：
      两条路价格一样（同一个上游），而在一个坏窗口里
      **worker 4/4 失败、同一分钟的直连成功**；而且直连还给得到增量
      （首字延迟、稳态速率）与思考分项，worker 两样都不给。
      pikachu 弃用之后，进程接口**已经没有不可替代的能力**——所以它不再是缺省。
      ⚠️ 想改回按环境变量：`PALIMPSEST_LLM_VIA=worker`。

    ★ `extra` 是**直接塞进请求体的字段**（直连与官方都支持），用来显式设思考开关：
      `extra={"thinking": {"type": "disabled"}}`。
      ⚠️ **实测两条路的默认不一样**，不显式设的话同一格子的重复跑不是同一个条件：
        · daapsaak 直连**默认不思考**（实测 0 字、5 tok）
        · 官方**默认思考**（实测 2999 字、1121 tok）
        · 进程接口那条**会思考**（pi-ai 自己带了开启字段）
      ⇒ 进程接口**不转发** `extra`：给了就报错，不许静默忽略。
    """
    via = via or os.environ.get("PALIMPSEST_LLM_VIA") or "http"
    if via in ("official", "http"):
        base = OFFICIAL_BASE if via == "official" else None
        key = official_key() if via == "official" else None
        if via == "official":
            # ★ **两条路的模型名不一样**。实测官方的原话：
            #   "The supported API model names are deepseek-flash, deepseek-v4-pro,
            #    but you passed deepseek-v4.1-flash."
            #   ⇒ 直接拿中转站的名字打官方会 400。
            model = OFFICIAL_ALIAS.get(model, model)
        r = chat_stream(messages, model=model, base=base, key=key,
                        max_tokens=max_tokens, temperature=temperature,
                        extra=extra, **kw)
        r["via"] = via
        return r
    if via != "worker":
        raise ValueError("不认识的取数路径：%r（只有 worker / http / official）" % via)
    if extra:
        raise ValueError(
            "进程接口不转发这些字段：%r ⇒ 换 --via http（否则思考开关设不上，"
            "而两条路的默认又不一样）" % (extra,))
    return worker_stream(messages, model=model, max_tokens=max_tokens,
                         temperature=temperature, system=system,
                         provider=provider, timeout=timeout)


def normalize_annotation(obj):
    """把标注输出归一成约定的键名。认不出来就报错，不静默当空。

    实测模型稳定输出 `nodes`/`groups`/`edges`（而不是约定的中文键），
    引文键见过 `引文` 与 `text`。两套都认，是为了不让"认错键"变成"0 个节点"。
    """
    if not isinstance(obj, dict):
        raise ValueError("标注不是 JSON 对象：%r" % type(obj).__name__)

    def pick(*names):
        for n in names:
            if n in obj:
                return obj[n]
        return None

    nodes = pick("节点", "nodes")
    groups = pick("组", "groups")
    edges = pick("边", "edges")
    if nodes is None or groups is None:
        raise ValueError("标注缺字段（实际键：%s；需要 节点 与 组，或 nodes 与 groups）"
                         % list(obj.keys())[:12])
    # ★ `边` **可以缺**：v4 起标注不产出关系（依据 设计 3.6——标注只给出组）。
    #   这里不能把它当"缺字段"报错，否则提示词一改、读端就整批失败。
    if edges is None:
        edges = []
    # ★ `组说明` 是**记录层**的东西（设计 3.2：记录装"模型写下的理由与概念名"）。
    #   它不进图，所以也**可以缺**；缺了就是空。
    names = pick("组说明", "groupNames", "说明")


    def quote(n):
        if isinstance(n, str):
            return n
        for k in ("引文", "text", "quote", "内容"):
            if isinstance(n, dict) and k in n:
                return n[k]
        return ""

    def members(g):
        if isinstance(g, dict):
            for k in ("成员", "nodes", "members", "组"):
                if k in g:
                    return g[k]
            return []
        return g if isinstance(g, list) else []

    # 模型给节点起了 id（实测是 "n1"/"n2"…），组与边里引用的就是这些 id。
    # 折算成下标；折不出来的原样留着，由调用方在核验时报出来。
    id2idx = {}
    for i, n in enumerate(nodes):
        if isinstance(n, dict):
            for k in ("id", "编号", "标识"):
                if n.get(k) is not None:
                    id2idx[str(n[k])] = i

    def as_list(v):
        """模型有时给列表，有时给单个字符串。字符串不要当序列拆开。"""
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return list(v)
        return [v]

    def idxs(raw):
        out = []
        for m in as_list(raw):
            if isinstance(m, bool):
                out.append(m)
            elif isinstance(m, int):
                out.append(m)
            elif str(m) in id2idx:
                out.append(id2idx[str(m)])
            else:
                out.append(m)                       # 折不出来，原样留
        return out

    def edge_members(e):
        if not isinstance(e, dict):
            return as_list(e)
        for k in ("成员", "nodes", "members"):
            if k in e:
                return idxs(e[k])
        return idxs(as_list(e.get("from")) + as_list(e.get("to")))

    return {
        "节点": [{"引文": quote(n)} for n in nodes],
        "组": [{"成员": idxs(members(g))} for g in groups],
        "边": [{"成员": edge_members(e),
                "类型": (e.get("类型") or e.get("type") if isinstance(e, dict) else "")}
               for e in edges],
        "组说明": [x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
                   for x in as_list(names)],
    }


def require_keys(obj, keys, where="回复"):
    """核对取出来的 JSON 是不是约定的形状。

    ★ 必须显式核对，不能靠 `.get()` 取不到就静默当空：
      JSON 合法、`finish_reason=stop`、字段却是另一套名字时，
      取值会得到 0 个节点——**看起来像一个正常的空结果**。
      实测踩过：模型输出的是 `nodes`/`groups`/`edges`，而约定是 `节点`/`组`/`边`。
    """
    if not isinstance(obj, dict):
        raise ValueError("%s 不是 JSON 对象：%r" % (where, type(obj).__name__))
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError("%s 缺少约定的键 %s（实际键：%s）"
                         % (where, missing, list(obj.keys())[:12]))
    return obj


def fill_material(prompt, material, placeholder="<材料原文>"):
    """把材料填进提示词的占位符。

    ★ 填不进去就报错，不要静默返回原样：
    提示词里少了占位符时，模型收到的是"材料：（空）"，
    它会返回一个合法的空结果（0 个节点、finish=stop），**看起来一切正常**。
    这种失败必须在调用之前挡住。
    """
    if placeholder not in prompt:
        raise ValueError("提示词里没有占位符 %s —— 材料根本送不进去" % placeholder)
    return prompt.replace(placeholder, material)


def parse_json(text):
    """从回复里取出 JSON 对象。取不到就返回 None——不猜、不修。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except Exception:
            return None
    return None


# ================= 主人（以某角色为主人公） =================
OWNER = None
_原_fill_material = fill_material


def fill_material(template, material, **kw):
    # ★ 必须在**调用原函数之前**换：原函数内部就检查了
    #   "拼完之后还剩没换掉的占位符"（实测就是这样被拦住的）。
    if '<主人>' in template:
        template = template.replace('<主人>', OWNER or "（未指定主人）")
    return _原_fill_material(template, material, **kw)
