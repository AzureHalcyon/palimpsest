# 参考项目：Zerolan 四仓（LiveRobot / Playground / core / data）

> 目标项目：**palimpsest**，以原文为根的记忆系统，为长期陪伴设计（见 [`../设计.md`](../设计.md)）。
>
> 本文回答一件事：**Zerolan 这套东西里，哪些能当"第一步"的运行时骨架用，哪些不能，
> 以及它在"记忆"这件事上具体错在哪。**
>
> **证据标注约定**（沿用本项目规矩）：
>
> - `[源码]` —— 我实际读到了源码，给 `文件:行号`
> - `[官方文档]` —— 仓库自己的 README / 配置注释
> - `[实测]` —— 我自己跑出来的数
> - `[推断]` —— 我的判断，非原文陈述
> - `[查不到]` —— 找过，没有
>
> **本次环境说明**：四个仓库已完整克隆到本机，**克隆命令与目录约定见 §9**。
> 本文所有 `文件:行号` 都指那四个本地副本。
> ⛔ **本文不写本机绝对路径**：那会随机器变，而且这份记录要跟着仓库走、给别人看。
> **没有跑过它们**：本文全部结论都是读出来的，
> 凡涉及"跑起来会怎样"的一律标 `[推断]`。
>
> **怎么读出来的**：四个仓库各自做了一遍"逐文件通读"，每次都是只读，
> 四个克隆的工作树事后都是干净的（`git status` 无改动）。
> 凡是本文标 `[源码]` 的关键断言，我又**自己重跑了一遍对应的检索或重读了一遍那段代码**，
> 复核过的都进了 §9 的复算表。**两处没能坐实，已在 §9 末尾显式列出，不当结论用。**
>
> **没搬进来的东西**：通读过程里产出过一份更细的工程清单（约 58 KB，含逐模块的依赖、
> 端点、缺陷流水账）。**它没有进仓库**：按 `.local/准入记录.md` 的规矩，
> 仓库里的每一样东西都要有一条准入记录，而它是一份没有记录的中间产物；
> 它与本文的**详略层次也不同**——本文是"与本设计的关系"，它是"这个工程的全部细节"。
> ⇒ 其中**能支持结论**的部分已经并进本文（每条都带行号）；
> **没并进来的**是那些与本设计无关的工程细节，需要时按 §9 的命令重跑即可拿到。

---

## 0. 一句话

**这不是一个记忆系统的参照物，它是一个"接了很多模型的直播机器人"的参照物。**

四仓合起来是一套完整可跑的**多模态实时 agent 运行时**：事件驱动主循环、八条统一 HTTP 模型管线、
Unity AR 渲染前端、一份共享数据契约。**这一层是它真正的资产，也是它唯一值得借的东西。**

而它的"记忆"——README 上标着已完成的两条——**逐条查完是这样**：

| README 的声明 | 源码实际情况 | 等级 |
|---|---|---|
| 「📓 基于最大记录条数的简单的运行时上下文短期记忆」 | **有，但是"满了就清空"**：超过 20 条 ⇒ 整个历史被重置回配置文件里那两句示例问答 | `[源码]` |
| 「📖 基于向量数据库的长期记忆存储与提取」 | **只有读，没有写**：写入函数 `save_memory()` **在整个仓库里没有任何调用点**，从来没被跑过；读取被一个字面关键字 `"记得"` 触发 | `[源码]` |

⇒ 因此本文的主结论分两句：

1. **能借的是骨架**：事件分发、管线抽象、数据契约、前后端协议——这些与记忆无关，
   而且做得比"个人项目"这个标签该有的水平好。
2. **记忆这一层不能借，而且它的失败方式值得单独记下来**：它把"记忆"实现成
   **对对话做摘要、把摘要存进向量库、按相似度捞回来**——正好是 palimpsest
   第 1 条根（原文是根）的反面。**它不是"做得不够好"，是走的是另一条路。**

---

## 1. 定位：这四个仓库各自是什么

来源：四份 README 的互相引用关系 `[官方文档]`，加本地目录实测。

| 仓库 | 一句话 | 语言 | 干什么的 | 最近提交 |
|---|---|---|---|---|
| **ZerolanLiveRobot** | 直播机器人的**控制框架** | Python 3.10~3.11 | 采集各类输入（语音/弹幕/屏幕/游戏），综合分析，发出动作（说话/控制 Live2D/浏览器/Minecraft/QQ） | `2026-01-17` |
| **zerolan-core** | AI 推理服务的**核心模块** | Python 3.1x | 把 LLM / ASR / TTS / 图像字幕 / OCR / 视频字幕 / VLA / 提示注入防御 / 向量库 各自封成一个 Web API 服务 | `2026-04-11` |
| **zerolan-data** | 各项目之间的**数据格式定义** | Python 3.10+ | 纯 pydantic 数据类，无逻辑；被上面两个仓库同时依赖 | `2026-04-11` |
| **ZerolanPlayground** | **Unity + Vuforia 的 AR 形象展示器** | C# / Unity 6000.0.51f1 | 显示 Live2D 与 3D 模型、播语音、显示字幕与聊天记录、接收服务器推送的显示指令 | `2025-07-01` |

**⇒ 关键结构事实：四仓不是主从关系，是"一个运行框架 + 一组模型服务 + 一份契约 + 一个渲染端"。**
ZerolanLiveRobot 是唯一有主循环的那个 `[源码]`，其余三个都是被它调的。

**⇒ 时间线**（截至本文写作的 `2026-10-09`）：`zerolan-data` 在本机可见历史里最早到 `2024-11-23`
（浅克隆，更早的没取），其余三个起于 `2025-06`；
最后活动是 `2026-04-11`（core 与 data），LiveRobot 停在 `2026-01-17`，Playground 停在 `2025-07-01`。
⇒ **主体框架已经停了约九个月**，而最后那两次提交都只是 README/贡献者图表的改动
（`zerolan-core` 的 `baefa0f` 是 `Merge pull request #5 from … contributors-readme-action`，
`zerolan-data` 的 `647157d` 是 `docs: Update README.md`）`[源码]`。
⇒ `[推断]` **这套东西不是一个正在长跑的项目**，是一份已经停下的完成品。
本文按"读一份存档去学形状"来对待它，而不是按"跟一个活跃上游"来对待。

---

## 2. 运行时骨架（★ 这是唯一值得认真看的资产）

### 2.1 事件驱动主循环：材料是离散事件

ZerolanLiveRobot 自述："没有事件发生，机器人就不会有任何回应" `[官方文档]`。
实现在 `event/event_emitter.py` `[源码]`：

- 全局单例 `emitter = TypedEventEmitter()`（`event_emitter.py:250`）；
- 监听器用装饰器注册：`@emitter.on(event_key)`（`:232-236`）；
- 同步监听器进线程池，异步监听器进 asyncio 队列（`:206-216`）；
- **每个任务默认 5 秒超时**（`:209,214`），但**超时只打日志、不取消任务**——
  文件头自己写明 *"The operation will not cancel the task, but only for the diagnosis of the function called."*（`:14-17`）`[源码]`。

事件类型在 `event/registry.py` 里集中登记，分 `System` / `Pipeline` / `Device` / `LiveStream` /
`Koneko` / `QQBot` / `Playground` 七类 `[源码]`。**这是一个"来源分类"的事件表，不是"内容分类"的。**

**⇒ 与 palimpsest 的关系（正面）**：

palimpsest 设计 §6.2 说"系统不持续观察世界，接收材料的方式是离散的；
两次材料之间可以间隔任意长的时间，间隔本身不产生材料"。
**这个事件循环正好就是这个形状**：它只有 `SECOND` 一个时间事件（每秒一次，`bot.py:113-116`），
且那个事件**不产生材料**——它只用来发心跳。人说话、弹幕到达、屏幕被截图，
各自是独立事件，彼此之间没有"时间在流动"的表示。`[源码]`

**⇒ 与 palimpsest 的关系（反面，重要）**：

设计 §6.3 要求"材料进入系统后走同一条路径，不因为它是怎么来的而分流"。
**这个仓库恰恰是分流的**，而且分流得极其粗暴：`bot.py:219-282` 是一串
`if "打开浏览器" in transcript: ... elif "网页搜索" in ...: elif "游戏" in ...: elif "记得" in ...`
`[源码]`。**输入来源直接决定走哪条分支，而分支里的行为是硬编码的字符串匹配。**

⇒ 这正是设计 §11.1（机械的归机械，模型的归模型）要挡的形状：
**"该不该打开浏览器"是一个判断，被写成了 `if "打开浏览器" in text`。**
`[推断]` 而它的后果可以预见：用户说"你现在别打开浏览器"，也会命中这个分支。

### 2.2 统一模型管线（UMP）：八条管线同一形状

`pipeline/` 下每个模态一个目录，各自 `config.py` + `*_sync.py` + `*_async.py` `[源码]`。
每条管线的形状一致：**一个配置对象进去，一个可 `predict()` 的对象出来。**

```python
config = LLMPipelineConfig(predict_url="http://127.0.0.1:11000/llm/predict")
llm = LLMPipeline(config)
prediction = llm.predict(LLMQuery(text="你好", history=[]))
```

（引自 README §Pipeline `[官方文档]`，实现见 `pipeline/llm/llm_sync.py:33-91` `[源码]`）

**⇒ 可借之处**：`[推断]` 这是 palimpsest 第一步最需要的那种"机械层"——
**模型在别的进程、通过一个死接口调用、调用侧只认数据契约**。
它把"模型"整个关在了 HTTP 后面，主框架里没有任何模型权重、没有 CUDA 依赖。

**⇒ 服务端那一侧的形态（这一条比客户端侧更要紧）** `[源码]`：

`zerolan-core` **不是一个框架、也不是网关，是一个部署脚手架**：
**一个模型一个进程、一个 venv，各自起一个 Flask 应用**（werkzeug 开发服务器），
靠**客户端分端口寻址**把十几个模型串起来 `[源码]`。
仓库里**没有 dispatcher / gateway / orchestrator** `[查不到]`。
全仓 **47 个 `.py` / 2124 行**（实测统计）`[源码]`。

