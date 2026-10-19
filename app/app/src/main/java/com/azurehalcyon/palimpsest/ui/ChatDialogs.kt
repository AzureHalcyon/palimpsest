package com.azurehalcyon.palimpsest.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
/**
 * 归档确认。入口在工具栏「⋯」。
 *
 * ★ 文案分工（用户定的）：**动作叫「归档」（说清是什么），结果叫「睡醒了」（有趣）**。
 *   这不是硬凑——代码里这次整理本来就叫 `dream`（`DREAM_SYS` / `dream_rounds`），
 *   "它睡下去把这一场过一遍，醒了"本来就是这套东西自己的说法。
 */
@Composable
fun FinishConfirmDialog(onDismiss: () -> Unit, onConfirm: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(FlatRadius),
        title = { Text("归档") },
        text = { Text("这一场就到这里。它会自己把刚才那些过一遍，收进记忆里。") },
        confirmButton = { FlatButton("归档", onConfirm) },
        dismissButton = { FlatOutlineButton("再聊会儿", onDismiss) },
    )
}

