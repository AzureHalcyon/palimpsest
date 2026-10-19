package com.azurehalcyon.palimpsest.probe.alarm

import android.app.AlarmManager
import android.content.Context
import com.azurehalcyon.palimpsest.plugin.Contract
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * **闹钟**这一路读数（渠道插件里的第三个数据域，2026-10-06）。
 *
 * ★ 它和日历同属"**他写的计划**"，但追问的是**作息**：闹钟定了几点 ⇒
 *   他打算几点起/几点提醒自己做什么。与传感器（观测）互不依赖。
 *
 * ★ 为什么只读"**下一个**闹钟"就够（而不是整张闹钟表）：
 *   实测 `com.android.deskclock` 的闹钟**列表**要走它的 provider，
 *   而那条门是 `miui.permission.ACCESS_ALARM_PROVIDER` = **signature|privileged** ⛔ 拿不到；
 *   而"下一个闹钟"有**公开 API**：`AlarmManager.getNextAlarmClock()`（不用权限）。
 *   ★ 这恰好也是最有用的一条 —— "他下一个闹钟还有多久响"。
 *   ⚠️ 读不到就**如实说"没有下一个闹钟"**，⛔ 不回 0（那会被读成"马上要响"）。
 */
class AlarmCore(private val ctx: Context) {

    companion object {
        val METRICS = setOf("next_alarm_minutes")

        /**
         * ★★ **是否把这一路放进工具表**（2026-10-06 用户决定：**先关闭**）。
         *
         * 关闭的三条理由（都是实测出来的，不是估计）：
         *   ① **语义太弱**：`getNextAlarmClock()` 给的是"系统登记的下一个闹钟"（任何 App 用
         *      `setAlarmClock()` 排的都算）⇒ 实测同一分钟内从 01:42 变 08:00，**会被动态提醒瞬变顶掉**，
         *      用户当场指出"我没有这个时间的闹钟"；
         *   ② **来源读不到**：`AlarmClockInfo.showIntent` 实测是 null ⇒ 只能写 `source_known:false`，
         *      没法按来源分级；
         *   ③ ★ **只能读、不能写**：读整张闹钟表要签名级权限（`miui.permission.ACCESS_ALARM_PROVIDER`），
         *      而"设闹钟"虽然有公开路（`AlarmClock.ACTION_SET_ALARM` + `com.android.alarm.permission.SET_ALARM`
         *      = **normal**，处理者是 `com.android.deskclock.HandleSetAlarmActivity`），
         *      **但用户 2026-10-06 选择不真机试写**⇒ **MIUI 认不认 `EXTRA_SKIP_UI`、
         *      `DISMISS_ALARM` 撤不撤得掉，都还是未验**。
         *      ⇒ 一条"只能看、还看不太准"的读数不值得占工具表一格。
         *
         * ★ **什么时候该重新打开**：当"可设可读"一起做的时候 ——
         *   设完立刻读回来验证，读数的弱就变成可验证的（设/读同源，互相印证）。
         *   ⚠️ 那一刻要先解决一件事：**那是这套系统第一次"写动作"**（至今一律"只看不改"）。
         */
        const val EXPOSE = false
    }

