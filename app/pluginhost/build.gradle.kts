// ★ 宿主客户端（**独立 APK**）：证明"本体 ← 插件"这条跨进程链路真的通。
//
// 为什么不直接塞进 :app：
//   1. :app 是 80 MB 的 Chaquopy 包，为了验一个 AIDL 往返去动它不划算；
//   2. 这一支本身就是**诊断工具**，将来还能当"插件体检器"用；
//   3. 真正要用的宿主代码在 `:plugin-api` 的 `PluginClient` 里 —— 本体以后直接复用那一个类。
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.azurehalcyon.palimpsest.host"
    compileSdk = 36
    buildToolsVersion = "36.1.0"

    defaultConfig {
        applicationId = "com.azurehalcyon.palimpsest.host"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { aidl = true }
}

dependencies {
    implementation(project(":plugin-api"))
}
