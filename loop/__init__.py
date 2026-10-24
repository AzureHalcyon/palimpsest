"""持续在线的循环运行时。

上下文（sink + 五段 + 当前块）在运行时压缩，历史交给 memory_v2；
设计见 `docs/持续在场-API实现.md`，入口 `python -m loop`。
"""
