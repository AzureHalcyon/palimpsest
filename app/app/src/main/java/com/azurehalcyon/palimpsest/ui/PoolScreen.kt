package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlin.math.roundToInt
import com.niki914.uikit.infra.liquidScreenBottomPadding

/**
 * 屏 2：池子。
 *
 * 排版原则：**不套卡片**。层次靠字号 / 字重 / 间距 / 颜色分 —— 一屏要多看得见几条。
 * 文字原则：不解释机制（用户是这套系统的作者），只给数。
 *
 * 三条必须一眼看见的标记：
 *  - `alive:false`（正在淡出）**单独标出来，不藏**：临时池**归档时才结算**，
 *    两次归档之间本来就躺着一些 `w < eps` 的条目，藏掉会让人以为规则和实际不一致；
 *  - `long:true`（已升长期）标出来，并写明路径（规则 `hits>=2` / 模型 `promote`）；
 *  - 存活度条：`w` 的填充 + `eps` 处的红刻度。
 */
@Composable
fun PoolScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val v = vm.pool

    // ★ 提示**自己走**（与对话页那套一样）：看一眼就够，不占屏幕。
    //   ⛔ 错误（`poolErr`）不在这里 —— 它得摆着等人处理。
    LaunchedEffect(vm.poolNotice) {
        if (vm.poolNotice.isNotBlank()) {
            delay(5000)
            vm.dismissPoolNotice()
        }
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            // ⛔ 这里**不要**再吃状态栏 inset。
            //   工具栏已经吃过一次（`FlatShell` 里工具栏的 `statusBarsPadding()`），
            //   内容靠 Column 布局流天然排在他下面。
            //   ★ 实测踩过（用户一眼就看出来了）：多吃一次会在工具栏与正文之间
            //     多出一条 **44.6dp ≈ 状态栏高度** 的纯黑横带 —— 量出来是
            //     分隔线在 96.7dp、而 ScrollView 从 142.3dp 才开始。
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp)
            // 底栏现在**占布局空间**（不再浮在内容上），所以底部只需一点呼吸位，
            // 不再需要 `liquidScreenBottomPadding()` 那套（它现在是 0）。
            .padding(top = 6.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        if (vm.poolErr.isNotBlank()) NoticeCard(vm.poolErr, NoticeTone.Warn)
        if (vm.poolNotice.isNotBlank()) NoticeCard(vm.poolNotice, NoticeTone.Info)

        when {
            v == null -> Unit
            v.ok -> {
                Overview(v)
                // ★ 空间图排在总览下面、三层记忆上面。
                //   三层记忆内部的次序是用户定的（短期 → 长期 → 自我），这里不动；
                //   但这张图不是"一层"，它是**整池的一张速写**，所以占总览那个位置。
                //   默认展开，但可以收起 —— 日常只想看短期记忆的人点一下就把它折掉。
                SpaceSection(v)
                // ★ 排布顺序（用户定的）：短期 → 中期 → 长期记忆 → 情境 → 底色。
                //   理由是"最常看/最易变"的放上面：临时池是这两天的近况、天天在变；
                //   底色是最稳的那一层，几乎不用看。按重要性递增往下沉。
                // ★ 2026-10-05：临时池拆成两档（用户："单开一组"）。
                //   ⚠️ **不能用 `tempAlive`** —— 它是"w ≥ eps"（还活着的），**两种档位都算**；
                //     组头要的是"这一组里有几条"（**含正在淡出的**，因为组里一个都不藏）。
                //     ⇒ 短期 = `tempTotal − tempPromoted`（与 Python 那边 `tier != 中期` 等价）。
                //   ⚠️ 归档的 `delta` 只按**整个临时池**记，还没按档位拆 ⇒
                //     短期那行显示 add/del，另外两行只显示"这场升进来几条"。
                // ★ 三类**互斥且覆盖全**（与 `host_glue.pool_entries_json` 同一套判据）：
                //   短期 = total − 中期 − 悬着。
                TempSection(vm, v, key = "临时", label = "短期记忆",
                            count = (v.stats.tempTotal - v.stats.tempPromoted
                                     - v.stats.tempHanging).coerceAtLeast(0),
                            // ★★ 各档显示**自己**的增减（2026-10-06）：
                            //   以前这里用的是 `delta.temp`（**整个临时池**的增/减）⇒
                            //   实测出现过「短期 0，上次 +1」——那一条其实落进了「悬着的事」。
                            //   · 加几条：按 `from_session` 现算（`added_*`）
                            //   · 减几条：`del_by_tier`（含"悬着办完被删"）
                            //   · 短期还会**收到**从中期退回来的（`demote_by_tier["中期"]`）
                            d = DeltaCount(
                                add = v.stats.addedShort + (v.delta?.tempDemote("中期") ?: 0),
                                del = v.delta?.tempDel("临时") ?: 0,
                            ),
                            promote = 0,
                            empty = "短期记忆是空的。")
                TempSection(vm, v, key = "中期", label = "中期记忆",
                            count = v.stats.tempPromoted,
                            d = DeltaCount(
                                add = v.stats.addedMid,
                                // 两条路都算"离开中期"：衰减到底被删 + 被 `done` 退回短期
                                del = (v.delta?.tempDel("中期") ?: 0)
                                    + (v.delta?.tempDemote("中期") ?: 0),
                            ),
                            promote = v.delta?.tempPromote ?: 0,
                            empty = "中期记忆是空的。")
                // ★ 「悬着的事」：不按时间退，**只由归档时的 `done` 结束**。
                TempSection(vm, v, key = "悬着", label = "悬着的事",
                            count = v.stats.tempHanging,
                            d = DeltaCount(
                                add = v.stats.addedHang,
                                del = v.delta?.tempDel("悬着") ?: 0,
                            ),
                            promote = 0,
                            empty = "没有悬着的事。")
                LongSection(vm, v)
            }

            // ★ 池子不存在时给一句人话，不是把异常糊在屏幕上。
            else -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = v.err.ifBlank { "记忆缓存不存在" },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = "手机上还没有记忆。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }

    // ★★ 长按那一行 ⇒ 弹出的改 / 删弹窗（**与对话页同一个** `MemoryEditDialog`）。
    //   ⛔ `fromPool = true` 由各行的长按处给（提示落这一屏、改完重读池子）。
    MemoryEditDialog(vm)
}

// --------------------------------------------------------------------------- 总览
@Composable
private fun Overview(v: PoolView) {
    val s = v.stats
    // ★ 只留**一行**，而且**不在这里分长期/短期**。
    //   原先写的是「长期记忆 14 · 短期记忆 12」—— 可下面三个分组是
    //   **事实 6 / 情境 2 / 自我 6**，读者看到"长期记忆 14"却找不到 14 在哪：
    //   它其实等于那三组之和。同一个词在一屏里指两件事（既是"三组的总和"，
    //   又是"短期记忆"的对家）⇒ 用户的原话是「**长期记忆指代不明**」。
    //   现在层级写出来：总览只说"一共多少"，"长期记忆"降级成下面三组的一个**组头**。
    Text(
        text = buildString {
            append("一共 ${s.longTotal + s.tempTotal} 条")
            if (s.tempFading > 0) append("（快淡忘 ${s.tempFading}）")
            append(" · 已聊过 ${v.sessions} 场")
        },
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

/**
 * 不做收纳的**组头**（"长期记忆 14"）。
 *
 * ★ 它存在的意义就是消歧：下面那三组（事实/情境/自我）加起来的和 = 这个数。
 *   只写"长期记忆"不写数，读者还是对不上；只写数不给组头，就还是不知道 14 是谁。
 */
@Composable
private fun SectionLabel(text: String, count: Int) {
    Text(
        text = "$text　$count",
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.85f),
        modifier = Modifier.padding(top = 6.dp, bottom = 2.dp),
    )
}

// --------------------------------------------------------------------------- 长期池
// ★ 顺序（用户定的）：长期记忆 → 情境 → 底色。底色在最下面。
private val LAYER_ORDER = listOf("记忆", "情境", "底色")

/**
 * 层名 → **界面显示名**（用户定的：记忆 · 长期/短期 · 自我 · 事实 · 短期记忆）。
 *
 * ★ 显示名与**代码键**是分开的两件事：界面用正式的词，代码键是**数据契约**的一部分
 *   （`pool_texts.jsonl` / 转写的 `push_comp` 都用它）。
 * ⛔ **硬规矩：代码里的键不动，除非用户明确要求。**
 * ⚠️ **2026-10-05 是那个例外** —— 用户说「**先把名字改对，要不然一直 negative 也不好**」，
 *   于是 `情境负面` → `情境`（那一层装的不只是"别往这儿走"，还有"此刻的处置"）。
 *   改动是**一次性的、带过渡别名的**（`engine._read_cache` 新旧两个键都认，见 `companion/CHANGELOG.md`）。
 *   ⛔ **下不为例**：键一改，手机上已有的池子就对不上。
 * ★ **「情境」这一层的显示名 = 键名**（用户同日裁：原名"回避项"带着回避味道），
 *   所以下面**故意没有这一项** —— `labelOf` 会回落成键名。
 */
private val LAYER_LABEL = mapOf(
    "底色" to "自我",
    "记忆" to "事实",
    "临时" to "短期记忆",
    // ★ 2026-10-05 加：临时池拆成两档（短期 / 中期），见 `TempSection` 的注释。
    //   ⚠️ `中期` **不是 `LAYERS` 里的层**，只是临时池内部的档位名。
    "中期" to "中期记忆",
    // ★ 2026-10-05 再加一类：**「悬着的事」不按时间退，只由 `done` 结束**。
    //   ⚠️ 它**不是**衰减阶梯上的一档（短期→中期→长期说的是"多久会淡"），
    //   而是**另一个轴**："还没结的结"。所以给它**单独一组**，不并进中期 ——
    //   否则一条永不褪色的条目会藏在标着"慢衰减"的组里，组头还会数错。
    "悬着" to "悬着的事",
)

internal fun labelOf(layer: String): String = LAYER_LABEL[layer] ?: layer

/**
 * 长期池的三组。**每组都列出来**（哪怕空的）—— 空着本身是信息，藏掉就看不出来了。
 *
 * ★ 条目**分页取**：展开时才向 Python 要第一页，点「加载更多」再要下一页。
 *   池子只会越来越大，而条目要在主线程上解析成对象 —— 一次吐上千条卡的就是解析。
 */
@Composable
private fun LongSection(vm: AppViewModel, v: PoolView) {
    // ★ 先给组头：下面三组之和 = 这个数（消歧，见 SectionLabel 的注释）
    SectionLabel("长期记忆", v.stats.longTotal)
    LAYER_ORDER.forEach { layer ->
        val total = v.stats.longByLayer[layer] ?: 0
        val g = vm.poolGroups[layer]
        CollapsibleGroup(
            label = labelOf(layer),
            count = total,
            sub = v.delta?.let { deltaText(it.layer(layer)) },
            onOpen = { vm.loadGroup(layer) },
        ) {
            val items = g?.long.orEmpty()
            Column {
                LoadingOrErr(g, empty = items.isEmpty())
                if (g?.loaded == true && total == 0) {
                    Text(
                        text = "这一层还是空的。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                items.forEach { e ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Text(
                            text = "${e.idx}",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                            modifier = Modifier.width(18.dp),
                        )
                        // ★★ 长按 ⇒ 就地改 / 删（2026-10-07）。用户真机实测的原话：
                        //   「记忆页面长按删不了，**哪一个都是**」—— 这一屏当时连长按都没挂，
                        //   所以那条路一直是"按下去没反应"。手势与对话页**同一套**（`MemoryItemLine`）。
                        MemoryItemLine(
                            text = e.text,
                            edited = e.text in vm.editedTexts,
                            onLongPress = {
                                vm.openMemoryEditor(it, isLast = false, fromPool = true)
                            },
                            textStyle = MaterialTheme.typography.bodyMedium,
                            textColor = MaterialTheme.colorScheme.onSurface,
                        )
                    }
                }
                MoreRow(g, items.size) { vm.loadGroup(layer, more = true) }
            }
        }
    }
}

/** 「正在取…」/ 出错。两者都不占位时不显示任何东西。 */
@Composable
private fun LoadingOrErr(g: PoolGroup?, empty: Boolean) {
    if (g?.err?.isNotBlank() == true) {
        Text(
            text = g.err,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.error,
        )
    } else if (g?.loading == true && empty) {
        Text(
            text = "正在取…",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

/** 「还有 N 条」—— 点了取下一页。 */
@Composable
private fun MoreRow(g: PoolGroup?, shown: Int, onClick: () -> Unit) {
    if (g?.hasMore != true) return
    val left = (g.total - shown).coerceAtLeast(0)
    Text(
        text = if (g.loading) "正在取…" else "还有 $left 条",
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(6.dp))
            .clickable(enabled = !g.loading) { onClick() }
            .padding(vertical = 8.dp),
    )
}

/**
 * 可收纳的一组（默认**收起**）。
 *
 * ★ 为什么必须能收纳：池子**只会越来越大**（记忆层尤其）。平铺到底的话，
 *   几十条之后这一屏就没法看了 —— 用户的原话是「很快就会变得不可用」。
 *   标题上带条数，想看哪一组再点开。
 */
@Composable
internal fun CollapsibleGroup(
    label: String,
    count: Int,
    sub: String? = null,
    onOpen: (() -> Unit)? = null,
    /** ★ 空间那张图**默认开着**（它是这一屏的主视图）；三组记忆仍默认收起。 */
    initiallyOpen: Boolean = false,
    content: @Composable () -> Unit,
) {
    var open by rememberSaveable { mutableStateOf(initiallyOpen) }
    // ★ 展开时才去取那一页（懒加载）。放 LaunchedEffect 里，不在 clickable 里 ——
    //   这样"重进页面时本来就开着"的情况也会补取一次。
    LaunchedEffect(open) { if (open) onOpen?.invoke() }
    Column {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .clickable { open = !open }
                .padding(vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(
                imageVector = if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = if (open) "收起" else "展开",
                modifier = Modifier.size(16.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "$label　$count",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            // ★ 「上次归档后的变化」跟在条数后面。
            //   ⛔ 变化**为 0 时也说一句**（"上次没有变化"）：不说的话，
            //      "没显示"和"没变化"看起来一样，而这两件事不一样。
            if (!sub.isNullOrBlank()) {
                Text(
                    text = "· $sub",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                )
            }
        }
        if (open) content()
    }
}

/**
 * 变化的说法（用户给的样子：`上次产生了 x 条 / 减少了 y 条`）。
 *
 * ★ 只写非零的那一边：两边都写会变成"上次 +2 −0"这种机器话。
 * ★ 升迁（临时池里不再衰减的那些）**不算减少** —— 条目还在，只是不再淡忘。
 */
private fun deltaText(d: DeltaCount, promote: Int = 0): String = when {
    d.quiet && promote <= 0 -> "上次没有变化"
    d.add > 0 && d.del > 0 -> "上次 +${d.add} −${d.del}"
    d.add > 0 -> "上次 +${d.add}"
    d.del > 0 -> "上次 −${d.del}"
    else -> "上次升迁 ${promote} 条"
}

// --------------------------------------------------------------------------- 临时池
/**
 * 临时池的**一档**。★ 2026-10-05 拆开（用户："这个单开一组"）：
 * 临时池内部本来就有两档 —— **短期**（刚归档留下，几天就淡）与
 * **中期**（被反复提到 / 出生即中期；`tau = tau_mid`，一个季度不提才退），
 * 以前挤在"短期记忆"一个组里，界面上看不出来。
 *
 * ⚠️ `key` 是**界面组键**，不是 `LAYERS` 里的层：`"临时"` 与 `"中期"` 都取**临时条目**，
 * 由 `host_glue.pool_entries_json` 按 `tier` 分开 —— `LAYERS` 那四个键**没动**。
 */
@Composable
private fun TempSection(vm: AppViewModel, v: PoolView, key: String, label: String,
                        count: Int, d: DeltaCount, promote: Int, empty: String) {
    val s = v.stats
    val g = vm.poolGroups[key]
    CollapsibleGroup(
        label = label,
        count = count,
        sub = v.delta?.let { deltaText(d, promote) },
        onOpen = { vm.loadGroup(key) },
    ) {
        val items = g?.temp.orEmpty()
        Column {
            LoadingOrErr(g, empty = items.isEmpty())
            if (g?.loaded == true && count == 0) {
                Text(
                    text = empty,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            // 已升中期的在前、存活的次之、正在淡出的在后 —— 但**一个都不藏**。
            items.sortedWith(
                compareByDescending<TempEntry> { it.long }
                    .thenByDescending { it.alive }
                    .thenByDescending { it.w },
            ).forEach { e ->
                TempRow(
                    e = e,
                    eps = s.eps,
                    edited = e.text in vm.editedTexts,
                    onLongPress = { vm.openMemoryEditor(it, isLast = false, fromPool = true) },
                )
            }
            MoreRow(g, items.size) { vm.loadGroup(key, more = true) }
        }
    }
}

@Composable
private fun TempRow(e: TempEntry, eps: Double, edited: Boolean, onLongPress: (String) -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "${e.idx}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
            )
            // 状态只用颜色 + 短词：写长句会把一屏塞满。
            when {
                // ★ 悬着的事**放最前**：它 `long=false` 且 `alive=true`，
                //   不先判就会被归进最后的"还记得"，而它根本不会淡。
                e.hanging -> {
                    Dot(MaterialTheme.colorScheme.tertiary)
                    Text(
                        text = "悬着 · 办完才退",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.tertiary,
                    )
                }

                e.long -> {
                    Dot(MaterialTheme.colorScheme.primary)
                    Text(
                        text = "已经记住了",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }

                !e.alive -> {
                    Dot(MaterialTheme.colorScheme.error)
                    Text(
                        text = "快淡忘了",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }

                else -> {
                    Dot(MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.55f))
                    Text(
                        text = "还记得",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (e.tag.isNotBlank()) {
                Text(
                    text = "· ${e.tag}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }

        // ★★ 长按 ⇒ 就地改 / 删（2026-10-07，与长期池那三组同一套手势）。
        //   ⛔ 这里以前是一条**纯 Text** ⇒ 记忆页"按下去没反应"（用户真机实测）。
        MemoryItemLine(
            text = e.text,
            edited = edited,
            onLongPress = onLongPress,
            textStyle = MaterialTheme.typography.bodyMedium,
            textColor = MaterialTheme.colorScheme.onSurface,
        )

        // ★ 把"记得有多牢"说清楚：**左边给数字、右边给条**。
        //   原先只有一条细杆 + 一道红刻度，用户反馈"看不清、也说不清什么意思" ——
        //   光有长度没有刻度，人没法把它换算成任何具体的量。
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = if (e.long) "不会淡忘" else "记得 ${(e.w * 100).roundToInt()}%",
                style = MaterialTheme.typography.labelMedium,
                color = if (e.alive) MaterialTheme.colorScheme.onSurface
                else MaterialTheme.colorScheme.error,
                modifier = Modifier.width(78.dp),
            )
            WeightBar(w = e.w, eps = eps, alive = e.alive, modifier = Modifier.weight(1f))
        }

        Text(
            text = buildString {
                append("${trimNum(fmt(e.ageDays, 1))} 天前")
                append("　被提起过 ${e.hits} 次")
                if (e.long && e.how.isNotBlank()) {
                    append("　")
                    append(if (e.how.contains("promote")) "它自己升上来的" else "提起够多次，升上来的")
                }
            },
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

// --------------------------------------------------------------------------- 小件
/** 分组标题：小、灰、带一条细分隔线 —— 不画卡片。 */
@Composable
private fun GroupLabel(text: String) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 4.dp, bottom = 2.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelLarge.copy(fontSize = 12.sp),
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(MaterialTheme.colorScheme.onSurface.copy(alpha = 0.10f)),
        )
    }
}

@Composable
private fun Dot(color: Color) {
    Box(
        modifier = Modifier
            .size(6.dp)
            .clip(RoundedCornerShape(3.dp))
            .background(color),
    )
}