⇒ 这个形态对 palimpsest 有直接意义：**它证明了"机械层可以薄到只剩进程边界"**。
标注、裁决、编码器各自一个进程、各自一套依赖、之间只走 pydantic 契约——
**这是可以照搬的形状**，而且它的好处正是设计 §11.1 要的：
**机械的部分被封在接口后面，换实现不影响别处。**
⚠️ 但代价也在这里：**"薄"不等于"可运维"**（见 §4.5）。

**⇒ 但有一处不一致要记住**：`pipeline/llm/config.py:35` 的默认 URL 是
`http://127.0.0.1:11000/llm/predict`，而 `zerolan-core` 的 `config.template.yaml:22`
里 LLM 服务默认端口是 **11002** `[源码]`。
⇒ 默认配置对不上，必须手改。`[推断]` 这是"两份默认值各写各的"的典型后果。

（其余端口是对得上的，排一遍免得误判：ASR `11001`·OCR `11004`·TTS `11005`·防御 `11006`·
VLA `11009`·向量库 `11010`·视频字幕 `11011`，见 `config.template.yaml` 各节 `[源码]`。
**只有 LLM 这一条错位**——而它恰好是唯一必需的那条，见 `config.py:36-37` 的自述"至少需要启用 LLMPipeline"。）

### 2.3 前后端协议：服务端推、客户端画

ZerolanPlayground 的 C# 侧 `Data/Protocol.cs:5-24` 与 zerolan-data 的
`src/zerolan/data/protocol/protocol.py:8-17` 是**同一个信封**的两份实现 `[源码]`：

```
protocol="ZerolanProtocol" · version="1.1" · message · action · code · data
```

握手：客户端连上后发 `client_hello`，服务端回 `server_hello`（带 ws 端口与资源端口）
（`ZerolanProtocolClient.cs:49-54` 与 `services/playground/bridge.py:69-77`）`[源码]`。
之后**全部由服务端单向推**：`play_speech` / `load_live2d_model` / `load_model` /
`modify_gameobject_scale` / `create_gameobject` / `show_user_text_input` / `add_history` / `show_menu`
（`bridge.py:26-41` 的 `Action` 枚举，与 C# 侧 `Data/Route.cs:5-19` 对得上）`[源码]`。

**⇒ 值得记的一点**：`Data/Route.cs` 里定义了 `ShowMenu`（带 `interactive` 标记的选项菜单），
但**服务端没有任何接收菜单回执的通路**——`bridge.py` 只有 `send`，没有对应的 `on_protocol` 分支
（对比 `bridge.py:62-67`，它只处理 `CLIENT_HELLO` 与 `UPDATE_GAMEOBJECTS_INFO`）`[源码]`。
⇒ **"问了但收不到答案"**：菜单能弹出来，选了没人接。`[推断]`

### 2.4 一个诊断性的护栏做法（值得抄的形状）

`event_emitter.py:37-60` 的 `Timer`：超时不取消任务，只记 `elapsed` 并打日志。
文件头明说这是**为了诊断**，不是为了控制 `[源码]`。

⇒ 这与 palimpsest 的"护栏必须可能触发"（`.local/环境与接入记录.md` K5）是同一件事的两面：
**它把"这个量会在坏情况下响"做成了显式的东西，且写明了它不负责什么。**
`[推断]` 可以借的是**这种"声明式护栏"的写法**：一个量、一个阈值、一句"它响了代表什么、它不负责什么"。

### 2.5 骨架里已经能看到的缺陷（借之前要知道）

| 缺陷 | 位置 | 后果 |
|---|---|---|
| ★ **14 处 HTTP 调用，没有一处设 timeout** | `pipeline/base/base_sync.py:75,90,134,137`·`asr_sync.py:40,51`·`baidu_asr.py:83`·`whisper_asr.py:70`·`tts_sync.py:30,43`·`baidu_tts.py:61`·`milvus_sync.py:23`·`agent/tool/web_search.py:18`·`services/live_stream/youtube.py:19` `[源码]`（逐条核过，无一带 `timeout=`） | **对端挂住 ⇒ 调用的那个 worker 永久阻塞**，没有超时、没有重试、没有降级 |
| `once` 监听器**形同虚设** | `event_emitter.py:227-229`：`if listener.once:` 分支里**只有一行 `listeners.remove(listener)`**（相当于死代码），而 `self._add_task(...)` 无条件执行 | `@emitter.once(...)` 与 `@emitter.on(...)` 行为完全相同：都不会只触发一次 |
| 遍历中改列表 | 同上，`for listener in listeners` 里对同一个 list 做 `remove` | 一旦 `once` 被修好，会静默漏掉监听器（Python 遍历中删除的经典坑） |
| `tool_agent` 被覆盖 | `framework/context.py:110` 建一次（供 game agent 用），`:142` 又建一次 | 配置里的 game agent 持有的是旧对象；`:142` 那次建完没人用 `[推断]` |
| `Timer.elapsed` 在任务未结束时崩 | `event_emitter.py:54-60`：`if self._start and self._end: return self._end - self._start`；否则走 `time.time() - self._end`，而此时 `_end is None` ⇒ `TypeError` | `elapsed` 只在 `finally` 里 `stop()` 之后读（`:96,113`），所以**当前不触发** `[推断]` |
| 主循环没有出口 | `bot.py:47` 赋 `_timer_flag = True`，`:113` 判它，**全仓没有第二处赋值** ⇒ 永远为真 | `bot.py:118` 的 `thread.join()` 与 `main.py:11` 的 `await bot.stop()` **都不可达**；没有优雅停机 |
| 线程靠"不安全强杀"结束 | `common/concurrent/killable_thread.py:54-58` 用 `PyThreadState_SetAsyncExc`，其自身 docstring（`:48`）写明 *unsafe、线程执行可能被破坏* | 同时 `main.py:12` 只捕 `Exception`，Ctrl-C 的 `KeyboardInterrupt` 是 `BaseException` ⇒ 直接穿透 |
| 库代码在 import 链里 `exit()` | `manager/config_manager.py:28`（无配置）、`:62`（LICENSE 校验失败），而 `get_project_dir()` 在 import 时执行（`:107`） | **把进程生死写进了 import**；配置目录靠向上递归找 `LICENSE` 并匹配字符串 `"Copyright (c) 2024 AkagawaTsurunaki"`（`:32-39`）`[源码]` |
| 用 `assert` 做运行期校验 | `pipeline/base/base_sync.py:106,110,140`·`framework/context.py:82`·`pipeline/db/milvus/milvus_sync.py:40,44`·`services/playground/res/res_server.py:49,97` 等 | `python -O` 下全部失效 |
| 资源服务开着调试器 | `services/playground/res/res_server.py:173` 以 `debug=True` 起 Flask（Werkzeug 调试器）；`:93-106` 的 `/resource/file` 无鉴权，按 id 回传任意已注册的绝对路径 | `[推断]` 同网段可读该进程能读到的文件 |
| README 的 worker 数不对 | README 说"默认 4 个 worker"，实为 `ThreadPoolExecutor(max_workers=None)`（`event_emitter.py:146,183`）⇒ Python 默认 `min(32, cpu+4)` | `[官方文档]` 与 `[源码]` 冲突 |

⚠️ **一处我先写错、又改回来的（留在这里当例子）**：

`agent/adaptor.py:57` 写的是 `self._tool_names = set[str]` —— **把类型注解当值赋了**
（应为 `set()`）。我一开始把它记成"工具名集合功能坏掉"，
但重读之后发现 **`_tool_names` 在整个文件里只出现这一行**，
**从来没有被读过或写过** `[源码]`。
⇒ 所以它的实际严重性是"**一个没写完的字段**"，**不是**一个会让功能崩掉的缺陷。
**正确的说法是"未完成"，不是"bug"。**
⇒ ★ 留这一条的理由是它是**本文方法的一个自证**：
"代码看起来不对"与"它真的造成了什么"是**两件事**。
判据仍然回到 §3.10 那句——**有没有一条能到达它的路径**。
（真正到了缺陷那一档的是 `agent/tool_agent.py:56-57`：
工具调用解析**裸 `except Exception` 之后隐式 `return None`**，失败无痕。）`[源码]`

⇒ `[推断]` **这些都是"个人项目"级的、可修的缺陷，不影响骨架的可用性**；
但它们说明一件事：**这个仓库没被当成服务跑过很久，也没被并发压过。**
⚠️ 其中**第一条（无 timeout）不是可修的小毛病**：它意味着这套东西
**在任何一次后端卡住时都会静默地停止回应**，而事件总线不会报错。

### 2.6 它到底记不记日志：只有一个方向，且落不下来

**全仓没有一处 `logger.add(...)`** `[源码]`（`grep logger\.add` 0 命中）`[查不到]`。

⇒ 所有语义日志（ASR 文本 `bot.py:207`、LLM 文本 `bot.py:390`、OCR 文本 `bot.py:313`、
图像 caption `:320`、弹幕 `bilibili.py:67`、历史长度 `:476`）**只到 stderr**，
**没有文件、没有轮转、没有保留策略**。

落盘的只有媒体临时文件：截图 `.temp/image/screenshot-<时间>-<uuid>.png`（`common/io/file_sys.py:62`）、
TTS wav（`common/io/api.py:28-43`）、QQ 图片、Live2D 模型 zip（`bridge.py:120-122`）`[源码]`。
而 **`os.remove` / `.unlink` / `shutil.rmtree` 在非测试代码里一处都没有** `[源码]`
⇒ `.temp` 只增不删。`res_server.py:42` 的 `_files` 映射同理只增。

⇒ **两个结论**：
1. **这台机器上没有任何"当时到底说了什么"的凭据。** 事故复盘时无从查起。
2. ⚠️ 但要注意方向：**这些日志从来没有被读回作为输入**（没有任何代码读 `.temp` 或日志）
   `[源码]`。所以它在"记录不进读取路径"这一条上**意外达标**——
   **达标的原因是它根本没有记录层，而不是它把记录层隔开了。**
   `[推断]` 这两者的区别在出问题时才显出来：**没有记录 = 无法核验**，
   设计 §10 要的"凭什么、谁验的"在这里没有对象。

---

## 3. 记忆这一层：README 说完成了，源码说没有

