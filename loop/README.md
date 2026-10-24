# loop —— 持续在线的循环运行时

设计见 [`../docs/持续在场-API实现.md`](../docs/持续在场-API实现.md)。
上下文是 sink + 四段旧摘要 + 两块原文（上一块、当前块）：当前块满了先升成上一块，
下一块满时才压上一块。历史交给 [`../memory_v2/`](../memory_v2/README.md)。

| 文件 | 作用 |
|---|---|
| `config.py` | 运行参数与凭证（DeepSeek 主模型、百炼摘要） |
| `context.py` | sink + 旧摘要 + 上一块 + 当前块；system + 一条记录 |
| `compress.py` | 压缩：一块一次，调摘要模型（默认 deepseek-flash） |
| `store.py` | SQLite：`bands` / `blocks` / `entries` / `recall_log` |
| `driver.py` | 步循环：工具、正文、停、攒块、预算 |
| `memory_bridge.py` | 工具面：remember / recall / recollect |
| `selfcheck.py` | 离线自检：假模型、假数据、临时库 |

## 跑

```powershell
python -m loop --name Rin --data data/loop --memory-data data/memory --max-steps 20
```

`--block-tokens` 调小可以让压缩早点发生（调试用）；`--park-seconds 0`（默认）表示
挂起就停，给了秒数才空转等待。`--say` 塞一条开场用户消息。摘要默认走
`deepseek-flash`，`--summary-model qwen-plus` 走凭证里的百炼端点。

## 现在的边界

- 记忆已接：`remember` 写 note（不进上下文）、`recall` 联想（默认只用百炼嵌入，
  词面用 `--lexical` 开）、`recollect` 由编写模型写成回忆；`short` 到期已做；
  最近 N 步是默认关的开关（`--recent-steps`）；去重/来源标不做；
- 冻结 / 重放只留 `python -m memory_v2 freeze/replay` 手工命令，不作为模型工具；
- 两块原文与旧摘要都落盘，重启能接上；
- 预算按两块原文 40k + 旧摘要 30k 估，默认 80k。

## 测试

```powershell
python -m loop.selfcheck
```
