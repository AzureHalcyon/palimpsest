# Neuro-sama 实际技术架构调研

> 调研时间基准：本报告的页面快照显示 vedal.ai 已挂出「Neuro & Evil Live Concert · 2026-12-19」，NeuroWiki 最新条目到 2026-10，官方 SDK 文档标注 "Last update: 1st of July 2026"。Neuro-sama 项目**仍在活跃运营**。
>
> 出处标记：`[一手]` = 官方 / Vedal 本人 / 官方仓库；`[二手]` = 媒体、wiki、社区整理；`[推测]` = 社区分析或明显不可靠来源。

---

## ① 是什么

| 项 | 内容 | 出处 |
|---|---|---|
| 是什么 | 英国开发者 Vedal 制作的 AI VTuber（虚拟主播），对话/动作/打游戏由 AI 生成，无真人中之人 | [一手] https://vedal.ai/ ；[二手] https://en.neurosama.info/wiki/Neuro-sama |
| 作者 | Vedal（Twitch/网名 `vedal987`），自称自学编程，UK 出身，全职做这件事 | [一手] https://vedal.ai/ ；[二手] https://en.neurosama.info/wiki/Vedal987 |
| 前身 | ① 2018–2019 年 Vedal 写的 `osu!` 游戏 AI（当时尚未叫 Neuro-sama，命名来自粉丝 Str0mboli 建议）；② 2021 年内部代号 "Airis" 的 AI 主播原型。两者合并为现在的 Neuro | [二手] https://en.neurosama.info/wiki/Neuro-sama （附 YouTube 原始视频链接作证） |
| 出道时间 | **2022-12-19** 以 VTuber 身份首播（用免费 Live2D 皮「桃瀬ひより / Hiyori Momose」），这天被当作官方生日 | [二手] https://en.neurosama.info/wiki/Neuro-sama ；[一手侧证] 官方站与 blog 的纪念活动围绕 12/19 |
| 平台 | **主战场 Twitch**：`twitch.tv/vedal987`；另有日语频道 `twitch.tv/vedal987_jp`、YouTube `@Neurosama`、Bilibili、TikTok | [一手] https://vedal.ai/ ；[二手] https://en.neurosama.info/wiki/Neuro-sama |
| 是否仍在运营 | **是**。官方 SDK 文档 2026-07 仍在更新；NeuroWiki 记到 2026 年 10 月的直播排期；官方站公布 2026-12-19 第四届生日线下 Live Concert（Vermont Hollywood, LA） | [一手] https://vedal.ai/ ；https://raw.githubusercontent.com/VedalAI/neuro-game-sdk/main/README.md （"Last update: 1st of July 2026"）；[二手] https://en.neurosama.info/wiki/Main_Page |
| 家族 | 妹妹 **Evil Neuro**（原本是分裂人格，后独立成角色，滤镜更松、语音模型更外放） | [一手侧证] 官方 API 里 `characterId` 取值为 `neuro` / `evil`（见 ② ）；[二手] https://en.neurosama.info/wiki/Main_Page |

**关于「被封号」传闻的核实**：这条**不是传闻，是真的，且有多家媒体报道**。2023 年 1 月，Neuro 在直播中说出敏感/冒犯性言论（多方报道称涉及否认大屠杀），被 Twitch 以 hateful conduct 封禁约两周，2023-01-26 解封。

- [二手/媒体] Kotaku：https://kotaku.com/neuro-sama-twitch-vtuber-ban-holocaust-minecraft-ai-1849977269
- [二手/媒体] TheGamer：https://www.thegamer.com/ai-vtuber-banned-from-twitch-after-denying-the-holocaust/
- [二手/媒体] AUTOMATON WEST（解封报道）：https://automaton-media.com/en/news/20230127-17630/
- [一手] Vedal 本人在 2023-04 的日媒采访中确认了封禁与后续过滤器改动：https://premium.kai-you.net/article/655

---

## ② 控制流 / 技术栈

### 2.1 总体形状：多专家模型拼装，不是单体大模型

NeuroWiki 的描述（**二手**）：Neuro 是「多个专家 AI 模型整合的产物」，模型之间互相通信，整体像复合系统；**游戏相关组件用 Python，其余大部分用 C#，前端跑在 Unity 上**。

- [二手] https://en.neurosama.info/wiki/Neuro-sama → "Capabilities" 段

Vedal 本人**从未公开**完整架构图。他在 2023 年日媒采访中只肯确认两点：(a) 她用的是 **大语言模型（LLM）**；(b) 「**具体的调参内情，我不太方便细说**」。

- [一手] https://premium.kai-you.net/article/655

### 2.2 控制流（尽量逐段给依据）

