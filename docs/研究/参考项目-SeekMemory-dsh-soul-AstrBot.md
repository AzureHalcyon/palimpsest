# 参考项目：SeekMemory · dsh-soul · AstrBot（2026-10-29）

> 目标项目：**palimpsest**——常驻循环 ＋ 事件网 ＋ 回忆编写 ＋ 自改 persona 文本卡。
> 本文回答一件事：**这三个仓里有没有能借的东西；借哪几样，为什么其余借不了。**
>
> 证据标注：
>
> - `[源码]` —— 读过具体文件 / 函数，给路径与行号
> - `[官方文档]` —— 仓库自带的 README 或 `docs/`
> - `[推断]` —— 我的判断
> - `[查不到]` —— 找过没有，或快照里缺
>
> **取数与边界**：三个仓都是 tarball 快照（`codeload.github.com`，无 git 历史），读源码与自带文档，**一个都没跑起来**。快照说明：SeekMemory 为 `astrbot_plugin_seekmemory` v0.5.3；dsh-soul 的版本号自称不一（`package.json` 写 1.5.0、README 标 1.6.0、CHANGELOG 顶层 1.13.0）；AstrBot 为 master，尾部截断 8 个 `tests/unit/test_t*.py`（纯测试，不影响结论）。取数时间 2026-10-29。

---

## 0 一句话

**三个仓没有一个是"常驻在场 ＋ 重建式回忆"的形状。** SeekMemory 与 dsh-soul 都是"检索出少量条目、注入提示词"，AstrBot 干脆没有记忆层（明确外包给插件）。所以记忆架构这一层没有能整体照搬的东西——这一条与 [`参考项目-按topic扫一遍-2026-10.md`](参考项目-按topic扫一遍-2026-10.md) 的结论一致。

能借的是三处**工程纪律**，而它们正好压在本项目**已经写下、还没落地**的三处空白上：

| 借什么 | 出处 | 压在本项目的哪处空白 |
|---|---|---|
| 自改的护栏 | SeekMemory ① 与 dsh-soul 2.2 **独立收敛到同一套** | [`../persona.md`](../persona.md) §3–§5（外部核验四条）——现在只有文档，没有代码 |
| 记忆的可见性（来源 / 覆盖边界 / 为什么没想起来） | dsh-soul 2.3、SeekMemory ①③ | [`../理想.md`](../理想.md):19「这段我是查到的，不是记得的」；`recall_log` 只有命中了谁 |
| 通道现实的薄壳 | AstrBot 3.2 | `inbox` 之外还没有接真的即时通讯平台（适配器在 `rin` 仓） |

还有一条**独立佐证**值得单独说：SeekMemory 把"原文不可变 / 解释是派生的、可版本化"落成了代码（`blobs` / `nodes` / `versions` / `artifact_evidence` / `ledger(prev_hash, self_hash)`，`store/schema.sql:6-133` `[源码]`），不变量写在它自己的 agent 指南里——"Source rows are the only immutable truth… Artifact is a derived, versioned interpretation"。这正是 [`../解构与重建.md`](../解构与重建.md) §2 的 trace / schema 分层，**别人的实现与这份草案撞在同一个形状上**。

---

## 1 SeekMemory（NeverENG，31 星）

### 1.1 它是什么 `[源码]`

AstrBot 插件形态的**记忆内核 + 宿主中立 runtime**，不是聊天记录检索器。关键一点：**记忆不经过模型调用**——`adapters/astrbot/hooks.py:65-135` 的 `inject_memory` 挂在 `on_llm_request` 上，每次请求前把组装好的块追加进 `req.system_prompt`。它没有自己的对话循环，runtime 门默认关（`_conf_schema.json:245-249`）。

数据落单个 SQLite（`memory.db`），**没有向量库**：种子是字面（字符二元组 Dice ＋ 倒排，`recall/seed.py:36-55`），扩散是沿图两跳（`recall/spread.py:44-75`）。抽取与人格合成走宿主 provider。

一句话评价：**检索比预期弱，记忆治理比预期强。** 该借的是后者。

### 1.2 结构（只记和本项目对得上的） `[源码]`

- **不变量**：`sources` 追加不覆盖；`nodes` 存 head、`versions` / `artifact_versions` 每次落库留一份；`artifact_evidence` 是版本级证据；`ledger` 是哈希链（`store/schema.sql:6-133`）。
- **写入时机**：白天不抽取——`app/flush_policy.py:167-168` 只要 `dream_only` 就一律拒，记忆在夜间（23:50 nightly / 03:30 Dream）形成。单位是"话题 → 0~6 条 Artifact"（`perceive/extract.py:9-73`），层级 `fact/scene/detail/impression`。
- **检索打分**：`3.0×字面 + 2.0×标签 + 1.5×情绪 + 1.0×weight + 0.5×新鲜度 + 1.5×激活 − 2.0×刚用过`（`recall/score.py:165-182`）。**证据闸门**：`weight / 新鲜度 / 刚用过` 只参与排序，**不能单独让一条记忆过门**；过门下限是 `max(0.5, top×0.3)`（`recall/gate.py:3-13, 30-36`，我逐行读过）。
- **组装**：模板句按层拼，层序 `persona > impression > scene > detail > fact > example`，超预算**从低层开始丢**（`recall/assemble.py:128-181`）。有"组装"，没有"编写"。
- **巩固**：`R = exp(−t/S)`，`S = 30 × salience × (1+0.5×hits) × shape × emotion`；`R<0.3` 直接 `state=forgotten` 并踢出缓存（`core/strength.py:16-31`、`maintain/decay.py:35-63`）。合并：active scene/detail 超 500 条才动手，≥3 条拼成一条 impression（`maintain/merge.py`）。印象：同 id 上出 revision、正文换成新的（`maintain/impression.py:22-33`）。
- **人格**：带版本的单链 Artifact，7 天一次有界增量改写，**必须返回 `change_reason`**，文本没变就不写（`services/maintenance_service.py:180-256`、`app/persona_synthesis.py:98-150`）；装载时做**结构溯源校验**——只有 `origin=seed`（corpus hash 自洽）或 `origin=dream + synthesis 版本 + 理由 + 证据 lineage` 才允许进上下文（`core/bot_behavior.py:73-145`）。

