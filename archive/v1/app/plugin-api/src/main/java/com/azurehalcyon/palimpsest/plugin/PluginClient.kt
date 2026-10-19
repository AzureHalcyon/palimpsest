package com.azurehalcyon.palimpsest.plugin

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * 本体侧的**插件客户端**：发现 → 绑定 → 调 `hello/describe/read`。
 *
 * 放在契约库里（而不是本体里），这样**任何宿主都能复用**：本体、诊断 App、以后的命令行工具。
 *
 * ⚠️ 三个必须知道的约束（都来自设计稿 §2.5）：
 *   · **未发现插件不是错误** —— `discover()` 回空表示"这个渠道没有"，本体应当**静默降级**，
 *     也就是"工具表里不出现"，而不是报错。
 *   · **绑定会失败/超时** —— 插件没装、被禁、或版本不匹配都会走到这条路上。
 *   · ⛔ **下面三个 `call*` 是阻塞的**（内部用 `CountDownLatch` 等跨进程回调），
 *     **绝不能在主线程调** —— 否则就是 ANR。
 */
class PluginClient(private val ctx: Context) {

    private var svc: CompanionPlugin? = null

    /** 最近一次失败原因。**本体要能说出"为什么这个渠道没了"**，而不是默默少一个工具。 */
    var lastError: String? = null
        private set

    /** 已绑定的插件组件（`unbind()` 要用）。 */
    private var bound: ComponentName? = null

    /** 发现插件。**空列表 = 这个渠道没有**（不是错）。 */
    fun discover(): List<ComponentName> {
        val pm = ctx.packageManager
        val i = Intent(Contract.ACTION)
        // ⚠️ Android 11+ 必须在 manifest 里 <queries> 声明这个 action，否则这里恒为空
        return pm.queryIntentServices(i, 0).mapNotNull { ri ->
            val si = ri.serviceInfo ?: return@mapNotNull null
            ComponentName(si.packageName, si.name)
        }.sortedBy { it.packageName }
    }

    /** 绑定（阻塞到 `timeoutMs`）。成功返回 true。 */
    fun bind(cn: ComponentName, timeoutMs: Long = 4000): Boolean {
        unbind()
        lastError = null
        val latch = CountDownLatch(1)
        val conn = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
                svc = CompanionPlugin.Stub.asInterface(binder)
                bound = name
                latch.countDown()
            }
            override fun onServiceDisconnected(name: ComponentName?) {
                // 插件进程死了。★ 不当异常处理：下一次 read 会拿到 lastError，本体降级即可。
                svc = null
                lastError = "插件进程断开：$name"
            }
        }
        val ok = try {
            ctx.bindService(Intent(Contract.ACTION).setComponent(cn), conn, Context.BIND_AUTO_CREATE)
        } catch (t: Throwable) {
            lastError = "bindService 抛异常：$t"
            false
        }
        if (!ok) {
            lastError = lastError ?: "bindService 返回 false（插件不可用？）"
            return false
        }
        val got = latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        if (!got || svc == null) {
            lastError = lastError ?: "绑定超时（${timeoutMs}ms）"
            return false
        }
        // 记下连接，供 unbind 使用
        this.conn = conn
        return true
    }

    private var conn: ServiceConnection? = null

    fun hello(): JSONObject? = call { it.hello() }

    fun describe(): JSONArray? =
        call { it.describe() }?.optJSONArray(Contract.K_METRICS)

    fun fetch(metric: String, fromMs: Long, toMs: Long): JSONObject? =
        call { it.fetch(metric, fromMs, toMs) }

    /**
     * 统一收口：**插件抛异常不许穿到本体里**。
     * 跨进程调用失败（RemoteException、插件里 SecurityException 等）一律转成 `null` + `lastError`。
     */
    private inline fun call(block: (CompanionPlugin) -> String?): JSONObject? {
        val s = svc ?: run { lastError = lastError ?: "还没绑定"; return null }
        return try {
            val raw = block(s) ?: run { lastError = "插件返回空"; return null }
            JSONObject(raw)
        } catch (t: Throwable) {
            lastError = "${t.javaClass.simpleName}: ${t.message}"
            null
        }
    }

    fun unbind() {
        try { conn?.let { ctx.unbindService(it) } } catch (_: Throwable) {}
        conn = null
        svc = null
    }
}
