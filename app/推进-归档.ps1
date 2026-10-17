# 真对话跑完之后：把这一场**归档**（出草案）→ 按「完成」落地 → 报图里现在有几个件。
#
# 界面上这条路是三下：
#   ① 工具栏右上"更多"（`content-desc="更多"`）→ 菜单里点「归档」
#   ② 确认框 → 确认（`vm.finish()` 出草案，几十秒）
#   ③ 草案出来之后点「完成」才真正落地（④ 层1/层2 + 图版本 +1）
param(
    [string]$Package = 'com.azurehalcyon.palimpsest'
)

$ErrorActionPreference = 'Stop'
$env:TEMP = 'D:\Works\workplace\dsh-workplace\.android-build\adb-tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$adb = 'D:\Android\Sdk\platform-tools\adb.exe'

function GetDump {
    & $adb shell uiautomator dump /sdcard/ui.xml *> $null
    return ((& $adb shell cat /sdcard/ui.xml 2>$null) | Out-String)
}

function CenterOf($xml, $what, $attr) {
    $pat = $attr + '="' + [regex]::Escape($what) + '"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    $m = [regex]::Match($xml, $pat)
    if (-not $m.Success) {
        $pat = 'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*' + $attr + '="' + [regex]::Escape($what) + '"'
        $m = [regex]::Match($xml, $pat)
    }
    if (-not $m.Success) { return $null }
    return @([int](([int]$m.Groups[1].Value + [int]$m.Groups[3].Value) / 2),
             [int](([int]$m.Groups[2].Value + [int]$m.Groups[4].Value) / 2))
}

function AllTexts($xml) {
    return ([regex]::Matches($xml, 'text="([^"]+)"') | ForEach-Object { $_.Groups[1].Value }) |
        Where-Object { $_.Trim() } | Select-Object -Unique
}

Write-Host "=== ① 「更多」菜单 ==="
$xml = GetDump
$c = CenterOf $xml '更多' 'content-desc'
if (-not $c) { throw "找不到工具栏「更多」按钮" }
& $adb shell input tap $c[0] $c[1]
Start-Sleep -Seconds 2

Write-Host "=== ② 点「归档」 ==="
$xml = GetDump
$c = CenterOf $xml '归档' 'text'
if (-not $c) { throw ("菜单里没「归档」。当前文本：" + ((AllTexts $xml) -join ' / ')) }
& $adb shell input tap $c[0] $c[1]
Start-Sleep -Seconds 2

Write-Host "=== ③ 确认框 ==="
$xml = GetDump
Write-Host ("  屏幕上：" + ((AllTexts $xml) -join ' / '))
$tapped = $false
foreach ($w in @('确认', '收场', '归档', '确定', '好')) {
    $c = CenterOf $xml $w 'text'
    if ($c) {
        Write-Host ("  点「{0}」({1},{2})" -f $w, $c[0], $c[1])
        & $adb shell input tap $c[0] $c[1]
        $tapped = $true
        break
    }
}
if (-not $tapped) { throw "确认框没点下去" }

Write-Host "=== ④ 等草案（最多 180 秒）==="
$ok = $false
for ($k = 0; $k -lt 60; $k++) {
    Start-Sleep -Seconds 3
    $xml = GetDump
    if ($xml -match 'text="完成"' -or $xml -match 'content-desc="完成"') { $ok = $true; break }
}
if (-not $ok) {
    Write-Host ("  ⚠️ 没等到「完成」。屏幕上：" + ((AllTexts $xml) -join ' / ')) -ForegroundColor Yellow
    throw "草案没出来"
}
Write-Host "  ok  草案出来了" -ForegroundColor Green
Write-Host ("  草案里的字：" + ((AllTexts $xml | Where-Object { $_.Length -gt 10 }) -join ' ｜ '))

Write-Host "=== ⑤ 点「完成」 ==="
$c = CenterOf $xml '完成' 'text'
if (-not $c) { $c = CenterOf $xml '完成' 'content-desc' }
Write-Host ("  ({0},{1})" -f $c[0], $c[1])
& $adb shell input tap $c[0] $c[1]
Start-Sleep -Seconds 15
$xml = GetDump
Write-Host ("  落地后屏幕上：" + ((AllTexts $xml) -join ' / '))
