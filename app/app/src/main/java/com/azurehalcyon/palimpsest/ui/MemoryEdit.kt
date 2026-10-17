package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.niki914.uikit.infra.component.SettingExpandableTextItem

/**
 * ★★ **长按一条记忆 ⇒ 就地改 / 删**（2026-10-06 用户要求）。
 *
 * 用户的原话：「对对话时展示的那些搜到的和 push 的条目，也加上长按修改功能，
 * 这样我使用时看到不合适的可以自己先手动修改，然后重新生成；之后再在电脑上整理更改。」
 *
 * ★ 2026-10-07 **抽成公共**（原来是 `ChatScreen` 里的一段局部代码 + 局部 `remember`）：
 *   记忆页（`PoolScreen`）也要用**同一个**弹窗。用户真机实测的原话是
 *   「记忆页面长按删不了，**哪一个都是**」—— 那一屏当时连长按手势都没挂。
 *   ⛔ 抽出来的是**界面**；裁决仍在 `AppViewModel.editMemory / deleteMemory` 一处。
 *
 * ⚠️ 改的是**池子**，不是已经发出去的那一轮上下文：
 *   · 在对话里 ⇒ 可以「保存并重新生成」（把最近一轮撤了重跑，新文本才进模型）；
 *   · 在记忆页 ⇒ 只给「保存」（那儿没有"最近一轮"这回事）。
 *   这个区别由 [AppViewModel.memoryIsLast] 决定，调用处不用管。
 * ⚠️ 删掉**不是**改池子的副本：原稿还在电脑上，这次改动记进 `cache/pool_edits.jsonl`
 *   （`backup.ps1` 能取回），所以在删除确认里**如实写出来**。
 */
@Composable
internal fun MemoryEditDialog(vm: AppViewModel, onRegen: () -> Unit = {}) {
    // ★ 先抓住**这一条是哪一条**：点下去之后弹窗就关了（`memoryTarget` 变 null），
    //   而那两个按钮还要用它的原文去核销（池子按**文本**找条目）。
    val target = vm.memoryTarget ?: return
    FlatDialog(
        visible = true,
        onDismissRequest = { vm.closeMemoryEditor() },
        title = {
            Text(
                text = if (vm.memoryDeleting) "删掉这一条？" else "改这一条",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
                modifier = Modifier.fillMaxWidth(),
            )
        },
        content = {
            if (vm.memoryDeleting) {
                Text(
                    text = target,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = "删掉之后它不会再被想起来。桌面上的原稿不动 —— " +
                        "这次改动会记进 pool_edits.jsonl（backup.ps1 能取回），你之后在电脑上整理。",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                SettingExpandableTextItem(
                    title = "条目原文",
                    // ⚠️ 提示**必须放在 `description` 里、且不能写 markdown 记号** ——
                    //    App 的 Text 不渲染 `**`，上一版把星号原样显示出来了（真机截图抓到）。
                    description = "改的是池子里的这一条（底色 / 情境 / 记忆都行）。" +
                        "这一轮上下文里还是旧文本，要让它用上得重新生成那一轮。",
                    value = vm.memoryDraft,
                    onValueChange = { vm.memoryDraft = it },
                    placeholder = "改成…",
                    initiallyExpanded = true,
                    minLines = 2,
                    maxLines = 8,
                )
            }
        },
        actions = {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (vm.memoryDeleting) {
                    TextButton(onClick = { vm.memoryDeleting = false }) { Text("返回") }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = {
                        vm.closeMemoryEditor()
                        vm.deleteMemory(target)
                    }) {
                        Text("确认删掉", color = MaterialTheme.colorScheme.error)
                    }
                } else {
                    TextButton(onClick = { vm.memoryDeleting = true }) {
                        Text("删除这条", color = MaterialTheme.colorScheme.error)
                    }
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { vm.closeMemoryEditor() }) { Text("取消") }
                    if (vm.memoryIsLast) {
                        TextButton(onClick = {
                            vm.closeMemoryEditor()
                            onRegen()
                            vm.editMemory(target, vm.memoryDraft, regen = true)
                        }) { Text("保存并重新生成") }
                    }
                    TextButton(onClick = {
                        vm.closeMemoryEditor()
                        vm.editMemory(target, vm.memoryDraft, regen = false)
                    }) { Text("保存") }
                }
            }
        },
    )
}
