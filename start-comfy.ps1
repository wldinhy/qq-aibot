param(
    [string]$ApiBase = 'http://127.0.0.1:8188',
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$comfyInstall = 'D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI'
$serviceProfilePath = Join-Path $projectRoot '.local\services-windows.json'
$serviceProfile = $null
if (Test-Path -LiteralPath $serviceProfilePath) {
    $serviceProfile = Get-Content -LiteralPath $serviceProfilePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($serviceProfile.comfy_root) { $comfyInstall = [string]$serviceProfile.comfy_root }
}
if ($env:COMFYUI_HOME) { $comfyInstall = $env:COMFYUI_HOME }
$comfyRoot = Join-Path $comfyInstall 'ComfyUI'
if (Test-Path -LiteralPath (Join-Path $comfyInstall 'main.py')) { $comfyRoot = $comfyInstall }
$comfyPython = Join-Path $comfyRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath (Join-Path $comfyRoot '.venv-windows\Scripts\python.exe')) {
    $comfyPython = Join-Path $comfyRoot '.venv-windows\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $comfyPython)) {
    $comfyPython = Join-Path $comfyInstall 'standalone-env\python.exe'
}
if ($serviceProfile -and $serviceProfile.comfy_python -and -not $env:COMFYUI_HOME) {
    $comfyPython = [string]$serviceProfile.comfy_python
}
$comfyMain = Join-Path $comfyRoot 'main.py'
$modelDirectory = Join-Path $comfyRoot 'models'
if (-not (Test-Path -LiteralPath $comfyPython) -or -not (Test-Path -LiteralPath $comfyMain)) {
    throw "找不到 ComfyUI 安装：$comfyInstall。可设置环境变量 COMFYUI_HOME 为安装目录。"
}
$uri = [Uri]$ApiBase
if ($uri.Scheme -ne 'http' -or $uri.Host -notin @('127.0.0.1', 'localhost')) {
    throw '自动启动 ComfyUI 需要 image_generation.api_base 使用本机 http://127.0.0.1:端口。'
}
if ($CheckOnly) {
    Write-Host "ComfyUI 启动路径有效：$comfyMain"
    return
}

$runtime = Join-Path $projectRoot 'data\comfyui'
$userDirectory = Join-Path $runtime 'user'
$tempDirectory = Join-Path $runtime 'temp'
New-Item -ItemType Directory -Force -Path $userDirectory, $tempDirectory | Out-Null
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HOME = Join-Path $runtime 'cache\hf'
$env:TORCH_HOME = Join-Path $runtime 'cache\torch'
$databaseUrl = 'sqlite:///' + (Join-Path $userDirectory 'comfyui.db').Replace('\', '/')
$comfyArguments = @(
    '-B', $comfyMain, '--listen', '127.0.0.1', '--port', [string]$uri.Port,
    '--base-directory', $runtime, '--models-directory', $modelDirectory,
    '--user-directory', $userDirectory, '--temp-directory', $tempDirectory,
    '--database-url', $databaseUrl, '--disable-all-custom-nodes', '--disable-api-nodes'
)
if ($serviceProfile -and $serviceProfile.torch_backend -eq 'cpu') {
    $comfyArguments += '--cpu'
}
# Reuse shared models as well as the checkpoint in this ComfyUI installation.
$sharedModels = 'D:\Comfy-Desktop\ComfyUI-Shared\models'
if (Test-Path -LiteralPath $sharedModels) {
    $modelConfig = Join-Path $runtime 'extra-model-paths.yaml'
    $sharedPath = $sharedModels.Replace('\', '/').Replace("'", "''")
    $yaml = "qq_aibot_shared:`n  base_path: '$sharedPath'`n  checkpoints: checkpoints`n  loras: loras`n  vae: vae`n  embeddings: embeddings`n"
    [IO.File]::WriteAllText($modelConfig, $yaml, (New-Object Text.UTF8Encoding($false)))
    $comfyArguments += @('--extra-model-paths-config', $modelConfig)
}
if ($serviceProfile -and $serviceProfile.checkpoint_directory) {
    $checkpointConfig = Join-Path $runtime 'checkpoint-path.yaml'
    $checkpointPath = ([string]$serviceProfile.checkpoint_directory).Replace('\', '/').Replace("'", "''")
    $checkpointYaml = "qq_aibot_checkpoint:`n  checkpoints: '$checkpointPath'`n"
    [IO.File]::WriteAllText($checkpointConfig, $checkpointYaml, (New-Object Text.UTF8Encoding($false)))
    $comfyArguments += @('--extra-model-paths-config', $checkpointConfig)
}
Set-Location -LiteralPath $projectRoot
Write-Host "ComfyUI 本地生图服务：$ApiBase；按 Ctrl+C 停止。"
& $comfyPython @comfyArguments
if ($LASTEXITCODE -ne 0) { throw "ComfyUI 启动失败（退出码 $LASTEXITCODE），请查看上方错误。" }