**这是本文最重要的一节。** 它回答的是"能不能借它的记忆"——答案是**不能，而且理由不是质量**。

### 3.1 短期记忆：满了就清空，不是滑动窗口

`manager/llm_prompt_manager.py` 全文 45 行 `[源码]`：

```python
def reset_history(self, history: list[Conversation]) -> None:
    if history is None:
        self.current_history = deepcopy(self.injected_history)
    else:
        if len(history) <= self.max_history:
            self.current_history = deepcopy(history)
        else:
            self.current_history = deepcopy(self.injected_history)   # ← 这里
```

`max_history` 默认 **20**（`character/config.py:40-41`）`[源码]`。

⇒ **超过 20 条消息时，整个对话历史被替换成配置文件里写死的那 4 条示例问答**
（`character/config.py:31-36`：`"你叫什么名字？"/"我是<YOUR_BOT_NAME>…"` 之类）`[源码]`。

**这不是"滑动窗口丢最旧的"，是"全部忘掉，回到出厂设置"。**
而且它是**静默的**：没有日志、没有事件、没有标记。`[推断]`

⇒ 对照 palimpsest 设计 §4.3：**"粗筛可以漏，不可以丢。漏是这次没取用，它在图上仍在原处；丢是它在图上没有了位置。"**
这里的 20 条上限是**丢**：超出之后那部分内容在系统里彻底不存在了。`[源码]`

**更严重的一层**：`current_history` 是纯内存对象（`llm_prompt_manager.py:14`），
**进程重启即归零**，且**没有任何落盘**——
我搜过全仓：没有 `open(..., 'a')`、没有 `json.dump` 到数据文件、没有 sqlite、没有 pickle、没有 shelve `[源码]`。`[查不到]`

⇒ **后果**：这个机器人**从不记得上一次运行发生过什么**。README 说"上下文理解和人机聊天"，
但它能"理解"的只有本次运行内的最多 20 条。`[推断]`

### 3.2 长期记忆：写入路径是死代码

README 第 26 行标着「📖 基于向量数据库的长期记忆存储与提取」`[官方文档]`。
实际链路：

**写入侧**（`bot.py:501-514`）`[源码]`：

```python
def save_memory(self):
    start = len(self.llm_prompt_manager.injected_history)
    history = self.llm_prompt_manager.current_history[start:]
    ai_msg = summary_history(history)          # ← 调 LLM 把对话总结成一段话
    row = InsertRow(id=1, text=ai_msg.content, subject="history")
    insert = MilvusInsert(collection_name="history_collection", texts=[row])
    insert_res = self.vec_db.insert(insert)
```

**`save_memory` 在整个仓库里的调用点数量是 0** `[源码]`。
`grep 'save_memory'` 只命中定义本身（`bot.py:501`）。主循环里没有它，事件监听器里没有它，
没有任何定时器调它。`[查不到]`

⇒ **向量库里从来没有被写入过任何东西。** README 那条声明的"存储"那一半不成立。

⇒ 顺带记两个当场可见的缺陷：
- `InsertRow(id=1, ...)` **硬编码 `id=1`**（`bot.py:505`），而契约里 `id: int` 是必填
  （`zerolan-data/src/zerolan/data/pipeline/milvus.py:6-12`）⇒ 每次写入都会用同一个 id，语义上不对；
- `except Exception: logger.warning("Milvus pipeline failed!")`（`bot.py:513-514`）
  ⇒ **吞掉异常，只留一行 warning**。`[源码]` 与 `.local/环境与接入记录.md` M8
  "修不回来的失败必须可见地失败"正相反。

**读取侧**（`bot.py:255-262`）`[源码]`：

```python
elif "记得" in prediction.transcript:                       # ← 字面关键字触发
    query = MilvusQuery(collection_name="history_collection", limit=2, ...)
    result = self.vec_db.search(query)
    memory = result.result[0][0]                             # ← 只取第 1 条
    memory = memory.entity["text"]
    self.emit_llm_prediction(f"{memory}\n\n请根据上文回答：{prediction.transcript} \n")
```

⇒ 四个问题，一个比一个要紧：

1. **触发条件是硬编码中文关键字 `"记得"`**。用户说"你记不记得…"能触发，
   说"我上周跟你提过的那件事"不触发。**这是"该不该想起来"被写成字符串匹配**——
   设计 §11.1 明确列为要挡的形状（"判：选什么、看哪里、该不该说"归模型）。
2. **`limit=2` 取了 2 条，只用 `[0][0]` 第 1 条**：第 2 条白取，且第 1 条的"第 1"由
   向量距离决定——**没有权重、没有相对取用、没有"各取多少"**。
   设计 §4.1 要求的输出是"一个子集 + 子集内各部分的相对权重"，这里两者都没有。
3. **捞回来的文本被字符串拼进问题前面**（`f"{memory}\n\n请根据上文回答：…"`）
   ⇒ 这是设计 §5 说的"原文片段"形态：长度不可控、无法表示完整信息。
4. **没有"是不是同一件事"的判断**，也没有失效、时间、来源——捞到什么就信什么。

### 3.3 存进去的到底是什么：摘要，不是原文

`summary_history`（`agent/api.py:161-173`）`[源码]`：

```python
system_template = "将这段用户与你的对话总结成一段话，需要包含重要细节。"
text = "".join(f"[{conversation.role}]\n{conversation.content}" for conversation in history)
...
response = _model.invoke(result)      # ← 一次 LLM 调用
return response
```

⇒ **落进"长期记忆"的东西是一次 LLM 摘要**。原文（用户到底说了什么、当时怎么说的）**没有存**。
捞回来的是摘要，拼进 prompt 的也是摘要。

**⇒ 这就是与 palimpsest 的根本分歧，一句话说清：**

| | Zerolan | palimpsest |
|---|---|---|
| 存的是 | **对话的摘要**（派生物） | **逐字原文**（设计 §2.1） |
| 派生物的地位 | 派生物**就是**记忆本体 | 派生物可重算，**不落库**（§5.6） |
| 摘要丢了原文还在吗 | **不在了** | 在（原文是根） |
| 会不会在派生物上继续加工 | **会**：摘要 → 检索 → 拼进 prompt → 再生成 | **不许**（§11.2 第 11 条） |

⇒ 这正是 `.local/准入记录.md` 里已经认定的那条高危假设的**实物**：
**"自包含句 = 记忆本体"**。这里的形态是"一段总结 = 一条记忆"。
⚠️ **它抄进来不会报错**——它会跑得很好，只是把记忆做成了不能再回到原文的东西。`[推断]`

### 3.4 那个"实验性记忆"开关，和它实际做的事

`config.py:26-27` `[源码]`：

```
enable_intelligent_memory: bool = False
  '🧪 EXPERIMENTAL: Automatically scores and filters conversation history entries
     based on sentiment, relevance, and safety.'
```

实现是 `bot.py:438-458` 的 `exp_memory()` `[源码]`：

```python
t_memory = 0.3 * (l_max - len_history) / l_max + 0.2 * s + 0.2 * b + 0.1 * r
return t_memory > 0.5
```

`s` 是 LLM 打分的情感分（`agent/api.py:200-214`），`r` 是 LLM 打分的"记忆价值"分（`:218-233`），
`b = self.filter.match(response)` 是敏感词命中**计数**（`character/filter/strategy.py:43-48`，
**≥0 且无上界**）。**每轮多两次 LLM 调用**（`s` 一次、`r` 一次）。

⇒ **它是"要不要把这一轮记进短期历史"的门控，不是"存不存长期记忆"**——
与它的描述（"filters conversation history entries"）一致，但与 README 的第 26 条不是一回事。`[源码]`

**四个当场可见的问题** `[源码]`：

1. **量纲不一致**：`bot.py:479` 传参是 `len(prediction.response)`（**字符数**），
   而形参位置是 `len_history`（`:438`），`l_max` 是 `max_history`（**条数**，`:440`）
   ⇒ `(l_max - len_history)/l_max` 这一项算的是"回复有多长"，不是"历史有多满"。
   条数与字符数被相减。`[源码]`
2. **`b` 的分支是死代码**：`bot.py:466-470` 里 `is_filtered` 为真时**提前 `return None`**，
   于是 `exp_memory(...)` 里那句 `if not is_filtered: b = 0`（`:447-450`）**永远进不去**，
   `b` 永远是命中计数，权重 0.2 下**可以主导**结果。
3. **阈值大概率够不着**：`l_max`（条数）通常远小于 `len(response)`（字符数）
   ⇒ 第一项变成一个大负数。`[推断]` 这项门控**可能实际上从不放行**——
   但**这没有测试**（见 §8），所以没人知道。
4. `bot.py:478-483`：门控不过时**不更新历史**。默认关（`False`）时走 else 分支每轮更新；
   一旦打开，**被判为"不值得记"的轮次就直接不记**（本轮 user + assistant 一起蒸发）——
   而"漏掉一轮"和"清空全部"（§3.1）之间没有任何中间档。

### 3.5 人格：一个 system prompt + 4 条注入问答

`character/config.py:27-36` `[源码]`：`system_prompt` 是一段写死的猫娘设定（含
`<YOUR_BOT_NAME>` 占位符），`injected_history` 是 4 条"你叫什么名字/我是…"的示例问答，
`_parse_history_list`（`llm_prompt_manager.py:33-45`）**按奇偶下标猜角色**
（偶数=user，奇数=assistant）`[源码]`。

⇒ **人格是每轮重新装载的常量，不是被取用的内容。**
设计 §6.5 明确说"设定起作用靠的是激活，不是重复；把设定每轮重述一遍等于每轮重新装载，
那样设定不会成为系统的一部分"。**这里就是每轮重述。** `[源码]`

⇒ 另外：**`Conversation` 没有时间**。`metadata` 字段存在（`zerolan-data/.../llm.py:24`）
但调用侧一律传 `None`（`agent/adaptor.py:23-29`）`[源码]`。
⇒ 记忆里没有"何时"，设计 §3.8 / §8.1 要按时间尺度与成立区间取用，**这里连原料都没有**。

### 3.6 ★ 这条历史没有键：四个输入源共用一条

**只有一个 `LLMPromptManager` 实例**（`framework/context.py:85`）`[源码]`，
**`current_history` 是一个 list，没有按用户 / 会话 / 平台 / 房间分片的任何维度** `[源码]`。

