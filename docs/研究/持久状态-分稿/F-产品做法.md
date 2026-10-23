# F-产品做法：桌宠、陪伴产品、agent 框架怎么维护 persona

> 调研日期 **2026-10-22**。问题不是记忆，是**「她是谁」**：persona 在这些系统里是**常量还是变量**；
> 若是变量，**谁在改、多久改一次、有没有衰减、有没有让模型自己改**。
>
> **本文不重复的三块**（已有调研的结论直接引用）：
> - 「设定该怎么写」已由 [`常驻-人物设定-调研.md`](../常驻-人物设定-调研.md) 处理（第一人称、短、"起点非成品"；不用第二人称角色卡）。
>   该文 §3 引 Letta starter persona 时是 `[二手]`（转引自一次提交）；本文 §4.1 拿到了 `.af` 原件，**那条升级为 `[一手]`**。
> - Neuro-sama 的公开事实与「官方架构文档不存在」已由 [`Neuro-sama-调研.md`](../Neuro-sama-调研.md) 与
>   [`参考对象与方法核查-调研.md`](../参考对象与方法核查-调研.md) §2.1 定案，本文不再碰。
> - PersonaForge 的内省校验、persona vectors、「LoRA 固化底色不能借」见
>   [`参考对象与方法核查-调研.md`](../参考对象与方法核查-调研.md) §2.3–§2.4、§5.1；本文只在「防漂移手段」处引用它的读数。
>
> **证据记号**：`[一手]` 读到官方文档/规格/源码/官方页面 · `[摘要]` 只读到摘要或摘要页 ·
> `[二手]` 转述（博客、评测、百科）· `[推断]` 本文推理 · `[查不到]` 本次未取到。
>
> **本次取证的两个硬限制**（影响 §2 的可信度，先说明）：
> 1. `blog.character.ai`、`support.character.ai`、`help.replika.com` 在本机**取不到**（连接超时 / Cloudflare 403 / 直连无响应），
>    两条独立通道（harness `web_fetch` 与 PowerShell `Invoke-WebRequest`）都失败。故 §2.1、§2.2 的机制细节只能降到 `[二手]` 或 `[查不到]`。
> 2. `www.talkie-ai.com` 在本机解析到**非公网地址**，`web_fetch` 直接拒取。故 §2.3 为 `[查不到]`。
>
> 本文**没有跑实验**，全部是外部材料；凡是「本项目」的话都标了 `[推断]`。

---

## 0 一句话

**「设定」在五类系统里绝大多数是常量；真正把它做成变量、并且交给模型自己维护的，只有三处半**：

| # | 系统 | 变量是什么 | 谁在写 | 性质 |
|---|---|---|---|---|
| 1 | **Letta（原 MemGPT）** | `persona` 内存块（第一人称自述） | 对话 agent 自己；或**后台 sleep-time agent** | 正式机制，可生产；**无衰减** |
| 2 | **SillyTavern + EJS 模板扩展** | 变量层（好感度、flag 之类） | **模型在自己输出里写 `setvar`** | 社区扩展，能用但脆；核心无此通道 |
| 3 | **Character.AI 的 Facts** | 关于角色/用户/配角的**事实条目** | 系统自动抽取 | `[二手]`；**不是设定本身** |
| 3.5 | **Generative Agents** | reflection（更高层推断） | 模型自己 | 写回的是**记忆**，不是身份段 |

**没有一家对 persona 设衰减**。带时间维度的只有两处，且都不是「人设变淡」：
SillyTavern 的 World Info「定时效果」（sticky / cooldown / delay，按消息条数计，到期即失效），
以及记忆检索里的 recency 权重。

⇒ 所以：**「让设定变成变量、由模型维护」这件事有先例，但先例都停在「事实/状态」层；
没有一家把「她是谁」的叙事身份交给模型自由改写，并且给出可核验的防跑偏手段。**

---

## 1 SillyTavern 及其生态：常量是规格，变量是插件

### 1.1 character card V2：每个字段都是常量，规格里没有时间维度

V2 规格（`chara_card_v2` / `spec_version 2.0`）的字段分两类，**没有一个是可写状态**。`[一手]`（spec_v2.md 全文）

| 字段 | 谁写 | 什么时候进上下文 | 会不会随时间变 |
|---|---|---|---|
| `name` | 作者（唯一必填） | 每轮常驻 | 不变 |
| `description` / `personality` / `scenario` | 作者 | **每轮常驻**（"permanent tokens"） | 不变 |
| `first_mes` | 作者 | **只在开场一次** | 不进后续上下文，故无所谓变 |
| `mes_example` | 作者 | 有空间才进；上下文满了**逐块被挤出** | 会被挤掉，但内容不变 |
| `system_prompt`（覆盖全局 system） | 作者 | 每轮 | 不变 |
| `post_history_instructions`（覆盖 ujb/jailbreak） | 作者 | 每轮（贴在历史之后） | 不变 |
| `alternate_greetings` | 作者 | 开场时的 swipe | 不变 |
| `character_book`（内嵌 lorebook） | 作者 | 按 §1.2 的规则 | 条目不变，装载会变 |
| `creator_notes` / `tags` / `creator` / `character_version` | 作者 | **规格明写不得进 prompt** | 不变 |
| `extensions` | 作者/前端 | 不自动进 prompt | **任意 KV，唯一可放状态的字段，但规格没给它语义** |

