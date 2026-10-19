package com.azurehalcyon.palimpsest.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import androidx.compose.ui.window.PopupProperties
import kotlin.math.roundToInt
import kotlinx.coroutines.flow.collectLatest

/**
 * 对话屏的骨架件。
 *
 * **骨架 vendor 自 zafiro**（MIT，见 `uikit/NOTICE.md` 的追记）
 * —— `UserBubblePosition` 的语义、`userBubblePosition` 的分组规则、
 * `rememberScrollFollowState` 的贴底状态机、composer 绝对定位 + 底部避让的布局思路，
 * 出处都是 `_ref/zafiro/.../ui/content/HomeChatComponents.kt` 与 `HomePageContent.kt`。
 *
 * ★ **但视觉不是照抄的**：zafiro 那套密度是配它"一条用户消息 + 一长串推理/工具块"的，
 *   我们的气泡少、字少，同样的数会变成大片虚空。所以按本项目**真机实测**重定：
 *
 * | 项 | zafiro | 本项目 | 依据 |
 * |---|---|---|---|
 * | 轮间距 | 42dp | **16dp** | 实测 68dp 太散 |
 * | 块间距 | 12dp | **8dp** | 同上 |
 * | 气泡 | 24dp 连续曲率 + 手绘尾巴 | **12dp 纯圆角、无尾巴** | 用户要求扁平化 |
 * | composer 单行总高 | ~86dp（含玻璃胶囊） | **48dp** | 实测比例 4.5:1 |
 * | 发送钮 | 嵌在大胶囊里的 48sp | **独立的 36dp 圆钮** | 圆上叠圆很丑 |
 */

// ── 排版密度：调视觉只改这一处 ───────────────────────────────────────────────
/** 轮与轮之间的分隔。 */
internal val TurnSeparator = 16.dp

/** 一轮内部的块间距。 */
internal val BlockSpacing = 8.dp

/** 连续用户气泡组内间隙。 */
internal val UserBubbleGap = 3.dp

/** 气泡圆角（扁平）。 */
internal val BubbleRadius = 12.dp

/** 折叠行与它所属正文的间距。 */
internal val DisclosureGap = 6.dp

/** 单行 composer 总高（含内边距）。 */
internal val COMPOSER_SINGLE_LINE_HEIGHT = 48.dp

/** 上下内边距（各 8dp）。 */
internal val COMPOSER_VPADDING = 8.dp

/**
 * 文字行高。★ **必须与 `BasicTextField` 的 `textStyle.lineHeight` 是同一个值**
 * （行号槽靠它对行）；用 `sp` 而不是 `dp`，这样用户改系统字号时行号与文字同步缩放。
 */
internal val COMPOSER_LINE_HEIGHT = 22.sp

/**
 * composer 最多显示几行（再多就**整块（行号 + 文字）一起滚**）。
 *
 * ★ 2026-10-05 修的真 bug 记在这里：
 *   原来写的是 `maxLines = if (expanded) 6 else 1`，`expanded` 由 `LaunchedEffect` 看
 *   `value.contains('\n')` 置位 —— 那个状态机没用，而且引入了 `maxLines = 1`
 *   这个**会把内容压成一行**的取值 ⇒ 用户报「输入第二行时第一行不显示」。
 *   ⇒ 状态机删掉，`maxLines` 不再写死在这里：见 `maxTextH`（行高 × 行数）。
 */
internal const val COMPOSER_MAX_LINES = 5

/** 输入框**自己**（不含内边距）的最小高度 = 单行总高 − 上下内边距 = 32dp。 */
internal val COMPOSER_MIN_TEXT_HEIGHT = COMPOSER_SINGLE_LINE_HEIGHT - COMPOSER_VPADDING * 2

/** 行号槽宽度（含它与文字之间的 `COMPOSER_GUTTER_GAP`）。 */
internal val COMPOSER_GUTTER_WIDTH = 26.dp

