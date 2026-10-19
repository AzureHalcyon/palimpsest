# archive/v1 —— 上一代（格 / 件 / 组 / 势）

冻结，保留可运行。当前链路在 [`../../memory_v2/`](../../memory_v2/README.md)；
这里的代码不再接新功能，只为了以后偶尔跑一下。

## Python

```powershell
cd archive/v1
python memory/tests/smoke.py
python memory/tests/p1.py ; python memory/tests/p2.py ; python memory/tests/p3.py
```

这些测试不需要网络与凭证，模型调用走脚本桩。

## Android

从归档目录直接构建：

```powershell
cd archive/v1/app
pwsh -File build.ps1 -Toolchain 'C:\Android' :app:assembleDebug
```

`-Toolchain` 指向含有 `jdk-17` / `Sdk` / `Python313` 的目录；换成自己的路径。

`app/app/src/main/python/memory` 是指向 `archive/v1/memory` 的本地 junction，
不随 git 走。全新克隆后重建：

```powershell
cd archive/v1
cmd /c mklink /J "app\app\src\main\python\memory" memory
```

## 说明

- 归档前的树由 `v0.2` tag（提交 `844415e`）保存；`v0.1` 是更早的发布标记。
  要回看历史用 `git show v0.2` / `git show v0.1`。
- 归档后的相对路径：Python 包是 `archive/v1/memory/`，App 是 `archive/v1/app/`。
- 设计文档仍在仓库根的 `docs/`（标着 v1 的那几份）。
