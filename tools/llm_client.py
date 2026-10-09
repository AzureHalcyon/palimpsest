# -*- coding: utf-8 -*-
"""OpenAI 兼容端点的最小客户端。只用标准库。

约定与纪律：
  · 每次调用都把 `max_tokens` 与**空回复数**报出来（推理模型配额被 reasoning 吃光时，
    正文会是空的，`finish_reason=length`）。
  · 不打印密钥。
  · 读到的响应原样返回，不做"尽力而为"的修补。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.daapsaak.top/v1"
DEFAULT_MODEL = "deepseek-chat"
CRED = r"C:\Users\AzureHalcyon\.dsh\.credentials.yaml"
# 本机覆盖：格式 KEY=VALUE，用于指定某个端点的 key 与地址。不进版本控制。
LOCAL_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         ".local", "接入凭证.env")


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


def chat_stream(messages, model=DEFAULT_MODEL, base=None, key=None,
                max_tokens=4000, temperature=0.0, timeout=600,
                retries=3, backoff=4, extra=None, on_delta=None):
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
                if d.get("reasoning_content"):
                    reason.append(d["reasoning_content"])
                if d.get("content"):
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
            ct = u.get("completion_tokens") or len(body)
            rt = u.get("reasoning_tokens") or (
                (u.get("completion_tokens_details") or {}).get("reasoning_tokens"))
            if rt is None:
                rt = sum(len(x) for x in reason)
            return {"content": body, "reasoning": "".join(reason),
                    "finish": finish, "usage": usage, "first_s": first,
                    "chars": len(body), "rate": rate,
                    "content_tokens": len(body), "reasoning_tokens": rt,
                    "elapsed": time.time() - t0}
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
    if nodes is None or groups is None or edges is None:
        raise ValueError("标注缺字段（实际键：%s；需要 节点/组/边 或 nodes/groups/edges）"
                         % list(obj.keys())[:12])

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
