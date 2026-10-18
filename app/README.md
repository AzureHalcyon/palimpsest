# app —— 手机上那个壳（Android 宿主）

**一句话**：`memory/`（Python 记忆层）的 Android 宿主。**业务逻辑一行都不重写** ——
构建时由 [Chaquopy](https://chaquo.com/chaquopy/) 把 `memory/` 原样嵌进 APK，
所以手机上跑的就是桌面上能跑的那份。Kotlin 只管界面、权限、前台服务，以及把 Python 的回调接到 Compose 上。

> **来历**：本工程从旧仓 `companion-ai/companion-android` **整体搬来**（2026-10-17）：
> 包名 `com.azurehalcyon.companion` → `com.azurehalcyon.palimpsest`，Python 包
> `companion` → `memory`。
> ★★ **2026-10-18 起 `memory/` 已是新链路**（归档五步 · 件与组 · 势），宿主的**界面**还留着旧池子那几页
> （未改）⇒ **别拿 App 的界面判断记忆层的状态**，状态看 [`../memory/README.md`](../memory/README.md)。
> 旧仓已冻结只读、当对照基线；搬来之前的全部细节与真机证据留在
> [`README-从旧仓搬来-历史.md`](README-从旧仓搬来-历史.md)。

## 构建

```powershell
pwsh -File build.ps1 :app:assembleDebug     # 需要代理（Chaquopy 装 numpy/PyYAML 要 pip）
pwsh -File build.ps1 :app:tasks             # 看有哪些 task
```

脚本替你把四件容易踩的事做了：**工具链自动发现**（⛔ 写死在脚本里：`-Toolchain` 参数 →
`$env:PALIMPSEST_TOOLCHAIN` → 从 `JAVA_HOME`/`ANDROID_HOME` 反推共同父目录）· Gradle 缓存与
TEMP 挪到工具链目录下（不撞工作区继承的 ACL）· **构建用 Python 同样自动发现**后传给 gradle
（`-PbuildPython`；`app/build.gradle.kts` 里⛔ 写路径）· pip 代理**每次现探**（`chaquo.com` 直连极慢）。

```powershell
# 工具链不在上面那些地方时，显式给：
pwsh -File build.ps1 -Toolchain 'X:\Android' :app:assembleDebug
```

## ★ Python 源码只有一份（目录 junction）

```
app/app/src/main/python/memory  ──junction──▶  <仓库根>/memory/
```

磁盘上**只有一份** Python 源码，改 `memory/` 立刻反映到手机上。
`.gitignore` 排掉了 junction 本身（否则 git 会跟进去、把源码复制第二份进仓 —— 正是要避免的"两份实现"）。
⚠️ junction 是 **Windows 专有**（`mklink /J`）；换平台要改成符号链接或拷贝。

## 它现在有什么

| 面 | 状态 |
|---|---|
| 对话（流式正文 + CoT 折叠行 + token 账 + 撤回/重新生成 + 长按记忆条改删） | ✅ 能跑（真机验过，见历史 README） |
| 收场 → 出草案 → 审（能对着草案说话改）→ 按「完成」才落地 | ✅ 能跑 |
| 记忆页（空间图 + 四档池子） | ⚠️ **旧形状**：`palimpsest` 里没有"四档池子"。P0 起先隐藏；P2 换成"件与组"的浏览（画布与手感留、36 轴那套退场） |
| 底片 / 逐轮记录（带 `logprobs` 与指纹） | ⛔ **还没有** —— `P1` 要加的第一件东西 |

## 目录

```
app/app/src/main/python/host_glue.py                 ← 宿主胶水（Kotlin ↔ Python 的唯一通道）
app/app/src/main/java/com/azurehalcyon/palimpsest/   ← Kotlin（UI / 服务 / 通知）
app/app/src/main/python/memory  →  <仓库根>/memory/   ← junction（不进 git）
app/uikit/                                           ← 设计系统（vendored 自 MIT 的 niki914/zafiro）
app/channelplugin/ · plugin-api/ · pluginhost/        ← 渠道插件（独立 APK）与它的契约
```

脚本：`backup.ps1`（手机数据取回桌面）· `push_pool.ps1`（往手机推池子，**旧形状**，
将来被数据集导出取代）· `迁移.ps1` **还没有**，`P1` 里加（旧包 `files/cache` → 新包，debug + `run-as`）。
