# 旧包 → 新包的**一次性数据迁移**
#
# 为什么要它：`P0` 把 Android 包名从 `com.azurehalcyon.companion` 改成了
#   `com.azurehalcyon.palimpsest`。**改了包名就是另一个 App** —— 新包装上去是一个全新的
#   `files/` 目录，看不到旧包的任何东西（跨包私有目录没权限，同签名也不行）。
#   而手机里攒着真东西：池子 330 条、几场底片转写、在途的那一场。
#
# 做法（每一步都踩过坑，别改）：
#   1) 旧包里把 **`files/cache`（单个成员）** 打成 tar，经 stdout 取回
#      —— cmd 的重定向字节安全；PowerShell 的 `>` 会把二进制当字符串、损坏文件；
#   2) `adb push` 到 `/data/local/tmp`（★ 这个目录 **app 读得到**；`/sdcard` 不一定）；
#   3) `run-as <新包>` 用 `cat` 把它搬进自己的 `files/`，再 `tar -xf` 解
#      （⛔ toybox 的 tar **吃不了管道**：`tar -xf -` 报 `EOF: Illegal seek`）；
#   4) 凭证 / 设置**单文件**用 base64 走命令行（几 KB，够；顺便逐字节可核）；
#   5) 逐字节核 md5。
#
# ⚠️ 三个纯 Windows / Android 的坑（都踩过）：
#   · `cmd /c "… sh -c 'cat > files/x'"` —— cmd **不认单引号**，会把里面的 `>` 当 PC 侧重定向
#     ⇒ 报「The system cannot find the path specified」。设备侧的 `>` 一律放在 **PowerShell 字符串**里发。
#   · 用 `dd` 从 stdin 收 2 MB 会**静默截断**（实测只收到 1291 B）⇒ 大文件只能走 push。
#   · **多成员 tar 会坏**（`files/cache files/credentials.yaml` 一起打，设备上 `tar -xf` 报
#     `bad header`）⇒ 缓存走 tar，配置文件走 base64，分开传。
#
# 用法：
#   pwsh -File 迁移.ps1                      # 默认旧包 → 新包
#   pwsh -File 迁移.ps1 -Device 14d824f2
#   pwsh -File 迁移.ps1 -WhatIf              # 只看会做什么
#
# ⛔ 只对 **debug 包**有效（`run-as` 只对 debuggable 应用开放）。
# ⛔ 不会删旧包的任何东西；旧包也不卸载（留着当对照）。确认无误后你自己决定要不要卸。
param(
    [string]$Adb,
    [string]$Device = '',
    [string]$Old = 'com.azurehalcyon.companion',
    [string]$New = 'com.azurehalcyon.palimpsest',
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

# adb 要往 %TEMP% 写 adb.log；沙箱默认的 %TEMP% 在工作区外会 Permission denied。
# ★ 2026-10-17：这里原来写死了这台机器的路径（D:\Android\Sdk\... 之类），换电脑就崩。
#   ⇒ 改成按顺序发现（⛔ 在仓库里写任何盘符路径）。
#   adb：-Adb 参数 → 环境变量 PALIMPSEST_ADB → $env:ANDROID_HOME\platform-tools\adb.exe → PATH 上的 adb
if (-not $Adb) { $Adb = $env:PALIMPSEST_ADB }
if (-not $Adb -and $env:ANDROID_HOME) { $Adb = Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe' }
if (-not $Adb) { $c = Get-Command adb -ErrorAction SilentlyContinue; if ($c) { $Adb = $c.Source } }
if (-not $Adb -or -not (Test-Path -LiteralPath $Adb)) {
    throw "没找到 adb。用 -Adb <路径>，或设 ANDROID_HOME（其下有 platform-tools\adb.exe），或把 adb 放进 PATH。"
}
$adb = $Adb
# ★ TEMP 要挪出工作区（工作区继承的 ACL 会让 adb 的临时文件写不进去，2026-10-04 实测）；
#   但 ⛔ 写死某个盘 ⇒ 用系统临时区下的固定子目录（跟着用户走，不跟着盘符走）。
$env:TEMP = Join-Path $env:LOCALAPPDATA 'Temp\palimpsest-adb'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
if (-not (Test-Path $adb)) { throw "找不到 adb：$adb" }

if (-not $Device) {
    $line = (& $adb devices) | Where-Object { $_ -match '^\S+\s+device$' } | Select-Object -First 1
    if (-not $line) { throw "没有已授权的设备（先在手机上点「允许 USB 调试」，或插好数据线）" }
    $Device = ($line -split '\s+')[0]
}
Write-Host "设备 = $Device"
Write-Host "旧包 = $Old  →  新包 = $New"

$tmp = Join-Path $env:TEMP 'migrate'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$tar = Join-Path $tmp 'cache.tar'
$devTar = '/data/local/tmp/palimpsest-migrate.tar'
$配置 = @('credentials.yaml', 'settings.yaml')

# ---- 0) 两个包都在不在
$pkgs = (& $adb -s $Device shell pm list packages) -join "`n"
if ($pkgs -notmatch [regex]::Escape("package:$Old")) { throw "旧包没装：$Old" }
if ($pkgs -notmatch [regex]::Escape("package:$New")) { throw "新包没装：$New（先 build.ps1 + adb install）" }

# 旧包里哪些配置文件真的存在（旧包可能没有 settings.yaml）
$旧有 = @(& $adb -s $Device shell "run-as $Old sh -c 'ls files'") -split "`r?`n" |
    ForEach-Object { $_.Trim() }
$要搬的配置 = $配置 | Where-Object { $旧有 -contains $_ }
Write-Host ("配置：旧包里有的 = {0}" -f (($要搬的配置 -join ', ') -replace '^$', '（一个都没有）'))

if ($WhatIf) {
    Write-Host "（-WhatIf）会：tar 取回 files/cache → push $devTar → 解进新包 → 配置走 base64 → 核 md5"
    return
}

# ---- 1) 取回 cache（**单个成员**，多成员 tar 会坏）
Write-Host "1) 从旧包取回 files/cache ..."
cmd /c "`"$adb`" -s $Device exec-out run-as $Old tar -cf - files/cache > `"$tar`" 2>nul"
if (-not (Test-Path $tar) -or (Get-Item $tar).Length -eq 0) {
    throw "取回失败 —— 确认旧包装的是 debug 包（run-as 只对 debuggable 应用有效）"
}
Write-Host ("   tar = {0:N0} B" -f (Get-Item $tar).Length)
# ★ 先在桌面验一下这个 tar 是好的（坏包别往手机上带）
$bad = (& tar -tf $tar 2>&1 | Where-Object { $_ -match 'bad|error|Error' })
if ($bad) { throw ("取回的 tar 坏了：" + ($bad -join ' ')) }
Write-Host ("   成员数 = {0}" -f ((& tar -tf $tar | Measure-Object).Count))

# ---- 2) push 到 /data/local/tmp（app 读得到；/sdcard 不一定）
Write-Host "2) push 到设备 ..."
& $adb -s $Device push $tar $devTar | Out-Null

# ---- 3) 搬进新包并解（设备侧的 '>' 走 PowerShell 字符串，不经 cmd）
Write-Host "3) 解进新包 ..."
& $adb -s $Device shell "run-as $New sh -c 'cat $devTar > files/migrate.tar'"
$sz = ((& $adb -s $Device shell "run-as $New sh -c 'wc -c < files/migrate.tar'") -join '').Trim()
Write-Host ("   设备上收到 {0} B（本地 {1} B）" -f $sz, (Get-Item $tar).Length)
& $adb -s $Device shell "run-as $New sh -c 'tar -xf files/migrate.tar; rm -f files/migrate.tar'"
& $adb -s $Device shell "rm -f $devTar"

# ---- 4) 配置文件：base64 走命令行（几 KB，够；逐字节可核）
foreach ($f in $要搬的配置) {
    $local = Join-Path $tmp "cfg_$f"
    cmd /c "`"$adb`" -s $Device exec-out run-as $Old cat files/$f > `"$local`" 2>nul"
    if (-not (Test-Path $local) -or (Get-Item $local).Length -eq 0) { throw "$f 取不回来" }
    $b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($local))
    & $adb -s $Device shell "run-as $New sh -c 'echo $b64 | base64 -d > files/$f'"
    $dev = (((& $adb -s $Device shell "run-as $New sh -c 'md5sum files/$f'") -join '') -split '\s+')[0]
    $loc = (Get-FileHash $local -Algorithm MD5).Hash.ToLower()
    if ($dev -ne $loc) { throw "$f 的 md5 不一致（dev=$dev local=$loc）" }
    Write-Host "   $f ✓ 字节一致"
}

# ---- 5) 逐字节核对
Write-Host "4) 核对 md5 ..."
$list = 'files/cache/pool.npz files/cache/state.json files/cache/pool_texts.jsonl files/cache/mobile_transcripts.jsonl'
$l = ((& $adb -s $Device shell "run-as $Old sh -c 'md5sum $list 2>/dev/null'") -join "`n").Trim()
$r = ((& $adb -s $Device shell "run-as $New sh -c 'md5sum $list 2>/dev/null'") -join "`n").Trim()
if ($l -ne $r) {
    Write-Host "旧：`n$l" ; Write-Host "新：`n$r"
    throw "md5 不一致 —— 别往下走"
}
Write-Host "   ✓ 逐字节一致："
Write-Host ($r -replace '(?m)^', '     ')

Write-Host ""
Write-Host "完成。手机上新包已带着旧数据。" -ForegroundColor Green
Write-Host "⚠️ 旧包（$Old）**没动也没卸** —— 确认没问题后你自己决定要不要卸（卸了会删旧包的数据）。"
Write-Host "⚠️ 迁过来的是**旧形状**的数据（池子/四档），不是 palimpsest 的底片；后者从下一场对话开始攒。"