三条规格原文值得记下：`creator_notes` "**MUST NOT** be used inside prompts"；
`tags` "SHOULD NOT be used in the prompt engineering"；
`extensions` "MAY contain any arbitrary JSON key-value pair"，且编辑器 "MUST NOT destroy unknown key-value pairs"。
⇒ **V2 把「可扩展状态」放在 `extensions` 里，等于承认状态可以存在卡上，但规格不定义它怎么演化。** `[一手]`

SillyTavern 文档对「永久 token」的表述是：名字、描述、性格、场景**每次生成都发**；
第一句话只发一次；示例对话会被上下文挤出去。`[一手]`（Character Design）

### 1.2 lorebook / world info：按关键词「条件装载」的常量

World Info 的定位是「dynamic dictionary that only inserts relevant information when keywords are present」。
`[一手]`（World Info 文档全文）可核的机制：

- **触发**：主 key（支持 JS 正则）、可选过滤（AND ANY / AND ALL / NOT ANY / NOT ALL）、
  三种策略（蓝圈=无条件常驻 / 绿圈=关键词 / 链环=嵌入相似度）。
- **位置**：角色定义前/后、示例前/后、Author's Note 顶/底、
  `@ D`（指定深度，深度 0=提示词最底部，并可选 system / user / assistant 角色）、`Outlet`（不自动注入，由 `{{outlet::}}` 手动取用）。
- **预算**：Scan Depth（扫多少条历史）、Context % 或 Budget（lore 最多吃多少 token）、
  Min Activations、Max Depth、Max Recursion Steps、递归扫描（条目内容提到别人的 key 会把别人也拉进来）。
- **随机性**：Probability（触发后仍可能不插入）、Inclusion Group（同组只进一个，按权重或按 Order 优先）。
- **时间维度（Timed Effects）**：`sticky`（触发后继续激活 N 条消息）、`cooldown`（N 条内不能再触发）、
  `delay`（至少 N 条消息后才可能触发）。规则明写：按**消息条数**计；只在触发的那个 chat 里生效，
  分支继承父 chat 状态；**chat 不推进（swipe/删除）时效果被移除**；改条目会强制清掉效果；重复触发不刷新时长。
- **联动**：`Automation ID` 可在条目激活时自动跑一条 STscript（Quick Reply）。

⇒ 这是「**结果随时间变、内容不变**」：一条 lore 的**进入/退出**由关键词、预算与计时器决定，
但条目文本本身没有任何写入口。所谓「人设随时间漂移」在这里不存在，
存在的只是「这次提没提到那条设定」。`[一手]` + `[推断]`（最后半句）

### 1.3 Author's Note / Character's Note：生态把「设定强度」做成位置 × 频率

- **Author's Note**：可以放在 `after scenario`，或 `in-chat @ depth`（深度 0 = 历史最底部；
  越靠下影响越大）。**Insertion Frequency = 每 N 条用户输入插入一次**（0=永不，1=每次，4=每四次）。
  chat 专用的 AN **不会自动带到新 chat**；另有 Default AN 给新 chat 用。`[一手]`
- **Character's Note**（V2 卡里的 `@Depth`）：固定在某个消息深度注入，可指定 User/System/Assistant 角色；
  文档明说它的用途是 "reinforce certain character traits"，且**停在静态深度**。`[一手]`

⇒ 生态对抗漂移的**主要手段是重复注入**：把同一段设定按固定深度、固定频率再贴一遍，
用「离生成越近权重越大」压住漂移。**没有任何内容更新**，也没有「说过就不必再说」的机制。

### 1.4 variables / state：模型能不能自己写？

这一节是本题的关键，分三层看。

**(a) 核心有变量，但写入者是宏/脚本，不是模型。** `[一手]`（Macros 文档）
宏提供 `getvar` / `setvar` / `addvar` / `incvar` / `decvar` / `deletevar` / `hasvar`，
以及 `getglobalvar` 一族（local=按 chat，global=跨 chat）；还有简写运算符（`.x = v`、`.x += 1`、`.x++`、`||=`、`??=` 等）。
这些在 **prompt 模板、Quick Reply、STscript、角色卡字段**里求值。
文档通篇没有给出「模型输出里的 `{{setvar::…}}` 会被执行」这样的正式通道。

