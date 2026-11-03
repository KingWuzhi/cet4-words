# 编译安卓 APK（不需要 Gradle，也不需要联网）
# 依赖：Android SDK（build-tools + platforms/android-34）、JDK 17+、Python 3
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File android\build.ps1
#
# 产物：仓库根目录的 四级背单词.apk
$ErrorActionPreference = 'Stop'

function Step($msg) { Write-Host "== $msg" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

# ---------- 找 Android SDK ----------
$sdk = $env:ANDROID_SDK_ROOT
if (-not $sdk) { $sdk = $env:ANDROID_HOME }
if (-not $sdk) {
    foreach ($p in @("$env:LOCALAPPDATA\Android\Sdk", "$env:USERPROFILE\Android\Sdk")) {
        if (Test-Path (Join-Path $p 'build-tools')) { $sdk = $p; break }
    }
}
if (-not $sdk -or -not (Test-Path (Join-Path $sdk 'build-tools'))) {
    throw '找不到 Android SDK：请设置 ANDROID_SDK_ROOT，或把 SDK 装到 %LOCALAPPDATA%\Android\Sdk'
}

# build-tools 取主版本号最大的一个
$btDir = Get-ChildItem (Join-Path $sdk 'build-tools') -Directory |
    Sort-Object { [int](($_.Name -split '\.')[0]) } | Select-Object -Last 1
$bt = $btDir.FullName

# platforms 优先 android-34，没有就用最新的
$androidJar = Join-Path $sdk 'platforms\android-34\android.jar'
if (-not (Test-Path $androidJar)) {
    $plat = Get-ChildItem (Join-Path $sdk 'platforms') -Directory | Sort-Object Name | Select-Object -Last 1
    if (-not $plat) { throw 'SDK 里没有 platforms，请安装 android-34 平台' }
    $androidJar = Join-Path $plat.FullName 'android.jar'
}

# ---------- 找 JDK ----------
# 注意两点：
#   1) Oracle 的 javapath 目录里只有 javac.exe，没有 jar/keytool —— 必须整套校验
#   2) "C:\Program Files\Java\latest" 往往是 junction，Get-ChildItem -Recurse 不会跟进去，
#      所以这里手动逐层找
function Test-Jdk($dir) {
    if (-not $dir) { return $false }
    return (Test-Path (Join-Path $dir 'javac.exe')) -and
           (Test-Path (Join-Path $dir 'jar.exe')) -and
           (Test-Path (Join-Path $dir 'keytool.exe'))
}
$cands = New-Object System.Collections.ArrayList
if ($env:JAVA_HOME) { [void]$cands.Add((Join-Path $env:JAVA_HOME 'bin')) }
$javacCmd = Get-Command javac.exe -ErrorAction SilentlyContinue
if ($javacCmd) { [void]$cands.Add((Split-Path $javacCmd.Source -Parent)) }

function Find-Jdk($root, $depth) {
    if (-not (Test-Path $root)) { return }
    foreach ($d in (Get-ChildItem $root -Directory -ErrorAction SilentlyContinue)) {
        $bin = Join-Path $d.FullName 'bin'
        if (Test-Jdk $bin) { [void]$cands.Add($bin); continue }
        if ($depth -gt 1) { Find-Jdk $d.FullName ($depth - 1) }
    }
}
foreach ($r in @('C:\Program Files\Java', 'C:\Program Files\Eclipse Adoptium',
                 'C:\Program Files\Microsoft', 'C:\Program Files\Android\Android Studio\jbr')) {
    Find-Jdk $r 3
}
$jdk = $cands | Where-Object { Test-Jdk $_ } | Select-Object -First 1
if (-not $jdk) { throw '找不到 JDK：请安装 JDK 17+ 并设置 JAVA_HOME' }

# ---------- 找 Python（只用来合并 APK） ----------
$pyArgs = @()
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $py = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
    if ($py) { $python = $py; $pyArgs = @('-3') } else { throw '找不到 Python 3（打包脚本需要它来合成 APK）' }
}

$root     = $PSScriptRoot
$repo     = Split-Path $root -Parent
$work     = Join-Path $root 'build'
$assets   = Join-Path $root 'assets'
$outApk   = Join-Path $repo '四级背单词.apk'
$keystore = Join-Path $root 'cet4.keystore'
$ksPass   = 'cet4pass'
$ksAlias  = 'cet4'
$verCode  = 5
$verName  = '1.6'

# ---------- 0. 清理 ----------
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Force $work, (Join-Path $work 'gen'), (Join-Path $work 'classes'),
    (Join-Path $work 'assets'), (Join-Path $work 'dex') | Out-Null