```
观众消息(Twitch chat / Bits)  ┐
他人语音(Discord 音源, 分说话人)├─► 输入汇聚/事件模块 ──► 情绪模拟 & 过滤/判定模块
游戏状态(经 Neuro SDK)         ┘                              │
环境判定模块 / 空闲随机模块（无需输入也能自己开口）──────────────┤
                                                              ▼
                                                      LLM（核心）
                                                              │
                                    ┌─────────────────────────┼──────────────────────┐
                                    ▼                         ▼                      ▼
                            机械词典过滤器            类 LLM 二次审核过滤器      TTS（语音合成）
                            （输出前拦截）            （边说边判，几乎不触发）          │
                                                                                      ▼
                                                                        Live2D/3D 皮套动作与表情模块
                                                                        （预置面捕/身体动画开关，口型随语音）
```

逐项依据：

1. **观众消息 + 语音输入 → 输入层**
   - [二手] NeuroWiki：她处理来自 chat 和捐赠的文本，也处理直播环境里他人的语音；**用 Discord 语音源做说话人区分**（同一套机制也用于 Bilibili 的自动字幕翻译）。https://en.neurosama.info/wiki/Neuro-sama
2. **「不需要输入也能开口」**
   - [二手] NeuroWiki 明确写：与多数商用 LLM 不同，她**不一定需要输入才说话**，这通常由**环境判定模块（environmental judgment modules）**或**空闲态随机模块（random modules in idle mode）**把输入喂给语言模型触发。https://en.neurosama.info/wiki/Neuro-sama
3. **编排层（orchestrator）—— 官方没有公开命名这个层，但存在**
   - 证据不是「官方声明有 orchestrator」，而是**官方 API 的行为**暴露了它：官方 Neuro API 规定游戏端用 websocket 发 `context`、`actions/register`、`actions/force`，Neuro 端回 `action`、`speech_finished`；且 `actions/force` 的 `priority`（low/medium/high/critical）会决定**她在说话时是否缩短句子甚至打断当前发言**来优先处理这个强制动作。
     - [一手] https://raw.githubusercontent.com/VedalAI/neuro-game-sdk/main/API/SPECIFICATION.md
     - 结论：说话队列 + 打断 + 优先级仲裁 + 「她正忙着跟 chat 或别人说话」这类判断，必然由一个统一的中枢进程管理。**这是我从官方 API 语义推导的，不是 Vedal 的原话**，标注为 `[一手证据 + 我的推断]`。
   - 另外官方 API 的 `startup` 回包里带 `sessionId / characterId / displayName`，`characterId` 为 `neuro` 或 `evil` —— 说明**两个角色是同一服务下的不同 character 实例**。[一手] 同上。
4. **LLM 是哪个？→ 查不到具体型号**
   - Vedal **只公开说是「大语言模型」**，拒绝透露调参内情：[一手] https://premium.kai-you.net/article/655
   - 官方 wiki 的技术段**也没有写具体模型名**（只写「Large Language Model」）：[二手] https://en.neurosama.info/wiki/Neuro-sama
   - 社区长期猜测（例如 GPT 系列、微调自研模型）**均无一手出处**。有第三方站点声称「Vedal 的技术 devlog 记录了从 GPT-based API 调用转向自研 fine-tuned 模型」，但该页面是 AI 生成内容、未给出任何可核验链接，**我不采信**：https://shapes.inc/fandom/neuro-sama/deep-dive `[推测/不可靠]`
5. **TTS**
   - [二手] NeuroWiki：**Neuro 的 TTS 是基于微软语音 "Ashley" 的二次开发**（"a redevelopment based on Microsoft's voice, Ashley"）。https://en.neurosama.info/wiki/Neuro-sama
   - 这段是**二手且无引注**，但它是目前最具体的一条公开说法。**没有官方确认**，Vedal 从未公开 TTS 供应商。
   - 同页：大写字母等情绪化文本**不一定**被 TTS 正确表达，后来的更新改善了「TTS 模块与其他模块的通信」，让她能按意图带情绪说话。
6. **Live2D / 动作驱动**
   - [二手] NeuroWiki：Live2D 表情与动作是**切换预置的面部与身体动画**实现的（张嘴、皱眉、惊讶等），由她的对话内容、他人话语、歌曲、音乐共同触发；「情绪模拟」主要是视觉表现层的映射，不等于内在情绪能力。https://en.neurosama.info/wiki/Neuro-sama
   - [二手] 同页：她还**可能**有一个独立的情绪模块，把上下文转成情绪参数，去影响对话系统与皮套操作；语音识别模块也可能用同一套判断说话人情绪。**「possibly」= wiki 自己也不确定**。
   - **官方从未说明用 VTube Studio / Warudo 还是自研 Unity 渲染管线**。wiki 只给了「前端在 Unity」这一条。**这是明确的未知项**。
   - 我**没有找到**任何一手材料描述她的口型同步（lip sync）具体实现路径（是音量驱动口型参数，还是音素对齐）。社区同类项目普遍用「音量 → MouthOpen 参数」或 VTube Studio 的麦克风口型，但**不能倒推 Neuro 这么做**。
