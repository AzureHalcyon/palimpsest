package com.azurehalcyon.palimpsest.ui

import org.json.JSONObject

// =========================================================================== 池子
/** 池子屏的状态。`ok=false` 时只有 [err] 有意义（"长期池缓存不存在"要给人话，不是异常）。 */
data class PoolView(
    val ok: Boolean,
    val err: String = "",
    val clock: Double = 0.0,
    val sessions: Int = 0,
    /** ⚠️ 条目**不在这里**了 —— 走 [PoolPage] 分页取（池子会越来越大）。 */
    val long: List<LongEntry> = emptyList(),
    val temp: List<TempEntry> = emptyList(),
    val stats: PoolStats = PoolStats(),
    /** 上一次归档动了什么；从没归档过就是空。 */
    val delta: Delta? = null,
    /** 分布图；算不出来就是 null 或 `ok=false`。 */
    val layout: Layout? = null,
)

/** 分布图上的一个点。 */
data class LayoutPoint(
    val i: Int,
    val layer: String,
    val temp: Boolean,
    val text: String,
    val x: Double,
    val y: Double,
    val z: Double,
    /** 精确半径 = 与底色方向的余弦距离（**真测量**）。 */
    val r: Double,
    /** 图上的半径（投影后，有约 33% 幅度误差；排序可靠）。 */
    val r3: Double,
    val hits: Int,
    /** 什么时候进来的（引擎钟，天）；种子条目是 null。 */
    val t: Double? = null,
    // ---- 定轴重心图（2026-10-05）才有 ----
    /** 它最靠哪根轴 / 第二靠哪根。 */
    val axis: String = "",
    val axis2: String = "",
    /** 靠这根轴的哪一端（`+` / `-`）。 */
    val pole: String = "",
    /** top-1 在两根轴里的占比（0.5 = 两根打平 ⇒ 成分混杂）。 */
    val w1: Double = 1.0,
    val mix: Double = 0.0,
    /**
     * ★ **落点对齐度** = 合成方向 与 它 top-1 射线 的余弦。
     *
     * 为什么要有这个数：上一版把 36 根轴的坐标**全加**起来，实测中位只有 **0.217**，
     * 而把坐标打乱的对照是 **0.239** —— 与乱猜无异。这一版只用 top-2，实测中位 **0.797**。
     * ⇒ 凡这条 < 0.5 的，图上的位置基本不可信（界面上要能看出来）。
     */
    val align: Double = 1.0,
)

/**
 * ★ 骨架里的一根轴（**约定几何**，与数据无关）。
 *
 * 为什么方向是约定而不是从数据算：**空轴没有条目 ⇒ 没有任何数据能定出它的方向**，
 * 而用户要"空轴本身要画出图" ⇒ 方向只能来自约定；语义只通过"落点"进来。
 */
data class AxisRay(
    val id: String,
    val name: String,
    val x: Double,
    val y: Double,
    val z: Double,
    /** 这根轴在池子里**被用到多少**（拿该轴锚自己的量级当尺子，现算）。 */
    val use: Double = 0.0,
    /** 整体偏哪一端（带符号；0 = 没立场）。 */
    val stance: Double = 0.0,
    /** 有多少条目把它当 top-1 / top-2。 */
    val nTop1: Int = 0,
    val nTop2: Int = 0,
    /**
     * ⚠️ **手标的「空」**（内容层判断），**不是算出来的**。
     * 实测：本语料上 36 根的 use 全在 0.11–0.18 ⇒ **数据判不出"这一维没人"**。
     * ⇒ 空轴照画，只是线画淡、并标明"当前语料未表态"。
     */
    val empty: Boolean = false,
)