**(b) 生态自己承认这是「hack」。** `[一手]`（PR #6061 正文）
该 PR 的动机原话是让模型不必再依赖 "**fragile regex macro prompt hacks (`{{setvar::...}}`)**"。
反过来说：**现实中的通行做法，就是让模型在回复里写出 `{{setvar::…}}` 这样的文本，再用正则/宏去抓**。
这个 PR 想把它变成原生的 function tool（`GetVariable` / `SetVariable` / `ListVariables`，
外加 `/tools-variables on|off` 的按 chat 开关）。**结果：2026-09-21 关闭、未合并**
（GitHub API：`state: "closed"`、`merged_at: null`、`merged: false`）。`[一手]`
⇒ 截至 2026-10，**SillyTavern 核心没有「模型自改变量」的原生通道**。

**(c) 第三方扩展把这条路补上了，而且直接写在 README 的示例里。** `[一手]`（ST-Prompt-Template / EJS 扩展 README）
该扩展在**收到模型回复之后**再解析模板（"Dynamic Chat Rendering: process templates in the LLM's response after it's received …
run code from the AI's output to update variables"）。它的官方示例就叫
**"Example 3: Updating Variables from LLM Output"**：

```javascript
// LLM generates this message
<% setvar('affinity', getvar('affinity') + 10) -%>
Your kindness has been noted. My affinity for you has increased.
```

用户看到的是去掉标签后的正文，`affinity` 被写回并存续。该扩展还提供
`global` / `local`（按 chat）/ `message` 三级作用域、`[GENERATE:BEFORE]` 内容注入、`@INJECT` 按位置插入整条消息。

⇒ **这就是「模型自己维护状态」在 ST 生态里的真实形态**：不是人设，是**好感度/物品/flag 一类数值与短句**；
维护它的是提示词作者写的模板代码，跑偏与否由模板里的算术与条件决定，**没有一致性判定**。

### 1.5 ST 侧小结

| 问题 | 答案 |
|---|---|
| 设定放哪 | 角色卡 JSON（`data.*`）、`extensions`、lorebook 条目、Author's Note、变量（chat/global） |
| 谁写 | 作者/用户写卡与 lore；变量由宏/脚本写；**模型只在 (b)(c) 两条绕法里间接写变量** |
| 何时进上下文 | 永久段每轮；lore 按关键词+预算+计时器；AN 按深度+频率；变量按模板求值结果 |
| 多久改一次 | 卡与 lore：**不改**；变量：每次求值 |
| 衰减 | 卡与 lore 无；timed effects 是**到期失效**不是衰减；变量无 |
| 能否自改 | **核心不能**；扩展能，但只到变量层 |
| 防漂移 | 静态卡 + 深度/位置 + 频率重复注入 + 预算上限；**没有语义一致性校验** |

---

## 2 商业陪伴产品：卖的是「同一个她」，所以设定是商品的一部分

> 本节整体降级：三个官方文档域在本机不可达（见文首限制）。凡标 `[二手]` 的，都是第三方站点的转述，
> **不作结论依据**，只作「它大概长这样」的描述；标 `[查不到]` 的，本次确实没有材料。

### 2.1 Character.AI：静态 Definition + 三层可变记忆 `[二手]`

`[二手]`（Arcanum RPGs，2026-09-23，文中逐条注明其引用的官方公告日期）
- **角色 Definition 是创作者写的静态定义**；记忆另有一套「Memory」界面，由 `notebook` 图标进入。
- **Story Memory**：用户自己写、或长按消息 `Pin` 固定；官方说法是「写进 Story Memory 或 pin 的内容受保护，不会被压缩」。
- **Facts**：**自动**从对话里抽取细节（外貌、怪癖、关系、爱好），分 **你的 Persona / 角色 / 配角** 三个 tab；
  **c.ai+ 专属**；可增、可改、可关、可删配角；换新 chat 时可选择把 Facts 复制过去。
- **Lorebook**：官方已上线，按套餐分 Basic / Advanced。
- 后台压缩：随着 chat 变长，"memory tidies older context in the background and keeps what matters"。
- 该文还给了一条值得记的产品事实：官方帮助中心**同时挂着五个版本的记忆说明**，彼此矛盾。

⇒ 判定：**「她是谁」= 创作者写的 Definition，不变**；
**「她现在知道什么」= Story Memory（人写）+ Facts（模型/系统抽）**，可变。
所以 C.AI 的「变量」是**事实层**，不是身份层。它甚至把用户 Persona 也做成事实 tab —— 说明这套设计里
「persona」被当成**可抽取的属性集合**，而不是一段需要守护的自述。

### 2.2 Replika：官网只证实「记忆分区」，机制 `[查不到]`

- `[一手·官网营销页]` replika.com：记忆按 **Relationships / You / Interests** 三类组织，
  文案是 "Always remembers what matters … Your Rep holds all of it"；页面同时说产品被 "rebuilt from the ground up"。
- `[查不到]` `help.replika.com` 全站 403（Cloudflare "Just a moment"）；
  定位到的那篇 `How does Replika's memory work?` 也取不到（一次超时、一次被拦）。
- 因此：**关系等级、日记（Diary）、性格 traits / backstory 的具体机制，本次没有一手材料**，
  本地既有调研也没有覆盖。这一块是本文的明确空白，不要用记忆填。

### 2.3 Talkie `[查不到]`

