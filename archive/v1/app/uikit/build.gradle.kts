// 设计系统模块。
//
// 源码 vendored 自 MIT 许可的 `niki914/zafiro` 的 `ui-kit/`（见 NOTICE.md）。
// 这里**只改构建配置，不改它的代码** —— 目标是让上游的组件原样能用。
//
// 唯一两处与原版的差别（都是被迫的）：
//   1. 插件版本改走本工程的版本目录（原版写死 `com.android.library 9.1.1`），
//      这样它跟着 AGP 8.13.2 走，不必为了一个 UI 库升级整条构建链；
//   2. material3 **不写版本**，交给 Compose BOM（=1.4.0）。原版钉 1.5.0-alpha22，
//      而那一版的 AAR 硬要求 minCompileSdk=37 + minAGP=9.1.0。
//   被删掉的只有 `base/ComposeMVIViewModel.kt`（原版里唯一依赖它自家 logging 模块的文件，
//   且全模块无人引用；本工程不用 MVI）。
plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "com.niki914.uikit"
    compileSdk = 36
    buildToolsVersion = "36.1.0"

    defaultConfig {
        minSdk = 26
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
    }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.core)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)

    // 玻璃观感：Capsule 出形状（G2 连续曲率），backdrop 出模糊/透镜/高光
    implementation(libs.kyant.capsule)
    implementation(libs.kyant.backdrop)
    implementation(libs.material.kolor)
}
