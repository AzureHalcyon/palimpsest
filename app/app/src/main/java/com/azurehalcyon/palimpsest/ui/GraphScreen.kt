package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

/**
 * ★★ 记忆页 = **件浏览**（2026-10-17 换成新线）。
 *
 * 旧的四档池子（短期 / 中期 / 悬着 / 长期）在新线里**不存在**：底层现在是
 * `cell → 件 → 标记集`，而"件"就是一组 cell（依赖簇的落点），强度来自标记集。
 *
 * 这一屏只做三件事：
 *  1. 头上一行统计（cell / 件 / 标记集 / 待观察池）；
 *  2. 每个件一张卡：强度 · 由几段原文组成 · **点开看原文**（按区间从底片切回来的原话）；
 *  3. 掉出标记集的（待观察池）单列一块 —— ⛔ 件没删，只是不再参与回忆。
 *
 * ⛔ 这里**不许改**任何东西（"改件"在新线里是"重新标注"，形态还没定）。
 */
@Composable
fun GraphScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    LaunchedEffect(Unit) { if (vm.graph == null && !vm.graphLoading) vm.loadGraph() }
    val g = vm.graph
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
                        "${g.cell数} 轮 · ${g.件数} 件 · 在标记集 ${g.标记集数} · 待观察 ${g.待观察数}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (vm.graphErr.isNotBlank()) {
                    Spacer(Modifier.height(6.dp))
                    Text(vm.graphErr, style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error)
                }
            }
            HorizontalDivider()
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
        if (g != null && g.件们.isEmpty()) {
            item {
                Text(
                    "还没有件。\n一场对话里出现「意外」的地方会被标出来、判成「归进某个件 / 新建一件」——" +
                        "目前这一套还没在真对话上跑出件来。",
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(vertical = 18.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        items(g?.件们.orEmpty(), key = { it.id }) { 件 ->
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
        item { Spacer(Modifier.height(24.dp)) }
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
