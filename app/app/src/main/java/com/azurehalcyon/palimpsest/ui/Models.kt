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
data class Pull(val query: String, val got: List<String>)

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

/** 记忆页的整份数据（`graph_view_json`）。 */
data class GraphView(
    val ok: Boolean,
    val err: String = "",
    val 版本: Int = 0,
    val cell数: Int = 0,
    val 件数: Int = 0,
    val 标记集数: Int = 0,
    val 待观察数: Int = 0,
    val 件们: List<件View> = emptyList(),
    val 待观察池: List<待观察View> = emptyList(),
)

data class 待观察View(val 单元: String, val 为什么: String)
