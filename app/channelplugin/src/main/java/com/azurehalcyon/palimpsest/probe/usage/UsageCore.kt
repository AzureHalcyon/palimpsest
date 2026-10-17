package com.azurehalcyon.palimpsest.probe.usage

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Process
import com.azurehalcyon.palimpsest.plugin.Contract
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar

/**
 * **屏幕使用**这一路读数（渠道插件里的第四个数据域，2026-10-06）。
 *
 * ★ 它是**观测**类（和传感器同类，但看的是**另一件事**）：
 *   传感器看"手机在空间里怎么动"，屏幕使用看"**他在怎么用这台手机**"。
 *   ⇒ 与传感器、与日历/闹钟（声明类）都互不依赖。
 *
 * ★ 权限不是运行时权限，而是一条 **appop**（`PACKAGE_USAGE_STATS`）：
 *   要在系统里手动开（设置 → 应用 → 特殊应用权限 → **使用情况访问**），
 *   `pm grant` 对 appop 无效 ⇒ 必须先 `checkOpNoThrow` 查，拿不到就**如实说没开**。
 *
 * ⚠️ 三条读数的语义边界（都写进 note）：
 *   1. **屏幕亮着 ≠ 他在用**（可能只是放在桌上亮着）；
 *   2. **"解锁次数"是近似**：用 `KEYGUARD_HIDDEN` 事件数代替，锁屏被划掉不等于他主动解锁；
 *   3. `screen_on_minutes_*` 需要 API 28+ 的 `SCREEN_INTERACTIVE / SCREEN_NON_INTERACTIVE` 事件，
 *      更老的机器上会**没有数据**（如实回 ok=false，不是 0）。
 */
class UsageCore(private val ctx: Context) {

    companion object {
        private const val DAY_MS = 86_400_000L
        val METRICS = setOf("screen_on_minutes_today", "screen_on_minutes_last_hour", "unlock_count_today")
    }

