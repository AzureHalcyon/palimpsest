# 构建包装脚本（DSH 里就用它）
#
# 为什么需要它：
#   1) 工具链不在 PATH 上 ⇒ 每次显式给上；
#   2) Gradle/pip 缓存默认落 C 盘用户目录 ⇒ 挪到工作区旁边（`<workspace>/.android-build`）；
#   3) ★ **TEMP/TMP 也必须挪出工作区**：Gradle / AGP / pip 都会往 %TEMP% 建临时目录，
#      而 pip 下载轮子后要重命名/清理临时目录，撞上工作区继承下来的 ACL 会报
#      `[Errno 13] Permission denied: '...pip-unpack-xxx\numpy-...whl'`（见 2026-10-04 实测）；
#   4) 用工程自带 gradlew（Gradle 8.13，分发地址指向腾讯云镜像）。
#
# 用法：
#   pwsh -File build.ps1 :app:assembleDebug
#   pwsh -File build.ps1 :app:tasks
#   pwsh -File build.ps1 -Toolchain 'X:\Android' :app:assembleDebug    # 工具链不在默认位置时
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$GradleArgs,
    # ★ 工具链根目录（里面要有 jdk-17 / Sdk）。取值顺序：
    #     ① `-Toolchain` 参数  ② 环境变量 `PALIMPSEST_TOOLCHAIN`
    #     ③ 本机常见的 `D:\Android`
    #     ④ 从已有的 `JAVA_HOME` 与 `ANDROID_HOME` 反推**共同的父目录**
    #     ⑤ 都没有 ⇒ **报错说清要设什么**（⛔ 猜一个路径）
    [string]$Toolchain,
    # 三个缓存目录都可以单独覆盖；默认放在仓库旁边的 `.android-build/`。
    [string]$BuildRoot,
    [string]$GradleHome,
    [string]$PipCache
)

$ErrorActionPreference = 'Stop'

if (-not $Toolchain) { $Toolchain = $env:PALIMPSEST_TOOLCHAIN }
if (-not $Toolchain -and (Test-Path -LiteralPath 'D:\Android\jdk-17') -and
    (Test-Path -LiteralPath 'D:\Android\Sdk')) {
    $Toolchain = 'D:\Android'
}
if (-not $Toolchain -and $env:JAVA_HOME -and $env:ANDROID_HOME) {
    # 反推：两者若在同一个父目录下，那个父目录就是工具链根
    $j = (Resolve-Path -LiteralPath $env:JAVA_HOME -ErrorAction SilentlyContinue).Path
    $a = (Resolve-Path -LiteralPath $env:ANDROID_HOME -ErrorAction SilentlyContinue).Path
    if ($j -and $a) {
        $pj = Split-Path $j -Parent
        if ($pj -eq (Split-Path $a -Parent)) { $Toolchain = $pj }
    }
}
if (-not $Toolchain) {
    throw @"
没找到 Android 工具链。请用下面任一方式给出（它下面要有 jdk-17 / Sdk）：
  · pwsh -File build.ps1 -Toolchain 'X:\Android' ...
  · 设环境变量 PALIMPSEST_TOOLCHAIN=X:\Android
  · 或先设好 JAVA_HOME 与 ANDROID_HOME（两者在同一父目录下）
"@
}
if (-not (Test-Path -LiteralPath $Toolchain)) { throw "工具链目录不存在：$Toolchain" }

# 缓存默认放在仓库**旁边**（`<workspace>/.android-build`），既不占 C 盘，
# 也不进 git（`.android-build` 不是本仓库的一部分）。三个目录都能用参数覆盖。
if (-not $BuildRoot) {
    $BuildRoot = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) '.android-build'
}
if (-not $GradleHome) { $GradleHome = Join-Path $BuildRoot 'gradle-home' }
if (-not $PipCache)   { $PipCache   = Join-Path $BuildRoot 'pip-cache' }
$tmp        = Join-Path $BuildRoot 'tmp'
$userHome   = Join-Path $BuildRoot 'android-user-home'
New-Item -ItemType Directory -Force -Path $tmp, $GradleHome, $PipCache, $userHome | Out-Null

$env:JAVA_HOME         = Join-Path $Toolchain 'jdk-17'
$env:ANDROID_HOME      = Join-Path $Toolchain 'Sdk'
$env:ANDROID_SDK_ROOT  = Join-Path $Toolchain 'Sdk'
$env:ANDROID_USER_HOME = $userHome
$env:GRADLE_USER_HOME  = $GradleHome
$env:TEMP              = $tmp
$env:TMP               = $tmp
$env:GRADLE_OPTS       = "-Djava.io.tmpdir=$tmp"
$env:Path = "$(Join-Path $Toolchain 'jdk-17\bin');$env:Path"

