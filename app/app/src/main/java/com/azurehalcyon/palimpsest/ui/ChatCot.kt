package com.azurehalcyon.palimpsest.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
/**
 * ★ 折叠行：「当时想起 k 条」。整体可点、高 ≤28dp、紧贴正文 6dp。
 *
 * 展开与否**只**由这一处 `remember` 决定：不会因为重组、切屏而变成展开。
 * 用户不点，内容一个字都不显示。
 */
@Composable
internal fun PushDisclosure(
    summary: String,
    push: List<String>,
    onExpand: () -> Unit,
    onCollapse: () -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    LaunchedEffect(expanded) {
        if (expanded) onExpand() else onCollapse()
    }
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .clip(RoundedCornerShape(8.dp))
                .clickable { expanded = !expanded }
                .padding(horizontal = 4.dp, vertical = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            Icon(
                imageVector = if (expanded) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = null,
                modifier = Modifier.size(12.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = if (expanded) "收起" else summary,
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        AnimatedVisibility(
            visible = expanded,
            enter = expandVertically() + fadeIn(),
            exit = shrinkVertically() + fadeOut(),
        ) {
            Column(
                modifier = Modifier.padding(start = 2.dp, top = 2.dp, end = 2.dp, bottom = 2.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                push.forEachIndexed { i, p ->
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(
                            text = "${i + 1}",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        MemoryItemLine(text = p, modifier = Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

/**
 * 归档进行中的那一条 —— **占输入框的位置**，不与输入框叠在一起。
 *
 * 文案只说两件使用者真正关心的事：现在在做什么、我能不能走开。
 */
@Composable
internal fun ArchivingBar(phase: String = "", reasoning: String = "", modifier: Modifier = Modifier) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(FlatRadius))
            .background(MaterialTheme.colorScheme.surfaceContainerHigh)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            CircularProgressIndicator(
                modifier = Modifier.size(18.dp),
                strokeWidth = 2.dp,
                color = MaterialTheme.colorScheme.primary,
            )
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    // ★ 流式：把"第 3/5 轮""正在核对"这类**如实显示**出来
                    //   —— 那 25–60 秒从"不知道在干嘛"变成"看得见它在想"。
                    text = phase.ifBlank { "正在归档这一场…" },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Text(
                    text = "可以放着不管；就算关掉，下次打开它也会接着做完。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        // ★ 归档的 CoT：与工具调用**同形**的折叠行（点了才展开）。
        if (reasoning.isNotBlank()) CotRow(reasoning, streaming = true)
    }
}

/**
 * 这一轮发出去花掉的 token —— 标在**用户气泡**上。
 *
 * ★ 分别标注（用户定的）：输入归用户那一边、输出归助手那一边，各贴各的气泡。
 * ★ 一轮里可能调了多次模型（模型自己再查一次）⇒ 这里已经是**累加值**，
 *   两次调用的输入都算在里面了。
 * ★ 服务端不给 `usage` ⇒ 返回 null，那一行整个不显示（不留空壳）。
 */
internal fun usageIn(u: Map<String, Int>): String? {
    val p = u["prompt_tokens"] ?: 0
    return if (p > 0) "输入 $p" else null
}

/** 这一轮生成花掉的 token —— 标在**助手气泡**上。 */
internal fun usageOut(u: Map<String, Int>): String? {
    val c = u["completion_tokens"] ?: 0
    return if (c > 0) "输出 $c" else null
}

/**
 * ★★ **归档直接摆在对话框里**（2026-10-07）。
 *
 * 用户口径：「**直接在对话框里展示归档，画条线和对话隔开就最简单了**」。
 * ⇒ 不做卡片、不做浮层：一条 `FlatDivider` 隔开，下面就是这一次归档的每一步
 *   （`归档：第 3/12 轮` / `核对：…`），每一步是**与工具调用同形**的折叠行。
 * ★ **什么时候收起**（两句话，别混）：
 *   · 归档**跑完**（出草案、等用户审）⇒ **留着不清**（用户原话：「我还没看完就消失了」）；
 *   · 用户按了「**完成**」（落地）⇒ **收起来**（2026-10-07 用户真机指出：
 *     「点了完成了之后，显示睡醒了，但归档 COT 还在」）—— 见 `AppViewModel.archiveCommit`。
 *   下一次归档开始时也换页（`archiveLog.clear()`）。
 */
@Composable
internal fun ArchiveLogInline(log: List<ArchiveStep>, modifier: Modifier = Modifier) {
    Column(modifier = modifier.fillMaxWidth()) {
        FlatDivider()
        Spacer(Modifier.height(8.dp))
        Text(
            text = "归档 · 共 ${log.size} 步",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 6.dp, bottom = 2.dp),
        )
        log.forEach { step ->
            CotRow(cot = step.text, label = step.phase.ifBlank { "推理" })
        }
    }
}

/**
 * ★ **推理行**（CoT，2026-10-07）：与「再查一次记忆」**同一个形状** ——
 * 一行标签 + 右侧箭头，**点了才展开**（用户口径：「思维链形式做成和工具调用一样的，
 * 点了才展开，格式统一」）。
 *
 * ★ 生成中（`streaming = true`）标签上带一句**实时预览**（取推理的末 34 字）⇒
 *   不展开也看得出它在动；展开才看全文。跑完就只剩「思考 · <预览>」。
 * ⛔ 默认折叠：推理动辄几千字，铺开就把正文挤没了（这是"围观"，不是主角）。
 */
@Composable
internal fun CotRow(
    cot: String,
    streaming: Boolean = false,
    label: String = "",
    modifier: Modifier = Modifier,
) {
    var open by remember { mutableStateOf(false) }
    val preview = remember(cot) {
        val one = cot.trim().replace(Regex("\\s+"), " ")
        when {
            one.isEmpty() -> ""
            // ★ 取**末尾**：流式时最能说明"它现在想到哪儿了"（开头早就过去了）。
            one.length <= 34 -> one
            else -> one.takeLast(34)
        }
    }
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
                imageVector = Icons.Filled.Psychology,
                contentDescription = null,
                modifier = Modifier.size(14.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = buildString {
                    append(label.ifBlank { if (streaming) "思考中" else "思考" })
                    if (preview.isNotEmpty()) append(" · ").append(preview)
                },
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
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
            ReasoningBlock(
                cot = cot,
                modifier = Modifier.padding(start = 22.dp, top = 2.dp, bottom = 2.dp),
            )
        }
    }
}

@Composable
internal fun ThinkingRow(phase: String = "", reasoning: String = "") {
    val transition = rememberInfiniteTransition(label = "thinking")
    val a by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse),
        label = "thinkingAlpha",
    )
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(FlatRadius))
                    .background(MaterialTheme.colorScheme.surfaceContainerHigh)
                    .padding(horizontal = 10.dp, vertical = 6.dp)
                    .alpha(a),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                CircularProgressIndicator(modifier = Modifier.size(12.dp), strokeWidth = 2.dp)
                Text(
                    // ★ 有 `phase`（归档在跑第几轮）就先说它在干什么，比一个"…"有用得多。
                    text = phase.ifBlank { "它在想…" },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (reasoning.isNotBlank()) CotRow(reasoning, streaming = true)
    }
}

/**
 * ★★ **推理块**（CoT，2026-10-07）：淡一档、限高、可滚动 —— 只服务"看得见它在想"。
 *
 * 为什么要限高：推理常常几千字，铺开会把这一轮挤得看不见正文；
 * 它是"围观"，不该抢正文的位置（用户口径：「把推理展示出来」＝看得见，不是主角）。
 * ⚠️ 首行留白**不留空行**：`trim()` 一下，免得上面多出一段空白。
 */
@Composable
internal fun ReasoningBlock(cot: String, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(max = 220.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceContainerHighest.copy(alpha = 0.5f))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Text(
            text = cot.trim(),
            style = MaterialTheme.typography.bodySmall.copy(lineHeight = 18.sp),
            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.85f),
            modifier = Modifier.verticalScroll(rememberScrollState()),
        )
    }
}

