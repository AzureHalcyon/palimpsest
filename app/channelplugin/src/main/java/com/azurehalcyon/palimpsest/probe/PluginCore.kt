package com.azurehalcyon.palimpsest.probe

import android.content.Context
import com.azurehalcyon.palimpsest.plugin.Contract
import com.azurehalcyon.palimpsest.probe.alarm.AlarmCore
import com.azurehalcyon.palimpsest.probe.calendar.CalendarCore
import com.azurehalcyon.palimpsest.probe.usage.UsageCore
import org.json.JSONArray
import org.json.JSONObject

/**
 * 插件逻辑本体 —— **与传输方式无关**。
 *
 * ★ 为什么要抽出来：实测发现「跨 App 绑 Service」在这台设备上被系统挡住
 *   （`bindService` 返回 false、不抛异常、`resolveService` 却一切正常），
 *   于是传输改成 **ContentProvider**（系统按需拉起，不要求插件先生成进程）。
 *   ⇒ 但 `hello/describe/read` 这三件事**不该跟着传输改**，所以放在这里，
 *     由 `SensorPluginService`（AIDL）和 `SensorPluginProvider`（provider）两个薄壳共用。
 *
 * ★★ 2026-10-05 第二轮：**一个进程只能有一个 `PluginCore`**（见 [get]）。
 *   provider / service / 诊断 Activity 各建一个的话，就会有多个 `SensorRecorder`
 *   同时注册同一批传感器 ⇒ 同一个物理事件被记两遍（真机 `triggers.csv` 里同时间戳成对出现）。
 */
class PluginCore private constructor(private val ctx: Context) {

    // ★ 采集器也是进程内单例（同一个理由，见 `SensorRecorder.get`）
    private val rec = SensorRecorder.get(ctx)

    /** ★ 渠道插件里的**第二个数据域**：日历（观测 vs 声明，两条互不依赖的信源）。 */
    private val cal = CalendarCore(ctx)

    /** ★ 第三个数据域：闹钟（同属"他写的计划"，但问的是**作息**：下一个闹钟还有多久）。 */
    private val alarm = AlarmCore(ctx)

    /** ★ 第四个数据域：屏幕使用（**观测**类 —— "他在怎么用这台手机"，与传感器互补）。 */
    private val usage = UsageCore(ctx)

    fun start() = rec.start()
    fun stop() = rec.stop()

    fun hello(): String = JSONObject()
        .put(Contract.K_PROTOCOL, Contract.PROTOCOL)
        .put(Contract.K_ID, "device")
        .put(Contract.K_NAME, "手机渠道")
        .put(Contract.K_VERSION, "0.4.0")
        .toString()

