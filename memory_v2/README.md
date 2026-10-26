# memory_v2 —— 事件网络这一代

> 记忆层。上一代在 [`../archive/v1/`](../archive/v1/README.md)（格 / 件 / 组 / 势），冻结但可运行。
> 持续在线的循环在 [`../loop/`](../loop/README.md)：它用这里的 `read/write.py`（编写）、
> `read/activate.py`（检索）、`read/archive.py`（短期记忆到期归档），网页控制台也在那边。
> `chat/` 是旧的“一轮一回复”入口，`serve.py` 还在用它。
> 设计与理由见 [`../docs/事件状态与读取.md`](../docs/事件状态与读取.md)；
> 参数见 [`../docs/参数.md`](../docs/参数.md)；代码规矩见 [`CODE_STYLE.md`](CODE_STYLE.md)。

## 它是什么

- **单位是事件**：模型在对话里用 `event` 工具标出“正在说哪件事”，一轮可以标多个；
  每轮先判断值不值得记：不值得就 `skip`，名字盖不住就 `new`，还看不出是什么事就 `hold`
  （最多 6 轮，落盘时并进去）；名字写错能 `rename`，边连错能 `unlink`；
- **数据是网**：节点之间只有关联，没有父子、没有栈；旧数据里的 `属于` 按关联读；
- **读**：线索 → 种子 → 沿网和内容扩散 → 竞争 → 重建 → 编写；
- **归档**：不再有模型判断，只剩每轮落盘 ＋ 一场结束跑一次重放（REM-SWS：不均匀激活、加强、全局衰减、剪枝）；
  一场跑 3 遍、每遍重抽旧事件；旧事件跨场攒证据，攒到阈值才长出 replay 连接，
  被反复支撑的连接有强度底，一次性连接会衰减掉；
- **进上下文的只有编写出来的那段**，材料与候选都不进。
- **材料两种**：`loop/` 用 `remember` 写的 note；`chat/`、`serve.py`、App 用场次记录里的
  对话原文（旧入口，不再维护）。
- **检索建在 `name` 和 note 上**：向量和词面用这两样，场次记录里的对话原文不参与检索，
  只给“编写”用；所以挂新段不重算向量，写新 note 只重算那一件。
- **内容近邻一次算好**：`content-index.json` 存 top-256 近邻（按名字/词集/模型的指纹失效），
  扩散只走这张短表；装了 numpy 走矩阵，≥3000 节点且有 faiss 走 ANN，都没有就退回纯词面。

事件 id 是不含意义的随机串（`e-` ＋ 12 位十六进制），先后看 `created` 和 `seq`；
旧的 `e-0001` 仍是不透明 id，不迁移。

## 目录

| 路径 | 作用 |
|---|---|
| `core/` | 配置、模型调用、嵌入、文本、提示词加载 |
| `store/` | 图、逐轮状态、向量缓存、编写落档、回灌重建 |
| `read/` | 落点、扩散、竞争、编写 |
| `consolidate/` | 重放、加强、衰减、剪枝 |
| `chat/` | 一轮的循环与工具 |
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

# 升级/迁移：把当前 live 图与向量写成全局冻结版（旧数据升级、loop 启动时并进 live 图用）
python -m memory_v2 freeze

# 备份 / 恢复（包含 frozen、messages、说法与全局冻结）
python -m memory_v2 snapshot --out memory.psnap
python -m memory_v2 restore --snapshot memory.psnap --to <dir>

# 看图：把事件图画成一页 HTML（数据内嵌，浏览器直接打开）；--png 另出一张快照
python -m memory_v2 view --data data/memory
python -m memory_v2 view --data data/memory --label Rin --png

# 改图：把页面上攒的编辑清单落到 graph.json（循环在跑时会自己应用，这是离线时的入口）
python -m memory_v2 apply --data data/memory --file edits.json
python -m memory_v2 apply --data data/memory --file edits.json --dry-run
```

**改图走"清单"这条路**：页面是只读的（记忆图只有一个写者——循环进程），所以
改名 / 删事件 / 剪边都在页面上先攒成一份清单（选节点 → 面板里改名或删掉、
边列表里点 × 剪断 → 顶栏「存到服务器」）。**循环在跑就不用管**：`loop/driver.py`
的 `_apply_edits` 会在两步之间把 `<数据>/edits/*.json` 取出来应用，先备份
`graph.json.bak-<时间>`，落完挪进 `edits/applied/`。循环没在跑（或者想离线改）才用
`apply` 那个命令。`apply` 只改图、不动 `状态/*.jsonl`：`ingest` / `ingest --rebuild` 会照状态记录把
事件补回来，改名也只活在 `graph.json` 里。

`view` 只用只读地读 `<dir>/graph.json`（有 `content-index.json` 时会画内容近邻），
写出一页自带数据的 HTML；一页里能切「链接布局／语义投影」、按内容近邻条数过滤、
点节点做两跳扩散（权重与 `read/activate.py` 一致）。`networkx` 只用于社区划分、
`matplotlib` 只在 `--png` 时用，缺了也能出 HTML。

`chat --replay-on-exit` 是封口开关：聊完自动跑一次重放，不自动对别的场跑。
归档会把 live 图与向量写成下一版 `冻结/`。回忆读的是 live 图（本场和旧场都在里面），
规则只有一份：[`../docs/事件状态与读取.md`](../docs/事件状态与读取.md) 的“回忆”一节。
续聊加载 `messages.json`（含 tool calls）；旧未归档场没有
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
python check_all.py                   # 全部离线自测，CI 跑的就是这条
python memory_v2/tests/check.py       # 也可以单跑
python memory_v2/tests/replay_check.py
python -m loop.selfcheck              # 循环那层（不联网、不用凭证）
python tools/文档/check_docs.py
```

新增入口加进 `check_all.py` 的 `ENTRIES`。

`check.py` 不需要网络和凭证，模型调用走脚本桩；覆盖事件网络、一轮多事件、孤立节点、
同名按已有节点标、skip / hold / 挂起恢复、改名与断边、读取、编写、编写时不含正文、
重放、封口只读、坏图隔离、续聊与 messages 恢复、向量缓存维度、event 必须最后、随机 id 与 `seq` 排序。
`replay_check.py` 覆盖跨归档证据：一场不够就不建 replay 边、证据封顶、支撑只留最强几个。

## 目前的状态

- 实聊验证过两场：38 轮（林晚，装修）和 32 轮（周岚，工作/生活）；都是在场、一次一句、读回复再往下说；
- 跨会话问 10 题，两场都是 10/10，0 编造；假前提、未定项、具体日期都守住了；
- App 已接（走 `chat/` 旧入口）；`loop/` 现在是网页控制台那条路（设置 / 提示词 / 模型 / 主题
  都能在线改，上网工具已接），**跨多天连续跑还没做**；读取参数（网络赢家门槛、扩散权重）
  还是探针上定的；
- 单场小图重放长不出关联主干，这是种子密度的边界，不是读取坏了。