### 1.3 值得借

| # | 机制 | 本项目缺什么 | 怎么落 |
|---|---|---|---|
| ① | **人格的版本链 ＋ 理由 ＋ 回滚 ＋ 装载溯源**（`maintenance_service.py:330-368`；`core/bot_behavior.py:73-145`） | [`../persona.md`](../persona.md):49 写了"外部核验、留版本、可回滚"，但 `loop/persona.py` 只有 `read/write/stamp/due/rewrite` ＋ append 到 `persona.log`：**没有版本目录、没有 diff、没有回滚、没有"为什么改"** | person 目录每次改写留一份 `<时间>.md` ＋ 一行 reason ＋ 引用哪几条记忆；网页给 diff 与"退回上一版" |
| ② | **"改过"读得出来**：从版本表取最早一版，渲染成 `（三周前觉得…）` 补在句后（`recall/history.py:77-85`） | [`../解构与重建.md`](../解构与重建.md) §1 自认的三处硬伤之一：`remember` 覆盖旧 note，旧说法只剩 `edited` 时间戳 | note 改成只增版本 ＋ `recollect` 拼材料时把最早/上一版作为一句括号带上。**这是 `说法/` 那份追加日志的读法，不是新增一层**，与 [`../决策记录.md`](../决策记录.md) 2026-10-19「说法只做追加日志」不冲突 |
| ③ | **回忆的诊断字段**：每次召回留 `候选数 / gate 通过数 / 阈值 / dropped_ids / fallback`（`recall/pipeline.py:388-405`） | `recall_log` 只有 `(ts, question, event_ids)`——只记"命中了谁"，不记"为什么没命中" | 加几列：候选数、赢家分、被抑制的候选、材料段数与丢弃段数。纯量具，符合 [`../../README.md`](../../README.md):200「量具只报警，不裁决」 |
| ④ | **人格评测的三种手法**：植入私标记查泄漏（"许澄/栖云城/橙色纸鹤"不得到处出现）＋ 双跑查幂等 ＋ 负例（没证据就不该造出人格），退出码卡阈值（`benchmarks/persona_growth.py:96-134`） | [`../persona.md`](../persona.md):81-88、110-114 写了"固定 10–15 题、一个脚本"，仓里没有这个脚本 | 照这个形状写 `tools/persona_probe.py`。零风险，且正对 [`../理想.md`](../理想.md) 的「私有」 |

### 1.4 借不了

| 做法 | 为什么不 |
|---|---|
| 事后抽取 ＋ 话题切分（90 分钟 / Dice 0.12）＋ 夜间批处理 | 正是 [`../决策记录.md`](../决策记录.md) 2026-10-19 否掉的那条（"被否掉的：按轮切；事后用模型判是不是同一件事"）。事件边界必须由**经历时的 `remember`** 给出，否则"把运行态判决冻成库里的属性"原样成立 |
| 每条记忆挂 `level/tags/about/confidence/arousal/valence/funny` | [`../决策记录.md`](../决策记录.md) 2026-10-19 否"给每个节点标标签"；[`../事件状态与读取.md`](../事件状态与读取.md) §7「杏仁核、情绪、唤醒：不建模」。把情绪降成两个浮点数喂进衰减公式，正是 [`../理想.md`](../理想.md) 的「把像人当成演出」 |
| 关系档位 → 回复概率 / 语气 / 允许的主动类型（`core/interaction_policy.py:92-196`） | 掷骰子替她决定说不说话，与 [`../决策记录.md`](../决策记录.md) 2026-10-25「把停不停还给模型」反向 |
| 合并成印象 / 就地改写印象 | 把"上回你说到一半"压成"他有点倔"，而 [`../理想.md`](../理想.md):17-19 要的恰恰是那半句还在 |
| 每轮自动注入记忆块 | 与"只有回忆进上下文"整条读取设计相反，且每轮变长＝前缀缓存每步作废（[`../决策记录.md`](../决策记录.md) 2026-10-25 墙钟那条同一个道理） |
| 自造综合指数给自己打 `100 / 100` | 本项目 [`../../README.md`](../../README.md):200 写着"没有自动评分，量具只报警，不裁决"。它自己的明细里也能看出问题：8 个查询注入的片段前两句永远是同样两条 pinned 印象 |

### 1.5 存疑

- **`explore=0.1`**（`recall/score.py:297-304`）：是"采样而非 argmax"的廉价近似，还是检索抖动？没有条件分布 `p(z|o)`，倾向后者 → 不做。
- **单条记忆的 `R(t)` 留存**：可借"用 R 当强度"，**不可借**"R<0.3 就整条看不见"——那是它的"忘了＝看不见"，与 [`../../README.md`](../../README.md):82「忘的是连接强度，原文一直保留」相反。
- **四级披露**（`core/disclosure.py:100-133`）：分级概念与 [`../理想.md`](../理想.md):19 同向，但它的兜底是固定句（hint → "你隐约记得有件相关的事。"）；换成固定句会把回忆变模板 → 只借分级，不借句子。
- **它自己也没验证**：v0.5.3、31 星、benchmark 是自造 19 事件；而且作者自己的预注册判据把 Dream 分诊判成 `NOT_ACCEPTED`（"成本 −72.8%，重要事实召回 65.2%"，`benchmarks/results/dream_tradeoff.md`）。凡本文说"它做到了"，都是**代码里这么写**，不是线上跑通。

---

## 2 dsh-soul（icyaaaww）

