package com.azurehalcyon.palimpsest

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/**
 * 常驻前台服务 —— **主动保活**。
 *
 * ★ 为什么需要它：这套设计本来就假定"手机上 App 被杀是常态"（每轮落盘 + 场次可续 +
 *   归档幂等，都是为它准备的）。但那是**兜底**。一场聊到一半、或者正在归档的时候
 *   被系统回收，代价是用户要重新接一次、或者多等一次补归档 —— 能用前台服务避免，
 *   就没必要靠兜底。
 *
 * ★ 只在**真有事在做**的时候驻留：
 *   · 有一场进行中（聊到一半被回收是最亏的）；
 *   · 正在归档（那几十秒里被回收最浪费，模型调用已经花掉了）。
 *   收场之后没有在途的东西 ⇒ 停掉，把它还给系统。**不做常驻监控**。
 *
 * ⚠️ Android 14（SDK 34）起 `startForeground` 必须声明类型；这里用 `specialUse`
 *   （manifest 里要带 `PROPERTY_SPECIAL_USE_FGS_SUBTYPE` 说明用途）。
 *   不用 `dataSync`：那一条在 Android 15+ 有每日 6 小时上限，而"聊一场"可能断断续续很久。
 */
class CompanionService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        val text = intent?.getStringExtra(EXTRA_TEXT).orEmpty().ifBlank { "在" }
        ensureChannel()
        ServiceCompat.startForeground(
            this,
            NOTIF_ID,
            buildNotification(text),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            } else {
                0
            },
        )
        // ★ START_STICKY：被系统清掉之后尽量自己回来。
        //   回来了也**不会**凭空多出数据 —— 真相在 pending.json / state.json 里，
        //   服务只是让它别被清掉。
        return START_STICKY
    }

    private fun ensureChannel() {
        val nm = getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        val ch = NotificationChannel(
            CHANNEL_ID,
            "进行中的对话",
            // ★ LOW：只在状态栏占一格，不响、不弹、不亮屏 —— 它是"我还在"，不是通知你什么。
            NotificationManager.IMPORTANCE_LOW,
        ).apply { description = "有一场对话进行中，或者正在归档" }
        nm.createNotificationChannel(ch)
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("companion")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "companion.active"
        private const val NOTIF_ID = 1001
        private const val EXTRA_TEXT = "text"
        private const val ACTION_STOP = "com.azurehalcyon.palimpsest.STOP"

        /** 起（或更新文案）。重复调用只会换掉通知文字，不会叠出第二个服务。 */
        fun start(ctx: Context, text: String) {
            val i = Intent(ctx, CompanionService::class.java).putExtra(EXTRA_TEXT, text)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(i)
            } else {
                ctx.startService(i)
            }
        }

        /** 没有在途的东西了 ⇒ 停掉，把它还给系统。 */
        fun stop(ctx: Context) {
            ctx.startService(
                Intent(ctx, CompanionService::class.java).setAction(ACTION_STOP),
            )
        }
    }
}
