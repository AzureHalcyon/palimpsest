package com.azurehalcyon.palimpsest.probe

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.hardware.TriggerEvent
import android.hardware.TriggerEventListener
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 传感器采集与**落盘**。插件里唯一懂传感器的地方。
 *
 * ★★ 核心规矩（用户原话「这些传感器按各自的方法调用就 OK」）：
 *   同一个 `SensorManager` 上，**不同种类的传感器必须用不同的注册方法**，用错了就是"注册失败"，
 *   而且报错会让人误以为是权限/机型问题 —— 这正是探针实测抓到的那条：
 *
 *   | 种类 | 方法 | 监听接口 |
 *   |---|---|---|
 *   | 连续 / 变化时（光照、加速度、HAR…） | `registerListener` | `SensorEventListener` |
 *   | ★ **触发式**（显著运动、静止、运动检测） | `requestTriggerSensor` | ★ **`TriggerEventListener`** |
 *
 * ★★ 2026-10-05 起：采样不再只躺在内存环形缓冲里（那样**解绑就丢**、绑上立刻读是空的），
 *   而是**压成标量后交给 `SensorStore` 落盘**（三档：1min/1h/1day）。
 *   `fetch()` 只从存储**交桶**（`[t,mean,lo,hi,n]`），**趋势由宿主算**（见 `SensorStore` 的说明）。
 *
 * ★★ 2026-10-05 第二轮（五条真问题，判据都在当时的记录（不在本仓）第 21 轮）：
 *   ① **触发后必须重新武装** —— significant motion 是**一次性**的（官方原话 "then it disables
 *      itself"）⇒ 不重新武装，"显著运动"这条路**只响一次**。
 *   ② **同一个事件不许记两遍** —— 同一个 type 常有 wakeup / non-wakeup 两个实例，
 *      两个都注册就会各报一次（真机 `triggers.csv` 里同时间戳成对出现就是这么来的）。
 *      ⇒ 一个渠道**只认一个实例**，外加"一个进程只有一个采集器"（见 [get]）。
 *   ③ **记的类型 = 查的类型** —— 以前记的是 `stationary`、查的是 `significant_motion`，
 *      于是 `since_motion_minutes` 永远没有数据。现在包装层的触发通道是**同一个字符串**，
 *      而且**没武装成功的触发式渠道不许暴露**（否则工具表里永远挂着一个空渠道）。
 *   ④ **读时排序**（见 `SensorStore.fetch`）。
 *   ⑤ ⛔ **OEM 私有号段不许暴露** —— `har_wakeup`=33171070 / `activity_recognition`=33171090 /
 *      `DrivingDetection`=33171091 都是小米/QTI 私有类型，**官方文档里没有** ⇒
 *      `values` **没有可依据的语义**（实测 `activity_raw` 恒为 `5,5,5`，信息量为 0）。
 *      ⇒ 仍在采集（留着将来对照），但 `exposable()` 为 false、`fetch()` 直接拒答。
 *
 * ⚠️ 因此**别把 OEM 的数字翻译成"他在走路"这种话** —— 那不是谨慎，那是没有依据。
 */
