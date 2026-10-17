# 构建包装脚本（DSH 里就用它）
#
# 为什么需要它：
#   1) 工具链在 D:\Android，而本会话的环境快照里没有那几个变量 ⇒ 每次显式给上；
#   2) Gradle 缓存默认落 %USERPROFILE%\.gradle（C 盘！）⇒ 改到工作区内的
#      .android-build\gradle-home（**仍在 D 盘**）；
#   3) ★ **TEMP/TMP 也必须挪进工作区**：沙箱只允许写工作区，而 Gradle / AGP /
#      pip 都会往 %TEMP% 建临时目录 —— 实测第一次构建就是卡死在这里
#      （守护进程日志停在 "Chaquopy: Installing for arm64-v8a"，
#        下面那行 pip 的 "Looking in indexes" 之后 11 分钟没有任何动静）；
#   4) 用工程自带 gradlew（Gradle 8.13，分发地址指向腾讯云镜像）。
#
# 用法：
#   pwsh -File build.ps1 :app:assembleDebug
#   pwsh -File build.ps1 :app:tasks
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GradleArgs)

$ErrorActionPreference = 'Stop'

$buildRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path + '\.android-build'
# ★ TEMP 为什么**不能**放在工作区里（2026-10-04 实测真因）：
#   工作区目录带一条继承下来的 `Everyone:(I)(CI)(DENY)(DC)`（沙箱 ACL 运行器留下的
#   "拒绝删除子容器"），而 pip 下载轮子后要重命名/清理临时目录 —— 正好撞上它，
#   报 `[Errno 13] Permission denied: '...pip-unpack-xxx\numpy-...whl'`，
#   并在工作区里留下 14 个删不掉的 pip-* 残骸。
#   ⇒ 临时目录放到 D:\Android\tmp（同样是 D 盘，但不沾那套 ACL）。
$tmp        = 'D:\Android\tmp'
$gradleHome = Join-Path $buildRoot 'gradle-home'
New-Item -ItemType Directory -Force -Path $tmp, $gradleHome | Out-Null

$env:JAVA_HOME         = 'D:\Android\jdk-17'
$env:ANDROID_HOME      = 'D:\Android\Sdk'
$env:ANDROID_SDK_ROOT  = 'D:\Android\Sdk'
$env:ANDROID_USER_HOME = 'D:\Android\user-home'
$env:GRADLE_USER_HOME  = $gradleHome
$env:TEMP              = $tmp
$env:TMP               = $tmp
$env:GRADLE_OPTS       = "-Djava.io.tmpdir=$tmp"
$env:Path = "D:\Android\jdk-17\bin;$env:Path"

$gradlew = Join-Path $PSScriptRoot 'gradlew.bat'
$gradleCmd = if (Test-Path $gradlew) { $gradlew } else { 'D:\Android\gradle-8.9\bin\gradle.bat' }

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
    foreach ($c in ($cands | Select-Object -Unique)) {     # 注册表那个排最前，通常一次就中
        $u = if ($c -match '^https?://') { $c } else { "http://$c" }
        # ★★ 必须测 **HTTPS**（2026-10-05 实测的真因）：
        #   pip 走的是 **CONNECT 隧道**，而"只测明文 HTTP"会选到**只会明文转发**的端点 ——
        #   实测 127.0.0.1:4310 明文 200、HTTPS 却 `000 Connection was aborted`，
        #   于是 pip 报 `ProxyError('Cannot connect …')` / `from versions: none`。
        #   ⇒ 拿 pip 真正要访问的索引站做判据（顺带把"能连上 chaquo"一起验了）。
        $code = & curl.exe -s -o NUL -w '%{http_code}' -x $u --max-time 8 'https://chaquo.com/pypi-13.1/' 2>$null
        if ($code -eq '200') { return $u }
    }
    return ''
}

$extra = @()
$pipCache = Join-Path $buildRoot 'pip-cache'      # 放 app/build 之外 ⇒ 清构建不会清掉轮子
New-Item -ItemType Directory -Force -Path $pipCache | Out-Null
$extra += "-PpipCacheDir=$pipCache"
$proxy = Get-ProxyUrl
if ($proxy) {
    Write-Host "pip 走代理 = $proxy（现探）"
    $extra += "-PpipProxy=$proxy"
} else {
    Write-Host "⚠️ 没探到可用代理 —— pip 直连，chaquo.com 可能非常慢"
}
Write-Host "pip 缓存   = $pipCache"
Write-Host ""

& $gradleCmd @GradleArgs @extra
exit $LASTEXITCODE
