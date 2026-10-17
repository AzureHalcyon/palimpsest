# 底片仓取回通道：把手机上的**数据**拉回桌面
#
# 为什么需要它：
#   原则是「手机是唯一入口，桌面是实验台 + 底片仓」。
#   于是**数据只活在手机里** —— 手机丢了 / 刷了 / 坏了，攒的全没了。
#   这个脚本就是那条"定期把底片取回来"的通道。
#
# ★ 只备**数据**，不备运行时：
#   `files/chaquopy/` 是 Chaquopy 每次安装重新解出来的 Python 运行时
#   （stdlib + numpy + 一堆 .so），实测把它一起打包是 **29.5 MB**，
#   而我们真正要保的只有几 KB —— 池子、状态、底片转写。
#   备它会掩盖真正的变化，也让人懒得备。
#
# 怎么做的（都实测过）：
#   · 应用私有目录（/data/data/<包名>/files）shell 读不了，但 debug 包可以 `run-as`；
#   · 用 `run-as <包名> tar -cf - files/cache` 把数据打到 stdout，
#     再由 `cmd /c "... > 本地.tar"` 落盘 —— **cmd 的重定向是字节安全的**。
#     ⚠️ PowerShell 的 `>` 会把二进制当字符串处理、损坏文件；实测取回的
#     `.npz`（当时还是池子文件）与原始文件 SHA256 一致（49,593 字节）就是走 cmd 这条。
#   · 不用 `adb pull /data/local/tmp/xxx`：那样 tar 包归应用 uid 所有，
#     adb shell 反而读不了它。
#
# ⚠️ 输出里**包含 credentials.yaml**（真实 API 密钥）。别把这个目录发给任何人。
#
# 用法：
#   pwsh -File backup.ps1
#   pwsh -File backup.ps1 -OutDir 'E:\companion-底片仓'

param(
    [string]$OutDir = 'D:\Works\workplace\dsh-workplace\companion-backups',
    [string]$Package = 'com.azurehalcyon.palimpsest'
)

$ErrorActionPreference = 'Stop'

# ★ 必须先设 TEMP/TMP：adb 要往 %TEMP% 写 adb.log，而沙箱给的 %TEMP% 在工作区外，
#   会报 "cannot open ...adb.log: Permission denied"。
#   （Gradle 恰好相反 —— 它的 TEMP 要放工作区外，见工具详情.md 第 7/8 条。）
$env:TEMP = 'D:\Works\workplace\dsh-workplace\.android-build\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

$adb = 'D:\Android\Sdk\platform-tools\adb.exe'
if (-not (Test-Path $adb)) { throw "找不到 adb：$adb" }

$dev = (& $adb devices) -join "`n"
if ($dev -notmatch 'device') {
    Write-Host "当前设备列表：`n$dev" -ForegroundColor Yellow
    throw "没有已授权的设备。先在手机上点「允许 USB 调试」。"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$ts = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$dest = Join-Path $OutDir "companion-$ts"
New-Item -ItemType Directory -Force -Path (Join-Path $dest 'files\cache') | Out-Null

Write-Host "从 $Package 取回数据 ..."

# ---- 1) 池子 + 状态 + 底片转写（整目录）----
$tar = Join-Path $dest 'cache.tar'
cmd /c "`"$adb`" exec-out run-as $Package tar -cf - files/cache > `"$tar`" 2>nul"
if (-not (Test-Path $tar) -or (Get-Item $tar).Length -eq 0) {
    throw "取回失败。确认装的是 **debug** 包（run-as 只对 debuggable 应用有效）。"
}
& tar -xf $tar -C $dest
Remove-Item $tar -Force

# ---- 2) 凭证与设置（单文件，可能不存在）----
foreach ($rel in @('files/credentials.yaml', 'files/settings.yaml')) {
    $probeOut = (& $adb shell run-as $Package ls $rel 2>&1) -join ''
    if ($probeOut -notmatch 'No such file') {
        $local = Join-Path $dest ($rel -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path $local) | Out-Null
        cmd /c "`"$adb`" exec-out run-as $Package cat $rel > `"$local`" 2>nul"
    }
}

# ---- 3) 报告 ----
$files = Get-ChildItem $dest -Recurse -File
$total = ($files | Measure-Object Length -Sum).Sum
Write-Host ""
Write-Host ("内容（共 {0:N0} 字节）：" -f $total) -ForegroundColor Cyan
foreach ($f in $files) { "{0,10:N0}  {1}" -f $f.Length, $f.FullName.Substring($dest.Length + 1) }

# 底片单独报数 —— 那是"永远不改写"的那一份，最该确认它好好的
# ★ 2026-10-17 换成新线的形状：`底片/<场id>.json.gz`（整场存档）+ `逐轮记录/*.jsonl`（一轮一行）。
#	⛔ 别再找 `*transcripts*.jsonl` —— 那是旧线的转写文件，新线已经不写它了
#	  （找不着的表现是"明明收过场，报告却说还没收过"）。
$底片 = $files | Where-Object { $_.FullName -match '\\底片\\' -and $_.Name -like '*.json.gz' }
$逐轮 = $files | Where-Object { $_.FullName -match '\\逐轮记录\\' -and $_.Name -like '*.jsonl' }
Write-Host ("底片：{0} 场 · 逐轮记录：{1} 个文件" -f $底片.Count, $逐轮.Count) -ForegroundColor Green
foreach ($f in $底片) {
    Write-Host ("  {0,10:N0}  {1}" -f $f.Length, $f.Name)
}
if (-not $底片) { Write-Host "（这次没有底片 —— 还没在手机上收过场）" -ForegroundColor Yellow }

# 在途的场次也要报：它在就是"上一场没归档完"
$pend = $files | Where-Object { $_.Name -eq 'pending.json' }
if ($pend) {
    Write-Host "⚠️ 有在途的场次（pending.json）—— 下次启动 App 会补归档。" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "⚠️ 含 credentials.yaml（真实密钥），别外发。" -ForegroundColor Yellow
Write-Host "备份目录：$dest"
