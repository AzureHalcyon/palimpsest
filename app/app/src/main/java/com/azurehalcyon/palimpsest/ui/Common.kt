package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog

// ⛔ 这里曾经有个 `Modifier.clearStatusBar()`（= `windowInsetsPadding(WindowInsets.statusBars)`）。
//   它的来历是 `uikit` 的 `LiquidScreen` 把顶栏当**浮层**、内容层不自动加 top inset，
//   所以页面得自己让开状态栏。**现在外壳已经换成 `FlatShell`（占位工具栏）**，
//   状态栏 inset 由工具栏自己吃一次；页面再吃一次就会多出一条
//   44.6dp ≈ 状态栏高度 的纯黑横带（实测踩到，用户一眼就看出来了）。
//   ⇒ 直接删掉这个 helper：留着它就是在等下一个人踩。

internal enum class NoticeTone { Info, Warn, Error }

/**
 * 扁平对话框（**替代 `uikit` 的 `LiquidDialog`**）。
 *
 * ★ 为什么必须换掉：`LiquidDialog` 依赖 `LiquidScreen` 提供的宿主，少了它直接抛
 *   `IllegalStateException: LiquidDialog must be used inside LiquidScreen` ——
 *   我们的外壳是 `FlatShell`，**实测三个对话框（凭证 / 提示词 / 诊断）一点就崩**。
 *   既然整体要扁平，就不该再依赖那套浮层宿主：这里用 Compose 自带的 `Dialog` +
 *   一块 12dp 方角面板，自带高度上限与滚动（提示词那种长内容也放得下）。
 *
 * 参数形状**刻意与 `LiquidDialog` 保持一致**，让调用处只改名字。
 */
@Composable
internal fun FlatDialog(
    visible: Boolean,
    onDismissRequest: () -> Unit,
    title: @Composable () -> Unit,
    content: @Composable ColumnScope.() -> Unit,
    actions: (@Composable () -> Unit)? = null,
) {
    if (!visible) return
    Dialog(onDismissRequest = onDismissRequest) {
        Surface(
            shape = RoundedCornerShape(FlatRadius),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(20.dp)) {
                title()
                Spacer(Modifier.height(12.dp))
                Column(
                    modifier = Modifier
                        .heightIn(max = 460.dp)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                    content = content,
                )
                if (actions != null) {
                    Spacer(Modifier.height(16.dp))
                    actions()
                }
            }
        }
    }
}

/** 一句提示（人话，不是异常堆栈）。文案要短：它是打断，不是正文。 */
@Composable
internal fun NoticeCard(
    text: String,
    tone: NoticeTone = NoticeTone.Info,
    modifier: Modifier = Modifier,
) {
    val c = when (tone) {
        NoticeTone.Info -> MaterialTheme.colorScheme.secondaryContainer
        NoticeTone.Warn -> MaterialTheme.colorScheme.tertiaryContainer
        NoticeTone.Error -> MaterialTheme.colorScheme.errorContainer
    }
    val t = when (tone) {
        NoticeTone.Info -> MaterialTheme.colorScheme.onSecondaryContainer
        NoticeTone.Warn -> MaterialTheme.colorScheme.onTertiaryContainer
        NoticeTone.Error -> MaterialTheme.colorScheme.onErrorContainer
    }
    Box(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(c)
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Text(text = text, style = MaterialTheme.typography.bodySmall, color = t)
    }
}

/**
 * 「记得有多牢」的条。
 *
 * ★ 界面上**不写 `w` 这个符号**，数字由调用方在条旁边给出（「记得 74%」）。
 *   原先这里只有一条 5dp 细杆 + 一个红刻度，用户反馈「看不清、也说不清什么意思」——
 *   **光有长度没有刻度，人没法把它换算成任何一个具体的量**。
 *   现在：条加粗到 8dp、快淡忘时整条变红、界线那道刻痕加重，数字在条左边。
 *
 * @param w 存活度，1.0 满
 * @param eps 淡出阈值（内部量；只用它决定那道刻痕画在哪，不显示数字）
 */
@Composable
internal fun WeightBar(
    w: Double,
    eps: Double,
    alive: Boolean,
    modifier: Modifier = Modifier,
) {
    val frac = w.coerceIn(0.0, 1.0).toFloat()
    val epsFrac = eps.coerceIn(0.0, 1.0).toFloat()
    val track = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.12f)
    // 快淡忘 ⇒ 整条转成警示色：颜色本身就是状态，不必再去对照那道刻痕。
    val fill = if (alive) MaterialTheme.colorScheme.primary
    else MaterialTheme.colorScheme.error.copy(alpha = 0.8f)

    Box(
        modifier = modifier
            .height(8.dp)
            .clip(RoundedCornerShape(4.dp))
            .background(track),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth(frac.coerceAtLeast(0.02f))
                .height(8.dp)
                .background(fill),
        )
        // 快淡忘的界线：从 0 到 eps 的一段，末端一道刻痕（要调去设置里调）。
        Box(
            modifier = Modifier
                .fillMaxWidth(epsFrac)
                .height(8.dp),
            contentAlignment = Alignment.CenterEnd,
        ) {
            Box(
                modifier = Modifier
                    .width(2.dp)
                    .height(8.dp)
                    .background(MaterialTheme.colorScheme.error),
            ) {}
        }
    }
}
