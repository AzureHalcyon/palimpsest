package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.RadioButtonUnchecked
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
import androidx.compose.ui.unit.dp
import com.niki914.uikit.infra.component.MaterialTintLiquidButton
import com.niki914.uikit.infra.component.SettingExpandableTextItem
import com.niki914.uikit.infra.component.SettingToggleItem
import com.niki914.uikit.infra.component.settings.SettingsPageSpec
import com.niki914.uikit.infra.component.settings.SettingsRowAction
import com.niki914.uikit.infra.component.settings.SettingsRowSpec
import com.niki914.uikit.infra.component.settings.SettingsSectionLayout
import com.niki914.uikit.infra.component.settings.SettingsSectionSpec
import com.niki914.uikit.infra.component.settings.SettingsSpecPageContent

/**
 * 屏 3：设置。
 *
 * 结构照 zafiro 的 `SettingsHomePageContent` / `ModelConfigSettingsContent`：
 * **`SettingsSpecPageContent` + `SettingsSectionSpec`（GroupedCard）**，一组一张卡，
 * 卡片里是一行行 `SettingsListItem` 形态（标题 + 右侧当前值 + chevron）。
 * 不自己画纵向滚动容器 —— `SettingsSpecPageContent` 内部就是可滚动的设置列表页。
 *
 * 文案规矩（用户的原话：**按钮能标识清楚功能就够了，这不是说明书**）：
 *  - 参数行只给「人话名字 + 当前值」；`chat.temperature` 这种内部路径**不上界面**；
 *  - 每项的 `note`（依据）折进 [SettingExpanderRow]，**默认收起** —— 它是对账材料；
 *  - `eps` / `τ` / `w` / `k_raw` / `eff_n` 一律不出现；
 *  - **任何时长都不写**；
 *  - ★ 密钥绝不回显：两个 Key 输入框永远从空开始，旁边标明「已设置 / 未设置」。
 */
@Composable
fun SettingsScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    var knobDetail by remember { mutableStateOf<Knob?>(null) }
    var credsDialog by remember { mutableStateOf(false) }
    var promptDialog by remember { mutableStateOf(false) }
    var diagDialog by remember { mutableStateOf<Diag?>(null) }

    SettingsSpecPageContent(
        spec = settingsSpec(vm),
        modifier = modifier,
        onAction = { action ->
            when (action) {
                is SettingsRowAction.Navigate -> when {
                    action.id == ROW_CREDS -> credsDialog = true
                    action.id == ROW_PROMPT -> promptDialog = true
                    action.id.startsWith("knob:") -> {
                        val p = action.id.removePrefix("knob:")
                        knobDetail = vm.knobs.firstOrNull { it.rawPath == p }
                    }

                    action.id.startsWith("diag:") -> {
                        val n = action.id.removePrefix("diag:")
                        diagDialog = Diag.entries.firstOrNull { it.name == n }
                    }
                }

                is SettingsRowAction.ToggleChanged ->
                    vm.setKnob(action.id, action.checked.toString())

                is SettingsRowAction.Click -> Unit
            }
        },
        contentAfterSections = {
            // 「保存」只在一级页出现一次；参数改动攒在这里一起落盘。
            val dirty = vm.knobs.count { vm.isDirty(it) }
            Box(Modifier.padding(bottom = 8.dp)) {
                MaterialTintLiquidButton(
                    text = if (dirty > 0) "保存（$dirty 项）" else "保存",
                    onClick = { vm.saveKnobs() },
                    enabled = dirty > 0,
                )
            }
            if (vm.settingsMsg.isNotBlank()) {
                Text(
                    text = vm.settingsMsg,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
    )

    knobDetail?.let { k ->
        KnobDetailDialog(
            vm = vm,
            knob = k,
            onDismiss = { knobDetail = null },
        )
    }

    if (credsDialog) {
        CredsDialog(vm = vm, onDismiss = { credsDialog = false })
    }

    if (promptDialog) {
        PromptDialog(vm = vm, onDismiss = { promptDialog = false })
    }

    diagDialog?.let { d ->
        val running = vm.diagRunning
        DiagDialog(
            title = d.label,
            lines = if (running) listOf("…") else (vm.diagResult?.lines ?: listOf("…")),
            onDismiss = {
                vm.dismissDiag()
                diagDialog = null
            },
        )
        LaunchedEffect(d) { vm.runDiag(d) }
    }
}

// --------------------------------------------------------------------------- 规格
private const val ROW_CREDS = "creds"
private const val ROW_PROMPT = "prompt"

@Composable
private fun settingsSpec(vm: AppViewModel): SettingsPageSpec {
    val c = vm.creds
    val dirty = vm.knobs.count { vm.isDirty(it) }

    return SettingsPageSpec(
        sections = listOf(
            // ── 凭证
            SettingsSectionSpec(
                title = "凭证",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    SettingsRowSpec.Navigation(
                        id = ROW_CREDS,
                        title = "API 凭证",
                        currentState = if (c.configured) "已配置" else "未配置",
                    ),
                ),
            ),

            // ── 对话参数（按 settings.py 的分组，保持它的顺序与分组）
            SettingsSectionSpec(
                title = "对话",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    knobRow(vm, "chat.model"),
                    knobRow(vm, "chat.temperature"),
                    knobRow(vm, "chat.max_tokens"),
                    knobRow(vm, "chat.effort"),
                ).filterNotNull(),
            ),

            // ── 读取与注入（新线的活开关：编写成一段 / 她再查几次 / 冷却）
            SettingsSectionSpec(
                title = "读取与注入",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    knobRow(vm, "session.render_recall"),
                    knobRow(vm, "session.max_pull"),
                    knobRow(vm, "cooldown.enabled"),
                    knobRow(vm, "cooldown.turns"),
                ).filterNotNull(),
            ),

            // ── 收场整理
            // ⛔ 2026-10-17：「归档」这一节（`dream.temperature` / `dream.turns` /
            //   `dream.resume_attempts`）删掉了 —— 那三个数控制的是**旧归档那条对话**
            //   （`DreamConfig` + `dreaming.py`），而那条路已经删了。
            //   新线的归档温度/回合在 `archive_step` 与 `writing` 里，按参数账走。

            // ── 提示词（可编辑；默认是 prompts.py 里的冻结稿，改过就存覆盖）
            SettingsSectionSpec(
                title = "提示词",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    SettingsRowSpec.Navigation(
                        id = ROW_PROMPT,
                        title = "会话提示词",
                        currentState = if (vm.promptEdited) "已改过" else "默认",
                    ),
                ),
            ),

            // ── 诊断（原来是那 4 个按钮的自检屏）
            SettingsSectionSpec(
                title = "诊断",
                layout = SettingsSectionLayout.GroupedCard,
                rows = Diag.entries.map { d ->
                    SettingsRowSpec.Navigation(
                        id = "diag:${d.name}",
                        title = d.label,
                    )
                },
            ),

            // ── ⛔ 原「手环 → 健康数据（Health Connect）」入口已删（2026-10-06，手环信源放弃；
            //      理由与留档见 `companion-data/_archive/2026-10-06-手环与HealthConnect-已放弃/`）。
            //      要补第二个独立信源，改走不依赖厂商的渠道（日历 / 通知 / 屏幕使用 / 位置…）。
        ),
        description = if (dirty > 0) "有 $dirty 项改动还没保存" else null,
    )
}

