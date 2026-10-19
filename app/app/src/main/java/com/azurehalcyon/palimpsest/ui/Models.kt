package com.azurehalcyon.palimpsest.ui

import org.json.JSONObject

// =========================================================================== 对话
// ★ 2026-10-17：这一节前面原来是**池子**那一段（`PoolView` / `LayoutPoint` / `AxisRay` /
//   `Layout` / `PoolPage` / `Delta` / `PoolStats` / `LongEntry` / `TempEntry`，以及文件下半截
//   的 `parsePoolView` / `parseDelta` / `PoolGroup` / `parseLayout` / `parsePoolPage`）—— 全删了。
//   它们服务的是"四档池子 + 空间图"那一屏（`PoolScreen.kt` / `SpaceMap.kt`），
//   而池子在**新线**里不存在了 ⇒ 记忆页换成 `GraphView` / `件View`（文件末尾那一节）。
//
//   一轮 = 一条用户消息 + 它的回复。
/**
 * 一轮 = 一条用户消息 + 它的回复。
 *
 * ★ **以"轮"为单位组织列表**（照 zafiro 的 `HomeChatTurn`）：连续的用户消息才会
 *   成为一组、气泡连体、只有组尾带尾巴。本工程一轮就是一问一答，所以大多数轮
 *   是 `Single`；保留整组语义是为了连发两句时也对。
 */
class ChatTurn(
    /** 本轮的 id；恢复历史时用序号，避免与运行时生成的 id 撞上。 */
    val id: Long,
    val userText: String,
    /** ★ 本轮想起的那几条。
     *  这是**给用户看的交互信息**，与"模型看到什么"无关。
     *  界面：每轮一个默认折叠的块，摆在回复**上方**（类似 CoT 的位置），不点开不显示。 */
    val push: List<String> = emptyList(),
    /** ★★ 这一轮**注入的那段回忆**（2026-10-17 新线）：`{编写, 没有, 用了[], 版本, 耗时ms}`。
     *  外层只显示 `编写` 那一段；点「怎么编的」才展开"用了哪几条 + 编写版本 + 耗时"。
     *  ⛔ 与 `push` 不是一回事：`push` 是条目清单（旧线），`recall` 是编写出的一段（新线）。 */
    val recall: org.json.JSONObject? = null,
) {
    /** 回复只可能有一条（一问一答）；`Asking` 时为空 = 还没答。 */
    var reply: String? = null

    /**
     * ★ 这一轮模型的**推理**（CoT，2026-10-07）。
     *
     * 用户口径：「把推理展示出来」。生成时是**边收边显示**，跑完之后留在这里
     * ⇒ 事后也翻得到（⛔ 只是显示：不进池子、不进上下文、不进归档）。
     * ⚠️ `ChatTurn` 不是可观察对象 ⇒ 赋值之后要靠外部状态翻转触发重画
     *   （`askNow` 里紧接着翻 `chatPhase`，那一下就会重画）。
     */
    var reasoning: String? = null
    var replyRetrieval: Retrieval? = null
    var failed: Boolean = false

    /** 模型自己"再查一次记忆"的调用 —— 界面按**工具调用**的样子展示（默认折叠）。 */
    var pulls: List<Pull> = emptyList()

    /** 这一轮花的 token（服务端给的；缺就是空 map，界面就不显示那一行）。 */
    var usage: Map<String, Int> = emptyMap()

    /** 系统提示行（开场/收场那一句），不参与气泡。 */
    var notice: String? = null

    val hasContent: Boolean get() = reply != null || failed

    /** 折叠块的标题。**想起几条是每条消息各不相同的**，所以每轮都要显示它。 */
    val pushSummary: String get() = "想起 ${push.size} 条"
}

/** 模型自己调 `read_z` 再查一次记忆：查了什么、拿回了哪几条。 */
data class Pull(
    val query: String,
    val got: List<String>,
    val 回: String = "",
    /** `回忆` / `事件` —— 工具行按它换图标与标题。 */
    val kind: String = "回忆",
)

/** 一轮的"想起"侧信息。 */
class Retrieval(
    val push: List<String>,
)

/** 从 `chat_turns_json` 恢复出来的一轮。 */
data class RestoredTurn(
    val user: String,
    val ai: String,
    val push: List<String>,
    val pulls: List<Pull> = emptyList(),
    val usage: Map<String, Int> = emptyMap(),
    /** ★ 这一轮的 CoT（核心侧 `Session.ask` 记的，随 `pending.json` / 转写留档）。 */
    val reasoning: String = "",
    /** ★★ 这一轮注入的**那段回忆**（新线）：`{编写, 没有, 用了[], 版本, 耗时ms}`。 */
    val recall: org.json.JSONObject? = null,
)

sealed interface ChatPhase {
    /** 还没开场 / 刚收场。 */
    data object Idle : ChatPhase

    data object Opening : ChatPhase