### 2.1 它是什么 `[源码]`

DSH 的普通 cordis 插件（能注册 `systemPrompt.context` / 工具 / 命令 / 事件 / compaction 后端）。**名字叫 soul，但里面没有"我是谁"的文本，也没有情绪、关系、对人的印象**——0.8.1 起把旧 Soul 的记忆检索与后台学习整块删了（`CHANGELOG.md:137`，原文"移除旧 Soul 记忆、后台学习…"）。现在剩两块：

1. **任务状态骨架 ＋ 图压缩**：把每轮机械编译成 4 类节点（`constraint/decision/artifact/progress`），压成约 600 字节卡片注入系统提示；并把宿主的"模型写摘要"压缩后端换成"图选子集"。
2. **harness 自演化 Evolve**（0.8–1.0）：观测分歧 → 规则表归因到某个组件 → 一次编辑会话改它 → 结构门 ＋ 成对 A/B ＋ 影子试用 ＋ 回滚。

### 2.2 值得借之一：自改的护栏（全篇最值钱的一节）

链路（`src/evolve.js:30-39`）：

```
diagnose(免费·规则表) → edit(一次编辑会话) → structure(免费·结构门)
  → probe(同提交成对 A/B) → checks(声明的验收) → decide → shadow → 用户 /evolve verify 才转正
```

四条硬的：

- **边界是代码，不是散文**：`<!-- ANCHOR:BEGIN -->…<!-- ANCHOR:END -->` 之间的内容必须与基线**逐字节相同**，删、改、数量变都抛 `anchor_tampered`（`src/tree.js:73-119`；我在 `harness/default/system.md:1`、`guardrails.md:1` 确认了实际用法）。作者的理由写在注释里：**"可编辑的系统提示不能变成可编辑的权限"**。
- **只能改被诊断出的那一个组件**（`src/diagnose.js:113-119`，越界报 `off_target`）；每个组件有字节上限，每轮固定税上限 `MAX_TAX_BYTES = 2400`（`src/gate.js:13, 47-53`）。
- **采纳要有外部证据**：成对重跑同一提交、同一任务、同一验收定义，交替顺序；验收文件先声明 `protectedPaths` 并核指纹；**每一对都赢**才算，"步数变少 / 提示变短"不能单独取胜（`src/validation.js:89-119`）。
- **转正不靠沉默**：影子期只有"同类任务 ＋ `verified`"才 `pass`，**用户纠正一条直接 fail**（`src/watch.js:16-22`）；冷却按**轮**算而不是墙钟（`src/evolve.js:123-133`，理由是"一次编辑的代价是前缀缓存重建，摊在轮上"）。
- **版本**：内容寻址 ＋ 幂等提交、只追加 ledger、任意点回滚、**retired 不自动复活**（`src/store.js:333-363`，`retired_revision`）。

**对 palimpsest 的意义**：这些逐条对上 [`../persona.md`](../persona.md) §4（核验四条）与 [`../理想.md`](../理想.md) 的归属（"外框不归她"）。而且 —— 这一点是本文最想记的 —— **SeekMemory 与 dsh-soul 互不相干，却收敛到同一套**：可写文件里嵌一段她不能动的锚定区、每次改写带理由与证据、失败的版本不静默复活。本项目这四条现在只在文档里，一条都没实现。

### 2.3 值得借之二：五个小东西

| # | 机制 | 本项目缺什么 | 怎么落 |
|---|---|---|---|
| ⑤ | **检索充分性提示**：把当前提问与卡片做词面重合，`< 0.2` 就在注入末尾加一句"先 recall，别凭印象"，每轮最多 3 次（`index.js:236-248, 368-370`） | `RELEASE-1.5.md` 自己承认的死穴："模型不主动检索时，这套机制等于不存在" | 每步用当前块与最近一次回忆算词面重合，低时在**末尾帧**加一行"这问题你可能不记得，先 recall"。不进前缀、不花模型调用 |
| ⑥ | **回捞写回指针**：成功的检索写成 `reference` 节点，同一问题第二次直接命中（`src/memory.js:182-192`） | `recall_log` 已存在但只记不返 | 第二个相同/近似 query 直接拿上次的 event id 当种子 |
| ⑦ | **覆盖边界与来源要说出来**：`[索引] N 条可检索（seq a–b，跨 K 个会话）`；检索结果逐条标 `source: node|span`、`current: true/false`；压缩产物末尾"另有 29 个片段未展开（seq 4–33）"（`src/memory.js:119-134`、`src/directory.js:76-83`） | [`../理想.md`](../理想.md):19 要"诚实地说这段我是查到的"，但回忆接口没有来源字段；四档摘要之外她不知道"还有没有更多" | `recollect` 产物尾部带一行 anchor（来自哪几个事件）；旧摘要**正文不动**（守住 [`../决策记录.md`](../决策记录.md) 2026-10-25），只在段尾加一行"另有 N 条原文（步 a–b）可 expand" ＋ 一个只读 `expand(from,to)` |
| ⑧ | **跨会话交接行**：`turn/end` 把最后一条 progress 写进 meta，**只在换了会话时**拼在卡片最前（`src/memory.js:102-134`） | 跨场靠四档摘要 ＋ 归档，没有"上次停在哪"的显式指针 | 归档时写一行 handoff，开场拼在 sink 之后；同场不显示 |
| ⑨ | **"有来源的状态"这条方法论**：所有状态量都是事件的函数，没有一个允许模型自报（`src/store.js:685-694`） | [`../她存在于何处.md`](../她存在于何处.md):100-101 的"心境"未定 | 若要做心境，先做成从事件网统计出的量（被点亮事件的强度分布、纠正率、距上次说话的时长），**不许她自报"我今天心情…"** |

### 2.4 借不了

