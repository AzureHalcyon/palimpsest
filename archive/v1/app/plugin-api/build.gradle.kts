// 契约库：**本体与插件共用的唯一一份接口定义**
//
// 为什么单独一个 library 模块：
//   用户定的形态是「插件 = 独立 APK + 一个接口」⇒ 那个"接口"必须**只有一份**，
//   否则两边各写一份 .aidl 迟早漂移。放在这里，本体和每个插件都 `implementation(project(":plugin-api"))`，
//   编译期就对着同一份校验。
//
// ⛔ 这里**不放任何实现**，也不依赖 :app —— 插件不该看见本体的任何东西。
plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "com.azurehalcyon.palimpsest.plugin"
    compileSdk = 36
    buildToolsVersion = "36.1.0"

    defaultConfig {
        minSdk = 26
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures {
        // ★ AIDL 要显式打开，否则 `src/main/aidl` 会被忽略（且报错只在引用处出现，不好查）
        aidl = true
    }
}
