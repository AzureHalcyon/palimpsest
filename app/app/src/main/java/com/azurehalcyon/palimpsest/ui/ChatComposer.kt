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
 * 输入框（composer）—— 扁平、单行总高 48dp，多行带**行号槽**。
 *
 * 用户反馈：「输入框这么大一个，输入后里面就窄窄一行字，比例严重失调」。
 * 实测旧版：文字行 19dp，装在一个 86dp 的玻璃药丸里（4.5:1）。
 * 这里刻意**不用** `uikit` 的 `LiquidTextField`（18dp 横向 + 12dp 纵向内边距，
 * 外面还有一层 drawBackdrop 玻璃 + `G2FieldShape` 大曲率），改成自己的 `BasicTextField`：
 *   · 单行总高 48dp；横向内边距 16dp、纵向 8dp；字号 16sp、行高 22sp；
 *   · 多行向上长到 `COMPOSER_MAX_LINES` 行，再多**整块（行号 + 文字）一起滚**；
 *   · 12dp 圆角（不是 pill）；
 *   · 发送钮是**外面独立的 36dp 圆钮**（不叠在输入框的圆角里）。
 *
 * ★ 2026-10-05 修的真 bug 与三处纠偏（都是实测，不是推理）：
 *   ① 原来 `maxLines = if (expanded) 6 else 1`，`expanded` 由 `LaunchedEffect` 看
 *      `value.contains('\n')` 置位 —— 那个状态机没用，而且引入了 `maxLines = 1`
 *      这个**会把内容压成一行**的取值 ⇒ 用户报「输入第二行时第一行不显示」。
 *      ⇒ 删掉状态机，`maxLines` 固定。
 *   ② 我还一度以为"最小高度放外层 Box 上"是元凶，改了**没用**。
 *      ⛔ 而且我用 `uiautomator` 报的 bounds 当证据，被 **Compose 的 IME 代理视图**骗了
 *      （它报的一直是 48dp）。⇒ **判断输入框长没长高，只能看截图。**
 *   ③ 真正剩下的是**两处上限打架**：`maxLines = 5` 允许 5 行，而外层 `heightIn(max=120dp)`
 *      只装得下 4 行多（120 − 8×2 内边距 = 104dp ÷ 22dp ≈ 4.7）⇒ 用户看到的"最多 4 行"。
 *      ⇒ 上限**由一个地方算出来**：`行高 × 行数`，见 `maxTextH`。
 */
