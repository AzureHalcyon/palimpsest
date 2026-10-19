package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.Surface
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp

/**
 * 设置里的**二级页面**（2026-10-18，作者：「需要做二级页面」）。
 *
 * 为什么不再用弹窗：这四样东西**内容多、要来回改、要对照**（参数账的可改项 / 提示词九段全文 /
 * 模型列表 / 改动记录），弹窗装不下也翻不动。二级页就是把它们各给一屏，左上角返回。
 *
 * ⛔ 这里一个表情符号都不放（作者：所有地方都不要用 emoji）。
 */


// --------------------------------------------------------------------------- 外壳
@Composable
private fun 二级页(
    标题: String,
    副标题: String = "",
    onBack: () -> Unit,
    content: @Composable ColumnScope.() -> Unit,
) {
    // ★ 2026-10-18（作者：颜色反了）：一级是**浅白底 + 浅灰卡**；
    //   这里原来把页面底设成了 `surfaceContainerHighest`（最深的那一档）⇒ 整页发灰。
    //   ⇒ 页面底交回默认，卡片用 `surfaceContainer`（与一级页同一档）。
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 6.dp, end = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
            }
            Column(Modifier.fillMaxWidth()) {
                Text(标题, style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold)
                if (副标题.isNotBlank()) {
                    Text(副标题, style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        HorizontalDivider()
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 10.dp),
            content = content,
        )
    }
}

/**
 * 账本里的文字进界面前，去掉表情/强调符号。
 *
 * 仓库里的代码注释保留那些记号（给改代码的人看），但**界面上不许出现**（作者口径）。
 * 这里兜的是显示层：`⛔` / `★` / 表情都会被删掉，只留正文。
 */
private fun 去符号(s: String): String {
    // 不用正则：一是码位要跨 BMP（`\u{1F300}` 在 Java 的 Pattern 里不成立），
    // 二是这里只做一件事 —— 把表情与强调符号从**显示**里删掉。
    val sb = StringBuilder()
    var i = 0
    while (i < s.length) {
        val cp = s.codePointAt(i)
        val 删 = cp in 0x1F300..0x1FAFF || cp in 0x2600..0x27BF ||
            cp in 0x2B00..0x2BFF || cp == 0xFE0F || cp == 0x26D4 || cp == 0x2605
        if (!删) sb.appendCodePoint(cp)
        i += Character.charCount(cp)
    }
    return sb.toString()
}

/** 一张卡：和一级页同一种底色、圆角与内边距（作者：二级页的主题要和一级一致）。 */
@Composable
private fun 卡(内容: @Composable ColumnScope.() -> Unit) {
    Surface(
        // ★ 2026-10-18（作者：二级的卡片比一级深）：一级页的卡是 `surfaceContainer`，
        //   页面底才是 `surfaceContainerHighest` —— 这里跟它对齐。
        color = MaterialTheme.colorScheme.surfaceContainer,
        shape = RoundedCornerShape(20.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 2.dp), content = 内容)
    }
}

/** 小标题：靠字号与留白分层（与设置页那张卡同一种写法）。 */
@Composable
private fun 分节(文本: String) {
    Text(
        文本,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 18.dp, bottom = 6.dp),
    )
}

/** 一行「标签 + 当前值」，点了做点什么。 */
@Composable
private fun 行(
    标签: String,
    值: String,
    说明: String = "",
    onClick: (() -> Unit)? = null,
) {
    Column(
        Modifier.fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(vertical = 10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(标签, style = MaterialTheme.typography.bodyLarge)
            Spacer(Modifier.weight(1f))
            Text(值, style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary)
        }
        if (说明.isNotBlank()) {
            Text(说明, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 2.dp))
        }
    }
    HorizontalDivider()
}

// --------------------------------------------------------------------------- API 与模型
/**
 * 模型行：**一个输入框 + 两个按钮**（存 / 从列表挑）。
 *
 * ★ 2026-10-18（作者）：「编写模型这就没法填」「拉取模型应该放在一个气泡里展示，
 *   不然页面排版会变」⇒ ① 三家模型**各自可填**（不再只有"从列表挑"这一条路）；
 *   ② 列表走弹窗，⛔ 不往页面上摊（摊一次，整页就动一次）。
 */
