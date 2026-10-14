[CmdletBinding()]
param(
    [string]$KafkaVersion = "4.3.1",
    [string]$ScalaVersion = "2.13",
    [string]$InstallRoot = (Join-Path $PSScriptRoot "..\tools")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-JavaMajorVersion {
    $output = (& java -version 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        return 0
    }
    if ($output -match 'version "(?<major>\d+)') {
        return [int]$Matches.major
    }
    return 0
}

function Install-OpenJdk {
    $java = Get-Command java -ErrorAction SilentlyContinue
    if ($java -and (Get-JavaMajorVersion) -ge 17) {
        Write-Host "Compatible Java already installed: $($java.Source)"
        return (Split-Path (Split-Path $java.Source -Parent) -Parent)
    }

    $jdkRoot = Join-Path $InstallRoot "openjdk-21"
    $installedJava = Join-Path $jdkRoot "bin\java.exe"
    if (-not (Test-Path $installedJava)) {
        New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
        $apiUrl = "https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse"
        Write-Host "Finding the latest Eclipse Temurin OpenJDK 21 release..."
        $release = Invoke-RestMethod -Uri $apiUrl
        $package = $release[0].binary.package
        if (-not $package.link -or -not $package.checksum) {
            throw "Adoptium did not return a downloadable JDK package."
        }

        $archivePath = Join-Path ([IO.Path]::GetTempPath()) "live-intent-openjdk21.zip"
        Write-Host "Downloading Eclipse Temurin OpenJDK 21..."
        Invoke-WebRequest -Uri $package.link -OutFile $archivePath
        $actualHash = (Get-FileHash $archivePath -Algorithm SHA256).Hash
        if ($actualHash -ne $package.checksum.ToUpperInvariant()) {
            throw "OpenJDK download checksum verification failed."
        }

        $extractRoot = Join-Path $InstallRoot "openjdk-21-extracted"
        if (Test-Path $extractRoot) {
            throw "Incomplete JDK setup directory exists: $extractRoot"
        }
        Expand-Archive -Path $archivePath -DestinationPath $extractRoot
        $javaFile = Get-ChildItem $extractRoot -Filter java.exe -Recurse |
            Where-Object { $_.FullName -match '\\bin\\java\.exe$' } |
            Select-Object -First 1
        if (-not $javaFile) {
            throw "The downloaded OpenJDK archive does not contain java.exe."
        }
        $extractedHome = Split-Path (Split-Path $javaFile.FullName -Parent) -Parent
        Move-Item -LiteralPath $extractedHome -Destination $jdkRoot
    }

    $env:JAVA_HOME = $jdkRoot
    $env:Path = "$jdkRoot\bin;$env:Path"
    [Environment]::SetEnvironmentVariable("JAVA_HOME", $jdkRoot, "User")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*%JAVA_HOME%\bin*") {
        $newUserPath = "%JAVA_HOME%\bin;$userPath".TrimEnd(";")
        [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
    }
    Write-Host "Installed Eclipse Temurin OpenJDK 21: $jdkRoot"
    return $jdkRoot
}

function Install-Kafka {
    $archiveName = "kafka_${ScalaVersion}-${KafkaVersion}.tgz"
    $downloadBase = "https://downloads.apache.org/kafka/$KafkaVersion"
    $archiveUrl = "$downloadBase/$archiveName"
    $checksumUrl = "$archiveUrl.sha512"
    $kafkaHome = Join-Path $InstallRoot "kafka_${ScalaVersion}-${KafkaVersion}"

    if (Test-Path (Join-Path $kafkaHome "bin\windows\kafka-server-start.bat")) {
        Write-Host "Kafka already installed: $kafkaHome"
        return $kafkaHome
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    $downloadDirectory = Join-Path ([IO.Path]::GetTempPath()) "live-intent-kafka"
    New-Item -ItemType Directory -Path $downloadDirectory -Force | Out-Null
    $archivePath = Join-Path $downloadDirectory $archiveName
    $checksumPath = "$archivePath.sha512"

    Write-Host "Downloading Apache Kafka $KafkaVersion..."
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath
    Invoke-WebRequest -Uri $checksumUrl -OutFile $checksumPath

    $checksumText = Get-Content $checksumPath -Raw
    if ($checksumText -notmatch '(?i)\b([0-9a-f]{128})\b') {
        throw "The Kafka SHA-512 checksum file is not valid."
    }
    $expectedHash = $Matches[1].ToUpperInvariant()
    $actualHash = (Get-FileHash $archivePath -Algorithm SHA512).Hash
    if ($actualHash -ne $expectedHash) {
        throw "Kafka download checksum verification failed."
    }

    Write-Host "Extracting Kafka to $InstallRoot..."
    & tar -xzf $archivePath -C $InstallRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Kafka extraction failed with exit code $LASTEXITCODE."
    }

    return $kafkaHome
}

function Initialize-KafkaStorage {
    param([string]$KafkaHome)

    $baseConfig = Join-Path $KafkaHome "config\server.properties"
    $localConfig = Join-Path $KafkaHome "config\live-intent-server.properties"
    $dataDirectory = Join-Path $InstallRoot "kafka-data"
    $dataDirectoryForConfig = $dataDirectory.Replace("\", "/")

    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    $configuration = Get-Content $baseConfig
    $configuration = $configuration -replace '^log\.dirs=.*$', "log.dirs=$dataDirectoryForConfig"
    Set-Content -Path $localConfig -Value $configuration -Encoding UTF8

    if (Test-Path (Join-Path $dataDirectory "meta.properties")) {
        Write-Host "Kafka storage is already initialized: $dataDirectory"
        return $localConfig
    }

    $storageCommand = Join-Path $KafkaHome "bin\windows\kafka-storage.bat"
    $clusterId = (& $storageCommand random-uuid | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $clusterId) {
        throw "Kafka could not generate a cluster ID."
    }

    Write-Host "Formatting Kafka KRaft storage..."
    & $storageCommand format --standalone -t $clusterId -c $localConfig
    if ($LASTEXITCODE -ne 0) {
        throw "Kafka storage formatting failed with exit code $LASTEXITCODE."
    }

    return $localConfig
}

$javaHome = Install-OpenJdk
$env:JAVA_HOME = $javaHome
$env:Path = "$javaHome\bin;$env:Path"
$kafkaHome = Install-Kafka
$configPath = Initialize-KafkaStorage -KafkaHome $kafkaHome

Set-Content -Path (Join-Path $InstallRoot "kafka-home.txt") -Value $kafkaHome
Set-Content -Path (Join-Path $InstallRoot "kafka-config.txt") -Value $configPath

Write-Host ""
Write-Host "Java and Kafka setup completed."
Write-Host "Kafka home: $kafkaHome"
Write-Host "Start Kafka: .\scripts\start-kafka.ps1"
Write-Host "Create topic: .\scripts\create-kafka-topic.ps1"