| 做法 | 为什么不 |
|---|---|
| 整套 Evolve 自演化（组件树 ＋ 经验编译 ＋ 探测/影子/转正） | 它优化的是**任务成功率**，不是"她是谁"；代价是 `src/evolve.js` 25 KB 加 budget/probe/watch/validation/host-runner 全套。借闸门形态，不借演化整机 |
| 用图压缩替换摘要模型 | 等于给"约束"开第二套常驻记忆通道；而且摘要模型现在还能写"现在到哪了 / 未完成项"，确定性选点写不出这个 |
| 机械 claim 当记忆本体 | 4 类节点是"压缩失败模式"的补丁，不是"我们经历过什么"。拿 claim 替事件，会把"记得有分量"降级成"这轮定了什么" |
| 词面检索替掉嵌入 | 它是**故意的**（理由是"embedding 让每次 recall 不可审计"）；这个理由在本项目不成立——我们的读法本来就是"线索→种子→沿网与内容扩散→竞争"，回退到词面只会让扩散变脆 |
| 让模型自己决定何时改自己 | 它自己也禁止（`index.js:451-459`，工具只回答"有没有资格"，付费探测只能由用户命令启动）。**这是反向给 [`../决策记录.md`](../决策记录.md) 2026-10-25「不给 persona 工具」背书** |

### 2.5 存疑

- **`systemPrompt.context` 的多个块在宿主侧是否各自成为缓存段**：它靠这个假设把易变内容摆在 940（`index.js:338-353`）。DSH 实现是 asar，没读；本项目的实测是按"整段前缀"算的（总命中 79%）。**这条决定 2.2 / 2.3 里"放在哪"的建议是否成立，应先验。**
- **"缓存稳定"它自己没有命中率实测**：只有"同一图状态同一字节串"这个必要条件，而卡片每轮都会变。
- **`needsLookup` 的 0.2 阈值在中文上是否可用**：`terms()` 是英文词 ＋ 汉字二元组，中文短问句的分母容易失真，无实测。
- **`docs/SKELETON-BENCH.md` 自己写着**：2400 字节时均匀采样**反超**骨架（86.8% vs 84.1%），600 字节是断崖；合成场景、可抽取项占 9/11。不能拿"25.5%→83.4%"去论证摘要模型该被替换。

---

## 3 AstrBot（AstrBotDevs）

### 3.1 它是什么 `[源码]` `[官方文档]`

通用 IM 聊天机器人框架：19 个平台适配器（`astrbot/core/platform/sources/`）× LLM agent 运行时（`core/agent/`）× 插件市场（Star）。部署 `uv run main.py`，暴露 `:6185`；数据在 `data/`（`data_v4.db`、`config/*.json`、`plugins/`、`knowledge_base/`）。

**它没有长期记忆层**，而且这是明确立场：`docs/zh/dev/star/plugin.md:579` 写着"对于长期记忆、知识库…不建议全部塞进提示词，可以优先注册为 `llm_tool`…也可以先在插件中检索出本轮真正相关的少量摘要再放入"。框架只给工具面与注入点。

### 3.2 值得借：平台现实那一层

| # | 机制 | 证据 | 对本项目的用处 |
|---|---|---|---|
| ⑩ | **UMO 一个键贯穿**：`{platform_id}:{message_type}:{session_id}` | `core/platform/message_session.py:18-19`（我读过原文件） | 配置 / 白名单 / 对话 / 知识库 / cron / workspace 全按它查。`rin` 侧通道该定成同一个形状 |
| ⑪ | **适配器契约只有四件**：`run()` / `meta()` / `convert_message()` / **`send_by_session(session, chain)`**——只凭可持久化的 session 字符串就能发，不需要活的 event | `core/platform/platform.py:134-149` | 给 `meta.via` 配一个对称的发送签名：`event 在场 → 回原会话` / `via 在场 → 按 chat_id 定向`。2026-10-28 那条决策只定了 `via`，缺这个对称面 |
| ⑫ | **叫醒是五路并集**（@自己 / @全体 / 引用 bot / 唤醒前缀 / 私聊 / 等待窗口 / 插件 filter），不是适配器里的 if | `pipeline/waking_check/stage.py:92-247` | `inbox` 现在按会话策略投递，缺"这一条到底算不算在叫我" |
| ⑬ | **群聊旁听缓冲 ＋ 被叫时补课**：deque 存最近 N 条，被叫时把"我上次说话之后"的记录一次性作为块注入，然后清空 | `builtin_stars/astrbot/group_chat_context.py:142-198` | 我们的 `wake` **只响铃不给正文**——她知道自己错过了，但不知道错过了什么、谁在跟谁说话 |
| ⑭ | **行内 `[昵称/时刻]: 内容` ＋ 被 @ 时行首 `⚠️[DIRECTED AT YOU]`** | 同上 `:199-272` | 比现在的 `外部 · 群「群甲」 甲：…` 更省 token、更好扫 |
| ⑮ | **@ 了但没说话 → 先问一句 ＋ 开 60 秒窗等续话**（窗口可续期） | `builtin_stars/astrbot/main.py:41-127`、`session_waiter.py:124-141` | 现在遇到空正文只会回"我在，什么事？"，没有"这话没说完"的概念 |
| ⑯ | **per-会话固定窗口限流，`stall` / `discard` 二选一** | `pipeline/rate_limit_check/stage.py:60-92` | 我们只有**全局日预算**——群里刷屏会把一天的额度花在一个群上 |
| ⑰ | **角色 × 工具权限收窄 ＋ fail-closed**：`allow_execution` / `allow_network` / `filesystem_scope ∈ {none, workspace, host}` 按 member/admin 分档；装不上沙箱就一律禁执行 | `core/agent/computer_tools/util.py:24-46, 92-180` | `inbox` 只有 `sender_id/name`，**没有 role**；`run`/`write` 对谁开口都一样。可在 `chats` 加 `trusted`、按来源裁剪**工具面**，而不是在工具里判断 |
| ⑱ | **`mark_as_temp()`：一句进请求、不进历史，是一等公民标记** | `core/agent/message.py:68-77`；落库处 `internal.py:522-523` | 我们现在是四处手写"只拼进输入、不落库"（wall clock / ask / watchdog / system-reminder）。它的代价是 4 个 provider 都要剥字段；本项目只有一处落库路径，反而更划算 |
| ⑲ | **发言人表** `umo_senders(umo, sender_id, sender_name)` | `core/db/po.py:346-382` | [`../理想.md`](../理想.md):44「对人的持续印象」最便宜的落点：`people(chat_id, sender_id, name, first_seen, last_seen)` |