/** 分布图 + 它自己的质量数字（这些数要画在图上，否则图会骗人）。 */
data class Layout(
    val ok: Boolean,
    val why: String = "",
    val points: List<LayoutPoint> = emptyList(),
    val nTotal: Int = 0,
    val nDrawn: Int = 0,
    val nCenter: Int = 0,
    /** 前三个方向各占多少。 */
    val share: List<Double> = emptyList(),
    /** 3D 一共画出多少 —— 图上必须写，剩下的在视野外。 */
    val cum3: Double = 0.0,
    /** 有效维度；幂迭代路径拿不到完整谱 ⇒ null（宁可不报）。 */
    val effDim: Double? = null,
    /**
     * **同样条数、同样维度的随机向量**的有效维度 —— "扁不扁"的对照。
     *
     * ★ 没有它 `有效维度 4.8` 就是个没法读的数：4.8 算高算低取决于最多能有多少。
     *   ⛔ 不能拿"条数"当对照 —— 那只在条数远小于维度时成立。
     */
    val effDimRandom: Double? = null,
    /**
     * **同样条数、同样维度的随机向量**的前三个方向占比 —— 同一件事的另一种说法。
     *
     * ★ 它和 [effDimRandom] 的区别是**两条路都给得起**（占比是精确的，有效维度不是）：
     *   实测 400 条的池子走幂迭代 ⇒ `effDim` 是 null ⇒ "扁不扁"那句话在界面上
     *   **整句消失**过。所以这句话必须由这个数兜底。
     */
    val share3Random: Double? = null,
    /** 投影半径与真半径的相关（实测 0.98）。 */
    val radiusCorr: Double = 0.0,
    /** 底色平均半径（画参考壳用）。 */
    val baseR: Double = 0.0,
    val maxR: Double = 1.0,
    /**
     * 这张图**这次只能画成这样**的说明（例：临时池的向量还没落盘，短期记忆不在图里）。
     *
     * ★ 不报的话，界面上就是"短期记忆一条都没画" —— 看起来像坏了，其实是缓存旧。
     *   宁可说一句，也不让人对着半张图猜。
     */
    val note: String = "",
    // ---- 定轴重心图（2026-10-05）才有 ----
    /** `"frame"` = 定轴重心图；空 = 旧画法。 */
    val mode: String = "",
    /** ★ 骨架：36 根**双极轴**的约定方向（含空轴 —— 空轴也画）。 */
    val axes: List<AxisRay> = emptyList(),
    val nAxes: Int = 0,
    /** 锚的选择性中位（>1 = 有分辨力；天然对照 <1）。 */
    val axSelAnchor: Double = 0.0,
    /** ★ 落点对齐度中位。上一版 0.217（与乱猜无异）⇒ 这一版 0.797。 */
    val axAlignMed: Double = 0.0,
    /** 对齐度 < 0.5 的占比（图上位置不可信的那部分）。 */
    val axAlignLo: Double = 0.0,
    /** 手标的空轴清单。 */
    val quietAxes: List<String> = emptyList(),
)

/** 一组的一页条目。`group` 用的是**代码里的层键**（底色/情境/记忆/临时）。 */
data class PoolPage(
    val ok: Boolean,
    val err: String = "",
    val group: String = "",
    val total: Int = 0,
    val offset: Int = 0,
    val hasMore: Boolean = false,
    val nextOffset: Int = 0,
    val long: List<LongEntry> = emptyList(),
    val temp: List<TempEntry> = emptyList(),
)

/** 上一次归档（"睡醒了"）带来的变化。 */
data class Delta(
    val layers: Map<String, DeltaCount> = emptyMap(),
    val temp: DeltaCount? = null,
    /** 临时池里这次被"升迁"（不再衰减）的条数 —— 它不是减少，条目还留着。 */
    val tempPromote: Int = 0,
    /**
     * ★★ **按档位拆开的"减了几条"**（2026-10-06）。
     *
     * 为什么要它：`temp.del` 是**整个临时池**的减量，印到"短期记忆"那一行就会自相矛盾
     * （实测：一条「还没说完的」（悬着的事）办完被删，却在短期那行显示 −1）。
     * 键 = 档位名（`短期` / `中期` / `悬着`）。
     */
    val tempDelByTier: Map<String, Int> = emptyMap(),
    /** ★ 同样是**按档**：这次被 `done` 从"中期"退回短期的条数。 */
    val tempDemoteByTier: Map<String, Int> = emptyMap(),
) {
    fun layer(key: String): DeltaCount = layers[key] ?: DeltaCount(0, 0)

    /** 某一档**减少了**几条（缺省 0）。 */
    fun tempDel(tier: String): Int = tempDelByTier[tier] ?: 0

    /** 某一档**被退回**了几条（缺省 0）。 */
    fun tempDemote(tier: String): Int = tempDemoteByTier[tier] ?: 0
}

