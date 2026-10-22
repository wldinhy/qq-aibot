$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$botExe = Join-Path $projectRoot '.venv\Scripts\qq-aibot.exe'
$napcatShell = Join-Path $projectRoot '.local\napcat\shell'
$napcatMain = Join-Path $napcatShell 'napcat.mjs'

Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -m venv (Join-Path $projectRoot '.venv')
    } else {
        $python = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $python) { throw '请先安装 Python 3.10 或更新版本，并勾选 Add Python to PATH。' }
        & $python.Source -m venv (Join-Path $projectRoot '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 虚拟环境失败。' }
}

& $venvPython -m pip install -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw '安装机器人依赖失败，请检查网络和 Python 版本。' }

& $botExe init
if ($LASTEXITCODE -ne 0) { throw '初始化机器人配置失败。' }

$envPath = Join-Path $projectRoot '.env'
$tokenLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^ONEBOT_ACCESS_TOKEN=' } | Select-Object -First 1
if (-not $tokenLine -or $tokenLine -match 'change-this-to-a-long-random-string') {
    & $botExe secret set onebot | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '生成 OneBot 连接密钥失败。' }
    Write-Host '已生成本机 OneBot 连接密钥（保存在 .env，不会上传到 GitHub）。'
}

if (-not (Test-Path -LiteralPath $napcatMain)) {
    Write-Host '正在从 NapCat 官方 GitHub Release 下载 Windows Shell 版...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest' -Headers @{ 'User-Agent' = 'qq-aibot-installer' }
    $asset = $release.assets | Where-Object { $_.name -eq 'NapCat.Shell.zip' } | Select-Object -First 1
    if (-not $asset) { throw '官方最新 Release 中找不到 NapCat.Shell.zip。' }
    $archive = Join-Path $env:TEMP ("NapCat.Shell.{0}.zip" -f $release.tag_name)
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive -UseBasicParsing
    New-Item -ItemType Directory -Path $napcatShell -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $napcatShell -Force
    if (-not (Test-Path -LiteralPath $napcatMain)) { throw 'NapCat 解压后缺少 napcat.mjs。' }
    Write-Host ("已安装 NapCat {0}。" -f $release.tag_name)
} else {
    Write-Host 'NapCat 已安装，保留现有登录和配置。'
}

Write-Host ''
Write-Host '安装完成。先运行 .\.venv\Scripts\qq-aibot.exe 配置 API Key 和模型。'
Write-Host '配置完成后运行 .\start.cmd，一条命令启动机器人和 NapCat。'
