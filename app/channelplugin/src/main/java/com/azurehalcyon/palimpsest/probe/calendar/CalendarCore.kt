package com.azurehalcyon.palimpsest.probe.calendar

import android.Manifest
import android.content.ContentResolver
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import com.azurehalcyon.palimpsest.plugin.Contract
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/**
 * **日历**这一路读数（渠道插件里的第二个数据域，与传感器并列）。
 *
 * ★ 为什么和传感器**放在同一个插件里**（而不是各做一个 APK）：
 *   设计稿 §2.7 说「权限按插件分开」，那条本身没错，但实测的代价更实在：
 *   ① 多一个包就要多一道**安装确认**（MIUI 拦新包：`INSTALL_FAILED_USER_RESTRICTED`，
 *      而**更新**已有插件是通的）；② 多一个桌面图标、多一个模块；
 *   ③ 而隔离的收益在自用场景很小（同一台设备、同一个用户、同一套签名）。
 *   ⇒ 一个"渠道插件"承载多个数据域；**权限仍按域申请**（这条只管要 `READ_CALENDAR`）。
 *
 * ★ 它给的是跟传感器**完全不同的那一类信息**：
 *   传感器答"**他实际在做什么**"（观测），日历答"**他打算做什么**"（声明）。
 *   两者互不依赖 ⇒ 正好当"多独立信源交叉验证"里的第二条
 *   （例：日历说他 14:00 有会 + 加速度计说他 14:00 之后 40 分钟没动 ⇒ 两路一致）。
 *
 * ⚠️ 两个刻意的取舍（都写进 note，让模型看得见）：
 *   1. **全天日程不算"他多久后有事"** —— 「元旦」不是「他 0 分钟后要开会」；
 *      但它**要出现在今天的清单里**（`today_events` 的第 4 列标出来）。
 *   2. **没有未来日程 ≠ 0 分钟** ⇒ `next_event_minutes` 回 `ok:false` + 说清"14 天内没有日程"，
 *      ⛔ 绝不回 0 让上层当成"马上就要开会"。
 */
class CalendarCore(private val ctx: Context) {

    companion object {
        private const val DAY_MS = 86_400_000L

        /** "下一个日程"最多往后看多久（再远就不是"他接下来有事"了）。 */
        private const val HORIZON_MS = 14 * DAY_MS

        /** 这一路暴露的 metric id（宿主按 id 路由到这里）。 */
        val METRICS = setOf("next_event_minutes", "busy_now", "today_events")

        private val PROJ = arrayOf(
            CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END,
            CalendarContract.Instances.TITLE,
            CalendarContract.Instances.ALL_DAY,
        )
    }

    private class Ev(val begin: Long, val end: Long, val title: String, val allDay: Boolean) {
        val minutes: Long get() = (end - begin) / 60_000L
    }

    private val fmt = SimpleDateFormat("MM-dd HH:mm", Locale.US)

