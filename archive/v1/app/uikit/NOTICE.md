# 第三方代码来源与许可

## `uikit/` —— vendored 自 niki914/zafiro

本模块的 Kotlin 源码**不是本项目原创**，来自：

- 项目：**zafiro** — <https://github.com/niki914/zafiro>
- 模块：`ui-kit/`
- 许可：**MIT License**，Copyright (c) 2026 niki914
- 取用时的上游提交：`HEAD`（浅克隆，2026-10-05）；上游最后推送 2026-10-03
- 许可全文见本目录下的 [`LICENSE-zafiro`](LICENSE-zafiro)

### 相对上游做的改动（仅此三处）

1. **删除了 1 个文件**：`base/ComposeMVIViewModel.kt`。
   它是上游整个 `ui-kit` 里**唯一**依赖上游自家 `:libs:logging` 模块的文件（`Logger.e` 一处），
   且模块内**无人引用**它。本工程不用 MVI，所以整份删掉，从而 `uikit/` 对上游仓库的依赖归零。
2. **构建插件改走本工程的版本目录**：上游写死 `com.android.library 9.1.1` 与
   `kotlin.plugin.compose 2.2.0`；这里改为跟随本工程的 AGP 8.13.2 / Kotlin 2.2.21。
3. **`material3` 不写版本**，交给本工程的 Compose BOM（= `1.4.0`）。
   上游钉的是 `1.5.0-alpha22`，而那一版的 AAR 硬要求 `minCompileSdk=37` +
   `minAndroidGradlePluginVersion=9.1.0`，会逼着上升 AGP/Gradle 并补装 `android-37` 平台。
   实测 `ui-kit` 真正用到的 19 个 material3 符号在 `1.4.0` 里**全部存在**。
   （上游依赖声明里另外两条未被使用的依赖 —— `com.google.android.material:material`
   与 `material-icons-extended` —— 本工程不引入：前者在上游源码里零 import，
   后者用到的图标全在 `material-icons-core` 集合内。）

除上述之外，**源码逐字未改**。

### 2026-10-05 追记：`:app` 的界面接进来时，`uikit/` 依旧一个字没改

三个屏（对话 / 池子 / 设置）落地时撞到的是**依赖可见性**，不是源码问题，记在这里免得下次重复踩：

1. **`:app` 必须自己声明 `kyant-backdrop` 与 `material-icons-*`。**
   本模块用 `implementation` 引入它们（不是 `api`），所以它们**不出现**在 `:app` 的
   编译类路径上。而 `:app` 的界面要直接用到 `LiquidButton` / `LiquidDialog` 的
   `Backdrop` 参数（这是必要参数，拿不到该类型连按钮都构造不出来）与 `Icons.*`。
   漏掉的报错是 `Unresolved reference 'Icons'` /
   `Cannot access class 'Backdrop'. Check your module classpath`。
   ⇒ 已在 `app/build.gradle.kts` 里补了这三条 `implementation`（版本目录里本来就有）。

2. **`SettingsSpecPageContent` 不能塞进另一个可滚动容器。**
   它内部自带 `SettingsListPageContent`（= `Modifier.verticalScroll`）。若把它放进
   设置页那一层 `verticalScroll` 里，会直接 FATAL：
   `Vertically scrollable component was measured with an infinity maximum height constraints`。
   ⇒ 「诊断」那一段改成自己拿 `SettingsGroupCard` + `SettingsListItem` 渲染同一张
   `SettingsPageSpec`（规格照写，只是渲染换成非滚动版）。

3. **`LocalNavigationEntry` 是 `staticCompositionLocalOf { error(...) }`。**
   在 `LiquidScreen` **外面**读它会抛 `IllegalStateException: No NavigationEntry provided`。
   `LiquidScreen` 要的 `titleBarScroll` 只能由调用方自己造一个 `NavigationEntry` provide 进去
   （本工程只有一个外壳、三个平级入口，没有导航栈，所以外壳就是那唯一的 entry）。

### 保留上游许可的要求

MIT 许可要求"在软件的所有副本中保留上述版权声明与许可声明"。
因此：

- 本文件与 [`LICENSE-zafiro`](LICENSE-zafiro) **必须随源码一起分发**，不要删除；
- 若将来更新 `uikit/` 的源码，请同步更新本文件里的"上游提交"一行。

---

## 其它第三方依赖（以 Gradle 依赖形式引入，非拷贝源码）

| 依赖 | 许可 | 用途 |
|---|---|---|
| `com.github.Kyant0:Capsule` | 见其仓库 | G2 连续曲率形状 |
| `io.github.kyant0:backdrop` | 见其仓库 | 背景模糊 / 透镜 / 高光 |
| `com.materialkolor:material-kolor` | 见其仓库 | Material You 动态取色 |
| `androidx.compose.*` | Apache-2.0 | Compose UI |