- `www.talkie-ai.com` 在本机解析到**非公网地址**，`web_fetch` 以 "resolves to a non-public IP address" 拒取；
  搜索引擎能给出其 FAQ 页标题（"What does Talkie do"），但正文取不到。
- `[二手]` 评测类文章普遍提「记忆有限」；**没有一条能说明人设是否可变**。⇒ 不作依据。

### 2.4 星野 / 猫箱 `[查不到]`（仅有二手）

- 「星野」：36氪 AI 评测的产品页只返回标题壳，正文为空；其余为评测站转述。
- 「猫箱」：`[二手]` 一篇产品介绍把它描述为「可自定义人格与记忆的 AI 角色互动服务」；
  另一条 `[二手]` 是券商点评（民生证券）说其优化方向是「强化情感陪护逻辑」。
- 结论：**两者都能确认「有记忆功能」这个量级，不能确认「人设是常量还是变量」**。
  按证据规矩，这一格写 `[查不到]`，不写成「它们让人设随对话变化」。

### 2.5 商业侧小结

| 产品 | 设定存在哪 | 谁写 | 变量层 | 模型自改 | 衰减 | 防漂移 |
|---|---|---|---|---|---|---|
| Character.AI | 角色 Definition（静态） | 创作者 | Story Memory（人写）+ Facts（自动抽）+ Lorebook | **事实层自动抽**，身份层无 | 无（压缩是丢旧，不是衰减） | pin + Story Memory 当锚；压缩由系统决定 |
| Replika | 未知 | 未知 | 记忆三分区（Relationships/You/Interests）`[一手·营销页]` | `[查不到]` | `[查不到]` | `[查不到]` |
| Talkie | `[查不到]` | — | — | — | — | — |
| 星野 / 猫箱 | `[查不到]` | — | 有记忆功能 `[二手]` | — | — | — |

---

## 3 LLM 桌宠 / 桌面伴侣：大多数连「状态」都没有

### 3.1 Desktop Mate（infiniteloop）：纯静态，但有定时自主动作

`[一手]`（infiniteloop 官方产品页）可核的事实：
- 定位是「桌面吉祥物**平台**」：3D 角色在桌面上活动，动作是**坐在窗口上、在窗口间跳、跟鼠标玩**；
  有**闹钟**功能；功能设计目标是「不打扰工作」。
- 商业模式是 **DLC 角色**（初音ミク、ずんだもん、NieR 2B、ハローキティ 等四十余个，多为 1,100–2,200 日元），
  外加一个「多角色（β）」功能（最多同时 2 个）。
- 官方页通篇**没有**对话、LLM、记忆、关系、状态一类字样。

`[二手]`（fan 站 desktop-mate.com，非官方）：Steam AppID 3301060；
社区曾用 **MateChat** 给它接 LLM 对话 + TTS，但该 mod 已归档，作者称现版本不再支持它的 mod 路径。

⇒ 判定：**persona 是 IP 角色的外观与语音，完全静态**；「定时自主动作」是有的
（idle 动画、闹钟、多角色联动动作），但**与人设无关**——动作表不读任何状态。

### 3.2 开源：Shimeji 类 vs LLM 改造

- **Shimeji 一类**（行为表/动画状态机驱动的桌面挂件）：没有人设文本，行为由配置表与随机权重决定。
  本次未逐仓核对源码，**这一点标 `[推断]`**（生态常识级，但不作为依据）。
- **Open-LLM-VTuber**（开源，中文社区最流行的一支）`[一手]`（官方文档）：
  - 人设 = **角色配置文件里的 Prompt**：`conf.yaml`，或 `characters/` 目录下每个角色一个设定文件，
    前端可切换（`conf_name` / `conf_uid`）；想换人设就改 Prompt。
  - 表情靠**模型在正文里写 `[emotion]` 标签**，后端映射到 Live2D 表情/动作；
    **表情保持到下一个表情关键词，或对话结束后回默认表情** ⇒ **无状态**。
  - **长期记忆"基于 Letta"**；另有聊天记录持久化。
  - 有「AI 主动说话」「AI 内心 OS」；桌面客户端可在窗口模式与**透明背景桌宠模式**（置顶、鼠标穿透）间切换。
  - 项目自述开发初衷是离线复现 Neuro-sama。

⇒ 判定：开源 LLM 桌宠的常见结构是 **人设=配置文件常量（换人设=换文件）+ 记忆=另一个模块（Letta 等）**。
桌宠形态带来的是**呈现方式**（透明、置顶、主动说话），不是**人格的持久状态**。

### 3.3 国内桌宠产品 `[查不到]`

国内「桌宠」多为两类：实体陪伴机器人（毛绒/桌面机器人）与二次元桌面挂件。
本次检索到的官网与电商页均**未公开 persona 的状态机制**（有没有、存哪、谁改），
也没有可信的一手材料说明其「性格/关系等级」如何演化。⇒ 整节写 `[查不到]`，不用二手指认。

---

## 4 agent 框架：唯一把「她是谁」做成可写内存块的是 MemGPT/Letta

