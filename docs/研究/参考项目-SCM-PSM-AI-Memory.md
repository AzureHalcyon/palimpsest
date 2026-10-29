# 参考项目：生命周期记忆的三个外部实现（SCM / PSM / AI-Memory）

> **记录，不是设计。** 这份回答一个问题：这三个系统里，哪些机制能落进 `memory_v2/` 与 `loop/`，
> 哪些看着像、其实借不了，为什么。
>
> 与 [`记忆技术先例-调研.md`](记忆技术先例-调研.md)、[`参考项目与记忆层-调研.md`](参考项目与记忆层-调研.md)
> 的分工：那两份写的是 mem0 / Zep / Graphiti 那一支**检索式**记忆；这份写的是**生命周期**那一支
> （巩固、遗忘、睡眠期重排）。Graphiti 与 Mem0 只在 [`§4`](#4-graphiti-与-mem0只补既有两份调研没写的)
> 补两句它们没写的，不重复。
>
> 等级：`[源码]` 我读了代码并给出 `文件:行号`；`[一手]` 我读了仓库/官方页面原文；
> `[论文]` 读了正文；`[二手]` 别人转述；`[推断]` 我的判断；`[查不到]` 没核到。

---

## 0. 一句话

**只有 SCM 的机制表值得动，而且只借它的常量与公式，不借它的评估。**
SCM 是一个真仓库（46,749 行 Python、真测试、真论文），但它那套"1500 次 ALB 矩阵"在数值上等于
"空闲开关是否打开"——它自己的 LoCoMo 数字还输给云端 LLM 抽取基线 8 倍。PSM 的论文真、
**交付物查不到**（npm 三个地址全 404），没有可跑的东西。AI-Memory 是一个**用 LLM 改写事实再删除**
的管线，与我们"原文不改、忘的只是连接"正面相反，只能借它的衰减公式形状。

| 对象                  | 有没有可跑的实物        | 它真解决的问题                  | 对我们的作用                  |
| ------------------- | --------------- | ------------------------ | ----------------------- |
| SCM                 | 有：PyPI 包 + 源码   | 空闲期巩固、痕迹级衰减、版本失效         | **借 4 条常量与公式**；评估数字不能用  |
| PSM                 | **没有**（包不存在）    | 把记忆操作当学习目标（设计稿）          | 无一物可借；撞"读法冻结"这条决策       |
| AI-Memory           | 有：Go + Vue 服务   | STM→暂存→LTM 漏斗、监控告警       | 只借衰减公式形状；管线不可借          |
| Graphiti            | 有（既有调研已读）      | 事实有效期建模                  | 补一句"失效谁触发"               |
| Mem0                | 有（既有调研已读）      | 生产级基础记忆层                 | 补一句"开源版 decay 不存在"       |

---

## 1. 我们的坐标：这次要回答的三个缺口

先把自己的现状摆出来，否则"能借什么"没有判据。

| 缺口                       | 我们现在怎么做                                                                 | 这一批里谁碰过         |
| ------------------------ | ----------------------------------------------------------------------- | --------------- |
| 重放只在手动归档时跑              | `replay` / `--replay-on-exit` 手动触发；`loop/` 在等的时候只做过期归档（`loop/driver.py:878` `_maintain`） | SCM 的 idle learner、AI-Memory 的定时器 |
| 衰减是"每场一乘"，不是"按痕迹"         | 所有非结构连接乘 `0.87`，靠支撑给的底活下来（`memory_v2/consolidate/replay.py:30-44,273-287`） | SCM 的痕迹级 λ、AI-Memory 的公式 |
| 没有"过时"这个维度                | 原文只读、同一事件以时间较晚者为准；旧入口的转述会拿到最后发言权（`事件状态与读取.md` §5 已知后果） | SCM 的版本链、Memora 的 FCC/FAA（见 [`基准-Memora-CloneMem.md`](基准-Memora-CloneMem.md)） |

---

## 2. SCM

### 2.1 它是什么

| 项      | 值                                                                                                    |
| ------ | ---------------------------------------------------------------------------------------------------- |
| 仓库     | [clyrai/SCM_OpenSource](https://github.com/clyrai/SCM_OpenSource)，MIT，0 star                          |
| 规模     | 204 个 `.py`、46,749 行、64 个测试文件 `[一手]`；单作者，公开历史 26 个 commit（最早 2026-05-14），首个 commit 说明是 "remove sensitive files" `[一手 gh api]` |
| 包      | PyPI [`scm-memory`](https://pypi.org/project/scm-memory/) 1.1.0，11 个版本 `[一手]`                         |
| 论文     | *SCM: Lifecycle Memory for Language Agents*，DOI [`10.5281/zenodo.21323877`](https://doi.org/10.5281/zenodo.21323877)，CC-BY 4.0，38 页，仓库内 `research/SCM_Final_Paper.pdf` `[一手 DataCite]` |
| 形态     | 嵌入式库为主，另有 REST / MCP server / Postgres+pgvector；对外只挂五个动词：`add_memory` / `search_memory` / `sleep` / `wake_summary` / `forget` |

它**不是** prompt 套壳：有真实的衰减算术、真实的测试，与 engram / MemGPT / mem0 没有代码雷同。
但它是单作者、五个月、一次大提交式长出来的项目，star 为 0，除作者外无人复核过——**读它的代码，
不要读它的结论**。

### 2.2 机制（源码级）

| 机制          | 做法                                                                                                                | 出处                                |
| ----------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| 留存分数        | `0.22*grasp + 0.24*salience + 0.18*rehearsal + 0.18*association + 0.12*recency − 0.22*interference`，clamp 到 [0,1] | `src/sleep/forgetting_dynamics.py:214-222` |
| **痕迹专属衰减率** | `λ = 0.04*(1 + (1−retention)*1.5 + interference*0.8 + 0.15(非当前版本) + 0.10(已压制))`，下界 0.005                      | 同上 `:224-241`                     |
| 强度衰减        | `strength ← max(0.05, strength * exp(−λ * max(0.25, age_days)))`                                                   | 同上 `:139-140`                     |
| 自适应遗忘阈值     | `threshold = 0.32 + min(1, 概念数/100)*0.18 + 目标遗忘率*0.05`，上限 0.85                                                      | 同上 `:243-256`                     |
| 矛盾处理（版本链）   | 旧节点 `valid_to=now`、`is_current_version=False`、`state=ARCHIVED`、`superseded_by`；新节点 `valid_from`；另建 `CONTRADICTS` 边 strength 1.0；**旧版本不删** | `src/core/long_term_memory.py:357-388` |
| 三层睡眠        | 微睡眠（每 4 轮 / 熵阈值 0.82 / 轻衰减 0.97 / 重放 top-12）、NREM 全局降尺度 0.8、深睡降尺度 0.90（压力阈值 0.93、最小空闲 900s、冷却 12 轮） | `src/sleep/{micro_sleep,nrem,deep_sleep}.py`、`src/core/config.py:142-166` |
| 空闲即算力       | `idle_threshold 600s / min_interval 1800s / tick 60s / max_sleep_duration 120s`；**默认关**（`IDLE_LEARNER_ENABLED=false`）；时钟可注入 | `src/lifecycle/idle_learner.py:66-95` |
| 唤醒摘要        | **纯模板、零 LLM、确定性**；`narrative` + `to_dict()` 双形态；区分 `autonomous_cycles_run` 与 `sleep_cycles_run`          | `src/lifecycle/wake_summary.py:1-80` |
| 扩散激活        | `A_j(t+1) = decay*A_j(t) + Σ A_i*W_ij*gate`，`decay=0.45`，低于阈值截断、多步收敛                                            | `src/retrieval/spreading_activation.py:24-28,296-344` |

最后一行值得单独说：**它和我们的读取是同一族公式**（我们 `read/activate.py` 的扩散 + 内容感应）。
差别在我们多一层"几件事竞争、选一件"，它多一层"门控 + 阈值截断"。参数上它用 0.45 做每步传播系数，
我们不分步、直接两跳。这属于**参数标定参考**，不是新机制。

### 2.3 评估声明核查：1500 与 500 分别在说什么

用户转述的两条数字**都真实存在**，但都不是"更强"的证据。

**1500 次 ALB**：ALB 是它自造的 `autonomous-learning benchmark`。资产
`scm-alb-v02-canonical-1500.tar.gz`（741,981 B）在 release 里，SHA-256 与 `docs/ARTIFACTS.md`、
`SHA256SUMS.txt` 三方一致；解开是 50 persona × 5 seed × 2（idle 开/关）× 3 系统 = 1500 个 raw，
`aggregate.json` 写 `complete_matrix=true` `[一手]`。

**但逐文件统计 SCM 那 500 次运行后发现** `[一手]`：

| 指标                     | SCM（500 次）             | 基线（Lexical / Vector） |
| ---------------------- | ---------------------- | ------------------- |
| pdr / cgc_id / cgc_fill / wsi_f1 | 只有 0 与 1 两个取值，均值恰 0.500 | 结构性恒 0             |
| css                    | 恒 1.000                | 0.960               |

也就是说这四个"提升"指标在数值上**等价于"空闲机制是否开启"这个布尔量**：开着得 1、关着得 0。
基线在两个维度上恒 0，因为"模式发现"和"唤醒摘要"这两个对象它们根本不产出。所以
"SCM 1.0 vs 基线 0.0"是**功能有无**的对比，不是能力高低的对比；bootstrap CI 因此退化到 `[1.0, 1.0]`。
等于把同一句话测了 1500 遍。

**500 次 Mem0 对比**：资产 `scm-mem0-official-500.tar.gz`（13,359,936 B）存在、校验和已发布，
论文 §11.12 记为 50 persona × 5 seed × 配对 idle = 500 行、零崩溃，用的是**已安装的官方 `mem0ai` 包**
`[一手 gh api + 论文]`（压缩包下下来校验和与发布值不符、打不开，见 §8 第 2 条，标 `[查不到]`）。结果里 Mem0 在
`pdr/cgc_id/cgc_fill/crai_current/wsi_f1` 上两种 idle 条件下都是 0.000——因为那是它不实现的机制。
作者自己写的是 "This is not a broad Mem0 failure claim"，并声明对 MemoryBank / A-Mem / MemGPT /
Generative Agents 的对比仍是 future work。

**而它自己报的 LoCoMo 数字是输的**：云端 LLM 抽取基线 0.520，SCM HME-full 0.016（8 倍差距），
LoCoMo++ 里也没有任何系统全维占优 `[一手 docs/BENCHMARKS.md:163-189]`。这是它诚实的地方，
也是它适用边界的说明：**生命周期机制回答的不是"干净事实检索"那个问题**。

⇒ 结论：**数字可信、含义不可引用**。我们能引用的只有一条结构性观察——"检索式记忆在生命周期轴上
没有输出"，而这句话反过来也成立。

### 2.4 能借的四条、借不了的三条

| 能借 | 借什么 | 落在我们哪 | 代价 |
|---|---|---|---|
| **痕迹级 λ** | 每条连接一个衰减率，由支撑、干扰、版本状态算出，下界 0.005 | `consolidate/replay.py` 的衰减段（现在统一 `0.87`） | 每条边多存一个"上次被点亮的场次序号"；`参数.md` 一行 |
| **按天衰减 + 0.25 下限** | `exp(−λ·max(0.25, age_days))`，地板 0.05 | 同上；顺带把衰减的时钟从"场"换到"天" | 需要可靠时间戳与时钟归一 |
| **阈值随规模上升** | `base + min(1, N/100)*0.18`，上限 0.85 | 剪枝门限（现在 `PRUNE=0.12` 固定） | 需要"活跃节点数"的一致视图；极低 |
| **idle 四件套里的两条** | `max_sleep_duration` 硬上限（防一次重放跑飞）、可注入时钟（把几小时压到毫秒做测试） | `loop/driver.py` 的 `_breathe` | 我们已有 600s tick，只缺硬上限 |

| 借不了 | 为什么 |
|---|---|
| **ALB 基准与 1500/4000/1200 矩阵** | 指标是"功能开关"级二值量，CI 退化。拿它当对标基线，等于继承一个同义反复 |
| **它的 embedding / 抽取栈** | sentence-transformers / Ollama / OpenAI，与我们"中间层只做算术、记忆不依赖外部模型服务"的约束冲突（注意：本项目**两端**都用模型——`core/llm.py`、`read/archive.py` 的压缩、`read/write.py` 的编写；受限的是中间层，不是"全程不用模型"）；它自己 `docs/DEPLOYMENT.md` 承认启发式抽取导致显著性扁平、深睡误剪 |
| **它的 `src/` 与 `scm/` 双层结构** | 那是为了同时支持嵌入式与 REST/多租户，是产品化负担；我们是库，不需要 |

---

## 3. PSM：论文真，交付物查不到

### 3.1 逐条核对

| 用户说法 | 核对结果 |
|---|---|
| "没有 GitHub 仓库，以 npm 包 `epsm-memory/cli`、`epsm-memory/sdk` 交付" | `https://registry.npmjs.org/@epsm-memory%2fcli` → **404**；`.../@epsm-memory%2fsdk` → **404**；`https://registry.npmjs.org/epsm-memory` → **404**；搜索接口 `text=epsm` 命中 1 条且与记忆无关，**名称含 epsm 的包 0 个**；`gh search repos "epsm-memory"` → `[]` `[一手]` |
| "Zenodo record 19647417 是论文，CC0" | record 存在、CC0 1.0、2026-04-19，标题 *The Personal Small Model (PSM): Memory as a Learned Cognitive Primitive for LLM Agents*；作者 **Chirravuri, Krishna** `[一手 DataCite]` |
| "SCM 与 PSM 是同一批系统" | **不是**。SCM 作者 Shinde（Clyrai IP Studio），DOI `21323877`；PSM 作者 Chirravuri，DOI `19647417`。两个不同作者、两套东西 |
| "Qwen2.5-0.6B-Instruct 微调" | `[查不到]`。PSM 摘要与元数据里没有这个型号；论文写的是 "small model (1–3B parameters)"；没找到任何公开的"小模型学记忆操作"权重或代码 |
| "核心是让模型学记忆操作本身" | 提法属实，但训练信号是**下游效用 RL**（检索到的记忆让 LLM 变好就强化、用户纠正是最强负信号），论文 "What's Still Open" 里自己列着 "Exact training curriculum for memory operations" 未定 |

Zenodo 摘要还明写 *"This document constitutes a public prior art disclosure. No patent is sought."*，
即**设计披露**，不是实验报告；DataCite 记录里 `contentUrl` 为空、无数据、无权重。

### 3.2 为什么"复现它"不成立

要把"记忆操作"训成一个模型的输出，至少需要三样：任务分布、可测奖励、训练课程。PSM 三样都没给
（奖励要在线 A/B 与真实纠正信号，课程自认未定）。照着做等于我们自己发明全部训练目标与数据集——
那是原创研究，不是复现。**这一条比"包不存在"更根本**：即使包存在，也不能直接拿来用。

### 3.3 唯一可用的东西是一个提法，而它撞我们的红线

它的提法值得记在案：**记忆操作可以拆成六个职责**（相关性门控、巩固、回忆加权、干扰检测、
衰减调度、睡眠期重排）。这六个职责当作**模块边界**是有用的——但实现仍应是确定性公式。

它与本项目已定的方向正面冲突：我们的第一步是"读法冻结、不训练，只积累材料"。
引入一个 0.6B–3B 的模型接管强度计算，等于换掉"为什么这条连接变弱了"这个可回答性。

---

## 4. AI-Memory：三级漏斗是一条"改写事实"的管线

### 4.1 它是什么

[xwj-vic/AI-Memory](https://github.com/xwj-vic/AI-Memory)，Go + Vue，10 star，最后 push 2025-12-24。
`pkg/memory/{funnel,decay,judge,ltm_dedup}.go`、`pkg/store/{redis,qdrant,mysql}.go`，
另有告警引擎（`alert_*.go`）与监控仪表盘（`frontend/src/views/MonitoringDashboard.vue` 等）。
工程完整度确实高，但它解决的是**多用户服务**的问题，不是单个长命个体的记忆问题。

### 4.2 参数与漏斗（源码级）

| 参数 | 默认 | 出处 |
|---|---|---|
| `STM_CONTEXT_WINDOW` / `STM_WINDOW_SIZE` | 10 / 100 | `pkg/config/config.go` |
| `STM_EXPIRATION_DAYS` / `STM_MAX_RETENTION_DAYS` | 7 / 7 | 同上 |
| `STAGING_VALUE_THRESHOLD` | 0.6 | 同上 |
| `LTM_DECAY_HALF_LIFE_DAYS` / `LTM_DECAY_MIN_SCORE` | 90 / 0.3 | 同上 |
| LTM 去重相似度 | 0.95（Qdrant top-1） | `pkg/memory/funnel.go:219` |
| 暂存区条目 TTL | 30 天（硬编码） | `manager.go:50` |

**三段各由不同东西驱动**，这是它与我们最不一样的地方（我们每一层都在同一轮对话里完成）：

| 段 | 谁触发 | 条件 |
|---|---|---|
| 写 STM | 每次 `add` 同步 `RPushWithExpire`，TTL 7 天随写重设 | 无判定、不调 LLM `[源码 manager.go:112-144, redis.go:41-53]` |
| STM → 暂存区 | **后台定时器每 10 分钟**扫 `memory:stm:*:*` | 未判定 ≥5 条，或最早一条已等 ≥60 分钟 `[源码 funnel.go:416-456,48-61]` |
| 暂存区 → LTM | **另一个定时器，每 24 小时** | 出现次数 ≥2 且首见 ≥48 小时且 `status=pending` `[源码 funnel.go:385-412, staging_store.go:207-253]` |

闸门细节：进暂存区要 `IsCritical` **或**（`ShouldStage` 且 `value ≥ 0.6`）；`IsCritical` 走绿色通道
**跳过暂存区直写 LTM** `[源码 funnel.go:125-143]`。出暂存区按信心三档：≥0.8 自动晋升、
0.5–0.8 **只打日志等人确认（`TODO` 未实现，实际停在暂存区等 30 天 TTL）**、<0.5 删除
`[源码 funnel.go:179-195]`。

两处与宣传不符：

- 它叫"Redis 滑动窗口"，但代码里**没有 `LTRIM`**；`STM_WINDOW_SIZE=100` 只出现在配置结构里、
  从未被读取 `[源码 config.go:107,165]`。实际形态是"只追加 + key 级 TTL"，不是滑动窗口。
- 判定过的记录会被 `LRem` 从 STM **物理删除** `[源码 funnel.go:152-157]`。这与我们"原文永不删"
  正面冲突，也说明它的 STM 是缓冲区而不是记忆。

### 4.3 衰减公式的真实形状

```go
// pkg/memory/decay.go:31-45
decayScore = 0.6*exp(-daysSinceAccess/halfLifeDays) + 0.4*min(1.0, accessCount/10.0)
ShouldEvict = decayScore < 0.3
```

两处要说清：

1. **`halfLifeDays` 不是半衰期**，是 1/e 时间常数。取 90 时真正的半衰期是 62 天；一条从未被取用的
   记录在 `0.6*exp(-d/90) = 0.3` 时被判死，即 **d = 90·ln2 ≈ 62 天**。文档里"90 天半衰期"的说法
   是名不副实的。
2. 频次项 `0.4*min(1, n/10)` 意味着**访问 10 次就顶到上限**，之后时间项单独决定生死。这与
   "常用的记忆该留下"同向，但粗糙。
3. 而 `decay_score` **不参与召回排序**：它只在 24 小时一次的清理扫描里被读 `[源码 funnel.go:462]`；
   而 `RefreshAccess`（`decay.go:53`）在整个仓库里**没有调用点**——召回不刷新 `last_access_at`。
   按公式推算，访问次数 ≥8 的条目 `0.6·e^(−t/90)+0.4 ≥ 0.32 > 0.3`，**永不被淘汰**。
   所以它是**清理器**，不是排序信号；这一条与 Mem0 平台版的用途正好相反。

### 4.4 能借 / 借不了

| 能借 | 说明 | 代价 |
|---|---|---|
| 两段式衰减形状（时间项 + 频次项加权） | 我们只有"支撑给底"，没有"取用频次"这一项，而 `可及` 已经在读侧存在（`1 + 0.1×可及`）——可以考虑把它接到**连接强度**上 | 低：一个公式 + 一处更新 |
| 过期扫描"每 N 小时一次"的定时形态 | 我们 loop 已有 600s tick，可对照它设扫描周期 | 无 |
| 仪表盘"暂存区待审"这个界面范式 | 我们 `view.py` 有事件图查看器，可加一栏"待人工确认的候选连接" | 中：前端工作量 |

| 借不了 | 为什么 |
|---|---|
| **三级漏斗整体** | 它的 LTM 存的是 **LLM 改写后的事实陈述**（`judge.go` 的 `SummarizeAndRestructure`：把对话/事件转成独立事实；`DecideMergeStrategy`：合并/跳过/更新）。我们存的是原文与 note，忘的只是连接 |
| **按分数删除 LTM** | 与"原文不删"正面冲突。我们连 `short` 类记忆都只是"压一行搬出图"，不删内容 |
| **LLM 判"值不值得记"** | 我们已经有这一步（`event` / `remember` 的判定），且判定发生在对话中而不是离线批处理；换成离线 judge 会把"当时的判断"换成"事后的判断" |
| **告警引擎／多租户／认证** | 服务化需求，不是库的需求 |

---

## 5. Graphiti 与 Mem0：只补既有两份调研没写的

### 5.1 Graphiti：失效是谁触发的，"不用 LLM"这句话对哪一半成立

| 事实 | 证据 |
|---|---|
| 边上有**五个**时间字段，不是四个：`created_at`、`expired_at`（事务时间）、`valid_at`、`invalid_at`（事实有效期）、`reference_time`（情节的参照时间） | `graphiti_core/edges.py:271-285` `[源码]` |
| 检索按 node / edge / community / episode 四类分别配置方法与重排器；方法是 `cosine_similarity`、全文（BM25）、`breadth_first_search`；重排是 `rrf`、`mmr`、`node_distance`、`episode_mentions`、可选 `cross_encoder` | `graphiti_core/search/search.py:34-65,98-131` `[源码]` |
| **写入侧必须用 LLM**：`extract_edges` 走 `llm_client` 抽实体与边；`resolve_extracted_edges` 用 LLM 判重与矛盾 | `graphiti_core/utils/maintenance/edge_operations.py:134-236,424-595` `[源码]` |
| 失效动作：判出矛盾后 `invalid_at = candidate.valid_at`（旧边不删，只标"从那一刻起不再为真"） | 同上 `:715-745` `[源码]` |
| 融合默认 RRF `Σ 1/(rank_i+1)`，**`rank_const=1`**（不是常见的 60）；三路各自先取 `2×limit` 再融合；`DEFAULT_MIN_SCORE=0.6`、`MAX_SEARCH_DEPTH=3` | `search_utils.py:1786-1801`、`search.py:291-362` `[源码]` |
| 判定被拆成两件事：**"是不是矛盾"由 LLM 判**（输出 `contradicted_facts[]`），**"从哪一刻起无效"是纯日期算术**（旧边 `valid_at` 更晚才失效；两段区间不重叠就不算矛盾） | `prompts/dedupe_edges.py:30-32,86-97`、`edge_operations.py:715-783` `[源码]` |
| **默认查询不排除失效边**：`SearchFilters()` 四个时间字段全 `None`，失效边照样进候选；"排除已失效"是调用方自己塞 `DateFilter` 的可选行为，库层没有内置的时间点查询 | `graphiti.py:1966,2012`、`search_filters.py:62-65` `[源码]` |

⇒ 用户那句"无需 LLM 摘要即可低延迟"**只对检索成立**：检索侧确实不需要摘要、不走 LLM，融合是纯数学；
但**写入侧离了 LLM 转不动**（每个 episode 至少 4–6 次：抽实体、节点去重、属性与实体摘要、抽边、
边去重、补时间戳）。这解释了既有调研里那条实测（Zep 每条会话记忆 600k+ tokens、
"写入后立刻查询往往失败，等几小时再查就好了"）——成本与延迟都在写入侧，不在查询侧。

两处容易引错的数字：OSS 检索的说法是 "typically sub-second"；**"sub-200ms" 是 README 里 Zep 平台那一列**，
不是开源库。另外"旧事实被标记无效后查询时排除"在**库层不成立**——排除要靠调用方自己加过滤条件。

### 5.2 Mem0：开源版的 decay 不是一个开关，是一条"不支持"

| 说法 | 核对 |
|---|---|
| "开源版只暴露 `{"decay": bool}` 开关" | **不准确**。开源 SDK 收到 `decay` 参数时给的是**错误**：`DECAY_FEATURE_ERROR_MESSAGE = "The decay parameter is not supported by the OSS Memory SDK."`（`mem0/memory/notices.py:134`）；配套的 `decay_stub` / `decay_usage` 通知项在 `mem0/memory/oss_notices_config.json` 里默认关闭 `[源码]` |
| "0.3×–1.5× 的缩放因子主要在平台版" | **属实**，而且有官方文档：[Memory Decay](https://docs.mem0.ai/platform/features/memory-decay)。按项目开关、默认关、只影响 v3 搜索排序、**从不过滤**；候选池先放宽到 `top_k×3`（下限 50），乘完再排序，返回分数截到 [0,1]；访问历史最多记 20 次；没有历史的老记忆用 `event_date` 或 `updated_at` 兜底 `[一手]` |
| "它仍是可靠的基础记忆层起点" | 要补一条：开源版 v3 **已删掉图记忆**（官方对照页原话 "Removed … no queryable graph and no `relations` field"），`project.update(decay=…)` 会抛 "Project updates are not supported"；OSS 真正剩下的时间能力只有 `expiration_date` 硬过期加本地实体 boost `[源码 docs/platform/platform-vs-oss.mdx:70]` |

对我们没有可借的实现（开源版没有），可借的是**设计判断**：软衰减排序该在"放宽候选池"之后做，
否则重排没空间；且它明确"从不过滤"——这与我们"忘的是连接强度、原文保留"同一个方向。

---

## 6. 汇总：能落进本项目的候选动作

按"值不值得做"排。**前两条要改行为，得先进 `决策记录.md`。**

| # | 动作 | 落在哪 | 借自 | 代价 | 先决条件 |
|---|---|---|---|---|---|
| A1 | **微重放**：在 `wait` 的 `_breathe` 里跑单遍、有硬超时（120s）的重放，只从最近被标记的事件出发，不动 `冻结/`、不调 `SessionStore.archive()` | `loop/driver.py:864-895`、新增一个不依赖"场"的 consolidate 入口 | SCM 的 idle learner | 中：要新增入口；要定"微重放"与"归档重放"是否共用证据计数 | 先答一个问题：没有"场"这个单位时，证据按什么累加 |
| A2 | **痕迹级 λ**：用已有的支撑、强度、来源算出每条连接的衰减率，衰减时钟从"场"换成"天" | `consolidate/replay.py:30-44,273-287` | SCM 的 λ 公式 | 低：每条边多一个字段 | 需要"上次被点亮的场次序号" |
| A3 | **剪枝门限随规模上升** | 同上 | SCM 的自适应阈值 | 极低 | 需要活跃节点数的一致视图 |
| A4 | **事件级版本链**（`valid_from/valid_to` + `CONTRADICTS` 边 + 旧版本额外衰减罚） | `store/graph.py`、`read/activate.py` 的候选过滤 | SCM 的版本链 | 中：读取多一个否决口 | **要单独决策**：它会让"旧事件"在某些查询里被排除，等于给读取开了第二个闸 |
| A5 | **量具**：用 Memora 的"旧状态不该冒出来"那一半当外部尺 | 见 [`基准-Memora-CloneMem.md`](基准-Memora-CloneMem.md) | Memora | 高：适配层 + 每题多次 LLM 调用 | 先定"忠实通道 / 回忆通道"要不要分开 |
| A6 | 把 `可及` 接到连接强度上（取用过的连接更抗衰减） | `replay.py` | AI-Memory 的频次项 | 低 | 无 |

**建议的顺序**：A2 + A3 先做（都是同一处、都只改常量与一处更新，做完能直接对比"统一衰减 vs 痕迹级衰减"）；
A1 单独做，因为它要动 `loop/` 的行为；A4 和 A5 要先写决策，不急着实现。

---

## 7. 借不了的总表

| 东西 | 为什么 |
|---|---|
| SCM 的 ALB / 那 500 行 Mem0 对比 | 指标等于"功能开关"，CI 退化；作者自己限定为"不是 Mem0 失败声明" |
| SCM 的 embedding / 抽取栈 | 与"中间层只做算术"的约束冲突（两端用模型不受影响）；它自己的 encoder-dependence 失效模式就是证据 |
| PSM 全部 | 无代码、无包、无权重、无课程；训练目标要我们自己发明 |
| AI-Memory 的三级漏斗 | 它的 LTM 是 LLM 改写后的事实，且按分数删除 |
| Graphiti 的实体图谱 + 社区摘要 | 单位不匹配（实体/关系/社区 vs 事件）、成本不匹配（写入侧 600k+ tokens/会话） |
| Mem0 的 entity 层与 ADD-only 抽取 | 同上的形态问题；只借 `utils/scoring.py` 的纯数学（既有调研已记） |

---

## 8. 没核实到的

1. **SCM 的测试没跑**：64 个测试文件、`docs/QUALITY_GATES.md` 自称 322/322 全绿，是它的自述，不是我实测。
2. **Mem0 500 行压缩包打不开**：下载到的 `scm-mem0-official-500.tar.gz` 尺寸与发布值相同
   （13,359,936 B）、gzip 头正常，但本地 SHA-256 `d8ee728c…` 与发布值 `f256f3a1…` 不符，
   `tarfile` 直接报错。所以只核到"资产存在 + 尺寸 + 发布校验和 + 论文 §11.12 的描述"，
   **内容没有核验过**；4000 行 reference 矩阵、1200 行 ablation、real-LLM 三个资产没下载。
3. **SCM 的数字来自 `docs/BENCHMARKS.md` 与 release 资产**，论文 PDF 我读的是其正文与元数据（DataCite），
   两者在 LoCoMo 那节互相印证；arXiv / OpenReview 上是否有 SCM 版本 `[查不到]`。
4. **AI-Memory 没跑**：结论全部来自源码阅读，没有部署验证。
5. `zenodo.org` 在本机不可直连（DNS 解析到非公网地址），两篇论文的元数据都取自 DataCite 权威 registry；
   PSM 的 PDF 正文是否存在 `[查不到]`。
