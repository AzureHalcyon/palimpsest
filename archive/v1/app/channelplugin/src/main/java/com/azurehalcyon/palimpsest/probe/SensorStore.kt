package com.azurehalcyon.palimpsest.probe

import java.io.File
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/**
 * 插件的**存储层**：把采样压成分桶，落盘，按三档保留，`fetch()` 只交桶。
 *
 * ★★ 为什么插件只做「采 · 分桶 · 落盘 · 交桶」，不算趋势
 * ----------------------------------------------------
 * 趋势 / 分位 / 覆盖率是**宿主**算的（`companion/aggregate.py`，18 项自检覆盖）。
 * 理由：设计 sophistication 在数学那侧，而**自检在 Python 侧 ⇒ 能离线验证**；
 * 且换插件不用重写数学。⇒ 契约：`fetch()` 回**桶** + `levelMs`，趋势由宿主算。
 *
 * 三档（设计稿 §2.9）
 * ------------------
 * ```
 * 1min（7 天） ──滚动归并──▶ 1h（90 天） ──▶ 1day（2 年）
 * ```
 * 每一档一个目录，文件名带当地日期；**原始采样不落盘**（200Hz 存下来毫无意义）。
 *
 * 落盘格式：**CSV**（`t,mean,lo,hi,n`），不是 JSONL。
 * ★ 理由：桶是纯数值、量大（10 个 metric × 7 天 ≈ 10 万行）⇒ CSV 省一半体积、不用逐行解 JSON。
 *
 * ⚠️ 时间用**墙钟毫秒**，并且**分桶按"当地日/时/分"边界**切 ——
 *   否则"他今天走了多少"会随 UTC 偏移算错一天。
 */
class SensorStore(private val root: File) {

    // 每档一个子目录
    private val dirMin = File(root, "m")
    private val dirHour = File(root, "h")
    private val dirDay = File(root, "d")

    /** 正在攒的那一分钟：metric -> (桶起点, 和, 最小, 最大, 条数) */
    private class Acc(var t: Long, var sum: Double, var lo: Float, var hi: Float, var n: Int)

    private val minute = HashMap<String, Acc>()
    private val hour = HashMap<String, Acc>()
    private val day = HashMap<String, Acc>()