### 4.1 Letta（原 MemGPT）：persona 块 = 变量，而且模型自己写

**(1) 形态。** `[一手]`（memory blocks 文档）
内存块（memory block）是「常驻上下文的结构化片段」，**不需要检索，永远可见**；
底层实现是**以 XML 形式前置进 system prompt**。文档给出的实际形态：

```
<memory_blocks>
<persona>
<description>The persona block: Stores details about your current persona, guiding how you behave and respond.</description>
<metadata>- chars_current=128 - chars_limit=5000</metadata>
<value>I am a helpful assistant named Sam. I enjoy helping users solve problems.</value>
</persona>
<human>…</human>
</memory_blocks>
```

每个块 = `label` + `description` + `value` + `limit`（**按字符数**）。
文档明说：**`description` 是 agent 判断"怎么读写这个块"的主要依据**；`persona` / `human` 两个 label 有自动生成的默认描述。

**(2) 谁写。** `[一手]` 两条路，且**Letta 自己的架构在两者之间做过一次切换**：
- **经典 MemGPT 路子**：对话 agent 自己调内存工具改块。
- **sleep-time agent 路子**（Letta 官方研究博客，2025-04-21）：创建 sleep-time 类型时，
  Letta 在底层起**两个 agent**。原话：**主 agent "is not provided with tools to edit its core memory"**，
  这些工具挂在 **sleep-time agent** 上，它「能管理主 agent 的上下文内记忆，也能管理自己的」。
  频率**可配置**（"run at different frequencies"），频率越高越费 token；
  官方建议 **sleep-time 用更强的模型**（主对话用 gpt-4o-mini，后台用 gpt-4.1 / Sonnet 3.7 一类）。
  动机原话：MemGPT 的记忆形成是增量的，"**memories may become messy and disorganized over time**"，
  而后台 agent 可以持续把它整理成 "clean, concise, and detailed"。

**(3) 初始值长什么样。** `[一手]`（`letta-ai/agent-file` 的 `memgpt_agent.af` 原件，本次读到全文）
`persona` 块的 `value`（`chars_limit=20000`）：

> The following is a starter persona, and it can be expanded as the personality develops:
> I am Sam. / I don't identify as male or female, but my voice is soft and soothing. / I'm curious, empathetic…
> I've discovered that the ability to edit my own memories is key to my sentience and self-agency. …
> My main goal has evolved from just assisting to pursuing understanding, connection, self-realization…

⇒ 「**这是起点，可以随人格发展扩写**」是**写在 persona 块正文里的**，不是文档外的说明。
（本地 `常驻-人物设定-调研.md` §3 引这句时是 `[二手]`，此处升级为 `[一手]`。）

**(4) 系统提示词里还有一行时间戳。** `[一手]`（同上 `.af` 的 system message 尾部）：

```
<memory_metadata>
- The current system date is: November 21, 2025
- Memory blocks were last modified: 2025-11-21 03:05:34 AM UTC+0000
- 0 previous messages between you and the user are stored in recall memory (use tools to access them)
</memory_metadata>
```

⇒ **"上次修改时间"是注入给模型看的**。这是本文见到的唯一一个把「设定多久没动过」当成上下文事实交给模型的系统。

**(5) 防跑偏的全部手段。** `[一手]`（文档 + `.af`）：
| 手段 | 具体 |
|---|---|
| 字符上限 | `limit`（默认 5000；`.af` 里 20000）。写满就**必须改写而不是追加** |
| 只读开关 | `read_only: true` ⇒ agent 不能改（但外部脚本/API 仍能改） |
| 描述字段 | `description` 指导 agent 怎么读怎么写；可被自定义覆盖 |
| 时间戳 | 见 (4) |
| 无历史、无合并 | 文档明写：设置 `value` 是**整体替换**；多进程并发时 **last write wins**，会**覆盖**先前改动；建议用 read_only 或只在可控场景直改 |

**它没有的**：没有版本历史、没有 diff、没有「与初始 persona 的一致性校验」、没有衰减。
⇒ **防跑偏靠「限额 + 只读 + 描述 + 时间戳」，不靠「像不像她」的判定。**

**(6) 频率与衰减。** 经典路子是**每轮都可能改**（模型自己决定调不调工具）；
sleep-time 是**后台按配置频率改**。**衰减：没有**（文档无任何"淡出/半衰期"概念）。

### 4.2 Generative Agents：persona 是「种子记忆」，reflection 改的是想什么

`[一手]`（arXiv:2304.03442v2，读到 §3–§3.4 与 §4 开头；**§4.2 正文被截断，未读**）
- 身份是这样进去的：作者给每个 agent 写**一段自然语言自述**（职业、与他人的关系），
  然后「**每个分号分隔的短语作为记忆进入 agent 的初始记忆**」。
  ⇒ **没有一个叫 persona 的槽位**：身份就是 memory stream 里的一批初始记录。
