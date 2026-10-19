// ★ 探针模块（独立 APK）：`com.azurehalcyon.palimpsest.probe`
//
// 为什么是**独立模块 / 独立 applicationId**：
//   1. 它要测的东西（往小米 OEM 传感器上 registerListener）**不该混进陪伴本体**；
//   2. 用户定的插件形态就是「**独立 APK + 一个接口**」⇒ 这个模块顺便当**第一个插件的骨架**；
//   3. 复用主工程已验证的 AGP 8.13.2 / Kotlin 2.2.21 / Gradle 缓存，不重新下载工具链。
//
// ⛔ 刻意**不用 Compose**（原生 View 就够），也**不要任何危险权限**：
//   这次只测「能不能注册监听」，注册本身不需要权限（带权限的那 4 个 step_* 另行处理）。
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.azurehalcyon.palimpsest.probe"
    compileSdk = 36
    buildToolsVersion = "36.1.0"

    defaultConfig {
        applicationId = "com.azurehalcyon.palimpsest.probe"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0-probe"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { aidl = true }   // 实现 CompanionPlugin.Stub() 需要
}

dependencies {
    // ★ 契约只有一份：插件也依赖同一个 :plugin-api
    implementation(project(":plugin-api"))
}