# ---------- 0.5 页面没生成就先跑 tools/build.py ----------
if (-not (Test-Path (Join-Path $assets 'index.html'))) {
    Step 'build page (assets/index.html missing)'
    & $python @pyArgs (Join-Path $repo 'tools\build.py')
    Check 'tools/build.py'
}

# ---------- 1. 放进网页 ----------
Copy-Item -LiteralPath (Join-Path $assets 'index.html') -Destination (Join-Path $work 'assets\index.html') -Force
Step ("asset: index.html " + (Get-Item (Join-Path $work 'assets\index.html')).Length + " bytes")

# ---------- 2. 编译资源 ----------
Step 'aapt2 compile'
& (Join-Path $bt 'aapt2.exe') compile --dir (Join-Path $root 'res') -o (Join-Path $work 'res.zip')
Check 'aapt2 compile'

# ---------- 3. 链接资源与清单 ----------
Step 'aapt2 link'
& (Join-Path $bt 'aapt2.exe') link `
    -o (Join-Path $work 'base.apk') `
    -I $androidJar `
    --manifest (Join-Path $root 'AndroidManifest.xml') `
    -R (Join-Path $work 'res.zip') `
    --java (Join-Path $work 'gen') `
    --min-sdk-version 23 `
    --target-sdk-version 34 `
    --version-code $verCode `
    --version-name $verName `
    --auto-add-overlay
Check 'aapt2 link'

# ---------- 4. 编译 Java ----------
# -parameters 不能省：d8 8.2 遇到 javac 21 生成的空 MethodParameters 会内部崩溃
Step 'javac'
$srcs = @()
$srcs += (Get-ChildItem (Join-Path $root 'src') -Recurse -Filter *.java | ForEach-Object { $_.FullName })
$srcs += (Get-ChildItem (Join-Path $work 'gen') -Recurse -Filter *.java | ForEach-Object { $_.FullName })
& (Join-Path $jdk 'javac.exe') -source 8 -target 8 -bootclasspath $androidJar -parameters `
    -nowarn -Xlint:-options -encoding UTF-8 -d (Join-Path $work 'classes') $srcs
Check 'javac'

# ---------- 5. classes.jar -> classes.dex ----------
Step 'd8'
& (Join-Path $jdk 'jar.exe') cf (Join-Path $work 'classes.jar') -C (Join-Path $work 'classes') .
Check 'jar'
& (Join-Path $bt 'd8.bat') --lib $androidJar --min-api 23 --output (Join-Path $work 'dex') (Join-Path $work 'classes.jar')
Check 'd8'

# ---------- 6. 合成 APK ----------
Step 'pack'
& $python @pyArgs (Join-Path $root 'pack.py') (Join-Path $work 'base.apk') (Join-Path $work 'dex\classes.dex') (Join-Path $work 'assets') (Join-Path $work 'unsigned.apk')
Check 'pack'

# ---------- 7. 对齐 ----------
Step 'zipalign'
& (Join-Path $bt 'zipalign.exe') -f -p 4 (Join-Path $work 'unsigned.apk') (Join-Path $work 'aligned.apk')
Check 'zipalign'

# ---------- 8. 签名 ----------
if (-not (Test-Path $keystore)) {
    Step 'create keystore'
    & (Join-Path $jdk 'keytool.exe') -genkeypair -keystore $keystore -alias $ksAlias `
        -storepass $ksPass -keypass $ksPass -keyalg RSA -keysize 2048 -validity 10950 `
        -dname 'CN=CET4 Words, O=CET4, C=CN'
    Check 'keytool'
}
Step 'apksigner sign'
& (Join-Path $bt 'apksigner.bat') sign --ks $keystore --ks-key-alias $ksAlias `
    --ks-pass "pass:$ksPass" --key-pass "pass:$ksPass" `
    --min-sdk-version 23 --v1-signing-enabled true --v2-signing-enabled true `
    --out $outApk (Join-Path $work 'aligned.apk')
Check 'apksigner sign'

# ---------- 9. 校验 ----------
Step 'verify'
# 先把输出收进变量再截取，避免管道提前关闭把 apksigner 弄成非零退出
$vout = & (Join-Path $bt 'apksigner.bat') verify --verbose $outApk 2>&1
$vout | Select-Object -First 4 | ForEach-Object { Write-Host "   $_" }
if (($vout -join "`n") -match 'DOES NOT VERIFY') { throw 'apksigner verify: does not verify' }
& (Join-Path $bt 'aapt2.exe') dump badging $outApk | Select-String -Pattern 'versionName|application-label' | ForEach-Object { $_.Line }

$size = [math]::Round((Get-Item $outApk).Length / 1KB, 1)
Write-Host ""
Write-Host "APK -> $outApk ($size KB)" -ForegroundColor Green

exit 0
