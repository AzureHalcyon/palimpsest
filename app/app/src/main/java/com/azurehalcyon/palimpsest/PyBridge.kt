package com.azurehalcyon.palimpsest

import android.content.Context
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.asCoroutineDispatcher
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference

/**
 * Kotlin ↔ Python 的**唯一**通道。
 *
 * 规矩（别让桥长胖）：
 *  - **只传字符串 / 布尔 / 字符串列表**，不传业务对象、不传字典。
 *    ★ 这条是踩过坑的：`PyObject.asMap()` 给的是 `Map<PyObject, PyObject>`，
 *      Kotlin 侧按键取值会 "Type inference failed"，所以在 Python 那边就拼成字符串。
 *    ★ 嵌套结构一律走 **JSON 字符串**（`*_json` 那几个函数），由界面侧解析。
 *      好处是**以后加字段不用改桥**。
 *  - 业务逻辑一律留在 Python 侧，Kotlin 只管界面与系统权限；
 *  - 任何异常都在这里被抓成可读文本，绝不让界面崩。
 *
 * ------------------------------------------------------------------ 线程规矩
 * ★ 所有 Python 调用**必须**走 [dispatcher]（一条独占线程）。
 *   两个理由：
 *   1) **Chaquopy 的线程亲和性**：Python 在 `Python.start()` 的那条线程上初始化；
 *      从**没被 Python 认领过**的线程（Dispatchers.IO 的池线程就是）直接 `getModule`
 *      会挂。让所有调用串在同一条线程上，那条线程在第一次调用时自动完成认领。
 *   2) **状态一致**：`host_glue` 的 `_SESS`（在场次句柄）是进程内的 Python 全局，
 *      `Engine` 又是读改写同一份缓存文件 —— 串行化省掉一整类竞态。
 *   ⚠️ `setPaths()` 必须**最先**跑（它设 `COMPANION_CRED_PATH`），
 *      ViewModel 在 init 里就把它排进这条线程的最前面。
 */
object PyBridge {

    private val py: Python get() = Python.getInstance()

    /**
     * ★★ **保证 Python 起来了**（幂等）。
     *
     * 为什么必须有这个函数（2026-10-05 真机 `l0-report.txt` 抓到的**真 bug**）：
     *   原来 `Python.start(AndroidPlatform(...))` **只写在 `MainActivity.onCreate` 里** ⇒
     *   从**后台**进来的那条路（WorkManager 周期任务 / 系统广播接收器）**没有 Activity**，
     *   Python 从没启动过，于是 L0 一跑就抛：
     *     `RuntimeException: Cannot use GenericPlatform on Android. Call Python.start(...)`
     *   ⇒ 报告里就是 `[work] 失败：桥接 channel_tick 失败：…`。
     *   ⛔ 后果比"少跑一次"严重：**"它一直活着"的底线（周期任务）一次都没成功过**，
     *     而"开门顺手跑"那次是好的（有 Activity）⇒ 只验前者会一直是绿的。
     *   ⇒ 规矩改成：**谁用 Python，谁负责它起来**（[L0.run] 就在最前面调它）。
     */
    fun ensureStarted(ctx: Context) {
        if (Python.isStarted()) return
        synchronized(this) {
            if (!Python.isStarted()) Python.start(AndroidPlatform(ctx.applicationContext))
        }
    }

    /**
     * ★★ **启动 Python，但不占主线程**（2026-10-06）。
     *
     * 为什么要有它：`Python.start(...)` 是**同步**的（首次要解包/加载解释器），
     * 而它原来在 `MainActivity.onCreate` 里直接调 ⇒ **首帧被它挡住**。
     * 【真机实测】冷启动 TotalTime ≈ 780ms，启动期间 **p99 帧 = 250ms**。
     *
     * ⛔ **不能只是"扔到后台"**：谁先用 Python 谁就得先保证它起来，
     *    否则会重现 2026-10-05 抓到那个真 bug（`Cannot use GenericPlatform on Android`）。
     * ★ 做法：把 `ensureStarted` **排到那条独占 Python 线程的队首** ——
     *    界面侧所有桥调用都走同一个 `dispatcher`（就是这条线程）⇒ **天然有序**：
     *    它一定在随后任何一个桥调用之前跑完，而且是**在后台线程上**跑完的。
     * ⛔ 别再改回 `onCreate` 里同步调；也别改用别的线程池（那样就丢掉"同一队列"这个保证）。
     */
    fun ensureStartedAsync(ctx: Context) {
        if (Python.isStarted()) return
        val app = ctx.applicationContext
        pythonThread.execute { ensureStarted(app) }
    }