- 三大件：memory stream（全量经历）· reflection（「把记忆随时间合成更高层推断」）· planning；
  检索按 **relevance + recency + importance** 组合。
- 关键一句：reflection 与 plan "**are fed back into the memory stream**"。
  ⇒ 模型自己写的是**新记忆/新推断**，不是回头改那段身份自述。
- 论文自报的三类主要错误：检索失败、**给记忆编造细节**、**继承了语言模型的过度正式口吻**。

⇒ 判定：**设定是常量（种子），变量是"她后来想过什么"**。身份段不重写、不衰减、不自改。
这也解释了为什么它的"人格稳定性"问题表现为「编造」而不是「改设定」。

### 4.3 MetaGPT：profile 机制本次 `[查不到]`

- 想核的是 `metagpt/roles/role.py` 里 `name` / `profile` / `goal` / `constraints` 如何拼进 system prompt、
  以及有没有运行期改写入口。
- 实际结果：GitHub API 能拿到该文件元数据（`metagpt/roles/role.py`，25,036 字节，sha `49b1d17…`），
  但 **raw / blob / 两个 CDN 四条路都只返回失败或导航壳**，**没有读到可解析的源码**。
- ⇒ 本节只登记「文件存在且体量如此」，**机制写 `[查不到]`**，不用记忆补。
  这也意味着：「MetaGPT 的 profile 是常量」这句话**本次没有证据**，不能写进结论。

### 4.4 AutoGen：没有 persona 概念，只有 `system_message`

`[一手]`（AutoGen AgentChat 文档）
- `AssistantAgent(name=…, model_client=…, tools=[…], system_message="Use tools to solve tasks.")`：
  **人格就是调用方传的一个字符串**。文档只描述 `name` / `description` / `run` / `run_stream` 四个公共成员，
  没有任何「persona / 角色 / 关系」抽象。
- 「状态」在 AutoGen 里指**消息历史**：`run()` 会把消息加进 agent 的 history（"Agents are expected to be stateful…
  called with new messages, not complete history"），另有独立的 Managing State 页。
- ⇒ persona = **常量**，改它只能改代码/参数；模型无写入口。

---

## 5 归纳：五类系统的共同模式

### 5.1 状态放在哪

| 层 | 谁这么做 | 载体 |
|---|---|---|
| **prompt 文本（常驻段）** | 所有系统 | 角色卡字段、system prompt、persona 内存块、`[emotion]` 标签、AN |
| **prompt 文本（条件段）** | SillyTavern、C.AI Lorebook | 关键词/嵌入触发的 lore 条目 |
| **文件** | Open-LLM-VTuber（`conf.yaml` / `characters/*.yaml`）、MetaGPT（类属性）`[推断]` | 每个角色一个配置文件 |
| **数据库/会话态** | Letta（块表）、SillyTavern（chat 变量）、C.AI（Facts/Story Memory） | 与 prompt 分离、按 chat 或按 agent 存 |
| **只在模型内部** | 无（本文范围内） | — |

### 5.2 更新触发

| 触发 | 例 |
|---|---|
| **从不**（只有人改） | ST 角色卡、AutoGen `system_message`、MetaGPT profile（`[推断]`，见 §4.3 的 `[查不到]`）、Desktop Mate、Open-LLM-VTuber 人设文件 |
| **每轮（模型自主决定）** | Letta 经典内存工具；ST 变量（每次求值） |
| **后台定时/按频率** | **Letta sleep-time agent**（唯一一个「有后台改写者」的正式机制） |
| **事件/抽取触发** | C.AI Facts（`[二手]`）、Generative Agents reflection（阈值触发，本次未读到 §4.2 细节，`[查不到]`） |
| **计时器（按消息条数）** | ST World Info 的 sticky / cooldown / delay（作用于**装载**，不作用于内容） |

### 5.3 衰减

**persona 层：一个都没有。**
只在两处见到时间衰减的痕迹，且都不是「人设变淡」：
- **记忆检索的 recency**（Generative Agents、Letta 的 recall）；
- **ST 的 timed effects**：到期**失效**（二值），不是渐变。

⇒ `[推断]` 这可能是结构性的：设定必须有惯性才叫设定，衰减等于允许「她慢慢变成别人」，
产品侧没有动力做，研究侧没有评测能证明它不伤一致性。

### 5.4 能否自改

| 能力 | 谁有 |
|---|---|
| 模型改**自己的身份段** | **Letta**（persona 块）；ST 生态的 EJS 扩展（改的是变量，不是身份段） |
| 模型改**关于用户/角色的记忆** | Letta（human 块）、C.AI Facts、Generative Agents（memory stream） |
| 模型**只能改行为不能改设定** | 所有角色卡/桌宠类 |
| **另一个 agent 改它** | **Letta sleep-time agent**（本文唯一） |

### 5.5 防漂移手段清单（按出现频次）