⇒ 麦克风、B 站弹幕、QQ 消息、屏幕 OCR **全部共享同一条历史**——
它们最后都调 `bot.py:462` 的同一个 `self.llm_prompt_manager.current_history` `[源码]`。

⇒ 后果：**弹幕用户 A、QQ 群友 B、和主人说的话，被拼进同一条对话流。**
弹幕的用户名只出现在当次 prompt 文本里（`bot.py:291`：
`f"你收到了一条弹幕，用户"{event.danmaku.username}"说：\n{...}"`），
**一旦进历史，说话人身份就丢了**——`Conversation` 只带 `role` / `content` `[源码]`。

**⇒ 这是一条很干净的对照**：设计 §6.1 要求材料带**时间戳与说话者**。
这里两样都没有：时间没有、说话者只在当次提示词里、**且第二条之后就查不出来了**。
⇒ `[推断]` 而且这不是"缺个字段"，是**结构上分不开**：
它没有"材料"这一层，只有"发出去的那串 messages"。

### 3.7 情绪：只有"选哪个 TTS 音色"

`enable_sentiment_analysis`（`config.py:24-25`，默认 `False`）`[源码]`：
对 LLM 回复判一个情感标签，用来从 `prompts_dir` 里挑文件名带该标签的参考音频
（`character/config.py:49-57` 的命名约定 `[lang][sentiment_tag]text.wav`）`[源码]`。

⇒ **情绪是"这一句用什么音色念"的一个瞬时标签，不落库、不累积、不参与任何检索或判断。**
设计 §3.8 要的"情绪作为调制函数"在这里完全没有 `[查不到]`。

### 3.8 视觉：把模型的描述当成事实再喂给模型

`bot.py:304-321` `[源码]`：截图 ⇒ OCR，OCR 平均置信度 > 0.6 就用 OCR 文本，
否则走图像字幕模型（BLIP）生成 caption，再**翻译**，再作为 LLM 输入 `[源码]`。

⇒ `[推断]` 这是**模型生成的文本成为后续模型的输入**——形式上就是"处理产物回流"。
它比摘要那条轻（caption 描述的是屏幕上真实存在的东西，有对应的实拍帧），
但**链路里没有任何东西记录"这句话是模型说的"**：OCR 文本与 caption 文本
在 LLM 眼里和用户说的话没有区别。

**⇒ 记一条通用的**：只要多模态前端存在，**"观测"与"对观测的描述"就必然混在同一条输入里**。
palimpsest 要区分"实际发生过的事"与"由它算出的东西"（设计 §2.2），
⇒ 这里给了一个反面样本：**不标来源，两者就分不开了。** `[推断]`

### 3.9 ★ 生成文本逐字回流成材料——这一条要说准

**我在这一节前面一度想说"它没有回流"，那是错的。要分两层说清：**

**第一层：模型自己的回复，逐字回到下一轮的材料里。这是真的回流。**
链路 `[源码]`：`emit_llm_prediction`（`bot.py:460-488`）把
`prediction.history`（含本轮 user + 本轮 assistant 的**全文**）写回 `current_history`
（`:480` 或 `:483`），下一轮再作为 `history=` 发出去（`:462`）。
⇒ **上一轮它自己说的话，成了这一轮的输入。** 这就是"处理产物回流为材料"。

⚠️ **而且写回的不是逐字原文。** `bot.py:466-470` 的敏感词过滤只判不删，
但 `bot.py:473-474` 会把回复开头的换行删掉，**改完之后才写回**：

```python
if prediction.response[0] == '\n':
    prediction.response = prediction.response[1:]   # ← 就地改
...
self.llm_prompt_manager.reset_history(prediction.history)   # ← 改完的版本落进历史
```

⇒ 对照设计 §2.1 / §11.2 第 1 条（**原文逐字保存，定稿之后永不改写**）：
**这里存进历史的文本是就地改过的副本，不是原文。**
这是本节最要紧的一条：**不是"存得少"，是"存下来的东西已经不是原来那个了"。** `[源码]`

**第二层：摘要回流那条路不通，但不是因为设计，是因为没接线。**
唯一会写摘要的 `save_memory()` 是死代码（§3.2）。
⇒ 所以"派生物不回流"这一条**它侥幸达标**——
**达标的原因是摘要能力根本没接上，不是它把派生物隔离了。** `[推断]`
⚠️ 这个区别很重要：**它不是一个"做对了"的先例，它是一个"还没开始做"的状态。**

**⇒ 顺带一条可直接借的观察**：`bot.py:473` 的 `prediction.response[0]`
在回复为空串时会 `IndexError` `[源码]`——
⇒ 与 `.local/环境与接入记录.md` A1（推理模型 `max_tokens` 给小 ⇒ 返回空 `content`）**是同一个坑的两种落点**。
A1 那边记的是"读数作废"，这里记的是"**空回复会让进程在取首字符时崩**"。
`[推断]` 通用形式：**凡是"取第 0 个字符/第 0 个元素"的地方，都要先问空集怎么办。**

---

### 3.10 一份 README 与代码的对照清单（★ 本文最可操作的一节）

把"它说自己做了什么"与"代码里是什么"并排放。**这不是挑刺，是本文的一个方法结论：
外部项目的完成度标记不能当依据，必须落到调用点与行号。**

| README 的说法 | 代码实际情况 | 依据 |
|---|---|---|
| 「📖 基于向量数据库的长期记忆**存储**与提取」 | **存储侧不存在**：`save_memory` 零调用点 | `README.md:26` vs `bot.py:501` |
| 「🍻 根据弹幕内容**挑选**并回复」 | **挑选函数被注释掉了**：`select_max_long_one` 的唯一调用点在注释块里；活路径是**每条弹幕立刻回** | `README.md:16` vs `bot.py:298`、`services/live_stream/bilibili.py:98-107`、`bot.py:289-292` |
| 「🛠️ 根据上下文语境采取行动」 | 行动路由是**硬编码子串**，且只挂在麦克风通道上 | `bot.py:219-275` |
| 「必须至少配置 LLM、ASR、TTS」 | 代码只对 LLM 做断言，ASR/TTS 可缺 | `README.md:57` vs `framework/context.py:82` |
| 「默认按下 `f8` 可开启/关闭麦克风」 | 默认 `default_enable_microphone=False`，VAD 线程只在为真时启动 ⇒ 默认按 f8 只打印一句"已禁用" | `README.md:96` vs `config.py:10-12`、`bot.py:64-66,179` |
| 「同步监听器在默认 **4 个 worker** 的线程池中执行」 | 实为 `max_workers=None` ⇒ Python 默认 `min(32, cpu+4)` | `README.md:202` vs `event_emitter.py:146,183` |
| 「没有事件发生，机器人就不会有任何回应」 | **与代码一致**——但这不是设计声明，是**能力缺失**：唯一的周期性事件 `SecondEvent`（`bot.py:115`）**没有任何监听器**（唯一的那个被注释在 `:294-302`） | `README.md:196` vs `bot.py:294-302` |
| 「TTS 已集成 · 流式 ✅ · 1.3 GiB」 | **整个 TTS 是桩**：`tts/app.py:1-3` 首行 `"""Not implemented"""`；且 `starter.py:154-155` 传 `host=`/`port=` 而 `TTSApplication` 无 `__init__` ⇒ **构造即 `TypeError`** | `zerolan-core` README §文本转语音 vs `tts/app.py`、`starter.py:154-155` |
| 「Qwen 流式 ✅」 | `llm/qwen/model.py:97-99` 的循环里**没有 `yield`** ⇒ 空生成器 | `zerolan-core` README §大语言模型 vs `llm/qwen/model.py:97-99` |

⇒ `[推断]` 上表里前六条的**共同形状**是：
**"能力已实现"与"能力已接线"被当成同一件事。**
函数的定义被写出来了，README 就打了勾——**而调用点从来没有接上。**
（后两条更重一层：**不只是没接线，是接上就会崩。**
⇒ 说明**那些接口从来没有被调用过一次**——调一次就发现了。）

⇒ ★★ **这条与本项目的直接关系**：
palimpsest 的规矩是"**一个断言要能回答『凭什么』和『谁验的』**"（`../文档规范.md` §二）。
**这份清单是那条规矩的外部旁证**：只要不落到调用点，
"已完成"这个说法可以一直挂着而不报错。
`[推断]` 建议的形式化版本：**凡"某能力已具备"的断言，必须指到一个调用点。**
（本仓库的 `check_docs.py` 已经在机械层做同类的事：查编号而不是查文字。）

---

## 4. 数据契约：zerolan-data 做对了什么，缺了什么

`zerolan-data` 只有 19 个 `.py`、47 KB，**没有逻辑，全是 pydantic 数据类** `[源码]`。

### 4.1 它做对的三件事

1. **契约独立成仓、被多方依赖，且不含数据。**
   ZerolanLiveRobot 与 zerolan-core **都 `from zerolan.data.pipeline.llm import ...`** `[源码]`
   （`bot.py:10-16` 与 `llm/app.py:3`）。⇒ 与 `.local/环境与接入记录.md` M10
   "先定格式契约，再写实现"是同一个做法，**而且是本机已有的正面先例**。

2. **`LLMQuery` / `LLMPrediction` 天然是一对**（`pipeline/llm.py:27-40`）`[源码]`：
   query 带 `text` + `history`，prediction 带 `response` + `history`（含本轮）。
   ⇒ **请求与响应同构**，这一点对 palimpsest 有用（见 §6）。

3. **服务状态有独立的小契约**：`AppStatusEnum{RUNNING,STOPPED,INITIALIZING,UNKNOWN}` +
   `ServiceState(state, msg)`（`data/state.py`）`[源码]`。
   ⇒ "一个服务现在是什么状态"被当成数据，不是当成日志。

### 4.2 它缺的（都直接对应 palimpsest 的硬要求）