class SensorRecorder internal constructor(
    private val ctx: Context,
    /** ★ 落盘目录。默认就是应用私有 `files/sensors/`；可注入是为了自检能写到别处。 */
    root: File = File(ctx.filesDir, "sensors"),
) :
    // ⚠️ `TriggerEventListener` 是**抽象类**（不是接口）⇒ 必须带构造函数调用，且父类要写在前面。
    TriggerEventListener(), SensorEventListener {

    private val sm = ctx.getSystemService(Context.SENSOR_SERVICE) as SensorManager

    /** ★ 落盘目录：应用私有 `files/sensors/`（`run-as` 拉得出来，别的 App 读不到）。 */
    val store = SensorStore(root)

    /** 注册方式：**按种类分**，不是按名字分。 */
    private enum class Kind { LISTEN, TRIGGER }

    private class Chan(val metric: String, val kind: Kind) {
        var sensor: Sensor? = null

        /** 厂商私有号段（`type >= 0x10000`）⇒ ⛔ 不对外提供（见类注释 ⑤）。 */
        var oemPrivate = false

        /** ★ **成功注册/武装过**（LISTEN=注册成功；TRIGGER=武装成功）—— 暴露与否就看它。 */
        var everArmed = false

        /** ★ **此刻**是否武装着：触发式传感器触发后会自己失效，所以它与 `everArmed` 是两件事。 */
        var armedNow = false

        val name: String get() = sensor?.name ?: "?"
        val wake: String get() = if (sensor?.isWakeUpSensor == true) "wakeup" else "non-wakeup"
    }

    private val chans = LinkedHashMap<String, Chan>()
    private val notes = HashMap<String, String>()

    /** 每个 metric **最近一次**的原始 values —— 只为"语义验证"那一项留着（不落盘，只留最新）。 */
    private val lastRaw = HashMap<String, FloatArray>()

    /** 同一渠道**没注册**的那些实例（写进 note，让"为什么只注册了一个"看得见）。 */
    private val skipped = HashMap<String, String>()

    companion object {
        /**
         * ★★ **一个进程只能有一个采集器。**
         *
         * 为什么必须这样（真机抓到的后果）：插件这个 App 里，provider（传输层）、
         * service（规范参照）和诊断 Activity 会各建一个 `PluginCore`。每个 `PluginCore`
         * 自己 new 一个 `SensorRecorder` ⇒ **同一个传感器被注册两遍** ⇒ 每个物理事件
         * 落两次盘（`triggers.csv` 里同时间戳两行、`posture_raw` 里整行重复）。
         * ⇒ 这里做成**进程内单例**，从根上让"注册两路"不可能发生。
         */
        @Volatile
        private var shared: SensorRecorder? = null

        fun get(ctx: Context): SensorRecorder =
            shared ?: synchronized(this) {
                shared ?: SensorRecorder(ctx.applicationContext).also { shared = it }
            }

        /** 厂商私有号段的判据：`Sensor.TYPE_DEVICE_PRIVATE_BASE == 0x10000`。 */
        fun isOemPrivate(type: Int): Boolean = type >= 0x10000
    }

    // ---------------------------------------------------------------- 选渠道
    private fun pick(): List<Chan> {
        val all = sm.getSensorList(Sensor.TYPE_ALL)
        val out = mutableListOf<Chan>()

        fun add(metric: String, kind: Kind, pred: (Sensor) -> Boolean) {
            val hit = all.filter(pred)
            if (hit.isEmpty()) {
                notes[metric] = "这台机器上没有这个传感器（降级：该读数不可用）"
                return
            }
            // ★★ **一个渠道只认一个实例**（修的是"同一事件记两遍"）：
            //   同一个 type 常同时有 wakeup / non-wakeup 两个实例，两个都注册 ⇒ 各报一次。
            //   挑法：触发式挑 wakeup（不 wakeup 的息屏根本不响），
            //         连续/变化时挑 non-wakeup（不为记一个光照值去唤醒 AP）。
            val wantWake = kind == Kind.TRIGGER
            val picked = hit.firstOrNull { it.isWakeUpSensor == wantWake } ?: hit.first()
            val c = Chan(metric, kind)
            c.sensor = picked
            c.oemPrivate = isOemPrivate(picked.type)
            out += c
            if (hit.size > 1) {
                skipped[metric] = hit.filter { it !== picked }
                    .joinToString("、") { "${it.name}(${if (it.isWakeUpSensor) "wakeup" else "non-wakeup"})" }
            }
        }

        // ★ 触发式：标准 type 17/29/30 —— 用错方法必失败（探针已验）
        add("significant_motion", Kind.TRIGGER) { it.type == 17 }
        add("stationary", Kind.TRIGGER) { it.type == 29 }
        add("motion_detect", Kind.TRIGGER) { it.type == 30 }

        // 标准连续/变化时
        add("light", Kind.LISTEN) { it.type == 5 }
        add("accel", Kind.LISTEN) { it.type == 1 }
        add("steps", Kind.LISTEN) { it.type == 19 }          // ⚠️ 需要 ACTIVITY_RECOGNITION
        add("orientation", Kind.LISTEN) { it.type == 27 }

        // ★ 小米 OEM（有就用；没有 notes 里会写"降级"）
        //   ⚠️ metric id **必须与 `PluginCore.describe()` 里那个 id 逐字一致**（踩过：`activity` vs `activity_raw`）。
        //   ⛔ 这五条**采集但不暴露**（OEM 私有号段，values 语义无从解释）—— 见类注释 ⑤。
        add("activity_raw", Kind.LISTEN) { it.name?.contains("har", true) == true }
        add("activity_type_raw", Kind.LISTEN) { it.name?.contains("activity_recognition", true) == true }
        add("driving_raw", Kind.LISTEN) { it.name?.contains("DrivingDetection", true) == true }
        add("posture_raw", Kind.LISTEN) {
            val n = it.name ?: ""
            n.contains("GripPosture", true) || n.contains("pocket_mode", true) || n.contains("holder", true)
        }
        add("pickup_raw", Kind.LISTEN) { it.name?.contains("pickup", true) == true }
        add("screen_down_raw", Kind.LISTEN) { it.name?.contains("screen_down", true) == true }

        return out
    }

    // ---------------------------------------------------------------- 起停
    fun start() {
        if (chans.isNotEmpty()) return
        store.prune(System.currentTimeMillis())            // ★ 每次连上顺手清一次老档
        store.pruneTriggers(System.currentTimeMillis())    // ★ 触发记录也按保留期清（顺带清掉墙钟 bug 时代的行）
        for (c in pick()) {
            chans[c.metric] = c
            arm(c)
        }
    }

    /**
     * 注册 / 武装一个渠道。
     * ★ **触发式的返回值就是"武装成功没有"，必须记下来** —— 否则"武装失败"与
     *   "还没触发过"在上层是同一个 `ok:false`，会被读成"他确实很久没动"（这是修 ③ 的关键）。
     */
    private fun arm(c: Chan) {
        val s = c.sensor ?: return
        val ok = try {
            when (c.kind) {
                Kind.LISTEN -> sm.registerListener(this, s, SensorManager.SENSOR_DELAY_NORMAL)
                Kind.TRIGGER -> sm.requestTriggerSensor(this, s)
            }
        } catch (t: Throwable) {
            false
        }
        c.armedNow = ok
        if (ok) c.everArmed = true
        refreshNote(c, if (ok) "注册成功" else "★ 注册失败")
    }

    /**
     * ★★ **触发后重新武装**（官方 sensors_motion 指南原话：significant motion
     * 「triggers an event each time significant motion is detected and **then it disables itself**」）。
     * ⛔ 不重新武装 ⇒ 这条渠道**一辈子只响一次**，`since_motion_minutes` 从此永远"还没有触发过"。
     * ★ 就在 `onTrigger` 里重新武装 —— 这也是官方给的用法。
     */
    private fun rearm(c: Chan) {
        val s = c.sensor ?: return
        val ok = try {
            sm.requestTriggerSensor(this, s)
        } catch (t: Throwable) {
            false
        }
        c.armedNow = ok
        if (ok) c.everArmed = true
        refreshNote(c, if (ok) "触发后已重新武装" else "★ 触发后重新武装失败")
    }

    /** 把"这个渠道当前是什么状态"拼成一行（`fetch()` 会把它带给宿主，宿主再带给模型）。 */
    private fun refreshNote(c: Chan, what: String) {
        val b = StringBuilder(what).append('(').append(c.name).append(", ").append(c.wake).append(')')
        if (c.kind == Kind.TRIGGER) {
            b.append(if (c.armedNow) "·已武装" else "·★此刻没武装上")
        }
        skipped[c.metric]?.let { b.append("；同一传感器的另一个实例**没注册**（").append(it).append("）") }
        if (c.oemPrivate) {
            b.append("；⛔ OEM 私有号段 type=").append(c.sensor?.type)
                .append("（官方文档没有 ⇒ values 语义无从解释，不对外提供读数）")
        }
        notes[c.metric] = b.toString()
    }

    /** ★ 停之前**先把没落的桶落下去** —— 否则解绑就丢（这正是第一版实测撞到的坑）。 */
    fun stop() {
        try {
            store.flush()
        } catch (_: Throwable) {
        }
        for (c in chans.values) for (s in listOfNotNull(c.sensor)) {
            try {
                when (c.kind) {
                    Kind.LISTEN -> sm.unregisterListener(this, s)
                    Kind.TRIGGER -> sm.cancelTriggerSensor(this, s)
                }
            } catch (_: Throwable) {
            }
        }
        chans.clear()
    }

    // ---------------------------------------------------------------- 收数据
    /**
     * ★★ **把事件时刻换成墙钟毫秒** —— 这是一个**踩过的真 bug**：
     *
     *   `SensorEvent.timestamp` / `TriggerEvent.timestamp` 是 **`elapsedRealtimeNanos()`**
     *   （**开机以来的纳秒**），**不是** `System.currentTimeMillis()`。
     *   而桶是**按当地日/时/分**切的、查询用墙钟 ⇒ 直接拿事件时间戳当墙钟，
     *   桶会被写在"开机后第 83 分钟"（t≈5e6），而查询问的是 1.79e12 ⇒ **永远查不到**。
     *
     *   ⚠️ 合成数据的自检**发现不了**这个（它自己喂的就是墙钟）——
     *     是靠"真机上跑一段再读"才逼出来的。⇒ 自检里也补了一条**实测桶时刻**的检查。
     *   ★ 后遗症：修复前写下的行留在 `triggers.csv` 里（如 `stationary,177067904`），
     *     `SensorStore.pruneTriggers()` 会按 `WALL_FLOOR_MS` 把它们清掉。
     */
    private fun wallOf(eventNanos: Long): Long {
        val nowWall = System.currentTimeMillis()
        val nowNanos = android.os.SystemClock.elapsedRealtimeNanos()
        val dtMs = (nowNanos - eventNanos) / 1_000_000L
        return nowWall - dtMs
    }

    private fun record(metric: String, tWallMs: Long, v: FloatArray) {
        lastRaw[metric] = v.copyOf()
        try {
            store.record(metric, tWallMs, scalar(metric, v))
        } catch (_: Throwable) {
            // ★ 落盘失败不许把监听搞崩：丢一个采样比断一个渠道轻
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        val c = chanOf(event.sensor) ?: return
        record(c.metric, wallOf(event.timestamp), event.values)
    }

    override fun onTrigger(event: TriggerEvent) {
        val c = chanOf(event.sensor) ?: return
        val tMs = wallOf(event.timestamp)
        // ★ 触发式传感器包装层要的就是这个时刻（「距上次触发多久」）
        store.markTrigger(c.metric, tMs)
        record(c.metric, tMs, event.values)
        // ★★ ① 一次性传感器：**必须重新武装**，否则这条路只响一次
        rearm(c)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    /** 事件属于哪个渠道：**按实例**（name + type）认，不按 type 认（同名不同实例是常态）。 */
    private fun chanOf(s: Sensor): Chan? =
        chans.values.firstOrNull { it.sensor?.name == s.name && it.sensor?.type == s.type }

    /**
     * 把一条 `values` 压成一个标量（趋势统计只能对付标量）。
     *
     * ★ 只有 `accel` 是**明确知道怎么压**的：三轴模长（静止≈9.8，走动起伏）。
     *   其余一律取 `values[0]` 并**不动语义**。
     */
    fun scalar(metric: String, v: FloatArray): Float = when (metric) {
        "accel" -> {
            if (v.size >= 3) kotlin.math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
            else v.getOrElse(0) { 0f }
        }
        else -> v.getOrElse(0) { 0f }
    }

    fun metrics(): List<Pair<String, String>> = chans.values.map { it.metric to it.name }

    fun note(metric: String): String = notes[metric] ?: ""

    // ---------------------------------------------------------------- ★ 暴露与否
    /**
     * ★★ **能不能给模型看**（这是 ③⑤ 两条的落点，一条规则管两件事）：
     *   ① 这个渠道真的要在这台机器上（`pick()` 找得到）；
     *   ② ★ **真的采得到**：LISTEN=注册成功过；TRIGGER=**武装成功过**
     *      —— 否则"还没有触发过"会永远成立，工具表里就挂着一个永远空的渠道（正是 `since_motion_minutes` 的病）；
     *   ③ ⛔ **不是 OEM 私有号段**（`values` 语义无从解释，见类注释 ⑤）。
     *
     * 包装层（`since_motion_minutes` / `still_minutes`）看的是**它底下那个触发通道**。
     */
    fun exposable(metric: String): Boolean {
        val c = chans[wrapped[metric]?.trigger ?: metric] ?: return false
        return !c.oemPrivate && c.everArmed
    }

    /** 采集到但**不许对外提供**的渠道（诊断用，让"排除了什么"看得见）。 */
    fun collectedPrivate(): List<String> = chans.values.filter { it.oemPrivate }.map { it.metric }

    /** 一条渠道当前的状态说明（注册/武装结果、跳过了哪个实例、是不是 OEM 私有）。 */
    fun stateOf(metric: String): String {
        val c = chans[wrapped[metric]?.trigger ?: metric] ?: return "这个渠道没在这台机器上"
        return notes[c.metric] ?: ""
    }

    // ---------------------------------------------------------------- ★ 包装层
    /**
     * ★★ **特殊传感器要包一层方法**（用户指出）：触发式传感器只报"发生了一次"，
     * **它本身不是一个标量**，直接交给模型只会得到"次数"这种没意义的东西。
     *
     * ⇒ 包装成 **"距上次触发过了多久"** —— 有意义、会自己变化，
     *   正是"**30 分钟没动**才值得说"那句话需要的量。
     *
     * ★★ ③ 的落点：`trigger` 这个字符串**既是记的名字、也是查的名字**（同一个），
     *   以前一边记 `stationary` 一边查 `significant_motion` —— 两边永远不会见面。
     */
    private class Wrap(val metric: String, val trigger: String, val label: String)

    private val wrapped: Map<String, Wrap> = listOf(
        Wrap("since_motion_minutes", "significant_motion", "距上次显著运动"),
        Wrap("still_minutes", "stationary", "已静止"),
    ).associateBy { it.metric }

    /** 被包装的 metric 名单（`describe()` 要用它去暴露）。 */
    fun wrappedMetrics(): List<Triple<String, String, String>> =
        wrapped.values.map { Triple(it.metric, it.trigger, it.label) }

    private fun fetchWrapped(wrap: Wrap, o: JSONObject): JSONObject {
        val c = chans[wrap.trigger]
        if (c == null) {
            o.put("ok", false)
            o.put("note", "这台机器上没有 `${wrap.trigger}` 这个触发式传感器 ⇒ 这条读数不可用")
            return o
        }
        o.put("sources", JSONArray().put(c.name))
        // ★★ ③：「没武装上」与「武装了但还没触发过」**必须分开说** ——
        //   否则前者会被读成"他确实很久没动"（那是把"没有依据"说成了"结论"）。
        if (!c.everArmed) {
            o.put("ok", false)
            o.put("note", "★ 触发式传感器 `${wrap.trigger}` **没能武装上**（${notes[c.metric]}）" +
                "⇒ 这条读数**不可用**；⚠️ 这不是「他很久没动」，是**我们这边没采到**")
            return o
        }
        val last = store.lastTrigger(wrap.trigger)
        if (last == null) {
            o.put("ok", false)
            o.put("note", "已武装，但还没有记录到「${wrap.trigger}」触发过 ⇒ **不知道**他多久没动了" +
                "（⚠️ 这不是 0，是还没有依据）")
            return o
        }
        val mins = (System.currentTimeMillis() - last) / 60_000.0
        o.put("ok", true)
        o.put("value", mins)
        o.put("unit", "分钟")
        o.put("scalar", true)          // ★ 标量读数（没有时间序列）
        o.put("since_ms", last)
        o.put("note", "${wrap.label}：由触发式传感器 `${wrap.trigger}` 包装而来" +
            "（上次触发 ${store.describeTime(last)}）" +
            (if (c.armedNow) "·当前已重新武装" else "·⚠️ 当前没武装上，这个数不会再更新") +
            "；★ 语义清楚、会自己变化，这才是有用的读数")
        return o
    }

    // ---------------------------------------------------------------- 出读数
    /**
     * 取读数。**交的是桶，不是趋势**（趋势/分位/覆盖率由宿主算，见 `SensorStore` 的说明）。
     *
     * 返回：`{ok, metric, levelMs, buckets:[[t,mean,lo,hi,n]...], value, sources, at, note, rawLast}`
     * ★ `rawLast` 只为"语义验证"留着 —— 那项做完就该删掉。
     */
    fun fetch(metric: String, fromMs: Long, toMs: Long): JSONObject {
        val o = JSONObject()
        o.put("metric", metric)
        o.put("at", System.currentTimeMillis())

        // ★★ 包装层优先：被包装的 metric 不查桶，直接算"距上次触发多久"
        wrapped[metric]?.let { w ->
            return fetchWrapped(w, o)
        }

        val c = chans[metric]
        if (c == null) {
            o.put("ok", false)
            o.put("note", "这台机器上没有这个渠道（$metric）")
            return o
        }
        o.put("sources", JSONArray().put(c.name))

        // ★★ ⑤：OEM 私有号段**不提供读数**。数据仍在采集（将来若有官方文档可对照），
        //   但"交给模型一个我们无权解释的数字"比"不交"危险得多。
        if (c.oemPrivate) {
            o.put("ok", false)
            o.put("note", "⛔ OEM 私有号段（type=${c.sensor?.type}，${c.name}）：**官方文档里没有这个类型**" +
                " ⇒ `values` 没有可依据的语义（实测恒为一个常数，信息量为 0）" +
                " ⇒ **不对外提供读数**（仍在采集，只用于诊断）")
            return o
        }

        val (level, rows) = try {
            store.fetch(metric, fromMs, toMs)
        } catch (t: Throwable) {
            o.put("ok", false)
            o.put("note", "读存储失败：${t.javaClass.simpleName}: ${t.message}")
            return o
        }
        o.put("levelMs", level)
        val arr = JSONArray()
        for (r in rows) {
            arr.put(JSONArray().also { a -> r.forEach { x -> a.put(x) } })
        }
        o.put("buckets", arr)

        if (rows.isEmpty()) {
            o.put("ok", false)
            o.put("note", notes[metric].orEmpty() + "；这段时间没有桶" +
                "（一次性/变化时类传感器本来就是「有事才报」）")
            return o
        }
        o.put("ok", true)
        o.put("value", rows.last()[1])       // 最后一个桶的均值（★ 行已按时间排好，见 SensorStore.fetch）
        lastRaw[metric]?.let { raw ->
            o.put("rawLast", JSONArray().also { a -> raw.forEach { a.put(it.toDouble()) } })
        }
        o.put("note", notes[metric].orEmpty() +
            "；★ 交的是桶（levelMs=$level，**已按时间排序**），趋势/分位由宿主算")
        return o
    }
}
