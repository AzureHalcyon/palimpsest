package com.azurehalcyon.palimpsest.probe

import android.app.Service
import android.content.Intent
import android.os.IBinder
import com.azurehalcyon.palimpsest.plugin.CompanionPlugin

/**
 * 插件的 **AIDL 传输层（bound Service）** —— 现在是**薄壳**，逻辑在 `PluginCore`。
 *
 * ⚠️ **实测：这条路在这台设备上走不通**（跨 App 绑 Service 被系统挡住，详见 `SensorPluginProvider`）。
 *   保留它有两个理由：
 *   1. `CompanionPlugin.aidl` 是**接口的规范**（三个方法 + 版本协商），留着让契约有据可依；
 *   2. 换台设备/换 ROM 也许就通了 —— 那时把传输切回 Service 只改这一层。
 *   ⛔ **但设计上不能依赖它**：设计的传输是 ContentProvider。
 */
class SensorPluginService : Service() {

    private lateinit var core: PluginCore

    override fun onCreate() {
        super.onCreate()
        // ★★ 进程内单例（与 provider 共用同一个 core ⇒ 不会注册两路）
        core = PluginCore.get(this)
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onUnbind(intent: Intent?): Boolean {
        core.stop()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        core.stop()
        super.onDestroy()
    }

    private val binder = object : CompanionPlugin.Stub() {
        override fun hello(): String = core.hello()
        override fun describe(): String = core.describe()
        override fun fetch(metric: String, fromMs: Long, toMs: Long): String =
            core.fetch(metric, fromMs, toMs)
    }
}
