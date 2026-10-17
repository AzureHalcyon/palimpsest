plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.chaquopy)
}

android {
    namespace = "com.azurehalcyon.palimpsest"
    compileSdk = 36
    buildToolsVersion = "36.1.0"

    defaultConfig {
        applicationId = "com.azurehalcyon.palimpsest"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0-mobile"

        // 手机（骁龙 8 Gen 3）是 arm64-v8a；x86_64 是给**本机模拟器**验证用的。
        // 将来出正式自用包时可以把 x86_64 去掉，APK 能小十几 MB。
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 自用侧载：先用 debug 签名，省掉 keystore 的麻烦（要发出去再换）
            signingConfig = signingConfigs.getByName("debug")
        }
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

chaquopy {
    defaultConfig {
        version = "3.13"                          // 与构建机 Python 3.13.0 同主次版本
        buildPython("E:/Python3.13/python.exe")   // 不依赖 PATH 上的 py 启动器
        // ★ 下面两个由 `build.ps1` 用 `-P` 传进来。**刻意不做成写死的常量**：
        //   · pipProxy    —— 代理**端口每次现探**（见 ~/.dsh/工具详情.md 的 FlClash 三步探测：
        //                    注册表 → `netstat -ano` 找 LISTENING → `curl -x` 实测 200 才算数）。
        //                    写死过一次 127.0.0.1:7890，刷新后失效 ⇒ 绝不写进工程文件。
        //                    chaquo.com 在国内直连很慢（实测：500 KB 级半截下载挂十几分钟）。
        //   · pipCacheDir —— 把 pip 的轮子缓存放到 `app/build` **之外**。
        //                    否则清构建会把已下好的 numpy(4.8 MB) 一起清掉，下次又得重下；
        //                    而下面的 `doNotTrackState` 让这步**每次构建都跑**。
        val pipProxy = (project.findProperty("pipProxy") as String?)?.takeIf { it.isNotBlank() }
        val pipCacheDir = (project.findProperty("pipCacheDir") as String?)?.takeIf { it.isNotBlank() }
        pip {
            // ★ 索引这一行是踩过坑的：
            //   · Chaquopy 官方站（chaquo.com/pypi-13.1）才有 **android_24_arm64_v8a** 的
            //     预编译 wheel（numpy 4.8 MB / PyYAML 143 KB），必须放主索引；
            //   · 国内镜像只有 manylinux / win 的轮子，找不到 android 标签 —— 我第一次
            //     把 --index-url 设成清华镜像，就直接 "No matching distribution found"；
            //   · 清华留作后备（纯 Python 包走它快，0.06 s vs pypi.org 的 1.19 s）；
            //   · 短超时：默认索引 pypi.org 实测会长时间无响应，第一次构建卡死 11 分钟。
            options("--index-url", "https://chaquo.com/pypi-13.1")
            options("--extra-index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
            options("--timeout", "20", "--retries", "2")
            pipProxy?.let { options("--proxy", it) }
            pipCacheDir?.let { options("--cache-dir", it) }
            install("numpy")                      // 官方预编译 wheel（arm64_v8a）
            install("PyYAML")
        }
        pyc {
            // 自用调试期保留 .py，这样手机上的栈里能看见源码行号。
            // 稳定之后改成 true（APK 里只留 .pyc，体积更小、启动更快）。
            src = false
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("src/main/python")
        }
    }
}

// ★ Chaquopy 的 pip 任务**必须关掉增量状态跟踪**（2026-10-05 实测真因）：
//   那一步是在**低完整性级别**下跑的 ⇒ 它建出来的 `app/build/python/pip/...`
//   带 `Mandatory Label: Low (NW)`，而那条路径上**当前用户一条权限项都没有**
//   （只有 SYSTEM / Administrators / OWNER RIGHTS）。于是下一次构建时 Gradle
//   想**读**这个已存在的输出目录做状态快照，直接
//     java.nio.file.AccessDeniedException: ...\pip\debug\arm64-v8a\chaquopy
//     Cannot access output property 'outputDir' ... unreadable inputs or outputs
//   ⛔ 这**不是**沙箱 ACL 那类问题（`diagnose-windows-sandbox-acl` 两次都判
//      NOT_THIS_CLASS，是对的），也**不是**工作区权限缺失 —— 手删那个目录只能治一次。
//   ✅ 正解就是 Gradle 报错里自己给的那句：这几个任务不参与状态跟踪。
tasks.matching { it.name.endsWith("PythonRequirements") }.configureEach {
    doNotTrackState("Chaquopy pip 的产物建在低完整性级别下，状态快照读不了它")
}

dependencies {
    implementation(project(":uikit"))
    // ★ L0（空闲窗口）要：WorkManager 排周期任务；插件契约要 ProviderClient/Contract
    implementation(libs.androidx.work.runtime)
    implementation(project(":plugin-api"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    // ★ 下面三条是 `:uikit` 用 `implementation` 引入的（不是 `api`），所以**不会**
    //   出现在 :app 的编译类路径上 —— 而 :app 的界面要直接用它们：
    //     · `material-icons-*`：Icons.Default.* / Icons.AutoMirrored.Filled.*;
    //     · `kyant-backdrop`  ：`LiquidButton` / `LiquidDialog` 的 `Backdrop` 参数
    //       （这是个**必要**参数，拿不到这个类型就连按钮都构造不出来）。
    //   漏掉它们的报错长这样：`Unresolved reference 'Icons'` /
    //   `Cannot access class 'Backdrop'. Check your module classpath`。
    implementation(libs.androidx.compose.material.icons.core)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.kyant.backdrop)
    // ★ 直接声明而不是靠传递依赖：UIKit / backdrop 都会把它带进来，
    //   但「谁需要谁声明」这条规矩一破，将来它们换版本就会把这里带崩。
    //   本工程自己用到了 ViewModelStore（NavigationController 的 per-entry store）。
    implementation(libs.androidx.lifecycle.viewmodel.compose)

    // ★★ **LaTeX 真排版**（2026-10-06，用户要求「就让 markdown 也能渲染 Latex」+「不是做成 unicode，
    //   是真渲染，要不然复杂公式可读性不好」）。
    //   · 坐标已确认：Maven Central 上 `ru.noties:jlatexmath-android:0.2.0`（aar，2020-04-30），
    //     源码/上游是 JLaTeXMath（GPL-2 **带 Classpath Exception**）—— 只是链接，不传染本项目源码。
    //   · API 用 `javap` 从 aar 里读出来的（不是猜的）：`JLatexMathDrawable.builder(tex)
    //     .textSize(px).color(argb).align(...).padding(...).build()`，有 `intrinsicWidth/Height` 与 `draw(Canvas)`。
    //   · 字体资源随 aar 一起打进 APK（`assets/org/scilab/forge/jlatexmath/`）⇒ **离线可用**，不联网。
    //   · aar 自带 `JLatexMathInitProvider`（manifest 合并进来）⇒ 不用手工 init。
    implementation("ru.noties:jlatexmath-android:0.2.0")
}
