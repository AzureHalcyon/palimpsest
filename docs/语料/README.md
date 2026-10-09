# 语料

第三方语料的**本地副本**。★ **仓里只留这份清单，数据不进版本控制**
（同 [`../研究/参考论文/`](../研究/参考论文/) 的做法：第三方材料不放仓库）。

**用途**：给 [`../标注提示词.md`](../标注提示词.md) 当测试材料。
选择依据见 [`../语料评估.md`](../语料评估.md)。

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

**合计约 845 MB**（`Douban/train.txt` 一份就占 659 MB）。

## 拿它们试什么

| 要测的东西 | 用哪份 |
|---|---|
| **该不该标这一段**（取舍）· **组与组之间的关系**（跨话题） | ★★ `KdConv/`（平均 19 轮，且每条发言挂着知识三元组 = 现成判据） |
| **一轮在干什么**（作用） | `HCoT/`（策略标注：`[Restatement]`/`[Interpretation]`/`[Direct Guidance]`…） |
| **会不会把没说的当说过** | `DuLeMon/`（`user_no_said_persona` 是"没说过"的那一格） |
| **同一情境下哪个取用更好**（对照） | `Douban/`（有候选回复 + 三标注者的人工判断） |
| 短对话上的下限 | `chatterbot-中文/`（很短，主要用来看"材料太短会怎样"） |

## 规矩

1. **数据不进版本控制**（本目录除这份 README 外都被 `.gitignore` 排除）；
2. ⛔ **不把语料内容抄进仓库**——要引用就指"读它的地方"，不复制原文；
3. ⚠️ **许可从干净到严**：`KdConv`（Apache-2.0）> `chatterbot-中文`（BSD-3）>
   `HCoT`（CC BY 4.0）> `DuLeMon`（仓库 Apache-2.0，包未探）>
   ⚠️ `Douban`（无许可）⇒ **无许可的那份只做本地测试，不再分发**；
4. ★ **用之前重新看一眼许可**（这条是给未来的自己）；探源时是什么样，不代表现在还是。
