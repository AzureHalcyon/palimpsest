# 语料

第三方语料的**本地副本**。★ **仓里只留这份清单，数据不进版本控制**
（同 [`../研究/参考论文/`](../研究/参考论文/) 的做法：第三方材料不放仓库）。

**用途**：早期给标注试跑当测试材料（那套标注层已归档，见 `99_归档/标注提示词-2026-10-16/`）；
现在主要是取材与对照的原料。选择依据见 [`../语料评估.md`](../语料评估.md)。

**取源日期**：2026-10-08。

---

## 清单

| 目录 | 是什么 | 许可 | 规模 | 取法 |
|---|---|---|---|---|
| `KdConv/` | 中文多域知识驱动对话（ACL 2020） | **Apache-2.0** | **46.6 MB**：`data/{film,music,travel}/{train,dev,test}.json` + `kb_*.json` | `git clone https://github.com/thu-coai/KdConv` 后只留 `data/` |
| `HCoT/` | 中文心理支持多轮对话（Helping Skills CoT） | **CC BY 4.0**（论文补充材料） | **104.1 MB**：`All_merged_data.json` | ModelScope `dlq1998/HCoT-Corpus` 的 `All_merged_data.json` |
| `DuLeMon/` | 长期个性化记忆对话（Findings of ACL 2022，百度） | 仓库 **Apache-2.0**；数据包条款未探 | **11.4 MB**：`DuLeMon.zip` | 直链 `https://dialogue.bj.bcebos.com/Knover/DuLeMon.zip` |
| `Douban/` | 豆瓣多轮对话 + 人工判断（Douban Conversation Corpus） | ⚠️ **无许可证** | **700 MB**：`DoubanConversaionCorpus/{train,dev,test}.txt`；另有 `train.sample`（60 KB 小样） | Dropbox 直链（`dl=1`）→ `MarkWuNLP/MultiTurnResponseSelection` 的 README 里指向它 |
| `chatterbot-中文/` | 中文闲聊短对话（chatterbot-corpus 中文部分） | **BSD-3-Clause** | **37 KB**：17 个 `.yml` | `git clone https://github.com/gunthercox/chatterbot-corpus` 后取 `chatterbot_corpus/data/chinese` |
| `_说明/` | 多来源整合项目的说明与许可（它的 8 个来源里，豆瓣那份 = 上面的 `Douban/`） | 见该目录内 `*-LICENSE` | 16 KB | `github.com/codemayq/chinese-chatbot-corpus` |
| `LongMemEval/` | ★ 长期记忆**评测基准**（ICLR 2025），**是尺子不是材料** | **MIT** | **279 MB**：`longmemeval_oracle.json`（14.7 MB）· `longmemeval_s_cleaned.json`（264.5 MB） | ModelScope 镜像 `evalscope/longmemeval-cleaned`（★ 要**带命名空间**） |

**合计约 1.1 GB**（`Douban/train.txt` 一份就占 659 MB）。

★ **`LongMemEval/` 的用法与别份不同**：它标出"哪一轮含答案"（`has_answer`），
实测**只占 0.38%**（约 1/260）——拿它核我们的标注**取舍得够不够紧**，
而不是拿它当标注材料（它是英文的，答案也是短语）。

## 拿它们试什么

| 要测的东西 | 用哪份 |
|---|---|
| **该不该标这一段**（取舍） | ★★★ `LongMemEval/`（轮级 `has_answer`：**0.38% 的极稀疏真值**） |
| **组与组之间的关系**（跨话题） | ★★ `KdConv/`（平均 19 轮，且每条发言挂着知识三元组） |
| **一轮在干什么**（作用） | `HCoT/`（策略标注：`[Restatement]`/`[Interpretation]`/`[Direct Guidance]`…） |
| **会不会把没说的当说过** | `DuLeMon/`（`user_no_said_persona` 是"没说过"的那一格） |
| **知识更新**（旧的失效、新的成立） | ★★ `LongMemEval/`（`knowledge-update` 78 道） |
| **该拒答**（没有就不许说） | ★★ `LongMemEval/`（`abstention` 约 30 道） |
| **同一情境下哪个取用更好**（对照） | `Douban/`（有候选回复 + 三标注者的人工判断） |
| 短对话上的下限 | `chatterbot-中文/`（很短，主要用来看"材料太短会怎样"） |

## 规矩

1. **数据不进版本控制**（本目录除这份 README 外都被 `.gitignore` 排除）；
2. ⛔ **不把语料内容抄进仓库**——要引用就指"读它的地方"，不复制原文；
3. ⚠️ **许可从干净到严**：`KdConv`（Apache-2.0）> `chatterbot-中文`（BSD-3）>
   `HCoT`（CC BY 4.0）> `DuLeMon`（仓库 Apache-2.0，包未探）>
   ⚠️ `Douban`（无许可）⇒ **无许可的那份只做本地测试，不再分发**；
4. ★ **用之前重新看一眼许可**（这条是给未来的自己）；探源时是什么样，不代表现在还是。
