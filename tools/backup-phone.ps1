# 把手机 App 私有目录里的归档备份（.psnap）拉到本机。
# 每次归档提交后，App 会自己在 files/backups/ 留一份；这个脚本只负责取回。
param(
    [string]$Serial = '14d824f2',
    [string]$Package = 'com.azurehalcyon.palimpsest.dev',
    [string]$Out
)

$ErrorActionPreference = 'Stop'

$adb = 'D:\Android\Sdk\platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $adb)) { $adb = 'adb' }

$repo = Split-Path $PSScriptRoot -Parent
if (-not $Out) {
    $Out = Join-Path $repo '.local\backups'
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dest = Join-Path $Out ("phone-" + $stamp)
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$tmp = Join-Path $repo '.local\tmp'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:TEMP = $tmp
$env:TMP = $tmp

$names = & $adb -s $Serial shell run-as $Package ls files/backups 2>$null
$snapshots = @($names | ForEach-Object { $_.Trim() } | Where-Object { $_ -like '*.psnap' })
if (-not $snapshots) {
    Write-Host "手机上还没有归档备份：files/backups/ 为空。先归档一场。"
    exit 0
}

foreach ($name in $snapshots) {
    $target = Join-Path $dest $name
    cmd.exe /c "`"$adb`" -s $Serial exec-out run-as $Package cat `"files/backups/$name`" > `"$target`""
    if (-not (Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -eq 0) {
        throw "拉取失败：$name"
    }
}

Write-Host "已备份 $($snapshots.Count) 份到 $dest"
Get-ChildItem -LiteralPath $dest | Select-Object Name, Length, LastWriteTime
