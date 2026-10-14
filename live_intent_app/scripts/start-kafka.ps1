[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $PSScriptRoot "..\tools")
)

$ErrorActionPreference = "Stop"
$homeFile = Join-Path $InstallRoot "kafka-home.txt"
$configFile = Join-Path $InstallRoot "kafka-config.txt"

if (-not (Test-Path $homeFile) -or -not (Test-Path $configFile)) {
    throw "Kafka is not configured. Run .\scripts\setup-openjdk-kafka.ps1 first."
}

$kafkaHome = (Get-Content $homeFile -Raw).Trim()
$serverConfig = (Get-Content $configFile -Raw).Trim()
$startCommand = Join-Path $kafkaHome "bin\windows\kafka-server-start.bat"

Write-Host "Starting Kafka on localhost:9092. Press Ctrl+C to stop it."
& $startCommand $serverConfig
exit $LASTEXITCODE