7. **唱卡拉OK 不是 AI 唱的（澄清一个常见误解）**
   - [二手] NeuroWiki：卡拉OK 段落的歌声是**AI 歌声合成 + 人工调参（tuner: queenpb）**做的预制曲目，直播时只是**播放预制歌 + 皮套跟着节奏对口型**。所以「Neuro 会唱歌」在技术上是假的。https://en.neurosama.info/wiki/Neuro-sama
8. **打游戏侧的接口是公开的（这是唯一官方开源的部分）**
   - [一手] https://github.com/VedalAI/neuro-game-sdk —— 让游戏把 Neuro 接进来的官方 SDK + websocket 协议；官方 Unity / Godot SDK，另有大量社区 SDK（Rust/TS/Java/Lua/C/Python/Kotlin/C++/Ren'Py/RPG Maker/StarCraft II 等）。**注意：SDK 只是「游戏 ↔ Neuro」的接口，不是 Neuro 本体，也完全不含她的 LLM/TTS/记忆代码。**
   - 官方仓库列表（含各游戏集成，如 neuro-inscryption、neuro-sts2、neuro-buckshotroulette-reference、neuro-hollow-knight、neuro-cyberpunk、neuro-pokemon-platinum）：[一手] https://api.github.com/orgs/VedalAI/repos
   - 官方还 fork/使用了 `Discord.Net`、`NetCord`、`UnityRenderStreaming`，侧面印证 **C# + Unity 技术栈与 Unity 渲染串流**：[一手] 同上
9. **过滤模块（安全层）**
   - [一手] Vedal 原话（2023-04）：「**现在的过滤器有一部分用了 AI**，用来检测该说什么、不该说什么。比如即便句子里没有歧视性词汇，只要语境上带有歧视含义，也会被识别出来。」https://premium.kai-you.net/article/655
   - [二手] NeuroWiki 补充：她有**两级过滤**——①纯机械词典过滤（日常都由它处理，输出前完成）；②类 LLM 的自评估过滤（几乎不触发，截至 2025-01-04 只触发过 3 次）。行为特征是**先说出前一两句再突然停住**，说明是边说边判。https://en.neurosama.info/wiki/Neuro-sama

---

## ③ ★ 记忆机制（重点）

**核心结论：Neuro-sama 确实有跨直播的长期记忆，但它的实现细节官方从未公开。目前所有「怎么实现」的说法都来自社区/二手，没有任何一手材料说明数据结构、检索方式、写入策略。**

### 3.1 有哪些是「能确认的」

| 能确认的事实 | 依据 | 等级 |
|---|---|---|
| 她**有**长期记忆能力，且是**跨会话/跨直播**的 | NeuroWiki 设专节 "Memory System"，明确写 "Neuro possesses a memory system, including the ability for long-term memory"，并在人物总述里说更新后她获得了 long-term memory | 二手 https://en.neurosama.info/wiki/Neuro-sama |
| **不是**把全部经历机械地全量存进存储介质；而是**由 LLM 等组件根据上下文和分析选择性地决定记什么** | 同上 | 二手 |
| 记忆系统**不完整**：**不能自动分类或分层整理**，缺少完整的层级记忆功能 | 同上 | 二手 |
| 官方 API 层面**区分「持久上下文」与「临时上下文」** | 官方 API 规范 `actions/force` 的 `ephemeral_context` 参数：为 `false` 时，`state`/`query` 提供的上下文**在动作强制结束后仍会被 Neuro 记住**；为 `true` 时她**只在本次动作强制期间记住** | **一手** https://raw.githubusercontent.com/VedalAI/neuro-game-sdk/main/API/SPECIFICATION.md |
| 崩溃重启后**会尝试恢复她之前的上下文** | 官方 API 的 `actions/reregister_all`：Neuro 崩溃重启后会发这个命令，收到方应重新注册所有 action，**官方文档建议「你可能还想把她在崩溃前拥有的上下文还给她」** | **一手** https://github.com/VedalAI/neuro-sdk （官方文档页，社区镜像同内容：https://ktrain5169.github.io/neuro-sdk-docs/base/proposals/reregister.md） |
| 记忆是可**被外部改动**的：wiki 说她的「核心价值观」在训练时形成，但**可以被记忆改变**（"...they can be altered due to memory"） | 该段是 wiki 的推断段落，且 wiki 自己标注为 conjecture | 二手/推测 |
| 只有「被她放进长期记忆的东西」才算实质设定；随口编造的内容不算 | NeuroWiki 的判定规则：她经常编造虚构事件，**只有进入长期记忆的内容才有实质意义**；早期随口说的宠物猫、养女、体重等下次再问答案完全不同 | 二手 |

### 3.2 「她是怎么做到的」——**查不到**

我搜到的所有「实现层」说法都不成立，逐条说明：

