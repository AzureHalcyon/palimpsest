package com.azurehalcyon.palimpsest

import android.content.Context
import com.azurehalcyon.palimpsest.plugin.ProviderClient
import org.json.JSONArray
import org.json.JSONObject

/**
 * 把插件"问一遍"，交出一份给 Python 的**载荷**。
 *
 * ★★ 为什么这一步在 Kotlin、而不是 Python 里做：
 *   调 provider 是 Android 的事（`ContentResolver.call`）。把它挡在 Kotlin 这一侧，
 *   `companion.channels.apply_l0()` 就**不知道 Android 的存在** ⇒ **能离线自检**
 *   （这是 2026-10-05 定下的分层：**插件 · 宿主 · 业务包** 三层各管各的）。
 *
 * ★ 这一步**不碰模型**（也就没有模型调用）—— 它是 L0 的"机械"部分。
 */
object ChannelHost {

    /** 读数窗口：L0 只看最近这段时间，够判断"有没有变化"就行。 */
    private const val WINDOW_MS = 24L * 60 * 60 * 1000

    /**
     * 收集载荷：`{"plugins":[{"pkg","name","metrics":[…]}], "reads":{"<pkg>|<metric>": {…}}}`
     *
     * ⚠️ **插件进程没在跑**时 provider 调不通（`Unknown authority`）⇒
     *   这一路拿到的是空 `plugins`，于是 Python 那边会把渠道从目录里撤掉（静默降级）。
     *   这是**已知且实测过**的行为，不是 bug。
     */
    fun collect(ctx: Context): String {
        val plugins = JSONArray()
        val reads = JSONObject()
        val now = System.currentTimeMillis()
        for (pkg in ProviderClient.discover(ctx)) {
            val metrics = ProviderClient.describe(ctx, pkg)
            if (metrics.isEmpty()) continue
            val hello = ProviderClient.call(ctx, pkg, "hello")
            val item = JSONObject()
                .put("pkg", pkg)
                .put("name", hello?.optString("name") ?: pkg)
                .put("metrics", JSONArray(metrics))
            plugins.put(item)
            for (m in metrics) {
                val id = m.optString("id")
                if (id.isEmpty()) continue
                val r = ProviderClient.fetch(ctx, pkg, id, now - WINDOW_MS, now)
                reads.put("$pkg|$id", r ?: JSONObject().put("ok", false)
                    .put("note", "没取到（插件进程可能没在跑）"))
            }
        }
        return JSONObject().put("plugins", plugins).put("reads", reads).toString()
    }
}
