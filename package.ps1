$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dist = Join-Path $projectRoot 'dist'
$archive = Join-Path $dist 'qq-aibot.zip'
$stageParent = Join-Path $env:TEMP ("qq-aibot-release-{0}" -f [guid]::NewGuid().ToString('N'))
$stage = Join-Path $stageParent 'qq-aibot'
$sourceStage = Join-Path $stage 'src\qq_aibot'

New-Item -ItemType Directory -Path $sourceStage -Force | Out-Null
New-Item -ItemType Directory -Path $dist -Force | Out-Null

$files = @('README.md', 'LICENSE', 'pyproject.toml', '.env.example', 'install.py', 'start.py', 'install.cmd', 'install.ps1', 'start.cmd', 'start.ps1', 'start-comfy.ps1', 'start-ubuntu.cmd', 'start-ubuntu.ps1', 'start.sh', 'install.sh')
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $stage
}

Get-ChildItem -LiteralPath (Join-Path $projectRoot 'src\qq_aibot') -File -Filter '*.py' |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $sourceStage }
Copy-Item -LiteralPath (Join-Path $projectRoot 'src\qq_aibot\workflows') -Destination $sourceStage -Recurse
Copy-Item -LiteralPath (Join-Path $projectRoot 'src\qq_aibot\stickers') -Destination $sourceStage -Recurse

Compress-Archive -LiteralPath $stage -DestinationPath $archive -Force
Copy-Item -LiteralPath $archive -Destination (Join-Path $dist 'qq-aibot-windows.zip') -Force
Write-Host "已生成安装包：$archive"
Write-Host '安装包只含公开源码和安装脚本，不含 API Key、QQ 登录态或聊天数据。'
