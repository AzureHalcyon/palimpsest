package com.azurehalcyon.palimpsest

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * **L0 机械检查 + 空闲窗口的调度**（2026-10-05）。
 *
 * 两级唤醒（设计稿 §3.1）：
 *   · **L0（这里）**：把插件问一遍、算"有没有变化" —— ★ **不调模型，成本 0**
 *   · **L1**：只有 L0 说"有变化"时，才去唤起模型（本轮**还没接**，见 §待办）
 *
 * 谁会让 L0 跑起来（都是**不常驻**的方式）：
 *   · `WorkManager` **周期**任务（≥15 分钟，**非精确** —— `SCHEDULE_EXACT_ALARM` 默认不给）
 *   · **系统广播踢一脚**：开机 / 蓝牙连断 / 下一个闹钟变了
 *   · ⛔ **没有"渠道报警"这条路**：插件是 provider、只能被查询（实测过的那条）
 *
 * ⚠️ 一条**未验证**（我特意不依赖它）：Android 12+ 上收 `ACTION_ACL_CONNECTED` 这类蓝牙广播
 *   是否需要 `BLUETOOTH_CONNECT` 这个**运行时**权限，官方那页没直接回答。
 *   ⇒ 所以蓝牙只当"**可能多一次机会**"：收不到也不影响（周期任务照跑）。
 */
object L0 {

    const val PERIODIC_WORK = "companion.l0.periodic"
    const val ONESHOT_WORK = "companion.l0.oneshot"
    const val DEBUG_ACTION = "com.azurehalcyon.palimpsest.DEBUG_L0"
    private const val TAG = "companion.l0"

    /** 排上周期任务。**每次启动都调**（幂等：`KEEP` 不会重复排）。 */
    fun schedulePeriodic(ctx: Context) {
        val req = PeriodicWorkRequestBuilder<L0Worker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.NONE)      // ★ 不加"要有网/要充电"：它本来就该在没网时也能看一眼
            .build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
            PERIODIC_WORK, ExistingPeriodicWorkPolicy.KEEP, req)
    }

    /** 踢一脚：立刻跑一次（开机 / 蓝牙 / 闹钟变化 / 调试）。 */
    fun kick(ctx: Context, why: String) {
        Log.i(TAG, "kick: $why")
        val req = OneTimeWorkRequestBuilder<L0Worker>().addTag(why).build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            ONESHOT_WORK + "." + why, ExistingWorkPolicy.REPLACE, req)
    }

    /**
     * 跑一次 L0。返回给人看的摘要（也写进 `files/l0-report.txt`，便于取证据）。
     *
     * ★ 全程**不构造 LLM**：先收集（Kotlin），再交给 Python 的 `channel_tick`（L0 纯逻辑 + 够格才 L1）。
     */
    fun run(ctx: Context, why: String): String {
        val dataDir = ctx.filesDir.absolutePath
        // ★★ **先把 Python 起起来**（2026-10-05 修的真 bug）：从 WorkManager / 广播接收器
        //   进来的这条路上**没有 Activity**，而 `Python.start()` 原来只在 MainActivity 里 ⇒
        //   冷进程里 L0 必然抛 `Cannot use GenericPlatform on Android`
        //   （真机报告里就是 `[work] 失败：桥接 channel_tick 失败：…`）。
        //   ⇒ 幂等，起过一次就立刻返回。
        PyBridge.ensureStarted(ctx)
        val payload = try {
            ChannelHost.collect(ctx)
        } catch (t: Throwable) {
            "{\"plugins\":[],\"reads\":{}}"          // 收不到就按"没有渠道"走，绝不崩
        }
        val out = try {
            PyBridge.channelTick(dataDir, payload)
        } catch (t: Throwable) {
            "{\"ok\":false,\"err\":\"${t.javaClass.simpleName}: ${t.message}\"}"
        }
        val ts = SimpleDateFormat("MM-dd HH:mm:ss", Locale.US).format(Date())
        val b = StringBuilder()
        b.append("$ts  [$why]  ")
        b.append(try {
            val o = JSONObject(out)
            if (o.optBoolean("ok")) {
                val l0 = o.optJSONObject("l0") ?: o
                val n = l0.optInt("changed_n")
                val l1 = o.optJSONObject("l1")
                val pol = o.optJSONObject("policy")
                //   ⚠️ `ran=true` 时 Python 侧**不带 `why`**（JSON 里是 null）⇒ 这里要判空，
                //   否则报告里会变成一个没意义的 `null`（2026-10-06 真机看到的显示瑕疵）。
                val l1Why = l1?.optString("why").orEmpty().let { if (it == "null") "" else it }
                "变化 $n 条" + (if (n > 0) "（${o.optJSONArray("changed") ?: l0.optJSONArray("changed")}）" else "（无需唤起模型）") +
                    " · " + l0.optJSONObject("stats") +
                    // ★ 唤醒与否现在由 **AI 自己的策略**决定（2026-10-06）：
                    //   所以报告里要把"为什么没叫它"（`why`，如 `coalesced`/`filtered_by_ai_policy`）
                    //   和"它当前用的策略"一起写出来 —— 否则调灵敏度时无从下手。
                    " · L1=" + (l1Why.ifEmpty { if (l1?.optBoolean("ran") == true) "ran" else "?" }) +
                    (l1?.optString("wake_reason").orEmpty().takeIf { it.isNotEmpty() && it != "null" }
                        ?.let { "($it)" } ?: "") +
                    " · 策略=" + (pol?.optString("mode") ?: "?")
            } else {
                "失败：" + o.optString("err")
            }
        } catch (t: Throwable) {
            out.take(200)
        })

        // ★★ 2026-10-06：顺带把"**已收场、但归档被打断**"的那一场补完。
        //   为什么挂在这里：WorkManager 每 15 分钟就在跑这条路（还有开机 / 开门那几次），
        //   而 `channel_tick` 本身**不构造 LLM**（那条约定不动）⇒ 补归档是**另一条**明确的路。
        //   ⚠️ Python 侧**自己判 `ts_end`** ⇒ "聊到一半"的那一场不会被误归档（那要由会话入口接回去）。
        //   ⚠️ 「没有在途的场次」「还没收场」这两句是**常态**，不进报告（免得每 15 分钟刷一行）。
        val resumeLine = try {
            PyBridge.resumePending(dataDir).firstOrNull { it.isNotBlank() }.orEmpty()
        } catch (t: Throwable) {
            "补归档失败：${t.javaClass.simpleName}: ${t.message}"
        }
        if (resumeLine.isNotEmpty() &&
            !resumeLine.startsWith("没有在途") && !resumeLine.contains("还没收场")) {
            b.append(" · ").append(resumeLine)
        }

        val line = b.toString()
        appendReport(ctx, line + "\n")
        Log.i(TAG, line)
        return line
    }

    /** 在**后台线程**跑一段（广播接收器的 `onReceive` 在主线程，⛔ 不许阻塞）。 */
    fun runShell(block: () -> Unit) {
        Thread {
            try {
                block()
            } catch (_: Throwable) {
            }
        }.start()
    }

    /** 报告留在应用私有目录（`run-as` 拉得出来）—— 用来验"L0 到底跑了没有、花了多少钱"。 */
    fun appendReport(ctx: Context, text: String) {
        try {
            val f = File(ctx.filesDir, "l0-report.txt")
            if (f.length() > 200_000) f.writeText("")          // 别让它无限长
            f.appendText(text)
        } catch (_: Throwable) {
        }
    }
}