/** 一项参数 → 一行设置项（标题 + 人话当前值 + chevron；点了进详情改）。 */
@Composable
private fun knobRow(vm: AppViewModel, path: String): SettingsRowSpec? {
    val k = vm.knobs.firstOrNull { it.rawPath == path } ?: return null
    return SettingsRowSpec.Navigation(
        id = "knob:$path",
        title = k.label,
        currentState = knobValueLabel(vm, k),
    )
}

private fun knobValueLabel(vm: AppViewModel, k: Knob): String {
    val v = vm.knobValue(k)
    return when (k.kind) {
        "bool" -> if (v.toBooleanStrictOrNull() == true) "开" else "关"
        "int" -> v + if (k.unit.isNotBlank()) " ${k.unit}" else ""
        "float" -> trimNum(v) + if (k.unit.isNotBlank()) " ${k.unit}" else ""
        else -> v
    }
}

// --------------------------------------------------------------------------- 参数详情
/**
 * 参数详情弹窗：控件 + 「为什么是这个数」。
 *
 * ★ 照 zafiro 的 `SingleChoiceLiquidDialog` 形态 —— 列表直接放 `LiquidDialog` 的
 *   content（面板自带滚动与高度上限），选中即应用；**不要**再自己包一层 scroll。
 */
@Composable
private fun KnobDetailDialog(vm: AppViewModel, knob: Knob, onDismiss: () -> Unit) {
    FlatDialog(
        visible = true,
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = knob.label + if (knob.unit.isNotBlank()) "（${knob.unit}）" else "",
                style = MaterialTheme.typography.headlineSmall,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        content = {
            when (knob.kind) {
                "bool" -> SettingToggleItem(
                    title = "启用",
                    checked = vm.knobValue(knob).toBooleanStrictOrNull() ?: false,
                    onCheckedChange = { vm.setKnob(knob.rawPath, it.toString()) },
                )

                "int" -> Stepper(
                    value = vm.knobValue(knob),
                    low = (knob.low ?: 0.0).toInt().toDouble(),
                    high = (knob.high ?: 100.0).toInt().toDouble(),
                    step = intStep(knob),
                    onValue = { vm.setKnob(knob.rawPath, it.toIntOrNull()?.toString() ?: it) },
                )

                "float" -> Stepper(
                    value = vm.knobValue(knob),
                    low = (knob.low ?: 0.0),
                    high = (knob.high ?: 1.0),
                    step = floatStep(knob),
                    onValue = { vm.setKnob(knob.rawPath, it) },
                )

                else -> if (knob.choices.isNotEmpty()) {
                    knob.choices.forEach { choice ->
                        ChoiceRow(
                            label = choice,
                            selected = vm.knobValue(knob) == choice,
                            onClick = { vm.setKnob(knob.rawPath, choice) },
                        )
                    }
                } else {
                    SettingExpandableTextItem(
                        title = knob.label,
                        value = vm.knobValue(knob),
                        onValueChange = { vm.setKnob(knob.rawPath, it) },
                        placeholder = knob.label,
                        description = null,
                        minLines = 1,
                        maxLines = 3,
                    )
                }
            }

            if (knob.note.isNotBlank()) {
                WhyRow(note = knob.note, grade = knob.grade)
            }
            if (knob.changed) {
                Text(
                    text = "改动会在「保存」后生效。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        actions = {
            MaterialTintLiquidButton(
                text = "好",
                onClick = onDismiss,
                modifier = Modifier.fillMaxWidth(),
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            )
        },
    )
}

/** 数值步进器：`−` 值 `+`。比滑块更准，也不用为了拖到某一位而纠结。 */
@Composable
private fun Stepper(
    value: String,
    low: Double,
    high: Double,
    step: Double,
    onValue: (String) -> Unit,
) {
    val v = value.toDoubleOrNull() ?: low
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = trimNum(fmt(v, decimalsFor(low).coerceAtLeast(decimalsFor(high)).coerceIn(0, 4))),
            style = MaterialTheme.typography.headlineSmall,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            MaterialTintLiquidButton(
                text = "−",
                onClick = {
                    val nv = (v - step).coerceAtLeast(low)
                    onValue(trimNum(fmt(nv, 6)))
                },
                modifier = Modifier.weight(1f),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                contentColor = MaterialTheme.colorScheme.onSurface,
            )
            MaterialTintLiquidButton(
                text = "+",
                onClick = {
                    val nv = (v + step).coerceAtMost(high)
                    onValue(trimNum(fmt(nv, 6)))
                },
                modifier = Modifier.weight(1f),
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            )
        }
    }
}