    /** 独占的 Python 工作线程。daemon：进程退出时不必等它。 */
    private val pythonThread = Executors.newSingleThreadExecutor { r ->
        Thread(r, "companion-python").apply { isDaemon = true }
    }

    /** 界面侧用 `withContext(PyBridge.dispatcher)` 把重活挪出主线程。 */
    val dispatcher: CoroutineDispatcher = pythonThread.asCoroutineDispatcher()

    /** `set_paths` 只该跑一次；记下它，别每次调用都重设环境变量。 */
    private val pathsSet = AtomicReference<String?>(null)

    // ★ 内核换成了 memory_v2：v1 的适配层单独放 `host_glue_v2.py`，
    //   原 `host_glue.py` 留在工程里当参考（archive 里还有一份完整 v1）。
    private fun glue(): PyObject = py.getModule("host_glue_v2")

    // ------------------------------------------------------------------ 无参调用
    /**
     * 离线自检：证明 numpy + 业务包在手机上真跑得起来（图落盘 / 标记集加减 / 底片压缩）。
     *
     * ★ 要给 `dataDir`：自检在**应用私有目录**下开一个 `_自检/` 干活、跑完删掉
     *   （`tempfile` 在 Chaquopy 里不一定可写，见 `host_glue.selfcheck` 的说明）。
     */
    fun selfCheck(dataDir: String): String = guard { glue().callAttr("selfcheck", dataDir).toString() }

    fun pythonInfo(): String = guard { glue().callAttr("python_info_str").toString() }

    // ------------------------------------------------------------------ 一带参调用
    /**
     * 把 Android 私有目录告诉业务包（凭证路径走 `COMPANION_CRED_PATH`，业务代码不用改）。
     * 幂等：同一个 `dataDir` 只真正调一次 Python。
     */
    fun setPaths(dataDir: String, credPath: String = ""): String {
        pathsSet.get()?.let { if (it == dataDir) return it }
        val r = guard { glue().callAttr("set_paths", dataDir, credPath).toString() }
        pathsSet.set(dataDir)
        return r
    }

    fun credsOk(dataDir: String): Boolean = bool1("creds_ok", dataDir)

    fun stateLines(dataDir: String): List<String> = call1("state_summary_lines", dataDir)

    /**
     * **新线参数账**（`memory/knobs.py` 那 19 项）：逐项「值 + 依据等级 + 说明」。
     * ⛔ 只读 —— 改一个要留「改前→改后→为什么」的记录，不是拖滑块。
     */
    fun ledgerLines(dataDir: String = ""): List<String> = call0("ledger_lines")

    /** 有没有"上一场还没归档完"（进程被杀 / 归档失败过）。 */
    fun pendingLines(dataDir: String): List<String> = call1("pending_lines", dataDir)

    /**
     * 把**已收场但归档被打断**的那一场补做完。⚠️ **要跑 3–4 回合模型，几十秒** ⇒ 必须走后台线程。
     *
     * ★ 2026-10-06 加这条桥：原来 Kotlin 侧**根本调不到它**（只有 `pending_lines` 那条），
     *   于是"收场时被杀"的那一场**只有等用户下次主动打开对话**才会被补做（`chat_open` 那条路），
     *   用户不来它就永远停在未归档。现在挂到 `L0.run` 上 ⇒ WorkManager 每 15 分钟顺带补一次。
     *
     * ⚠️ Python 侧**自己判 `ts_end`**：没收到场的一律不动（那是"接着聊"，不是补归档）。
     */
    fun resumePending(dataDir: String): List<String> = call1("resume_pending", dataDir)