data class DeltaCount(val add: Int = 0, val del: Int = 0) {
    val quiet: Boolean get() = add == 0 && del == 0
}

data class PoolStats(
    val longTotal: Int = 0,
    val longByLayer: Map<String, Int> = emptyMap(),
    val tempTotal: Int = 0,
    val tempAlive: Int = 0,
    val tempPromoted: Int = 0,
    /** ★ 「悬着的事」的条数 —— **不按时间退**那一类（与 `tempPromoted` 互斥）。 */
    val tempHanging: Int = 0,
    /**
     * ★★ **各档"本场收进来几条"**（2026-10-06，按每条临时条目的 `from_session` 现算）。
     * 用它们、别用 `delta.temp.add`：那个是整个临时池的增减，印到某一档上会自相矛盾
     * （实测用户看到「短期 0，上次 +1」——那一条其实落进了「悬着的事」）。
     */
    val addedShort: Int = 0,
    val addedMid: Int = 0,
    val addedHang: Int = 0,
    /** ★ "正在淡出"（`w < eps`、还没被归档清掉）—— 要**单独标出来，别藏掉**。 */
    val tempFading: Int = 0,
    val eps: Double = 0.14,
)

data class LongEntry(val idx: Int, val layer: String, val text: String)

data class TempEntry(
    val idx: Int,
    val text: String,
    val tag: String,
    val ageDays: Double,
    val tau: Double,
    val w: Double,
    val alive: Boolean,
    val hits: Int,
    val long: Boolean,
    /** ★ 「悬着的事」—— **不按时间退**，只由 `done` 结束（用户 2026-10-05 裁）。 */
    val hanging: Boolean = false,
    val how: String,
    val fromSession: Int,
)

// =========================================================================== 对话
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

internal fun parsePoolView(raw: String): PoolView {
    val o = parseObj(raw)
    if (o == null) return PoolView(ok = false, err = "池子数据读不出来（返回的不是合法 JSON）")
    if (!o.bool("ok")) {
        return PoolView(ok = false, err = o.str("err").ifBlank { "池子读不出来" })
    }
    val long = o.arr("long")?.let { a ->
        (0 until a.length()).mapNotNull { i ->
            val e = a.optJSONObject(i) ?: return@mapNotNull null
            LongEntry(e.int("idx"), e.str("layer", "?"), e.str("text"))
        }
    }.orEmpty()
    val temp = o.arr("temp")?.let { a ->
        (0 until a.length()).mapNotNull { i -> a.optJSONObject(i)?.let { tempEntryOf(it) } }
    }.orEmpty()
    val st = o.sub("stats")
    val byLayer = mutableMapOf<String, Int>()
    st?.sub("long_by_layer")?.let { m ->
        m.keys().forEach { k -> byLayer[k] = m.optInt(k, 0) }
    }
    return PoolView(
        ok = true,
        clock = o.dbl("clock"),
        sessions = o.int("sessions"),
        long = long,
        temp = temp,
        delta = parseDelta(o.sub("delta")),
        layout = parseLayout(o.sub("layout")),
        stats = PoolStats(
            longTotal = st?.int("long_total") ?: long.size,
            longByLayer = byLayer,
            tempTotal = st?.int("temp_total") ?: temp.size,
            tempAlive = st?.int("temp_alive") ?: temp.count { it.alive },
            tempPromoted = st?.int("temp_promoted") ?: temp.count { it.long },
            tempHanging = st?.int("temp_hanging") ?: temp.count { it.hanging },
            addedShort = st?.int("added_short") ?: 0,
            addedMid = st?.int("added_mid") ?: 0,
            addedHang = st?.int("added_hang") ?: 0,
            tempFading = st?.int("temp_fading") ?: temp.count { !it.alive },
            eps = st?.dbl("eps") ?: 0.14,
        ),
    )
}

