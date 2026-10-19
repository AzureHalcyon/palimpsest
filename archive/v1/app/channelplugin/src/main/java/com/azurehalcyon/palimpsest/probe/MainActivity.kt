package com.azurehalcyon.palimpsest.probe

import android.Manifest
import android.app.Activity
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.widget.ScrollView
import android.widget.TextView
import java.io.File
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

/**
 * 渠道探针（最小 App）—— 回答两个问题：
 *
 *   ① **一个一个传感器到底能不能注册监听、注册上之后到底有没有数据？**
 *   ② **存储自检**：分桶 · 三档落盘 · 交桶 · **重启后还读得到昨天的聚合吗？**
 *
 * ⚠️ 记一个坑（我自己连踩两次）：**Kotlin 注释支持嵌套**（不像 Java）⇒
 *   中文里写"甲 / 乙（乙是加粗）"时，如果**斜杠后面直接跟两个星号**，
 *   那个序列会被当成"开了一个嵌套注释"，而报错却是**文件末尾**的 `Unclosed comment`
 *   （指不到真正的行）。写注释时**斜杠后面留个空格**。
 *
 * 为什么必须实测、不能靠 `dumpsys sensorservice` 的清单推断：
 *   清单里能列出全部传感器（名称/元数据是公开的），但**第三方 App 能不能 `registerListener` 是另一回事**；
 *   而且就算注册返回 `true`，**也可能一个事件都不给**。
 * ⇒ 对每个传感器做三件事：**注册 → 等 400ms 数事件 → 注销**。
 *
 * ★ 存储自检用**假时钟**喂合成数据，所以**不用等一天**就能验"重启后读得到昨天的"。
 */
class MainActivity : Activity(), SensorEventListener {

