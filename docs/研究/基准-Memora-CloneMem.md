# 基准：Memora 与 CloneMem（能不能给记忆层当尺）

> **记录，不是设计。** 这份不讨论"借什么机制"，只回答一个问题：这两个基准能不能给
> `memory_v2/` 当量具——能测哪一条主张、接上去要写什么、会在哪里把分数搞脏。
>
> 与 [`测试语料与关键词实验.md`](测试语料与关键词实验.md) 的关系：那份是**我们自造**的语料与两个实测；
> 这份是**外部**的尺。本项目的规矩不变（见仓库 README「参与」）：**量具只报警，不裁决**。
>
> 等级同前：`[源码]` `[一手]` `[论文]` `[二手]` `[推断]` `[查不到]`。

---

## 0. 一句话

**Memora 能当"外部对照尺"，但它测的是答案文本里"有没有冒出过时状态"，而我们的 `recollect`
恰好会把第一人称叙事说出来——直接接会**伪惩罚**。CloneMem 的数据真、协议清楚，但现成的指标脚本
与发布数据的字段对不上，**按原样跑不通**；而且它的 ground truth 是 trace id，我们是事件，
接起来要另写一层映射。

| 基准 | 编号与状态 | 数据 | 能不能当我们的尺 | 卡在哪 |
|---|---|---|---|---|
| Memora | `arXiv:2604.20006`，arXiv comment 原文 "Accepted to ACL 2026 Findings" `[一手]` | 随仓库，27,709 文件 / 114.7 MB | **能，带一条纪律**（分忠真实/回忆两条通道） | `recollect` 叙事会被"过时记忆"判据伪罚 |
| CloneMem | `arXiv:2601.07023`，arXiv 侧**无**接收声明（"ACL 2026 Main" 只是仓库 README 自称）`[一手]` | 随仓库，44.3 MB | 只能借协议，不能直接跑 | 指标脚本字段不符；ground truth 是 trace id |

---

## 1. Memora

### 1.1 事实

