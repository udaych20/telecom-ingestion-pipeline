[CmdletBinding()]
param(
    [string]$Topic = "live-intent",
    [string]$BootstrapServer = "localhost:9092",
    [string]$InstallRoot = (Join-Path $PSScriptRoot "..\tools")
)

$ErrorActionPreference = "Stop"
$homeFile = Join-Path $InstallRoot "kafka-home.txt"

if (-not (Test-Path $homeFile)) {
    throw "Kafka is not installed. Run .\scripts\setup-openjdk-kafka.ps1 first."
}

$kafkaHome = (Get-Content $homeFile -Raw).Trim()
$topicsCommand = Join-Path $kafkaHome "bin\windows\kafka-topics.bat"

& $topicsCommand `
    --create `
    --if-not-exists `
    --topic $Topic `
    --bootstrap-server $BootstrapServer `
    --partitions 1 `
    --replication-factor 1

if ($LASTEXITCODE -ne 0) {
    throw "Kafka topic creation failed with exit code $LASTEXITCODE."
}

Write-Host "Kafka topic is ready: $Topic"