### 3.3 借不了

| 做法 | 为什么不 |
|---|---|
| 记忆 / 长期信息外包成插件或 `llm_tool` | 直接撞 [`../理想.md`](../理想.md):59-60「记忆层不该做成可插拔的通用服务」与 :28「记忆不是又一件工具」。它这么说是**因为它没有**记忆层 |
| 压缩 ＝ 摘要写成假 user/assistant 对 ＋ 丢整轮 | 每压一次重写整段旧历史，正是 [`../决策记录.md`](../决策记录.md) 2026-10-25 否掉的"旧档跟着重写"；而且摘要以"Our previous history summary…"混进历史，等于**她说过一句"我收到了摘要"** |
| 主动 ＝ cron 定时任务 ＋ `random() < 0.1` 插话 | [`../理想.md`](../理想.md) 的「容易滑掉的地方」第一条就是"把空闲改写成任务"；它自己在 `docs/zh/use/proactive-agent.md:5` 写"这不是一直运行的自主监控" |
| 一条对话一行 JSON history ＋ per-UMO 锁 | 本项目的单写者前提（网页只读库、只往 inbox 写）就是为了不面对"两处写同一批表" |
| 进程内全权胖 Context（35 个方法，能 `get_db()`、能 `register_web_api`） | 我们需要的是**能被审计的窄口**（现在就是 `POST /inbox`），不是第三方写适配器的开放性 |
| 群聊历史塞进 user 内容 | 位置不对：本项目 sink 里已经写明"套着标签的都是系统注入，不是对方说的"，群消息该走 `inbox` 的正规渲染（`source` / `chat_name` / `sender`），不是一坨字符串 |

### 3.4 存疑

- `active_reply.prompt` 是死键：读出来但没用（`group_chat_context.py:70, 86`）。
- 群聊旁听缓冲是**纯内存**（重启即清），持久化那条是另一套开关 — "她记不记得群里刚才说什么"到底跨不跨重启，文档没写清。
- **没有跨平台身份合并**（只有 per-UMO 的 `umo_senders`）。这与本项目"网页和 QQ 是同一个对话的两扇门"**正相反**——那是它的设计选择，不是缺陷；反过来印证 `meta.via` 是必要的（它不需要 `via`，因为它没有这件事）。
- 文档与代码漂移：`docs/zh/dev/plugin-platform-adapter.md:85` 写 `super().__init__(event_queue)`，而 `Platform.__init__` 的签名是 `(config, event_queue)` — 照文档抄会 `TypeError`。

### 3.5 「活人感」来自哪一层（第二轮，专查说话那一侧）

社区口碑说 AstrBot "有活人感"。这层不在记忆里，也不在上下文装配里，而在**她说完之后的发送侧**。逐条查下来，五个手段里**只有一个真正改变了模型能做什么**：

| 手段 | 位置 | 模型看得见吗 | 改变她的行为吗 |
|---|---|---|---|
| **分段回复** | 配置 `platform_settings.segmented_reply`（`config/default.py:109-126`）；切在 `pipeline/result_decorate/stage.py:209-267`，发在 `pipeline/respond/stage.py:340-368`，等在同一文件 `:123-132` | 否——历史里存的仍是**整段** `llm_response`，她不知道自己被切成了三条 | 否 |
| **@ / 引用 / 前缀** | `result_decorate/stage.py:202-206, 434-447` | 否 | 否 |
| **`pre_ack_emoji`** | `pipeline/preprocess_stage/stage.py:104-124`，调模型**之前** | 否——是给对面那条消息贴 reaction，不发消息、不进对话 | 否 |
| **`send_typing`** | `ProcessStage`，`internal.py:221-225`，`finally` 里 `stop_typing()` `:492-496` | 否 | 否 |
| **`begin_dialogs`（人格预设对话）** | 进 `req.contexts[:0]`，`astr_main_agent.py:608-609`；构造与校验在 `core/persona_mgr.py:465-487` | **是，每轮都读** | **是** |

具体到"为什么它像人"：

1. **分段之间的间隔是随机的，而且跟这一段自己有多长相关**（`respond/stage.py:123-132`）：`random` 模式 `uniform(1.5, 3.5)`；`log` 模式 `uniform(log(wc+1, 2.6), +0.5)`，`wc` 是**这一段**的字数 —— 字越多"打得越久"。这是整块里最像人的一个参数。
2. **句子末的标点会被删掉**（`result_decorate/stage.py:116-119`，`split_mode=words` 时）——因为真人发消息不带句号。
3. **引用框只挂在第一条上，之后 `header_comps.clear()`**（`respond/stage.py:341-362`）——真人也是只在第一条上引用。
4. **超 150 字整段不分**（`result_decorate/stage.py:220-223`）：长文连发像刷屏，这条是刻意的。
5. **平台黑名单**：`qq_official_webhook` / `weixin_official_account` / `dingtalk` 一律不分（`:209-213`），因为那几个平台的被动回复窗口不允许连发。