    // ------------------------------------------------------------------ JSON 通道
    // 下面这些都返回 **JSON 字符串**；解析交给界面侧（`ui/Json.kt`）。
    // 一律不抛：失败时 Python 侧回 `{"ok":false,...}`，读不懂也回一个合法的 JSON。

    /** 开一场。**有在途的就接着那一场**。需要凭证。 */
    fun chatOpen(dataDir: String): String = json1("chat_open", dataDir)

    /** ★ 记忆页的数据源：图里的**件**（旧的四档池子在新线里不存在了）。 */
    fun graphViewJson(dataDir: String): String = json1("graph_view_json", dataDir)

    /** 件上的删改：`剪出`（掉出标记集、件还在）/ `删`（从图里移除，记一笔）。 */
    fun graphActionJson(dataDir: String, 件id: String, action: String): String =
        json3("graph_action_json", dataDir, 件id, action)

    fun chatTurnsJson(dataDir: String, sid: String): String =
        json2("chat_turns_json", dataDir, sid)

    /**
     * ★★ **流式回调**（2026-10-07）：Python 侧按名字调 `onDelta(kind, text)`，
     * `kind` ∈ {`phase`, `reasoning`, `content`}。
     *
     * ⚠️ 它在**Python 那条独占线程**上被调（Chaquopy 把 Kotlin 对象直接当 Python 对象用）
     * ⇒ ⛔ 回调里**不许直接改 Compose 状态**（跨线程写状态是未定义行为）。
     * 本工程的做法：往 `MutableSharedFlow` 里 `tryEmit`，由主线程那条协程去应用。
     */
    class StreamSink(private val emit: (kind: String, text: String) -> Unit) {
        @Suppress("unused")                 // ← Python 侧按名字调它，Kotlin 这边看不到调用点
        fun onDelta(kind: String, text: String) {
            emit(kind, text)
        }
    }

    /** 给"带 sink 的桥函数"用：参数个数不定（`chat_ask` 4 个、`chat_finish` 3 个）。 */
    private fun jsonSink(fn: String, vararg args: Any?): String = try {
        glue().callAttr(fn, *args).toString()
    } catch (t: Throwable) {
        failJson(fn, t)
    }

    /** 问一句。⚠️ **要跑模型** ⇒ 必须走 [dispatcher]。给了 [sink] 就流式（CoT 可见）。 */
    fun chatAsk(dataDir: String, sid: String, text: String, sink: StreamSink? = null): String =
        if (sink == null) json3("chat_ask", dataDir, sid, text)
        else jsonSink("chat_ask", dataDir, sid, text, sink)

    /**
     * ★ **撤回最后一轮**（2026-10-06）：把这一轮当成没发生过，可连续用。
     *
     * ⛔ **不跑模型**（只回退内存与 `pending.json`）⇒ 很快；但仍走 [dispatcher]，
     *    因为 `_SESS` 与 `Engine` 的状态都在 Python 那条独占线程上。
     */
    fun chatWithdraw(dataDir: String, sid: String): String =
        json2("chat_withdraw", dataDir, sid)

    /** 收场并归档。⚠️ **一次 25–60 秒** ⇒ 必须走 [dispatcher]。给了 [sink] 就流式。 */
    fun chatFinish(dataDir: String, sid: String, sink: StreamSink? = null): String =
        if (sink == null) json2("chat_finish", dataDir, sid)
        else jsonSink("chat_finish", dataDir, sid, sink)

    // ---- 归档期（2026-10-07）：出草案 → 按你说的改 → 按「完成」才落地。
    // 用户设计：「把归档看作接着刚才对话的特殊对话 …… 最后归档完成之后完成按钮用户点」。

    /** ★ **出草案**（池子一字不动）。⚠️ 25–60 秒 ⇒ 必须走 [dispatcher]。 */
    fun chatArchiveOpen(dataDir: String, sid: String, sink: StreamSink? = null): String =
        jsonSink("chat_archive_open", dataDir, sid, sink)

