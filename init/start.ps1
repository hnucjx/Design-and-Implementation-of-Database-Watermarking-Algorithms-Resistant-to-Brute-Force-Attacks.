# YouTube Downloader 一键启动脚本（Windows / PowerShell）
#
# 用法（在仓库根目录的 PowerShell 中执行）：
#   powershell -ExecutionPolicy Bypass -File init\start.ps1
#   powershell -ExecutionPolicy Bypass -File init\start.ps1 dev          # 开发模式：后端 + 前端 Vite 并发
#   powershell -ExecutionPolicy Bypass -File init\start.ps1 --no-build   # 单端口模式跳过前端构建
#   （单端口模式默认已带 --auto-port 自动选端口；如要固定端口可传 --port 8010）
#
# 任意未知参数都会原样透传给 `python -m app`，例如：
#   powershell -ExecutionPolicy Bypass -File init\start.ps1 --port 8010
#
# 也可直接双击 init\start.bat（它已带上 -ExecutionPolicy Bypass）。
#
# 前置依赖：Node.js 20+、Python 3.12+、npm；ffmpeg 可选。
# 若仓库根或 backend\ 下已存在 .venv，脚本会自动使用；否则使用 PATH 中的 python。

$ErrorActionPreference = 'Stop'

# 让 Python 子进程（后端）的输出不被块缓冲，便于脚本实时解析自动选择的端口
$env:PYTHONUNBUFFERED = '1'

# PowerShell 5.1 的 Start-Process 会把环境复制到一个大小写不敏感的字典；若同时出现
# HTTP_PROXY 与 http_proxy（本机很常见）会抛 "Item has already been added"。下面仅在「大写副本也存在」
# 时才删小写副本，避免误删仅以小写存在的代理（Python 两种大小写都认，删掉小写、留大写即可消除冲突）。
$proxyPairs = @(
    @('http_proxy',  'HTTP_PROXY'),
    @('https_proxy', 'HTTPS_PROXY'),
    @('no_proxy',    'NO_PROXY'),
    @('all_proxy',   'ALL_PROXY'),
    @('ftp_proxy',   'FTP_PROXY')
)
$rawEnv = [System.Environment]::GetEnvironmentVariables()
foreach ($p in $proxyPairs) {
    $lower, $upper = $p[0], $p[1]
    if ($rawEnv.ContainsKey($lower) -and $rawEnv.ContainsKey($upper)) {
        try { [System.Environment]::SetEnvironmentVariable($lower, $null) } catch { }
    }
}

$RepoRoot    = Resolve-Path (Join-Path $PSScriptRoot '..')
$FrontendDir = Join-Path $RepoRoot 'frontend'
$BackendDir  = Join-Path $RepoRoot 'backend'

$Mode         = 'prod'
$NoBuild      = $false
$BackendExtra = [System.Collections.Generic.List[string]]::new()

foreach ($arg in $args) {
    switch ($arg) {
        'dev'        { $Mode = 'dev' }
        '--no-build' { $NoBuild = $true }
        '-h'         { Get-Help $MyInvocation.MyCommand; exit 0 }
        '--help'     { Get-Help $MyInvocation.MyCommand; exit 0 }
        default      { $BackendExtra.Add($arg) }   # --port / --auto-port / --reload / --host 等透传给后端
    }
}

function Fail([string]$msg) {
    Write-Host "错误：$msg" -ForegroundColor Red
    exit 1
}

# ---- 前端依赖检查 ----
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "未找到 node。请先安装 Node.js 20+（https://nodejs.org）。" }
if (-not (Get-Command npm  -ErrorAction SilentlyContinue)) { Fail "未找到 npm。请先安装 Node.js（npm 随 Node 一起提供）。" }

$nodeVer    = (node -v).TrimStart('v')
$nodeMajor  = [int]($nodeVer.Split('.')[0])
if ($nodeMajor -lt 20) { Fail "Node.js 版本过低（当前 v$nodeVer），需要 20+。" }

# ---- 选择 Python 解释器 ----
$Python = $null
if ($env:VIRTUAL_ENV -and (Test-Path (Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'))) {
    $Python = Join-Path $env:VIRTUAL_ENV 'Scripts\python.exe'
} elseif (Test-Path (Join-Path $RepoRoot '.venv\Scripts\python.exe')) {
    $Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
} elseif (Test-Path (Join-Path $BackendDir '.venv\Scripts\python.exe')) {
    $Python = Join-Path $BackendDir '.venv\Scripts\python.exe'
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = 'python'
} else {
    Fail "未找到 Python 3.12+。请先安装 Python（https://www.python.org）。"
}

$pyVer = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
if (-not $pyVer) { Fail "无法运行 Python 解释器：$Python" }
$pyMajor = [int]($pyVer.Split('.')[0])
$pyMinor = [int]($pyVer.Split('.')[1])
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 12)) { Fail "Python 版本过低（当前 $pyVer），需要 3.12+。" }

Write-Host "使用 Python：$Python ($pyVer)"

# ---- 前端依赖与构建 ----
if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
    Write-Host "==> 安装前端依赖（npm install）..."
    Push-Location $FrontendDir
    try { & npm install; if ($LASTEXITCODE -ne 0) { Fail "前端依赖安装失败。" } }
    finally { Pop-Location }
}

if ($Mode -eq 'prod' -and -not $NoBuild) {
    Write-Host "==> 构建前端（npm run build）..."
    Push-Location $FrontendDir
    try { & npm run build; if ($LASTEXITCODE -ne 0) { Fail "前端构建失败。" } }
    finally { Pop-Location }
}