/**
 * 解析「上次归档后的变化」。
 *
 * ★ 解析不出来就返回 **null**（老缓存里根本没这个字段，或者还没归档过）
 *   ⇒ 界面上那一行**整句不显示**，而不是显示一句"上次 +0 −0"的假话。
 *   这与"没有就不显示"是同一条规矩：宁可不说，不说假话。
 */
internal fun parseDelta(o: JSONObject?): Delta? {
    if (o == null) return null
    val layers = mutableMapOf<String, DeltaCount>()
    o.sub("layers")?.let { m ->
        m.keys().forEach { k ->
            val e = m.optJSONObject(k) ?: return@forEach
            layers[k] = DeltaCount(e.int("add"), e.int("del"))
        }
    }
    val t = o.sub("temp")
    val temp = t?.let { DeltaCount(it.int("add"), it.int("del")) }
    if (layers.isEmpty() && temp == null) return null
    // ★ 按档拆开的两个数（2026-10-06）：老缓存里没有 ⇒ 空 map（各档就显示 0，不撒谎）
    fun byTier(key: String): Map<String, Int> {
        val m = t?.sub(key) ?: return emptyMap()
        val out = mutableMapOf<String, Int>()
        m.keys().forEach { k -> out[k] = m.optInt(k) }
        return out
    }
    return Delta(
        layers = layers,
        temp = temp,
        tempPromote = t?.int("promote") ?: 0,
        tempDelByTier = byTier("del_by_tier"),
        tempDemoteByTier = byTier("demote_by_tier"),
    )
}

/** 一组当前已取到哪儿了（懒加载的状态）。 */
data class PoolGroup(
    val loading: Boolean = false,
    val loaded: Boolean = false,
    val err: String = "",
    val total: Int = 0,
    val nextOffset: Int = 0,
    val hasMore: Boolean = false,
    val long: List<LongEntry> = emptyList(),
    val temp: List<TempEntry> = emptyList(),
)

/** 一条临时池条目。抽出来是因为分页那条路也要用同一份解析（免得两处漂移）。 */
private fun tempEntryOf(e: JSONObject): TempEntry = TempEntry(
    idx = e.int("idx"),
    text = e.str("text"),
    tag = e.str("tag"),
    ageDays = e.dbl("age_days"),
    tau = e.dbl("tau"),
    w = e.dbl("w"),
    alive = e.bool("alive", true),
    hits = e.int("hits"),
    long = e.bool("long"),
    // ★ 2026-10-05：「悬着的事」—— 不按时间退，只由 `done` 结束。缺字段 ⇒ false（老缓存）。
    hanging = e.bool("hanging"),
    how = e.str("how"),
    fromSession = e.int("from_session", -1),
)