**对本仓的判断（重要）**：本仓**没有发送层**——正文说出去就是投递，而且原文只读（[`../决策记录.md`](../决策记录.md) 2026-10-19「再巩固：原文只读」）。这意味着"AstrBot 式装饰"在本仓是**有害**的：装饰只活在 `result.chain` 上，记录里是整段，两份东西本来就分家；而本仓的记录**就是她回忆的原料**，一旦"说出去三条、记下来一段"，回忆会与事实错位。它自己也为相近的问题修过四次（"人格预设对话重复注入"，`changelogs/v3.5.3.md:28`、`v4.5.0.md:12`、`v4.14.7.md:4`、`v4.14.8.md:8`）。

所以这一层该这样落：

| 落点 | 怎么做 | 理由 |
|---|---|---|
| **她自己的正文** | 分段的观感、句子短、句末不带标点、一段只说一件事——**让她那样写**，不是发出去再切 | 记录即事实；她看到的就是她说的 |
| **投递那一步** | 只剩"什么时候出现"。而比 `sleep` 更贴 [`../理想.md`](../理想.md) 的做法是**把时机还给她**：`wait(until=...)` 到点再说 | 系统在发送时偷偷延迟＝替她决定节奏 |
| **`rin` 的通道层** | @ / 引用 / typing / reaction 是纯平台语汇 | 她的记录里不该有平台语汇的位置 |
| **唯一值得原样搬** | **`begin_dialogs` 的形态**：真 few-shot（交替的 user/assistant 对）、每轮现插、`_no_save`、构造时校验偶数条 | [`../persona.md`](../persona.md) 那五节是**形容词**；缺的正好是样例。带着它的教训搬：必须幂等 |

两个它自己没解决的问题，别一起学过去：`need_active_reply` 对**每一条**群消息掷骰子、**没有冷却、没有日上限、不看她上次说话隔了多久**（`group_chat_context.py:112-130`，全文 335 行只有两个 deque 和一把锁）；`send_typing` 没有心跳刷新，模型卡住时 typing 会在平台侧自己过期。

### 3.6 调度：三层里只有两件**形状**能用（第三轮）

**① 消息调度——它的答案，对本仓是负担**

| 它怎么做 | 证据 | 本仓对应物 |
|---|---|---|
| **一条全局队列**，所有平台共用一个 `Queue()` | `core_lifecycle.py:208, 222, 276-278` | `inbox` 一张表 |
| **单消费者**：`while True: await queue.get()`，取到就 `create_task(scheduler.execute(event))` —— 出队串行、执行并发 | `event_bus.py:55-75` | 一个循环 |
| **九段洋葱**：`yield` 前是前处理、`yield` 后递归回来做后处理；`is_stopped()` 在前后两点都查 | `pipeline/stage_order.py:3-13`、`scheduler.py:38-82` | `driver.py` 里几步顺序判断 |
| **per-UMO 锁**罩住 `build_main_agent` → `step_until_done` → `_save_to_history`；正常一轮 = **1 次加锁 ＋ 1 次主库整写** | `process_stage/.../internal.py:239, 540-545` | 单写者 |
| 中途插话：序号**在捕获时**分配、`while next_turn != seq: await condition.wait()` 严格放行、**没有超时** | `process_stage/follow_up.py:95-104, 138-144, 237-243`（248 行） | 不需要 |

这一整层换来的性质（并发正确、插件可插、多平台并行），本仓靠"**单进程 ＋ 单写者 ＋ 一个入口**"免费得到。硬套是负收益——尤其 follow-up 那个 248 行状态机，本仓的等价物是"同一时刻只有一个循环在跑"。

**② 时间调度——表的形状值得抄，机制不值得**

`cron_jobs` 表很紧凑（`db/po.py:186-216`）：`job_id`、`name`、`description`、`job_type(basic|active_agent)`、`cron_expression`、`timezone`、`payload(JSON)`、`enabled`、`persistent`、`run_once`、`status`、`last_run_at`、`next_run_time`、`last_error`。

- 重启恢复靠 `sync_from_db()` 把 `enabled and persistent` 的重新挂回（`cron/manager.py:140-154`）；错过超过 5 分钟不补跑（`misfire_grace_time=300`，`:334`）。
- **"到点"被伪装成一条进来的消息**：`CronMessageEvent`（`cron/events.py:17-74`）——平台名写 `"cron"`、`sender.nickname="Scheduler"`、`is_wake=True`、覆写 `send()` 直接按会话发；它**不走 pipeline**，直接 `build_main_agent` ＋ `step_until_done(max_steps)`（`cron/manager.py:589-598`）。
- 跑完把结果写回历史：追加一对**假消息**（`{"role":"user","content":"Output your last task result below."}` ＋ 一条摘要）（`utils/history_saver.py:20-30`）。
- **三处时区坑**（都值得记）：crontab 的星期数字与 APScheduler 的 Mon=0 冲突，专门写了 `_normalize_crontab_day_of_week`（`cron/manager.py:37-94`）；DB 里 `next_run_time` **永远是 UTC 且 SQLite 存成 naive**，显示前要先补 `tzinfo=utc` 再转（`cron_tools.py:173-189`）；时区名解析失败只 warning 并退回系统时区。

**③ agent 内部——两条值得看**

- **超限不硬停**：`max_steps`（默认 128）到了不是砍掉，而是**拔掉全部工具**、塞一句 `MAX_STEPS_REACHED_PROMPT`、再跑最后一步逼它收尾（`tool_loop_agent_runner.py:1159-1176`）。这与本项目 `ask.txt`「问了还是只说话就停下」是同一类收束动作。
- **打断本身进历史**：`/stop` 触发后写一对"用户打断 / 我收到"并标 `_aborted`（`:1568-1589`）。照 [`../理想.md`](../理想.md):43「会改主意，也记得改过」，这条迟早要用：**被打断/改主意要留痕**。

**④ 她能不能给自己排活——`future_task`（最有用的一件）** `[源码]`