    /** 有没有拿到 `READ_CALENDAR`。⛔ 没拿到就**如实说没授权**，不是"没日程"。 */
    fun granted(): Boolean =
        ctx.checkSelfPermission(Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** `Instances` 会**自动展开重复日程**（rrule）—— 这是"今天有什么"唯一正确的读法。 */
    private fun instances(fromMs: Long, toMs: Long): List<Ev> {
        val cr: ContentResolver = ctx.contentResolver
        val out = mutableListOf<Ev>()
        CalendarContract.Instances.query(cr, PROJ, fromMs, toMs).use { c ->
            while (c != null && c.moveToNext()) {
                out += Ev(
                    begin = c.getLong(0),
                    end = c.getLong(1),
                    title = c.getString(2) ?: "(无标题)",
                    allDay = c.getInt(3) == 1,
                )
            }
        }
        return out.sortedBy { it.begin }
    }

    // ------------------------------------------------------------------ describe
    /**
     * 这一路的 metric 描述（给 `PluginCore.describe()` 拼进总表）。
     * ★ `useWhen` 都照实写：日历是**他写的计划**，不代表他真的做了。
     */
    fun describeMetrics(): JSONArray {
        fun m(
            id: String, name: String, unit: String, desc: String,
            tool: String, toolDesc: String, useWhen: String, nature: String,
            grainMs: Long = 60_000L, maxWindowMs: Long = 30 * DAY_MS,
        ) = JSONObject()
            .put(Contract.K_M_ID, id)
            .put(Contract.K_M_NAME, name)
            .put(Contract.K_M_UNIT, unit)
            .put(Contract.K_M_DESC, desc)
            .put(Contract.K_M_TOOL_NAME, tool)
            .put(Contract.K_M_TOOL_DESC, toolDesc)
            .put(Contract.K_M_USE_WHEN, useWhen)
            // ★ 性质（事实）：判断权在 AI —— 见 `Contract.K_M_NATURE`
            .put(Contract.K_M_NATURE, nature)
            .put(Contract.K_M_GRAIN_MS, grainMs)
            .put(Contract.K_M_MAX_WINDOW_MS, maxWindowMs)
            .put(Contract.K_M_SOURCES, JSONArray().put("日历"))

        return JSONArray()
            .put(m(
                "next_event_minutes", "距下一个日程", "分钟",
                "离下一件事开始还有多少分钟（★ 不算全天日程：元旦不是「马上要开会」）",
                "get_next_event", "他接下来有没有安排、还有多久",
                "想知道他是不是快有事了（★ 这是「他 20 分钟后有个会」那句话要用的量）",
                "事件型（日程开始/新增会跳变；没安排时无值）"))
            .put(m(
                "busy_now", "此刻在日程中", "0/1",
                "此刻是不是落在一个日程区间里（★ 日程是**他写的计划**，不代表他真在做）",
                "get_busy_now", "他现在是不是在日程安排里",
                "想知道他此刻是不是应该在忙（会议/课程/行程）",
                "事件型（0↔1，进出一件事才变）"))
            .put(m(
                "today_events", "今天的日程", "窗口",
                "今天的日程清单（含全天），每行 [开始, 结束, 分钟数, 全天?]",
                "get_today_events", "他今天安排了几件事",
                "想知道他今天有几件事、都是什么时候",
                "单调累加（今天的事件数，一天内只增）"))
    }

    // ------------------------------------------------------------------ fetch
    fun fetch(metric: String, fromMs: Long, toMs: Long): JSONObject {
        val o = JSONObject()
        o.put(Contract.K_METRIC, metric)
        o.put(Contract.K_AT, System.currentTimeMillis())
        o.put(Contract.K_SOURCES, JSONArray().put("日历"))

        if (!granted()) {
            o.put(Contract.K_OK, false)
            o.put(Contract.K_NOTE, "还没拿到「读取日历」授权 ⇒ **不知道**他的安排" +
                "（⚠️ 这不是「他没有安排」，是我们没被允许看）——打开「渠道插件」授一次即可")
            return o
        }

        return try {
            when (metric) {
                "next_event_minutes" -> nextEvent(o)
                "busy_now" -> busyNow(o)
                "today_events" -> todayEvents(o)
                else -> {
                    o.put(Contract.K_OK, false)
                    o.put(Contract.K_NOTE, "日历这一路没有这个渠道（$metric）")
                }
            }
        } catch (t: Throwable) {
            o.put(Contract.K_OK, false)
            o.put(Contract.K_NOTE, "读日历失败：${t.javaClass.simpleName}: ${t.message}")
        }
    }

    private fun nextEvent(o: JSONObject): JSONObject {
        val now = System.currentTimeMillis()
        val ev = instances(now, now + HORIZON_MS).firstOrNull { !it.allDay && it.begin >= now }
        if (ev == null) {
            o.put(Contract.K_OK, false)
            o.put(Contract.K_NOTE, "往后 ${HORIZON_MS / DAY_MS} 天里没有（非全天的）日程 ⇒ **不知道**他接下来有没有事" +
                "（⚠️ 这不是 0 分钟）")
            return o
        }
        val mins = (ev.begin - now) / 60_000.0
        o.put(Contract.K_OK, true)
        o.put(Contract.K_VALUE, mins)
        o.put("unit", "分钟")
        o.put("scalar", true)
        o.put("next_at", ev.begin)
        o.put("next_title", ev.title)
        o.put(Contract.K_NOTE, "下一件事：${ev.title}，${fmt.format(Date(ev.begin))} 开始（还有 ${"%.0f".format(mins)} 分钟）" +
            "；★ 这是**他写的计划**，不是我们观察到的行为")
        return o
    }

    private fun busyNow(o: JSONObject): JSONObject {
        val now = System.currentTimeMillis()
        val hit = instances(now - DAY_MS, now + DAY_MS)
            .firstOrNull { !it.allDay && it.begin <= now && now < it.end }
        o.put(Contract.K_OK, true)
        o.put(Contract.K_VALUE, if (hit != null) 1 else 0)
        o.put("scalar", true)
        o.put("unit", "0/1")
        o.put(Contract.K_NOTE, if (hit != null) {
            "此刻在日程里：${hit.title}（${fmt.format(Date(hit.begin))} – ${fmt.format(Date(hit.end))}）"
        } else {
            "此刻没有日程覆盖（★ 只说明**日历上**没有安排）"
        })
        return o
    }

    private fun todayEvents(o: JSONObject): JSONObject {
        val cal = Calendar.getInstance()
        cal.set(Calendar.HOUR_OF_DAY, 0); cal.set(Calendar.MINUTE, 0)
        cal.set(Calendar.SECOND, 0); cal.set(Calendar.MILLISECOND, 0)
        val day0 = cal.timeInMillis
        val list = instances(day0, day0 + DAY_MS)

        val arr = JSONArray()
        for (e in list) {
            arr.put(JSONArray().put(e.begin.toDouble()).put(e.end.toDouble())
                .put(e.minutes.toDouble()).put(if (e.allDay) 1.0 else 0.0))
        }
        o.put(Contract.K_OK, true)
        o.put("scalar", false)
        o.put("windows", arr)
        o.put(Contract.K_VALUE, list.size)      // ★ 这个 0 是**真 0**（今天确实没安排）
        o.put(Contract.K_NOTE, "今天 ${list.size} 件事（含全天）；每行 [开始, 结束, 分钟数, 全天?]；" +
            "★ 全天日程（纪念日/倒计时）也算在内，看第 4 列区分")
        return o
    }

    /** 诊断用：把"到底读到了什么"打成一段人话（给插件的 App 界面看）。 */
    fun summary(): String {
        if (!granted()) return "还没拿到「读取日历」授权 —— 点上面的按钮授一次。"
        val now = System.currentTimeMillis()
        val b = StringBuilder()
        val today = Calendar.getInstance()
        today.set(Calendar.HOUR_OF_DAY, 0); today.set(Calendar.MINUTE, 0)
        today.set(Calendar.SECOND, 0); today.set(Calendar.MILLISECOND, 0)
        val list = instances(today.timeInMillis, today.timeInMillis + DAY_MS)
        b.append("今天：").append(list.size).append(" 件事\n")
        for (e in list.take(8)) {
            b.append("  · ").append(if (e.allDay) "[全天] " else "").append(e.title)
                .append("  ").append(fmt.format(Date(e.begin)))
                .append(" → ").append(fmt.format(Date(e.end))).append('\n')
        }
        val nxt = instances(now, now + HORIZON_MS).firstOrNull { !it.allDay && it.begin >= now }
        b.append(if (nxt == null) "往后 14 天没有（非全天的）日程。"
        else "下一件：${nxt.title}（${fmt.format(Date(nxt.begin))}）")
        return b.toString()
    }
}