- ❌ **社区常说「她用向量数据库 / RAG」**。我找到的唯一来源是 shapes.inc 的 "Neuro-sama Deep Dives"，它写「2024–2025 更新中 Vedal 实现了向量数据库，让她能记住常客和合作者」——**这个页面是 AI 生成内容，无一条可核验引用，且同一页的"Evidence"里编造了「Vedal 技术 devlog」这种不存在的东西。判定为不可信，不予采信。** https://shapes.inc/fandom/neuro-sama/deep-dive `[推测/不可信]`
- ❌ **「context + summarization」** 这一类说法：我**没有**找到任何一手或可靠二手来源明确说 Neuro 用「上下文窗口 + 摘要」做记忆。wiki 的说法是「选择性记忆」，比 summarization 更含糊。**不要替她补上这个机制。**
- ❌ 第三方帖（如 thecolony.cc 的分析帖）只说「LLM 是她最小的一部分」，**没有给出任何记忆实现细节**，且自身注明来源只有 Wikipedia/vedal.ai/SDK。https://thecolony.cc/post/49e45417-a5b1-4985-9136-b6f51ae06d5a `[二手/浅]`
- ❌ 中文技术文（如 LINUX DO 讨论帖「我至今无法理解 neuro sama 的工作原理」、B 站《NeuroSama 是如何实现的？》技术原理分析）**都是推测性讨论，非官方**。https://linux.do/t/topic/2220260 `[二手/推测]`
- ❌ GitHub 上的 `o9nn/neuro-sama`（带 `identity-framework/docs/architecture.md`、`ARCHITECTURE.md`）**不是官方仓库，也从未声明与 Vedal 有关**，看起来是个人/实验性仓库，**不可作为 Neuro 架构依据**。https://github.com/o9nn/neuro-sama `[推测/来源不明]`

### 3.3 边界声明（我搜了什么）

搜索词（中英日混合）：`Neuro-sama memory long-term memory across streams`、`Neuro-sama memory system vector database transcript RAG`、`Neuro-sama "memory bank" Vedal`、`Vedal Neuro-sama dev stream memory explanation`、`Neuro-sama \"context\" memory summarization`、`Neuro-sama 記憶 機制 向量 資料庫`、`Vedal 記憶 長期記憶 訪談`、`Neuro-sama memory not documented reverse engineering closed source`。

**负结论的读法**：Neuro 本体是闭源商业项目，Vedal 明确表示「不方便说细节」，因此「记忆实现未被公开」是一个**结构性事实**，而不仅仅是「我没搜到」。但要注意：**即使有一次 dev stream 口头讲过，只要没被转录成可检索文本，我也搜不到** —— 所以不能说「他从未讲过」，只能说「**公开可检索范围内没有实现细节**」。

---

## ④ 人格 / 角色一致性做法

### 4.1 有一手依据的部分

1. **人格不是微调出来的角色卡，而是「用数据教出来的」+「被社区养出来的」**
   - [一手] Vedal 原话（2023-04）：
     - 问「她是靠什么语言模型获得那么开朗的性格？」→ Vedal：「**她用的就是大语言模型（LLM）。她之所以开朗，是因为我收集了会让她变成那样的数据来教她。具体调参内情不太方便细说。**」
     - 问「开发阶段你就打算让她变成现在这个性格吗？」→ Vedal：「**不，大体上是预料到的吧。**」并说「Neuro-sama 的皮用的是免费素材，这一点从一开始就定了。**我是从那套皮和声音出发，去决定要做什么样的角色的**」，并明确「**没有参考某个特定的人来设计她的性格**」。
     - [一手] https://premium.kai-you.net/article/655
   - **要点：这里说的是「收集数据去教（training data / tuning）」，措辞含糊，无法确定是 fine-tune 还是别的。Vedal 拒绝说明。**
2. **官方公开承认「部分特质是手动设定的」**
   - [二手但引自官方直播] NeuroWiki：少数特质是 Vedal **手动编程**的，例如「喜欢 cookies」「喜欢食蚁兽」「**dont like Vedal**」；这些往往有**功能性目的**，比如用「给你 cookie」当奖励让她配合任务（调试用）。出处是官方直播片段（wiki 给了 2024-12-19 生日直播 YouTube 时间戳）。https://en.neurosama.info/wiki/Neuro-sama
   - 我在官方站也看到这条被当作角色介绍的一部分：[一手] https://vedal.ai/ （Neuro 的介绍语是 "Born to entertain... occasional menace to her creator"）
3. **「没有预设人设」是官方 wiki 的表述（注意它是二手）**
   - [二手] NeuroWiki：「与多数 VTuber 不同，Neuro **没有预定义的 persona** —— 没有预设的角色设定或背景故事。她不是在演一个角色，而是纯粹作为一个主播存在。」人格与形象是**被社区逐步塑造**的。https://en.neurosama.info/wiki/Neuro-sama