# ---- 后端依赖 ----
Write-Host "==> 安装后端依赖（pip install -e .）..."
Push-Location $BackendDir
try { & $Python -m pip install -e .; if ($LASTEXITCODE -ne 0) { Fail "后端依赖安装失败。" } }
finally { Pop-Location }

# ---- 启动 ----
if ($Mode -eq 'dev') {
    Write-Host ""
    Write-Host "==> 开发模式：并发启动后端（--reload）与前端 Vite dev server"
    Write-Host "    后端地址：http://127.0.0.1:${env:YTDL_API_PORT}（默认 8000，可被仓库根 .env 覆盖）"
    Write-Host "    前端地址：http://127.0.0.1:5173"
    Write-Host "    关闭窗口或按 Ctrl+C 同时停止两者。"
    Write-Host ""

    $backendArgs = [System.Collections.Generic.List[string]]::new()
    $backendArgs.Add('-m'); $backendArgs.Add('app'); $backendArgs.Add('--reload')
    $backendArgs.AddRange($BackendExtra)

    $script:backendJob  = Start-Process -FilePath $Python -ArgumentList $backendArgs  -WorkingDirectory $BackendDir  -PassThru
    $script:frontendJob = Start-Process -FilePath 'npm'  -ArgumentList @('run','dev','--','--port','5173') -WorkingDirectory $FrontendDir -PassThru

    $script:cleanup = {
        Write-Host ""
        Write-Host "==> 正在停止前后端..."
        if ($script:backendJob  -and -not $script:backendJob.HasExited)  { Stop-Process -Id $script:backendJob.Id  -Force }
        if ($script:frontendJob -and -not $script:frontendJob.HasExited) { Stop-Process -Id $script:frontendJob.Id -Force }
    }
    # Ctrl+C 时先清理子进程，避免 Vite / uvicorn 成为孤儿进程
    # 注意：本机 Windows PowerShell 5.1 中 [Console]::CancelKeyPress 为 $null，必须用 CLR 访问器 add_CancelKeyPress
    [Console]::add_CancelKeyPress({ param($s,$e) $e.Cancel = $true; & $script:cleanup })

    try {
        while ($true) {
            Start-Sleep -Seconds 1
            if ($script:backendJob.HasExited -or $script:frontendJob.HasExited) { break }
        }
    } finally {
        & $script:cleanup
    }
} else {
    Write-Host ""
    Write-Host "==> 单端口模式：启动后端（默认带 --auto-port 自动选择可用端口，并自动打开浏览器）"
    Write-Host "    按 Ctrl+C 停止。"
    Write-Host ""

    $backendArgs = [System.Collections.Generic.List[string]]::new()
    $backendArgs.Add('-m'); $backendArgs.Add('app'); $backendArgs.Add('--auto-port')
    $backendArgs.AddRange($BackendExtra)

    $script:log    = Join-Path $env:TEMP ("ytdl-start-" + [System.Guid]::NewGuid().ToString("N") + ".log")
    $script:logErr = Join-Path $env:TEMP ("ytdl-start-" + [System.Guid]::NewGuid().ToString("N") + ".err")

    $script:backendProc = Start-Process -FilePath $Python -ArgumentList $backendArgs -WorkingDirectory $BackendDir -PassThru -NoNewWindow -RedirectStandardOutput $script:log -RedirectStandardError $script:logErr

    $script:cleanup = {
        if ($script:backendProc -and -not $script:backendProc.HasExited) { Stop-Process -Id $script:backendProc.Id -Force }
        Remove-Item $script:log    -ErrorAction SilentlyContinue
        Remove-Item $script:logErr -ErrorAction SilentlyContinue
    }
    # Ctrl+C 时先清理子进程，避免 uvicorn 成为孤儿进程
    # 注意：本机 Windows PowerShell 5.1 中 [Console]::CancelKeyPress 为 $null，必须用 CLR 访问器 add_CancelKeyPress
    [Console]::add_CancelKeyPress({ param($s,$e) $e.Cancel = $true; & $script:cleanup })

    $port = $null
    $shown = 0
    try {
        while (-not $script:backendProc.HasExited) {
            if (Test-Path $script:log) {
                $lines = @(Get-Content -Path $script:log -ErrorAction SilentlyContinue)
                if ($lines.Count -gt $shown) {
                    for ($j = $shown; $j -lt $lines.Count; $j++) {
                        $l = $lines[$j]
                        Write-Host $l
                        if ($l -match '服务地址：http://127\.0\.0\.1:(\d+)') { $port = $Matches[1] }
                    }
                    $shown = $lines.Count
                }
            }
            if ($port) {
            Write-Host "==> 已自动选择端口 $port，正在打开浏览器 http://127.0.0.1:$port ..."
            Start-Sleep -Seconds 1
            try { Start-Process "http://127.0.0.1:$port" } catch { Write-Host "提示：未能自动打开浏览器，请手动访问 http://127.0.0.1:$port" -ForegroundColor Yellow }
            $port = $null
            }
            Start-Sleep -Milliseconds 300
        }
    } finally {
        & $script:cleanup
    }

    # 排空剩余日志
    if (Test-Path $script:log)    { @(Get-Content -Path $script:log    -ErrorAction SilentlyContinue) | Select-Object -Skip $shown | ForEach-Object { Write-Host $_ } }
    if (Test-Path $script:logErr) { @(Get-Content -Path $script:logErr -ErrorAction SilentlyContinue) | ForEach-Object { Write-Host $_ -ForegroundColor Red } }
}