/** 行号与文字之间的间隔。 */
internal val COMPOSER_GUTTER_GAP = 10.dp

/** 行号槽离输入框**左边界**的距离。
 *
 * 用户："行号可以往左一点距离输入框边界近一点"。原来横向内边距是 16dp，
 * 行号就顶在 16dp 处；把它压到 6dp，同时把行号槽加宽到 26dp ⇒
 * **文字**的左边缘几乎没动（6+26=32dp，原来 16+20=36dp），**行号**则明显靠左。
 */
internal val COMPOSER_GUTTER_INSET = 6.dp

/**
 * 输入框两个动画的**统一个时长**：① 高度变化（加行/删行）② 内容超出后视野跟随。
 *
 * 用户："前五行，以及删行的时候也该有一样的效果" + "速度统一一下"。
 * 用一个常数是因为**这两件事永远不会同时发生**：
 *   · 没到 [COMPOSER_MAX_LINES] 行时 `maxValue = 0`，只有高度在变（没得滚）；
 *   · 到了上限，高度已经顶住不变，只有滚动在动。
 * 取 120ms：一行的高度滑过去，比瞬移柔和，又不会让人等。
 */
internal const val COMPOSER_ANIM_MS = 120

/** 行号字号 —— 比正文小一号，不抢视线。 */
internal val COMPOSER_GUTTER_FONT = 12.sp

/** 发送钮直径。 */
internal val SEND_BUTTON_SIZE = 36.dp

/** 用户气泡在连续用户消息组内的位置。 */
enum class UserBubblePosition {
    /** 独立单条 */
    Single,

    /** 组首 */
    GroupFirst,

    /** 组中 */
    GroupMid,

    /** 组末 */
    GroupLast,
}

/**
 * 扁平用户气泡：12dp 圆角、右对齐；组内相接的角收成 3dp。
 *
 * @param footer 气泡自己的脚注（这一轮发出去花掉的 token，标成「输入 N」）。
 *   ★ 与助手气泡的「输出 M」**各归各的气泡**（用户定的）：一个数字挂在它真正属于的
 *     那一边，比把三个数挤在一行里更好读。
 */
