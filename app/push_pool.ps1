# 底片仓上行通道：把桌面上建好的**池子**推到手机里
#
# 为什么需要它：
#   原则是「手机是唯一入口，桌面是实验台 + 底片仓」，而**底色是在桌面上改的**。
#   ⇒ 每次改完底色都要重建池子、再推上去。`backup.ps1` 是反方向（取回数据），
#     这一份是正方向（送出池子）。
#
# ★ 只推**池子 + 轴框架**这几个文件：`pool.npz` · `pool_texts.jsonl` ·
#   **`axes.npz` / `axes.jsonl`（36 根轴的锚句向量）**。
#   ⛔ **绝不推 `state.json`** —— 它装着手机上攒下的东西：`sessions`（聊过几场）·
#      `temp[]`（临时池记忆）· `applied_sids`（幂等账本，防重复落地）· `clock`。
#      把它覆盖掉 = **把手机上真实的记忆清空**。
#      （桌面上那个 `state.json` 是 `prep_pool` 刚建的空白状态，sessions=0、temp=[]。）
#   ⛔ 也**绝不动** `mobile_transcripts.jsonl`（底片，永远只追加）。
#
# ★ `axes.npz` 为什么必须一起推：定轴重心图要用它算"每条落在哪两根轴之间"。
#   锚向量是**建池时预编码**好的（`prep_pool.py --axes`），推上去之后**开图不调编码器**。
#   没推它 ⇒ 手机上那页会说"缓存里没有轴框架"，然后退回旧画法 —— 不会崩，但看不到轴。
#
# ⚠️ 编码器指纹必须一致：`pool.npz` 里记着 `(模型名, 维度)`，`state.json` 里也记着一份。
#   不匹配 ⇒ `Engine.load` **拒绝加载**（这是设计，不是 bug）。
#   现在两边都是 `qwen3.7-text-embedding@1024`；**换编码器就要连 state.json 一起重建**
#   —— 那等于清空手机记忆，所以别随便换。
#
# 怎么做的（踩过的点都在这儿）：
#   · `adb push` 到 `/data/local/tmp/`（那里 shell 可写）；
#   · 再由 `run-as <包名> cp ... files/cache/` 搬进应用私有目录 ——
#     `/data/data/<包名>/files` shell 直接读不了，debug 包才有 `run-as`；
#   · 推完**两边比 SHA256**，并用 `wc -l` 数一下条数；
#   · 收尾清掉 `/data/local/tmp` 里的中转文件。
#
# ★ 必须先设 TEMP/TMP：adb 要往 %TEMP% 写 adb.log，而沙箱给的 %TEMP% 在工作区外，
#   会报 "cannot open ...adb.log: Permission denied"。（与 backup.ps1 同一条坑。）
#
# 用法：
#   pwsh -File push_pool.ps1
#   pwsh -File push_pool.ps1 -Cache 'D:\Works\workplace\dsh-workplace\companion-data\.cache'

param(
    [string]$Cache = 'D:\Works\workplace\dsh-workplace\companion-data\.cache',
    [string]$Package = 'com.azurehalcyon.palimpsest'
)

$ErrorActionPreference = 'Stop'

$env:TEMP = 'D:\Works\workplace\dsh-workplace\.android-build\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null

$adb = 'D:\Android\Sdk\platform-tools\adb.exe'
if (-not (Test-Path $adb)) { throw "找不到 adb：$adb" }

$npz = Join-Path $Cache 'pool.npz'
$txt = Join-Path $Cache 'pool_texts.jsonl'
foreach ($f in @($npz, $txt)) {
    if (-not (Test-Path $f)) { throw "池子文件不存在：$f`n先跑 tools/prep_pool.py。" }
}
# ★ 轴框架：有就推；没有只**警告**，不中断（旧缓存本来就没有这一段）。
$axesNpz = Join-Path $Cache 'axes.npz'
$axesJsonl = Join-Path $Cache 'axes.jsonl'
if (-not (Test-Path $axesNpz)) {
    Write-Host "⚠️ 没有 $axesNpz —— 推完手机上仍走旧画法。" -ForegroundColor Yellow
    Write-Host "   想出新图：prep_pool.py 加 --axes <轴锚.jsonl> 重建缓存，再推一次。" -ForegroundColor Yellow
}
$files = @($npz, $txt)
foreach ($f in @($axesNpz, $axesJsonl)) { if (Test-Path $f) { $files += $f } }

$dev = (& $adb devices) -join "`n"
if ($dev -notmatch 'device') {
    Write-Host "当前设备列表：`n$dev" -ForegroundColor Yellow
    throw "没有已授权的设备。先在手机上点「允许 USB 调试」。"
}

Write-Host "本地要推的：" -ForegroundColor Cyan
foreach ($f in $files) { Write-Host ("  {0,-18}{1,10:N0} B" -f (Split-Path $f -Leaf), (Get-Item $f).Length) }

# ---- 1) 中转 ----
foreach ($f in $files) { & $adb push $f ("/data/local/tmp/" + (Split-Path $f -Leaf)) | Out-Null }

# ---- 2) 搬进应用私有目录（只这几个文件）----
foreach ($f in $files) {
    $leaf = Split-Path $f -Leaf
    & $adb shell run-as $Package cp "/data/local/tmp/$leaf" "files/cache/$leaf"
}

# ---- 3) 收尾 ----
foreach ($f in $files) { & $adb shell rm -f ("/data/local/tmp/" + (Split-Path $f -Leaf)) | Out-Null }

# ---- 4) 两边比哈希 + 数条数 ----
Write-Host "`n核对：" -ForegroundColor Cyan
$ok = $true
foreach ($f in $files) {
    $leaf = Split-Path $f -Leaf
    $local = (Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower()
    $remote = ((& $adb shell run-as $Package sha256sum "files/cache/$leaf") -split '\s+')[0].Trim().ToLower()
    $same = ($local -eq $remote)
    if (-not $same) { $ok = $false }
    Write-Host ("  {0,-18}{1}  本地 {2}" -f $leaf,
                ($(if ($same) { '一致 ✓' } else { '不一致 ✗' })), $local.Substring(0, 16)) `
        -ForegroundColor $(if ($same) { 'Green' } else { 'Red' })
}

$n = ((& $adb shell run-as $Package wc -l files/cache/pool_texts.jsonl) -split '\s+')[0]
Write-Host ("  条目数           {0}" -f $n)

Write-Host "`n手机上现在的 cache（state.json 与底片应当**保持原样**）：" -ForegroundColor Cyan
& $adb shell run-as $Package ls -l files/cache

if (-not $ok) { throw "哈希不一致，别继续用。" }

Write-Host "`n✅ 推完了。重启 App 才会重新加载池子：" -ForegroundColor Green
Write-Host "   & '$adb' shell am force-stop $Package"
Write-Host "   & '$adb' shell am start -n '$Package/.MainActivity'"
Write-Host "`n⚠️ 回滚：push 之前先用 backup.ps1 取一份（含旧 pool.npz），出事就把它里的两个文件推回去。"