private fun floatStep(k: Knob): Double {
    val span = ((k.high ?: 1.0) - (k.low ?: 0.0)).takeIf { it > 0 } ?: 1.0
    return when {
        span >= 100 -> 1.0
        span >= 10 -> 0.1
        span >= 1 -> 0.01
        else -> 0.001
    }
}

/**
 * 整数旋钮的步长 —— **跟着量程走**。
 *
 * ★ 2026-10-06 修：这里原来是**硬编码 `1.0`** ⇒ `chat.max_tokens`（1000–32000）
 *   按一下加号**只 +1**，从 6000 调到 10000 要按 **4000 次**（用户实测报的）。
 *   现在与 [floatStep] 对称：只有量程大的那一个拿到大步长，其余仍是 1 ——
 *   **不改变其它 int 旋钮的手感**（它们的 span 都 < 100）。
 */
private fun intStep(k: Knob): Double {
    val span = ((k.high ?: 100.0) - (k.low ?: 0.0)).takeIf { it > 0 } ?: 1.0
    return when {
        span >= 1000 -> 500.0
        span >= 100 -> 10.0
        else -> 1.0
    }
}

@Composable
private fun ChoiceRow(label: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            Modifier
                .size(20.dp)
                .clickable(onClick = onClick),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = if (selected) Icons.Default.Check else Icons.Default.RadioButtonUnchecked,
                contentDescription = null,
                modifier = Modifier.size(selected.let { if (it) 18.dp else 16.dp }),
                tint = if (selected) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.45f)
                },
            )
        }
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.clickable(onClick = onClick),
        )
    }
}

