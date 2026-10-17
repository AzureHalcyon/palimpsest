package com.azurehalcyon.palimpsest.plugin

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import org.json.JSONObject

/**
 * 插件的**取数客户端（provider 路线）**。
 *
 * ⚠️ 为什么不是 bound Service：实测（2026-10-05）这台 HyperOS 上跨 App 绑 Service 会被系统挡住 ——
 *   `bindService()` 返回 false 且不抛异常、`resolveService` 一切正常、去掉权限也一样、
 *   提到 `standby-bucket active` 也一样；`am startservice` 直接报 `app is in background uid null`。
 *   ⇒ 传输走 **ContentProvider**（provider 由系统按需拉起）：
 *     插件进程**已经在跑**时全通；**没跑过**时 `Unknown authority`（那就静默降级）。
 *
 * ★ 与 `PluginClient`（AIDL 那套）并存：那个保留作**接口规范**，这个才是**实际传输**。
 */
object ProviderClient {

    /** authority 约定：`<插件包名> + 这个后缀`（与 `SensorPluginProvider` 一致）。 */
    const val AUTHORITY_SUFFIX = ".companion.plugin"

    fun authorityOf(pkg: String) = pkg + AUTHORITY_SUFFIX

    /** 发现插件（用**它们声明的 action**）。★ 宿主 manifest 里要有对应的 `<queries>`。 */
    fun discover(ctx: Context): List<String> {
        val pm = ctx.packageManager
        return try {
            pm.queryIntentServices(Intent(Contract.ACTION), 0)
                .mapNotNull { it.serviceInfo?.packageName }
                .distinct().sorted()
        } catch (t: Throwable) {
            emptyList()
        }
    }

    /** 调一次 provider 的 `call()`。**任何失败都回 null**，由调用方决定降级。 */
    fun call(ctx: Context, pkg: String, method: String, arg: String? = null,
             extras: Bundle? = null): JSONObject? {
        val uri = Uri.parse("content://" + authorityOf(pkg))
        return try {
            val b = ctx.contentResolver.call(uri, method, arg, extras) ?: return null
            val raw = b.getString("json") ?: return null
            JSONObject(raw)
        } catch (t: Throwable) {
            // ★ 这里**不抛**：插件没跑 / 没装 / authority 不对，都是"这个渠道现在没有"，
            //   是设计里的正常情形（设计稿 §2.5 静默降级），不是错误。
            null
        }
    }

    /** `describe()`：拿到插件能提供哪些读数。 */
    fun describe(ctx: Context, pkg: String): List<JSONObject> {
        val r = call(ctx, pkg, "describe") ?: return emptyList()
        val arr = r.optJSONArray("metrics") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    }

    /** `fetch()`：取一次读数。 */
    fun fetch(ctx: Context, pkg: String, metric: String, fromMs: Long, toMs: Long): JSONObject? {
        val extras = Bundle().apply {
            putString("metric", metric)
            putLong("from", fromMs)
            putLong("to", toMs)
        }
        return call(ctx, pkg, "fetch", metric, extras)
    }
}