    /** ★ 等模型出话。 */
    data object Asking : ChatPhase

    /** ★ 收场整理：几十秒的活儿，界面不能卡。 */
    data object Archiving : ChatPhase
}

// =========================================================================== 设置
data class Knob(
    val path: String,
    /** 人话名字（`settings.py` 里那个 label）。 */
    val label: String,
    /** 内部点分路径 —— **不上界面**，只在调试时有用。 */
    val rawPath: String,
    /** float / int / bool / str */
    val kind: String,
    /** 【实测】/【推导】/【假设】/【护栏】 */
    val grade: String,
    val note: String,
    val unit: String,
    val low: Double?,
    val high: Double?,
    val choices: List<String>,
    val value: String,
    /** 与默认值不同（= 他在设置里动过这一项）。 */
    val changed: Boolean,
)

data class CredsState(
    val configured: Boolean = false,
    val err: String = "",
    val path: String = "",
    val embedBaseUrl: String = "",
    val chatUrl: String = "",
    /** ★ 只给"有没有"，**密钥本身绝不回显到界面**。 */
    val hasEmbedKey: Boolean = false,
    val hasChatKey: Boolean = false,
    /** 密钥的开头结尾（`sk-1…abcd`）。⛔ 这里**没有**整把密钥，界面也拿不到。 */
    val embedKeyHint: String = "",
    val chatKeyHint: String = "",
)

/** 依据等级 → 徽标配色（页面里再用主题色画出来）。 */
internal fun gradeRank(grade: String): Int = when {
    grade.contains("实测") -> 0
    grade.contains("护栏") -> 1
    grade.contains("推导") -> 2
    grade.contains("假设") -> 3
    else -> 4
}

internal fun parseKnobs(raw: String): Pair<List<Knob>, String> {    val o = parseObj(raw) ?: return emptyList<Knob>() to "参数表读不出来（返回的不是合法 JSON）"
    if (!o.bool("ok")) {
        return emptyList<Knob>() to o.str("err").ifBlank { "参数表读不出来" }
    }
    val list = o.arr("knobs")?.let { a ->
        (0 until a.length()).mapNotNull { i ->
            val e = a.optJSONObject(i) ?: return@mapNotNull null
            Knob(
                path = e.str("label", e.str("path")),
                rawPath = e.str("path"),
                label = e.str("label", e.str("path")),
                kind = e.str("kind", "str"),
                grade = e.str("grade"),
                note = e.str("note"),
                unit = e.str("unit"),
                low = if (e.isNull("low")) null else e.dbl("low"),
                high = if (e.isNull("high")) null else e.dbl("high"),
                choices = e.strList("choices"),
                value = scalarToString(e.opt("value")),
                changed = e.bool("changed"),
            )
        }
    }.orEmpty()
    return list to ""
}

internal fun parseCreds(raw: String): CredsState {
    val o = parseObj(raw) ?: return CredsState(err = "凭证状态读不出来")
    if (!o.bool("ok")) return CredsState(err = o.str("err").ifBlank { "凭证状态读不出来" })
    val configured = o.bool("configured")
    return CredsState(
        configured = configured,
        err = if (configured) "" else o.str("err"),
        path = o.str("path"),
        embedBaseUrl = o.str("embed_base_url"),
        chatUrl = o.str("chat_url"),
        hasEmbedKey = o.bool("has_embed_key"),
        hasChatKey = o.bool("has_chat_key"),
        embedKeyHint = o.str("embed_key_hint"),
        chatKeyHint = o.str("chat_key_hint"),
    )
}

// ---------------------------------------------------------------- 件浏览（记忆页的新形状）

/** 一个件 = 一组 cell；`强度` 来自标记集。★ 新线里"层级/四档"都不存在了。 */
data class 件View(
    val id: String,
    val 强度: Double,
    val 在标记集: Boolean,
    val 成员: List<件成员View> = emptyList(),
)

data class 件成员View(val 场id: String, val 轮: Int, val 文本: String, val 点亮: Boolean)

/**
 * 一个**组**（依赖簇）：一场里相邻、互相依赖的那几轮**算出来**的一段（`graph.组`）。
 *
 * ★ 它和 [件View] 不是一回事：件是**跨场**的落点（一批 cell 归在一起、带强度），
 *   组是**按场**算出来的缓存（轮区间 + 成员格）。看组是为了检验"归档把它分对了吗"。
 */
data class 组View(
    val id: String,
    val 场id: String,
    val 轮起: Int,
    val 轮止: Int,
    val 来源: String,
    val 场们: List<String> = emptyList(),
    val 成员: List<组成员View> = emptyList(),
)

data class 组成员View(val 场id: String, val 轮: Int, val 文本: String)

/**
 * 一格（或一件）的**势** —— 读取路径的 EMA（`势.json`，⛔ 不在图里）。
 * ★ 它只**引导传播**（乘在 α/β/γ 上），不是准入；也不是"存不存"（那是标记集的事）。
 */