    /** `PACKAGE_USAGE_STATS` 是 appop，不是运行时权限 —— 只能这样查。 */
    fun granted(): Boolean = try {
        val appOps = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = appOps.unsafeCheckOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName
        )
        mode == AppOpsManager.MODE_ALLOWED
    } catch (t: Throwable) {
        false
    }

    fun describeMetrics(): JSONArray {
        fun m(id: String, name: String, unit: String, desc: String, tool: String, toolDesc: String,
              useWhen: String, nature: String) =
            JSONObject()
                .put(Contract.K_M_ID, id)
                .put(Contract.K_M_NAME, name)
                .put(Contract.K_M_UNIT, unit)
                .put(Contract.K_M_DESC, desc)
                .put(Contract.K_M_TOOL_NAME, tool)
                .put(Contract.K_M_TOOL_DESC, toolDesc)
                .put(Contract.K_M_USE_WHEN, useWhen)
                // ★ 性质（事实）：告诉 AI"这个数为什么会变"，判断权在它 —— 见 `Contract.K_M_NATURE`
                .put(Contract.K_M_NATURE, nature)
                .put(Contract.K_M_GRAIN_MS, 60_000L)
                .put(Contract.K_M_MAX_WINDOW_MS, 30 * DAY_MS)
                .put(Contract.K_M_SOURCES, JSONArray().put("屏幕使用"))

        return JSONArray()
            .put(m("screen_on_minutes_today", "今天屏幕亮着多久", "分钟",
                "今天屏幕亮着的累计分钟数（★ 亮着 ≠ 他在用：可能只是放着亮）",
                "get_screen_today", "他今天盯了多久手机",
                "想知道他今天用手机的多少（久坐刷手机 / 一整天没碰）",
                "单调累加（今天累计，只增不减）"))
            .put(m("screen_on_minutes_last_hour", "最近一小时屏幕亮着多久", "分钟",
                "最近 60 分钟里屏幕亮着的分钟数（★ 此刻状态的刻度，不是全天）",
                "get_screen_last_hour", "他刚才一小时里是不是一直在用手机",
                "想知道他此刻是不是正拿着手机（与传感器的「没动」正好互补）",
                "滚动窗口（屏幕亮着就一直涨 —— 它变了**通常不等于发生了什么事**）"))
            .put(m("unlock_count_today", "今天解锁次数", "次",
                "今天「锁屏被划掉」的次数（★ 近似：不等于他主动解锁，见 note）",
                "get_unlock_count", "他今天解锁了几次手机",
                "想知道他今天的手机使用是不是很碎（频繁亮屏解锁）",
                "单调累加（今天累计次数，只增不减）"))
    }

    fun fetch(metric: String, fromMs: Long, toMs: Long): JSONObject {
        val o = JSONObject()
        o.put(Contract.K_METRIC, metric)
        o.put(Contract.K_AT, System.currentTimeMillis())
        o.put(Contract.K_SOURCES, JSONArray().put("屏幕使用"))

        if (!granted()) {
            o.put(Contract.K_OK, false)
            o.put(Contract.K_NOTE, "还没开「**使用情况访问**」⇒ **不知道**他用手机的情况" +
                "（⚠️ 这不是「他没在用」）——设置 → 应用 → 特殊应用权限 → 使用情况访问 → 授给「渠道插件」")
            return o
        }
        return try {
            when (metric) {
                "screen_on_minutes_today" -> screenOn(o, day0(), System.currentTimeMillis(), "今天")
                "screen_on_minutes_last_hour" ->
                    screenOn(o, System.currentTimeMillis() - 3600_000L, System.currentTimeMillis(), "最近一小时")
                "unlock_count_today" -> unlockCount(o, day0(), System.currentTimeMillis())
                else -> o.put(Contract.K_OK, false).put(Contract.K_NOTE, "屏幕使用这一路没有这个渠道（$metric）")
            }
        } catch (t: Throwable) {
            o.put(Contract.K_OK, false)
                .put(Contract.K_NOTE, "读使用记录失败：${t.javaClass.simpleName}: ${t.message}")
        }
    }

    private fun day0(): Long {
        val c = Calendar.getInstance()
        c.set(Calendar.HOUR_OF_DAY, 0); c.set(Calendar.MINUTE, 0)
        c.set(Calendar.SECOND, 0); c.set(Calendar.MILLISECOND, 0)
        return c.timeInMillis
    }

    /**
     * 事件只留"类型 + 时间"两项。
     * ⚠️ 必须**自己抄一份**：`getNextEvent(one)` 会**复用同一个对象**，
     *   而 `UsageEvents.Event` 的字段在 Kotlin 里是**只读**的（不能拷贝赋值）⇒ 用这个轻量 holder。
     */
    private class Ev(val type: Int, val ts: Long)

    private fun events(fromMs: Long, toMs: Long): List<Ev> {
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val out = mutableListOf<Ev>()
        val e = usm.queryEvents(fromMs, toMs)
        val one = UsageEvents.Event()
        while (e != null && e.hasNextEvent()) {
            e.getNextEvent(one)
            out += Ev(one.eventType, one.timeStamp)
        }
        return out
    }

    /** 屏幕亮着的累计分钟数（把 SCREEN_INTERACTIVE → SCREEN_NON_INTERACTIVE 成对加起来）。 */
    private fun screenOn(o: JSONObject, fromMs: Long, toMs: Long, label: String): JSONObject {
        val evs = events(fromMs, toMs)
        var onAt = -1L
        var totalMs = 0L
        for (e in evs) {
            when (e.type) {
                UsageEvents.Event.SCREEN_INTERACTIVE -> if (onAt < 0) onAt = e.ts
                UsageEvents.Event.SCREEN_NON_INTERACTIVE -> if (onAt >= 0) {
                    totalMs += (e.ts - onAt); onAt = -1
                }
            }
        }
        if (onAt >= 0) totalMs += (System.currentTimeMillis() - onAt)   // 现在还亮着

        if (evs.isEmpty()) {
            o.put(Contract.K_OK, false)
            o.put(Contract.K_NOTE, "这段时间没有屏幕事件（$label）—— 可能是 API < 28 的机器，" +
                "或系统没给这批事件（⚠️ 不是「他 0 分钟」）")
            return o
        }
        val mins = totalMs / 60_000.0
        o.put(Contract.K_OK, true)
        o.put(Contract.K_VALUE, mins)
        o.put("unit", "分钟")
        o.put("scalar", true)
        o.put(Contract.K_NOTE, "$label 屏幕亮着 ${"%.0f".format(mins)} 分钟（事件 ${evs.size} 条）；" +
            "★ 亮着 ≠ 他在用（可能只是放着亮）")
        return o
    }

    /** 近似解锁次数：数 `KEYGUARD_HIDDEN`（锁屏不再显示）。 */
    private fun unlockCount(o: JSONObject, fromMs: Long, toMs: Long): JSONObject {
        val evs = events(fromMs, toMs)
        val n = evs.count { it.type == UsageEvents.Event.KEYGUARD_HIDDEN }
        o.put(Contract.K_OK, true)
        o.put(Contract.K_VALUE, n)
        o.put("unit", "次")
        o.put("scalar", true)
        o.put(Contract.K_NOTE, "今天「锁屏被划掉」$n 次（★ 近似：不等于他主动解锁；事件 ${evs.size} 条）")
        return o
    }

    /** 诊断用一句话。 */
    fun summary(): String {
        if (!granted()) return "还没开「使用情况访问」（设置 → 应用 → 特殊应用权限 → 使用情况访问）。"
        val now = System.currentTimeMillis()
        val today = screenOn(JSONObject(), day0(), now, "今天")
        val hour = screenOn(JSONObject(), now - 3600_000L, now, "最近一小时")
        return "今天屏幕亮 ${today.opt("value")} 分钟 · 最近一小时 ${hour.opt("value")} 分钟"
    }
}