一个工具四个 action（`cron_tools.py:55-103`），由 `provider_settings.proactive_capability.add_cron_tools` 控制是否出现在工具面上（`default.py:178-180`）：

- `create` 必须给 **`note`**——描述里写的是 *"Detailed instructions for your future agent to execute when it wakes"*；再加 `cron_expression`（循环）或 `run_at`+`run_once`（一次性，ISO 带时区）二选一。
- `payload` 钉死三样：`session=当前会话`、`sender_id=当前发言人`、`origin="tool"`（`:133-138`）——**任务只能投回发起它的那个会话**。
- 边界三段式：**edit 只许本人**（`:213-235`）；**delete 放宽到群管理员**（`:302-309`）；**list 按会话过滤**，群聊能看到本群所有人的任务并标 creator，私聊只看自己的还会补一句"有些任务没列出来"以免它以为丢了（`:336-360`）。
- **没有次数上限、没有频率限制**。结果不是工具返回，而是任务到点时把 `note` 当消息送进主 agent，跑完写回同一会话的历史。

**这一条和本仓的接口是现成的**：`解构与重建.md` 的 `thread`（未完成的事）就是前瞻记忆的落点——给 thread 加一个 `due_at` 即可，不需要新开一类对象。**但 due 必须落库，不能挂在 `wait` 上**：她说"明天再问你结果"，如果当晚有人说话把她叫醒，那个"明天的意图"就没了（`wait` 的唤醒窗口关闭）。而本仓的时间感目前只有末尾一行墙钟，没有"将来的我该记得这件事"。

另外这四件事有个共同点值得单独提：下面这些**都别学**——

| 别学 | 为什么 |
|---|---|
| 全局队列 ＋ 每事件一个 task | 多平台多 profile 才需要；本仓只有一个入口 |
| per-UMO 锁 ＋ follow-up 顺序门 | 单写者已经买断 |
| 九段洋葱 | 九段里六段是白名单/profile/插件 filter/平台黑名单，都是多租户负担 |
| 跑完拿一对假消息把摘要塞回历史 | 本仓原文只读、`recollect` 重写；让历史里出现"她没说过的话"是硬伤 |
| `Context.register_task` 那种后台任务 | 已标弃用，且**起了不 await、没有回收**（`context.py:950-963`、`core_lifecycle.py:318-321`）——没人回收的任务在本仓没有归宿 |

### 3.7 示例实现：仓里基本没有 `[源码]`

| 以为有的 | 实际 | 能不能当样板 |
|---|---|---|
| `samples/` | 只有一个 `stt_health_check.wav`（语音自测素材） | 不能 |
| 示例 bot | **没有这个目录**；官方模板在**仓外** `github.com/Soulter/helloworld`，`astrbot plug new` 的实现就是下载那个仓库再补 `main.py` 骨架（`cli/commands/cmd_plug.py:68-70, 96`） | 模板可用，但不在仓里 |
| `builtin_stars/astrbot/` | 自带对话 bot 的**钩子层**：`main.py`（空 @ 追问、生命周期）＋ `group_chat_context.py`（群聊旁听/插话）＋ `skills/`；`metadata.yaml` 只有 6 行 | 半能——它是"往主链路挂钩子"的样板，对话逻辑在 core |
| `builtin_stars/builtin_commands/` | 内置命令：`/help /sid /name /reset /stop /new /stats /provider /set /unset`，**一个命令一个文件**＋i18n | **能——仓内唯一"最小可跑"的插件样板**：类 → `@filter.command` → 委派出去 |
| 人格预设 | **没有**。全库只有一个 seed：`"You are a helpful and friendly assistant."`（`persona_mgr.py:9-30`），启动时插入 `default` persona | 反过来说明：那些"活人感"口碑背后的人设，**都是用户自己写的** |
| "完整 bot 产品"样例 | 只有部署层：`compose.yml` ＋ `k8s/astrbot_with_napcat/`（含两个实战坑：initContainer 首次生成 machine-id 并复用、固定 `hostname` ＋ `terminationGracePeriodSeconds: 60` 防 QQ 风控） | 部署能看，产品组合没有样例 |

所以 AstrBot 的"示例"是三种东西：**文档里的片段**、**仓外一个模板仓**、**插件市场**。它靠分发"可复制的模板"扩张生态；本仓的分仓判据（[`../../README.md`](../../README.md):98-100）选择的是另一种——**一个真在跑的实例**（`rin`）。这一层本仓比它清楚，不必学。

---

## 4 合起来看：如果只做三件

| 排期 | 做什么 | 出处 | 冲突检查 |
|---|---|---|---|
| 1 | **persona 核验器 ＋ 版本链 ＋ 回滚**（锚定区逐字冻结、每次改写带 reason 与证据 id、退回旧版、失败版本不自动复活） | dsh-soul 2.2，SeekMemory ① | 与 [`../persona.md`](../persona.md) §3–§5 同向，**是把已写下的东西落地**，不新增机制 |
| 2 | **回忆的可见性三件**：`recollect` 带 anchor 行；`recall_log` 加诊断列；四档摘要段尾加"另有 N 条未展开"＋只读 `expand` | dsh-soul ⑦、SeekMemory ③ | 段尾加行不动摘要正文，守住 2026-10-25「旧摘要不重写」；`expand` 对上 [`../持续在场-API实现.md`](../持续在场-API实现.md) 那条未定项「是否保留原始块做审计」 |
| 3 | **通道薄壳**：`people` 表 ＋ `sender_role` ＋ per-会话限流 ＋ 空正文的"叫一下"分支 ＋ 定向发送签名 | AstrBot ⑩⑯⑰⑲⑪ | 全在 `rin` 侧，核心不动；`people` 是"对人的印象"的原料，不是印象本身 |

**只借形态、不落地**的三条：`mark_as_temp` 的统一注入构造器、跨会话 handoff 行、检索充分性提示。它们都便宜，但要等"每步与回忆的耦合点"定下来再动。