| 缺什么 | 位置 | palimpsest 为什么要它 |
|---|---|---|
| **`Conversation` 没有时间戳** | `pipeline/llm.py:17-24`：只有 `role` / `content` / `metadata`；实际调用一律 `metadata=None` | 设计 §6.1 要求材料带时间戳；§3.8/§8.1 的尺度与成立区间都以时间为原料 |
| **没有"说话者"概念**，只有 `role` 的四个固定值（system/user/assistant/function） | `pipeline/llm.py:7-14` | 设计 §6.1 要区分"人说的话"与"系统说的话"——`role` 能凑合，但**没有 uid/username** |
| **没有原文位置（区间）** | 全仓 `[查不到]` | 设计 §2.3：地址是"文档标识 + 字符区间"，唯一地址形式 |
| **没有来源/出处字段** | 全仓 `[查不到]` | 设计 §2.2 的准入判据是"它是不是真的发生过" |
| **`content` 默认 `None` 却是必填语义** | `pipeline/llm.py:23` `content: str = Field(default=None, ...)` | `[推断]` 类型标注是 `str`（非 `Optional`）但默认 `None` ⇒ **校验形同虚设，可以塞空内容** |
| **`data: T` 用裸 `TypeVar`** | `protocol/protocol.py:8-17` | `[推断]` pydantic v2 下这个字段的校验是按 `Any` 走的，信封类型不受保护 |
| ★ **`id` 的唯一性是假的** | `pipeline/abs_data.py:15,27`：`id: str = Field(default=str(uuid4()))` | ★ **`str(uuid4())` 在类体求值时就算完了**，不是 `default_factory` ⇒ **同进程内所有没显式传 `id` 的请求/响应共享同一个 UUID** `[源码]`。而它的 docstring 恰恰写着"确保每个 query 有唯一标识"（`:12-13`）`[源码]` |

⇒ **结论**：`zerolan-data` 的**做法**（契约独立成仓、pydantic、双向同构）值得照抄；
**它的字段集完全不够用**——缺时间、缺位置、缺来源，而这三样正是 palimpsest 的根。
`[推断]`

⇒ ★ 表里最后那一行值得单独记：**这是"pydantic 默认值的求值时机"这个坑的标本。**
`.local/环境与接入记录.md` M5 / N3 记的是"看起来确定的实现不一定确定"（内置 `hash` 每进程加盐）；
这一条是它的**同族**：`default=` 与 `default_factory=` 长得很像，
**但一个在定义时求值、一个在调用时求值**。
⇒ `[推断]` 通用形式：**凡是"每一份都该不一样"的字段（id、时间戳、指纹），
都要显式验一次"两份真的不一样吗"**；它不会报错，只会让所有记录共用一个 id——
而 id 一旦共用，**"哪条是哪条"就查不出来了**，正好废掉记录层的用途。

### 4.3 一处值得单独记的契约违例

`InsertRow` 声明了 `text` / `subject` 三个字段（`pipeline/milvus.py:6-12`）`[源码]`，
但服务端实现里 **`subject` 被写死成 `"history"`**（`zerolan-core/database/milvus/milvus.py:42-46`）：

```python
data = [{"id": i, "vector": vectors[i], "text": docs[i], "subject": "history"} ...]
```

⇒ **契约里有一个字段，实现里把它忽略掉换成常量。**
`[推断]` 这是"契约与实现各写各的"的实例——**契约不带上"谁验的"，就会退化成文档。**

### 4.4 向量库这一层的真实形态

`zerolan-core/database/milvus/milvus.py` `[源码]`：

- 客户端是 `MilvusClient(config.db_path)`，`db_path` 默认 `"./.data/milvus.db"`
  （`database/milvus/config.py:6`）⇒ **嵌入式 Milvus Lite（单文件），不是 Milvus 服务集群**；
- 嵌入函数是 `milvus_model.DefaultEmbeddingFunction()`（`:22`），维度**硬编码 768**（`:20`）；
- `auto_id=True`（`:33`）建集合，插入时又自己给 `id`（`:42-46`）⇒ `[推断]` 两处 id 语义打架；
- **没有任何"更新/失效/删除"接口**，只有 `insert` 与 `search` 两个动作（`:80-83`）`[源码]`。

⇒ 对照设计 §8.3：**失效是标记，不是删除**。这里是**连标记都没有**——
只有"加一条"和"按相似度捞"。
⇒ 对照设计 §2.2：**原文可以无损失重算，因此不能重算的才需要保存**。
这个库保存的是**不能重算的摘要**，而它本来该保存的原文根本没进来。`[推断]`

### 4.5 zerolan-core 的服务层：契约分离做对了，可运维性一处都没有

这一节补 §2.2 那一侧的实情。**它的"薄"是做对了的，但薄到没有一层可运维的。**

**★ 做对的一件事：契约与实现彻底分离。**
`zerolan-core` **完全使用 `zerolan-data` 包，本地零重定义** `[源码]`：
全仓 **25 条 `from zerolan...` import，覆盖 20 个文件**，
`.py` 里的 `class` 清单中**没有任何数据模型**（只有 `AbstractApplication` / `AbstractModel` /
各 `*Application` / `*Model` / 14 个 `@dataclass *Config`）`[源码]`。
⇒ 这是 §4.1 第 1 条那件事的**硬证据**：契约真的被当成外部依赖在用，不是摆设。

⚠️ **但版本是逐模块钉的，而且钉的不是同一版** `[源码]`（逐条从 `uv.lock` 里抄出来的）：

- **14 个模块**（`asr/kotoba_whisper_2`·`asr/paraformer`·`database/milvus`·`img_cap/blip`·`llm/chatglm3`·
  `llm/deepseek`·`llm/glm4`·`llm/ollama`·`llm/qwen`·`llm/shisa`·`llm/yi`·`ocr/paddle`·
  `vid_cap/hitea`·`vla/showui`）钉在
  `rev=1.5.0` ⇒ commit `942e2d8bd805850ed712e0b58998fc77c5c45d01`；
- **只有 `defense/deberta`** 钉 `rev=1.6.0` ⇒ commit `647157ddfe043ab89315c31902b0747158c1706d`
  （它需要 `DefenseQuery.history`）。

⇒ `[推断]` **同一个系统里跑着两个版本的契约**，而版本差异只在某个字段上——
**这类不一致不会有任何报错**，只会在某个服务上少一个字段。
⚠️ 而且 `defense` 钉的那个 commit **正是本机检出的 `zerolan-data` 的 HEAD**
（`647157d`，2026-04-11）`[源码]` ⇒ **一个模块跟着 main 走，其余十四个冻在 1.5.0**。
（本机检出的 `zerolan-data` 自身版本号已是 `1.6.0`，见 `pyproject.toml:3`。）
⇒ ★ 通用形式：**契约独立成包之后，"谁用的哪个版本"本身要进台账**，
否则"契约统一"会退化成"各钉各的"。

**★ 做对的第二件事：一后端一 venv 的依赖隔离。**
15 个 `pyproject.toml` + 15 个 `uv.lock` 精确对应 15 个模型目录，
14 个 `.python-version`（12 个 3.11、`chatglm3` 与 `hitea` 是 3.10）`[源码]`。
⇒ `[推断]` 对 palimpsest 直接有用：**标注模型、裁决模型、编码器各自要的 torch / transformers
版本必然不同**，一后端一 venv 是唯一不打架的做法。
⚠️ **但它只做了一半**：**根目录没有任何 `pyproject.toml` / lock** `[源码]`，
⇒ 共享壳（`starter.py` / `common/` / 各 `app.py`）的依赖**只由"当前激活的那个 venv"提供**。
**同一个 `common/abs_app.py` 在 15 个环境里跑 15 个版本。**
⇒ ★ 通用形式：**隔离要连"共享的那一层"一起管**，否则共享层就是那个没有锁的地方。

**★ 做对的第三件事：`AbstractModel` 的三方法接缝。**
`load_model()` / `predict()` / `stream_predict()`（`common/abs_model.py`）+ 每模型一个 config `[源码]`。
⇒ 一个模型要接进来，只需要实现这三个方法。

**❌ 可运维性：一处都没有。** 逐条列，每条都能复算：

| 缺什么 | 位置 | 后果 |
|---|---|---|
| **零测试** | `tests/` 里**只有 4 个二进制素材**（png / wav / mp4），**没有一个 `test_*.py`** `[源码]` | 全部 47 个 py、2124 行，**一行都没被自动化验过** |
| ★ **`/status` 健康检查是死代码** | 基类在 `self._app` 上注册 `/{name}/status`（`common/abs_app.py:15-16`），但**每个子类构造时都 `self._app = Flask(__name__)` 把它丢掉**（`llm/app.py:15`·`asr:16`·`defense:15`·`img_cap:16`·`ocr:17`·`vid_cap:16`·`vla/showui:15`）`[源码]` | 端点不存在；**且即使可达也永远返回 `stopped`**——`self.status` 全仓只在 `abs_app.py:13` 被赋值为 `STOPPED`，**没有任何地方再改它** `[源码]` |
| **无 `/health`、无鉴权、无 CORS、无并发控制** | 全仓 `[查不到]` | — |
| **客户端可指定服务端任意路径** | `asr/app.py:29-33`：`if not os.path.exists(query.audio_path): 保存上传; else: audio_path = query.audio_path` ⇒ **客户端给一个存在的路径，服务端就直接读它**（同类另有 4 处）`[源码]` | `[推断]` 任意文件读取（路径由请求体给） |
| **临时文件永不删除** | 全仓 `os.remove` / `unlink` / `rmtree` / `cleanup` **0 命中** `[源码]` | 上传素材写进 `.temp/` 后只增不减 |
| **无 seed、无模型指纹** | `seed` / `manual_seed` 0 命中；`self.model_id` 从不进响应、不进日志，唯一消费点 `validate_model_id`（`abs_app.py:27-28`）**是死代码** `[源码]` | **同一输入不可复算**——与 `.local/环境与接入记录.md` E2（冻结指纹）正相反 |
| **日志无 sink** | loguru 但**没有一处 `logger.add()`**；日志打了 `User Input` / `Model response` 明文，**却不带 `LLMQuery.id`** `[源码]` | **无法按请求聚合**；且从不被读回（这一半是对的，见 §2.6） |
| **TTS 整个是桩** | `tts/app.py:1-3` 首行 `"""Not implemented"""`，`gpt_sovits/model.py:20-24` 全部 `raise NotImplementedError`；而 `starter.py:154-155` 传 `host=` / `port=`，**`TTSApplication` 没有 `__init__`**，基类签名是 `(model, name)` ⇒ **`TypeError`** `[源码]`（已逐条核对：8 个 `app.py` 里只有 `tts/app.py` 没有 `def __init__`） | README 说 TTS 已集成、流式 ✅、1.3 GiB `[官方文档]` ⇒ **构造即崩，从没跑过** |
| **Qwen 流式是空生成器** | `llm/qwen/model.py:97-99` 的循环里**只有 `history[-1] = (text, response)`，没有 `yield`** `[源码]` | README 标 Qwen 流式 ✅ ⇒ **调用方拿到的迭代器一个元素都没有** |
| **同目录自相矛盾的 manifest** | `vid_cap/hitea/pyproject.toml:17,19` 要 `torch>=2.9.1` / `transformers>=4.57.5`，同目录 `requirements.txt:116,122` 钉 `torch==2.4.0` / `transformers==4.43.3` `[源码]` | 用哪个装法得到两个不同的环境 |