@Composable
internal fun UserMessageBubble(
    text: String,
    modifier: Modifier = Modifier,
    position: UserBubblePosition = UserBubblePosition.Single,
    footer: String? = null,
    menu: (@Composable (dismiss: () -> Unit) -> Unit)? = null,
) {
    val colorScheme = MaterialTheme.colorScheme
    val bg = colorScheme.primary.copy(alpha = 0.18f)
    val inner = 3.dp
    // ★ **长按那一点**（相对这颗气泡的局部坐标）。菜单要长在手指那儿，
    //   而"那儿"只有手势知道 —— 菜单内容由调用方给，位置由这里算。
    var pressAt by remember(text) { mutableStateOf<Offset?>(null) }
    val dens = LocalDensity.current
    val anchorGap = with(dens) { 6.dp.roundToPx() }
    val screenMargin = with(dens) { 12.dp.roundToPx() }
    val hasTailSide = position == UserBubblePosition.Single || position == UserBubblePosition.GroupLast
    val shape = RoundedCornerShape(
        topStart = BubbleRadius,
        topEnd = BubbleRadius,
        bottomEnd = if (hasTailSide) BubbleRadius else inner,
        bottomStart = if (position == UserBubblePosition.Single ||
            position == UserBubblePosition.GroupFirst
        ) {
            BubbleRadius
        } else {
            inner
        },
    )

    // ★ 气泡**按内容收拢**，不是永远占 82% 宽。
    //   旧写法 `fillMaxWidth(0.82f)` 会让"睡不着"三个字也撑成一个宽块、文字从左边起 ——
    //   用户反馈"用户信息应该右对齐"，说的就是这个：块是右对齐的，但看着不像。
    BoxWithConstraints(modifier = modifier.fillMaxWidth()) {
        val maxBubble = maxWidth * 0.84f
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
        ) {
            Box(
                modifier = Modifier
                    .widthIn(max = maxBubble)
                    .clip(shape)
                    .background(bg, shape)
                    // ★ **长按自己的气泡** ⇒ 撤回 / 重新生成（2026-10-06）。
                    //   为什么用 `detectTapGestures` 而不是 `combinedClickable`：
                    //   后者会带上涟漪与点击语义，而这颗气泡**本身没有点击动作**
                    //   （点一下不该有任何事发生）；长按弹出菜单本身就是反馈。
                    //   ⚠️ 它**不吃滚动**：`detectTapGestures` 一遇到拖动就取消，
                    //      列表照常滑（本仓库 `SpaceMap` 里是同一套写法）。
                    //   ★ 把**按下的那一点**（相对气泡的局部坐标）交出去 ——
                    //     菜单要长在手指那儿，而"那儿"只有手势知道。
                    //   ⛔ `menu == null` 时**一个手势都不挂** —— 不给"按了没反应"的假入口。
                    .then(
                        if (menu != null) {
                            Modifier.pointerInput(text) {
                                detectTapGestures(onLongPress = { off -> pressAt = off })
                            }
                        } else {
                            Modifier
                        },
                    )
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            ) {
                Column {
                    Text(
                        text = text,
                        style = MaterialTheme.typography.bodyLarge,
                        color = colorScheme.onSurface,
                    )
                    if (footer != null) {
                        Spacer(Modifier.height(6.dp))
                        Text(
                            text = footer,
                            style = MaterialTheme.typography.labelSmall,
                            color = colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                            modifier = Modifier.align(Alignment.End),
                        )
                    }
                }

                // ★★ **菜单挂在气泡自己身上**（2026-10-06，用户口径：不要底部大弹窗）。
                //   为什么放在**气泡的 Box 里面**：`Popup` 的定位器拿到的 `anchorBounds` 是
                //   **它父节点的窗口边界** —— 放在这里，锚点就正好是这颗气泡；
                //   放到外面（Row / Column）拿到的是整行，坐标立刻差一截
                //   （用户气泡是**右对齐**的，差得还不少）。
                //   ⛔ 它不受 `clip(shape)` 影响：Popup 画在**独立窗口**里。
                val at = pressAt
                if (at != null && menu != null) {
                    Popup(
                        popupPositionProvider = PressAnchoredMenu(
                            IntOffset(at.x.roundToInt(), at.y.roundToInt()),
                            anchorGap, screenMargin,
                        ),
                        onDismissRequest = { pressAt = null },
                        // `focusable` ⇒ 点外面 / 按返回都能关，且**吃掉**这次触摸
                        // （不会顺手点到气泡下面的东西）。
                        properties = PopupProperties(focusable = true),
                    ) {
                        menu { pressAt = null }
                    }
                }
            }
        }
    }
}

/**
 * 长按菜单的**定位器**：摆在手指按下的那一点附近，再**夹回屏内**。
 *
 * ★ 为什么必须自己算：`Popup` 默认的 `alignment`/`offset` 只能相对**父节点**摆，
 *   而长按点通常在气泡内部 —— 直接把偏移量交给默认定位，要么压在正在看的那句话上，
 *   要么被右边缘裁掉（用户气泡是右对齐的，最容易撞墙）。
 *   定位器这边能拿到三样默认拿不到的东西：`anchorBounds`（气泡在窗口里的边界）、
 *   `windowSize`、以及 **`popupContentSize`（卡片自己的尺寸）** ⇒ 于是可以"先摆、再夹"。
 *
 * ★ 与"绑气泡位置"的关系：两者的**锚点是同一个**（都是这颗气泡），
 *   区别只在偏移量 —— 这里多加了手势给的那个按下点。想改成"挂在气泡下方"
 *   只要把 `press` 传 `IntOffset.Zero` 即可，其余一行不用动。
 */
