package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * ★★ 记忆页 = **件 / 组 / 势** 三面（2026-10-18 补齐新线）。
 *
 * 旧的四档池子（短期 / 中期 / 悬着 / 长期）在新线里**不存在**：底层现在是
 * `cell（格）→ 件（跨场的落点）/ 组（依赖簇，按场算）→ 标记集`，另有一份**势**。
 *
 * 三个签各回答一个问题（**别混**）：
 *  1. **件** —— 记住的"东西"：强度来自标记集，由哪几段原文组成、点开看原话；
 *  2. **组** —— 归档**分对了吗**：依赖簇按场排，一场里相邻的那几轮算一段；
 *  3. **势** —— **下回往哪儿流**：读取路径的 EMA，只引导传播，⛔ 不是准入、⛔ 不是存不存。
 *
 * ⛔ 这一屏只读（长按件那条「剪出 / 删掉」除外）。组与势都**没有**手改入口 ——
 *   它们是算出来的，改它们等于改读法，不在这块屏做。
 */
@Composable
fun GraphScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    LaunchedEffect(Unit) { if (vm.graph == null && !vm.graphLoading) vm.loadGraph() }
    val g = vm.graph
    var 签 by remember { mutableIntStateOf(0) }
    LazyColumn(modifier = modifier.fillMaxWidth().padding(horizontal = 14.dp)) {
        item {
            Column(Modifier.padding(vertical = 12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("记忆", style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.weight(1f))
                    Text(if (g == null) "读取中…" else "图版本 ${g.版本}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    if (g == null) "" else
                        "${g.cell数} 格 · ${g.件数} 件 · ${g.组数} 组 · " +
                            "在标记集 ${g.标记集数} · 势 ${g.势数}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (vm.graphErr.isNotBlank()) {
                    Spacer(Modifier.height(6.dp))
                    Text(vm.graphErr, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
                Spacer(Modifier.height(10.dp))
                签们(选 = 签, on选 = { 签 = it })
            }
            HorizontalDivider()

            // ★ 2026-10-19（作者：之前记忆页是能查记忆的）：只读探针 ——
            //   给一句话，走同一条 `[R]`，看会亮什么；**全量激活** + 件取用/未取用。
            //   ⛔ 不写图、⛔ 不动势。
            Column(Modifier.padding(vertical = 10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = vm.查线索,
                        onValueChange = { vm.查线索 = it },
                        label = { Text("查一查：这句话去回想会亮什么") },
                        singleLine = true,
                        // ★ 回车就查（键盘弹出时按钮常被挡住）
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                        keyboardActions = KeyboardActions(onSearch = { vm.probe() }),
                        modifier = Modifier.weight(1f),
                    )
                    Spacer(Modifier.width(8.dp))
                    Button(
                        onClick = { vm.probe() },
                        enabled = !vm.查中 && vm.查线索.isNotBlank(),
                    ) { Text(if (vm.查中) "查…" else "查") }
                }
                if (vm.查错误.isNotBlank()) {
                    Spacer(Modifier.height(6.dp))
                    Text(vm.查错误, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
                vm.查结果?.let { r ->
                    Spacer(Modifier.height(10.dp))
                    Text("激活 ${r.激活数} 格（列出前 ${r.格.size}）",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                    r.格.forEach { c ->
                        Text(
                            "${c.场} 第 ${c.轮} 轮 · ${"%.2f".format(c.分)}" +
                                (if (c.组.isNotBlank()) " · ${c.组}" else ""),
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                    if (r.件.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        Text("件（取用 = 这次会交给她）",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                        r.件.forEach { p ->
                            Text(
                                "${p.件id} · 最高 ${"%.2f".format(p.最高)} · ${p.段数} 段 · " +
                                    if (p.取用) "取用" else "未取用",
                                style = MaterialTheme.typography.bodySmall,
                                color = if (p.取用) MaterialTheme.colorScheme.primary
                                        else MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
            // ★ 长按某件 ⇒ 问一句「剪出」还是「删掉」（⛔ 原文一个字都不动）
            val 待 = vm.件待处理
            if (待 != null) {
                AlertDialog(
                    onDismissRequest = { vm.件待处理 = null },
                    title = { Text("这个件") },
                    text = {
                        Text(
                            "$待\n\n剪出：掉出标记集，件还在图里（下次被碰到还能拉起）\n" +
                                "删掉：从图里移除，记一笔到「图编辑」\n\n原文不会动。"
                        )
                    },
                    confirmButton = {
                        TextButton(onClick = { vm.graphAction(待, "剪出") }) { Text("剪出") }
                    },
                    dismissButton = {
                        Row {
                            TextButton(onClick = { vm.件待处理 = null }) { Text("算了") }
                            TextButton(onClick = { vm.graphAction(待, "删") }) { Text("删掉") }
                        }
                    },
                )
            }
        }

        when (签) {
            0 -> 件面(g, vm)
            1 -> 组面(g)
            else -> 势面(g)
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

// --------------------------------------------------------------------------- 三个签
@Composable
private fun 签们(选: Int, on选: (Int) -> Unit) {
    val 名 = listOf("件", "组", "势")
    val 说 = listOf("记住的东西", "归档怎么分的", "下回往哪儿流")
    Row(verticalAlignment = Alignment.CenterVertically) {
        名.forEachIndexed { i, n ->
            Column(
                Modifier
                    .clickable { on选(i) }
                    .padding(end = 22.dp, bottom = 2.dp),
            ) {
                Text(
                    n,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = if (i == 选) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (i == 选) MaterialTheme.colorScheme.onSurface
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    说[i],
                    style = MaterialTheme.typography.labelSmall,
                    color = if (i == 选) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

// --------------------------------------------------------------------------- 件
private fun LazyListScope.件面(g: GraphView?, vm: AppViewModel) {
    if (g != null && g.件们.isEmpty()) {
        item {
            空(
                "还没有件。\n一场对话里出现「意外」的地方会被标出来，" +
                    "归档时判成「归进某个件 / 新建一件」。"
            )
        }
    }
    items(g?.件们.orEmpty(), key = { "件:${it.id}" }) { 件 ->
        件卡(件) { vm.件待处理 = 件.id }
    }
    if (g != null && g.待观察池.isNotEmpty()) {
        item {
            Column(Modifier.padding(top = 18.dp, bottom = 6.dp)) {
                Text("待观察（掉出标记集，件还在）", style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                g.待观察池.forEach { x ->
                    Text("· ${x.单元} —— ${x.为什么}", style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
private fun 件卡(件: 件View, onLongPress: () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .combinedClickable(onClick = {}, onLongClick = onLongPress)
            .padding(vertical = 12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(件.id, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Medium)
            Spacer(Modifier.weight(1f))
            Text(
                buildString {
                    append("强度 ")
                    append((件.强度 * 100).toInt())
                    append("%")
                    if (!件.在标记集) append(" · 已剪出")
                },
                style = MaterialTheme.typography.labelSmall,
                color = if (件.在标记集) MaterialTheme.colorScheme.primary
                else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        件.成员.forEach { m ->
            Row(Modifier.padding(top = 6.dp)) {
                Text(
                    "第 ${m.轮} 轮",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(end = 8.dp),
                )
                Text(m.文本, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
    HorizontalDivider()
}

// --------------------------------------------------------------------------- 组
private fun LazyListScope.组面(g: GraphView?) {
    if (g != null && g.组们.isEmpty()) {
        item {
            空(
                "还没有组。\n组是归档时按「依赖簇」算出来的：一场里相邻、互相依赖的那几轮算一段。" +
                    "（第一场归档之后就该有了。）"
            )
        }
    }
    items(g?.组们.orEmpty(), key = { "组:${it.id}" }) { 组 ->
        组卡(组)
    }
}

@Composable
private fun 组卡(组: 组View) {
    // ★ 两种形态：依赖簇（一场里的轮区间）· 相似聚类（**跨场**，轮号跨场没有意义 ⇒ 只说几场几格）
    val 跨场 = 组.场们.size > 1
    Column(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(组.id.ifBlank { "组" }, style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Medium)
            Spacer(Modifier.weight(1f))
            Text(
                buildString {
                    if (跨场) {
                        append("跨 ${组.场们.size} 场 · ${组.成员.size} 格")
                    } else {
                        if (组.轮起 == 组.轮止) append("第 ${组.轮起} 轮")
                        else append("第 ${组.轮起}–${组.轮止} 轮")
                        append(" · ${组.成员.size} 格")
                    }
                    if (组.来源.isNotBlank()) append(" · ${组.来源}")
                },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }
        if (组.场们.isNotEmpty()) {
            Text(
                组.场们.joinToString(" · ") { 短场(it) },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 2.dp),
            )
        }
        组.成员.forEach { m ->
            Row(Modifier.padding(top = 6.dp)) {
                Text(
                    if (跨场) "${短场(m.场id)} 第 ${m.轮} 轮" else "第 ${m.轮} 轮",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(end = 8.dp),
                )
                Text(m.文本, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
    HorizontalDivider()
}

/** `2026-10-18-18` → `10-18-18`（手机上那一行放不下整条场 id）。 */
private fun 短场(s: String): String = if (s.startsWith("2026-")) s.substring(5) else s

// --------------------------------------------------------------------------- 势
private fun LazyListScope.势面(g: GraphView?) {
    if (g != null && g.势们.isEmpty()) {
        item {
            空(
                "还没有势。\n势是「读取路径」叠出来的：她每想起一次，走过的那几格就厚一层。" +
                    "它只引导下回往哪儿流（不是准入）。聊几轮就有了。"
            )
        }
    }
    items(g?.势们.orEmpty(), key = { "势:${it.单元}" }) { p ->
        Column(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "%.2f".format(p.势),
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.weight(1f))
                Text(
                    "亮过 %.0f%%".format(p.唤醒 * 100),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(p.标签, style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (p.文本.isNotBlank()) {
                Text(p.文本, style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(top = 4.dp))
            }
        }
        HorizontalDivider()
    }
}

// --------------------------------------------------------------------------- 空态
@Composable
private fun 空(文本: String) {
    Text(
        文本,
        style = MaterialTheme.typography.bodyMedium,
        modifier = Modifier.padding(vertical = 18.dp),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}
