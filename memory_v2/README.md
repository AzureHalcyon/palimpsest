# memory_v2 —— 事件网络这一代

> 当前链路。上一代在 [`../archive/v1/`](../archive/v1/README.md)（格 / 件 / 组 / 势），冻结但可运行。
> 设计与理由见 [`../docs/事件状态与读取.md`](../docs/事件状态与读取.md)；
> 参数见 [`../docs/参数.md`](../docs/参数.md)；代码规矩见 [`CODE_STYLE.md`](CODE_STYLE.md)。

## 它是什么

- **单位是事件**：模型在对话里用 `event` 工具标出“正在说哪件事”，一轮可以标多个；
- **数据是网**：节点之间只有关联，没有父子、没有栈；旧数据里的 `属于` 按关联读；
- **读**：线索 → 种子 → 沿网和内容扩散 → 竞争 → 重建 → 编写；
- **归档**：不再有模型判断，只剩每轮落盘 ＋ 一场结束跑一次重放（REM-SWS：不均匀激活、加强、全局衰减、剪枝）；
- **进上下文的只有编写出来的那段**，原文和候选都不进。

事件 id 是不含意义的随机串（`e-` ＋ 12 位十六进制），先后看 `created` 和 `seq`；
旧的 `e-0001` 仍是不透明 id，不迁移。

## 目录

| 路径 | 作用 |
|---|---|
| `core/` | 配置、模型调用、嵌入、文本、提示词加载 |
| `store/` | 图、逐轮状态、向量缓存、编写落档、回灌重建 |
| `read/` | 落点、扩散、竞争、编写 |
| `consolidate/` | 重放、加强、衰减、剪枝 |
| `chat/` | 一轮的循环、工具、状态快照 |
| `prompts/` | 常驻（persona）、事件契约、sys2、编写 |
| `cli.py` | 命令行 |
| `serve.py` | 一句一发的 HTTP 入口 |

## 命令

```powershell
# 聊一场（交互）
python -m memory_v2 chat --credentials <yaml> --data <dir> --session <id>

# 用脚本跑（一次性；不是真对话，只用来冒烟）
python -m memory_v2 chat --script <file> --replay-on-exit ...

# 聊完对这场做一次巩固
python -m memory_v2 replay --session <id> --credentials <yaml>

# 查 / 看 / 改
python -m memory_v2 recall --keywords "空调,预算"
python -m memory_v2 show --session <id>
python -m memory_v2 events
python -m memory_v2 edges
python -m memory_v2 mark --event <id>
python -m memory_v2 detach --event <id> --last
python -m memory_v2 unlink --first <id> --second <id>

# 从状态记录补齐（可重复跑）
python -m memory_v2 ingest

# 整体重建：清空当前图，旧图先备份；重放边、强度、奖励与手工修改不恢复
python -m memory_v2 ingest --rebuild

# 升级/迁移：把当前 live 图与向量写成全局冻结版（之后新场从这里开场）
python -m memory_v2 freeze

# 备份 / 恢复（包含 frozen、messages、说法与全局冻结）
python -m memory_v2 snapshot --out memory.psnap
python -m memory_v2 restore --snapshot memory.psnap --to <dir>
```

`chat --replay-on-exit` 是封口开关：聊完自动跑一次重放，不自动对别的场跑。
归档会把 live 图与向量写成下一版 `冻结/`。本场 recall 直接读 live 图（含本场和旧场），
不再依赖 `状态/<场>.frozen/`。续聊加载 `messages.json`（含 tool calls）；旧未归档场没有
messages 日志时，按 `状态` 里的 user/assistant 重建，无法保证工具上下文逐字一致。

## 一句一发的入口

给外部代理用；进程常驻，对话状态在内存里，一次发一句：

```powershell
python -m memory_v2.serve --data <dir> --session <id> --credentials <yaml> --port 8765
```

```powershell
$body = '{"text":"最近在弄装修，预算二十八万。"}'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/send' `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

返回 `reply`，外加这一轮的 `events`（多个事件）。
`GET /state` 看整场；`POST /close` 跑这一场的重放。

## 测试

```powershell
python memory_v2/tests/check.py
python tools/文档/check_docs.py
```

`check.py` 不需要网络和凭证，模型调用走脚本桩；覆盖事件网络、一轮多事件、孤立节点、
同名拒绝、读取、编写、重放、封口只读、坏图隔离、续聊与 messages 恢复、向量缓存维度、
event 必须最后、随机 id 与 `seq` 排序。

## 目前的状态

- 实聊验证过两场：38 轮（林晚，装修）和 32 轮（周岚，工作/生活）；都是在场、一次一句、读回复再往下说；
- 跨会话问 10 题，两场都是 10/10，0 编造；假前提、未定项、具体日期都守住了；
- 还没接 App；还没做长期（跨多天）验证；读取参数（网络赢家门槛、扩散权重）还是探针上定的；
- 单场小图重放长不出关联主干，这是种子密度的边界，不是读取坏了。