internal class PressAnchoredMenu(
    private val press: IntOffset,
    private val gapPx: Int,
    private val marginPx: Int,
) : PopupPositionProvider {
    override fun calculatePosition(
        anchorBounds: IntRect,
        windowSize: IntSize,
        layoutDirection: LayoutDirection,
        popupContentSize: IntSize,
    ): IntOffset {
        // 手指在窗口里的绝对位置 = 气泡左上角 + 按下的局部偏移（+ 一点间隙，别压住字）
        var x = anchorBounds.left + press.x + gapPx
        var y = anchorBounds.top + press.y + gapPx
        // 先摆再夹：右下最容易越界（卡片比手指宽），左上留一点边距
        val maxX = windowSize.width - marginPx - popupContentSize.width
        val maxY = windowSize.height - marginPx - popupContentSize.height
        if (x > maxX) x = maxX
        if (y > maxY) y = maxY
        if (x < marginPx) x = marginPx
        if (y < marginPx) y = marginPx
        return IntOffset(x, y)
    }
}

/**
 * 长按弹出的**小卡片**（不是屏幕底部的大单）：一行一个动作，圆角与气泡同一套。
 *
 * ⚠️ 每一行的点击都**必须先关掉自己**再执行动作：动作里可能会把这张单的宿主
 *   （那颗气泡）从列表里撤掉 —— 不先关，就会留下一个指向已消失节点的弹窗。
 */
@Composable
internal fun FlatContextMenu(
    items: List<Pair<String, () -> Unit>>,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(FlatRadius),
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
        shadowElevation = 8.dp,
    ) {
        // ★ **卡片宽度 = 最宽那一行**，不是整屏。
        //   `IntrinsicSize.Max` 正是 Material 自己 `DropdownMenu` 用的那一招
        //   （它内部也是 `Column(Modifier.width(IntrinsicSize.Max))` + 每行 `fillMaxWidth()`）。
        //   ⚠️ 别把 `fillMaxWidth()` 去掉改成 `wrapContentWidth`：那样**短的那行点不到**
        //      （点击区只有文字那么宽），长按菜单的整行可点就没了。
        // ★ **必须再压一个最大宽度**：`IntrinsicSize.Max` 只认"一行排完要多宽"，
        //   而"只能从最近的一句开始往回撤 —— 这一轮后面还有 N 轮"这种长句会**撑出屏幕被裁掉**。
        //   `widthIn` 写在**前面**（先夹住可用宽度，内部再取固有宽度）⇒ 长句**换行**、短句不变。
        Column(
            Modifier
                .padding(vertical = 6.dp)
                .widthIn(max = 280.dp)
                .width(IntrinsicSize.Max),
        ) {
            items.forEach { (label, action) ->
                Text(
                    text = label,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable(onClick = action)
                        .padding(horizontal = 20.dp, vertical = 12.dp),
                )
            }
        }
    }
}

/**
 * 扁平助手气泡：与用户气泡**同一套圆角**，左对齐、底色更沉一点。
 *
 * 用户反馈：「AI 回复没有可见的气泡」—— 旧版助手正文是**裸文字**（照抄 zafiro，
 * 它那边靠推理块把层次撑起来；我们这边一条裸文字浮在深色底上，看不出这是"它说的话"）。
 * 现在给它一个和用户气泡配对的容器：同样是 12dp 方角、同样 84% 宽上限，只换底色与对齐。
 *
 * @param footer 气泡自己的脚注（这一轮花了多少 token）。
 *   ★ 必须画在**气泡里面**：放在气泡外面再靠屏幕右边对齐，看起来像页面级的说明，
 *     与"这一条回复"没关系（用户指出过）。
 */