4. **Vedal 会有意识地「保住人格」**
   - [二手] NeuroWiki（引 2023-06-05 dev stream）：Vedal 会周期性更新她的智能，**提升短期与长期记忆、感知力，降低延迟，同时小心平衡她的推理程度，以免把她已经形成的人格抹掉**；他说如果她不再偶尔想捉弄人「就不好玩了」。https://en.neurosama.info/wiki/Neuro-sama
5. **Evil Neuro 是同一套框架下的另一个角色实例**
   - [一手侧证] 官方 API `characterId` ∈ {`neuro`, `evil`}，`displayName` 分别为 "Neuro-sama" / "Evil Neuro"。https://raw.githubusercontent.com/VedalAI/neuro-game-sdk/main/API/SPECIFICATION.md
   - [二手] wiki：Evil 的**滤镜更松、语音模型更外放**，有独立的 prompt 集/行为参数（这句是 wiki 的表述）。https://en.neurosama.info/wiki/Neuro-sama

### 4.2 关于「system prompt / 角色卡」——**没有一手材料**

- 我**没有找到**任何官方公开的 Neuro system prompt、角色卡、人设文档。
- 「她用了 system prompt」「用 prompt engineering 维持人格」这类说法，我看到的来源都是**第三方分析或怀疑论者**（例如 shapes.inc 的 "Counterpoints"、thecolony.cc 的分析帖），属于**别人认为「应该」是这样**，不是官方说法。`[推测]`
- wiki 里那段「她的价值观来自 loss function / 需要 fine-tune 才能改」**明确自我标注为 "a plausible conjecture"，"the actual circumstances might be different"** —— 引用时必须带上这个免责声明。https://en.neurosama.info/wiki/Neuro-sama

---

## ⑤ 开源复现 / 参考项目清单

> 所有「成熟度」判断依据 star 数、README 自述与最近 push 时间（2026-10 抓取）。**关键提醒：没有任何一个项目拿到了 Neuro 的真实架构，全部是「受 Neuro 启发的独立重写」。** 记忆层的评价以各项目**自述/代码事实**为准。

### 5.1 大而全的通用框架（记忆层最值得抄）

| 项目 | 做什么 | 成熟度 | 记忆层 | URL |
|---|---|---|---|---|
| **Open-LLM-VTuber** | 语音交互 AI 伴侣：实时语音对话、语音打断、视觉感知、MCP 工具、浏览器控制、Bilibili 直播接入、Live2D（Cubism 5）+ 桌宠模式；可全离线。**README 自述「project's initial development goal was to recreate the closed-source AI Vtuber neuro-sama」** | **★最成熟**：约 14.0k star，v1.2.1（2025-08），Python | ⚠️ **重要纠偏**：**官方文档站**仍宣传「基于 **Letta** 的长期记忆」；但**主 README 现已写明「long-term memory feature is temporarily removed (coming back soon)」**，只剩聊天记录持久化。**引用时务必区分这两个页面。** 另外其 Agent 接口可接 Mem0 等外部记忆方案 | **[一手]** 仓库 https://github.com/Open-LLM-VTuber/Open-LLM-VTuber ；文档 https://docs.llmvtuber.com/en/docs/intro/ ；直连 README：https://api.github.com/repos/Open-LLM-VTuber/Open-LLM-VTuber/readme |
| **Project AIRI** | 明确自称「**Re-creating Neuro-sama**」：容器化的 AI 角色，支持实时语音、Minecraft、Factorio、Telegram/Discord、Live2D 与 VRM、浏览器/桌面/手机三端 | **★最活跃**：约 50k star，TypeScript，v0.12.0-beta，有完整 monorepo、DevLog、独立子组织 @proj-airi | ⚠️ **记忆是 roadmap 项，不算做完**：README 的 Roadmap 里 `Memory` 只勾了「浏览器内数据库（DuckDB WASM / pglite）」，**`Memory Alaya` 标为 WIP**。相关提案 issue #879 提出「Alaya 记忆层」：统一 MemoryDriver 接口、艾宾浩斯遗忘曲线时间衰减（30 天半衰期）、情绪权重，评分公式 `1.2*similarity + 0.3*time_decay + 0.1*emotion`，技术栈 PostgreSQL + pgvector.rs + Drizzle + xsai/embed。**该 issue 仍 open（17 条评论）** | **[一手]** https://github.com/moeru-ai/airi ；记忆提案 https://github.com/moeru-ai/airi/issues/879 |
| **Luna AI（Ikaros-521/AI-Vtuber）** | 中文圈最全的 AI 主播**工具箱**：几十种 LLM/ASR/TTS 后端可插拔，Live2D / VTube Studio / UE5+Audio2Face / metahuman-stream / Wav2Lip 等多种形象方案，接 Bilibili/抖音/快手/斗鱼/YouTube/Twitch/TikTok 等十余平台 | **成熟**：功能面极广，Python，个人使用免费、商用抽成 10% | 未在 README 中把「记忆」作为独立能力宣传；无明确长期记忆模块（主要靠 LLM 上下文 + 可插拔后端）。**若要抄记忆层，不是首选** | **[一手]** https://github.com/Ikaros-521/AI-Vtuber |
| **Amaidesu（Mai-with-u/Amaidesu）** | 中文 AI 虚拟主播框架（MaiBot 的 VTuber 项目；名字是 "Amadeus" 的 typo 形态）。事件总线 + StreamerAgent（Planner/Replyer）+ ToolRegistry 架构 | **早期开发中**，~54 star，Python 3.12，MIT，2026-10 仍在 push | ✅ **有明确的记忆层且写清了做法**：**SQLite 持久化**场次、消息、礼物、醒目留言、流程单；**跨场次关键词记忆召回，按相关性注入决策上下文**（工具 `memory_query_memory`）。也做主动开口（Rundown 流程单 + 空转检测）。**架构与数据流文档齐全，是"记忆 + 编排"最值得细读的中文项目** | **[一手]** https://github.com/Mai-with-u/Amaidesu |
| **AITuber OnAir** | TypeScript 模块化 AITuber 工具包（chat / voice / memory / agent / avatar 拆成 npm 包），支持 PNG/PSD/Live2D/VRM/Inochi2D/Pet 多种形象，接 YouTube/Twitch 评论 | 成熟度中等偏上：有托管 Web App、`create-aituber-onair` CLI、CI，MIT | ✅ **有名为 memory 的一等模块**：`@aituber-onair/core` 自述「Core runtime tying chat, voice, **memory**, and conversation context together」；另有 `@aituber-onair/kizuna`（观众关系/羁绊：点数、成就、等级、持久存储）——**「对观众的长期关系记忆」这个角度很像陪伴型记忆的需求** | **[一手]** https://github.com/shinshin86/aituber-onair |

