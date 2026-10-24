# Neuro-sama、小模型与"活人感"笔记

状态：调研笔记，未验证；2026-10-24。

> 2026-10-24 本机实测：见 [社区权重与工具能力-笔记.md](社区权重与工具能力-笔记.md)。
> Gemma2 权重不可用；Fimbulvetr 能说话但模板泄漏；现代 9B 的工具/动作结论见该文。

## 0 要回答的问题

- 小模型（8–11B）能不能做出 Neuro-sama 那种"活人感"？
- 有哪些开源权重可以直接试？
- 社区关于她的说法，哪些有证据，哪些是推测？

## 1 证据状态

| 说法 | 证据 | 判断 |
|---|---|---|
| Vedal 只确认"她用 LLM"，拒绝谈调参内情 | 一手采访（`Neuro-sama-架构调研.md`） | 可信 |
| "8–10B + LoRA + 单张 4090" | 社区转述；kimjammer 实为 8B / 4070 12GB / 无 LoRA | 混淆，不能当事实 |
| "4090 是后来升级的" | NGA #12，二手 | 至少说明 4090 不是最初配置 |
| "Neuro 是 text completion algorithm，Vedal 说的" | NGA #8，二手转述 | 有出处但未一手核实；与"她是 LLM"不矛盾 |
| 训练租多卡 H100 | 项目调研，二手 | 训练与推理要分开看 |
| 延迟优化了几个月 | NGA #10，二手 | 关键工程难点在延迟，不在模型大小 |
| "多年积累的数据" | NGA #26，推测 | 与"数据 + 微调"路线一致，但无一手 |
| 记忆可换、会坏、靠人补 | `Neuro-sama-反面核查.md`，二手 | 不能反推"她的记忆机制值得复刻" |

NGA 帖（tid=42426462）本身是 20 来条社区讨论，没有 Vedal 的技术说明；它有价值的地方只有三处：
"text completion"的转述、4090 是后来升级的、以及延迟是真正花时间的工程。其余多是猜测。

## 2 "活人感"拆四层

| 层 | 是什么 | 谁给 |
|---|---|---|
| 语气 | 短句、态度、口头禅、不客服腔 | 权重 / 微调 |
| 反应 | 接住上一句、抬杠、跑题、答错当梗 | 数据 + 采样参数 |
| 连续 | 记得刚才说过什么、会 callback、情绪延续 | 循环 / 记忆 |
| 主动 | 先开口、暂停、拒绝、换话题 | 动作空间（说 / 挂起 / 等） |

结论：开源权重能给出前两层；后两层是《持续在场》要做的。小模型能做"语气"，
能不能"活"取决于循环，而不取决于把 9B 换成 14B。

## 3 开源权重

| 模型 | 基座 / 数据 | 量化 | 许可 | 备注 |
|---|---|---|---|---|
| `neifuisan/Gemma2-9b-Neuro-sama` | Gemma2-9B + `neifuisan/Neuro-sama-QnA` | Q4_K_M 约 5.8 GB | Apache-2.0（微调卡） | 作者自述做了两天，"still far from a real Neuro" |
| `neifuisan/Fimbulvetr-11B-v2-Neuro-sama` | Fimbulvetr-11B-v2 | Q4_K_M 6.6 / Q5_K_M 7.7 / Q8 11.5 GB | Apache-2.0（微调卡） | RP 向基座，注意输出是否带角色扮演腔 |

两者都是 QnA / chat 微调，不是纯补全；18 GB 显存跑 Q5、甚至 Q8 都够。模型卡里的例子显示
"活人感"主要来自态度与跑题（"That's not a duck. That is my creator Vedal."），不是知识。

## 4 第一版实验

- 三组：Qwen3-8B-Instruct（客服基线）、Neuro-9B、Neuro-11B；
- 同一段 20–30 轮转录，格式用 `[T]` 推演 / `[S]` 说；
- 指标：平均句长、客服腔比例（"as an AI / how can I help"）、对前文的 callback 次数、
  重复率、首 token 延迟；
- 人读：像人还是像客服（项目规矩：量具只报警，人读裁决）；
- 通过后再接挂起与 K/V 年龄剪枝，看"连续"能不能把语气撑成一个人。

## 5 要提前知道的坑

- 作者自己说"far from a real Neuro"；例子里的机智是**段子级**的，不是稳定人格。
- QnA 微调对长转录可能不在分布内；先短对话，再拉长到 8k 以上。
- 微调卡写 Apache-2.0，但基座（Gemma / Fimbulvetr 的合并来源）各有条款，训练数据来自直播；
  自用没问题，分发前看一遍。
- 文本-only 与语音的"活人感"不是一回事；语音那一层（TTS / 口型）可以后接，不影响文本实验。

## 6 待办与未定

- 用 9B / 11B 各跑 20 轮，记录客服腔比例与 callback 次数；
- 测长转录（> 8k）是否退化；
- 测 `[T]/[S]` 格式是否被这两个权重接受；
- 若语气够、格式不对，做格式 LoRA（不改身份，只改转录格式）；
- 是否要拿 `neifuisan/Neuro-sama-QnA` 自己再训一版，等上述结果再说。

## 7 来源

- NGA《Neuro是如何实现的》（tid=42426462），本地存档；社区推测为主。要点：#8 text completion、
  #10 延迟优化、#12 4090 升级、#26 数据积累。
- 模型卡：<https://huggingface.co/neifuisan/Gemma2-9b-Neuro-sama>、
  <https://huggingface.co/neifuisan/Fimbulvetr-11B-v2-Neuro-sama>、
  <https://huggingface.co/mradermacher/Fimbulvetr-11B-v2-Neuro-sama-GGUF>；
  数据集 <https://huggingface.co/datasets/neifuisan/Neuro-sama-QnA>。
- kimjammer/Neuro：<https://github.com/kimjammer/Neuro>。
- 项目内：[`Neuro-sama-架构调研.md`](Neuro-sama-架构调研.md)、
  [`Neuro-sama-反面核查.md`](Neuro-sama-反面核查.md)、
  [`参考对象与方法核查-调研.md`](参考对象与方法核查-调研.md)、
  [`../持续在场.md`](../持续在场.md)。
