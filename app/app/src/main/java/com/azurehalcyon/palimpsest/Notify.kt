package com.azurehalcyon.palimpsest

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.RemoteInput

/**
 * **它开口**：投递通知 + 回话（2026-10-05）。
 *
 * ★★ 这一条是设计稿 §5.1 的落点：把"它主动开口"变成**一次可回话的事件**，
 *   而不是单向打扰。通知上带两样：
 *     · **回一句**（`RemoteInput`，不用打字也能回）→ 回的内容接成**正常对话的一个回合**
 *     · **我看到了**（一键已读）→ 只打勾，不生成回合
 *
 * ★ 为什么回话要带**投递 id**：一句"我在忙"如果丢了前文，就变成没有上下文的问题。
 *   id ⇒ 能取回"它当时说了什么、依据哪几个信源"（`DeliveryLog.context_of`）。
 *
 * ⛔ **不做"现在别说话"**（用户已否）：用户没回就是没回。
 *
 * ⚠️ 动作的 `PendingIntent` 是**我们自己 App 发的** ⇒ 与"从 shell 发广播"不同，
 *   不受跨 App 广播那道拦截影响（实测过 shell 广播送不到，见待办清单 §8.3）。
 */
object Notify {

    private const val CHANNEL_ID = "companion.say"
    private const val NOTIF_ID = 2001
    const val ACTION_REPLY = "com.azurehalcyon.palimpsest.DELIVER_REPLY"
    const val ACTION_SEEN = "com.azurehalcyon.palimpsest.DELIVER_SEEN"
    const val EXTRA_ID = "did"
    const val EXTRA_TEXT = "text"
    const val KEY_REPLY = "reply"

    /** 它开口时用这个（与 `CompanionService` 那个"进行中"的低优先级通知**不是一回事**）。 */
    fun post(ctx: Context, text: String, did: String) {
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL_ID, "它说的话", NotificationManager.IMPORTANCE_DEFAULT)
                    .apply { description = "它主动开口时走这一条；沉默是默认" })
        }
        // ★ RemoteInput 只能在 API 24+ 的 `addRemoteInput` 直连写法上用；
        //   minSdk 26 ⇒ 直接用（不需要兼容分支）。
        val reply = RemoteInput.Builder(KEY_REPLY)
            .setLabel("回一句")
            .build()
        val replyPi = PendingIntent.getBroadcast(
            ctx, 1, Intent(ctx, NotifyReceiver::class.java)
                .setAction(ACTION_REPLY).putExtra(EXTRA_ID, did),
            PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val seenPi = PendingIntent.getBroadcast(
            ctx, 2, Intent(ctx, NotifyReceiver::class.java)
                .setAction(ACTION_SEEN).putExtra(EXTRA_ID, did),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val open = PendingIntent.getActivity(
            ctx, 3, Intent(ctx, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val n = NotificationCompat.Builder(ctx, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("palimpsest")
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(open)
            .addAction(0, "我看到了", seenPi)
            .addAction(
                NotificationCompat.Action.Builder(0, "回一句", replyPi)
                    .addRemoteInput(reply).build()
            )
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(ctx).notify(NOTIF_ID, n)
        } catch (_: SecurityException) {
            // ★ 没给通知权限 ⇒ 静默失败：它"说了"但送不到，这事该由投递记录兜着，
            //   不该在这里崩。
        }
    }
}

/** 通知上的两个动作。★ 回话**接成正常对话的一个回合**（带投递 id 当上下文）。 */
class NotifyReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        val a = intent?.action ?: return
        val did = intent.getStringExtra(Notify.EXTRA_ID).orEmpty()
        when (a) {
            Notify.ACTION_SEEN -> {
                L0.runShell { PyBridge.deliverReply(ctx.filesDir.absolutePath, did, "") }
            }
            Notify.ACTION_REPLY -> {
                val text = RemoteInput.getResultsFromIntent(intent)
                    ?.getCharSequence(Notify.KEY_REPLY)?.toString().orEmpty()
                if (text.isBlank()) return
                L0.runShell { PyBridge.deliverReply(ctx.filesDir.absolutePath, did, text) }
                // ★ 把它接成**一个正常的对话回合**：打开对话屏，并把回话预置进输入框 ——
                //   用户按下发送，它就是一条普通消息（有前文、有上下文）。
                ctx.startActivity(
                    Intent(ctx, MainActivity::class.java)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                        // ★ 复用 App 已有的两个钩子（`tab` / `prefill`）：
                        //   回话 ⇒ 打开对话屏 + 把回话预置进输入框 ⇒ 它就是一个**正常回合**
                        .putExtra("tab", "chat")
                        .putExtra("prefill", text)
                )
            }
        }
    }
}