    // ---------------------------------------------------------------- describe
    /**
     * ★★ **只暴露真的能出数的渠道**（"暴露 ⇔ 可采"这条对齐规则，修的是 ③⑤）：
     *   · 传感器不在这台机器上 / 注册失败 ⇒ **不暴露**（否则工具表里挂着一个永远空的工具）；
     *   · 包装层（触发式）⇒ 底层触发**必须武装成功过**（`since_motion_minutes` 的病正是这条）；
     *   · ⛔ OEM 私有号段（`har_wakeup` / `activity_recognition` / `DrivingDetection` …）
     *     `values` 语义**无从解释**（官方文档没有这些类型）⇒ **不暴露**。
     *     判据与真机数据见 `companion-data/待办清单.md` 第 21 轮。
     *
     * 判据在插件侧（`SensorRecorder.exposable`），宿主只是**照实转交** ⇒
     * 换成别的手机（没有 HAR、没有显著运动）时，暴露的那几条会**自动变少**，不用改宿主。
     */
    fun describe(): String {
        rec.start()                       // 有人问就开采（见 SensorPluginProvider 的注释）
        val metrics = JSONArray()

        // ★ 每条的 `toolDesc`/`useWhen` 都**照实写**：
        //   语义没验过的一律不进这里（OEM 那几条现在被 `exposable()` 挡在外面），
        //   绝不写成"他在走路"—— 那既是骗模型，也是骗用户。
        fun m(
            id: String, name: String, unit: String, desc: String,
            tool: String, toolDesc: String, useWhen: String,
            nature: String = "",
            grainMs: Long = 60_000L, maxWindowMs: Long = 2L * 60 * 60 * 1000,
        ) = JSONObject()
            .put(Contract.K_M_ID, id)
            .put(Contract.K_M_NAME, name)
            .put(Contract.K_M_UNIT, unit)
            .put(Contract.K_M_DESC, desc)
            .put(Contract.K_M_TOOL_NAME, tool)
            .put(Contract.K_M_TOOL_DESC, toolDesc)
            .put(Contract.K_M_USE_WHEN, useWhen)
            // ★ `nature`：**这个数为什么会变**（事实，不是判断）—— 判断交给 AI，见 Contract 的注释
            .put(Contract.K_M_NATURE, nature)
            .put(Contract.K_M_GRAIN_MS, grainMs)
            .put(Contract.K_M_MAX_WINDOW_MS, maxWindowMs)
            .put(Contract.K_M_SOURCES, JSONArray().put("手机传感器"))

        /** ★ 唯一的入口：**不可采的渠道根本不会进工具表**。 */
        fun put(
            id: String, name: String, unit: String, desc: String,
            tool: String, toolDesc: String, useWhen: String,
            nature: String = "",
        ) {
            if (!rec.exposable(id)) return
            metrics.put(m(id, name, unit, desc, tool, toolDesc, useWhen, nature))
        }

        put("since_motion_minutes", "距上次显著运动", "分钟",
            "★ 由触发式传感器**包装**出来的读数（不是原始触发次数）",
            "get_since_motion", "他上次明显动过是多久以前",
            "想知道他是不是长时间没动（★ 这才是「30 分钟没动」那句话要用的量）",
            nature = "触发派生（显著运动触发后才更新）")
        put("accel", "加速度模长", "m/s²", "手机加速度的模长（静止时≈9.8）",
            "get_accel", "他手机最近的加速度模长与趋势",
            "想知道他此刻是不是在动（静止≈9.8，走动会明显起伏）",
            nature = "均值慢变（分桶均值，缓慢漂移）")
        put("light", "环境光", "lux", "环境光照",
            "get_light", "他手机所在环境的光照与趋势",
            "想知道他那边亮着还是黑着（昼夜 / 在不在室内）",
            nature = "瞬时噪声（环境光照，抖动大）")
        put("steps", "计步", "步", "计步传感器",
            "get_steps", "他手机记的步数",
            "想知道他今天动了多少（⚠️ 需要 ACTIVITY_RECOGNITION 授权；没授权时这条**不会出现在工具表里**）",
            nature = "单调累加（按天累计，只增不减）")

        // ⛔ 以下**刻意不进工具表**，但**仍在采集**（留作诊断 / 将来对照）：
        //   activity_raw · activity_type_raw · driving_raw · posture_raw · pickup_raw · screen_down_raw
        //   —— 全是**小米/QTI 私有号段**（type ≥ 0x10000），官方没有文档 ⇒ values 语义无从解释
        //      （实测 `activity_raw` 恒为 5,5,5）⇒ `exposable()` 判 false，`fetch()` 也直接拒答。
        //   ★ `orientation`（标准 type 27）也没进表：与 light/accel 重复，价值低。
        //   ★ `still_minutes`（由 `stationary` 包装）留着当**交叉验证的第二个独立测法** ——
        //     它和 significant_motion 是同一件事的两种测法，将来正好互相对照（暂不给模型）。

        // ── ★ 第二个数据域：日历（与传感器**互不依赖**的第二条信源）────────────────
        //   暴露规则同传感器：**没授权就不进工具表**（否则又是一个"永远空"的渠道，见 ③ 的教训）。
        //   `sources` 各自写各自的（"手机传感器" / "日历" / "闹钟" / "屏幕使用"）
        //   ⇒ 宿主那套「≥2 独立信源才开口」才认得出来。
        if (cal.granted()) {
            val calMetrics = cal.describeMetrics()
            for (i in 0 until calMetrics.length()) metrics.put(calMetrics.getJSONObject(i))
        }
        // ── ⛔ 第三个数据域：闹钟 —— **2026-10-06 用户决定先不开放**（理由写在 `AlarmCore.EXPOSE`）：
        //   语义弱（会被动态提醒瞬变顶掉）· 来源读不到 · 只能读不能写。
        //   ⚠️ 代码**留着**（插件自己的诊断页仍在读它），只是**不进工具表**；
        //      要重开就把 `AlarmCore.EXPOSE` 改成 true —— 前提是把"可设可读"一起做上。
        if (AlarmCore.EXPOSE) {
            val alarmMetrics = alarm.describeMetrics()
            for (i in 0 until alarmMetrics.length()) metrics.put(alarmMetrics.getJSONObject(i))
        }
        // ── ★ 第四个数据域：屏幕使用（appop 要手动开；**没开就不进工具表**，同一条规矩）
        if (usage.granted()) {
            val usageMetrics = usage.describeMetrics()
            for (i in 0 until usageMetrics.length()) metrics.put(usageMetrics.getJSONObject(i))
        }

        return JSONObject().put(Contract.K_METRICS, metrics).toString()
    }