    /** ★ 归档期里又说一句 ⇒ 改草案（池子仍然不动）。 */
    fun chatArchiveAsk(dataDir: String, sid: String, text: String,
                       sink: StreamSink? = null): String =
        jsonSink("chat_archive_ask", dataDir, sid, text, sink)

    /** ★ **「完成」**：把草案落地（这一步才写池子）。 */
    fun chatArchiveCommit(dataDir: String, sid: String, sink: StreamSink? = null): String =
        jsonSink("chat_archive_commit", dataDir, sid, sink)

    /** 可改参数清单 + 当前生效值 + **每项的依据等级**。 */
    fun knobsJson(dataDir: String): String = json1("knobs_json", dataDir)

    /** 写回用户设置，返回**重新读一遍**的生效值。 */
    fun settingsSave(dataDir: String, overridesJson: String): String =
        json2("settings_save", dataDir, overridesJson)

    /** 凭证**状态**（只给"有没有"，**绝不回显密钥**）。 */
    fun credsStateJson(dataDir: String): String = json1("creds_state_json", dataDir)

    /**
     * 整条覆盖凭证。
     * ★ 密钥传**空字符串 = 这一项不动**（界面拿不到旧密钥，所以"只改地址"必须能留空）。
     */
    fun credsSave(
        dataDir: String,
        embedBaseUrl: String,
        embedApiKey: String,
        chatApiKey: String,
        chatUrl: String,
    ): String = json5("creds_save", dataDir, embedBaseUrl, embedApiKey, chatApiKey, chatUrl)

    /** 会话前缀（system①②）的当前生效值 + 它是不是被改过 + 默认稿长什么样。 */
    fun promptJson(dataDir: String): String = json1("prompt_json", dataDir)

    /** 存提示词覆盖；两段都传空 = 恢复默认（Python 侧会删掉覆盖文件）。 */
    fun promptSave(dataDir: String, sys1: String, sys2: String): String =
        json3("prompt_save", dataDir, sys1, sys2)

    // ---- 2026-10-18：参数账可改 / 提示词按项改 / 可用模型列表
    /** 参数账全部 48 项（键 / 值 / 类型 / 依据 / 说明）。⛔ 只读地列，改走 [ledgerSet]。 */
    fun ledgerJson(dataDir: String): String = json1("ledger_json", dataDir)

    /** 改一项参数（Python 侧校验类型、写覆盖、立刻生效、记一行）。 */
    fun ledgerSet(dataDir: String, key: String, value: String): String =
        json3("ledger_set", dataDir, key, value)

    /** 参数改动记录（新在前）。 */
    fun ledgerChanges(dataDir: String, n: String = "200"): String = json2("ledger_changes", dataDir, n)

    /** 全部提示词（按组）—— 只有元信息，正文单独取。 */
    fun promptListJson(dataDir: String): String = json1("prompt_list_json", dataDir)

    /** 一项提示词的正文（生效那份 + 默认那份）。 */
    fun promptGetJson(dataDir: String, id: String): String = json2("prompt_get_json", dataDir, id)

    // ------------------------------------------------------------------ 旧场（只读）
    /** 已归档的场列表（新在前）。 */
    fun pastListJson(dataDir: String): String = guard { glue().callAttr("past_list_json", dataDir).toString() }

    /** 某一场的全貌：系统前缀 + 常驻 + 每轮（含 CoT / 回忆调用）。只读。 */
    fun pastGetJson(dataDir: String, sid: String): String = json2("past_get_json", dataDir, sid)

    /** 「查一查」（只读）：这句话去回想会亮什么。 */
    fun recallProbe(dataDir: String, sid: String, clue: String): String =
        json3("recall_probe", dataDir, sid, clue)

    /** 存一项提示词（Python 侧校验占位符，不过就不存）。 */
    fun promptSaveOne(dataDir: String, id: String, text: String): String =
        json3("prompt_save_one", dataDir, id, text)

    /** 删掉一项的覆盖 ⇒ 回到仓库默认。 */
    fun promptResetOne(dataDir: String, id: String): String =
        json2("prompt_reset_one", dataDir, id)

