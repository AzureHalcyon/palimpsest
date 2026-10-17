package com.azurehalcyon.palimpsest

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.CreationExtras
import com.azurehalcyon.palimpsest.ui.ChatScreen
import com.azurehalcyon.palimpsest.ui.FlatShell
import com.azurehalcyon.palimpsest.ui.ActivityScreen
import com.azurehalcyon.palimpsest.ui.PoolScreen
import com.azurehalcyon.palimpsest.ui.SettingsScreen
import com.azurehalcyon.palimpsest.ui.Tab
import com.azurehalcyon.palimpsest.ui.AppViewModel

/**
 * 唯一的 Activity。界面是三个屏（对话 / 池子 / 设置），外壳在 [AppShell]。
 *
 * ★ 那个"4 个按钮的自检屏"已经删掉 —— `selfcheck` / 跨平台探针 / 池子状态 /
 *   在途场次这四件事全收进了**设置屏的「诊断」入口**（桥函数一个没少）。
 *
 * ⚠️ 这里**只**做与 Python 有关的一件事：确保解释器起来（脏活收口在 `PyBridge.ensureStarted`）。
 *    真正的调用都在 `AppViewModel` 里 `withContext(PyBridge.dispatcher)`，
 *    **不在主线程**（`chat_ask` 8–9 秒、`chat_finish` 25–60 秒，压主线程必 ANR）。
 */
class MainActivity : ComponentActivity() {