/** 「为什么是这个数」：默认收起，点开才给依据等级 + 那条 note。 */
@Composable
private fun WhyRow(note: String, grade: String) {
    var open by remember(note) { mutableStateOf(false) }
    Column {
        Row(
            modifier = Modifier
                .padding(vertical = 4.dp)
                .clickable { open = !open },
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Icon(
                imageVector = if (open) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                contentDescription = null,
                modifier = Modifier.size(12.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "为什么是这个数",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (open) {
            Text(
                text = note,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 2.dp, bottom = 2.dp),
            )
        }
    }
}

// --------------------------------------------------------------------------- 凭证
@Composable
private fun CredsDialog(vm: AppViewModel, onDismiss: () -> Unit) {
    val c = vm.creds
    FlatDialog(
        visible = true,
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = "API 凭证",
                style = MaterialTheme.typography.headlineSmall,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        content = {
            if (c.err.isNotBlank()) {
                Text(
                    text = c.err,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            SettingExpandableTextItem(
                title = "接入地址",
                value = vm.embedBaseUrlDraft,
                onValueChange = vm::updateEmbedBaseUrl,
                placeholder = "https://…",
                minLines = 1,
                maxLines = 2,
            )
            // ★ 密钥绝不回显：输入框永远从空开始，"已设置"只是一个标记。
            SettingExpandableTextItem(
                title = "编码器 Key" + if (c.hasEmbedKey) "（已设置）" else "（未设置）",
                value = vm.embedKeyDraft,
                onValueChange = vm::updateEmbedKey,
                placeholder = if (c.hasEmbedKey) "留空 = 不动" else "粘贴",
                minLines = 1,
                maxLines = 2,
                secretVisible = false,
            )
            SettingExpandableTextItem(
                title = "对话 Key" + if (c.hasChatKey) "（已设置）" else "（未设置）",
                value = vm.chatKeyDraft,
                onValueChange = vm::updateChatKey,
                placeholder = if (c.hasChatKey) "留空 = 不动" else "粘贴",
                minLines = 1,
                maxLines = 2,
            )
            if (vm.settingsMsg.isNotBlank()) {
                Text(
                    text = vm.settingsMsg,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        actions = {
            MaterialTintLiquidButton(
                text = "保存",
                onClick = { vm.saveCreds() },
                modifier = Modifier.fillMaxWidth(),
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
            )
        },
    )
}

// --------------------------------------------------------------------------- 提示词
/**
 * 会话提示词的编辑器。
 *
 * ★ 默认值（`companion/prompts.py` 里的**冻结稿**）留在代码里，这里改的是**覆盖**：
 *   存盘只放"他改过的那一份" ⇒ 默认改了能传下去，改过的也能一键回到默认。
 * ★ 改提示词 = 改行为，所以界面上要能一眼看出"现在跑的不是冻结稿"。
 */
@Composable
private fun PromptDialog(vm: AppViewModel, onDismiss: () -> Unit) {
    FlatDialog(
        visible = true,
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = "会话提示词",
                style = MaterialTheme.typography.headlineSmall,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        content = {
            Text(
                text = if (vm.promptEdited) "现在跑的是你改过的版本，不是默认稿。"
                else "现在是默认稿。改了就等于改了它的行为。",
                style = MaterialTheme.typography.bodySmall,
                color = if (vm.promptEdited) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.onSurfaceVariant,
            )
            SettingExpandableTextItem(
                title = "① 处境与用法",
                value = vm.promptSys1,
                onValueChange = vm::updatePromptSys1,
                placeholder = "你在和用户自然对话…",
                minLines = 3,
                maxLines = 10,
            )
            SettingExpandableTextItem(
                title = "② 分寸",
                value = vm.promptSys2,
                onValueChange = vm::updatePromptSys2,
                placeholder = "- 不派活…",
                minLines = 4,
                maxLines = 14,
            )
            if (vm.settingsMsg.isNotBlank()) {
                Text(
                    text = vm.settingsMsg,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        actions = {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                MaterialTintLiquidButton(
                    text = "恢复默认",
                    onClick = { vm.restoreDefaultPrompt() },
                    modifier = Modifier.weight(1f),
                )
                MaterialTintLiquidButton(
                    text = "保存",
                    onClick = { vm.savePrompt() },
                    modifier = Modifier.weight(1f),
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary,
                )
            }
        },
    )
}

// --------------------------------------------------------------------------- 诊断
@Composable
private fun DiagDialog(title: String, lines: List<String>, onDismiss: () -> Unit) {
    FlatDialog(
        visible = true,
        onDismissRequest = onDismiss,
        title = {
            Text(
                text = title,
                style = MaterialTheme.typography.headlineSmall,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        content = {
            if (lines.size == 1 && lines.first() == "…") {
                CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
            } else {
                lines.forEach { line ->
                    Text(
                        text = line,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
        },
        actions = {
            MaterialTintLiquidButton(
                text = "好",
                onClick = onDismiss,
                modifier = Modifier.fillMaxWidth(),
                containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                contentColor = MaterialTheme.colorScheme.onSurface,
            )
        },
    )
}