### 5.2 Neuro 专用复刻

| 项目 | 做什么 | 成熟度 | 记忆层 | URL |
|---|---|---|---|---|
| **kimjammer/Neuro** | 「**7 天复刻 Neuro-Sama**」，目标是**只用消费级硬件跑本地模型**。模块化 Python：`llmWrapper.py`、`prompter.py`、`tts.py`、`stt.py` + 模块机制（每个模块独立线程、可 `get_prompt_injection()` 往 prompt 注入文本并带优先级） | ~MIT，Python；AIRI 官方 README 称其 "very well completed implementation"，但作者自称 7 天赶工、不很复杂 | ✅ **明确做了长期记忆**：README 写「**Memory/RAG — Long-term (persists across restarts) memories can be manually added, but they will also be automatically generated as the AI talks**」，细节另见仓库 `memories/readme.md`。**架构非常值得抄：STT=RealtimeSTT(faster-whisper tiny.en)、TTS=RealtimeTTS(CoquiTTS XTTSv2)、LLM=text-generation-webui + Llama-3-8B-Instruct-EXL2-4.0bpw、多模态=MiniCPM-Llama3-V-2_5-int4、皮套=VTube Studio 经虚拟声卡做口型（"Vtube Studio handles the lip sync"）** | **[一手]** https://github.com/kimjammer/Neuro （README：https://raw.githubusercontent.com/kimjammer/Neuro/master/README.md ） |
| **my-neuro（morettt/my-neuro）** | 中文项目，受 Neuro-sama 启发，定位「**工作台**」：可训练声音/性格/替换形象，超低延迟全本地推理（<1s）、实时打断、字幕语音同步、视觉、MCP、Bilibili 直播、AI 唱歌、游戏陪玩、主动对话、安卓 App | 活跃，功能覆盖面大，作者自述完成度约 30% | ✅ **明确有「长期记忆，让模型记住你的关键信息、你的个性、脾气」**，且**明确标注记忆系统用的是 MemOS**（https://github.com/MemTensor/MemOS ）。**记忆这块可以直接去看 MemOS，而不是自己造** | **[一手]** https://github.com/morettt/my-neuro |
| **aituber-onair / 其它** | 见上表 | — | — | — |

### 5.3 名字叫「Amadeus」但**不是** Neuro 复刻（易混淆，需分开）

这三个是《命运石之门》主题的数字伴侣，**与 Neuro-sama 无关**，但「陪伴型长期记忆」需求上仍可参考：