**第四条候选**（3.5 带出来的，代价最小）：把 persona 从"形容词"补成"形容词 ＋ 几组样例对话"——借 `begin_dialogs` 的**形态**（交替的 user/assistant 对、每轮现插、不落库、构造时校验偶数条），不借它"每轮都插进历史"的落法（它为这个修过四次）。这一条正对 [`../persona.md`](../persona.md):39-42 里"风格靠样例"那句还没落地的地方。

**第五条候选**（3.6 带出来的）：给"未完成的事"加到期时间——`thread` ＋ `due_at`，到点往 `inbox` 插一行（带 `note` 与回给谁），由 `wait` 自然醒来；**不新开执行路径、不写假对话**。借用 `future_task` 的边界三段（发起会话 ＋ 发起人钉死；改严格、删放宽给管理员）。前置是先把时区定死（`wait(until="14:30")` 现在只有本机时区）。

**一句提醒**：这三仓最容易让人心动的是它们的"记忆"，而恰恰那一层全都与本项目相反（注入 vs 重建、事后抽取 vs 经历时写、条目 vs 事件）。真正稀缺的是它们各自在**边界**上磨出来的东西——她可写的文件里哪一段不能动、什么算证据、什么时候该说"我不确定"、群里的哪句话算在叫她。

---

## 5 宣传与证据：三家的自评分别能信到哪

| 仓 | 对外说的 | 证据 | 能信到哪 |
|---|---|---|---|
| SeekMemory | "综合像人指数 **100 / 100**"、"1000 条记忆压缩 **850×**" | 19 条合成事件 · 8 个查询 · 模拟 30 天；gold 由构造给出（`benchmarks/results/latest.md:3`） | 低——见下 |
| dsh-soul | 1200 字节预算下细节保留 **25.5% → 83.4%** | 300 个确定性合成场景，自带「局限（必读）」 | 中——它把边界写清了 |
| AstrBot | 不报指标 | 无；"活人感"是用户口碑 | 不是可引用证据，是产品信号（但信号很强，见 3.5） |

**SeekMemory 那份 100/100 的四个具体问题**（都出自它自己的 `benchmarks/results/latest.md`）：

1. **逐查询明细里，8 条注入全部以同样两句 pinned 印象开头**，连"我今天几号"也是（:41-48）。"叙述化比例 100%"与"层级命中 100%"里有一大块是常驻层撑起来的，它不区分查询。
2. **"压缩 850×"是"注入 17 字/轮"这个常数算出来的**，记忆从 50 条到 1000 条都不变（:21-26）——测的是"常驻层很短"，不是"检索压得好"。
3. **"BM25 开关对照"两行数字完全相同**（6/6、53.75 字、0.137/0.243 ms，:50-56）——这个消融没有区分度，等于没做。
4. 反过来，它另一份评测是诚实的：Dream 分诊按**预注册阈值**把自己判成 `NOT_ACCEPTED`（成本 −72.8%，重要事实召回 65.2%，`benchmarks/results/dream_tradeoff.md:5`）。**同一个仓里，营销口径和工程口径并存**——读 README 的数字要先看它出自哪份文件。

**dsh-soul 的自限值得抄**（`docs/SKELETON-BENCH.md:36-47`）：明写"优势来自压缩比，不来自绝对质量"（2400 字节时均匀采样 86.8% **反超**骨架 84.1%）、"11 个记忆项里 9 个是可抽取的，83.4% 里有一部分是抽取对了而不是选对了"、"摘要替代不是 LLM，别当基线"。

**AstrBot 反过来证明了一件事**：`活` 这个感受**不依赖模型能力和记忆质量**。它那些手段全是确定性、便宜、与模型无关的（随机间隔 · 删句末标点 · 只给第一条挂引用 · 150 字以上不分段 · 被叫必回 / 窗口期必回 / 没叫 10%）。这对本仓的意义不是"照搬"，而是**排期上的提醒**：本仓现在把"活"几乎全押在记忆与人格上，而人第一眼感到的"活"来自**节奏与语汇**——[`../理想.md`](../理想.md) 里"时间感 / 会犹豫 / 会无聊"三条都长在这层，而现在实现里只有末尾一行墙钟。

一句方法论：看这类仓库的宣传先问三件事——**样本多大、gold 谁给的、消融有没有区分度**。三家只有一家肯把自己的结论判负。

---

## 6 与已有调研的关系

- 不重复 [`参考项目-按topic扫一遍-2026-10.md`](参考项目-按topic扫一遍-2026-10.md)：那篇按 topic 扫，落点是时间感 / 开口闸门 / 睡眠期整理 / 上下文形状 / 重要度与反思。本文落点是**自改护栏、回忆可见性、通道现实**，三块那篇都没覆盖。
- 新增一条对照证据：[`../解构与重建.md`](../解构与重建.md) 的 trace / schema 分层，在 SeekMemory 里有独立实现（不可变 Source ＋ 版本化 Artifact ＋ 哈希链 ledger）。
- 本文是**记录**，不是设计；采纳与否记 [`../决策记录.md`](../决策记录.md)。

## 7 没做的事

- **一个仓都没跑**：没有实测它们的 bench、没有量它们的阈值在本项目语料上的表现。
- 没读：AstrBot 的 `provider/sources/*`、`dashboard/`、`mcp_client`、`subagent_orchestrator`、`skills/`、`trace*`；SeekMemory 的 `.claude/`（快照解压失败）与 `docs/en/` 逐篇；dsh-soul 的 `src/host-probe.js` / `src/probe.js` 全文（只读接口面）。
- FTS5 span 检索与 AstrBot 知识库那套（dense＋sparse＋RRF＋rerank）没细看——与本项目"线索→扩散→竞争"不是一条路，暂不评估。
