// 根构建脚本：**只声明插件版本，不应用**。
//
// 为什么需要它：`:app` 与 `:uikit` 各自用 `alias(...)` 应用 Kotlin 插件时，
// Gradle 会在不同子工程里各加载一次 Kotlin 插件，报
//   "The Kotlin Gradle plugin was loaded multiple times in different subprojects"
// —— 官方建议就是把插件提到根工程并标 `apply false`，版本只在版本目录里写一次。
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.chaquopy) apply false
}