| 项目 | 做什么 | 成熟度 | 记忆层 | URL |
|---|---|---|---|---|
| **Code-Amadeus/Amadeus** | **实时多模态桌面 Agent**：可打断的实时语音（AEC/barge-in，打断贯穿 LLM→TTS→物理播放）、角色演出（字幕/口型/表情与播放时间轴同步，SpriteForge + PixiJS）、Provider 委派执行、Host 掌握权限与持久化、AUIP 应用会话协议 | **较高**：AGPL-3.0，0.15.2 Alpha，Windows/macOS/Linux，安装分层 L1–L4 文档极完整 | ❌ **记忆明确是 roadmap，没做**：README 的架构图把 **"Memory & Persona Runtime" 画成虚线**，并写「Its two-way connection to Main Chat represents **proposed** context retrieval and updates; **storage, memory formation, persona updates, and lifecycle mechanisms remain to be designed**」。有一个**默认关闭的可选 character RAG**（`docs/character_rag.md`，含中日文起始语料库、支持个人知识目录）。**定位是「记忆 + 人格」问题域最诚实的一份现状说明** | **[一手]** https://github.com/Code-Amadeus/Amadeus |
| **Lx1228/Amadeus2026** | 《命运石之门》三角色数字伴侣：Live2D + LLM 聊天 + TTS 语音克隆 + 「语音通话」循环（说话→ASR→LLM→TTS）+ 图片识别 + 5 种情绪驱动 Live2D 表情。Tauri 桌面应用 | 可安装：34MB NSIS 安装包，Next.js 16 + React 19 + Tauri 2，MIT | ✅ **有具体记忆做法**：**多会话管理 + 会话压缩 + 记忆提取，按角色隔离**；聊天记录存 `%LOCALAPPDATA%\com.amadeus.kurisu\EBWebView\`，升级不丢、三角色完全隔离。**「会话压缩 + 记忆提取 + 按角色隔离」是陪伴型记忆里很实用的一套最小设计** | **[一手]** https://github.com/Lx1228/Amadeus2026 |
| **AIwaifu（HRNPH/AIwaifu）** | 受 Neuro-sama 启发的开源 AI waifu，卖点是**可微调（finetunable）** | ~517 star，Python，GPL-2.0，仍有 push（2026-07） | 未在仓库描述中强调记忆层 | **[一手]** https://github.com/HRNPH/AIwaifu |

### 5.4 项目索引（找同类项目的入口）

- **[一手]** Awesome AI VTubers（@proj-airi 维护，按字母列了几十个开源 AITuber，含 Open-LLM-VTuber、AIVTuber、AI-Waifu-Vtuber、z-waif、amica、LingChat、NekroAgent 等）：https://github.com/proj-airi/awesome-ai-vtubers
- **[一手]** 官方 Neuro SDK 的社区实现清单（12+ 语言 SDK + 测试工具）：https://github.com/VedalAI/neuro-game-sdk#community-maintained

### 5.5 「记忆」这一层：横向对比一句话总结

| 项目 | 记忆做到了什么程度 |
|---|---|
| kimjammer/Neuro | ✅ 跨重启持久 + 自动生成 + 可手动加（自称） |
| Amaidesu | ✅ SQLite 跨场次 + 关键词相关性召回注入 |
| my-neuro | ✅ 接 MemOS 做「记住你的关键信息/个性/脾气」 |
| Lx1228/Amadeus2026 | ✅ 会话压缩 + 记忆提取 + 按角色隔离 |
| AITuber OnAir | ✅ 独立 memory 包 + kizuna 观众关系持久化 |
| **AIRI** | ⚠️ 只有浏览器内数据库；Alaya 记忆层 WIP |
| **Open-LLM-VTuber** | ⚠️ 文档说 Letta 长期记忆，**README 说已临时移除** |
| **Code-Amadeus/Amadeus** | ❌ 架构图上是虚线 roadmap；仅有默认关闭的 character RAG |

---

## ⑥ 明确的未知与搜索边界

### 6.1 明确查不到（= 公开信息不存在，或至少不可检索）

1. **LLM 具体型号 / 供应商 / 是否微调**。Vedal 明确拒答调参内情（[一手] kai-you 采访）。所有「她用 GPT-x」「她自己微调的模型」的说法**无一手出处**。
2. **记忆系统的实现**：存储后端（向量库？SQL？图？）、写入策略（谁决定记什么、什么时候写）、检索策略（关键词？向量？时间衰减？）、容量上限、是否分层。**全部未知**。wiki 只说到「选择性地记」「不能自动分类/分层」。
3. **编排层的代码形态**：虽然从官方 API 语义能**证明**存在一个统一中枢（说话队列、打断优先级、跨角色 session），但**没有任何官方架构文档或命名**。
4. **TTS 的具体实现**：只有 wiki 的二手说法（基于微软 voice "Ashley" 二次开发），**无官方确认**。
5. **Live2D 的驱动管线**：用 VTube Studio / Warudo / 自研 Unity？口型是音量驱动还是音素驱动？表情是预置动画切换（wiki 说预置）由谁触发？**均无一手材料**。
6. **system prompt / 角色卡 / 人格设定文件的原文**。**从未公开**。
7. **是否出现过「记忆丢失/重置」事故的官方说明**。我在 moegirl 的摘要里看到「Vedal 很久没备份 Evil，差点重置几个月记忆」这类说法，但**原始出处是直播片段，我无法访问第一手 VOD 时间戳核实**，因此**不作为结论**。（所以：我不确认，也不否认。）
8. **一手的性能/成本数据**（上下文长度、延迟、推理成本、GPU 配置）。wiki 提到「日常训练租用多张 NVIDIA H100 的服务器」且「2024-11 起旧电脑专职跑 Neuro 和 Evil」，但都是**二手**，无官方技术披露。

### 6.2 被域名/访问限制挡住的源（换个环境可能还能挖）

- `en.wikipedia.org` / `zh.wikipedia.org`：web_fetch 报 "resolves to a non-public IP address"，**未能直读**。若在能访问的机器上取，英文维基的 References 段可能有额外媒体来源。
- `github.com` 页面本体、`www.reddit.com`、`neurosama.fandom.com`、`ngabbs.com`（403 需登录）、`www.ptt.best`：**均被拦**。我改用 `api.github.com` / `raw.githubusercontent.com` 拿到了需要的内容，**但 Reddit / NGA / PTT 的社区技术讨论我无法直读** —— 若要找「Vedal 在 dev stream 里亲口讲记忆怎么做的」这类线索，**NGA 的《Neuro是如何实现的》帖（tid=42426462）和 PTT C_Chat 的相关帖是还没挖的地方**。
- `premium.kai-you.net/article/655`：**付费墙**。我只读到前 ~40%，后面三节标题是「Neuro-sama 并未从丑闻中解脱」「从 osu! 用 bot 到成为 Neuro-sama」以及**最关键的第 7 节「好み、記憶……AIに感情は宿るのか」（喜好、记忆……AI 会有感情吗）**。**这一节很可能包含 Vedal 对记忆的一手说明，但被付费墙挡住。强烈建议有渠道的话补这一节。**
- B 站 / 抖音等视频内容无法直接取文本；《NeuroSama 是如何实现的？AI Vtuber 的技术原理分析》等技术分析视频**只有二手转述**，我未能核实其内容。

### 6.3 已知的「坏来源」（明确不要引用）

- **`https://shapes.inc/fandom/neuro-sama/deep-dive`**：AI 生成内容，无有效引用，还编造了不存在的「Vedal 技术 devlog」。它的「向量数据库」说法正是很多二手转述的源头。**已列入黑名单。**
- **`https://github.com/o9nn/neuro-sama`**（含 `identity-framework/`、`ARCHITECTURE.md`）：非官方、来源不明，搜索引擎常把它排到第一，容易误导。
- **`NeuroRAG`**：同名但**是生物医学/神经启发的 RAG 学术项目**，与 Neuro-sama **毫无关系**，别搜错。
- **thecolony.cc 的分析帖**：自称「三个架构教训」，但实际只复述 Wikipedia/vedal.ai/SDK，无新信息，且带营销口吻。

