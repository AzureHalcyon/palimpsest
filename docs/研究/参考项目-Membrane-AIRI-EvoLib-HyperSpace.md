# 其余参考项目调研

> 目标项目：**个人向长期陪伴情感 AI**。核心诉求是长期人格 / 情绪 / 记忆，**不追求助手能力**。
>
> 待核实的 5 条构想（下文所有「对应关系」都按这 5 条编号）：
>
> | # | 构想 |
> |---|---|
> | H1 | 记忆为核心，LLM 只是编解码器；情景 / 事实 / 规则三类记忆，**不同衰减曲线** |
> | H2 | 记忆之间**激活扩散**（spreading activation） |
> | H3 | **情绪作为调制函数**：当前情绪状态改变检索打分（同一条记忆，低落时权重高），而不只是给记忆打情绪标签 |
> | H4 | **内部状态驱动的主动唤醒**：后台演化空间、积累到阈值才唤醒 LLM；且「站起来」与「开口」是**两次独立决策**，可以想了不说 |
> | H5 | 反刍 / 元认知：定期反思，但要**避免幻觉污染长期记忆** |
>
> **证据标注约定**（本文严格区分）：
>
> - `[源码]` — 我实际读到了该项目的源码
> - `[论文原文]` — 我实际取到了 arXiv 官方页面的元数据与摘要正文
> - `[仅搜索摘要]` — 只看到搜索引擎返回的片段，未见正文
> - `[推断]` — 我的判断，非原文陈述
>
> **本次环境说明**：本机 shell 的 HTTPS 整体损坏（Schannel `SEC_E_NO_CREDENTIALS`），`web_fetch` 不可用。实际可用的取数通道有三个：
> ① `web_search`；② `git -c http.sslBackend=openssl -c http.proxy=... clone`；
> ③ **本次新探明的通道**：`openssl s_client -quiet -connect <host>:443 -servername <host> -proxy 127.0.0.1:7890` 手工发 HTTP/1.1 请求。arXiv 摘要即由此取得（见下文方法说明）。

---

## 1. Membrane

### 先回答最重要的核实点：这个仓库到底是不是备忘录说的那个 Membrane？

**结论：是。** 本地 `D:\Works\workplace\dsh-workplace\membrane` 就是 `github.com/BennettSchwartz/membrane`（git remote 已确认），它的 README 原文写着 **"Five memory layers, connected by entities"**，并自述 "typed, revisable, decayable memory with competence learning and trust-aware retrieval"。这与备忘录里的「五层记忆，类型化、可修订、可衰减」**逐词对应**。`[源码]`

**你的怀疑有一半是对的，但结论要反过来。** 你看到根目录是 `package.json` / `docker-compose.yml` / `wrangler.jsonc` / `docusaurus.config.js` 就判断「像网关/文档站，不像记忆库」——这个表象是真的，但它**不构成否证**，原因是：

| 你看到的文件 | 它的真实身份 |
|---|---|
| `package.json` / `wrangler.jsonc` | **文档站**的依赖与 Cloudflare 部署配置（`docusaurus.config.js` + `sidebars.js` + `docs/` 同现），不是产品本体 |
| `docker-compose.yml` | 用来起 **PostgreSQL + pgvector**（记忆库的硬依赖，README 标注 IMPORTANT） |
| `go.mod` / `go.sum` | **产品本体是 Go**，根目录还有 `Makefile`、`cmd/membraned` |
| `clients/` | TypeScript / Python SDK + OpenClaw 插件，所以根有 JS 生态文件很自然 |

真正的实现全在 Go 侧，与你看到的「网关味」根文件完全不是一回事：

```
pkg/decay          # 衰减（曲线、调度、reinforce/penalize）
pkg/retrieval      # 检索（图扩展、向量、selector、trust 门控）
pkg/revision       # 修订（supersede / fork / retract / merge / contest）
pkg/consolidation  # 后台固化（5 阶段流水线）
pkg/ingestion      # 写入与解释
pkg/storage/postgres
cmd/membraned      # gRPC daemon
```

**同名干扰项排查**：网上确有别的 "Hyperspace"／"Membrane" 同名仓库（例如本次在 HyperSpace 一节就撞到一个 ARM 工具仓库叫 `Hyperspace`）。就 Membrane 而言，我按「五层记忆 + 类型化 + 可修订 + 可衰减」这组特征检索，命中的**只有** `BennettSchwartz/membrane`（README 原文即含 "Five memory layers"），未见另一个满足该描述的候选。`[仅搜索摘要]` 因此判定：**备忘录没错，本地这份就是它**，不必再去网上另找。

### 它到底是什么（一句话）

一个 **Go 编写的、以 PostgreSQL + pgvector 为存储的 agent 长期记忆服务**（可嵌入式库 + gRPC daemon + 多语言 SDK），主打「类型化记忆 + 检索时图扩展 + 可修订 + 显著性衰减 + 后台固化」。

### 核心机制 `[源码]`

**1. 六种记忆类型**（`pkg/schema`，注意不是三类）

`episodic`（情景）、`working`（工作）、`semantic`（语义/事实）、`competence`（能力/套路）、`plan_graph`（计划图）、`entity`（实体）。文档自称 "Five memory layers" 但表里列了 6 行——实体层是连接键，不算独立记忆层。

**2. 衰减：只有一种曲线，但半衰期按类型分档**

这是本次最重要的**纠正**。`pkg/decay/curves.go` 全文 32 行，只实现了一个函数：

