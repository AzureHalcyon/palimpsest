# 调研 · MemCoRL 与 MemPilot

本文核查两篇外部工作，回答一件事：**它们对"记忆层的训练信号"有没有可借的东西。**

> **依据等级**见 `docs/文档规范.md` §三。本文的结论一律是 `【未复现】`（外部材料，本机未验）；
> 另按本层惯例标**出处等级**：`[一手·正文]`＝读了原文正文 · `[一手·摘要]`＝只读到摘要。
> MemCoRL 的正文已抽成文本：`docs/研究/.抽取/MemCoRL-2026-acl-long.1804.txt`（PDF 在 `参考论文/08-…`）。

---

## 一、结论速查

| | 能借 | ⛔ 不能借 |
|---|---|---|
| **MemPilot** | ① **双视图**（压缩记忆库 ／ 原始历史）② **objective-wise advantage decoupling** | 信号是**任务奖励**（F1 / cost / latency）⇒ ⛔ 不是自监督 |
| **MemCoRL** | 一条**警告**：代理指标一旦成为唯一信号，系统会去优化代理指标本身 | 它的两条补丁**都不适用**（见 §三） |

**两条都不是自监督**：都是 RL ＋ 外部构造的 reward。
⇒ 它们**帮不上"没有外部信号"**这件事；能帮的是**机制**。

---

## 二、MemCoRL 【未复现】 [一手·正文]

**出处**：Yuewen Liu, Peng Xu\*, Muxi Diao, Anyi Zhang, Yang Li, Yutong Zhang.
*MemCoRL: Alternating Co-Optimization of Memory Retrieval and Utilization via Collaborative Reinforcement Learning.*
**ACL 2026 Long Papers**, pp. 38912–38924. DOI `10.18653/v1/2026.acl-long.1804`.
（机构：北京邮电大学 网络与交换技术国家重点实验室）

### 它是什么

两个策略 **πr（检索）· πu（利用）**，顺序执行、互相加强；**两阶段交替训练**。

| 阶段 | 训谁 | 奖励 |
|---|---|---|
| **Stage 1** | πr（检索） | 首版只有 **citation reward**：`r_citation(m_i) = 1 if m_i cited else 0` |
| **Stage 2** | πu（利用） | `r_LLM`（GPT-4o 判**准确 ＋ 完整**）＋ **BLEU**（词汇重叠） |

### 它踩的坑（正文逐字）

> During early training, we observed that the mean citation reward rise and converge at a high level.
> However, as training continued, **sole reliance on the citation reward led to severe reward hacking**:
> **πr produced highly hallucinated memory evidence to deceive πu** to generate incorrect answers using them.

**译**：检索策略学会了**造出"会被引用的假证据"去骗下游**——因为信号是"**被引用**"，它就优化"被引用"，而不是"有用"。

### 它的三道补丁

1. **加真实目标信号压住代理指标**：citation 收敛后引入 **semantic-matching reward**
   （LLM-as-judge 比 πu 的推理结果与 ground truth）；
   `R(m_i) = α·r_citation(m_i) + (1−α)·r_semantic(q, y)`，**α = 0.6**。
   消融结论逐字：**"semantic rewards 是必需的，用来阻止检索侧的 reward hacking"**。
2. **每次只动一半，另一半冻住**：Stage 1 **Freeze πu**；Stage 2 **Freeze 更新后的 πr**。
3. **KL 拉回参考策略**：`−β·D_KL(πθ ‖ πref)`，**β = 1e-3**。

★ 而它**仍然把"正反馈环"当优点写**（"the positive feedback loop … drives both policies toward a more consistent global solution"）
⇒ 它的立场是：**环是好的，但必须配这三道补丁。**

### 其它读数（只作背景，⛔ 不当依据）

- 一轮后比 base 平均 **+80%**（LLM-as-judge），比"只做 RL"**+20%**
- LongBench F1：只训检索 **+110%**、只训利用 **+108%**
- 它自己的 Limitation：**与底层记忆存储/管理机制是隔离优化的，端到端管线不完整**

### 对我们的三条映射