data class 势View(
    val 单元: String,
    val 标签: String,
    val 文本: String,
    val 势: Double,
    val 唤醒: Double,
)

/** 记忆页的整份数据（`graph_view_json`）。 */
data class GraphView(
    val ok: Boolean,
    val err: String = "",
    val 版本: Int = 0,
    val cell数: Int = 0,
    val 件数: Int = 0,
    val 组数: Int = 0,
    val 标记集数: Int = 0,
    val 势数: Int = 0,
    val 待观察数: Int = 0,
    val 件们: List<件View> = emptyList(),
    val 组们: List<组View> = emptyList(),
    val 势们: List<势View> = emptyList(),
    val 待观察池: List<待观察View> = emptyList(),
)

data class 待观察View(val 单元: String, val 为什么: String)

// ---------------------------------------------------------------- 参数账 / 提示词 / 模型（2026-10-18）

/**
 * 参数账里的一项。
 *
 * ★ `值` 一律按字符串交给界面：账上的类型有 bool / int / float / str 四种，
 *   但界面要的是「一个框、随便填、我来校验」（作者：「不要加号减号，直接一个框」）。
 */
data class 账项(
    val 键: String,
    val 组: String,
    /** 界面上那一组的名字（「标记规则」「记忆与衰减」…），⛔ 不用文档里的组键。 */
    val 组名: String,
    val 名: String,
    val 值: String,
    val 类型: String,
    val 依据: String,
    val 说明: String,
    val 无读者: String,
    /** false = 不该给人改（版本号 / 没有读者的旋钮）⇒ 界面不列出来。 */
    val 可改: Boolean = true,
    /** 能填的值（非空 ⇒ 界面上直接给选项，⛔ 不让人敲错）。 */
    val 可填: List<String> = emptyList(),
    /** 怎么填的一句话（⛔ 不是文档正文）。 */
    val 提示: String = "",
) {
    /** 人话的当前值：bool 说 开/关，其余照原样。 */
    val 显示值: String get() = if (类型 == "bool") (if (值 == "true") "开" else "关") else 值
}

/** 一行改动记录。 */
data class 账改动(val 时间: String, val 键: String, val 旧: String, val 新: String)

/** 提示词清单里的一项（正文不在这里，单独取 —— 正文很长）。 */
data class 提示词项(
    val id: String,
    val 名称: String,
    val 占位符: List<String>,
    val 说明: String,
    val 已改: Boolean,
    val 字数: Int,
)

data class 提示词组(val 组: String, val 项: List<提示词项>)

/** 一项提示词的编辑页数据：生效正文 + 默认正文（用来「恢复默认」前对照）。 */
data class 提示词屏(
    val id: String,
    val 名称: String,
    val 占位符: List<String>,
    val 说明: String,
    val 已改: Boolean,
    val 正文: String,
    val 默认: String,
)

/** 端点报回来的一个可用模型。 */
data class 模型项(val id: String, val own: String)

/** 旧场（已归档）的一条。 */
data class PastSession(val 场id: String, val 轮数: Int, val ts: Double)

/** 旧场里的一次工具调用（回忆）。 */
data class PastCall(val 名: String, val 参数: String)

/** 旧场里按时间顺序的一段：思考 / 回忆（含带回来的那段）/ 正文。 */
data class PastSeg(val 类: String, val 文本: String, val 回: String = "")

/** 旧场里的一轮（只读）。 */
data class PastTurn(
    val 轮: Int,
    val user: String,
    val ai: String,
    val 段: List<PastSeg>,
    val 撤回: Boolean,
)

/** 归档流程里的一条判 / 一次自标。 */
data class PastJudge(
    val 轮: Int,
    val 来源: String,
    val 归进: String,
    val 新建: Boolean,
    val 说法: String,
    val 记: List<String>,
    val err: String,
)

/** 这一场归档流程的全部落盘记录。 */
data class PastArchive(val 判: List<PastJudge>, val 层1: List<String>)

/** 旧场全貌（只读）。 */
data class PastDetail(
    val 场id: String,
    val 系统前缀: List<String>,
    val 常驻: String,
    val 在场: String,
    val 轮: List<PastTurn>,
    val 指纹: String,
    val 归档: PastArchive,
)

/** 「查一查」里的一格（只读探针）。 */
data class ProbeCell(val 场: String, val 轮: Int, val 分: Double, val 组: String)

/** 「查一查」里的一个件：被取用了没有。 */
data class ProbePiece(val 件id: String, val 最高: Double, val 段数: Int, val 取用: Boolean)

/** 「查一查」的结果：全量激活 + 取用/未取用。 */
data class ProbeResult(
    val 线索: String,
    val 激活数: Int,
    val 格: List<ProbeCell>,
    val 件: List<ProbePiece>,
)

