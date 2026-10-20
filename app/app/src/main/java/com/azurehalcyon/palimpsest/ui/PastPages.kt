package com.azurehalcyon.palimpsest.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 「旧场」：**已归档的场**，只读。
 *
 * 作者 2026-10-19：「加一个能看之前场的地方吧？可以放在归档按钮下面。
 * 看归档场就是只读，归档那些系统消息都可见的那种」。
 *
 * ⛔ 这一页只读：不写图、不写底片、不改任何参数。
 * ★ 能看到的是**当时真的发出去的那几条 system**（`底片.系统前缀` 里的系统一 / 系统二；
 *   常驻另存在 `常驻/<场id>.json`）。⚠️ 「在场」那条按作者定**不进底片** ⇒ 这里看不到。
 */
@Composable
fun PastListPage(
    vm: AppViewModel,
    onBack: () -> Unit,
    onOpen: (String) -> Unit,
) {
    BackHandler(onBack = onBack)
    LaunchedEffect(Unit) { vm.loadPastList() }

    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Row(
            Modifier.fillMaxWidth().statusBarsPadding()
                .padding(horizontal = 8.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
            }
            Text(
                "旧场",
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.padding(top = 10.dp),
            )
        }
        if (vm.旧场错误.isNotBlank()) {
            Text(
                vm.旧场错误,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(horizontal = 20.dp),
            )
        }
        if (vm.旧场们.isEmpty()) {
            Text(
                "还没有归档过的场。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
            )
        }
        LazyColumn(
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(vm.旧场们, key = { it.场id }) { s ->
                Card(
                    onClick = { onOpen(s.场id) },
                    shape = RoundedCornerShape(20.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceContainer,
                    ),
                ) {
                    Column(Modifier.fillMaxWidth().padding(16.dp)) {
                        Text(s.场id, style = MaterialTheme.typography.titleMedium)
                        Text(
                            "${s.轮数} 轮 · ${时刻(s.ts)}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

/** 一场的全貌：系统前缀 → 常驻 → 每一轮。只读。 */
@Composable
fun PastDetailPage(vm: AppViewModel, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    val d = vm.旧场详情

    Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Row(
            Modifier.fillMaxWidth().statusBarsPadding()
                .padding(horizontal = 8.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
            }
            Text(
                d?.场id ?: "旧场",
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.padding(top = 10.dp),
            )
        }
        if (d == null) {
            Text(
                vm.旧场错误.ifBlank { "读取中…" },
                style = MaterialTheme.typography.bodyMedium,
                color = if (vm.旧场错误.isBlank()) {
                    MaterialTheme.colorScheme.onSurfaceVariant
                } else {
                    MaterialTheme.colorScheme.error
                },
                modifier = Modifier.padding(horizontal = 20.dp),
            )
            return@Column
        }
        Column(
            Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .padding(bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            卡块("系统提示（这一场真的发出去的）") {
                if (d.系统前缀.isEmpty()) {
                    灰("（底片里没有系统前缀）")
                }
                d.系统前缀.forEachIndexed { i, t ->
                    Text(
                        if (i == 0) "系统一" else if (i == 1) "系统二" else "系统${i + 1}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Text(t, style = MaterialTheme.typography.bodySmall)
                    if (i != d.系统前缀.lastIndex) HorizontalDivider()
                }
                if (d.在场.isNotBlank()) {
                    Text(
                        "系统三（在场）",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Text(d.在场, style = MaterialTheme.typography.bodySmall)
                } else {
                    灰("这一场没有记下「在场」。")
                }
            }

            卡块("常驻记忆") {
                if (d.常驻.isBlank()) 灰("（这一场没有常驻记录）") else Text(d.常驻, style = MaterialTheme.typography.bodySmall)
            }

            d.轮.forEach { t ->
                卡块("第 ${t.轮} 轮" + if (t.撤回) "（已撤回）" else "") {
                    Text("他", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                    Text(t.user, style = MaterialTheme.typography.bodySmall)
                    t.段.forEach { sg ->
                        when (sg.类) {
                            "思考" -> {
                                Text("思考", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                                Text(sg.文本, style = MaterialTheme.typography.bodySmall)
                            }
                            "回忆" -> {
                                Text(
                                    "回忆 · ${查询文本(sg.文本)}",
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                if (sg.回.isNotBlank()) {
                                    Text(sg.回, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            "事件" -> {
                                Text("事件 · ${sg.文本}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                                if (sg.回.isNotBlank()) {
                                    Text(sg.回, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                            }
                            "正文" -> Unit
                            else -> {
                                Text("她（这一轮中途）", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                                Text(sg.文本, style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                    Text("她", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                    Text(t.ai, style = MaterialTheme.typography.bodySmall)
                }
            }

            卡块("归档流程") {
                if (d.归档.判.isEmpty() && d.归档.层1.isEmpty()) {
                    灰("（这一场没有归档记录）")
                }
                d.归档.判.forEach { j ->
                    if (j.来源 == "摘要") {
                        Text(j.说法, style = MaterialTheme.typography.bodySmall)
                    } else if (j.来源 == "自标") {
                        Text("她自己的标记", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                        if (j.记.isEmpty()) 灰("（一处都没标）") else j.记.forEach { Text("· $it", style = MaterialTheme.typography.bodySmall) }
                        if (j.err.isNotBlank()) 灰("出错：${j.err}")
                    } else {
                        Text(
                            "第 ${j.轮} 轮 · ${j.来源}",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            if (j.归进.isNotBlank()) "归进 ${j.归进}" else if (j.新建) "新建一件" else "（没判）",
                            style = MaterialTheme.typography.bodySmall,
                        )
                        if (j.说法.isNotBlank()) Text(j.说法, style = MaterialTheme.typography.bodySmall)
                    }
                }
                d.归档.层1.forEach { line ->
                    Text("层一：$line", style = MaterialTheme.typography.bodySmall)
                }
            }

            if (d.指纹.isNotBlank()) {
                卡块("指纹") { Text(d.指纹, style = MaterialTheme.typography.bodySmall) }
            }
        }
    }
}

@Composable
private fun 卡块(标题: String, 内容: @Composable () -> Unit) {
    Card(
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
    ) {
        Column(
            Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(
                标题,
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            内容()
        }
    }
}

@Composable
private fun 灰(s: String) {
    Text(s, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** 工具参数是 JSON（`{"query":"…"}`）—— 界面上只要那半句查询。 */
private fun 查询文本(参数: String): String = runCatching {
    org.json.JSONObject(参数).optString("query").ifBlank { 参数 }
}.getOrDefault(参数)

private fun 时刻(ts: Double): String {
    if (ts <= 0.0) return ""
    return runCatching {
        SimpleDateFormat("MM月dd日 HH:mm", Locale.CHINA).format(Date((ts * 1000).toLong()))
    }.getOrDefault("")
}
