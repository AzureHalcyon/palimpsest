# app —— Android 客户端

Compose 写的壳；Python 侧用 Chaquopy 把 `memory_v2` 打进 APK，桥在
`app/src/main/python/host_glue_v2.py`。**现在接的是旧入口 `memory_v2 chat`，不是
[`../loop/`](../loop/README.md)** —— 循环那条路当前以网页控制台为主（见 [`../loop/README.md`](../loop/README.md)）。

| 模块 | 是什么 |
|---|---|
| `app/` | 本体（`com.azurehalcyon.palimpsest`；debug 包带 `.dev`，可与正式包并存） |
| `uikit/` | 设计系统（vendored 自 MIT 的 niki914/zafiro，见 `uikit/NOTICE.md`） |
| `plugin-api/` | 插件契约（AIDL ＋ 键名常量 ＋ 客户端），本体与插件共用这一份 |
| `pluginhost/` | 宿主侧示例 APK：只用来验证“发现 → 绑定 → 权限 → AIDL → JSON”真的通 |
| `channelplugin/` | 渠道插件 APK：手机传感器与日历两条信源（各自权限、同一个包） |

## 构建

```powershell
pwsh -File app/build.ps1 :app:assembleDebug
```

- 工具链：JDK 17 ＋ Android SDK 36（`compileSdk 36`、`minSdk 26`）；默认找 `D:\Android`，
  在别处就用 `-Toolchain <目录>` 或环境变量 `PALIMPSEST_TOOLCHAIN`；
- 构建机 Python 3.13（Chaquopy 17 要求**同主次版本**）；脚本会自动发现并传给 Gradle；
- Gradle / pip / TEMP 都被 `build.ps1` 挪到仓库旁边的 `.android-build/`，不写 C 盘。

产物：`app/app/build/outputs/apk/debug/app-debug.apk`（debug 签名，自用侧载）。

## 和数据的关系

- 记忆在手机上的私有目录里（由 `tools/backup-phone.ps1` 拉回本机）；
- `memory_v2` 的读取、编写、归档仍走 Python 侧那套代码，和电脑上是同一份；
- 服务端（`loop/`）与手机客户端**不共享存储**，两边的记忆各自独立；`snapshot` / `restore`
  是按目录做的，不自动合图，真要合并得手工搬。