@Composable
private fun 模型行(
    标签: String,
    值: String,
    onValue: (String) -> Unit,
    onSave: () -> Unit,
    onPick: (() -> Unit)? = null,
) {
    OutlinedTextField(
        value = 值,
        onValueChange = onValue,
        label = { Text(标签) },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Row(verticalAlignment = Alignment.CenterVertically) {
        TextButton(onClick = onSave) { Text("存") }
        if (onPick != null) {
            TextButton(onClick = onPick) { Text("从列表挑") }
        }
    }
}

@Composable
fun ApiPage(vm: AppViewModel, onKnob: (String) -> Unit, onBack: () -> Unit) {
    LaunchedEffect(Unit) { vm.loadCreds(); if (vm.账.isEmpty()) vm.loadLedger() }
    val c = vm.creds

    var 嵌入地址 by remember(c) { mutableStateOf(c.embedBaseUrl) }
    var 嵌入密钥 by remember(c) { mutableStateOf("") }
    var 对话地址 by remember(c) { mutableStateOf(c.chatUrl) }
    var 对话密钥 by remember(c) { mutableStateOf("") }

    // ★ 三家模型各自一份草稿：**都能直接敲**，不靠列表
    var 嵌入模型 by remember(vm.账) {
        mutableStateOf(vm.账.firstOrNull { it.键 == "嵌入.模型" }?.值.orEmpty())
    }
    var 对话模型 by remember(vm.knobs) { mutableStateOf(vm.knobValueOf("chat.model")) }
    var 编写模型 by remember(vm.账) {
        mutableStateOf(vm.账.firstOrNull { it.键 == "编写.模型" }?.值.orEmpty())
    }

    var 列表给谁 by remember { mutableStateOf("") }     // "" / embed / chat / writing
    var 编辑 by remember { mutableStateOf<账项?>(null) }

    fun 存模型(给谁: String) {
        when (给谁) {
            "embed" -> vm.setLedger("嵌入.模型", 嵌入模型)
            "chat" -> { vm.setKnob("chat.model", 对话模型.trim()); vm.saveKnobs() }
            else -> vm.setLedger("编写.模型", 编写模型.trim())
        }
    }

    二级页(
        标题 = "API 与模型",
        副标题 = "嵌入 · 对话 · 编写",
        onBack = onBack,
    ) {
        // ---------------------------------------------------------------- 嵌入
        分节("嵌入 · 取向量")
        卡 {
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = 嵌入地址,
            onValueChange = { 嵌入地址 = it; vm.updateEmbedBaseUrl(it) },
            label = { Text("地址（含 /v1）") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = 嵌入密钥,
            onValueChange = { 嵌入密钥 = it; vm.updateEmbedKey(it) },
            label = { Text("嵌入密钥") },
            singleLine = true,
            supportingText = {
                Text(
                    if (c.embedKeyHint.isNotBlank()) "已设置：${c.embedKeyHint}（留空 = 不改）"
                    else "未设置",
                )
            },
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        模型行(
            标签 = "嵌入模型",
            值 = 嵌入模型,
            onValue = { 嵌入模型 = it },
            onSave = { 存模型("embed") },
            onPick = { vm.loadModels("embed"); 列表给谁 = "embed" },
        )
        TextButton(onClick = { vm.saveCreds() }, enabled = !vm.settingsLoading) {
            Text(if (vm.settingsLoading) "保存中…" else "保存")
        }
        if (vm.嵌入模型错误.isNotBlank()) {
            Text(vm.嵌入模型错误, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error)
        }
        if (vm.嵌入模型端点.isNotBlank()) {
            Text("列表来源：${vm.嵌入模型端点}", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (vm.拉嵌入模型中) {
            Text("正在问…", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        // ---------------------------------------------------------------- 对话
        }
        分节("对话 · 说与想")
        卡 {
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = 对话地址,
            onValueChange = { 对话地址 = it; vm.updateChatUrl(it) },
            label = { Text("对话地址（完整 URL）") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = 对话密钥,
            onValueChange = { 对话密钥 = it; vm.updateChatKey(it) },
            label = { Text("对话密钥") },
            singleLine = true,
            supportingText = {
                Text(
                    if (c.chatKeyHint.isNotBlank()) "已设置：${c.chatKeyHint}（留空 = 不改）"
                    else "未设置",
                )
            },
            modifier = Modifier.fillMaxWidth(),
        )
        TextButton(onClick = { vm.saveCreds() }, enabled = !vm.settingsLoading) {
            Text("保存")
        }
        Spacer(Modifier.height(8.dp))
        模型行(
            标签 = "对话模型",
            值 = 对话模型,
            onValue = { 对话模型 = it },
            onSave = { 存模型("chat") },
            onPick = { vm.loadModels("chat"); 列表给谁 = "chat" },
        )
        if (vm.模型错误.isNotBlank()) {
            Text(vm.模型错误, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error)
        }
        if (vm.模型端点.isNotBlank()) {
            Text("列表来源：${vm.模型端点}", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (vm.拉模型中) {
            Text("正在问…", style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        行(标签 = "温度", 值 = vm.knobValueOf("chat.temperature"),
            onClick = { onKnob("chat.temperature") })
        行(标签 = "回复上限", 值 = vm.knobValueOf("chat.max_tokens") + " token",
            onClick = { onKnob("chat.max_tokens") })
        行(标签 = "思考档位", 值 = vm.knobValueOf("chat.effort"),
            onClick = { onKnob("chat.effort") })

        // ---------------------------------------------------------------- 编写
        }
        分节("编写 · 把取到的写成一段")
        卡 {
        Spacer(Modifier.height(8.dp))
        模型行(
            标签 = "编写模型（空 = 同对话）",
            值 = 编写模型,
            onValue = { 编写模型 = it },
            onSave = { 存模型("writing") },
            onPick = { vm.loadModels("chat"); 列表给谁 = "writing" },
        )
        listOf("编写.温度", "编写.判定温度").forEach { 键 ->
            val 项 = vm.账.firstOrNull { it.键 == 键 }
            行(
                标签 = 键.removePrefix("编写."),
                值 = 项?.显示值 ?: "—",
                说明 = "",
                onClick = { if (项 != null) 编辑 = 项 },
            )
        }
        if (vm.账.isEmpty()) {
            Text(
                if (vm.账错误.isNotBlank()) vm.账错误 else "参数账读取中…",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        }
    }

// ---- 模型列表：**弹窗**（⛔ 摊在页面上会把整个排版顶下去）
    if (列表给谁.isNotBlank()) {
        val 列表 = if (列表给谁 == "embed") vm.嵌入模型们 else vm.模型们
        AlertDialog(
            onDismissRequest = { 列表给谁 = "" },
            title = {
                Text(
                    when (列表给谁) {
                        "embed" -> "嵌入模型"
                        "chat" -> "对话模型"
                        else -> "编写模型"
                    },
                )
            },
            text = {
                Column(
                    Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()),
                ) {
                    if (列表.isEmpty()) {
                        Text("还没拉到列表 —— 关掉再点一次「从列表挑」。")
                    }
                    列表.forEach { m ->
                        行(
                            标签 = m.id,
                            值 = when (列表给谁) {
                                "embed" -> if (m.id == 嵌入模型) "在用" else "选用"
                                "chat" -> if (m.id == 对话模型) "在用" else "选用"
                                else -> if (m.id == 编写模型) "在用" else "选用"
                            },
                            onClick = {
                                when (列表给谁) {
                                    "embed" -> { 嵌入模型 = m.id; 存模型("embed") }
                                    "chat" -> { 对话模型 = m.id; 存模型("chat") }
                                    else -> { 编写模型 = m.id; 存模型("writing") }
                                }
                                列表给谁 = ""
                            },
                        )
                    }
                }
            },
            confirmButton = { TextButton(onClick = { 列表给谁 = "" }) { Text("关") } },
        )
    }

    编辑?.let { k -> 改一项(vm = vm, 项 = k, onDismiss = { 编辑 = null }) }
}

// --------------------------------------------------------------------------- 参数
@Composable
fun LedgerPage(vm: AppViewModel, onBack: () -> Unit) {
    LaunchedEffect(Unit) { vm.loadLedger() }
    var 选组 by remember { mutableStateOf("") }
    var 编辑 by remember { mutableStateOf<账项?>(null) }

    // 只列「能改」的；「写回忆」那一组在「API 与模型」里配过，这里不重复（作者：会混）。
    val 可改们 = vm.账.filter { it.可改 && it.组 != "编写" && it.组 != "嵌入" }
    val 组名们 = 可改们.map { it.组名 }.distinct()
    val 当前组 = 选组.ifBlank { 组名们.firstOrNull().orEmpty() }

    二级页(标题 = "参数", onBack = onBack) {
        if (vm.账错误.isNotBlank()) {
            Text(vm.账错误, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error)
        }
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            组名们.forEach { g ->
                Text(
                    g,
                    style = MaterialTheme.typography.labelLarge,
                    fontWeight = if (g == 当前组) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (g == 当前组) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.clickable { 选组 = g }.padding(vertical = 6.dp),
                )
            }
        }
        Spacer(Modifier.height(4.dp))
        卡 {
            可改们.filter { it.组名 == 当前组 }.forEach { 项 ->
                行(
                    标签 = 去符号(项.名),
                    值 = 项.显示值,
                    onClick = { 编辑 = 项 },
                )
            }
        }
        if (vm.账加载中 && vm.账.isEmpty()) {
            Text("读取中…", style = MaterialTheme.typography.labelSmall)
        }
    }

    编辑?.let { k -> 改一项(vm = vm, 项 = k, onDismiss = { 编辑 = null }) }
}

@Composable
private fun 改一项(vm: AppViewModel, 项: 账项, onDismiss: () -> Unit) {
    var 文本 by remember(项.键) { mutableStateOf(项.值) }
    val 错误 = vm.账提示[项.键].orEmpty()
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(去符号(项.名)) },
        text = {
            Column {
                if (项.类型 == "bool") {
                    // 开关型的参数不给输入框（别让人去敲 true/false）
                    listOf("开" to "true", "关" to "false").forEach { (标签, 值) ->
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                vm.setLedger(项.键, 值) { err -> if (err.isBlank()) onDismiss() }
                            }.padding(vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                标签,
                                style = MaterialTheme.typography.bodyLarge,
                                color = if (项.值 == 值) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.onSurface,
                            )
                            if (项.值 == 值) {
                                Spacer(Modifier.weight(1f))
                                Text("现在", style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary)
                            }
                        }
                    }
                } else if (项.可填.isNotEmpty()) {
                    // 能填的就那几个 ⇒ 直接给选项（作者：像「误差_推理」这种，得说清能填什么）
                    项.可填.forEach { c ->
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                vm.setLedger(项.键, c) { err -> if (err.isBlank()) onDismiss() }
                            }.padding(vertical = 10.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                c,
                                style = MaterialTheme.typography.bodyLarge,
                                color = if (c == 项.值) MaterialTheme.colorScheme.primary
                                else MaterialTheme.colorScheme.onSurface,
                            )
                            if (c == 项.值) {
                                Spacer(Modifier.weight(1f))
                                Text("现在", style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.primary)
                            }
                        }
                    }
                } else {
                    OutlinedTextField(
                        value = 文本,
                        onValueChange = { 文本 = it },
                        label = { Text(if (项.类型 == "bool") "开 / 关" else "值") },
                        singleLine = true,
                        isError = 错误.isNotBlank(),
                        // ★ 有错先报错；没错就把「怎么填」那一句显示出来（作者：说明还是没有）
                        supportingText = when {
                            错误.isNotBlank() -> {
                                { Text(错误) }
                            }

                            项.提示.isNotBlank() -> {
                                { Text(去符号(项.提示)) }
                            }

                            else -> null
                        },
                        keyboardOptions = KeyboardOptions(
                            keyboardType = when (项.类型) {
                                "int", "float" -> KeyboardType.Number
                                else -> KeyboardType.Text
                            },
                        ),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                if (项.提示.isNotBlank()) {
                    Text(
                        项.提示,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
        },
        confirmButton = {
            if (项.可填.isEmpty() && 项.类型 != "bool") {
                TextButton(onClick = {
                    vm.setLedger(项.键, 文本) { err -> if (err.isBlank()) onDismiss() }
                }) { Text("保存") }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(if (项.可填.isEmpty() && 项.类型 != "bool") "算了" else "关")
            }
        },
    )
}

// --------------------------------------------------------------------------- 改动记录
@Composable
fun LedgerLogPage(vm: AppViewModel, onBack: () -> Unit) {
    LaunchedEffect(Unit) { vm.loadLedgerChanges() }
    二级页(标题 = "改动记录", 副标题 = "新在前", onBack = onBack) {
        if (vm.改动错误.isNotBlank()) {
            Text(vm.改动错误, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error)
        }
        if (vm.账改动.isEmpty()) {
            Text(
                if (vm.改动加载中) "读取中…" else "还没有改过任何一项。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(vertical = 16.dp),
            )
        }
        // 外层已经是可滚动 Column ⇒ 这里不能再塞 LazyColumn（高度会变成无限，直接崩）
        卡 {
        vm.账改动.forEach { d ->
            Column(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
                Text(d.键, style = MaterialTheme.typography.bodyLarge)
                Text(
                    "${d.旧} → ${d.新}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
                Text(d.时间, style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            HorizontalDivider()
        }
        }
    }
}

// --------------------------------------------------------------------------- 提示词清单
@Composable
fun PromptsPage(vm: AppViewModel, onOpen: (String) -> Unit, onBack: () -> Unit) {
    LaunchedEffect(Unit) { vm.loadPrompts() }
    二级页(
        标题 = "提示词",
        副标题 = "",
        onBack = onBack,
    ) {
        if (vm.提示词错误.isNotBlank()) {
            Text(vm.提示词错误, style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error)
        }
        vm.提示词组们.forEach { 组 ->
            分节(组.组)
            卡 {
            组.项.forEach { 项 ->
                行(
                    标签 = 项.名称,
                    值 = if (项.已改) "改过" else "默认",
                    说明 = if (项.占位符.isEmpty()) "占位符：无"
                    else "占位符：" + 项.占位符.joinToString(" ") { "$$it" },
                    onClick = { onOpen(项.id) },
                )
            }
            }
        }
        if (vm.提示词加载中 && vm.提示词组们.isEmpty()) {
            Text("读取中…", style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
fun PromptEditPage(vm: AppViewModel, id: String, onBack: () -> Unit) {
    LaunchedEffect(id) { vm.loadPrompt(id) }
    val 屏 = vm.提示词屏
    var 文本 by remember(屏?.id, 屏?.正文) { mutableStateOf(屏?.正文 ?: "") }
    var 消息 by remember { mutableStateOf("") }

    二级页(
        标题 = 屏?.名称 ?: "提示词",
        副标题 = buildString {
            if (屏 != null) {
                if (屏.已改) append("改过 · ") else append("默认 · ")
                if (屏.占位符.isEmpty()) append("占位符：无")
                else append("占位符：" + 屏.占位符.joinToString(" ") { "$$it" })
            }
        },
        onBack = onBack,
    ) {
        if (屏?.说明?.isNotBlank() == true) {
            Column(Modifier.padding(bottom = 8.dp)) { MarkdownText(markdown = 屏.说明) }
        }
        if (屏 == null) {
            Text(if (vm.提示词屏加载中) "读取中…" else "读不出来",
                style = MaterialTheme.typography.bodyMedium)
            return@二级页
        }
        OutlinedTextField(
            value = 文本,
            onValueChange = { 文本 = it },
            modifier = Modifier.fillMaxWidth().height(420.dp),
            textStyle = MaterialTheme.typography.bodyMedium,
        )
        if (消息.isNotBlank()) {
            Text(消息, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(top = 6.dp))
        }
        Row(
            Modifier.padding(top = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            TextButton(onClick = {
                vm.savePrompt(id, 文本) { err -> 消息 = if (err.isBlank()) "已保存" else err }
            }) { Text("保存") }
            TextButton(onClick = {
                文本 = 屏.默认
                vm.resetPrompt(id) { err -> 消息 = if (err.isBlank()) "已恢复默认" else err }
            }) { Text("恢复默认") }
        }
        Text(
            "保存前会校验占位符：少了或多了都不存 —— 少一个占位符，那一处就是空白。",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 6.dp),
        )
    }
}