# ★ Chaquopy 要一个 Python 3.13（用来跑 pip 打包）。**⛔ 写死在 gradle 文件里**，
#   这里发现后传给 gradle（`-PbuildPython`）。顺序：工具链里 → PATH 上的 python。
$buildPython = $null
foreach ($c in @((Join-Path $Toolchain 'Python313\python.exe'),
                 (Join-Path $Toolchain 'python.exe'))) {
    if (Test-Path -LiteralPath $c) { $buildPython = (Resolve-Path -LiteralPath $c).Path; break }
}
if (-not $buildPython) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $buildPython = $cmd.Source }
}
if (-not $buildPython) { throw "没找到构建用 Python（3.13）。装到 '$Toolchain\Python313\' 或保证 PATH 上有 python。" }

$gradlew = Join-Path $PSScriptRoot 'gradlew.bat'
$gradleCmd = if (Test-Path $gradlew) { $gradlew } else { 'gradle.bat' }

Write-Host "JAVA_HOME        = $env:JAVA_HOME"
Write-Host "ANDROID_HOME     = $env:ANDROID_HOME"
Write-Host "GRADLE_USER_HOME = $env:GRADLE_USER_HOME"
Write-Host "TEMP / TMP       = $env:TEMP"
Write-Host "gradle           = $gradleCmd"
Write-Host ""

if (-not $GradleArgs -or $GradleArgs.Count -eq 0) { $GradleArgs = @('help') }

# --------------------------------------------------------------------------- 代理（给 pip 用）
# ★ chaquo.com 在国内直连很慢：实测多次在 500 KB 级半截下载上挂十几分钟。
#   端点**每次都现探，绝不写死端口号** —— 按 ~/.dsh/工具详情.md 的 FlClash 三步：
#     1) 注册表里的系统代理   2) `netstat -ano` 找 LISTENING   3) `curl -x` 实测 200 才算数
#   （查监听端口必须用 `netstat -ano`；`Get-NetTCPConnection` 在这台机器上会**静默返回空**。）
function Get-ProxyUrl {
    $cands = [System.Collections.Generic.List[string]]::new()
    $reg = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($reg.ProxyEnable -and $reg.ProxyServer) {
        foreach ($p in ($reg.ProxyServer -split ';')) {
            $v = ($p -split '=')[-1].Trim()
            if ($v) { $cands.Add($v) }
        }
    }
    & netstat -ano | Select-String 'LISTENING' | ForEach-Object {
        if ($_.Line -match '127\.0\.0\.1:(\d+)') { $cands.Add("127.0.0.1:$($Matches[1])") }
    }
    foreach ($c in ($cands | Select-Object -Unique | Select-Object -First 12)) {     # 注册表那个排最前，通常一次就中
        $u = if ($c -match '^https?://') { $c } else { "http://$c" }
        # ★★ 必须测 **HTTPS**（2026-10-05 实测的真因）：
        #   pip 走的是 **CONNECT 隧道**，而"只测明文 HTTP"会选到**只会明文转发**的端点 ——
        #   实测 127.0.0.1:4310 明文 200、HTTPS 却 `000 Connection was aborted`，
        #   于是 pip 报 `ProxyError('Cannot connect …')` / `from versions: none`。
        #   ⇒ 拿 pip 真正要访问的索引站做判据（顺带把"能连上 chaquo"一起验了）。
        $code = & curl.exe -s -o NUL -w '%{http_code}' -x $u --connect-timeout 3 --max-time 6 'https://chaquo.com/pypi-13.1/' 2>$null
        if ($code -eq '200') { return $u }
    }
    return ''
}

$extra = @()
$extra += "-PpipCacheDir=$PipCache"
# ★ 构建用 Python 也走 `-P`（`app/build.gradle.kts` 里 ⛔ 写死路径）
$extra += "-PbuildPython=$buildPython"
Write-Host "buildPython = $buildPython"
# ★ 允许显式给代理，跳过“现探”（探不到时它会挨个试，端口多时很慢）。
$proxy = $env:PALIMPSEST_PIP_PROXY
if (-not $proxy) { $proxy = Get-ProxyUrl }
if ($proxy) {
    Write-Host "pip 走代理 = $proxy（现探）"
    $extra += "-PpipProxy=$proxy"
} else {
    Write-Host "⚠️ 没探到可用代理 —— pip 直连，chaquo.com 可能非常慢"
}
Write-Host "pip 缓存   = $PipCache"
Write-Host ""

# ★ Gradle 必须在模块根目录跑：调用者可能从任意 cwd 启动本脚本。
Push-Location $PSScriptRoot
& $gradleCmd @GradleArgs @extra
$code = $LASTEXITCODE
Pop-Location
exit $code