@Composable
internal fun AssistantMessageBubble(
    modifier: Modifier = Modifier,
    footer: String? = null,
    content: @Composable () -> Unit,
) {
    val colorScheme = MaterialTheme.colorScheme
    val shape = RoundedCornerShape(BubbleRadius)
    BoxWithConstraints(modifier = modifier.fillMaxWidth()) {
        val maxBubble = maxWidth * 0.84f
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Start,
        ) {
            Box(
                modifier = Modifier
                    .widthIn(max = maxBubble)
                    .clip(shape)
                    .background(colorScheme.surfaceContainerHigh, shape)
                    .padding(horizontal = 14.dp, vertical = 10.dp),
            ) {
                Column {
                    content()
                    if (footer != null) {
                        Spacer(Modifier.height(6.dp))
                        Text(
                            text = footer,
                            style = MaterialTheme.typography.labelSmall,
                            color = colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                            modifier = Modifier.align(Alignment.End),
                        )
                    }
                }
            }
        }
    }
}

/**
 * 模型每轮自己调的 `recall` —— 按**工具调用**的样子展示（用户要求）。
 *
 * ★ 与「想起 N 条」是两回事：那个是每轮都有的注入；这个是**模型自己决定**要多查一次。
 *   【实测】80 轮里只触发 2 次 ⇒ 它出现本身就是信息，所以值得单独一行。
 * ⛔ 与注入一样**默认折叠、永不自动展开**：不点开就只看到查了什么。
 */
@Composable
internal fun ToolCallRow(
    query: String,
    got: List<String>,
    回: String = "",
    kind: String = "回忆",
    onExpand: () -> Unit,
    onCollapse: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var open by remember { mutableStateOf(false) }
    LaunchedEffect(open) { if (open) onExpand() else onCollapse() }
    val isEvent = kind == "事件"
    val label = if (isEvent) "事件" else "回忆"

    Column(modifier = modifier) {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(6.dp))
                .clickable { open = !open }
                .padding(horizontal = 6.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(
                imageVector = if (isEvent) Icons.Default.Edit else Icons.Default.Search,
                contentDescription = null,
                modifier = Modifier.size(14.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = if (query.isBlank()) label else "$label · $query",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f, fill = false),
            )
            Icon(
                imageVector = if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = if (open) "收起" else "展开",
                modifier = Modifier.size(14.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        AnimatedVisibility(visible = open) {
            Column(
                modifier = Modifier.padding(start = 22.dp, top = 2.dp, bottom = 2.dp),
                verticalArrangement = Arrangement.spacedBy(3.dp),
            ) {
                if (got.isNotEmpty()) {
                    got.forEach { t -> MemoryItemLine(text = t) }
                } else if (回.isNotBlank()) {
                    // ★ 2026-10-19（作者：返回值现在也看不见）：查空时也要能看见**它收到了什么**
                    //   （「记忆里没有一条达到标准」「刚说过」「整理失败」—— 三种空措辞不同）。
                    MemoryItemLine(text = 回)
                }
            }
        }
    }
}

/**
 * ★ **展示一条原文/一件的文本** —— `push` 的与「再查一次」带回的都用它。
 *
 * ★★ 2026-10-17：**收成只读一行**。原来这里有"长按就地改 / 删 + 一个「改」记号"，
 *   而它背后那一整条路（`chat_edit_entry` → `pool_edits.jsonl` → 旧池子）已经删掉了 ——
 *   手势挂着却改不动，是最糟的那种"按了没反应"。改 / 删现在在**记忆页的件**上。
 */
@Composable
internal fun MemoryItemLine(
    text: String,
    modifier: Modifier = Modifier,
) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = modifier.fillMaxWidth(),
    )
}


/**
 * 手势信任的贴底跟随状态机（逻辑逐字抄自 zafiro）。
 * 用户一开始拖拽就暂停跟随；滚动停下后按 [isAtEnd] 决定是否恢复。
 */