internal fun parseLayout(o: JSONObject?): Layout? {
    if (o == null) return null
    val pts = o.arr("points")?.let { a ->
        (0 until a.length()).mapNotNull { i ->
            val e = a.optJSONObject(i) ?: return@mapNotNull null
            LayoutPoint(
                i = e.int("i"), layer = e.str("layer", "?"), temp = e.bool("temp"),
                text = e.str("text"),
                x = e.dbl("x"), y = e.dbl("y"), z = e.dbl("z"),
                r = e.dbl("r"), r3 = e.dbl("r3"), hits = e.int("hits"),
                // 种子条目没有时间 ⇒ 显式 null（"随时间"那张图要据此排除它并说明）
                t = if (e.isNull("t")) null else e.dbl("t"),
                axis = e.str("axis"), axis2 = e.str("axis2"), pole = e.str("pole"),
                w1 = e.dbl("w1", 1.0), mix = e.dbl("mix"),
                align = e.dbl("align", 1.0),
            )
        }
    }.orEmpty()
    val share = o.arr("share")?.let { a ->
        (0 until a.length()).map { a.optDouble(it, 0.0) }
    }.orEmpty()
    val rays = o.arr("axes")?.let { a ->
        (0 until a.length()).mapNotNull { i ->
            val e = a.optJSONObject(i) ?: return@mapNotNull null
            AxisRay(
                id = e.str("id"), name = e.str("name"),
                x = e.dbl("x"), y = e.dbl("y"), z = e.dbl("z"),
                use = e.dbl("use"), stance = e.dbl("stance"),
                nTop1 = e.int("n_top1"), nTop2 = e.int("n_top2"),
                empty = e.bool("empty"),
            )
        }
    }.orEmpty()
    return Layout(
        ok = o.bool("ok"), why = o.str("why"), points = pts,
        nTotal = o.int("n_total"), nDrawn = o.int("n_drawn"), nCenter = o.int("n_center"),
        share = share, cum3 = o.dbl("cum3"),
        // ⛔ 幂迭代路径拿不到完整谱 ⇒ 它给的是 null，界面就不显示这个数（不显示假话）
        effDim = if (o.isNull("eff_dim")) null else o.dbl("eff_dim"),
        effDimRandom = if (o.isNull("eff_dim_random")) null else o.dbl("eff_dim_random"),
        share3Random = if (o.isNull("share3_random")) null else o.dbl("share3_random"),
        radiusCorr = o.dbl("radius_corr"), baseR = o.dbl("base_r"),
        maxR = if (o.dbl("max_r") > 0.0) o.dbl("max_r") else 1.0,
        note = o.str("note"),
        mode = o.str("mode"),
        axes = rays, nAxes = o.int("n_axes"),
        axSelAnchor = o.dbl("ax_sel_anchor"),
        axAlignMed = o.dbl("ax_align_med"), axAlignLo = o.dbl("ax_align_lo"),
        quietAxes = o.strList("quiet_axes"),
    )
}

/**
 * ★ **临时池那几档的界面组键** —— 它们都回**临时条目**（`temp`），而不是长期条目。
 *
 * ⚠️ 这些**不是 `LAYERS` 里的层**（底色/情境/记忆/临时那四个键没动）——
 * 它们是临时池**内部**的档位名，由 `host_glue.pool_entries_json` 按 `tier` / `hanging` 分开。
 *
 * ⛔ **一处漏写就会静默出错**（组头有数、组体空白，因为条目被当成长期条目解析进了 `long`）
 * —— 2026-10-05 加 `悬着` 时就真漏过一次。**加新档位只改这里**。
 */
private val TEMP_GROUP_KEYS = setOf("临时", "中期", "悬着")

internal fun parsePoolPage(raw: String): PoolPage {
    val o = parseObj(raw) ?: return PoolPage(ok = false, err = "读不出来（不是合法 JSON）")
    if (!o.bool("ok")) {
        return PoolPage(ok = false, err = o.str("err").ifBlank { "取不到这一组" })
    }
    val g = o.str("group")
    val a = o.arr("items")
    if (g in TEMP_GROUP_KEYS) {
        val t = a?.let { arr ->
            (0 until arr.length()).mapNotNull { i ->
                arr.optJSONObject(i)?.let { tempEntryOf(it) }
            }
        }.orEmpty()
        return PoolPage(ok = true, group = g, total = o.int("total"),
                        offset = o.int("offset"), hasMore = o.bool("has_more"),
                        nextOffset = o.int("next_offset"), temp = t)
    }
    val l = a?.let { arr ->
        (0 until arr.length()).mapNotNull { i ->
            val e = arr.optJSONObject(i) ?: return@mapNotNull null
            LongEntry(e.int("idx"), e.str("layer", g), e.str("text"))
        }
    }.orEmpty()
    return PoolPage(ok = true, group = g, total = o.int("total"),
                    offset = o.int("offset"), hasMore = o.bool("has_more"),
                    nextOffset = o.int("next_offset"), long = l)
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