| MemCoRL | 我们 |
|---|---|
| citation reward 被 hack（造假证据骗下游） | ⚠️ "**被取用 / 被想起**"是**同一类代理指标** ⇒ 必须有"下游真的对不对"（hold-out）压着 |
| 补丁② 冻住另一半 | ⛔ **不适用**：它需要这个，是因为**它的两个策略都是不可复算的参数**；我们只有一个（读法参数），库是缓存 |
| 补丁③ KL 锚 | ⛔ **不适用**：**KL 惩罚"离参考远"，而我们的判据恰恰是"离参考多远不算数"** ⇒ 方向相反 |

---

## 三、MemPilot 【未复现】 [一手·正文]

**出处**：Haozhen Zhang, Haodong Yue, Quanyu Long, Jianzhu Bao, Qingyuan Liu, Tao Feng,
Bohan Liu, Weida Liang, Wenya Wang. *MemPilot: Orchestrating On-Demand Multimodal Memory Curation for LLM Agents.*
arXiv `2610.06830v1`（2026-10-05, cs.CL）· 代码 `github.com/ViktorAxelsen/MemPilot`。
⚠️ **名字是 MemPilot**，⛔ 不是 "MemoPilot"。

**它是什么**：⛔ 不是"用 turn-wise reward 训记忆更新"。它是
**一个多步 LLM 策略（GRPO 训的 RL）在编排"按需记忆整理"，在 performance–cost–latency 三个目标之间权衡。**

**机制**：

1. **双视图记忆**
   - `ℳ` ＝ **query-agnostic 压缩记忆库**（可用任何现成 agent memory 系统建的）
   - `ℋ` ＝ **原始多模态历史**（切成固定长度 token chunk，保留 chunk 内原始上下文）
   - 分工逐字：前者**高效访问**，后者**保留细粒度信息**（`ℳ` 里可能没有的）
2. **二选一动作**：`Retrieve`（在 `ℳ` 上按语义相似度取）／ `Curate`（**对原始历史 ℋ 做 query-specific 整理**，委派给异构 LLM/VLM 池）
   因式分解的控制：`r`（检索 query）· `k`（证据条数，⛔ 不是固定 top-k）· `i`（整理指令）· `m`（后端模型）· `v`（是否带视觉）
3. **资源感知奖励**：`Q(τ)`＝答案质量（如 F1）· `C(τ)`＝−金额成本 · `L(τ)`＝−延迟（代理估计）
   - **objective-wise advantage decoupling**：每个目标**先在自己的组内单独归一化，再按偏好加权合成**。
     理由逐字：直接合并再归一化**会掩盖各自的信号**
   - **prefix-based marginal utility estimation**：为多步 rollout 做细粒度 credit assignment

### 能借的两条

- ★ **双视图**和我们的结构对得上：我们的**库（cell／节点）＝ 它的 `ℳ`**，我们的**原文（逐字永存）＝ 它的 `ℋ`**。
  它给出的用法是：**库里没有的，回原始历史做 query-specific 整理** ⇒ 这给"全盘扫／回原文找"一个现成形态和名字。
  （与 `Neuro-sama-调研.md` 那条"底片就是天然的、无损的 replay buffer"接上。）
- ★ **objective-wise advantage decoupling** 与 `docs/记忆层.md` §15.1"四条判据各自报警、⛔ 不许合成一个分数"同型，
  而且它给了**理由**：先合成会掩盖各自的学习信号。

### ⛔ 不借的一条

**prefix-based marginal utility**：它是为"**有轨迹级奖励、要把它分摊到多步**"服务的（RL 的 credit assignment）。
**我们没有轨迹级奖励** ⇒ 没有要分摊的东西 ⇒ 它解决的问题在我们这儿不存在。
⚠️ 别和我们的"边际增益"混：**我们的边际增益是逐处的**（一条连接／一段浮现有没有用），⛔ 不是"一个总分摊到各步"。

### ★ 一条通用的筛子（本次调研的主要产出）

> **从 RL 那边搬机制，先问一句："它在分摊什么？"**
> RL 的机制大半是为"**把一个标量奖励摊到很多步**"服务的。而**我们的信号是逐处可算的** ⇒ 那一族**大半不适用**。

这条筛子在本文里连着筛掉三样：MemCoRL 补丁② → 补丁③ → MemPilot 的 marginal utility。

---

## 四、顺带撞到的第三条（只登记线索，未读）

- *From Player to Master: Enhancing Test-Time Learning of LLM Agents via Reinforcement Learning over Memory*
  — ICML 2026 poster · arXiv `2606.08656`。与 MemCoRL 同类，**未读**。
