pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // ★ Capsule 只发在 JitPack 上（`com.github.Kyant0:Capsule`）。
        //   少了这一行必然解析失败，且报错会指向"找不到 Capsule"而不是"少了仓库"。
        maven { url = uri("https://jitpack.io") }
    }
}

rootProject.name = "palimpsest-app"
include(":app")
// 设计系统（vendored 自 MIT 的 niki914/zafiro，见 uikit/NOTICE.md）
include(":uikit")
// ★★ **渠道插件**（独立 applicationId ⇒ 独立 APK）。它现在承载**两个数据域**：
//    ① 手机传感器（探针起家：测"小米 OEM 传感器第三方能不能注册监听"）
//    ② 日历（2026-10-06 加：与传感器互不依赖的第二条信源）
//    ★ 为什么两个数据域**同一个 APK**（而不是各做一个）：实测代价 ——
//      多一个包就多一道安装确认（MIUI 拦新包：`INSTALL_FAILED_USER_RESTRICTED`，
//      而**更新**已有插件是通的），还多一个桌面图标、多一个模块；
//      而"权限隔离"的收益在自用场景很小（同一台设备/用户/签名）。
//    ⇒ 权限仍**按数据域申请**（`ACTIVITY_RECOGNITION` / `READ_CALENDAR`），只是同住一个 APK。
//    ⚠️ **applicationId 保持 `com.azurehalcyon.palimpsest.probe` 不变** —— 这样是**原地更新**，
//       不是新装（宿主 manifest 里的 `<queries><package …>` 也照旧）。
//    刻意不复用 :app 的任何东西 —— 插件就该跟本体分开。
include(":channelplugin")
// ★★ 插件契约（library）：**本体与插件共用的唯一一份接口定义**（AIDL + 键名常量 + 客户端）。
//    两边都依赖它 ⇒ 接口不可能漂移。
include(":plugin-api")
// ★ 宿主客户端（独立 APK）：只用来**证明跨 App 往返真的通**（发现→绑定→权限→AIDL→JSON）。
//    真正要用的宿主代码在 :plugin-api 的 PluginClient 里，本体以后直接复用那个类。
include(":pluginhost")