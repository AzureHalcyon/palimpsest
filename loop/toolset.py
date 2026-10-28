"""工具集：把「一组工具」包成循环唯一认识的那个形状。

循环（`driver.Loop`）只负责两件事——**有哪些工具**、**按名字调哪一个**。它不认识
`fetch`、`run`、`write` 是谁，也不认识它们要什么参数：那些归各工具模块，由入口
（`cli.py`）组装进来递给它。这样引擎与「接进某个具体 AI 的那一圈」之间只有一条
**单向**的边：入口 → 引擎；引擎不反向认识入口那侧的实现。

一个工具集只要满足三件事：

- `tools() -> list[dict]`：给模型的 schema 列表（原生 tool_calls 那套格式）
- `call(name, arguments) -> str`：按名字调，返回要写进记录的一段文字
- `owns(name) -> bool`：这个名字是不是我的

`ModuleToolset` 是对「模块级 `tools()` / `call()` / `NAMES`」的适配——现存三个工具
模块（`webtools` / `shell` / `workspace`）都是这个形状，`MemoryBridge` 则是直接满足
上面三条的类，不需要适配。

⚠️ `default_toolsets()` 是**唯一**知道「仓库自带哪几个工具模块」的地方。把工具搬去
另一侧时，只改这一个函数；`driver.py` 不用动。
"""

from __future__ import annotations

from typing import List


class ModuleToolset:
    """把模块级的三个约定包成一个工具集。

    `config` 为 `None` 时按不带参数的签名调（`webtools` 就是这样）；否则把 `config`
    作为最后一个位置参数传下去（`shell` / `workspace` 要它来定工作区和超时）。
    """

    def __init__(self, module, config=None):
        self.module = module
        self.config = config

    def owns(self, name: str) -> bool:
        return name in tuple(getattr(self.module, "NAMES", ()))

    def tools(self) -> List[dict]:
        if self.config is None:
            return list(self.module.tools())
        return list(self.module.tools(self.config))

    def call(self, name: str, arguments: str) -> str:
        if self.config is None:
            return self.module.call(name, arguments)
        return self.module.call(name, arguments, self.config)


def make_toolsets(config, modules) -> List[ModuleToolset]:
    """按给定的模块列表组装。核心自己一个工具都没有，模块全由入口递进来。"""
    return [ModuleToolset(module, config) for module in modules]