**⇒ 本节的方法结论（与 §3.10 是同一个形状，但在另一侧）：**

§3.10 那条是**"能力写了但没接线"**（函数有定义、没有调用点）；
这一节这条是**"能力写了但被覆盖/被写死"**（`/status` 注册了又被丢掉、
配置项写了但代码忽略）。**两种都不报错。**

⇒ ★ **通用形式**：外部项目里，"接口存在"与"接口活着"要分开验。
判据不是"有没有这段代码"，而是**"有没有一条路径能到达它、且到达之后它真的动"**。
（`/status` 同时踩了两条：**不可达**，且**可达也不动**——`self.status` 从不被改。）
`[推断]` 这条对 palimpsest 是现成的：`.local/环境与接入记录.md` K5（护栏必须可能触发）
说的就是后半句；**前半句（可达性）本项目还没有一条对应的检查。**

---

## 5. 把四仓与 palimpsest 的设计逐条对上

下表是本文的核对结果。**"能借"一栏只写真的能搬的，"借不了"一栏必须写清为什么。**

| palimpsest 的要求 | Zerolan 的情况 | 判定 | 依据 |
|---|---|---|---|
| §6.2 材料是离散事件，间隔不产生材料 | 事件循环就是这个形状；唯一的 `SECOND` 事件不产生材料（且它没有监听器） | ✅ **能借骨架** | `event_emitter.py:246`、`bot.py:113-116`、`bot.py:294-302` |
| §6.3 材料走同一条路径，不分流 | **按来源硬分流**，且行为是字符串匹配 | ❌ **不能借** | `bot.py:219-282` |
| §11.1 机械的归机械，判归模型 | 触发/路由/门控全是 `if "关键字" in text` 与线性加权阈值（且量纲错） | ❌ **不能借** | `bot.py:219-282,438-458,255` |
| §2.1 原文逐字保存、定稿后永不改写 | **存的是摘要**（摘要即记忆本体，原文从未保存）；且写回历史前**被就地改过**（删开头换行） | ❌ **方向相反** | `agent/api.py:161-173`、`bot.py:501-506`、`bot.py:473-474,483` |
| §5.6 编码器输出不落库 | 落库的就是模型输出（摘要）——Milvus 门面正是"编码→落库→检索" | ❌ **方向相反** | `agent/api.py:161-173`、`zerolan-core/database/milvus/milvus.py` |
| §11.2-11 一切记录不进读取路径 | **无记录层**（无日志 sink、无对话落盘）；模型自己的输出**逐字回流**为下一轮材料 | ❌ **违规**（但回流的是"输出"，不是"记录"） | `bot.py:462,483`、`bot.py:473-474`；`[查不到]` 无 `logger.add` |
| §3.1 节点锚在原文区间 | **无区间、无锚定概念** | ❌ **没有** | 全仓 `[查不到]` |
| §3.8 / §8.1 节点与边带时间尺度与成立区间 | `Conversation` 无时间；无失效标记（Milvus 门面只有 insert/search，**无 update/delete**） | ❌ **没有** | `pipeline/llm.py:17-24`、`milvus.py:80-83` |
| §4.1 激活输出"子集 + 相对权重" | 只有 top-k，且只用第 1 条 | ❌ **没有** | `bot.py:255-262` |
| §4.3 读取成本与图的大小无关 | 向量检索天然满足（近似最近邻） | ✅ **形态对** | `milvus.py:53-61` |
| §6.5 初始化靠激活取用，不靠重复 | system prompt 每轮原样重述 | ❌ **相反** | `llm_prompt_manager.py:12-14,38` |
| §6.1 材料带时间戳与说话者 | 两样都没有；四个输入源**共用一条无键历史**，说话人进历史即丢 | ❌ **没有** | `framework/context.py:85`、`bot.py:291,462`、`pipeline/llm.py:17-24` |
| M10 先定格式契约再写实现 | **契约独立成仓、双向同构**（但 15 个模块钉了两个版本） | ✅ **正面先例** | `zerolan-data` 全仓；各 `uv.lock` |
| M8 让系统可以离线整套跑 / 失败要可见 | `test_abs.py` 起了本地替身服务（正面）；但关键失败被 `logger.warning` 吞掉，且 TTS/Qwen 两条路径**一调就崩** | ⚠️ **各半** | `tests/pipeline/test_abs.py:10-17` vs `bot.py:513-514`、`tts/app.py`、`llm/qwen/model.py:97-99` |
| ★ **"已实现"要指到调用点**（本文新增，见 §3.10/§4.5） | 长期记忆写入、弹幕挑选、`/status`、TTS、Qwen 流式**全部无有效调用点或不生效**，README 一律打勾 | ❌ **反面教材** | `bot.py:501`、`bot.py:298`、`abs_app.py:15`、`tts/app.py` |

---

## 6. 真正能借的四样东西

**（一）事件驱动的离散材料入口。**
只借形状：**一个 `emit` 入口、事件类型集中登记、监听器按类型订阅**。
`[推断]` 对 palimpsest 的用处是：**"材料到达"与"归档处理"天然解耦**——
设计 §6.4 要求归档周期性发生、不逐条触发，事件表里加一个周期事件即可，
不需要改采集侧。

**（二）模型关在 HTTP 后面，且一后端一进程一 venv。**
客户端侧：`pipeline/` 的每条管线都是"配置对象 ⇒ `predict()`"。
服务端侧：一个模型一个进程、一个 venv，彼此只走 pydantic 契约（§2.2 / §4.5）`[源码]`。
⇒ palimpsest 第一步需要一个"机械层"（设计 §1.1），**这一层可以直接用这个形状**：
标注、裁决、编码器各自是一条管线、各自一个 venv（它们的 torch 版本必然打架），
主框架里没有权重、没有 CUDA。
⚠️ **但借的是形状，不是那层壳**：服务壳要自己写（见 §7 第 6 条）。

**（三）契约独立成仓、请求与响应同构。**
`LLMQuery` 带 `history` 进、`LLMPrediction` 带 `history`（含本轮）出 `[源码]`。
⇒ `[推断]` 这正好对上设计 §7.6 的"逐轮记录"：
**如果一次读取的输入与输出都走同一个信封，"这一轮"就是一条可以落盘的记录。**
⚠️ 但要按 §4.2 补上时间、位置、来源三个字段——**照抄字段集是不够的**；
并按 §4.5 那条给契约**建版本台账**（它自己有 15 个模块钉了两个版本）。
★ **还有一条不能照抄**：它的 `id` 唯一性是假的（§4.2 末行）。
⇒ 对 palimpsest 的直接后果是：**逐轮记录若要靠一个 id 串起来，那个 id 必须先验"每份都不一样"。**

**（四）"声明式护栏"的写法。**
`Timer` 超时只记日志、不取消，且文件头写明它不负责什么 `[源码]`。
⇒ 与 palimpsest 的"护栏必须可能触发"配套：**一个量、一个阈值、一句"响了代表什么、它不负责什么"。**

**（五）配置即 schema（本节唯一一条"照抄前要先修"的）。**
从 pydantic 反射出带注释的 yaml 配置，再从同一个 schema 生成配置界面 `[源码]`
（`common/generator/config_gen.py:26-42`、`gradio_gen.py:63-131`）——
**单一真源同时喂文件与界面**，对长跑系统很值。
⚠️ 但它自己踩了两个坑，抄的时候要一起修：**配置项被代码忽略**（§4.5 表末两条），
以及**热重载是空操作**（`bot.py:408-410` 的 handler 只调了一次 `get_config()` 又丢掉结果，
而 `get_config` 命中缓存直接返回）`[源码]`。
⇒ ★ 通用形式：**"配置里有这一项"与"这一项真的在起作用"要分开验**——
**这跟 §3.10 的"能力写了但没接线"是同一个形状，只是发生在配置层。**

---

## 7. 明确借不了的地方（不许糊过去）

1. **它的"记忆"整个不能借。** 不是质量不够，是**路线相反**：
   摘要即记忆本体（§3.3）。palimpsest 的第 1 条根是"原文是根"，
   而这里**原文从未被保存**——记进去的是派生物，派生物丢了就再也没有了。
   ⇒ 若把 `summary_history` + Milvus 那一套搬过来，会得到一个**看起来能跑、但永远回不到原文**的系统。
   **这类搬入不会报错**，这正是 `.local/准入记录.md` 警告的形状。`[推断]`

2. **它的"满了清空"不能借。** 设计 §4.3 的界线是"可以漏、不可以丢"；
   这里是全丢。而且丢的时候没有任何信号。`[推断]`
   ⇒ 更重一层：**写回历史前还对文本做了就地修改**（`bot.py:473-474`），
   ⇒ 这直接违反"原文逐字保存"：**存下来的东西已经不是原来那个了。**`[源码]`

3. **它的关键字路由不能借。** `if "记得" in text` / `if "打开浏览器" in text` 是
   "把判断写成规则"的教科书样本，与设计 §11.1 的总原则直接冲突。`[源码]`