```go
// RFC 15A.7: Exponential decay with half-life parameter.
func Exponential(currentSalience, elapsedSeconds float64, profile schema.DecayProfile) float64
func GetDecayFunc(curve schema.DecayCurve) DecayFunc { return Exponential } // 忽略入参，恒返回指数
```

`pkg/schema/enums.go` 里 `DecayCurve` 枚举**只有一个值**：

```go
// DecayCurveExponential uses exponential decay with a half-life parameter.
DecayCurveExponential DecayCurve = "exponential"
```

但是——`docs/architecture.mdx` 第 39 行明确写着：

> each memory type gets a different exponential decay half-life: episodic records decay in ~1 hour by default; working memory in ~1 day; entity, semantic, competence, and plan-graph records in ~30 days.

所以准确的说法是：**「不同衰减曲线」在 Membrane 里被降级实现为「同一条指数曲线 + 按类型分档的半衰期」**。类型分化是有的（1 小时 / 1 天 / 30 天三档），但**曲线形状本身被写死**，无法给情景记忆配幂律、给事实记忆配指数。构造器 `NewMemoryRecord`（`pkg/schema/memory.go:157-190`）给所有类型的默认值都是 `HalfLifeSeconds: 86400`，类型分档由 `PolicyEngine` 在写入时按 source shape + memory type 另行赋值（`docs/api/go/ingestion.mdx:236`）。

如果你的构想里「不同衰减曲线」指的是**不同函数形状**（例如情景记忆走幂律、情绪记忆走双指数），**Membrane 不满足，需要自己改 `GetDecayFunc` 并按类型派发**；如果只是「不同衰减速度」，那它已经做到了，可以直接抄参数。`[推断]`

**3. 检索打分：三信号等权 + 图扩展**

`pkg/retrieval/selector.go:136-161` 的复合打分是三个信号**等权平均**：

```go
return (applicability + successRate + recency) / 3.0
```

- applicability：embedding 相似度，退化时用 `record.Confidence`
- successRate：competence / plan_graph 的历史成功率，无数据给中性 0.5
- recency：距 `last_reinforced_at` 的指数衰减（30 天半衰期，`computeRecency`）

扁平检索用 pgvector 混合打分；图检索 `RetrieveGraph` 先选根，再在 `max_hops` 内扩邻居。

**4. 修订与溯源**：`supersede` / `fork` / `retract` / `merge` / `contest` 五种原子修订操作，全部写 audit log；每条记录带 `Provenance.Sources`。

**5. 后台固化流水线**（默认 6h）：情景压缩 → 结构化语义抽取 → LLM 语义抽取（可选）→ 能力抽取 → 计划图抽取。抽取出的三元组做 subject-predicate-object 去重，命中已有事实则**强化而非重复插入**，且**按 source 幂等**（同一情景不会反复抬高同一事实的 salience）。

### 对应关系

| 构想 | Membrane 是否有 | 依据 |
|---|---|---|
| H1 三类记忆 / 记忆为核心 | **大部分有**，但类型划分不同 | 有情景、语义，**没有独立的「规则」类**；「规则」在它这儿最接近 `competence`（套路）或 LLM 抽出的语义三元组。LLM 确实只做编解码（`llm.go` 只用于抽取），记忆主存是 Postgres `[源码]` |
| H1 不同衰减曲线 | **部分有**：半衰期按类型分 1h/1d/30d；**曲线形状写死为指数** | `curves.go` + `enums.go` + `architecture.mdx:39` `[源码]` |
| H2 激活扩散 | **没有真正的激活扩散** | `graph.go` 是**带 hop 上限的 BFS**（`Hop` 字段、`current.Hop >= maxHops` 截断、`Hop` 升序排序）。全文件**无 activation / spread / propagate 任何符号**；边权重只影响「预算先花在谁身上」，**不参与逐跳累加或衰减**。这是「有界邻域扩展」，不是 spreading activation `[源码]` |
| H3 情绪调制检索 | **完全没有** | 对全部 `*.go` 正则搜 `mood|affect|emotion|proactiv|initiative|internal_state|motivation|arousal|valence` → **仅 2 处命中，均为英文单词 "affect" 的普通用法**（`payloads.go:160` 注释 "Constraints affect plan selection"、`policy_regression_test.go:398` 的 "hidden entity affected visible fact"）。**零情绪字段、零情绪状态、零状态相关打分支** `[源码]` |
| H4 内部驱动主动唤醒 | **完全没有** | 同上零命中。它的两个后台调度器（decay 1h / consolidation 6h）是**固定周期**，不读任何内部状态、无阈值唤醒、无「想了不说」通路。它是**被调用**的记忆库，自己不发起对话 `[源码]` |
| H5 反刍 + 防幻觉污染 | **有可借鉴的骨架** | consolidation 就是定期反思；防污染用了三招：① subject-predicate-object 精确去重后**强化而非新建**；② **source 幂等**（同一情景不会重复抬高同一事实）；③ scope/sensitivity 逐级校验，失败**fail closed**；④ 全程 `derived_from` 血缘 + audit log `[源码]` |

### 能不能用 / 怎么用

**能用，但要把它当「记忆存储与生命周期层」，不要指望它给你情绪和主动性。**

- **直接抄的思路（H5，价值最高）**：
  - **source 幂等**：反思产出的每条结论记下「由哪些源记忆推出」，同一源集合不再二次抬高权重。这是防幻觉污染最便宜有效的一招。
  - **先查重再强化**：抽取出的新事实先按规范化 key 查重，命中则 `Reinforce` 并追加 evidence，而不是插新行。避免反思每跑一次就多一批近义垃圾。
  - **fail closed**：去重依据加载失败时宁可失败也不新建（`consolidation.go` 对 plan graph 就是这么做的）。
