package com.azurehalcyon.palimpsest.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
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
    var diagDialog by remember { mutableStateOf<Diag?>(null) }
    // ★ 2026-10-18：四样东西各给一屏（作者：「需要做二级页面」）—— 弹窗装不下也翻不动。
    var 子页 by remember { mutableStateOf("") }
    var 提示词id by remember { mutableStateOf("") }
    // ★ 2026-10-19（作者：设置里也记得加上）：旧场（只读）也留一个入口
    var 旧场sid by remember { mutableStateOf("") }

    // ★★ 2026-10-18（真机踩到）：这个对话框原来挂在下面 —— 而二级页是**早返回**的，
    //   所以「API 与模型」里点温度/回复上限**什么都不会发生**。对话框是覆盖层，
    //   放在这里与布局无关，反而在哪一页都弹得出来。
    knobDetail?.let { k ->
        KnobDetailDialog(
            vm = vm,
            knob = k,
            onDismiss = { knobDetail = null },
        )
    }

    if (子页.isNotBlank()) {
        // ★ 系统返回键也要能回到上一级（否则它会一路退到外壳，二级页卡在那儿）
        BackHandler {
            when {
                旧场sid.isNotBlank() -> 旧场sid = ""
                提示词id.isNotBlank() -> 提示词id = ""
                else -> 子页 = ""
            }
        }
        when (子页) {
            "api" -> ApiPage(vm, onKnob = { path ->
            knobDetail = vm.knobs.firstOrNull { it.rawPath == path }
        }, onBack = { 子页 = "" })
            "ledger" -> LedgerPage(vm) { 子页 = "" }
            "log" -> LedgerLogPage(vm) { 子页 = "" }
            "past" ->
                if (旧场sid.isBlank()) {
                    PastListPage(vm, onBack = { 子页 = "" }, onOpen = { sid ->
                        旧场sid = sid; vm.loadPast(sid)
                    })
                } else {
                    PastDetailPage(vm, onBack = { 旧场sid = "" })
                }
            "prompts" ->
                if (提示词id.isBlank()) {
                    PromptsPage(vm, onOpen = { 提示词id = it }, onBack = { 子页 = "" })
                } else {
                    PromptEditPage(vm, 提示词id, onBack = { 提示词id = "" })
                }
        }
        return
    }

    SettingsSpecPageContent(
        spec = settingsSpec(vm),
        modifier = modifier,
        onAction = { action ->
            when (action) {
                is SettingsRowAction.Navigate -> when {
                    action.id.startsWith("page:") -> {
                        提示词id = ""
                        子页 = action.id.removePrefix("page:")
                    }

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
        // ★ 2026-10-18（作者：最下面那个保存按钮不知道是给谁用的）：
        //   一级页不再有「保存」—— 每个二级页自己保存，改哪一项、按哪一次都看得见。
        //   但**必须留一点底**：最后一行（在途场次）原来紧贴下边缘。
        contentAfterSections = { Spacer(Modifier.height(28.dp)) },
    )










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
// ★ 2026-10-18：四样东西从弹窗改成**二级页面**（入口就是下面几个 `page:` 行）。
private const val PAGE_API = "page:api"
private const val PAGE_LEDGER = "page:ledger"
private const val PAGE_LOG = "page:log"
private const val PAGE_PROMPTS = "page:prompts"

@Composable
private fun settingsSpec(vm: AppViewModel): SettingsPageSpec {
    val c = vm.creds
    val dirty = vm.knobs.count { vm.isDirty(it) }

    return SettingsPageSpec(
        sections = listOf(
            // ── 端点与模型（地址 / 密钥 / 拉可用列表 / 选对话模型）
            SettingsSectionSpec(
                title = "API 与模型",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    SettingsRowSpec.Navigation(
                        id = PAGE_API,
                        title = "端点与模型",
                        currentState = if (c.configured) "已配置" else "未配置",
                    ),
                ),
            ),

            // ── 读取与注入（新线的活开关：编写成一段 / 她再查几次 / 冷却）
            SettingsSectionSpec(
                title = "读取与注入",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    // ★ 2026-10-18（作者：这些开关给得不合理）：只留两个**能改变行为**的；
                    //   「注入写成一段」「冷却开关/轮数」是排查用的，进「参数」里，不占这一页。
                    knobRow(vm, "session.auto_push"),
                    knobRow(vm, "session.max_pull"),
                ).filterNotNull(),
            ),

            // ── 参数账（48 项，可改）与改动记录
            SettingsSectionSpec(
                title = "参数",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    SettingsRowSpec.Navigation(
                        id = PAGE_LEDGER,
                        title = "查看与修改",
                        currentState = if (vm.账计数 > 0) "${vm.账计数} 项" else "",
                    ),
                    SettingsRowSpec.Navigation(id = PAGE_LOG, title = "改动记录"),
                ),
            ),

            // ── 收场整理
            // ⛔ 2026-10-17：「归档」这一节（`dream.temperature` / `dream.turns` /
            //   `dream.resume_attempts`）删掉了 —— 那三个数控制的是**旧归档那条对话**
            //   （`DreamConfig` + `dreaming.py`），而那条路已经删了。
            //   新线的归档温度/回合在 `archive_step` 与 `writing` 里，按参数账走。

            // ── 提示词（全部可编辑；默认正文在仓库的 `memory/提示词/*.txt`，改过的存这台手机上）
            SettingsSectionSpec(
                title = "提示词",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    SettingsRowSpec.Navigation(id = PAGE_PROMPTS, title = "提示词"),
                ),
            ),

            // ── 旧场（已归档的场，只读：系统提示 / 常驻 / 在场 / 每轮 / 归档流程）
            SettingsSectionSpec(
                title = "旧场",
                layout = SettingsSectionLayout.GroupedCard,
                rows = listOf(
                    SettingsRowSpec.Navigation(id = "page:past", title = "看已归档的场"),
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
            //      理由与留档不随本仓）。
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

/**
 * 校验一个数字输入（作者：「记得做一个简单的输入校验」）。
 *
 * 只判三件事：空 / 不是数字 / 整数项填了小数。**范围只提示、不夹住** ——
 * 账上的 `low/high` 是给滑块的，不是一个不许越过的墙。
 */
private fun 校验数字(s: String, k: Knob): String {
    val t = s.trim()
    if (t.isEmpty()) return "还没填"
    val v = t.toDoubleOrNull() ?: return "这不是一个数字"
    if (v.isNaN() || v.isInfinite()) return "要是有限的数字"
    if (k.kind == "int" && v != Math.floor(v)) return "这一项要整数"
    return ""
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

                // ★ 2026-10-18（作者：「所有旋钮都不要那个加号减号，直接一个框自己填」）：
                //   int / float 都换成一个输入框，按 kind 校验，**非法就当场说，不静默钳**。
                "int", "float" -> {
                    var 文本 by remember(knob.rawPath) { mutableStateOf(vm.knobValue(knob)) }
                    val 错 = 校验数字(文本, knob)
                    androidx.compose.material3.OutlinedTextField(
                        value = 文本,
                        onValueChange = { 文本 = it },
                        label = { Text(if (knob.unit.isNotBlank()) knob.unit else "值") },
                        singleLine = true,
                        isError = 错.isNotBlank(),
                        // 正常时不加任何说明；只有填错才说一句（⛔ 不要把"自己填、不夹住"摆给人看）
                        supportingText = if (错.isBlank()) null else {
                            { Text(错) }
                        },
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                            keyboardType = if (knob.kind == "int") {
                                androidx.compose.ui.text.input.KeyboardType.Number
                            } else {
                                androidx.compose.ui.text.input.KeyboardType.Decimal
                            },
                        ),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    MaterialTintLiquidButton(
                        text = "填好了",
                        onClick = {
                            if (错.isBlank()) {
                                vm.setKnob(knob.rawPath, 文本)
                                onDismiss()
                            }
                        },
                        enabled = 错.isBlank(),
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                        containerColor = MaterialTheme.colorScheme.primary,
                        contentColor = MaterialTheme.colorScheme.onPrimary,
                    )
                }

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
            // ★ 账本的 note 是 markdown（`**加粗**` / 反引号 / 列表）⇒ 用渲染器，
            //   否则记号原样露出来（真机截图里就是一片 `**待标定**`）。
            Column(Modifier.padding(top = 2.dp, bottom = 2.dp)) {
                MarkdownText(markdown = note)
            }
        }
    }
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
