param([switch]$CheckOnly, [switch]$Ubuntu)

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

$botConfig = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$botPort = [int]$botConfig.onebot.port
$websocketPath = [string]$botConfig.onebot.path
$tokenLine = Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object { $_ -match '^ONEBOT_ACCESS_TOKEN=' } | Select-Object -First 1
$onebotToken = $tokenLine.Substring('ONEBOT_ACCESS_TOKEN='.Length).Trim()

function Test-ListeningPort([int]$port) {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $connection = $client.ConnectAsync('127.0.0.1', $port)
        return ($connection.Wait(500) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

$comfyScript = Join-Path $projectRoot 'start-comfy.ps1'
$imageEnabled = $botConfig.image_generation -and $botConfig.image_generation.enabled
if ($imageEnabled) {
    $comfyApi = [string]$botConfig.image_generation.api_base
    & $comfyScript -ApiBase $comfyApi -CheckOnly
    if ($LASTEXITCODE -ne 0) { throw 'ComfyUI 启动配置无效。' }
}
$linuxScript = $null
if ($Ubuntu) {
    # --exec passes arguments directly; forward slashes also survive PS 5.1.
    # The default WSL shell otherwise treats Windows backslashes as escapes.
    $windowsRoot = $projectRoot.Replace('\', '/')
    $previousEncoding = [Console]::OutputEncoding
    try {
        [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
        $linuxRoot = (& wsl.exe -d Ubuntu --exec wslpath -a -u $windowsRoot | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $linuxRoot.StartsWith('/')) {
            throw "Ubuntu 项目路径转换失败：$windowsRoot。请检查 WSL 输出。"
        }
        $linuxScript = $linuxRoot + '/start.sh'
        & wsl.exe -d Ubuntu --exec test -f $linuxScript
        if ($LASTEXITCODE -ne 0) {
            throw "Ubuntu 无法读取启动脚本：$linuxScript"
        }
    } finally {
        [Console]::OutputEncoding = $previousEncoding
    }
    Write-Host "Ubuntu 启动脚本可用：$linuxScript"
}
if ($CheckOnly) {
    Write-Host '机器人、NapCat、ComfyUI 的启动配置检查完成。'
    exit 0
}
if ($imageEnabled) {
    $comfyPort = ([Uri]$comfyApi).Port
    if (Test-ListeningPort $comfyPort) {
        Write-Host "ComfyUI 已在端口 $comfyPort 运行。"
    } else {
        $escapedScript = $comfyScript.Replace("'", "''")
        $escapedApi = $comfyApi.Replace("'", "''")
        $comfyCommand = "& '$escapedScript' -ApiBase '$escapedApi'"
        $encodedComfy = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($comfyCommand))
        Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoExit -ExecutionPolicy Bypass -EncodedCommand $encodedComfy" -WorkingDirectory $projectRoot -WindowStyle Normal | Out-Null
        Write-Host '已打开 ComfyUI 生图服务窗口，首次加载可能需要一分钟。'
    }
}

$webuiPort = 6099
if (Test-Path -LiteralPath $webuiPath) {
    $webuiPort = [int](Get-Content -LiteralPath $webuiPath -Raw -Encoding UTF8 | ConvertFrom-Json).port
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
    if ($Ubuntu) {
        $wslArguments = '-d Ubuntu --exec bash "' + $linuxScript + '" serve'
        Start-Process -FilePath 'wsl.exe' -ArgumentList $wslArguments -WorkingDirectory $projectRoot -WindowStyle Normal | Out-Null
    } else {
        $escapedRoot = $projectRoot.Replace("'", "''")
        $escapedExe = $botExe.Replace("'", "''")
        $botCommand = "Set-Location -LiteralPath '$escapedRoot'; & '$escapedExe' serve"
        $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($botCommand))
        Start-Process -FilePath 'powershell.exe' -ArgumentList "-NoExit -EncodedCommand $encodedCommand" -WorkingDirectory $projectRoot -WindowStyle Normal | Out-Null
    }
    Write-Host '已打开机器人窗口，运行日志会显示在那里。'
}

Write-Host '等待 NapCat 登录 QQ，并自动连接机器人（最多 10 分钟）...'
$deadline = (Get-Date).AddMinutes(10)
$lastError = ''
while ((Get-Date) -lt $deadline) {
    try {
        if (-not (Test-Path -LiteralPath $webuiPath)) { throw '等待 NapCat WebUI 启动' }
        $webui = Get-Content -LiteralPath $webuiPath -Raw -Encoding UTF8 | ConvertFrom-Json
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
        Write-Host 'QQ 登录和 OneBot 连接已配置。保持 NapCat、机器人和 ComfyUI 窗口打开即可聊天和生成图片。'
        exit 0
    } catch {
        $lastError = $_.Exception.Message
        Start-Sleep -Seconds 3
    }
}

throw "等待 QQ 登录超时：$lastError。扫码完成后可以重新运行 .\start.cmd。"
