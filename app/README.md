# app —— 手机上那个壳（Android 宿主）

> **宿主目前接的还是 `memory/`（v1）；`memory_v2/` 尚未接入。**
> v2 的实时入口先用 `memory_v2/serve.py`，App 接入是后面的事。

> **来历**：本工程从旧仓整体搬来（2026-10-17）；更早的细节不在本仓，下面的说明只讲现在。

**一句话**：`memory/`（Python 记忆层）的 Android 宿主。**业务逻辑一行都不重写** ——
构建时由 [Chaquopy](https://chaquo.com/chaquopy/) 把 `memory/` 原样嵌进 APK，
所以手机上跑的就是桌面上能跑的那份。Kotlin 只管界面、权限、前台服务，以及把 Python 的回调接到 Compose 上。

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
| 对话（流式正文 + CoT 折叠行 + token 账 + 撤回/重新生成 + 长按记忆条改删） | ✅ 能跑（真机验过） |
| 收场 → 出草案 → 审（能对着草案说话改）→ 按「完成」才落地 | ✅ 能跑 |
| 记忆页 | ✅ **件 / 组 / 势 三签** + 只读「查一查」（给一句话，看她此刻会亮什么、哪些件取了/没取）；组和势没有手改入口 |
| 底片 / 逐轮记录 / 判（带 `logprobs`、指纹、每处判的说法） | ✅ 已落盘；「旧场」页可以只读回看（系统提示 / 常驻 / 每轮 / 归档流程） |

## 目录

```
app/app/src/main/python/host_glue.py                 ← 宿主胶水（Kotlin ↔ Python 的唯一通道）
app/app/src/main/java/com/azurehalcyon/palimpsest/   ← Kotlin（UI / 服务 / 通知）
app/app/src/main/python/memory  →  <仓库根>/memory/   ← junction（不进 git）
app/uikit/                                           ← 设计系统（vendored 自 MIT 的 niki914/zafiro）
app/channelplugin/ · plugin-api/ · pluginhost/        ← 渠道插件（独立 APK）与它的契约
```

脚本：仓里只留 `build.ps1`（构建）。⚠️ 真机推进 / 备份 / 迁移那几个脚本是**本机工作脚本**，不随仓库公开。