    /**
     * 问端点要可用模型（`GET {base}/models`）。
     * `which` = `"embed"` 问嵌入那一家，`"chat"` 问对话那一家（★ 实测不是同一家）。
     * ⚠️ 会联网，必须走 [dispatcher]。
     */
    fun modelsJson(dataDir: String, which: String = "chat"): String =
        json2("models_json", dataDir, which)

    // ------------------------------------------------------------------ 内部
    /** ★ **它最近做了什么**（活动记录；**只读、不推送**）。 */
    fun activityLines(dataDir: String, n: String = "80"): List<String> = call2("activity_lines", dataDir, n)

    /**
     * ★ **通知上回话**（2026-10-05）：打勾 + 把回话存进投递记录（`reply` / `replied_ms`）。
     * ⛔ 这里**不**顺手调模型：回话要走**正常对话**那条路（界面里接成回合），
     *    在这里偷偷调一次会让"它记得的"和"用户看到的"对不上。
     */
    fun deliverReply(dataDir: String, did: String, text: String): String =
        json3("deliver_reply", dataDir, did, text)

    private fun call2(fn: String, a: String, b: String): List<String> = try {
        glue().callAttr(fn, a, b).asList().map { it.toString() }
    } catch (t: Throwable) {
        listOf("调用 $fn 失败：${t.javaClass.simpleName}: ${t.message}")
    }

    private fun json1(fn: String, a: String): String = try {
        glue().callAttr(fn, a).toString()
    } catch (t: Throwable) {
        failJson(fn, t)
    }

    private fun json2(fn: String, a: String, b: String): String = try {
        glue().callAttr(fn, a, b).toString()
    } catch (t: Throwable) {
        failJson(fn, t)
    }

    /**
     * ★★ **L0→L1 一次做完**（2026-10-05）：机械检查 + （有变化且额度够时）**唤起一次模型**。
     * ⚠️ 这条**需要凭证**（L1 可能真调模型）；没配 Key 时它会回 `l1.why = "no_cred"`，
     *    而不是装作"看过了但没想法"。
     */
    fun channelTick(dataDir: String, payloadJson: String): String = json2("channel_tick", dataDir, payloadJson)

    private fun json3(fn: String, a: String, b: String, c: String): String = try {
        glue().callAttr(fn, a, b, c).toString()
    } catch (t: Throwable) {
        failJson(fn, t)
    }

    private fun json4(fn: String, a: String, b: String, c: String, d: String): String = try {
        glue().callAttr(fn, a, b, c, d).toString()
    } catch (t: Throwable) {
        failJson(fn, t)
    }

    private fun json5(
        fn: String,
        a: String,
        b: String,
        c: String,
        d: String,
        e: String,
    ): String = try {
        glue().callAttr(fn, a, b, c, d, e).toString()
    } catch (t: Throwable) {
        failJson(fn, t)
    }

    private fun failJson(fn: String, t: Throwable): String =
        """{"ok":false,"err":"桥接 $fn 失败：${t.javaClass.simpleName}: ${esc(t.message)}"}"""

    private fun esc(s: String?): String =
        (s ?: "").replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ")

    private fun call0(fn: String): List<String> = try {
        glue().callAttr(fn).asList().map { it.toString() }
    } catch (t: Throwable) {
        listOf("调用 $fn 失败：${t.javaClass.simpleName}: ${t.message}")
    }

    private fun call1(fn: String, arg: String): List<String> = try {
        glue().callAttr(fn, arg).asList().map { it.toString() }
    } catch (t: Throwable) {
        listOf("调用 $fn 失败：${t.javaClass.simpleName}: ${t.message}")
    }

    private fun bool1(fn: String, arg: String): Boolean = try {
        glue().callAttr(fn, arg).toJava(Boolean::class.java) as Boolean
    } catch (t: Throwable) {
        false
    }

    private inline fun guard(block: () -> String): String = try {
        block()
    } catch (t: Throwable) {
        "Python 侧失败：${t.javaClass.simpleName}: ${t.message}"
    }
}