@Composable
internal fun FlatChatComposer(
    value: String,
    onValueChange: (String) -> Unit,
    onSendClick: () -> Unit,
    isGenerating: Boolean,
    modifier: Modifier = Modifier,
) {
    val canSend = !isGenerating && value.isNotBlank()
    val buttonEnabled = isGenerating || canSend
    val scheme = MaterialTheme.colorScheme

    // 行高与 `textStyle.lineHeight` **用同一个 sp**（不是 dp）：这样用户改系统字号时，
    // 行号与文字同步缩放，不会错位。
    val dens = LocalDensity.current
    val lineH = with(dens) { COMPOSER_LINE_HEIGHT.toDp() }
    val maxTextH = lineH * COMPOSER_MAX_LINES
    // 视觉行数（含折行）——由 `onTextLayout` 给，比数 `\n` 准。
    var visLines by remember { mutableStateOf(1) }
    // 每个视觉行是不是某个**逻辑行**的第一行（折行的续行为 false）。
    var logicalStart by remember { mutableStateOf(listOf(true)) }
    val scroll = rememberScrollState()

    // ★ **高度本身做动画**（用户："前五行，以及删行的时候也该有一样的效果"）。
    //   5 行以内没有滚动，唯一的跳变就是"框一下子高了一行 / 矮了一行"；删行同理（反向）。
    //
    //   ⚠️ 我在这里记错过一次，留着免得再犯：先用的是 `Modifier.animateContentSize()`，
    //      把时长临时拉到 1200ms 后，删一行、~300ms 时截图看到框已缩回 48dp，
    //      于是判成"animateContentSize 没生效"。**用户纠正：不是没生效，是太慢。**
    //      ⇒ 教训：拿"某一时刻的截图"否定一个动画之前，先确认**采样点相对动画时长的位置**
    //        （`adb screencap` 一来一回就有几百 ms，1200ms 的动画很容易已经走完）。
    //   ✅ 现在用**显式算目标高度再动画**：目标高度由一个地方算出来（行高 × 行数，
    //      夹在"单行最小 32dp"与"上限 5 行"之间），`animateDpAsState` 两个方向都管，
    //      时长与滚动那条**共用 `COMPOSER_ANIM_MS`**。
    val targetTextH = (lineH * visLines).coerceIn(COMPOSER_MIN_TEXT_HEIGHT, maxTextH)
    val animTextH by animateDpAsState(
        targetValue = targetTextH,
        animationSpec = tween(durationMillis = COMPOSER_ANIM_MS),
        label = "composerTextH",
    )

    // ★ 超过 `COMPOSER_MAX_LINES` 行之后是**外层**滚动（不是输入框自己滚）。
    //   实测：光标在末尾、内容 8 行、可见 5 行时，视野**停在顶部**，末尾看不见
    //   —— 靠 `BasicTextField` 自己的 bringIntoView 不够。
    //   ⇒ 加了行就把视野滚到末尾。这里踩过的坑，注释留住免得再走一遍：
    //     ① ⛔ **不能用 `LaunchedEffect(value)`**：每敲一个字它就重启，而
    //        `animateScrollTo` 是**挂起动画** ⇒ 每次都被取消、永远滚不到位，
    //        表现就是"按回车，最新行跑到下面，看到的还是上面这些行"。
    //     ② ✅ 盯**行数**（`visLines`）：effect 只起一次，动画因此能跑完 ——
    //        换行时是**平滑滑上去**的，不是瞬移。
    //        ⚠️ 不要盯 `scroll.maxValue`：容器的视口在高度动画期间每帧都在变，
    //        那个值也跟着每帧变，盯它就等于让两条动画互相追。
    //        `visLines` 只在**加行/删行**时变，与高度动画解耦。
    //     ③ 用户正在拖滚动条时不抢（他可能正滚上去改中间某一行）。
    LaunchedEffect(scroll) {
        snapshotFlow { visLines }.collect {
            // 等这一帧布局落定，再动画到末尾。时长与高度动画**同一个常数**
            // （两者不会同时发生，见 COMPOSER_ANIM_MS 的注释）。
            withFrameNanos { }
            if (scroll.maxValue > 0 && !scroll.isScrollInProgress) {
                scroll.animateScrollTo(scroll.maxValue, tween(durationMillis = COMPOSER_ANIM_MS))
            }
        }
    }

    Row(
        modifier = modifier.fillMaxWidth(),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(
            modifier = Modifier
                .weight(1f)
                .clip(RoundedCornerShape(FlatRadius))
                .background(scheme.surfaceContainerHigh)
                .padding(
                    start = COMPOSER_GUTTER_INSET, end = 16.dp,
                    top = COMPOSER_VPADDING, bottom = COMPOSER_VPADDING,
                ),
        ) {
            if (value.isEmpty()) {
                Text(
                    text = "说点什么…",
                    style = TextStyle(fontSize = 16.sp, lineHeight = COMPOSER_LINE_HEIGHT),
                    color = scheme.onSurfaceVariant.copy(alpha = 0.7f),
                    // ★ 要让开行号槽 —— 否则占位文字会压在「1」上。
                    // ★ 垂直居中（与上面 `slack / 2` 同一件事）：占位只在**空输入**时出现，
                    //   也就是单行那种情况 ⇒ 居中正好与真正输入时的位置一致。
                    modifier = Modifier
                        .align(Alignment.CenterStart)
                        .padding(start = COMPOSER_GUTTER_WIDTH),
                )
            }
            // ★ 行号与文字**放在同一个滚动容器里** ⇒ 超过 `COMPOSER_MAX_LINES` 行时
            //   一起滚、永远对齐。所以 `BasicTextField` 自己**不设 maxLines**
            //   （不设就不会自己内部滚动，行号才对得上）。
            // ★★ **单行时把多出来的高度均分到上下**（2026-10-06，用户指出："输入框里这个
            //   输入气泡，到输入框下沿有一段距离，但到上沿却没有"）。
            //   真因：内层高度用 `animTextH`，而单行时它被 `COMPOSER_MIN_TEXT_HEIGHT`
            //   抬到 **32dp**，文字只有 **22dp** ⇒ 顶对齐就变成"上 8dp、下 18dp"。
            //   改法：算出多出来的那份 `slack`，**只加一半到 top** ——
            //   ⛔ 不用 `verticalAlignment = CenterVertically`：多行溢出时那个会把
            //      第一行顶出可视区（滚动容器里内容比框高时居中对齐＝上边被切）。
            //   ⛔ `slack` 必须按 `lineH * visLines` 算：多行 / 滚动时它为 0 ⇒ 什么都不做。
            val slack = (animTextH - lineH * visLines).coerceAtLeast(0.dp)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    // ★ 高度用**动画值**（见 `animTextH`），内容超出才滚动。
                    .height(animTextH)
                    .padding(top = slack / 2)
                    .verticalScroll(scroll),
                verticalAlignment = Alignment.Top,
            ) {
                Column(
                    modifier = Modifier
                        .width(COMPOSER_GUTTER_WIDTH)
                        .padding(end = COMPOSER_GUTTER_GAP),
                ) {
                    // 编号只给**逻辑行**的第一行；折行的续行留空占位（编辑器的做法），
                    // 否则编号会与视觉行错位，而且会跳号。
                    var logical = 0
                    repeat(visLines) { i ->
                        val isFirst = logicalStart.getOrElse(i) { i == 0 }
                        val label = if (isFirst) { logical += 1; "$logical" } else ""
                        // ⛔ 不能只靠 `TextStyle(lineHeight = …)`：**单行** `Text` 的行盒
                        //   不一定撑到 lineHeight（实测：7 行文字只显示 5 行，行号槽却
                        //   把 7 个号全挤出来了 ⇒ 槽被压扁、和文字错位）。
                        //   ⇒ 每一格**显式**给一个行高的高度，编号在格内垂直居中。
                        Box(
                            modifier = Modifier.fillMaxWidth().height(lineH),
                            contentAlignment = Alignment.CenterEnd,
                        ) {
                            Text(
                                text = label,
                                style = TextStyle(fontSize = COMPOSER_GUTTER_FONT),
                                color = scheme.onSurfaceVariant.copy(alpha = 0.55f),
                                textAlign = TextAlign.End,
                            )
                        }
                    }
                }
                BasicTextField(
                    value = value,
                    onValueChange = onValueChange,
                    enabled = !isGenerating,
                    textStyle = TextStyle(
                        fontSize = 16.sp,
                        lineHeight = COMPOSER_LINE_HEIGHT,
                        color = scheme.onSurface,
                    ),
                    cursorBrush = SolidColor(scheme.primary),
                    onTextLayout = { r ->
                        visLines = r.lineCount
                        logicalStart = (0 until r.lineCount).map { ln ->
                            ln == 0 || value.getOrNull(r.getLineStart(ln) - 1) == '\n'
                        }
                    },
                    modifier = Modifier
                        .weight(1f)
                        // ★ 下限跟着**一行的高度**走（不是 `COMPOSER_MIN_TEXT_HEIGHT`）：
                        //   框的整体高度已经由外层 `animTextH` 保证，这里再设 32dp 只会
                        //   让文字在框内"顶到上面"（就是用户看到的那处不对称）。
                        .heightIn(min = lineH),
                )
            }
        }

        Box(
            modifier = Modifier
                .size(SEND_BUTTON_SIZE)
                .clip(CircleShape)
                .background(if (buttonEnabled) scheme.primary else scheme.surfaceContainerHighest)
                .clickable(enabled = buttonEnabled, onClick = onSendClick),
            contentAlignment = Alignment.Center,
        ) {
            if (isGenerating) {
                CircularProgressIndicator(
                    modifier = Modifier.size(16.dp),
                    strokeWidth = 2.dp,
                    color = scheme.onPrimary,
                )
            } else {
                Icon(
                    imageVector = Icons.Default.ArrowUpward,
                    contentDescription = "发送",
                    modifier = Modifier.size(18.dp),
                    tint = if (buttonEnabled) scheme.onPrimary else scheme.onSurfaceVariant,
                )
            }
        }
    }
}

