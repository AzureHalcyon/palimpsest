# 把手机上的**旧数据**取回桌面存档（只取数据，不取 Chaquopy 运行时）。
#
# 为什么单独写一个而不是直接用 app\backup.ps1：
#   · 这份是**一次性归档**（旧线要退役了，取一次、收好、以后只读）；
#   · 要同时取 **两个包**：旧 app `com.azurehalcyon.companion`（真正的旧线数据）
#     与新 app `com.azurehalcyon.palimpsest`（其中还躺着迁移时抄过来的旧线文件）；
#   · 取完要出一份 **清单 + SHA256**，否则"存好了"只是一句话，不是证据。
#
# ⚠️ 输出里含 `credentials.yaml`（真实 API 密钥）⇒ 这个目录**不外发、不进仓库**。
param(
    [string]$Adb,
    [string]$OutRoot = (Join-Path $env:USERPROFILE 'palimpsest-备份')
)

$ErrorActionPreference = 'Stop'

# ★ adb 要往 %TEMP% 写日志，而沙箱给的 %TEMP% 在工作区外 ⇒ 必须挪进工作区
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
$dev = (& $adb devices) -join "`n"
if ($dev -notmatch '\tdevice') { throw "没有已授权的设备。`n$dev" }

$ts = Get-Date -Format 'yyyy-MM-dd'
$dest = Join-Path $OutRoot ("旧数据存档-$ts")
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Write-Host "存档目录：$dest"

# 两个包：旧线自己的数据 + 新包里躺着的旧线遗物
$pkgs = @(
    @{ name = 'com.azurehalcyon.companion';          label = '旧app' },
    @{ name = 'com.azurehalcyon.companion.probe';    label = '旧探针app' },
    @{ name = 'com.azurehalcyon.palimpsest';         label = '新app' }
)

foreach ($p in $pkgs) {
    $sub = Join-Path $dest $p.label
    New-Item -ItemType Directory -Force -Path $sub | Out-Null
    Write-Host ("--- {0} ({1}) ---" -f $p.label, $p.name)

    # ★ 先问一句"这个包里有哪些路径"，只打包**存在的** ——
    #   否则 `tar` 对不存在的路径会报错并写出一份半截包（探针包就没 `files/cache`）。
    $listing = (& $adb shell run-as $($p.name) ls files 2>&1) -join "`n"
    if ($listing -match 'No such file|not debuggable|Package') {
        Write-Host "  ⚠️ 读不到（不是 debug 包 / 包不存在）—— 跳过" -ForegroundColor Yellow
        continue
    }
    $want = @('files/cache', 'files/sensors', 'files/store_selftest',
              'files/credentials.yaml', 'files/settings.yaml', 'files/l0-report.txt')
    $take = @()
    foreach ($w in $want) {
        $leaf = $w -replace '^files/', ''
        if ($listing -match ("(?m)(^|\s)" + [regex]::Escape($leaf) + "(\s|$)")) { $take += $w }
    }
    if (-not $take) { Write-Host "  ⚠️ 没有可取的路径 —— 跳过" -ForegroundColor Yellow; continue }
    Write-Host ("  取：{0}" -f ($take -join ' '))

    # $LastExitCode 在每次 adb 调用后都会变，所以存完 tar 立刻判
    $tar = Join-Path $sub 'files.tar'
    $takeArg = ($take -join ' ')
    # ⚠️ 用 cmd 的重定向落二进制：PowerShell 的 `>` 会把它当字符串搞坏（实测踩过）
    cmd /c "`"$adb`" exec-out run-as $($p.name) tar -cf - $takeArg > `"$tar`" 2>nul"
    if (-not (Test-Path $tar) -or (Get-Item $tar).Length -eq 0) {
        Write-Host "  ⚠️ 包是空的 —— 跳过" -ForegroundColor Yellow
        Remove-Item $tar -Force -ErrorAction SilentlyContinue
        continue
    }
    & tar -tf $tar *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ⚠️ 包不完整（tar 列不出来）—— 跳过" -ForegroundColor Yellow
        Remove-Item $tar -Force -ErrorAction SilentlyContinue
        continue
    }
    # ⚠️ **解包必须用 Python 的 tarfile，不能用 Windows 的 bsdtar**：
    #   实测旧数据里有中文目录名（`底片/` `逐轮记录/` `说/` `判/` `图.json.gz`），
    #   bsdtar 按本地代码页解读 UTF-8 名字 ⇒ 目录被解成 `搴曠墖` 这种乱码，
    #   而 `说/` `判/` `图.json.gz` 干脆报 `Invalid empty pathname` 丢掉。
    #   Python 的 tarfile 按 UTF-8 处理，一次就对（文件名是数据的一部分，不是小事）。
    & python -c "import sys,tarfile; tarfile.open(sys.argv[1],'r:').extractall(sys.argv[2])" $tar $sub
    if ($LASTEXITCODE -ne 0) { throw "$($p.label) 解包失败（python tarfile）" }
    Remove-Item $tar -Force
    $n = (Get-ChildItem (Join-Path $sub 'files') -Recurse -File | Measure-Object).Count
    Write-Host ("  ok  取回 {0} 个文件" -f $n) -ForegroundColor Green
}

# ---- 清单 + SHA256（"存好了"的证据）
$manifest = Join-Path $dest '清单.txt'
$rows = Get-ChildItem $dest -Recurse -File |
        Where-Object { $_.Name -ne '清单.txt' -and $_.Name -ne 'SHA256.txt' }
$lines = @(
    "# 旧数据存档（$ts）",
    "# 来源：小米 14 (14d824f2) · adb exec-out run-as tar",
    "# 含 credentials.yaml（真实密钥）⇒ 不外发、不进仓库",
    "# 文件数 = $($rows.Count)",
    ""
)
foreach ($f in $rows) {
    $lines += ("{0,10:N0}  {1}" -f $f.Length, $f.FullName.Substring($dest.Length + 1))
}
$lines | Set-Content -Path $manifest -Encoding utf8

$sha = Join-Path $dest 'SHA256.txt'
foreach ($f in $rows) {
    $h = (Get-FileHash $f.FullName -Algorithm SHA256).Hash.ToLower()
    "$h  $($f.FullName.Substring($dest.Length + 1))" |
        Add-Content -Path $sha -Encoding utf8
}

Write-Host ""
Write-Host ("清单：{0}（{1} 个文件，共 {2:N0} 字节）" -f $manifest, $rows.Count,
            ($rows | Measure-Object Length -Sum).Sum) -ForegroundColor Cyan
Write-Host ("校验：{0}" -f $sha) -ForegroundColor Cyan

# 底片/转写单独报一句 —— 那是"永远不改写"的那一份，最该确认它好好的
foreach ($k in @('mobile_transcripts.jsonl', 'pool_texts.jsonl')) {
    $hit = $rows | Where-Object { $_.Name -eq $k }
    foreach ($h in $hit) {
        $n = 0
        try { $n = (Get-Content $h.FullName -Encoding utf8 | Where-Object { $_.Trim() }).Count } catch {}
        Write-Host ("  {0,-26} {1} 行  ({2:N0} 字节)" -f $k, $n, $h.Length)
    }
}