    private val fmt = SimpleDateFormat("yyyyMMdd", Locale.US)
    private val fmtFull = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)

    /** 已经关掉的档数（诊断用：看它到底滚动过没有）。 */
    var closedMinutes = 0
        private set
    var closedHours = 0
        private set
    var closedDays = 0
        private set

    companion object {
        const val MIN_MS = 60_000L
        const val HOUR_MS = 3_600_000L
        const val DAY_MS = 86_400_000L
        // ★ 三档保留期（设计稿 §2.9）
        const val KEEP_MIN_MS = 7 * DAY_MS
        const val KEEP_HOUR_MS = 90 * DAY_MS
        const val KEEP_DAY_MS = 2 * 365 * DAY_MS

        /** 真实墙钟毫秒的下限（2001-09-09）。低于它的时间戳必然是 elapsedRealtime 误当墙钟。 */
        const val WALL_FLOOR_MS = 1_000_000_000_000L

        /** 窗口多长就用哪一档（短的用细的，长的用粗的）。 */
        fun levelFor(spanMs: Long): Long = when {
            spanMs <= 3 * HOUR_MS -> MIN_MS
            spanMs <= 8 * DAY_MS -> HOUR_MS
            else -> DAY_MS
        }
    }

    // ------------------------------------------------------------------ 收
    /**
     * 记一个采样点。`tMs` 是墙钟毫秒，`v` 是**已经压成标量**的值
     * （谁压的、怎么压的由采集方决定，见 `SensorRecorder.scalar`）。
     */
    fun record(metric: String, tMs: Long, v: Float) {
        val mStart = floorTo(tMs, MIN_MS)
        val cur = minute[metric]
        if (cur != null && cur.t != mStart) closeMinute(metric, cur)
        val a = minute.getOrPut(metric) { Acc(mStart, 0.0, v, v, 0) }
        a.sum += v.toDouble()
        if (v < a.lo) a.lo = v
        if (v > a.hi) a.hi = v
        a.n += 1
    }

    private fun closeMinute(metric: String, a: Acc) {
        val row = doubleArrayOf(a.t.toDouble(), a.sum / a.n, a.lo.toDouble(), a.hi.toDouble(), a.n.toDouble())
        append(dirMin, metric, a.t, row)
        closedMinutes++
        minute.remove(metric)
        feed(hour, metric, a.t, row, HOUR_MS) { m, t, r -> closeHour(m, t, r) }
    }

    private fun closeHour(metric: String, t: Long, r: DoubleArray) {
        append(dirHour, metric, t, r)
        closedHours++
        hour.remove(metric)
        feed(day, metric, t, r, DAY_MS) { m, t2, r2 -> closeDay(m, t2, r2) }
    }

    private fun closeDay(metric: String, t: Long, r: DoubleArray) {
        append(dirDay, metric, t, r)
        closedDays++
        day.remove(metric)
    }

    /** 把一条细桶喂进粗档；跨过粗档边界就把旧的关掉。**按 n 加权**。 */
    private inline fun feed(
        accs: HashMap<String, Acc>, metric: String, t: Long, row: DoubleArray, levelMs: Long,
        closeFn: (String, Long, DoubleArray) -> Unit,
    ) {
        val bStart = floorTo(t, levelMs)
        val cur = accs[metric]
        if (cur != null && cur.t != bStart) {
            val finished = doubleArrayOf(cur.t.toDouble(), cur.sum / cur.n, cur.lo.toDouble(),
                cur.hi.toDouble(), cur.n.toDouble())
            closeFn(metric, cur.t, finished)
        }
        val a = accs.getOrPut(metric) { Acc(bStart, 0.0, row[2].toFloat(), row[3].toFloat(), 0) }
        // ★ 加权：用 n 把均值累起来（不是简单平均）
        a.sum += row[1] * row[4]
        if (row[2].toFloat() < a.lo) a.lo = row[2].toFloat()
        if (row[3].toFloat() > a.hi) a.hi = row[3].toFloat()
        a.n += row[4].toInt()
    }

    /** 把还没关的桶强制落盘（宿主解绑 / 进程要走了 ⇒ 不落就丢）。 */
    fun flush() {
        for ((metric, a) in minute.toList()) closeMinute(metric, a)
        for ((metric, a) in hour.toList()) closeHour(metric, a.t, doubleArrayOf(
            a.t.toDouble(), a.sum / a.n, a.lo.toDouble(), a.hi.toDouble(), a.n.toDouble()))
        for ((metric, a) in day.toList()) closeDay(metric, a.t, doubleArrayOf(
            a.t.toDouble(), a.sum / a.n, a.lo.toDouble(), a.hi.toDouble(), a.n.toDouble()))
    }

    // ------------------------------------------------------------------ 读
    /**
     * 取窗口内的桶。返回 `(levelMs, rows)`，`rows` 每行 `[t, mean, lo, hi, n]`。
     * ★ **窗口决定档位**：≤3h 用分钟档、≤8d 用小时档、更长用日档。
     */
    fun fetch(metric: String, fromMs: Long, toMs: Long): Pair<Long, MutableList<DoubleArray>> {
        val level = levelFor(toMs - fromMs)
        val dir = when (level) {
            MIN_MS -> dirMin
            HOUR_MS -> dirHour
            else -> dirDay
        }
        val rows = mutableListOf<DoubleArray>()
        val files = filesFor(dir, metric, fromMs, toMs)
        for (f in files) {
            f.forEachLine { line ->
                val r = parseRow(line) ?: return@forEachLine
                if (r[0] >= fromMs - level && r[0] <= toMs) rows.add(r)
            }
        }
        // 内存里还没落盘的那一分钟也算上（否则"刚刚"永远是空的）
        minute[metric]?.let { a ->
            if (level == MIN_MS && a.t in (fromMs - MIN_MS)..toMs) {
                rows.add(doubleArrayOf(a.t.toDouble(), a.sum / a.n, a.lo.toDouble(),
                    a.hi.toDouble(), a.n.toDouble()))
            }
        }
        // ★★ **读时必须排序** —— 文件是**追加写**的，而"最后攒着的那个桶"是在 flush 时才落的，
        //   于是它可能**排在已经落盘的更晚的桶后面**。真机证据（2026-10-05 `posture_raw`）：
        //     `…360000` → `…790000` → **又回到 `…360000`**
        //   ⛔ 乱序行会破坏上层的分桶/趋势（"最近一个桶"会取错）。
        //   ⇒ 这里排序，而**不是**去要求写入侧保证单调：写入侧漏一次就永久乱，
        //     读侧排一次则永远对（同样的理由也适用于三档之间的滚动归并）。
        rows.sortBy { it[0] }
        // ★★ **同一个桶可能被写了两遍**（2026-10-05 真机 `accel` 抓到的：
        //   同一分钟一行 `n=17`、紧接着一行 `n=1`）。成因是**同一个分钟里"先落一次、再重新开采"**
        //   （采集器/进程停启一次就会这样：flush 把攒着的落了，重新采样的又攒在同一个分钟）。
        //   ⛔ 不合并的话，上层算分位/覆盖率会把**同一分钟算两次**（多出来的不是数据，是重复）。
        //   ⇒ 按 **n 加权合并**（与小时档同一套算法）；⛔ 不许"丢掉后一行"—— 那是在丢采样。
        return level to mergeSameT(rows)
    }

    /** 把时间戳相同的行按 n 加权合成一行（`t` 相同的行必然相邻 —— 上面刚排过序）。 */
    private fun mergeSameT(rows: MutableList<DoubleArray>): MutableList<DoubleArray> {
        if (rows.size < 2) return rows
        val out = mutableListOf<DoubleArray>()
        for (r in rows) {
            val last = out.lastOrNull()
            if (last != null && last[0] == r[0]) {
                val n = last[4] + r[4]
                out[out.size - 1] = doubleArrayOf(
                    last[0],
                    (last[1] * last[4] + r[1] * r[4]) / n,
                    minOf(last[2], r[2]),
                    maxOf(last[3], r[3]),
                    n,
                )
            } else {
                out.add(r)
            }
        }
        return out
    }

    /** 有哪些 metric 落过盘（宿主问"这个渠道有没有历史"）。
     *
     * ⚠️ 文件名是 `<metric>_<yyyymmdd>.csv`，而 **metric 自己可能带下划线**
     *   （`activity_raw` / `screen_down_raw`…）⇒ **必须按最后一个下划线切**。
     *   第一版按"第一个到最后一个下划线"取子串，单个下划线时 `i == j` ⇒ **永远取不到**（自检抓到）。
     */
    fun metrics(): List<String> {
        val out = sortedSetOf<String>()
        for (d in listOf(dirMin, dirHour, dirDay)) {
            d.listFiles()?.forEach { f ->
                val n = f.name
                if (!n.endsWith(".csv")) return@forEach
                val j = n.lastIndexOf('_')
                if (j > 0) out.add(n.substring(0, j))
            }
        }
        return out.toList()
    }

    /** 按三档保留期删老文件。返回删了几个。 */
    fun prune(nowMs: Long): Int {
        var n = 0
        n += pruneDir(dirMin, nowMs - KEEP_MIN_MS)
        n += pruneDir(dirHour, nowMs - KEEP_HOUR_MS)
        n += pruneDir(dirDay, nowMs - KEEP_DAY_MS)
        return n
    }

    private fun pruneDir(dir: File, cutMs: Long): Int {
        var n = 0
        dir.listFiles()?.forEach { f ->
            val d = dateOf(f.name) ?: return@forEach
            if (d < cutMs - DAY_MS) { if (f.delete()) n++ }   // 整天边界，留一天余量
        }
        return n
    }

    // ------------------------------------------------------------------ 工具
    private fun floorTo(t: Long, level: Long): Long = t - (t % level)

    private fun append(dir: File, metric: String, t: Long, row: DoubleArray) {
        dir.mkdirs()
        val f = File(dir, fileName(metric, t))
        f.appendText(row.joinToString(",") { fmtNum(it) } + "\n")
    }

    private fun fileName(metric: String, t: Long) = "${metric}_${fmt.format(Date(t))}.csv"

    private fun parseRow(line: String): DoubleArray? {
        val s = line.trim()
        if (s.isEmpty()) return null
        val p = s.split(",")
        if (p.size < 5) return null
        return try {
            doubleArrayOf(p[0].toDouble(), p[1].toDouble(), p[2].toDouble(), p[3].toDouble(), p[4].toDouble())
        } catch (e: NumberFormatException) {
            null
        }
    }

    /** 数值写法要**可逆且稳定**：整数不带小数点，其它用最少位数。 */
    private fun fmtNum(d: Double): String =
        if (d == Math.floor(d) && !d.isInfinite()) d.toLong().toString() else d.toString()

    /** 从文件名里取那一天的起点毫秒（解析不了就 null）。 */
    private fun dateOf(fileName: String): Long? {
        val j = fileName.lastIndexOf('_')
        val k = fileName.lastIndexOf('.')
        if (j < 0 || k <= j) return null
        return try {
            val d = fmt.parse(fileName.substring(j + 1, k)) ?: return null
            val c = Calendar.getInstance()
            c.time = d
            c.set(Calendar.HOUR_OF_DAY, 0); c.set(Calendar.MINUTE, 0)
            c.set(Calendar.SECOND, 0); c.set(Calendar.MILLISECOND, 0)
            c.timeInMillis
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 窗口落在哪些日期文件上（避免把全部历史读一遍）。
     *
     * ★★ **当地日期 vs UTC 整日**（2026-10-05 由自检里那条"乱序档"检查抓出来的真 bug）：
     *   文件名里的日期是**当地日期**（`SimpleDateFormat` 用默认时区），
     *   而这里的推进量 `floorTo(_, DAY_MS)` 是**UTC 整日**（epoch 0 是 UTC 零点）。
     *   在 UTC+8 上，UTC 零点 = **当地 08:00** ⇒ 于是"候选日"与"文件名里的日"会错开一天：
     *     · 当 `fromMs`/`toMs` 落在**当地 00:00–08:00** 时，
     *       取到的候选日是**前一天**，而当天的档**还没轮到就被 `d <= toMs` 截断** ⇒
     *       **当天 00:00 之后写进去的桶一条都读不到**（不是空，是漏）。
     *   ⇒ 两头各放宽一天：多查两个**不存在就跳过**的候选（`f.exists()` 会挡掉），
     *     代价是两次 `File.exists()`，换来"凌晨读得到当天的档"。
     */
    private fun filesFor(dir: File, metric: String, fromMs: Long, toMs: Long): List<File> {
        val out = mutableListOf<File>()
        var d = floorTo(fromMs, DAY_MS) - DAY_MS
        while (d <= toMs + DAY_MS) {
            val f = File(dir, fileName(metric, d))
            if (f.exists()) out.add(f)
            d += DAY_MS
        }
        return out
    }

    fun describeTime(t: Long): String = fmtFull.format(Date(t))

    // ------------------------------------------------------------------ ★ 触发式传感器的包装
    /**
     * ★★ **特殊传感器要包一层方法**（用户指出）：
     * 触发式传感器（`significant_motion` / `stationary`）**只报"发生了一次"，它本身不是一个标量** ——
     * 直接把它当读数交给模型，模型看到的只是"次数"这种没有意义的东西。
     *
     * ⇒ 包装成 **"距上次触发过了多久"** —— 那才是**有意义、而且自己会变化**的读数，
     *   也正是设计稿里"**30 分钟没动**才值得说"那句话真正需要的量。
     *
     * 记法：一行一条 `metric,ms`，追加写（触发不频繁，不值得为它建分桶）。
     * ⚠️ 从来没触发过 ⇒ `lastTrigger` 回 `null` ⇒ 上层如实说"还不知道"（**不许当成 0**）。
     *
     * ★★ **同一时刻不许记两遍**（2026-10-05 真机抓到）：`triggers.csv` 里
     *   `stationary,1791210879391` 这样的行**成对出现** ⇒ 命中次数被虚增一倍。
     *   两处根因都已修（一渠道只注册一个实例 · 一个进程只有一个采集器），
     *   这里再加一道**写入侧的去重**：同一个 (metric, tMs) 只落一行。
     *   （时间戳是毫秒级，真出现"同一毫秒两次触发"的概率可以忽略，
     *     而"同一事件被两路回调各报一次"才是实际发生过的那个。）
     */
    private val triggerFile get() = File(root, "triggers.csv")

    fun markTrigger(metric: String, tMs: Long) {
        try {
            root.mkdirs()
            if (lastTriggerLine() == "$metric,$tMs") return      // ★ 去重
            triggerFile.appendText("$metric,$tMs\n")
        } catch (_: Throwable) {
        }
    }

    /** 文件里最后一条有效记录（用于去重）；没有就 null。触发不频繁，整读代价可忽略。 */
    private fun lastTriggerLine(): String? = try {
        if (!triggerFile.exists()) null
        else triggerFile.readLines().lastOrNull { it.isNotBlank() }?.trim()
    } catch (_: Throwable) {
        null
    }

    /**
     * 按保留期清掉老的触发行，并顺手清掉**墙钟修复之前**写进去的行。
     *
     * ★ 判据：`SensorEvent.timestamp` 是 `elapsedRealtimeNanos()`（开机以来的纳秒）——
     *   早期版本直接拿它当墙钟，于是文件里留下了 `stationary,177067904` 这种
     *   "1970 年"的行。**任何真实的墙钟毫秒都 ≥ 1e12**（2001-09-09），
     *   所以 `t < 1e12` 的行必然是那个 bug 的产物 ⇒ 清掉比留着更容易判读。
     */
    fun pruneTriggers(nowMs: Long): Int {
        if (!triggerFile.exists()) return 0
        return try {
            val keep = mutableListOf<String>()
            var dropped = 0
            triggerFile.forEachLine { line ->
                val p = line.trim().split(",")
                val t = p.getOrNull(1)?.toLongOrNull()
                if (p.size >= 2 && t != null && t >= WALL_FLOOR_MS && t >= nowMs - KEEP_HOUR_MS) {
                    if (keep.lastOrNull() != line.trim()) keep.add(line.trim())   // ★ 顺手把历史重复行也去掉
                } else {
                    dropped++
                }
            }
            if (dropped > 0) triggerFile.writeText(keep.joinToString("\n", postfix = if (keep.isEmpty()) "" else "\n"))
            dropped
        } catch (_: Throwable) {
            0
        }
    }

    /** 每个 metric 最近一次触发的时刻；**没触发过的不在表里**。 */
    fun lastTriggers(): MutableMap<String, Long> {
        val out = mutableMapOf<String, Long>()
        val f = triggerFile
        if (!f.exists()) return out
        try {
            f.forEachLine { line ->
                val p = line.trim().split(",")
                if (p.size >= 2) {
                    try {
                        val t = p[1].toLong()
                        val cur = out[p[0]]
                        if (cur == null || t > cur) out[p[0]] = t
                    } catch (_: NumberFormatException) {
                    }
                }
            }
        } catch (_: Throwable) {
        }
        return out
    }

    fun lastTrigger(metric: String): Long? = lastTriggers()[metric]
}