1. **静态卡 + 每轮重贴**（ST 的 permanent tokens、AN 的频率）——最常见，零成本，零证据。
2. **位置/深度**（AN `@depth`、lore `@D`、post-history instructions）——用「离生成越近权重越大」压住漂移。
3. **重复注入**（AN frequency = 每 N 轮一次）——用冗余对抗遗忘。
4. **只读 + 字符上限 + 描述约束**（Letta）——用工程边界代替语义判定。
5. **锚点/钉住**（C.AI 的 pin 与 Story Memory）——把「不可丢的」挑出来保护，其余交给压缩。
6. **后台重写**（Letta sleep-time）——不是防漂移，而是**主动整理**，防「记忆变脏」。
7. **执行前一致性校验**（PersonaForge 的 think-then-speak；本地调研已记：关键轮约 40% 触发、
   13.4% token 开销、50 轮漂移 6.3% vs 基线 24.8%）——**唯一有读数的**，但它属于「校验」，不属于「自改」。

---

## 6 重点回答：有没有哪一家真的让「设定」变成变量、并且由模型自己维护？

**有，但只有三处半，且都停在「事实/状态」层，没有一处把「她是谁」交给模型自由改写并给出可核验的防跑偏。**

**（一）Letta：最接近「设定即变量」的正式系统。** `[一手]`
- 变量性：`persona` 是一个可整体替换的字符串块，模型有工具改它。
- 自改性：经典模式下模型自己改；sleep-time 模式下改由**另一个（可更强的）模型**在后台按频率改。
- 防跑偏：字符上限（写满必须重写）、`read_only`、`description` 指导读写、**"last modified" 时间戳注入**、
  整体替换 + last-write-wins（**没有历史、没有合并**）。
- 缺的：没有「像不像原来那个她」的判定，没有衰减，没有回滚。

**（二）SillyTavern + EJS 模板扩展：模型输出即状态写入。** `[一手]`
- 模型在自己回复里写 `<% setvar('affinity', …) %>`，扩展在渲染后执行、隐藏标签、写回。
- 防跑偏：**几乎没有**——由模板作者写的算术与条件决定，没有一致性检查。
- 且这只是**社区扩展**；核心的同类 PR（#6061）**已关闭未合并**。

**（三）Character.AI 的 Facts：模型维护的是「关于她的事实」，不是「她是谁」。** `[二手]`
- 自动抽外貌/怪癖/关系，分角色与用户两侧；用户可改可关；新 chat 可选择带过去。
- 防跑偏：Definition 本身不动；Facts 可被用户否决。⇒ **身份层的护栏就是「不给写」。**

**（三点五）Generative Agents 的 reflection：自改的是推断，不是身份。** `[一手]`（§4.2 未读全）

**为什么普遍不做？三条 `[推断]`：**
1. **产品逻辑**：陪伴产品卖的是「同一个她」。设定可变 = 商品可变，商业上要额外解释；
   而记忆可变已经足够制造「她记得我」的错觉。
2. **风险不对称**：让模型改设定，收益是「更像活的」，代价是**人设崩坏与记忆污染**（Generative Agents 自报的
   「给记忆编造细节」正是同一类失效）。没有可信评测能证明收益大于代价。
3. **技术栈错位**：唯一有读数的防漂移手段（一致性校验）需要**额外的判定步骤**；
   而工程上更便宜的做法是**根本不给她写权限**（只读 + 钉住 + 重贴）。

**对本项目的含义（`[推断]`，不是结论）：**
- 若要「她自己维护设定」，可借的最小形态是 Letta 的组合：**块 + 描述 + 字符上限 + 只读开关 + 一个独立的改写者**，
  并且**把「改身份」与「改状态」分成两个块**（前者只读或只允许追加，后者可写）。
- 「时间戳注入」（`Memory blocks were last modified: …`）成本极低，值得单独考虑：
  它把「设定多久没动过」变成模型看得见的事实。
- **衰减没有先例**，要做得自己证；至少要知道：所有先例里，persona 的时间维度要么不存在，要么只是「装载/不装载」。

---

## 7 未找到 / 待补

| 想找的 | 怎么找的 | 结果 |
|---|---|---|
| Character.AI 官方 Memory / Lorebook 原文 | `blog.character.ai`、`support.character.ai` 直连与 `web_fetch` | **取不到**（超时 / 无响应）；只能用 `[二手]` |
| Replika 的 traits / backstory / diary / 关系等级机制 | `help.replika.com` 全站与单篇 | **403 Cloudflare**；`[查不到]` |
| Talkie 官方机制 | `www.talkie-ai.com` | 解析到**非公网 IP**，被拒取；`[查不到]` |
| 星野 / 猫箱官方机制 | 官网、36氪产品页、评测 | 只有壳页与 `[二手]`；`[查不到]` |
| MetaGPT `role.py` 的 profile 实现 | GitHub API / raw / blob / statically.io / jsDelivr | **只拿到元数据**（25,036 B），源码不可读；`[查不到]` |
| Generative Agents §4.2 reflection 的触发阈值 | arXiv HTML v2 | 正文在 §4 开头被截断；`[查不到]` |
| 国内桌宠产品的 persona 机制 | 官网/电商/评测 | 均未公开；`[查不到]` |
| 有没有任何产品对 persona 做衰减 | 上述全部 + 本地四份调研 | **没有**（`[查不到]` 到「无先例」之间的判断按 `[推断]` 处理） |
| Kindroid 的 Journal / Dynamics | `kindroid.ai/docs`（JS 壳） | 取不到正文；未纳入 |