    // ---------------------------------------------------------------- fetch
    fun fetch(metric: String, fromMs: Long, toMs: Long): String =
        try {
            // ★ 四个数据域在这里分派：日历 / 闹钟 / 屏幕使用各自走自己的 core，其余走传感器采集器。
            //   ⛔ 闹钟**当前未开放**：即使被问到也**明确回"未开放"**，而不是悄悄给个数 ——
            //     否则宿主那边可能还留着旧缓存，读到一个我们已决定不该再给的读数。
            when {
                metric in AlarmCore.METRICS && !AlarmCore.EXPOSE ->
                    JSONObject()
                        .put(Contract.K_METRIC, metric)
                        .put(Contract.K_OK, false)
                        .put("sources", JSONArray().put("闹钟"))
                        .put(Contract.K_NOTE, "闹钟这一路**当前未开放**（2026-10-06 用户决定）：" +
                            "读数语义太弱（会被计时器/就寝提醒瞬变顶掉）、来源读不到、且只能读不能写 ⇒ " +
                            "不占工具表；等做「可设可读」时再开").toString()
                metric in CalendarCore.METRICS -> cal.fetch(metric, fromMs, toMs).toString()
                metric in AlarmCore.METRICS -> alarm.fetch(metric, fromMs, toMs).toString()
                metric in UsageCore.METRICS -> usage.fetch(metric, fromMs, toMs).toString()
                else -> rec.fetch(metric, fromMs, toMs).toString()
            }
        } catch (t: Throwable) {
            // ★ 插件内部出错也要**正常回一个 ok:false**，不能让它变成跨进程异常
            JSONObject()
                .put(Contract.K_OK, false)
                .put(Contract.K_METRIC, metric)
                .put(Contract.K_NOTE, "插件内部出错：${t.javaClass.simpleName}: ${t.message}")
                .toString()
        }

    /** 诊断：哪些渠道采集了但不暴露、各是什么状态（探针自检用）。 */
    fun diagnostics(): String {
        rec.start()
        val chans = JSONArray()
        for ((metric, _) in rec.metrics()) {
            chans.put(JSONObject()
                .put("metric", metric)
                .put("exposable", rec.exposable(metric))
                .put("state", rec.stateOf(metric)))
        }
        return JSONObject()
            .put("channels", chans)
            .put("collected_private", JSONArray(rec.collectedPrivate()))
            .put("wrapped", JSONArray(rec.wrappedMetrics().map { it.first }))
            .toString()
    }

    companion object {
        /**
         * ★★ **进程内单例**。理由见类注释：多一份 `PluginCore` 就多一份传感器注册。
         * ⚠️ 用 `applicationContext`：单例活得比任何 Activity / Service 都久。
         */
        @Volatile
        private var shared: PluginCore? = null

        fun get(ctx: Context): PluginCore =
            shared ?: synchronized(this) {
                shared ?: PluginCore(ctx.applicationContext).also { shared = it }
            }
    }
}
