package com.azurehalcyon.palimpsest.host

import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.widget.ScrollView
import android.widget.TextView
import com.azurehalcyon.palimpsest.plugin.Contract
import com.azurehalcyon.palimpsest.plugin.PluginClient
import org.json.JSONObject
import java.io.File

/**
 * 插件体检器：把"本体 ← 插件"这条链路的**每一步**都跑一遍并留下证据。
 *
 * 顺序就是设计稿 §2 的契约顺序：
 *   **发现 → 绑定 → `hello()`（版本） → `describe()`（能力协商） → 每个 metric `fetch()` 一次**
 *
 * ⚠️ 全部在**子线程**做：`PluginClient` 的调用是阻塞的（内部等跨进程回调），
 *    放主线程就是 ANR。
 */
class MainActivity : Activity() {

    private val sb = StringBuilder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val tv = TextView(this)
        tv.textSize = 9f
        setContentView(ScrollView(this).apply { addView(tv) })
        Thread {
            val out = try { run() } catch (t: Throwable) { "体检崩了：$t" }
            runOnUiThread { tv.text = out }
        }.start()
    }

    private fun run(): String {
        sb.append("插件体检器 · 目标 action = ${Contract.ACTION}\n")
        sb.append("协议版本要求 = ${Contract.PROTOCOL}\n")
        sb.append("------------------------------------------------\n")

        val client = PluginClient(this)

        // ① 发现
        val found = client.discover()
        sb.append("① 发现插件：${found.size} 个\n")
        found.forEach { sb.append("    $it\n") }
        if (found.isEmpty()) {
            sb.append("\n⛔ 一个都没发现。两种可能：\n")
            sb.append("   (a) 插件没装；\n")
            sb.append("   (b) manifest 少了 <queries> 声明（Android 11+ 包可见性）\n")
            sb.append("   ⇒ 这条恰好就是要在体检里分清的事。\n")
            return done()
        }

        for (cn in found) {
            // ★★ 先问系统"你到底认不认这个 service" —— 这一步是为了**别再猜**。
            //   `bindService` 返回 false 且不抛异常时，原因只能在这里看到：
            //   exported / permission / enabled 三个字段是系统对这个组件的最终判定。
            val probeIntent = android.content.Intent(Contract.ACTION).setComponent(cn)
            val ri = try { packageManager.resolveService(probeIntent, 0) } catch (t: Throwable) { null }
            sb.append("\n===== ②' resolveService = $ri\n")
            ri?.serviceInfo?.let { si ->
                sb.append("     exported=${si.exported} permission=${si.permission} " +
                    "enabled=${si.enabled} processName=${si.processName}\n")
                sb.append("     packageName=${si.packageName} name=${si.name}\n")
            }
            if (ri == null) sb.append("     ⛔ 系统解析不到这个组件（visibility / 组件没进 APK / 被禁）\n")

            sb.append("\n===== ② 绑定 $cn\n")
            if (!client.bind(cn)) {
                sb.append("    绑定失败：${client.lastError}\n")
                sb.append("    ⚠️ 若报 SecurityException ⇒ 签名/权限不对（体检器与插件不是同一签名）\n")
                continue
            }
            sb.append("    绑定成功\n")

            // ③ 版本协商
            val hi = client.hello()
            sb.append("③ hello()：$hi\n")
            val proto = hi?.optInt(Contract.K_PROTOCOL, -1) ?: -1
            if (proto != Contract.PROTOCOL) {
                sb.append("    ⛔ 协议版本不匹配（插件 $proto / 本体 ${Contract.PROTOCOL}）⇒ 本体应当拒绝这个插件，而不是硬用\n")
                client.unbind()
                continue
            }

            // ④ 能力协商
            val metrics = client.describe()
            sb.append("④ describe()：${metrics?.length() ?: 0} 个读数\n")
            if (metrics == null) {
                sb.append("    ⛔ describe 失败：${client.lastError}\n")
                client.unbind()
                continue
            }

            // ★★ 预热：**绑上之后不能立刻读**。
            //   插件是"绑上才采"的（见 SensorPluginService 的注释），而 `fetch()` 是**窗口语义**：
            //   刚绑上时缓冲区里几乎没有东西 —— 连续类能在几毫秒内来几条，
            //   变化时/触发类（HAR、驾驶、显著运动）本来就"有事才报"，立刻读必然空。
            //   ⇒ 这里等 8 秒，让"读得到"和"读不到"这件事**分得清**：
            //     等到 8 秒还空，那是真没数据；不等就读，看到的只是"还没开始采"。
            sb.append("    （预热 8 秒，让慢速/变化时类传感器有机会出数据）\n")
            Thread.sleep(8000)

            // ⑤ 每个 metric 各读一次（最近 5 分钟）
            val to = System.currentTimeMillis()
            val from = to - 5 * 60 * 1000L
            for (i in 0 until metrics.length()) {
                val mi = metrics.optJSONObject(i) ?: continue
                val id = mi.optString(Contract.K_M_ID)
                val tool = mi.optString(Contract.K_M_TOOL_NAME)
                val r = client.fetch(id, from, to)
                sb.append("⑤ fetch($id)\n")
                sb.append("     工具名（给模型看的）= $tool\n")
                sb.append("     useWhen = ${mi.optString(Contract.K_M_USE_WHEN)}\n")
                if (r == null) {
                    sb.append("     ⛔ 调用失败：${client.lastError}\n")
                } else {
                    sb.append("     ok=${r.optBoolean(Contract.K_OK)} value=${r.opt(Contract.K_VALUE)} " +
                        "coverage=${r.opt(Contract.K_COVERAGE)}\n")
                    sb.append("     trend=${r.optJSONObject(Contract.K_TREND)}\n")
                    sb.append("     note=${r.optString(Contract.K_NOTE)}\n")
                }
            }
            client.unbind()
        }

        // ⑥ 把 describe 的完整内容也留一份（含给模型看的那份描述）—— 这是最该被检查的东西
        sb.append("\n===== ⑥ 完整 describe 输出（给模型看的工具描述就在里面）\n")
        client.bind(found.first())
        sb.append(client.describe()?.toString(2) ?: "（拿不到）")
        client.unbind()

        // ⑦ ★★ 第二条传输路线：ContentProvider
        sb.append(probeProvider())

        return done()
    }

    /**
     * ★★ provider 路线 —— 实测发现"跨 App 绑 Service"在本机被系统挡住
     * （`bindService` 返回 false 且不抛异常、`resolveService` 却一切正常），
     * 而 **provider 是系统按需拉起的**，所以它是设计上的传输。
     * 这一段就是用来确认它到底通不通。
     */
    private fun probeProvider(): String {
        val auth = "com.azurehalcyon.palimpsest.probe.companion.plugin"
        val uri = android.net.Uri.parse("content://$auth")
        sb.append("\n===== ⑦ provider 路线 authority=$auth\n")
        try {
            val hi = contentResolver.call(uri, "hello", null, null)?.getString(RESULT)
            sb.append("hello → $hi\n")
            if (hi == null) {
                sb.append("⛔ 拿到空结果：provider 不可达（可见性 / authority 写错 / 没装）\n")
                return sb.toString()
            }
            val de = contentResolver.call(uri, "describe", null, null)?.getString(RESULT)
            sb.append("describe 长度 = ${de?.length ?: 0}（给模型看的工具描述在里面）\n")

            // ★ 预热：说明见上面服务那条（read 是窗口语义，刚起来时缓冲区基本是空的）
            sb.append("  （预热 8 秒）\n")
            Thread.sleep(8000)
            val to = System.currentTimeMillis()
            val from = to - 5 * 60 * 1000L
            for (m in listOf("accel", "light", "activity_raw", "stationary", "steps")) {
                val args = Bundle().apply {
                    putString("metric", m); putLong("from", from); putLong("to", to)
                }
                val r = contentResolver.call(uri, "fetch", m, args)?.getString(RESULT)
                sb.append("fetch($m) → ${r?.take(260)}\n")
            }
            // ★ 顺便验"不认识的方法要明确报错"，而不是静默返回空
            val bogus = contentResolver.call(uri, "no_such_method", null, null)?.getString(RESULT)
            sb.append("未知方法 → $bogus\n")
        } catch (t: Throwable) {
            sb.append("⛔ provider 路线抛异常：${t.javaClass.simpleName}: ${t.message}\n")
        }
        return sb.toString()
    }

    private fun done(): String {
        sb.append("\n------------------------------------------------\n体检完。报告在 files/plugin-host.txt\n")
        try { File(filesDir, "plugin-host.txt").writeText(sb.toString()) } catch (t: Throwable) {
            Log.w(TAG, "落盘失败 $t")
        }
        Log.i(TAG, "HOST PROBE DONE")
        return sb.toString()
    }

    companion object {
        private const val TAG = "pluginhost"

        /** provider 返回的 JSON 装在这个键上（与 `SensorPluginProvider.RESULT` 要对上）。 */
        private const val RESULT = "json"
    }
}