4. **它的 UI 前端不能当"记录层"用。** `add_history` 只推给前端显示
   （`bridge.py:172-173`），前端 `ChatHistoryController` 只 `Instantiate` 聊天气泡
   （`ChatHistoryController.cs:32-59`）`[源码]`：
   **没有任何一份落到磁盘的对话记录，也没有时间戳。**
   ⇒ 事故复盘时，**这台机器上没有"当时到底说了什么"的凭据**。
   而设计 §2.1 说原文"决定其余部分的上限"——**这里上限是零**。`[推断]`

5. **Playground 的工程门槛不能忽略。** README 明说需要手动导入 Cubism SDK、Vuforia、
   DoTween、TriLib 等**需同意许可证、不在仓库里的**依赖，且 Unity 从 2021 升到 6000
   （`README.md:10-38`）`[官方文档]`。
   ⇒ `[推断]` **首次可运行的成本很高，且链条上有多个闭源/许可关键点。**
   要拿它当展示端，先算这一步的工。

6. ★ **它的服务壳一层都不能用，要自己写。** 这一条是 §4.5 的结论，单列出来免得被"骨架可借"盖过去：

   | 现成的东西 | 为什么不能用 |
   |---|---|
   | web 框架 | Flask + **werkzeug 开发服务器**；**无鉴权、无 CORS、无并发控制、无超时** |
   | 健康检查 | `/status` **不可达**（被每个子类丢掉），且 `self.status` 从不更新 ⇒ 可达也永远返回 `stopped` |
   | 文件处理 | **客户端可以在请求体里指定服务端任意存在的路径**（`asr/app.py:29-33` 等 5 处）⇒ `[推断]` 任意文件读取；上传的临时文件**永不删除** |
   | 可复算 | **无 seed、无模型指纹、采样参数硬编码在各 `model.py` 里且不记录** ⇒ 同一输入不可复算 |
   | 溯源 | loguru **无 sink**（只有 stderr）、**无请求 id 关联**；`LLMQuery.id` 有字段但没人用 |
   | 测试 | zerolan-core **零测试** |
   | 排除故障的基本面 | `live2d` 的 14 处 HTTP 调用**全无 timeout**（§2.5 首行）；主循环**没有出口**；线程靠**不安全强杀**结束 |

   ⇒ `[推断]` **"薄"不等于"可运维"。** 这套东西能演示，不能长跑；
   而 palimpsest 要的东西恰恰是**长跑 + 可复算 + 有记录**。
   ⇒ ★ 因此借骨架时的正确姿势是：**借接口形状（抽象、配置对象、三方法接缝），
   服务壳从零写**——它那 2124 行里没有一行是可运维的。

---

## 8. 测试：两个仓库加起来，测的是"线通不通"，不是"机制对不对"

**先分清两个仓库，这一步很容易混。** `[源码]`

| 仓库 | 测试情况 |
|---|---|
| **ZerolanLiveRobot** | **68 个 `test_*` / `atest_*` 函数**，分 `common/ devices/ pipeline/ services/`。但**形状是集成测试**：多数直接构造真实管线去打模型服务——`tests/pipeline/test_llm.py:58-100`（`test_llm` / `test_kimi_api` / `test_deepseek_api`）、`tests/pipeline/test_vec_db.py`、`tests/test_live_stream.py:30-42`（`test_bilibili` / `test_twitch` / `test_youtube`）。**没有服务就全部失败。** 另有几条**导入即失败**（`tests/test_qq.py:6` 从 `services.qqbot.bridge` 导入一个不存在的模块；`tests/test_vec_db.py:1` 从 `config` 导入 `get_config`，而根 `config.py` 没有这个名字）`[源码]`；`tests/test_agent.py` 全部只 `print`、**零断言** `[源码]` |
| **zerolan-core** | ★ **一个测试都没有。** `tests/` 里**只有 4 个二进制素材**（`imgcap-test.png` / `ocr-test.png` / `tts-test.wav` / `vidcap-test.mp4`），**没有一个 `test_*.py`** `[源码]` |

**值得记的一个做法**（对应 `.local/环境与接入记录.md` M8"让系统可以离线整套跑"）`[源码]`：
`tests/pipeline/test_abs.py:10-17` 起了个 **本地替身服务** `TestServer`（`127.0.0.1:5889`），
在同一个文件里先把服务拉起来再把管线打过去——**不花钱、不联网、不需要模型权重**，
测的是"HTTP 契约与解析对不对"。⇒ 这是全套测试里**唯一不依赖活体模型**的那种。

**⇒ 但测试覆盖不到本文 §3 与 §4.5 的任何一个结论** `[查不到]`：
`LLMPromptManager.reset_history`（"满了清空"）、`exp_memory`（门控）、
`save_memory`（死代码）、`/status`（死代码）、`TTSApplication` 的构造签名
（一调就 `TypeError`）**都没有任何测试**。

⇒ `[推断]` 这解释了为什么那些问题能一直在：
**测试测的是"每条线通不通"，而"机制对不对"没人测。**
这与 `.local/环境与接入记录.md` M1（先证明实现是对的，再说没效果）是同一类缺口。
⇒ ★ 而且**"一调就崩"的那两条本身就是最强的证据**：
`TTSApplication(model=..., host=..., port=...)` 只要被调用一次就会 `TypeError`，
所以"TTS 已集成"这句话意味着**那条路径从来没有被跑过一次**。`[推断]`

### 8.1 要不要去上游问作者（本条是决定记录，不是发现）

写到这一节时有一个自然的想法：**这些与记忆有关的问题，直接去问作者**。
结论是：**问，但只问设计取舍，不报"你路线错了"。理由与渠道如下。**
（记在这里是因为下一个读这份文档的人一定会想到同一件事。）

**支持去问的事实**（`2026-10-09` 查）`[源码]`：

| 事实 | 值 | 含义 |
|---|---|---|
| GitHub 作者最近活动 | `2026-10-02`（star 了几个 agent 项目） | **人还在，而且方向正在往 agent 走** |
| 作者自己最近 push | `2026-09-11`（另一个仓库） | 在做别的项目，但仍是活跃维护者 |
| Zerolan 四仓最近 push | `2026-04-14` | **本套已停约半年**，issue 大概率不会被修 |
| ZerolanLiveRobot 关注度 | **807 star / 73 fork** | 不是无人问津的小仓库 |
| 开着的 issue | LiveRobot 4 个、core 1 个 | **没有任何一条与记忆有关** `[查不到]` |
| Discussions | **未开启** | ⇒ 只能走 issue，没有更轻的渠道 |

**⇒ 值得问的是"设计取舍"，不是"bug 清单"。** 三条他从代码里答不出、而本项目最想知道的：

1. **"值不值得记住"最后由什么决定？** 源码里 `exp_memory` 是一个线性加权阈值
   （`bot.py:438-458`），而它内部又调两次 LLM 打分。
   ⇒ 问：**这是没调完的实验，还是"最终还是得写死一个公式"的结论？**
2. **`save_memory` 为什么没接上？** 是没来得及，还是试过之后发现"摘要记忆"不好用？
   ⇒ 这一条的答案直接决定本项目要不要重走一遍这条路。★ **这是最想问的一条。**
3. **原文有没有在别处留过？** 如果有（比如直播录像、弹幕存档），
   那"回不到原文"就只是本仓库里没做，而不是路线错。

**⇒ 不要去问的形状**（会得到零信息或敌意）：

- ⛔ 不要发"README 与代码不符"的清单（§3.10 那张表）。
  那是**本项目的核验习惯**，对作者本人没有价值，只会像挑错。
  ⇒ 要问就把它压缩成一句"我读到的和文档不太一样，想确认一下哪个是当前状态"。
- ⛔ 不要发"`if "记得" in text` 是错的"这类判断。
  **那是设计立场之争**（机械 vs 模型），作者按自己的设计做完了，不会被一篇外部文档说服。
- ⚠️ 不要贴本项目的设计全文。问的是**他的经验**，不是推销方案。

**⇒ 状态：尚未发出。** 本文只记"值不值得问、问什么、怎么问"；
**发不发、以谁的账号发，是用户的决定**（`[待用户裁定]`）。
发出去之后无论回不回，**都应回来在这里补一条结果**——
"问过、没回"和"没问"是不同的信息，前者能省下一次重复尝试。