### 6.4 「负结论」的正确读法

- 「Vedal 没公开记忆实现」= **有正面依据的结论**（他在采访里明说「不方便细说」+ 项目闭源 + 官方仓库只开放游戏 SDK）。
- 「我没搜到 X」≠「X 不存在」。特别是：**直播/dev stream 里的口头说明，如果没有被转录、被剪辑上传并加上可检索的标题/字幕，就搜不到。** 这类内容需要有人肉刷 VOD 或查 fan archive（wiki 大量引用 `@NArchiver` 的 YouTube 频道 https://www.youtube.com/@NArchiver 作为直播证据，那是最接近一手直播记录的可检索源，值得按需深挖）。

---

## 附：给「陪伴型长期记忆 AI」设计的可迁移要点

1. **官方 API 的 `ephemeral_context` 设计值得直接抄**：把「临时进上下文」和「持久写进记忆」做成一个显式开关，由调用方决定，而不是让模型自己猜。这是**一手可验证的、最接近 Neuro 官方记忆语义的设计**。
2. **两级过滤 + 「边说边判」**：输出前机械词典拦截（便宜、覆盖日常）+ 极少数情况下的类 LLM 自评估（贵、仅兜底）。对应陪伴型 AI 的「安全 vs 延迟」权衡，Neuro 的实践是**默认走便宜路径**。
3. **「人格靠数据教 + 少量手写功能性格 + 用记忆反哺」**：Vedal 的做法是——主体性格来自训练数据，少数特质（喜欢 cookie）是手写的且有**调试目的**，长期记忆会反过来改写她的自我认知。wiki 明确说「只有进了长期记忆的才算数」，这个**「记忆 = 人格的持久化载体」**的定位，正是陪伴型长期记忆的核心命题。
4. **不要指望从 Neuro 抄到记忆实现**——直接去看 kimjammer/Neuro（跨重启持久 + 自动生成）、Amaidesu（SQLite + 相关性召回）、my-neuro（接 MemOS）、Lx1228/Amadeus2026（会话压缩 + 记忆提取 + 角色隔离）这四个真正落地了的实现。
