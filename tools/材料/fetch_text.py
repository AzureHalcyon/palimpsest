# -*- coding: utf-8 -*-
"""不用内置 web_fetch（那走 DeepSeek API，贵），自己发带真浏览器头的请求。

    python fetch_text.py <url> [<url> ...] [--max 40000] [--out DIR]

- 用 urllib（本项目一贯做法）+ 完整的浏览器请求头 + gzip/br 之外的常见编码
- 只依赖标准库
- ⚠️ 在受限沙箱里往工作区外写会被拒 ⇒ 默认只打印；要存盘就 --out 指向工作区内
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import re
import sys
import urllib.error
import urllib.request
import zlib

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Connection": "close",
}

TAG = re.compile(r"<(script|style|noscript|svg|head)\b.*?</\1>", re.S | re.I)
ANYTAG = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t\r\f\v]+")
NL = re.compile(r"\n{3,}")
ENT = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
       "&#39;": "'", "&mdash;": "—", "&ndash;": "–", "&hellip;": "…"}


def _decode(resp) -> str:
    raw = resp.read()
    enc = (resp.headers.get("Content-Encoding") or "").lower()
    if "gzip" in enc:
        raw = gzip.decompress(raw)
    elif "deflate" in enc:
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    ctype = (resp.headers.get("Content-Type") or "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    charset = m.group(1) if m else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def to_text(html: str) -> str:
    html = TAG.sub(" ", html)
    if "<body" in html.lower():
        html = html[html.lower().index("<body"):]
    html = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", html, flags=re.I)
    txt = ANYTAG.sub("", html)
    for k, v in ENT.items():
        txt = txt.replace(k, v)
    txt = WS.sub(" ", txt)
    txt = "\n".join(ln.strip() for ln in txt.splitlines())
    return NL.sub("\n\n", txt).strip()


def fetch(url: str, timeout: float = 45.0, tries: int = 3) -> str:
    last = None
    for a in range(tries):
        try:
            rq = urllib.request.Request(url, headers=dict(HEADERS), method="GET")
            with urllib.request.urlopen(rq, timeout=timeout) as resp:
                body = _decode(resp)
            if "html" in (resp.headers.get("Content-Type") or "").lower() or "<html" in body[:2000].lower():
                body = to_text(body)
            return body
        except urllib.error.HTTPError as e:
            last = "HTTP %s %s" % (e.code, e.reason)
            if e.code in (429, 500, 502, 503, 504) and a < tries - 1:
                import time
                time.sleep(2.0 * (a + 1))
                continue
            break
        except Exception as e:  # noqa: BLE001 网络层
            last = "%s: %s" % (type(e).__name__, e)
            if a < tries - 1:
                import time
                time.sleep(2.0 * (a + 1))
                continue
    return "!! 抓不到 %s —— %s" % (url, last)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--max", type=int, default=40000, help="每个 URL 最多打印多少字符")
    ap.add_argument("--out", default="", help="存盘目录（必须在工作区内）")
    a = ap.parse_args()
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    for u in a.urls:
        print("\n" + "=" * 100)
        print("### " + u)
        print("=" * 100)
        t = fetch(u)
        if a.out:
            name = re.sub(r"[^\w.\-]+", "_", u.split("//")[-1])[:120] + ".txt"
            p = os.path.join(a.out, name)
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(t)
                print("(已存 %s · %d 字符)" % (p, len(t)))
            except Exception as e:  # noqa: BLE001
                print("(存盘失败：%s)" % e)
        print(t[:a.max])
        if len(t) > a.max:
            print("… （截断，全文 %d 字符）" % len(t))
    return 0


if __name__ == "__main__":
    sys.exit(main())