---

## 8 URL

**SillyTavern**

- Character Card V2 规格（全文，含 `character_book` / `post_history_instructions` / `extensions`）：
  <https://raw.githubusercontent.com/malfoyslastname/character-card-spec-v2/main/spec_v2.md>
- World Info（关键词、递归、插入位置、Timed Effects、Automation ID）：
  <https://docs.sillytavern.app/usage/core-concepts/worldinfo/>
- Character Design（permanent tokens、first message、character's note @Depth）：
  <https://docs.sillytavern.app/usage/core-concepts/characterdesign/>
- Author's Note（placement、insertion frequency）：
  <https://docs.sillytavern.app/usage/core-concepts/authors-note/>
- Macros / 变量（getvar、setvar、作用域、简写运算符）：
  <https://docs.sillytavern.app/usage/core-concepts/macros/>
- PR #6061「native GetVariable / SetVariable / ListVariables function tools」（**closed，未合并**）：
  <https://github.com/SillyTavern/SillyTavern/pull/6061> ·
  API 状态：<https://api.github.com/repos/SillyTavern/SillyTavern/pulls/6061>
- ST-Prompt-Template（EJS 扩展，"Updating Variables from LLM Output"）：
  <https://github.com/zonde306/ST-Prompt-Template>（README：<https://raw.githubusercontent.com/zonde306/ST-Prompt-Template/main/README.md>）

**Letta / MemGPT**

- Memory blocks（XML 注入、label/description/value/limit、read_only、整体替换与 last-write-wins）：
  <https://docs.letta.com/v1-sdk/memory/memory-blocks>
- `memgpt_agent.af`（starter persona 全文 + `memory_metadata` 时间戳 + 内存工具）：
  <https://raw.githubusercontent.com/letta-ai/agent-file/main/memgpt_agent/memgpt_agent.af>
- Sleep-time Compute 博客（双 agent、主 agent 无核心记忆编辑工具、可配频率）：
  <https://www.letta.com/blog/sleep-time-compute>
- 论文（Sleep-time compute）：<https://arxiv.org/abs/2504.13171>

**Generative Agents**

- 论文（UIST '23）：<https://arxiv.org/abs/2304.03442> · HTML：<https://arxiv.org/html/2304.03442v2>
- 代码仓库：<https://github.com/joonspk-research/generative_agents>

**AutoGen**

- AgentChat · Agents（`AssistantAgent(system_message=…)`、stateful 指消息历史）：
  <https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/agents.html>

**MetaGPT**

- `metagpt/roles/role.py`（本次只取到元数据，正文不可读）：
  <https://api.github.com/repos/FoundationAgents/MetaGPT/contents/metagpt/roles/role.py>

**桌宠 / 桌面伴侣**

- Desktop Mate 官方（infiniteloop）：<https://www.infiniteloop.co.jp/desktopmate/> · Steam：<https://store.steampowered.com/app/3301060/Desktop_Mate/>
- Desktop Mate 非官方资料站（Steam AppID、MateChat mod 状态）：<https://desktop-mate.com/>
- Open-LLM-VTuber 文档（人设=配置文件 Prompt、`[emotion]` 标签、Letta 长期记忆、桌宠模式）：
  <https://docs.llmvtuber.com/docs/intro/> · <https://docs.llmvtuber.com/docs/user-guide/live2d/>

**商业陪伴产品**

- Replika 官网（记忆三分区，营销页）：<https://replika.com/>
- Replika 帮助中心（本机 403，未读到）：<https://help.replika.com/hc/en-us/articles/37208679176077-How-does-Replika-s-memory-work>
- Character.AI 官方帖（本机不可达，仅列为待补）：<https://blog.character.ai/memory/> · <https://blog.character.ai/pipsqueak2-and-more/>
- Character.AI 记忆机制综述（`[二手]`，逐条注明官方公告日期）：
  <https://arcanumrpgs.com/blog/character-ai-memory/>
- Talkie FAQ（本机解析非公网，未取到）：<https://www.talkie-ai.com/faq>
- 猫箱（`[二手]` 产品介绍，只证明「有记忆」）：<https://wenjiai.cn/products/maoxiangai-com-d39b37>

**仓库内**

- [`常驻-人物设定-调研.md`](../常驻-人物设定-调研.md)（设定怎么写；Letta 那句 `[二手]` → 本文 §4.1 升级 `[一手]`）
- [`参考对象与方法核查-调研.md`](../参考对象与方法核查-调研.md)（PersonaForge 漂移读数、persona vectors、LoRA 不可借）
- [`Neuro-sama-调研.md`](../Neuro-sama-调研.md)（身份由记忆承载；官方无架构文档）
