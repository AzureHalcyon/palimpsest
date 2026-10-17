package com.azurehalcyon.palimpsest.probe

import android.content.ContentProvider
import android.content.ContentValues
import android.database.Cursor
import android.net.Uri
import android.os.Bundle
import com.azurehalcyon.palimpsest.plugin.Contract

/**
 * 插件的**传输层（ContentProvider）**。
 *
 * ★★ 为什么传输不是 bound Service（2026-10-05 实测）：
 *   在这台设备（小米 14 / Android 16 / HyperOS）上，**跨 App 绑 Service 被系统挡住**：
 *     · `resolveService` 能解析到、`exported=true`、`enabled=true`、权限也没问题；
 *     · 但 `bindService()` **返回 false 且不抛异常**；
 *     · `am startservice` 直接报 `Error: app is in background uid null`；
 *     · 把插件提到 `standby-bucket active` 也一样。
 *   ⇒ 这不是权限/可见性问题，是"**不让它为一个 bind 去生成另一个 App 的进程**"。
 *
 *   **ContentProvider 恰好绕开这一条**：provider 是**系统按需拉起**的
 *   （push SDK 全用这个机制就是这个原因）⇒ 插件没在跑也能被访问。
 *
 * ★ 接口没变：还是 `hello / describe / read` 三个（见 `CompanionPlugin.aidl` 与 `Contract`），
 *   只是搬到了 provider 的 `call(method, arg, extras)` 上。
 *   ⚠️ `Bundle` 走 Binder，**有事务大小上限**（设计稿 §2.3）⇒ 大结果仍然要回 Uri 而不是塞 Bundle。
 */
class SensorPluginProvider : ContentProvider() {

    private lateinit var core: PluginCore

    override fun onCreate(): Boolean {
        // ★★ 进程内单例（多一份 core 就多一份传感器注册 ⇒ 同一事件记两遍）
        core = PluginCore.get(requireNotNull(context))
        return true
    }

    /** 三个方法都走这里。`extras` 传参（读数的窗口），返回里装 JSON 字符串。 */
    override fun call(method: String, arg: String?, extras: Bundle?): Bundle {
        val out = Bundle()
        try {
            when (method) {
                "hello" -> out.putString(RESULT, core.hello())
                "describe" -> out.putString(RESULT, core.describe())
                // ★ 诊断（探针自检用）：各渠道的注册/武装状态 + 哪些是"采集但不暴露"
                "diagnostics" -> out.putString(RESULT, core.diagnostics())
                "fetch" -> {
                    val metric = extras?.getString("metric") ?: arg.orEmpty()
                    val from = extras?.getLong("from", 0L) ?: 0L
                    val to = extras?.getLong("to", System.currentTimeMillis()) ?: System.currentTimeMillis()
                    out.putString(RESULT, core.fetch(metric, from, to))
                }
                // ★ 不认识的 method 要**明确回错误**，不要静默返回空 Bundle
                //   （否则宿主会把"方法名写错"当成"这个渠道没数据"）
                else -> out.putString(RESULT, """{"ok":false,"note":"未知方法：$method"}""")
            }
        } catch (t: Throwable) {
            out.putString(RESULT, """{"ok":false,"note":"provider 内部出错：${t.javaClass.simpleName}: ${t.message}"}""")
        }
        return out
    }

    // ContentProvider 的四个抽象方法这里用不到（我们这个 provider 不是数据库）。
    // ⛔ 刻意**不实现成"能查表"** —— 少一个面就少一个越权入口。
    override fun query(u: Uri, p: Array<out String>?, s: String?, a: Array<out String>?, o: String?): Cursor? = null
    override fun getType(uri: Uri): String? = null
    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, s: String?, a: Array<out String>?): Int = 0
    override fun update(uri: Uri, v: ContentValues?, s: String?, a: Array<out String>?): Int = 0

    companion object {
        /** 结果 JSON 装在 Bundle 的这个键上。 */
        const val RESULT = "json"

        /**
         * 约定的 authority。★ 用**包名 + 固定后缀** ⇒ 宿主从发现的包名就能推出来，
         * 不需要在宿主里硬编码每一个插件。
         */
        const val AUTHORITY_SUFFIX = ".companion.plugin"
        fun authorityOf(pkg: String) = pkg + AUTHORITY_SUFFIX
    }
}