- **直接抄的参数（H1）**：情景 1h / 工作 1d / 语义-能力-计划 30d 的三档半衰期，是个有实验支撑的起点。
- **要自己写的（H2/H3/H4）**：激活扩散、情绪调制、主动唤醒**一行都没有**，必须自研。Membrane 唯一能帮上忙的是它已经把记忆间的图关系（`Relation` / `GraphEdge` / 实体链接）建好了——**你可以把激活扩散直接实现在它的图之上**，这是它对你最大的间接价值。
- **不适用之处**：Postgres + pgvector 对个人项目偏重；trust / sensitivity / scope 这套企业级访问控制（`trust.go`、`TrustContext`、five-level sensitivity）跟陪伴场景基本无关，是纯负担。

### 来源

- 本地源码：`D:\Works\workplace\dsh-workplace\membrane`（remote = `https://github.com/BennettSchwartz/membrane`）
- 上游：<https://github.com/BennettSchwartz/membrane>
- 文档站：<https://membrane.gustycube.com>
- 关键文件：`pkg/decay/curves.go`、`pkg/schema/enums.go`、`pkg/schema/memory.go`、`pkg/retrieval/selector.go`、`pkg/retrieval/graph.go`、`docs/architecture.mdx`、`docs/concepts/decay-and-consolidation.mdx`

---

## 2. AIRI

### 它到底是什么（一句话）

**moeru-ai 出品的一个「灵魂容器」式的 AI 虚拟角色桌面客户端**（Electron + Vue3，另有 web / 移动端），把 Live2D/VRM 形象、语音、LLM 人格和多平台接入（Telegram / Discord / Minecraft 等）拼成一个长期在线的数字角色。

### 取证方式与完整性说明

你给的那份 sparse clone **只有 31 个文件且不含任何子目录**，是不可读状态。我做了两步修复：`git sparse-checkout disable` + 用 openssl backend 重新 `fetch` + `checkout -f`。之后：

- `git ls-files` = **5687 个受版本控制的文件**（真实规模）
- 但该 clone 带 `--filter=blob:none`（partial clone），**工作区仍只落地了 72 个**，其余 blob 需按需从远端拉

