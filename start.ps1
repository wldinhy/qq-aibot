$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$botExe = Join-Path $projectRoot '.venv\Scripts\qq-aibot.exe'
$napcatShell = Join-Path $projectRoot '.local\napcat\shell'
$napcatLauncher = Join-Path $napcatShell 'launcher-user.bat'
$webuiPath = Join-Path $napcatShell 'config\webui.json'
$envPath = Join-Path $projectRoot '.env'
$configPath = Join-Path $projectRoot 'config.json'

if (-not (Test-Path -LiteralPath $botExe) -or -not (Test-Path -LiteralPath $napcatLauncher)) {
    throw '尚未安装机器人或 NapCat。请先运行 .\install.cmd。'
}

Set-Location -LiteralPath $projectRoot
& $botExe doctor
if ($LASTEXITCODE -ne 0) {
    throw '机器人配置不完整。运行 .\.venv\Scripts\qq-aibot.exe，在菜单里设置 API Key 和模型。'
}

$botConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$botPort = [int]$botConfig.onebot.port
$websocketPath = [string]$botConfig.onebot.path
$tokenLine = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^ONEBOT_ACCESS_TOKEN=' } | Select-Object -First 1
$onebotToken = $tokenLine.Substring('ONEBOT_ACCESS_TOKEN='.Length).Trim()

function Test-ListeningPort([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

$webuiPort = 6099
if (Test-Path -LiteralPath $webuiPath) {
    $webuiPort = [int](Get-Content -LiteralPath $webuiPath -Raw | ConvertFrom-Json).port
}

if (Test-ListeningPort $webuiPort) {
    Write-Host "NapCat 已在端口 $webuiPort 运行。"
} else {
    Write-Host '正在打开 NapCat 窗口。首次登录请用手机 QQ 扫描该窗口的二维码。'
    Start-Process -FilePath $env:ComSpec -ArgumentList '/c launcher-user.bat' -WorkingDirectory $napcatShell -WindowStyle Normal | Out-Null
}

if (Test-ListeningPort $botPort) {
    Write-Host "端口 $botPort 已有监听程序，暂不重复启动机器人。"
} else {
    $escapedRoot = $projectRoot.Replace("'", "''")
    $escapedExe = $botExe.Replace("'", "''")
    $botCommand = "Set-Location -LiteralPath '$escapedRoot'; & '$escapedExe' serve"
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($botCommand))
    Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoExit -EncodedCommand $encodedCommand" -WorkingDirectory $projectRoot -WindowStyle Normal | Out-Null
    Write-Host '已打开机器人窗口，运行日志会显示在那里。'
}

Write-Host '等待 NapCat 登录 QQ，并自动连接机器人（最多 10 分钟）...'
$deadline = (Get-Date).AddMinutes(10)
$lastError = ''
while ((Get-Date) -lt $deadline) {
    try {
        if (-not (Test-Path -LiteralPath $webuiPath)) { throw '等待 NapCat WebUI 启动' }
        $webui = Get-Content -LiteralPath $webuiPath -Raw | ConvertFrom-Json
        $webuiPort = [int]$webui.port
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $hashBytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$webui.token + '.napcat'))
        } finally {
            $sha.Dispose()
        }
        $hash = [BitConverter]::ToString($hashBytes).Replace('-', '').ToLowerInvariant()
        $api = "http://127.0.0.1:$webuiPort/api"
        $loginBody = @{ hash = $hash } | ConvertTo-Json -Compress
        $login = Invoke-RestMethod -Uri "$api/auth/login" -Method Post -ContentType 'application/json' -Body $loginBody -TimeoutSec 5
        if ($login.code -ne 0 -or -not $login.data.Credential) { throw '等待 NapCat WebUI 登录' }
        $headers = @{ Authorization = "Bearer $($login.data.Credential)" }
        $result = Invoke-RestMethod -Uri "$api/OB11Config/GetConfig" -Method Post -Headers $headers -TimeoutSec 5
        if ($result.code -ne 0) { throw '等待手机 QQ 扫码并确认登录' }

        $obConfig = $result.data
        $otherClients = @($obConfig.network.websocketClients | Where-Object { $_ -and $_.name -ne 'qq-aibot' })
        $botClient = [pscustomobject]@{
            name = 'qq-aibot'
            enable = $true
            url = "ws://127.0.0.1:$botPort$websocketPath"
            messagePostFormat = 'array'
            reportSelfMessage = $false
            reconnectInterval = 5000
            token = $onebotToken
            debug = $false
            heartInterval = 30000
        }
        $obConfig.network.websocketClients = @($otherClients) + @($botClient)
        $configJson = $obConfig | ConvertTo-Json -Depth 20 -Compress
        $body = @{ config = $configJson } | ConvertTo-Json -Depth 20 -Compress
        $saved = Invoke-RestMethod -Uri "$api/OB11Config/SetConfig" -Method Post -Headers $headers -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 10
        if ($saved.code -ne 0) { throw "NapCat 配置失败：$($saved.message)" }
        Write-Host 'QQ 登录和 OneBot 连接已配置。保持 NapCat 与机器人两个窗口打开即可自动回复。'
        exit 0
    } catch {
        $lastError = $_.Exception.Message
        Start-Sleep -Seconds 3
    }
}

throw "等待 QQ 登录超时：$lastError。扫码完成后可以重新运行 .\start.cmd。"