    private val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.US)

    fun describeMetrics(): JSONArray = JSONArray().put(
        JSONObject()
            .put(Contract.K_M_ID, "next_alarm_minutes")
            .put(Contract.K_M_NAME, "距下一个闹钟")
            .put(Contract.K_M_UNIT, "分钟")
            .put(Contract.K_M_DESC, "系统**当前登记的下一个闹钟**还有多少分钟（★ 任何 App 用 setAlarmClock 排的都算，" +
                "含计时器与系统的就寝/睡眠提醒 ⇒ **不等于「他手动设的闹钟」**；note 里带 set_by 说明是谁排的）")
            .put(Contract.K_M_TOOL_NAME, "get_next_alarm")
            .put(Contract.K_M_TOOL_DESC, "系统认为的下一个闹钟还有多久响、以及**是谁排的**")
            .put(Contract.K_M_USE_WHEN, "想知道他的作息线索（几点起）—— ⚠️ 只当**弱线索**用：它可能被计时器或系统的就寝提醒顶掉")
            .put(Contract.K_M_NATURE, "事件型（但会被计时器/就寝提醒瞬变顶掉，见 EXPOSE 的注释）")
            .put(Contract.K_M_GRAIN_MS, 60_000L)
            .put(Contract.K_M_MAX_WINDOW_MS, 7L * 24 * 3600_000L)
            .put(Contract.K_M_SOURCES, JSONArray().put("闹钟"))
    )

    fun fetch(metric: String, fromMs: Long, toMs: Long): JSONObject {
        val o = JSONObject()
        o.put(Contract.K_METRIC, metric)
        o.put(Contract.K_AT, System.currentTimeMillis())
        o.put(Contract.K_SOURCES, JSONArray().put("闹钟"))
        if (metric != "next_alarm_minutes") {
            o.put(Contract.K_OK, false).put(Contract.K_NOTE, "闹钟这一路没有这个渠道（$metric）")
            return o
        }
        return try {
            val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
            val info = am.nextAlarmClock          // ★ 公开 API，不需要权限
            if (info == null) {
                o.put(Contract.K_OK, false)
                o.put(Contract.K_NOTE, "系统说**当前没有下一个闹钟** ⇒ 不知道他的作息" +
                    "（⚠️ 这不是 0 分钟；也可能是本机不允许这个 App 读）")
            } else {
                val t = info.triggerTime
                val mins = (t - System.currentTimeMillis()) / 60_000.0
                // ★★ **谁排的**：这是这条读数的关键限定词。
                //   2026-10-06 真机踩到：01:15 读到"下一个闹钟 01:42"，而用户说**他没设过这个时间的闹钟**；
                //   3 分钟后同一 API 又回到 08:00 —— 这个值会被**别人的动态提醒**瞬变顶掉
                //   （实证指向 MIUI 时钟的「就寝/睡眠提醒」：通知渠道 `channel_id_deskclock_sleep`、
                //   alarm dump 里的 `SLEEP_ALARM_NOTIFICATION`）。
                //   ⇒ 不写来源，这条读数就会**替用户编出一个不存在的闹钟**。
                //   ⚠️⚠️ 但来源**经常读不到**：`AlarmClockInfo` 的 `showIntent` 可以是 null（实测就是），
                //   那时**必须说"读不到"**，⛔ **绝不能说成"不是时钟 App"** ——
                //   把"不知道"说成"不是"，就是另一种编造。
                val setBy: String? = try {
                    info.showIntent?.creatorPackage
                } catch (e: Throwable) {
                    null
                }
                o.put(Contract.K_OK, true)
                o.put(Contract.K_VALUE, mins)
                o.put("unit", "分钟")
                o.put("scalar", true)
                o.put("alarm_at", t)
                if (setBy != null) {
                    o.put("set_by", setBy)
                    o.put("is_clock_app", setBy == "com.android.deskclock")
                } else {
                    o.put("set_by", JSONObject.NULL)
                    o.put("source_known", false)
                }
                o.put(Contract.K_NOTE,
                    "系统登记的下一个闹钟：${fmt.format(Date(t))}（还有 ${"%.0f".format(mins)} 分钟）；" +
                        if (setBy == null) {
                            "**来源读不到**（它没带 showIntent）⇒ 无法判断是谁排的；" +
                                "★ 而且实测这个值**会被动态提醒瞬变顶掉**（同一分钟内见过 01:42 → 08:00）" +
                                "⇒ **只能当弱线索**，不当成「他设的闹钟」"
                        } else {
                            "由 `$setBy` 排的" +
                                (if (setBy == "com.android.deskclock") "" else "（⚠️ 不是时钟 App）") +
                                "；★ 这是**系统状态栏那一个**，不等于「他手动设的闹钟」"
                        })
            }
        } catch (t: Throwable) {
            o.put(Contract.K_OK, false)
                .put(Contract.K_NOTE, "读闹钟失败：${t.javaClass.simpleName}: ${t.message}")
        }
        return o
    }

    /** 诊断用一句话。 */
    fun summary(): String = try {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val i = am.nextAlarmClock
        if (i == null) "系统说当前没有下一个闹钟。"
        else {
            val by = try {
                i.showIntent?.creatorPackage
            } catch (e: Throwable) {
                null
            }
            "系统登记的下一个闹钟：${fmt.format(Date(i.triggerTime))}" +
                (if (by == null) "，来源读不到（无 showIntent）" else "，由 `$by` 排的")
        }
    } catch (t: Throwable) {
        "读闹钟失败：${t.javaClass.simpleName}: ${t.message}"
    }
}
