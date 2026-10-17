# 在真机上跑一场**够长的真对话**（≥7 轮，中间埋一个"意外"），用来验 `[R]` 到底出不出件。
#
# 为什么必须真机 + 真对话：② 误差标记要求"这一场里出现和前面不一样的东西"，
# 短对话天然没有（τ=5 的窗口还没填满）⇒ 自检永远验不到"出件"，只有真材料能。
#
# 手法（都实测过）：
#   · 送字：`am start -S --es prefill "…"`（`-S` = 先 force-stop ⇒ 永远只有一个 Activity 实例）
#   · 点发送：dump 找 `content-desc="发送"` 的节点中心（⛔ 不写死坐标：输入法开着/关着不一样）
#   · 判"这一轮答完了"：数 dump 里 `输出 N` 的个数（每轮助手气泡的 token 脚注），
#     比上一轮多 1 就是落地了 —— ⛔ 别睡固定秒数，那会在慢的一轮上把回复掐死。
param(
    [string]$Adb,
    [string]$Package = 'com.azurehalcyon.palimpsest'
)

$ErrorActionPreference = 'Stop'
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

# 第 4 句是**真正的意外**（辞职 + 瞒着家里），它该在 ② 里成为标记。
$话 = @(
    '今天脑子有点乱，随便聊聊。',
    '你说我为什么总在半夜写代码？',
    '其实我最近在想要不要换个工作。',
    '说起来，我上个月已经把工作辞了，一直瞒着家里没说。',
    '投了十几份简历，只有两个回音。',
    '有时候觉得自己挺失败的。',
    '算了不说这个了，你还记得我那只猫吗？'
)

function GetDump {
    & $adb shell uiautomator dump /sdcard/ui.xml *> $null
    $raw = & $adb shell cat /sdcard/ui.xml 2>$null
    return ($raw | Out-String)
}

function GetSendCenter($xml) {
    $pat = 'content-desc="发送"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
    $m = [regex]::Match($xml, $pat)
    if (-not $m.Success) {
        $pat = 'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*content-desc="发送"'
        $m = [regex]::Match($xml, $pat)
    }
    if (-not $m.Success) { return $null }
    $x = ([int]$m.Groups[1].Value + [int]$m.Groups[3].Value) / 2
    $y = ([int]$m.Groups[2].Value + [int]$m.Groups[4].Value) / 2
    return @([int]$x, [int]$y)
}

function GetOutCount($xml) {
    return ([regex]::Matches($xml, 'text="输出 \d+"')).Count
}

Write-Host "=== 预热 ==="
& $adb shell am start -S -n "$Package/.MainActivity" *> $null
Start-Sleep -Seconds 10
$xml = GetDump
$已答 = GetOutCount $xml
Write-Host ("现在的助手气泡：{0} 个" -f $已答)

$i = 0
foreach ($句 in $话) {
    $i++
    Write-Host ""
    Write-Host ("--- 第 {0} 轮：{1}" -f $i, $句) -ForegroundColor Cyan
    & $adb shell am start -S -n "$Package/.MainActivity" --es prefill $句 *> $null
    Start-Sleep -Seconds 10
    $xml = GetDump
    $短 = $句.Substring(0, [Math]::Min(8, $句.Length))
    if ($xml.IndexOf($短) -lt 0) {
        Write-Host "  ⚠️ dump 里没看到输入框中的这句话" -ForegroundColor Yellow
    }
    $c = GetSendCenter $xml
    if (-not $c) { throw "找不到发送按钮（dump 里没有 content-desc=发送）" }
    Write-Host ("  点发送 ({0},{1})" -f $c[0], $c[1])
    & $adb shell input tap $c[0] $c[1]
    $ok = $false
    for ($k = 0; $k -lt 34; $k++) {
        Start-Sleep -Seconds 3
        $xml = GetDump
        if ((GetOutCount $xml) -gt $已答) { $ok = $true; break }
    }
    if ($ok) {
        $已答 = GetOutCount $xml
        Write-Host ("  ok  答完了（累计 {0} 个气泡）" -f $已答) -ForegroundColor Green
    } else {
        Write-Host "  ⚠️ 等到 100 秒还没出现新的输出脚注" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== 这一场（读 pending.json）==="
$p = & $adb shell run-as $Package cat files/cache/pending.json 2>$null
$txt = ($p | Out-String).Trim()
if ($txt) {
    try {
        $j = $txt | ConvertFrom-Json
        Write-Host ("第 {0} 场 · {1} 轮 · 收场={2}" -f $j.k, ($j.turns | Measure-Object).Count, [bool]$j.ts_end)
    } catch {
        Write-Host "（pending.json 解析失败，原样长度 $($txt.Length)）" -ForegroundColor Yellow
    }
}
