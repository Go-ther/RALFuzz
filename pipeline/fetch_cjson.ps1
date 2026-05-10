param(
    [string]$Version = "v1.7.19",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot "api\cJSON"
}

$targetDir = [System.IO.Path]::GetFullPath($OutputDir)
$apiRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "api"))
if (-not $targetDir.StartsWith($apiRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDir must be under $apiRoot"
}

$archiveUrl = "https://github.com/DaveGamble/cJSON/archive/refs/tags/$Version.zip"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("ralfuzz-cjson-" + [System.Guid]::NewGuid().ToString("N"))
$zipPath = Join-Path $tempRoot "cjson.zip"
$extractDir = Join-Path $tempRoot "extract"

New-Item -ItemType Directory -Force -Path $tempRoot, $extractDir | Out-Null
try {
    Write-Host "Downloading cJSON $Version from $archiveUrl"
    Invoke-WebRequest -Uri $archiveUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $extractDir
    $sourceDir = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
    if (-not $sourceDir) {
        throw "Downloaded archive did not contain a source directory."
    }

    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    foreach ($name in @("cJSON.c", "cJSON.h", "cJSON_Utils.c", "cJSON_Utils.h")) {
        $src = Join-Path $sourceDir.FullName $name
        if (-not (Test-Path $src)) {
            throw "Missing expected file in cJSON archive: $name"
        }
        Copy-Item -Force -Path $src -Destination (Join-Path $targetDir $name)
    }

    Write-Host "Fetched cJSON $Version into $targetDir"
    Write-Host "cJSON is an external MIT-licensed target input from https://github.com/DaveGamble/cJSON"
} finally {
    Remove-Item -Recurse -Force -Path $tempRoot -ErrorAction SilentlyContinue
}