@Composable
internal fun rememberScrollFollowState(
    interactionSource: InteractionSource,
    isScrollInProgress: () -> Boolean,
    isAtEnd: () -> Boolean,
): MutableState<Boolean> {
    val follow = remember { mutableStateOf(true) }
    var hasPendingUserScrollDecision by remember { mutableStateOf(false) }
    val isUserDragging by interactionSource.collectIsDraggedAsState()

    LaunchedEffect(isUserDragging) {
        if (isUserDragging) {
            follow.value = false
            hasPendingUserScrollDecision = true
        }
    }
    LaunchedEffect(interactionSource) {
        snapshotFlow { isScrollInProgress() }
            .collectLatest { inProgress ->
                if (!inProgress && hasPendingUserScrollDecision) {
                    follow.value = isAtEnd()
                    hasPendingUserScrollDecision = false
                }
            }
    }
    return follow
}

/** 「回到底部」圆钮。 */
@Composable
internal fun ScrollToBottomButton(
    visible: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = visible,
        modifier = modifier,
        enter = fadeIn(tween(160)) + scaleIn(tween(180), initialScale = 0.82f),
        exit = fadeOut(tween(100)) + scaleOut(tween(120), targetScale = 0.86f),
    ) {
        Box(
            modifier = Modifier
                .size(36.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.surfaceContainerHighest)
                .clickable(onClick = onClick),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Default.ArrowDownward,
                contentDescription = "回到底部",
                tint = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

/** 轮间距：组内紧凑、跨轮用 [TurnSeparator]。 */
internal fun turnTopPadding(position: UserBubblePosition, isFirst: Boolean): Modifier = when {
    isFirst -> Modifier
    position == UserBubblePosition.GroupMid || position == UserBubblePosition.GroupLast ->
        Modifier.padding(top = UserBubbleGap)

    else -> Modifier.padding(top = TurnSeparator)
}

/**
 * ★★ 「这一轮注入的**那段回忆**」+「怎么编的 ›」（2026-10-17 新线）。
 *
 * 用户口径：「原来的条目变成一段」·「加一个能点进去的按钮，看到用哪些条目、怎么编写」。
 * ⛔ 外层只显示**编写出来的那一段**；条目清单、编写版本、耗时都藏在「怎么编的」里 ——
 *    正在打字的人看见的应该是"她想起了什么"，不是"检索命中了几条"。
 */
@Composable
fun RecallBlock(
    recall: org.json.JSONObject,
    onExpand: () -> Unit = {},
    onCollapse: () -> Unit = {},
) {
    val 编写 = recall.optString("编写").trim()
    val 没有 = recall.optBoolean("没有", false)
    var open by rememberSaveable { mutableStateOf(false) }
    val 用了 = recall.optJSONArray("用了")
    val n = 用了?.length() ?: 0
    if (!没有 && 编写.isBlank() && n == 0) return

    Column(Modifier.fillMaxWidth().padding(top = 6.dp)) {
        if (没有) {
            Text(
                "（这一轮她没想起什么）",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else if (编写.isNotBlank()) {
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
                shape = MaterialTheme.shapes.medium,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    编写,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
                )
            }
        }
        // ★ 「怎么编的 ›」—— 折叠行，点一下才展开
        Row(
            Modifier.fillMaxWidth().clickable {
                open = !open
                if (open) onExpand() else onCollapse()
            }.padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "怎么编的 · 用了 $n 条",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                if (open) " ˅" else " ›",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (open) {
            if (用了 != null) {
                for (i in 0 until 用了.length()) {
                    val o = 用了.optJSONObject(i) ?: continue
                    Row(Modifier.padding(start = 10.dp, top = 3.dp)) {
                        Text(
                            "%.2f".format(o.optDouble("强度", 0.0)),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(end = 8.dp),
                        )
                        Text(
                            o.optString("文本"),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            val 版本 = recall.optJSONObject("版本")
            val 耗时 = recall.optInt("耗时ms", 0)
            Text(
                buildString {
                    append("编写：")
                    append(版本?.optString("模型").orEmpty().ifBlank { "—" })
                    val h = 版本?.optString("提示词哈希").orEmpty()
                    if (h.isNotBlank()) append(" · 提示词 ").append(h.take(8))
                    if (耗时 > 0) append(" · ").append(耗时).append(" ms")
                },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 10.dp, top = 8.dp),
            )
        }
    }
}