    private val vm: AppViewModel by viewModels {
        object : ViewModelProvider.Factory {
            override fun <T : ViewModel> create(
                modelClass: Class<T>,
                extras: CreationExtras,
            ): T {
                @Suppress("UNCHECKED_CAST")
                return AppViewModel(application) as T
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        // 玻璃特效链要 API 31（RenderEffect）；minSdk 26，真机是 Android 16 ⇒ 实际观感没问题。
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        // ★ Python 的启动收口到 `PyBridge.ensureStarted*`（幂等）：
        //   后台那条路（WorkManager / 广播）没有 Activity，得由 `L0.run` 自己保证它起来。
        //   两处各写一份 `Python.start` 迟早会漂移 —— 现在只有一份。
        // ★★ 2026-10-06 改成**异步版**：`Python.start` 是同步的（首次要解包/加载解释器），
        //   原来在这里同步调 ⇒ **首帧被它挡住**（实测冷启动期间 p99 帧 250ms）。
        //   异步版把这件事排到那条独占 Python 线程的队首，与之后所有桥调用**天然有序**
        //   （所以不会重现"Python 没起来"那个老 bug）。
        PyBridge.ensureStartedAsync(this)
        askNotificationPermission()

        // ★★ 排上空闲窗口的**周期任务**（2026-10-05）。幂等（`KEEP`）⇒ 每次启动都调没问题。
        //   为什么放这里：WorkManager 的任务在"应用被强停/重装"后会丢，
        //   而用户下一次打开 App 就是最自然的"重新排上"的时机。
        //   ⚠️ 上一轮我用正则往这里插过这一行，**没匹配上却打印了"已加"** ⇒ 白跑一轮。
        //      教训：`[regex]::Replace` 不匹配时**静默返回原文**；写完必须回读确认。
        // ★★ 2026-10-06：这两件事（排周期任务 + 踢一脚 L0）都要走 WorkManager 的落盘，
        //   放在 `onCreate` 里就是**在主线程上写磁盘** ⇒ 挪到后台线程（它们本来就不需要主线程）。
        Thread {
            L0.schedulePeriodic(applicationContext)
            // ★★ 顺手跑一次 L0：**打开 App 就是最自然的"看一眼"时机**，
            //   而且它不花模型的钱（见 `apply_l0` 的说明）。
            //   ⚠️ 这一条同时是**唯一可靠的验证入口** —— 实测本机：
            //     · 自定义广播 `DEBUG_L0` **送不到**（MIUI 拦跨 App 广播，和当初绑 Service 同族）
            //     · `cmd jobscheduler run -f <pkg> <id>` 找不到 job（WM 内部 id ≠ JobScheduler id）
            //     · 周期任务 15 分钟才跑一次，等不起
            L0.kick(applicationContext, "app_open")
        }.start()

        // ── ⛔ 这里曾经有两个诊断钩子（`--es health_probe 1` / `--es mi_probe 1`）：
        //    Health Connect 读数探针 · 小米运动健康 provider 探针。
        //    **2026-10-06 已按用户决定删除**：手环这条信源放弃（依据见
        //    `companion-data/探针-HealthConnect-大陆地区门禁-2026-10-06.txt` 与
        //    `companion-data/探针-小米provider直读-2026-10-06.txt`：
        //    小米运动健康在大陆地区**不使用 Health Connect**，它自己的 provider 又只对手环名/电量开一格）。
        //    代码与"如何复活"留档在 `companion-data/_archive/2026-10-06-手环与HealthConnect-已放弃/`。

        // ── 调试钩子（★ 只在 debug 包里）：发一条示例投递通知，用来验"通知 + 回话"渲染。
        //    走 `am start --es notify_test 1` —— 实测 **shell 广播送不到**（MIUI 拦跨 App 广播），
        //    而 `am start` 带 extra 是通的 ⇒ 验证一律走这条。
        if (intent?.getStringExtra("notify_test") != null) {
            val dbg = (applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
            if (dbg) Notify.post(this, "他好像醒了，要不要说点什么？", "d-test")
        }

        // ── 调试钩子（只影响"首帧进哪一屏 / 输入框里预置什么"，不改变任何业务语义）──
        // 用途：真机上的输入法会把 `adb shell input text` 的字符留在组字区而不落到输入框，
        // 自动化验证没法把一句话送进去。有了这两个 extra，验证脚本可以：
        //   adb shell am start -n <pkg>/.MainActivity --es tab pool
        //   adb shell am start -n <pkg>/.MainActivity --es prefill "最近怎么样"
        // 之后仍然点发送按钮，走的路径与手打完全一样。
        intent?.getStringExtra("tab")?.let { t ->
            when (t) {
                "pool" -> vm.selectTab(com.azurehalcyon.palimpsest.ui.Tab.Pool)
                "settings" -> vm.selectTab(com.azurehalcyon.palimpsest.ui.Tab.Settings)
                else -> Unit
            }
        }
        intent?.getStringExtra("prefill")?.let { vm.prefillInput(it) }

        setContent {
            com.niki914.uikit.base.BaseTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    // ★★ **开屏盖在外壳之上**（2026-10-06 用户要求）：
                    //   外壳照常组合、照常跑它那些加载（`chat_open` / 池子 / Python 起来），
                    //   开屏只是**先挡着**，把"用户第一眼"和"后台那阵忙"错开。
                    //   ⛔ 别改成 `if (开屏) 只显示开屏 else 显示外壳` —— 那等于把加载推后。
                    var showSplash by remember { mutableStateOf(true) }
                    Box(Modifier.fillMaxSize()) {
                        // ★ 扁平外壳（不用 uikit 的 LiquidScreen）：顶栏是**占布局空间**的普通
                        //   工具栏，内容从它下面开始 —— "最上方文字被遮挡"从根上消失。
                        //   内容 modifier 由外壳算好（已避开工具栏与底栏），这里只需照用。
                        FlatShell(vm = vm) { contentModifier ->
                            Box(contentModifier) {
                                when (vm.tab) {
                                    Tab.Chat -> ChatScreen(vm = vm)
                                    Tab.Pool -> PoolScreen(vm = vm)
                                    Tab.Activity -> ActivityScreen(vm = vm)
                                    Tab.Settings -> SettingsScreen(vm = vm)
                                }
                            }
                        }
                        if (showSplash) {
                            com.azurehalcyon.palimpsest.ui.SplashOverlay { showSplash = false }
                        }
                    }
                }
            }
        }
    }

    /**
     * 要通知权限（Android 13+ 起是运行时权限）。
     *
     * ★ 为什么必须问：**保活用前台服务的通知**在 13+ 上要有这个权限才显示得出来。
     *   没有它服务照样跑，但你在状态栏看不到"它还在"——那反而更让人不放心。
     * ★ 被拒也不影响任何功能：服务照跑，只是没有那条通知。
     */
    private val notifPermLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private fun askNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (granted) return
        // ★ 只问一次。反复弹同一个系统弹窗不但烦，**它挡住的还是整个界面**（实测：
        //   自动化被它卡住两次）。被拒之后自动降级成"服务照跑、只是没有通知"，
        //   保活本身不受影响。
        val sp = getSharedPreferences("companion", MODE_PRIVATE)
        if (sp.getBoolean("notif_asked", false)) return
        sp.edit().putBoolean("notif_asked", true).apply()
        notifPermLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}
