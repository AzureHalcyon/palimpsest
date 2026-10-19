package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * **活动记录**：它最近做了什么。
 *
 * ★★ 这一页存在的理由，就是设计稿 §5 那条 —— **不可见 ≠ 不可查**：
 *   如果它的活动完全不可见，用户分不清「它有自己的生活」和「它什么都没做」，
 *   那"自主"就是空话。⇒ 要有地方**看得到**，但 ⛔ **绝不推送**。
 *
 * ★ 显示上的两个克制：
 *   · 倒序（最近的在上面），一行一条，**不折叠、不装饰** —— 它是流水账，不是成果展；
 *   · 空的时候直说「（它还没做过什么）」，⛔ 不写"暂无数据"这种像出错的话。
 */
@Composable
fun ActivityScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val lines = vm.activityLines
    Column(
        modifier = modifier.fillMaxSize().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        if (vm.activityErr.isNotBlank()) {
            Text(
                text = vm.activityErr,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }
        if (vm.activityLoading && lines.isEmpty()) {
            Text("读着呢…", style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            return@Column
        }
        LazyColumn(
            modifier = Modifier.fillMaxWidth().fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            items(lines) { line ->
                Text(
                    text = line,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
        }
    }
}