    private lateinit var sm: SensorManager
    private val counts = HashMap<String, Int>()
    private val sb = StringBuilder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        sm = getSystemService(SENSOR_SERVICE) as SensorManager
        // ★★ 两条运行时权限**一次性申请**（不要连着调两次 `requestPermissions`）：
        //   Android 一次只处理一个权限弹窗，第二个请求会被**静默丢掉**（实测踩过）。
        //   ⚠️ 更要紧的一条：MIUI 会把这两条的 **UID 模式 app-op 焊在 `ignore`** ——
        //   `pm grant` 与 `cmd appops set`（连 `--uid` 一起）都压不过它，
        //   **只有走一次系统弹窗、用户点「允许」**才会变 allow。
        //   ⇒ 这个弹窗不是可选的装饰：不走它，`steps` 和日历那三条都进不了工具表
        //     （而 `dumpsys package` 会骗你：它显示 granted=true）。
        val need = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 29 &&
            checkSelfPermission(Manifest.permission.ACTIVITY_RECOGNITION) != PackageManager.PERMISSION_GRANTED
        ) {
            need += Manifest.permission.ACTIVITY_RECOGNITION
        }
        if (checkSelfPermission(Manifest.permission.READ_CALENDAR) != PackageManager.PERMISSION_GRANTED) {
            need += Manifest.permission.READ_CALENDAR
        }
        if (need.isNotEmpty()) requestPermissions(need.toTypedArray(), 1)
        val tv = TextView(this)
        tv.textSize = 8f
        setContentView(ScrollView(this).apply { addView(tv) })
        Thread {
            val report = try {
                val store = runStoreSelftest()          // ★ 先跑存储自检（快）
                val contract = runChannelContractSelftest()   // ★ 再跑渠道契约自检（五条问题的判据）
                val calReport = runCalendarReport()      // ★ 日历那一路（第二个数据域）
                val auReport = runAlarmUsageReport()     // ★ 闹钟 + 屏幕使用（第三、四个数据域）
                val probe = runProbe()                  // 再跑传感器探针（慢，约 50s）
                store + "\n\n" + contract + "\n\n" + calReport + "\n\n" + auReport + "\n\n" + probe
            } catch (t: Throwable) {
                "探针崩了：$t"
            }
            runOnUiThread { tv.text = report }
        }.start()
    }

    // ================================================================ 闹钟 + 屏幕使用（第三、四个数据域）
    /**
     * 两条判据（与日历同一条规矩：**没准备好就不进工具表**）：
     *   · 闹钟：不需要权限（`AlarmManager.getNextAlarmClock()`），但**没有下一个闹钟时读数如实回 ok=false**；
     *   · 屏幕使用：`PACKAGE_USAGE_STATS` 是 **appop**（`pm grant` 没用）⇒ 没开就不暴露，且这里直接给入口。
     */
    private fun runAlarmUsageReport(): String {
        val r = StringBuilder()
        r.append("================ 闹钟 + 屏幕使用（第三、四个数据域）================\n")
        val al = com.azurehalcyon.palimpsest.probe.alarm.AlarmCore(this)
        r.append("  ── 闹钟（不需要权限）\n")
        r.append("     ").append(al.summary()).append('\n')
        val now = System.currentTimeMillis()
        val a1 = al.fetch("next_alarm_minutes", now - 86_400_000L, now)
        r.append("     next_alarm_minutes：ok=").append(a1.optBoolean("ok"))
        if (a1.has("value")) r.append("  value=").append(a1.opt("value"))
        r.append("\n        ").append(a1.optString("note").take(160)).append('\n')

        val us = com.azurehalcyon.palimpsest.probe.usage.UsageCore(this)
        r.append("  ── 屏幕使用（appop：使用情况访问）\n")
        r.append("     已开：").append(us.granted())
            .append("（★ 没开就不进工具表；入口：设置 → 应用 → 特殊应用权限 → 使用情况访问）\n")
        r.append("     ").append(us.summary()).append('\n')
        for (m in com.azurehalcyon.palimpsest.probe.usage.UsageCore.METRICS) {
            val o = us.fetch(m, now - 86_400_000L, now)
            r.append("     · ").append(m).append("：ok=").append(o.optBoolean("ok"))
            if (o.has("value")) r.append("  value=").append(o.opt("value"))
            r.append("\n        ").append(o.optString("note").take(160)).append('\n')
        }
        return r.toString()
    }

    // ================================================================ 日历（第二个数据域）
    /**
     * 日历那一路的**判据**（与传感器同一套"暴露 ⇔ 真的采得到"的规矩）：
     *   · 没授权 ⇒ **不进工具表**（否则又是一个"永远空"的渠道，见契约自检 ③ 的教训）；
     *   · 授权后 ⇒ 三条读数各自能不能出数（`next_event_minutes` 允许"没有安排"这种空，
     *     但必须**如实说"没有安排"而不是 0**）。
     */
    private fun runCalendarReport(): String {
        val r = StringBuilder()
        val cal = com.azurehalcyon.palimpsest.probe.calendar.CalendarCore(this)
        val granted = cal.granted()
        r.append("================ 日历（第二个数据域）================\n")
        r.append("  READ_CALENDAR 已授权：$granted\n")
        r.append("  暴露进工具表：$granted（★ 没授权就不暴露 —— 与 steps 同一条规矩）\n")
        val now = System.currentTimeMillis()
        for (m in com.azurehalcyon.palimpsest.probe.calendar.CalendarCore.METRICS) {
            val o = cal.fetch(m, now - 86_400_000L, now)
            r.append("  · ").append(m).append("：ok=").append(o.optBoolean("ok"))
            if (o.has("value")) r.append("  value=").append(o.opt("value"))
            r.append("\n      ").append(o.optString("note").take(160)).append('\n')
        }
        r.append("  · 一口气看：").append(cal.summary().replace("\n", " | ")).append('\n')
        return r.toString()
    }

    /** 同一个 type 可能有 wakeup / non-wakeup 两个实例 ⇒ **必须按实例**计数，不能按 type。 */
    private fun key(s: Sensor) = "${s.type}|${s.name}|${s.isWakeUpSensor}"

    private fun isOem(s: Sensor) = s.type >= 0x10000

    /**
     * ⚠️ 这里**故意用数值**而不是 `Sensor.TYPE_*` 常量：
     *   `TYPE_TILT_DETECTOR` / `TYPE_DEVICE_ORIENTATION` / `REPORTING_MODE_SPECIAL`
     *   在公开 SDK 里编不过（实测 `Unresolved reference`）⇒ 用数值最稳，也不影响这次要测的东西。
     */
    private fun reportingMode(s: Sensor) = when (s.reportingMode) {
        0 -> "连续"
        1 -> "变化时"
        2 -> "一次性"
        3 -> "特殊"
        else -> "?"
    }

    // ================================================================ 存储自检
    /**
     * 判据（对应待办清单第 3 项）：
     *   · 分桶正确（均值/极值/条数）
     *   · ★★ **按 n 加权合并**（不是简单平均）
     *   · 三档都落了盘（m/h/d）
     *   · 窗口决定档位（≤3h 分钟档、≤8d 小时档、更长日档）
     *   · ★★ **重新 new 一个 store（= 重启）仍读得到昨天的聚合**
     *   · 保留期到了会删老文件
     */
    private fun runStoreSelftest(): String {
        val r = StringBuilder()
        val dir = File(filesDir, "store_selftest")
        dir.deleteRecursively()
        dir.mkdirs()
        r.append("================ 存储自检（假时钟，不用等一天）================\n")
        val pass = StringBuilder()
        fun ck(name: String, cond: Boolean, detail: String = "") {
            r.append(if (cond) "  ok   " else "  FAIL ").append(name)
            if (detail.isNotEmpty()) r.append(" · ").append(detail)
            r.append('\n')
            if (cond) pass.append(".") else pass.append("X")
        }

        // 固定基准：取"今天"当地零点，再往前挪一天 ⇒ 昨天/今天各一段数据
        val cal = java.util.Calendar.getInstance()
        cal.set(java.util.Calendar.HOUR_OF_DAY, 0)
        cal.set(java.util.Calendar.MINUTE, 0)
        cal.set(java.util.Calendar.SECOND, 0)
        cal.set(java.util.Calendar.MILLISECOND, 0)
        val today0 = cal.timeInMillis
        val yest0 = today0 - SensorStore.DAY_MS
        val MIN = SensorStore.MIN_MS
        val HOUR = SensorStore.HOUR_MS

        val st = SensorStore(dir)
        // ---- 昨天 10:00–10:02：三分钟，每分 2 条 -------------------------------------------------
        var t = yest0 + 10 * HOUR
        st.record("accel", t, 10f); st.record("accel", t + 1000, 12f)          // 桶1 mean=11 n=2
        st.record("accel", t + MIN, 20f); st.record("accel", t + MIN + 1000, 20f)  // 桶2 mean=20 n=2
        st.record("accel", t + 2 * MIN, 30f)                                    // 桶3 mean=30 n=1
        // ---- 今天 09:00：一分钟 -----------------------------------------------------------------
        val t2 = today0 + 9 * HOUR
        st.record("accel", t2, 40f)
        // ★ 再记一个**名字里带下划线**的 metric：文件名切成 `<metric>_<日期>.csv`，
        //   按第一个下划线切就会把 `activity_raw` 读成 `activity`（自检抓到过这个 bug）。
        st.record("activity_raw", t2, 7f)
        st.flush()

        ck("★ 分钟档落了盘（m/ 目录有文件）",
            File(dir, "m").listFiles()?.isNotEmpty() == true,
            (File(dir, "m").listFiles()?.joinToString { it.name } ?: "空"))
        ck("★ 小时档落了盘（h/）", File(dir, "h").listFiles()?.isNotEmpty() == true)
        ck("★ 日档落了盘（d/）", File(dir, "d").listFiles()?.isNotEmpty() == true)
        ck("  滚动关掉过桶（诊断计数 > 0）",
            st.closedMinutes > 0 && st.closedHours > 0 && st.closedDays > 0,
            "min=${st.closedMinutes} hour=${st.closedHours} day=${st.closedDays}")

        // ---- 窗口决定档位 + 桶值对 --------------------------------------------------------------
        val (lvMin, rowsMin) = st.fetch("accel", yest0 + 9 * HOUR, yest0 + 11 * HOUR)
        ck("★ ≤3h 窗口 ⇒ 用分钟档", lvMin == MIN, "level=$lvMin")
        ck("  分桶值对（首桶均值 11、n=2）",
            rowsMin.size == 3 && rowsMin[0][1] == 11.0 && rowsMin[0][4] == 2.0,
            rowsMin.joinToString(" | ") { it.joinToString(",") })

        val (lvHour, rowsHour) = st.fetch("accel", yest0, yest0 + 2 * SensorStore.DAY_MS)
        ck("★ ≤8d 窗口 ⇒ 用小时档", lvHour == HOUR, "level=$lvHour")
        // 昨天那一小时：桶1(mean11,n2)+桶2(mean20,n2)+桶3(mean30,n1) ⇒ 加权 (22+40+30)/5 = 18.4
        val yHour = rowsHour.firstOrNull { it[0] <= yest0 + 10 * HOUR && yest0 + 10 * HOUR < it[0] + HOUR }
        ck("★★ 小时桶**按 n 加权** ⇒ (11×2+20×2+30×1)/5 = 18.4，而不是简单平均 20.33",
            yHour != null && Math.abs(yHour[1] - 18.4) < 1e-6,
            "hour=" + (yHour?.joinToString(",") ?: "没找到"))

        val (lvDay, _) = st.fetch("accel", yest0 - 30 * SensorStore.DAY_MS, today0 + HOUR)
        ck("★ 更长窗口 ⇒ 用日档", lvDay == SensorStore.DAY_MS, "level=$lvDay")

        // ---- ★★ 重启（同一目录新建 store）仍读得到昨天的 -----------------------------------------
        val st2 = SensorStore(dir)
        val (lv2, rows2) = st2.fetch("accel", yest0 + 9 * HOUR, yest0 + 11 * HOUR)
        ck("★★ **重启后仍读得到昨天的聚合**（判据）",
            lv2 == MIN && rows2.size == 3 && rows2[0][1] == 11.0,
            "level=$lv2 rows=${rows2.size}")
        ck("★ 重启后也能枚举出有哪些 metric", st2.metrics().contains("accel"),
            st2.metrics().toString())
        ck("★★ 名字里带下划线的 metric **不许被切错**（`activity_raw` 不是 `activity`）",
            st2.metrics().contains("activity_raw") && !st2.metrics().contains("activity"),
            st2.metrics().toString())

        // ---- 保留期 ---------------------------------------------------------------------------
        val dell = st2.prune(today0 + 800L * SensorStore.DAY_MS)
        ck("★ 保留期到了 ⇒ 删老文件（返回删了几个）", dell > 0, "删了 $dell 个")
        val (_, afterPrune) = st2.fetch("accel", yest0 + 9 * HOUR, yest0 + 11 * HOUR)
        ck("  删完就读不到了（说明删的确实是它）", afterPrune.isEmpty(), "剩 ${afterPrune.size} 桶")

        // ---- ★★ 实测路径：真跑 3 秒，桶的时刻必须是**墙钟**（不是开机以来的纳秒）------------
        //   这条是"合成数据自检发现不了"的那一类：合成数据自己喂的就是墙钟，
        //   而真机的 SensorEvent.timestamp 是 elapsedRealtimeNanos() ——
        //   第一版直接拿它当墙钟 ⇒ 桶写在"开机后第 83 分钟"，查询永远查不到（端到端才逼出来）。
        // ★ 用**进程内单例**采集器：再 new 一个就会把同一批传感器注册第二遍
        //   （真机上就是这么把 triggers.csv 写成一对一对的，见 SensorRecorder 的注释）。
        // ★★ **诊断不许 stop 它**（2026-10-05 真机抓到）：`stop()` 会 `flush()`，
        //   把"这一分钟攒着的桶"落盘；而采集器随后又被重新开采 ⇒
        //   同一个分钟会落出**两行**（真机 `accel` 上就是一行 n=17、一行 n=1）。
        //   ⇒ 自检只 `start()`，**从不 stop**（生产路径本来也是"有人问就开采、不主动停"）。
        val live = SensorRecorder.get(this)
        live.start()
        Thread.sleep(3200)
        val nowWall = System.currentTimeMillis()
        val (lvLive, rowsLive) = live.store.fetch("accel", nowWall - 5 * MIN, nowWall)
        val t0 = rowsLive.firstOrNull()?.get(0)
        ck("★★ 实测：真跑 3 秒就有桶（合成数据测不到这条）", rowsLive.isNotEmpty(),
            "level=$lvLive rows=${rowsLive.size}")
        ck("★★ 桶的时刻是**墙钟**（和当前时间差 <10 分钟，而不是「开机以来的毫秒」）",
            t0 != null && Math.abs(t0 - nowWall) < 10 * MIN,
            "桶时刻=" + t0 + " 现在=" + nowWall + " 差秒=" + (if (t0 != null) (nowWall - t0) / 1000 else -1))
        val wm = live.wrappedMetrics().map { it.first }
        ck("★ 包装层有名字（包一层方法，不是把原始触发次数丢给模型）",
            wm.contains("since_motion_minutes") && wm.contains("still_minutes"), wm.toString())
        val rw = live.fetch("since_motion_minutes", nowWall - 5 * MIN, nowWall)
        val rwNote = rw.optString("note")
        ck("★★ 触发通道没武装上时，必须说「没能武装上」——不许说成「他很久没动」",
            live.exposable("since_motion_minutes") || rwNote.contains("没能武装上"),
            rwNote.take(160))

        r.append("  小结：$pass\n")
        try {
            File(filesDir, "store-selftest.txt").writeText(r.toString())
        } catch (_: Throwable) {
        }
        return r.toString()
    }

    // ================================================================ 渠道契约自检
    /**
     * 第 21 轮查出的**五条**，每条一个可复现的判据（在真机上跑，不用等几天）：
     *   ① 触发后重新武装（机制）· ② 触发记录去重 · ③ 触发类型对齐（记录=查询、武装成功才暴露）
     *   · ④ 读取按时间排序 · ⑤ OEM 私有号段不暴露、也不给读数
     */
    private fun runChannelContractSelftest(): String {
        val r = StringBuilder()
        val pass = StringBuilder()
        r.append("================ 渠道契约自检（第 21 轮那五条）================\n")
        fun ck(name: String, cond: Boolean, detail: String = "") {
            r.append(if (cond) "  ok   " else "  FAIL ").append(name)
            if (detail.isNotEmpty()) r.append(" · ").append(detail)
            r.append('\n')
            if (cond) pass.append(".") else pass.append("X")
        }

        val live = SensorRecorder.get(this)
        live.start()          // ★ 只开采，**不 stop**（见上面那条注释：stop 会把同一分钟落两行）
        val now = System.currentTimeMillis()
        val MIN = SensorStore.MIN_MS
        val desc = PluginCore.get(this).describe()
        val exposedIds = Regex("\"id\":\"([a-z_]+)\"").findAll(desc)
            .joinToString(",") { it.groupValues[1] }

        // ---- ①③ 触发通道的真实状态（原始证据直接留在报告里）--------------------------------
        r.append("  · 触发通道现状（①③ 的原始证据）：\n")
        val wrapped = live.wrappedMetrics()
        for ((metric, trigger, label) in wrapped) {
            val one = live.fetch(metric, now - 5 * MIN, now)
            r.append("    - $metric ← $trigger（$label）· 暴露=${live.exposable(metric)}\n")
            r.append("        ${live.stateOf(metric)}\n")
            r.append("        ok=${one.optBoolean("ok")} note=${one.optString("note").take(150)}\n")
        }
        r.append("    · 工具表里的 id：$exposedIds\n")

        ck("★ 暴露出去的包装读数，底层触发通道必须**武装成功过**",
            wrapped.all { !live.exposable(it.first) || live.stateOf(it.first).contains("已武装") },
            wrapped.joinToString(" ") { "${it.first}=${live.exposable(it.first)}" })
        ck("★★ 采不到 / 武装不上的渠道**不许进工具表**（否则那里挂着一个永远空的工具）",
            wrapped.none { !live.exposable(it.first) && desc.contains("\"id\":\"${it.first}\"") },
            exposedIds)
        ck("★★ 「没能武装上」与「武装了但还没触发过」**必须说成两件事**",
            wrapped.all { (metric, _, _) ->
                val r = live.fetch(metric, now - 5 * MIN, now)
                val n = r.optString("note")
                when {
                    // 没武装上 ⇒ 说"没采到"，不许说成"他很久没动"
                    !live.exposable(metric) -> n.contains("没能武装上")
                    // 有触发记录 ⇒ 给的是真读数
                    r.optBoolean("ok") -> n.contains("由触发式传感器")
                    // 武装着但还没触发过 ⇒ 只能"不知道"（不是 0）
                    else -> n.contains("不知道")
                }
            },
            wrapped.joinToString(" ") { it.first })
        ck("★ 包装读数与它的触发通道是**成对写死的**（不许一边记一个、一边查另一个）",
            wrapped.associate { it.first to it.second } == mapOf(
                "since_motion_minutes" to "significant_motion",
                "still_minutes" to "stationary"),
            wrapped.joinToString(" ") { "${it.first}←${it.second}" })
        ck("★ 查的那个触发通道**必须是我们真的在记的渠道**（`onTrigger` 记的就是这个 id）",
            wrapped.all { (_, trigger, _) -> live.metrics().any { it.first == trigger } },
            live.metrics().joinToString(",") { it.first })

        // ---- ⑤ OEM 私有号段：不许暴露、不许给读数、不进工具表 ------------------------------
        val priv = live.collectedPrivate()
        ck("★ OEM 私有号段被识别出来（这台机器上应该有：har_wakeup / activity_recognition / DrivingDetection…）",
            priv.isNotEmpty(), priv.toString())
        ck("⛔ OEM 私有号段**一个都不许可暴露**", priv.none { live.exposable(it) }, priv.toString())
        ck("⛔ OEM 私有号段的读数**直接拒答**（不给一个我们无权解释的数字）",
            priv.all { live.fetch(it, now - 5 * MIN, now).optString("note").contains("OEM 私有号段") },
            priv.toString())
        ck("⛔ OEM 私有号段不许出现在工具表里",
            priv.none { desc.contains("\"id\":\"$it\"") }, exposedIds)

        // ---- ② 触发记录去重（写入侧）----------------------------------------------------
        val dir = File(filesDir, "contract_selftest")
        dir.deleteRecursively()
        dir.mkdirs()
        val st = SensorStore(dir)
        val t0 = System.currentTimeMillis()
        st.markTrigger("stationary", t0)
        st.markTrigger("stationary", t0)                    // ★ 同一时刻再来一次 ⇒ 不许多一行
        val rows1 = File(dir, "triggers.csv").readLines().filter { it.isNotBlank() }
        st.markTrigger("stationary", t0 + 1)                // 不同时刻 ⇒ 要记
        val rows2 = File(dir, "triggers.csv").readLines().filter { it.isNotBlank() }
        ck("★★ 同一时刻的触发**只记一行**（虚增命中次数的病根）", rows1.size == 1, rows1.toString())
        ck("  不同时刻照记（去重不许把真事件吃掉）", rows2.size == 2, rows2.toString())
        ck("  `lastTrigger` 取的是最近那一次", st.lastTrigger("stationary") == t0 + 1,
            "${st.lastTrigger("stationary")}")
        File(dir, "triggers.csv").appendText("stationary,177067904\n")
        val dropped = st.pruneTriggers(System.currentTimeMillis())
        ck("★ 清掉墙钟修复前的历史行（t < 1e12 ⇒ 必然是 elapsedRealtime 误当墙钟）",
            dropped == 1 && st.lastTrigger("stationary") == t0 + 1, "清掉 $dropped 行")

        // ---- ④ 读取按时间排序（故意写一个乱序的档）----------------------------------------
        val dir2 = File(filesDir, "contract_sort")
        dir2.deleteRecursively()
        dir2.mkdirs()
        val st2 = SensorStore(dir2)
        val cal = Calendar.getInstance()
        cal.set(Calendar.HOUR_OF_DAY, 0)
        cal.set(Calendar.MINUTE, 0)
        cal.set(Calendar.SECOND, 0)
        cal.set(Calendar.MILLISECOND, 0)
        val day0 = cal.timeInMillis
        val dayName = SimpleDateFormat("yyyyMMdd", Locale.US).format(java.util.Date(day0))
        File(dir2, "m").mkdirs()
        // ★ 真机 posture_raw 就是这么乱的：写入是**追加**，最后那个桶要等 flush 才落 ⇒ 排到后面去
        File(File(dir2, "m"), "sorttest_$dayName.csv")
            .writeText("${day0 + 60000},1,1,1,1\n${day0 + 180000},3,3,3,1\n${day0 + 120000},2,2,2,1\n")
        val (lv, rows) = st2.fetch("sorttest", day0, day0 + 2 * SensorStore.HOUR_MS)
        val ts = rows.map { it[0] }
        ck("★★ 读出来的桶**按时间排好序**（档里乱序也一样）",
            ts.size == 3 && ts == ts.sorted(), "level=$lv t=$ts")
        // ★ 这个窗口**故意从当地零点开始**：文件名里的日期是当地日期，而候选档的推进是 UTC 整日，
        //   两者在"当地 00:00–08:00"会错开一天 ⇒ 当天的档会被漏掉（真 bug，已修 `filesFor`）。
        ck("★★ 窗口从**当地零点**起也能读到当天的档（当地日期 ≠ UTC 整日这条错位）",
            rows.size == 3, "level=$lv rows=${rows.size}")

        // ---- ★★ 同一个桶被写了两遍：读的时候要**按 n 加权合并**（真机 accel 上抓到的第 6 条）----
        val dir3 = File(filesDir, "contract_dup")
        dir3.deleteRecursively()
        dir3.mkdirs()
        File(dir3, "m").mkdirs()
        // 真机原样：同一分钟一行 n=17、紧接着一行 n=1（"先 flush 一次、再重新开采"的产物）
        File(File(dir3, "m"), "duptest_$dayName.csv").writeText(
            "${day0 + 60000},10,8,12,2\n${day0 + 60000},20,20,20,1\n${day0 + 120000},5,5,5,1\n")
        val (lv3, rows3) = SensorStore(dir3).fetch("duptest", day0, day0 + 2 * SensorStore.HOUR_MS)
        val merged = rows3.firstOrNull { it[0] == (day0 + 60000).toDouble() }
        ck("★★ 同一分钟的重复行**合并成一条**（不合并 ⇒ 分位/覆盖率把同一分钟算两次）",
            rows3.size == 2, "level=$lv3 rows=${rows3.size} t=${rows3.map { it[0] }}")
        ck("  合并按 **n 加权**：(10×2 + 20×1)/3 = 13.33，极值取 8/20，n=3",
            merged != null && Math.abs(merged[1] - 40.0 / 3) < 1e-9 &&
                merged[2] == 8.0 && merged[3] == 20.0 && merged[4] == 3.0,
            merged?.joinToString(",") ?: "没找到")

        r.append("  小结：$pass\n")     // ★ 这里**不 stop** 共享采集器（理由见上面 store 自检那段）
        try {
            File(filesDir, "contract-selftest.txt").writeText(r.toString())
        } catch (_: Throwable) {
        }
        return r.toString()
    }

    // ================================================================ 传感器探针
    private fun runProbe(): String {
        sb.setLength(0)
        val all = sm.getSensorList(Sensor.TYPE_ALL)
        sb.append("================ 传感器探针 ================\n")
        sb.append("设备=").append(Build.MODEL)
            .append("  SDK=").append(Build.VERSION.SDK_INT)
            .append("  传感器总数=").append(all.size).append('\n')
        sb.append("读法：r=注册返回  ev=400ms 内收到的事件数\n")
        sb.append("  ⚠️ ev=0 不一定是失败：一次性/变化时类传感器本来就是「有事才报」\n")
        sb.append("  ⚠️ r=false 才是硬失败\n")
        sb.append("顺序：小米 OEM 类型 + wakeup 优先\n")
        sb.append("--------------------------------------------------------------\n")

        val sorted = all.sortedWith(
            compareByDescending<Sensor> { isOem(it) }
                .thenByDescending { it.isWakeUpSensor }
                .thenBy { it.type }
        )

        for (s in sorted) {
            val k = key(s)
            counts[k] = 0
            val ok = try {
                sm.registerListener(this, s, SensorManager.SENSOR_DELAY_NORMAL)
            } catch (t: Throwable) {
                false
            }
            try {
                Thread.sleep(400)
            } catch (_: InterruptedException) {
            }
            try {
                sm.unregisterListener(this, s)
            } catch (_: Throwable) {
            }
            val n = counts[k] ?: 0

            sb.append(
                String.format(
                    "r=%-5s ev=%-4d %-7s oem=%-5s wake=%-5s type=%-8d %-16s %-22s %s%n",
                    ok, n, reportingMode(s), isOem(s), s.isWakeUpSensor,
                    s.type, s.vendor ?: "?", typeShort(s), s.name ?: "?"
                )
            )
            save()   // ★ 增量落盘
        }

        sb.append("--------------------------------------------------------------\n")
        sb.append("完事。这份报告已写在 files/sensor-probe.txt\n")
        save()
        Log.i(TAG, "PROBE DONE 传感器=${all.size}")
        return sb.toString()
    }

    /** 把 type 的已知名字标出来（用数值，见 [reportingMode] 那条注释的原因）。 */
    private fun typeShort(s: Sensor): String = when (s.type) {
        1 -> "加速度"
        2 -> "磁力"
        3 -> "朝向(旧)"
        4 -> "陀螺"
        5 -> "光照"
        6 -> "气压"
        7 -> "温度"
        8 -> "接近"
        9 -> "重力"
        10 -> "线性加速度"
        11 -> "旋转矢量"
        12 -> "相对湿度"
        13 -> "环境温度"
        14 -> "未校准磁力"
        15 -> "游戏旋转"
        16 -> "未校准陀螺"
        17 -> "显著运动"
        18 -> "步伐检测"
        19 -> "计步"
        20 -> "地磁旋转"
        21 -> "心率"
        22 -> "倾斜检测"
        23 -> "唤醒手势"
        25 -> "拿起手势"
        27 -> "设备朝向"
        29 -> "静止检测"
        30 -> "运动检测"
        32 -> "动态元"
        34 -> "离体检测"
        35 -> "未校准加速度"
        36 -> "折叠角"
        else -> if (isOem(s)) "【小米OEM】" else "?"
    }

    private fun save() {
        try {
            File(filesDir, "sensor-probe.txt").writeText(sb.toString())
        } catch (t: Throwable) {
            Log.w(TAG, "落盘失败: $t")
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        val k = key(event.sensor)
        counts[k] = (counts[k] ?: 0) + 1
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    companion object {
        private const val TAG = "channelplugin"
    }
}