/** 周期任务（非精确，≥15 分钟）。 */
class L0Worker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        // ★ 用**这次任务的 tag** 当原因，别写死 `periodic`（2026-10-05 实测发现的）：
        //   否则"开门顺手跑的那次"在报告里也显示成 `[periodic]`，
        //   而报告正是用来分辨"它到底被谁叫醒的"。
        val why = tags.firstOrNull { it in KNOWN_WHYS } ?: "work"
        L0.run(applicationContext, why)
        L0.schedulePeriodic(applicationContext)     // 自我续订（KEEP 幂等）
        return Result.success()
    }

    companion object {
        /** `L0.kick()` 用的那几个 tag（与 `L0Receiver` 里的 why 一致）。 */
        val KNOWN_WHYS = setOf("boot", "bt_on", "bt_off", "alarm_changed", "time_changed",
                               "debug", "app_open")
    }
}

/**
 * 系统广播接收器 —— **"无事发生"时唯一能叫醒我们的东西**（都是**静态注册也豁免**的广播）。
 *
 * ⚠️ `ACTION_SCREEN_ON` / `ACTION_POWER_CONNECTED` **不在豁免清单里**（实测查过官方列表），
 *   进程不在时收不到 ⇒ 我早先想用"亮屏/插电源"当唤醒时机的设想**是错的**，已作废。
 */
class L0Receiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent?) {
        val a = intent?.action ?: return
        // ★ 调试入口**只在 debug 包里有效**：它是自定义 action + exported receiver，
        //   别的 App 也发得出来 ⇒ release 里必须拒掉（不靠"没人知道这个 action"）。
        if (a == L0.DEBUG_ACTION) {
            val dbg = (ctx.applicationInfo.flags and
                android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
            if (!dbg) return
        }
        val why = when (a) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_LOCKED_BOOT_COMPLETED -> "boot"
            "android.bluetooth.device.action.ACL_CONNECTED" -> "bt_on"
            "android.bluetooth.device.action.ACL_DISCONNECTED" -> "bt_off"
            "android.app.action.NEXT_ALARM_CLOCK_CHANGED" -> "alarm_changed"
            Intent.ACTION_TIMEZONE_CHANGED, Intent.ACTION_TIME_CHANGED -> "time_changed"
            L0.DEBUG_ACTION -> "debug"
            "com.azurehalcyon.palimpsest.DEBUG_NOTIFY" -> "notify_test"
            else -> "unknown:" + a
        }
        L0.schedulePeriodic(ctx)        // 保证周期任务还排着（被系统清掉后靠它回来）
        L0.kick(ctx, why)
    }
}