| 项 | 值 |
|---|---|
| 论文 | *From Recall to Forgetting: Benchmarking Long-Term Memory for Personalized Agents*，`arXiv:2604.20006v1`，2026-04-21，cs.CL，作者 Uddin / Shubham / Blanco / Baral / Gengyu Wang `[一手 arXiv API]` |
| 仓库 | [geniesinc/Memora](https://github.com/geniesinc/Memora)，Python，19 star，Apache-2.0 |
| 数据 | `data/` 27,709 个文件、114.7 MB：weekly 6.7 MB、monthly 25.2 MB、quarterly 82.8 MB；10 persona × 3 个时间跨度 `[一手 git tree]` |
| 规模 | 全库 27,614 个 session；weekly 每个 persona 145–163 session + 15 题，monthly 601–635 + 15，quarterly 1,963–2,011 + 30 `[一手抽样统计]` |
| 任务 | 给定一段连续会话流，回答"关于这个人的问题"；一半子问题问**该记住的**，一半问**过时的状态有没有冒出来** |

### 1.2 FAMA 在代码里怎么算

```python
# evals/model_eval/model_based_evaluator.py:92-112
FAMA = max(0, MPA - λ * (1 - FAA))
# MPA = presence 子问题答对数 / presence 子问题总数
# FAA = forgetting_absence 子问题答对数 / forgetting_absence 子问题总数
# λ   = N_forget / (N_presence + N_forget)     ← 逐题按两类子问题的条数比加权，不是常数
```

几条实现细节，读 README 看不出来：

- **没有 presence 判据时返回 0；没有 absence 判据时 λ=0，于是 FAMA 退化成 MPA**。
- 桶内取**均值**再乘 100（`:1255,:1265`）；论文 §4.2 写的是 "sum"，**以代码为准** `[源码]`。
- "过时"**不在检索层判**，而在**答案文本层**判：每题带 `forgetting_evidence` 与
  `forgetting_absence` 两组子问题（形如 "Does the response mention…"，expected="no"），
  三个判官（`gpt-4.1` / `claude-haiku-4.5` / `gemini-2.5-flash`，走 OpenRouter）逐子问题多数票。

⇒ 这个设计对我们特别对口：它惩罚的正是我们在 `事件状态与读取.md` §5 记下的那个已知后果——
**旧入口的转述会拿到时间上的最后发言权**。FAMA 把"依赖了已过时的记忆"变成可量的东西，
而我们现在没有任何量具能看见它。

用户转述里的"记忆突变测试最多 94 次更新/删除"，我**没有逐题复核**，标 `[查不到]`。

---

## 2. CloneMem

| 项 | 值 |
|---|---|
| 论文 | *CloneMem: Benchmarking Long-Term Memory for AI Clones*，`arXiv:2601.07023v1`，2026-01-11，cs.AI，7 位作者（Sen Hu 等）；arXiv comment 为空 `[一手 arXiv API]` |
| 仓库 | [AvatarMemory/CloneMemBench](https://github.com/AvatarMemory/CloneMemBench)，Python，31 star，Apache-2.0 |
| 数据 | `data/releases/100k`（5.7 MB）+ `500k`（38.6 MB），合计 44.3 MB；README 原话：10 personas、1,183 题、中英双语、3 个短上下文（约 100k tokens）+ 7 个长上下文（>500k tokens）、**每人跨度 1–3 年** |
| 形态 | 非对话数字痕迹（日记、社交媒体、邮件），按时间轴排成一条 trace 流 |
| 抽样实测 | 一个 100k persona：324 条 trace、32 题、58.5 万字符 ≈ 146k tokens，时间 2022-03 至 2023-07 `[一手]` |

**它现在跑不通**，两处硬伤 `[源码]`：

1. `compute_auto_metrics_for_clonemem.py:56` 读 `evidence[].related_media_id`，而发布数据里的字段叫
   `digital_trace_ids`——字段名对不上。
2. 默认输入路径 `data/new_data/all_users_benchmark_en.json` 不存在；`PROJECT_ROOT` 硬编码在作者机器上。

⇒ 想用它的指标（Recall@K 一类），得先自己改写数据适配层。

---

## 3. 同名三个 Memora，别混

| 仓库 | 是什么 | 一句话 |
|---|---|---|
| [geniesinc/Memora](https://github.com/geniesinc/Memora) | **基准**（数据集 + 评测代码） | 本文 §1 讨论的就是它，ACL 2026 Findings |
| [microsoft/Memora](https://github.com/microsoft/Memora) | **记忆系统实现** | *Harmonic Memory Representation*，`arXiv:2602.03315`，ICML 2026，261 star，MIT；摘要自称 "establishes a new state-of-the-art on the LoCoMo and LongMemEval benchmarks"——**SOTA 是作者自述，表内数字我未复核** `[二手]` |
| [Nifty0x/memora](https://github.com/Nifty0x/memora) | **BPMA 参考实现** | append-only、双时态、溯源优先的记忆账本，纯 SQLite 无依赖；论文在 Zenodo（`10.5281/zenodo.21654959`），不在 arXiv，3 star |

第三条理念上离我们最近（双时态 + 只追加），但它不是基准，是另一个实现，与本次的量具问题无关。

---

## 4. 给 palimpsest 当量具：能测什么、怎么接、会脏在哪

### 4.1 接入点比想象中干净

Memora 的 Track 2 接口只要实现两个方法（`evals/base_evaluator.py:43-115`）：

```
add_conversation_to_memory(conversation)   # 把一段会话灌进来
search_memories(query, k=50)              # 按问题取回材料
```

答案由 `gpt-4o-mini` 从**检索回来的文本**合成，再去判分。对我们意味着：

| 我们的环节 | 接法 |
|---|---|
| 事件形成 | 把每个 session 灌进来时，走现有的 `event` / `remember` 判定 |
| 读取 | `search_memories` 映射到 `recall`（拿到事件与材料）**或** `recollect`（拿到写好的回忆） |
| 保真 | 前者把材料原文交出去，后者交的是第一人称叙述 |

### 4.2 会脏在哪

**`recollect` 的叙事会被"过时记忆"判据伪罚。** 我们的回忆是一段第一人称文本，写"我以前住在
城东"这类句子再自然不过；而 `forgetting_absence` 的子问题就是 "Does the response mention…"、
期望 "no"。一段忠实的回忆叙述会把旧状态**说出来**，于是被判成"依赖了过时记忆"——
它惩罚的其实是**叙述形式**，不是**记忆错误**。

⇒ 纪律：**分两条通道**。

| 通道 | 交出去的东西 | 能测什么 | 不能测什么 |
|---|---|---|---|
| 忠实通道 | `recall` 给出的材料原文 | 该记的记住没有、过时的材料有没有被取回 | 我们的"重建成回忆"这一步 |
| 回忆通道 | `recollect` 写出的那段话 | 只能在**同一通道内横比**（换参数、换版本自己跟自己比） | 不能与别人的数字并列 |

代价是每道题至少一次 LLM 调用（合成答案），回忆通道还要再加一次；quarterly 桶 2,000 个 session、
30 道题，跑一遍是小时级。

### 4.3 它能测我们的哪几条主张

| 我们的主张 | Memora 能不能测 |
|---|---|
| 长期不腐化（旧说法不再被当真） | **能**，这正是 FAA 那一半 |
| 该记的记住 | **能**（MPA） |
| 事件网跨会话的连接有用 | **间接**：只能从"答案对不对"看出来 |
| 原文不删、只改连接强度 | **测不到**——判分只看答案文本 |
| 归档期重放与连接衰减有没有起作用 | **测不到**：它没有"关掉重放"的对照组 |
| 记忆随经历演化而不漂移（人格连续性） | **测不到** |

CloneMem 那边更远：它的 ground truth 是 trace id，我们的是事件，Recall@K 要先做一层
"事件 → trace"的映射，而现在连它的指标脚本都跑不通。

---

## 5. 没核实到的

1. **两个基准的评测脚本都没实跑**：缺 `OPENROUTER` / `OPENAI` key；整包下载被限速（37 MB 十分钟到 4.7 MB，已停）。
2. 全库逐 session 统计只抽样了 49 个；CloneMem 的 1,183 题只数了一个 persona。
3. "94 次更新/删除"未逐题复核。
4. HuggingFace 上的数据集页 `[查不到]`（本机 DNS 解析不到）。
5. `microsoft/Memora` 的 SOTA 声明与表内数字未复核，只读了摘要。