> ### 上面这条的后续（2026-10-09 补，**正文按当时的标准保留**）
>
> **用户裁定的结果：发。** 本条记的是发出去之后的事实，不改上面的推理。
>
> | 项 | 结果 |
> |---|---|
> | **落在哪** | **`ZerolanLiveRobot`** 的 issue [#11](https://github.com/AkagawaTsurunaki/ZerolanLiveRobot/issues/11)（不是 zerolan-core——`save_memory` 只存在于前者） |
> | 标题 | `关于 save_memory 与 history_collection 的几个疑问` |
> | 发起人 / 时间 | `AzureHalcyon` / `2026-10-09` |
> | **实际问的是哪一条** | **上表第 2 条**（`save_memory` 为什么没接上）+ 三个具体的岔路（摘要丢细节 / `limit=2` 拼 prompt 带偏生成 / `output_fields` 里的 `history` 字段压根没写入）。**第 1、3 条没问**——一次只问一件事 |
> | **去掉的** | 正文里**没有任何加粗**、没有 `-` 列表（三个岔路改成空行分隔的段落）、没有省略号（用户逐次要求），也不含 §3.10 那张对照表（按上面那条"不要去问的形状"） |
> | 标签 | **没打上**，见下 |
> | 回复 | **截至本条写下时无回复**（发起约 1 小时内查的；此后未再跟踪） |
> | 2026-10-09 三次修订 | 末段改了三轮，**最终定稿**为：「我自己也在做长期陪伴的对话记忆系统，不过我是打算试试另一条路：保留历史记录本身，对文本标注多种关系网络，用的时候通过激活部分网络读取信息。你的实践对我来说有很多参考意义，所以挺想知道你后来为什么没接着做下去。」<br>⇒ 走法：**由用户自己定措辞，我只做标点与结构的最小修正**。中途我拟过三版（"关系图/激活浮现"等），都被用户否掉——**用户的措辞比我的准**（"激活部分网络"正对上设计 §4.4"任何一次激活都只能取到图的一部分"）。<br>⇒ 每次改完都回读 API 逐字比对（最后一次 `identical = True`，且加粗/列表/省略号/旧措辞四项残留检查全空） |
> | ★ 这条的写法教训 | **这是本项目第一次对外发言，"怎么说"和"说什么"同样重要。** 记两条：① **不要在提问里陈述自己的方案**——第一版末段"……和'存摘要'正好是反的"是在划清界限，读起来像宣讲；② **省略号不能当占位符发出去**——我误把用户的 `……` 当成定稿推了上去，实际那是"这里我还没想好，你接着写"。⇒ 对外文本在发出前**必须逐字得到确认**，不能把"结构上说得通"当成"用户已认可" |
>
> **⇒ 权限规律（三条实测，以后不用重试）：**
>
> | 操作 | 结果 |
> |---|---|
> | 编辑**自己发的** issue 正文（`gh issue edit --body-file`） | ✅ 成功且**真的生效** |
> | 给 issue 加标签（`gh issue edit --add-label`） | ❌ `AddLabelsToLabelable` 权限不足 |
> | 创建时带标签（`gh issue create --label`） | ⚠️ `exit=0`，**静默丢弃**，无警告 |
>
> ⇒ 规律：**发起人对自己的 issue 有编辑权，但没有仓库级 triage 权。**
>
> **⇒ 两个可复算的失败经验（都是当场踩的，都值得留给下一个人）：**
>
> 1. ★ **只有 `pull` 权限的账号，GitHub 网页端不渲染标签选择器。**
>    实测 `viewer_permission = {"admin":false,"maintain":false,"pull":true,"push":false,"triage":false}`
>    ⇒ 由此可以解释**为什么这个仓库 5 条 issue 一条标签都没有**：
>    **不是作者不用标签，是外部贡献者根本点不到。** `[实测]`
> 2. ★★ **`gh issue create --label X` 在无权限时"静默成功"**：
>    返回码 `0`、正常输出 issue URL，**但标签没打上，也不报任何警告**；
>    事后 `gh issue edit --add-label` 才明确报
>    `does not have the correct permissions to execute 'AddLabelsToLabelable'`。
>    ⇒ **教训与本项目已有的一条同形**：`.local/环境与接入记录.md` K6「引用要验解析得到」——
>    **凡是"设置某项"的操作，都要回头读一次确认它真的生效了**；
>    返回码 0 只说明请求发出去了。`[实测]`
>
> **⇒ 发出去之后的实际收益（与 §8.1 正文的预期对照）：**
> 正文说"他大概率不会回，但记录本身有价值"。**这一点在发出后立刻成立**：
> 这条 issue 现在是一条**公开可检索的、指向 `history_collection` 写入路径缺失的记录**，
> 而 §3.2 那条结论此前只存在于本仓库的这份调研里。
> ⇒ 即使永远没有回复，**"问过"这个状态已经确定**，不必再试第二次。

---

## 9. 复算入口

**先从 §9.1 克隆，再按 §9.2 复算。** 本节不写任何本机绝对路径。

### 9.1 取到这四个仓库

假定当前目录是这四个克隆的**公共父目录**（本文用 `Zerolan/` 指它，
名字随你起；四个仓库都克隆到它下面，下文的 `Z`/`C`/`D` 前缀就以它为基准）：

```shell
git clone --depth 30 -c http.sslBackend=openssl https://github.com/AkagawaTsurunaki/ZerolanLiveRobot.git
git clone --depth 30 -c http.sslBackend=openssl https://github.com/AkagawaTsurunaki/ZerolanPlayground.git
git clone --depth 30 -c http.sslBackend=openssl https://github.com/AkagawaTsurunaki/zerolan-core.git
git clone --depth 30 -c http.sslBackend=openssl https://github.com/AkagawaTsurunaki/zerolan-data.git
```

⚠️ **本机实测的一处坑**：正常 `git clone` 会撞
`schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`
（沙箱里 git 拿不到沙箱外目录的 TLS 凭证句柄）。
`-c http.sslBackend=openssl` 一次即通，不必提权。
（这与 `.local/环境与接入记录.md` E1「遇到访问问题先提权」并存：
那条说的是**正常环境**，这条是**沙箱内**的另一条路。）

**⇒ 由此得到本文的目录约定**，下文一律用它，不用绝对路径：

| 前缀 | 指 |
|---|---|
| `Z` | `Zerolan/ZerolanLiveRobot` —— 主仓库 |
| `P` | `Zerolan/ZerolanPlayground` —— AR 前端 |
| `C` | `Zerolan/zerolan-core` —— AI 核心 |
| `D` | `Zerolan/zerolan-data` —— 数据结构定义 |

### 9.2 关键断言的复算命令

在标出的那个仓库根目录下执行。

| 断言 | 复算命令 | 期望 |
|---|---|---|
| `Z` `save_memory` 没有调用点 | `rg -n "save_memory" .` | 只有 `bot.py:501` 的定义行 |
| `Z` 长期记忆只有读、触发是关键字 | `rg -n '"记得"' .` | 只有 `bot.py:255` |
| `Z` 短期记忆超限即清空 | `rg -n "injected_history" manager/llm_prompt_manager.py` | `:14` 初始化、`:26` 与 `:31` 两处重置 |
| `Z` 全仓无对话落盘 | `rg -n "open\(.*['\"]a\|json\.dump\|sqlite\|pickle\|shelve" .` | 只命中 `agent/tool_agent.py`（拼 prompt）与测试/服务，**无一条是对话记录** |
| `Z` 无日志 sink | `rg -n "logger\.add" .` | **0 命中** |
| `Z` 临时文件永不删除 | `rg -n "os\.remove\|\.unlink\(\|shutil\.rmtree" .` | 非测试代码 **0 命中** |
| `Z` HTTP 调用全无 timeout | `rg -n "requests\.(post\|get)\(" pipeline agent services` | 14 处，**无一带 `timeout=`** |
| `Z` 主循环没有出口 | `rg -n "_timer_flag" bot.py` | 只有 `:47` 赋 `True` 与 `:113` 判断 |
| `Z` `Conversation` 无时间戳 | `rg -n "metadata=" .` | 全部传 `None`（`agent/adaptor.py`） |
| `Z` `once` 不生效 | 读 `event/event_emitter.py:227-229` | `if listener.once:` 分支里只有 `remove`，`_add_task` 无条件执行 |
| `Z` 写回历史前文本被改过 | 读 `bot.py:473-474,483` | 删开头换行 → 再 `reset_history` |
| `Z` 配置热重载是空操作 | 读 `bot.py:408-410` | handler 只调 `get_config()` 并丢弃结果 |
| `Z`↔`C` 默认端口不一致 | `rg -n "11000\|11002" pipeline/llm/config.py` 对比 `C` 的 `config.template.yaml` | `Z` 默认 11000，`C` 默认 11002 |
| `C` 零测试 | `rg --files tests` | 只有 4 个二进制素材，**无 `test_*.py`** |
| `C` `/status` 是死代码 | 各 `app.py` 的 `self._app = Flask` 与 `rg -n "self\.status" .` | 7 个子类各建一个新 Flask 丢掉基类注册；`self.status` **只在 `abs_app.py:13` 赋值一次** |
| `C` TTS 构造即崩 | `rg -n "def __init__" --glob "*/app.py"` | **8 个 `app.py` 里只有 `tts/app.py` 没有**，而 `starter.py:154-155` 传了 `host=`/`port=` |
| `C` Qwen 流式是空生成器 | 读 `llm/qwen/model.py:97-99` | 循环里没有 `yield` |
| `C` 契约零重定义 | `rg -n "^from zerolan\|^import zerolan" .` | 25 条 import / 20 个文件；`.py` 里的 `class` 无一是数据模型 |
| `C` 契约版本一分为二 | `rg -n "zerolan-data" --glob "uv.lock"` | 14 个模块 `rev=1.5.0`，只有 `defense/deberta` 是 `rev=1.6.0` |
| `C` 同目录 manifest 互相矛盾 | `rg -n "torch\|transformers" vid_cap/hitea/pyproject.toml vid_cap/hitea/requirements.txt` | `>=2.9.1`/`>=4.57.5` vs `==2.4.0`/`==4.43.3` |
| `D` `id` 唯一性是假的 | 读 `src/zerolan/data/pipeline/abs_data.py:15,27` | `Field(default=str(uuid4()))` 在类体求值，非 `default_factory` |
| `P` 前端只画不存 | 读 `Assets/Scripts/Controller/UI/ChatHistoryController.cs:32-59` | 只 `Instantiate` 聊天气泡 |

**不可复算的部分**：§5 表格里所有"❌ 不能借"的判定是**我的判断**（`[推断]`），
不是跑出来的读数——它们是"与设计条文的比对结果"，比对本身可以复核，判定不能复算。
另有两处**本次未坐实**（本机未装 flask / milvus_model，也不打算为读一份调研装它们）：

1. `Flask.run(host, port, False)` 第三个位置参数的语义与 `threaded` 默认值
   ⇒ 影响 `asr/paraformer/model.py:36,86` 那个**跨请求共享、无锁、无 session key 的 `self._cache`**
   到底有多严重；
2. `milvus_model.DefaultEmbeddingFunction()` 的实际输出维度
   ⇒ 影响 `database/milvus/milvus.py:20` 写死 `dimension=768` 是否真的会维度不匹配。

⇒ 两条都标 `[推断]`，**不当结论用**。

---

## 10. 一句话结论

**借它的骨架（事件循环、管线抽象、数据契约的形状、一后端一 venv），不借它的记忆，也不借它的运维。**

它的记忆路线是"**摘要即记忆**"：把对话压成一段话存起来，按相似度捞回来拼进 prompt。
palimpsest 的路线是"**原文是根**"：逐字保存，派生物可重算、不落库。
**这两条路在第一步的代码上长得几乎一样，但走三年之后是两个完全不同的东西。**

⇒ 因此本文最有用的可能不是"它能借什么"，而是**两张对照表**：

- **§3.3**：摘要即记忆 —— 一份"**照抄会走错**"的实物证据，而且它的错不会报错。
- **§3.10 / §4.5**：能力写了但没接线 / 接了但被覆盖 ——
  一份"**凭什么**"该怎么问的实物证据：
  **"已实现"不是依据，"有一条能到达且到达后真的动"的路径才是。**