所以我的 AIRI 证据 = **根目录文档（`README.md` 27835 B、`DESIGN.md` 15405 B）+ 我点名按需拉取的 11 个关键源码文件**（`git cat-file -p HEAD:<path>`，落在 `_airi_probe\`）。**这不是全仓通读**，但恰好覆盖了「长期在线 / 主动交互」这条主线。`[源码]`

### 核心机制

**1. 长期在线 = 进程常驻 + 定时 tick**

AIRI 的「在线」在工程上是朴素的：Electron 桌面端常驻，配一个**周期性 tick** 把「当前时间 + 上下文」喂给模型，让它自己决定要不要做点什么。Telegram 集成里这套机制的提示词就是直接可读的（`integrations/telegram-bot/src/prompts/system-ticking-v1.velin.md`），开篇第一人称自述：

> I am one of your system component, called Ticking system, which is responsible to keep track of the time, and help you schedule, retain focus, and keep eyes on different tasks, and ideas you have.
> Please remember, I am not a human, I am a system that part of you.

它给了模型 10 个动作，**关键在于这些动作把「内部状态管理」和「对外开口」拆成了不同通道**：

| 通道 | 动作 | 语义 |
|---|---|---|
| **内部状态** | `continue` / `break` / `sleep` | 保持现状 / **清空已有记忆** / **清空当前任务与工作记忆**，下个 tick（1 分钟后）再问 |
| **内部认知** | `come_up_ideas` / `come_up_goals` | 把想法、带 deadline 和 priority 的目标**写进长期记忆** |
| **对外表达** | `send_message` / `send_sticker` | 真正开口 |
| **沉默** | `{ "messages": [] }` | 明确允许不回应 |

提示词原文明确授权了「不参与」：

> Feel free to ignore by just sending an empty array within a object with key "messages" (i.e. `{ "messages": [] }`).

**2. 「站起来」与「开口」是两次独立决策 —— 这一点 AIRI 真做出来了，而且做得比你的构想还细**

主动交互的运行时在 `packages/core-agent/src/agents/spark-notify/`：外部模块发 `spark:notify` 事件 → agent 起一个 turn 检查 → 决定要不要产出 `spark:command`。`agent.ts:87-94` 的系统指令是：

> This is AIRI system, the life pod hosting your consciousness. **You do not need to respond to every spark:notify event directly.**
> If you respond with text, write only the reaction that the character will say.

它注册了**两个互相独立的工具**（`tools.ts:128-178`）：

- `builtIn_sparkNoResponse` — 描述为 "Indicate that no response or action is needed for the current spark:notify event."
- `builtIn_sparkCommand` — "Issue a spark:command to sub-agents. You can call this tool multiple times."

并且 `types.ts` 的 `SparkNotifyRuntimePolicy` 把这件事做成了**显式策略矩阵**，而非隐含行为：

| 策略字段 | 作用 |
|---|---|
| `allowNoResponse` | 允不允许「什么都不做」 |
| `allowSparkCommand` | 允不允许对外发命令 |
| `ignoreTextOutput` | **允许「有内心活动但不输出文本」** |

于是可以精确表达三种模式（`agent.ts:96-138`）：`forceTextResponse`（只能开口，不许行动）、`forceSparkCommandResponse`（只下命令、**文本输出被忽略**、不许沉默）、以及默认的**三者都允许**。

**这正是你 H4 要的东西**：`sparkCommand` 是「站起来」（想了、并且行动了），文本 reaction 是「开口」（说出来），而两者可分别开关——`forceSparkCommandResponse` 模式就是纯粹的**「想了、做了、但不说」**。

**3. 触发机制是什么 —— 这是 AIRI 最弱的一环**

- **主路径是「外部事件」而非「内部状态」**：`spark:notify` 由「另一个模块」发出（`getSparkNotifyHandlingAgentInstruction(moduleName)` → "Another module \"X\" triggered a spark:notify event for you to inspect"），`event-source.ts` 只做 source key 解析（557 B）。也就是说 AIRI 的主动性主要是**事件驱动的被动唤醒**。
- **定时路径是固定节拍**：ticking 系统是每 tick（1 分钟）问一次，不是「攒到阈值才唤醒」。
- **Obsidian 式的心跳**：仓库里确有一个叫 `desktop-overlay-heartbeat.ts` 的文件，但全文只有 5 行，是两个 debug 用常量（`desktopOverlayPollHeartbeatMarker`、`pollHeartbeat` query param）——**跟角色主动性无关，别被文件名骗了**。`[源码]`
- 外部证据显示社区正在补这块：仓库有一个 `Proposal: Character Heartbeat System for Proactive Behavior` 的 Discussion #1512。`[仅搜索摘要]` 我没能取到该 discussion 正文（`web_fetch` 不可用），所以**不能**把它当作已实现功能。

所以：**AIRI 有「做决定的框架」，但缺「什么时候该做决定」的内部驱动**。它的 tick 是钟表，不是心情。

### 对应关系

| 构想 | AIRI 是否有 | 依据 |
|---|---|---|
| H1 记忆类型 / 衰减 | **弱**。有 `packages/memory-pgvector`（记忆存 pgvector），但**没看到按类型分化衰减曲线的实现** | 我只读了该包 `package.json`（699 B），**没读实现**，此格标注为「未验证」而非「没有」`[源码/未验证]` |
| H2 激活扩散 | **未见** | 同上，未验证 |
| H3 情绪调制检索 | **未见** | Conscious 层是「provider 选择」语义（`docs/.../providers/consciousness/*.md` 全是各家 API 的接入文档），不是情绪状态机 `[源码]` |
| **H4 两次独立决策** | **有，且很完整** | `builtIn_sparkNoResponse` + `builtIn_sparkCommand` + `allowNoResponse/allowSparkCommand/ignoreTextOutput` 策略矩阵 `[源码]` |
| H4 内部状态阈值唤醒 | **没有**。是固定 tick（1 分钟）与外部事件，不读内部状态强度 | `system-ticking-v1.velin.md` 的 "I'll ask you again in (1 minute later)" `[源码]` |
| H5 反刍防污染 | **有朴素版**：`come_up_ideas` / `come_up_goals` 定期把想法与目标写进长期记忆；`break` / `sleep` 会**清空工作记忆**（相当于手工遗忘 / 上下文重置） | 但**没看到任何查重、溯源或幻觉校验**，反思产物是直接写进去的 `[源码]` |

### 能不能用 / 怎么用

**用它的「协议设计」，不要用它的「代码实现」。** AIRI 是 TS/Electron 前端项目，与你的记忆内核不是一套技术栈，直接复用代码不现实；但它的**交互契约**是可抄的、而且正好命中你最想要的那条：

1. **抄 `spark:notify` / `spark:command` 的二分契约**（最值得抄的一点）：把「被唤醒」与「对外发言」拆成两个独立工具，再加一个 `ignoreTextOutput` 开关。你的「站起来」= 产出内部 command，你的「开口」= 产出文本；`noResponse` 工具 = 想了不说。这套三态（静默 / 只行动 / 行动+说话）可以直接映射成你的决策枚举。
2. **抄 ticking 的「内部状态动作」**：`continue` / `break` / `sleep` 这三个动作把「维持 / 清空 / 休眠」交给了模型自己选，`come_up_ideas` / `come_up_goals` 则把「自我产生的想法与目标」结构化写回长期记忆（goals 还带 deadline + priority）。**这正是 H5 反刍的接口形状**——你的反思器可以照着出这个结构。
3. **不要抄它的触发节拍**：固定 1 分钟 tick 与你 H4 的「积累到阈值才唤醒」是相反的。AIRI 这里是反例，不是参考。
4. **别把 `desktop-overlay-heartbeat.ts` 当心跳系统**。

### 来源

- 本地：`D:\Works\workplace\dsh-workplace\airi`（remote = `https://github.com/moeru-ai/AIRI`），关键文件副本在 `D:\Works\workplace\dsh-workplace\_airi_probe\`
- 上游：<https://github.com/moeru-ai/AIRI>
- 关键文件：`packages/core-agent/src/agents/spark-notify/{agent.ts,tools.ts,types.ts,event-source.ts}`、`integrations/telegram-bot/src/prompts/system-ticking-v1.velin.md`、`DESIGN.md`
- 社区提案（未取得正文）：<https://github.com/moeru-ai/airi/discussions/1512>

---

## 3. HyperSpace

### 它到底是什么（一句话）

**一个开源的 VSA（Vector Symbolic Architectures，向量符号架构）框架**：把超维计算拆成模块化算子（编码 / 绑定 / 捆绑 / 相似度 / 清理 / 回归），用来在统一口径下**基准对比不同 VSA 后端**。

### 关键问题：它是超维计算（HDC/VSA）领域，还是通用向量代数工具？还是机器人空间编码？

**答案：它是 HDC/VSA 领域的东西，而且是一个「工程基准测试 / 算子拆解框架」，不是数学理论，也不是机器人空间编码。**

我取到了 arXiv 官方页面的**完整摘要原文**（方法见文末）：

> Vector Symbolic Architectures (VSAs) provide a well-defined algebraic framework for compositional representations in hyperdimensional spaces. We introduce HyperSpace, an open-source framework that decomposes VSA systems into modular operators for **encoding, binding, bundling, similarity, cleanup, and regression**. Using HyperSpace, we analyze and benchmark two representative VSA backends: **Holographic Reduced Representations (HRR)** and **Fourier Holographic Reduced Representations (FHRR)**. Although FHRR provides lower theoretical complexity for individual operations, HyperSpace's modularity reveals that **similarity and cleanup dominate runtime in spatial domains**. As a result, HRR and FHRR exhibit comparable end-to-end performance. Differences in memory footprint introduce additional deployment trade-offs where **HRR requires approximately half the memory of FHRR vectors**. By enabling modular, system-level evaluation, HyperSpace reveals practical trade-offs in VSA pipelines that are not apparent from theoretical or operator-level comparisons alone.

元数据（同为 arXiv 页面元标签，`[论文原文]`）：作者 **Shay Snyder, Andrew Capodieci, David Gorsich, Maryam Parsa**；提交 2026/04/16，online 2026/05/08；PDF `https://arxiv.org/pdf/2604.15113`。

**逐项回答你的三个问题：**

1. **是 HDC/VSA 还是通用向量代数？** → **是 HDC/VSA**。摘要里的 binding / bundling / similarity / cleanup 就是 VSA 的标准算子集，HRR 与 FHRR 是 VSA 的两个经典实现（Plate 的 HRR 与其傅里叶版本）。这**不是**通用线性代数库，也**不是**「高维向量空间代数工具」这种泛泛的说法——它特指 VSA 这一支。

2. **「spatial encoding」是指机器人空间编码吗？** → **不是机器人的空间编码，但确实以「空间数据」为主要试验场。** `[推断]` 标题里的 spatial 指的是**用 VSA 编码空间/场类数据**（摘要只说 "in spatial domains"，未展开）。旁证：作者之一 David Gorsich 的相关工作是 *Brain Inspired Probabilistic Occupancy Grid Mapping with Hyperdimensional Computing*（占用栅格地图 + HDC），`[仅搜索摘要]` 另一条检索片段则提到 HRR 重建「在边界与梯度陡峭区域噪声略高」`[仅搜索摘要]`——这两点共同指向**空间场/地图的重建与回归**作为评测任务，而不是 SLAM 里的位姿表征。**注意这是推断，不是原文断言。**

3. **能不能用来做「记忆场的代数运算」？** → **可以，而且这是本文里唯一能落到你项目上的东西；但要想清楚它给你的是「算子」而不是「记忆系统」。**

### 对应关系

| 构想 | HyperSpace 是否有 |
|---|---|
| H1 记忆类型 / 衰减 | **无关**。它不提供衰减、生命周期、持久化 |
| H2 激活扩散 | **部分相关，但是另一条路线**。VSA 的 binding/bundling 提供的是**代数式组合检索**（用向量运算做结构化查询、superposition 存储多条目），跟「沿图逐跳扩散激活」是两种完全不同的机制。可以二选一或混用，但它**不实现** spreading activation |
| H3 情绪调制 | **理论上有接口**。VSA 天然支持「用向量调制相似度」——把情绪状态编码成一个向量参与打分是可行的。但**论文没做这件事**，这是你的设计工作 |
| H4 主动唤醒 | **无关** |
| H5 反刍防污染 | **弱相关**。cleanup（清理/纠错）算子是 VSA 里用来把带噪的捆绑向量还原到最近合法码字的机制，**理念上**与「反思产物去噪」同构；但论文是在空间重建语境下评测它，不是记忆去噪 `[推断]` |

### 能不能用 / 怎么用

**领域是对的，但它是工具，不是方案。**

- **能用**：如果你打算走 H2 的「记忆场代数」路线——即把记忆条目绑定成超维向量、用 superposition 一次存多条、用相似度做整体检索——HyperSpace 给你现成的 modular operator 层和 HRR/FHRR 两个可切换后端，省掉自己搭 VSA 骨架的功夫。摘要里两个**可操作的工程结论**值得直接采纳：① **相似度与 cleanup 是运行时瓶颈**（不是绑定/捆绑），优化要往这两处使劲；② **HRR 内存约为 FHRR 的一半**，个人项目跑本地，HRR 是更实际的默认选择。
- **不能用**：它**不解决**你的 H3（情绪调制）和 H4（主动唤醒），也**不提供**记忆的持久化、衰减、修订、溯源。这些还得靠你自己或 Membrane 那一侧。
- **风险提示**：`2604.15113` 这份**只有摘要级别证据**。我没有取到正文（`web_fetch` 不可用，且本环境 HTTPS 需绕行）。**"它能否支撑记忆场代数"这个判断有一部分建立在我对 VSA 的既有知识上，而非本文正文** `[推断]`。若你要投入时间走这条路线，**先设法拿到 PDF 正文确认算子 API 与评测口径**。
- **同名坑（重要）**：检索 "Hyperspace github" 会撞到一个**完全无关**的仓库 `MicrosoftARMAssembler/Hyperspace`（ARM 的 Emulating/Injecting/Dumping 工具）`[仅搜索摘要]`；另有一篇 ACL/ACM 论文叫 **HyperSPACE**（Sparse-Adder-Compatible Encoding for Efficient Hyperdimensional Computing on Digital CIM Arrays，针对数字 CIM 阵列的硬件编码）`[仅搜索摘要]`，**与本文不是同一篇**。认准 `2604.15113` 与 Snyder/Capodieci 这组作者。

### 来源

- arXiv 摘要页（本次实际取到全文摘要）：<https://arxiv.org/abs/2604.15113>
- PDF：<https://arxiv.org/pdf/2604.15113>
- ADS：<https://ui.adsabs.harvard.edu/abs/2026arXiv260415113S/abstract>
- Semantic Scholar：<https://www.semanticscholar.org/paper/e6cfaf86292e761f9bf303c179c53b416f94ef63>

---

## 4. EvoLib

### 它到底是什么（一句话）

**微软研究院的「测试时学习（TTL）」框架**：不改任何模型权重、不要外部监督，让黑盒 LLM 把**自己推理轨迹**里抽出的**模块化技能（skills）**和**反思洞见（insights）**存进一个**共享、可演化的库**，靠 Information Gain 动态加权与合并，实现跨题累积进步。

### 取证方式

**这次拿到了官方实现源码。** 顺着搜索命中找到 <https://github.com/microsoft/EvoLib> 并成功 clone 到 `D:\Works\workplace\dsh-workplace\evolib`，**通读核心算法文件 `EvoLib/evolib_agent.py`（409 行，全文）**，并读了 `README.md`（220 行，全文）。arXiv 摘要亦已取到官方原文。`[源码]` + `[论文原文]`

### 「演化」机制到底怎么做的 `[源码]`

核心是**两个库 + 一套打分 + 一个门控 + 一个合并**。读 `evolib_agent.py`：

**1. 两个库，结构不同**（`__init__`，第 56-57 行）

```python
self.skill_lib = {}    # skill_code  -> [IG, future_IG_list, docstring, embedding]
self.insight_lib = {}  # insight_text -> [future_IG_list, embedding]
```

- **skill** = 可复用过程（代码函数 / 子问题解法 / 子任务流程），键是**函数代码本身**
- **insight** = 自然语言规则，`consolidate_insights` 里**强制要求以 "If " 开头**（第 135 行 `insight.strip().startswith("If ")`）——即「如果我遇到 X，就该 Y」形式的条件规则

**2. 加法：新条目怎么进来（`add_new_skills` / `add_new_insight`）**

- 先算 embedding，与库中已有条目比相似度，阈值 `consolidation_similarity_threshold=0.8`
- **不相似** → 直接新增（skill 带 `IG_score`，insight 的 future_IG 初始为空）
- **相似** → 取最相似的那一条旧条目，让 LLM 做 **consolidate**（合并两个为更一般的表述）：
  - 若合并成 **1 条** → 删旧条目，新条目**继承**旧的 `future_IG` 列表；skill 的 IG 用滑动平均 `new_IG = 0.5*IG_score + 0.5*old_IG`（第 203 行，`IG_update_rate=0.5`）
  - 若合并成**多条** → 各自 IG 重置为当前 `IG_score`、future_IG 清空

**3. 打分：Information Gain（`compute_IG`，第 219-220 行）**

```python
def compute_IG(self, best_score, score_list):
    return math.log(max(best_score, self.eps)) - math.log(max(np.mean(score_list), self.eps))
```

即**「最好那次」相对「平均表现」的对数增益**——衡量这批候选里最优解比平均水平好多少。`eps=0.01` 防 log(0)。

**4. 打分：Future IG —— 反向信用分配（`update_future_IG_for_*`，第 222-246 行）**

这是最巧的一步，也是 README 里 "propagates credit backward along evolution chains" 的实现：对本次**被采样进最优解**的每条 skill / insight，找出那些**没有采样到它**的候选分数，算同样的 log 增益：

```python
if insight not in insight_list:
    future_IG_baselines.append(score)
future_IG = log(best_score) - log(mean(future_IG_baselines))
```

效果：一条抽象不仅能因「这次帮上了忙」加分，还能因「它促成了未来有用的抽象」而被**追溯加分**——这正是「演化链」而非「一次性打分」的关键。

**5. 加权采样：库怎么被使用（`_sample_from_library`，第 73-107 行）**

```python
p_choice = random.random()
if self.skill_lib and p_choice < 0.4:      # 40% 抽 skill
    weight = w_IG * max(value[0], eps) + np.mean(value[1]) if value[1] else 0.5
elif self.insight_lib and p_choice < 0.7:  # 30% 抽 insight
    weight = max(np.mean(value[0]) if value[0] else 0.5, eps)
# 剩下 30%：两个都不抽（即不用库）
```

**注意那 30% 的空档**：库不是每次都用，有 30% 概率**空手求解**——这既是探索，也让 Future IG 有「不含该条目」的对照基线可算。skill 的权重里 `future_IG`（`value[1]`）是**累加进 IG 项**的（注意 `+=` 那行的条件 `if self.w_IG < 100` 是个兼容旧默认值的 hack）。

**整体流程**（`run_iteration`，第 248-409 行）：每题采样 `k_q_per_problem`（默认 3）次 → 每次独立从库采样后求解 → **自评打分**（合成测试 `eval_function`，无测试目标时退化为**多数投票**）→ 取最优解 → 算 IG → 对最优解生成 insight（**仅当 `best_score < 1`**，即「还有错才反思」）→ **门控后**入库 → 更新 Future IG。

### 防污染 / 防误差累积 —— 这是它对你最有价值的部分 `[源码]`

EvoLib 自己很清楚这个风险。README「Out-of-Scope Uses」原文警告：

> On tasks where the base model cannot evaluate and verify solutions reliably, **the iterative nature of the EvoLib algorithm may introduce accumulated errors that degrade downstream performance.**

它实际用的护栏（**全部可从源码核实**）：

| 护栏 | 实现位置 | 作用 |
|---|---|---|
| **只有「在进步」才写技能** | `run_iteration` 第 371-384 行：`is_improving = not prev_best_solution or best_score > prev_best_score`，`if is_improving: self.add_new_skills(...)` | **最关键的一条**。技能库**只接受胜过历史最优的解**，垃圾解产出的技能根本不入库 |
| **inverse：失败不能反向污染** | 同上——不进步时连「已有的」都不动 | 库是单调的，不会被一次坏结果拖坏 |
| **先查重再合并，不盲目追加** | `add_new_skills` / `add_new_insight` 的 0.8 相似度分支 | 高频近似条目被折叠成更一般的表述，而非线性膨胀 |
| **合并失败就重置权重** | 第 201-208 行：合并成多条时 `new_IG = IG_score; new_future_IG = []` | 血统不清时**丢弃继承的分数**，不把旧信用错记到新条目上 |
| **反思以「条件规则」收敛** | `consolidate_insights` 强制 `startswith("If ")` | insight 必须是可执行条件式，不能是模糊散文 |
| **「满分就不反思」** | 第 360-362 行：`if best_score < 1: 生成 insight` | 没出错就不产生新规则，减少无谓污染面 |
| **无监督自评有多重兜底** | 合成测试 / 多数投票 / LLM-as-a-Judge（`is_better_solution`） | README: "Self-evaluated scores (synthetic tests, majority voting, LLM-as-a-Judge)" |

**但要诚实说清代价**：上面所有护栏都**依赖那个自评信号是对的**。README 自己点破了这个前提的脆弱性。在你「情感陪伴」的场景里，**没有合成测试、没有标准答案**——「这次陪伴好不好」无法自动判分。所以 **EvoLib 的 `is_improving` 门控无法原样照搬**，这是移植时最大的真实障碍，不是工程量问题而是**信号来源问题**。

### 对应关系

| 构想 | EvoLib 是否有 |
|---|---|
| H1 记忆类型 / 衰减 | **有「规则」类，无衰减**。insight（"If ..." 条件规则）正好补上你三类里的**「规则」记忆**；但 EvoLib **没有任何时间衰减**——库里的条目除非被合并掉，否则永久保留，权重只升不降 |
| H2 激活扩散 | **没有**。它的「检索」是**按 IG 权重做加权随机采样**（`random.choices`），不是沿图扩散。但它给了 H2 一个有价值的**替代范式**：用**效用权重**而非**关联强度**决定哪条记忆被激活 |
| H3 情绪调制 | **没有**。但它的 `weight` 计算是**一个可以插入调制因子的现成位置**——`_sample_from_library` 里 `weight = w_IG * IG + 其他项`，你若把当前情绪状态作为一个乘子或加项接进这里，就是一个「情绪调制检索」的最小实现 `[推断]` |
| H4 主动唤醒 | **没有**。EvoLib 是**每题驱动**（有输入才迭代），不自己发起 |
| **H5 反刍防污染** | **有，而且是四个项目里最完整、最可直接借鉴的**。见上表 |

### 能不能用 / 怎么用

**能用，而且是你第二阶段（小模型 + 记忆空间接入模型层）最值得深挖的一条路线。** 关键是要做**概念移植**而不是代码移植（它是 Python + Azure OpenAI 的评测脚本，直接跑不现实）：

1. **照搬双库结构**：`skills`（可复用过程）+ `insights`（"If ..." 条件规则）。`insights` 直接对应你 H1 里缺的**「规则」记忆**，而且它强制条件式这一点，正好能治「反思产出一堆抒情废话」的病。
2. **照搬 `is_improving` 门控**（最重要）：**只有带来改善的反思才允许写进长期记忆**。这是防幻觉污染的核心，比任何 prompt 约束都硬。
3. **照搬查重-合并-权重继承**：新条目先按 embedding 相似度（他们用 0.8）查重，命中则让 LLM 合并成更一般的表述，合并成功继承权重、失败则重置。对应你「避免反思污染」的诉求，而且能防止库无限膨胀。
4. **照搬 Future IG 的反向信用分配**：让「促成了好结果的记忆」被追溯加分。这对陪伴场景很有意义——一次让对话变好的早期记忆，应该因后续效果而被重估。
5. **改造点（必须改）**：把 `is_improving` 的**判据换掉**。EvoLib 用合成测试 / 多数投票 / LLM-as-judge，你在陪伴场景要换成别的信号，例如：用户显式反馈、对话未中断/未冷场、下一轮用户情绪回升、用户主动延续话题等。**这是唯一真正难的地方，也是决定这条路走不走得通的地方。** `[推断]`
6. **补 EvoLib 没有的**：时间衰减（它完全没有）、情绪调制（接在 `weight` 上）、主动唤醒（它完全没有）。

### 来源

- arXiv 摘要页（本次取到完整摘要原文）：<https://arxiv.org/abs/2605.14477>
- 官方实现（本次 clone 并通读核心算法）：<https://github.com/microsoft/EvoLib>
- 本地：`D:\Works\workplace\dsh-workplace\evolib`
- 关键文件：`EvoLib/evolib_agent.py`（409 行，全文读过）、`README.md`、`EvoLib/eval_main.py`（读过函数清单与 prompt 构造器）
- 作者：Weijia Xu, Alessandro Sordoni, Chandan Singh, Zelalem Gero, Michel Galley, Xingdi Yuan, Jianfeng Gao（Microsoft Research）

---

## 结论

### 按对项目的实际价值排序

| 排名 | 项目 | 定位 | 投入建议 |
|---|---|---|---|
| **1** | **EvoLib** | **唯一直接命中 H5（反刍防污染）且拿到官方源码的** | **重点深挖。** 抄它的**机制设计**：双库（技能 + "If" 规则）、`is_improving` 写入门控、相似度查重-合并-权重继承、Future IG 反向信用。**唯一障碍是自评信号**——陪伴场景没有标准答案，你必须自己造一个「这次好不好」的判据，这是第二阶段的核心难题 |
| **2** | **Membrane** | 记忆**存储与生命周期**层的成熟实现，与 H1/H5 强相关 | **中等投入，抄参数与思路，不抄架构。** 直接采纳三档半衰期（1h/1d/30d）、source 幂等、查重后强化而非新增、fail-closed。**但要清醒**：它的「不同衰减曲线」只是**同一条指数曲线 + 分档半衰期**，曲线形状写死；H2 它只有有界 BFS 邻域扩展、**不是**激活扩散；H3/H4 **零命中**。它的图关系层可以作为你自研激活扩散的底座 |
| **3** | **AIRI** | **H4「两次独立决策」的唯一完整参考**，但只该抄协议 | **小投入、高回报，只抄契约不抄代码。** 把 `spark:notify`/`spark:command` 拆成 `builtIn_sparkNoResponse`（静默）+ `builtIn_sparkCommand`（行动）+ `ignoreTextOutput`（想了不说）三态，这套二分契约**正是你 H4 要的形状**，且是四个项目里唯一被工程验证过的。反面教材：它的触发是**固定 1 分钟 tick**，不是阈值唤醒；别抄这部分 |
| **4** | **HyperSpace** | VSA/HDC 算子层工具，**领域对但离你的痛点最远** | **暂缓，除非你要走「记忆场代数」路线。** 它给 binding/bundling/cleanup 算子与 HRR/FHRR 后端，能支撑 H2 的**代数式**组合检索；对 H3 只提供「可插入调制向量」的可能性，对 **H4 完全无关**，且**没有**持久化/衰减/修订/溯源。**且我只有摘要级证据**，正文未取得。同名坑两个（ARM 的 `Hyperspace`、硬件论文 `HyperSPACE`） |

### 三句话总结

1. **备忘录描述基本准确，但两处措辞需要修正**：Membrane 的「可衰减」是**一条指数曲线配三档半衰期**，不是「不同类型不同衰减曲线」；它的图检索是**有界 BFS 邻域扩展**，不是「激活扩散」。
2. **四个项目没有一个同时具备 H3（情绪调制）和 H4（内部驱动主动）**——这印证了母项目的前提：**这两条确实是空白，必须自研**。它们各自能补齐的是：EvoLib→H5，Membrane→H1/H5 与图底座，AIRI→H4 的决策契约，HyperSpace→H2 的可选代数路线。
3. **最值得深挖的是 EvoLib**：它是唯一「机制完整 + 源码在手 + 直击你最担心的幻觉污染问题」的一个，而且它的 `is_improving` 门控思路可以脱离 Agent 场景，直接成为你反思器写入门槛的设计原型。

### 诚实性声明

- **实际读到源码**：Membrane（Go 源码，逐文件读过 decay/retrieval/schema 关键实现）、EvoLib（`evolib_agent.py` 409 行全文 + README 全文）、AIRI（根文档全文 + 11 个点名关键文件；**非全仓 5687 文件通读**）。
- **实际读到官方摘要原文**：HyperSpace（`2604.15113`）、EvoLib（`2605.14477`）——均取自 arXiv 官方页面元数据与摘要区块。
- **只看到搜索片段**：HyperSpace 的正文细节（评测任务、spatial 的确切含义）、AIRI 的 Discussion #1512 提案正文、EvoLib 的基准分数细节、一切名字带 HyperSpace/Hyperspace 的同名干扰项。
- **未能验证而明确留空**：AIRI `packages/memory-pgvector` 的**实现**（只读了 `package.json`），故其记忆类型与衰减情况标注为「未验证」而非「没有」。
- **推断而非原文**：HyperSpace 的 spatial 含义、HyperSpace 能否支撑记忆场代数、EvoLib 移植到陪伴场景的具体改造点、Membrane 衰减参数可否直接沿用的判断。

### 取数方法备注（供后续复用）

本机 shell 的 HTTPS 损坏（Schannel `SEC_E_NO_CREDENTIALS`），`web_fetch` 返回 "resolves to a non-public IP address" 不可用。本次实际有效通道：

1. `web_search` —— 初筛与定位仓库。
2. `git -c http.sslBackend=openssl -c http.proxy=http://127.0.0.1:7890 clone --depth 1 <url> <dir>` —— 克隆仓库本体。**注意**：`http.sslBackend=openssl` 只在**命令行 `-c` 传入**时生效，写进 repo 的 `git config` 对已启动进程无效；partial clone（`blob:none`）拉下来的工作区可能极不完整（AIRI 只落地 72/5687），需 `git checkout -f` 或用 `git cat-file -p HEAD:<path>` 按需取 blob。
3. **本次新探明**：`openssl s_client -quiet -connect <host>:443 -servername <host> -proxy 127.0.0.1:7890`，随后手工发送 `GET <path> HTTP/1.1`。arXiv 摘要即由此取得（`curl` 走代理在本机返回 `http=000`，无效）。
