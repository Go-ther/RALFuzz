param(
    [ValidateSet("full", "seed", "mutation")]
    [string]$Mode = "full",

    [string]$ApiDir = "",
    [string]$RuntimeRoot = "",
    [string]$TestApi = "all",
    [string]$ApiNameRegex = "",
    [string]$Compiler = "gcc",

    [string]$SeedBaseUrl = "http://localhost:11434",
    [string]$SeedApiKey = "ollama",
    [string]$SeedModel = "deepseek-v3.2:cloud",
    [string]$SeedEndpointMode = "ollama",
    [int]$SeedSamplesPerApi = 12,
    [int]$SeedTargetValidPerApi = 8,
    [switch]$NoSeedRiskCard,

    [string]$MutationProvider = "openai_compatible",
    [string]$MutationBaseUrl = "http://localhost:11434/v1",
    [string]$MutationApiKey = "ollama",
    [string]$MutationModel = "qwen3-coder:480b-cloud",
    [int]$MutationMaxValid = 50,
    [int]$MutationBatchSize = 4,
    [int]$MutationTimeout = 1200,

    [switch]$DryRun,
    [switch]$Resume,
    [switch]$EnableSanitizer,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RestArgs
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$pythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

if (-not $ApiDir) {
    throw "ApiDir is required. Pass -ApiDir <path-to-target-library>."
}
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $repoRoot "runtime_data"
}

$argsList = @(
    "--mode", $Mode,
    "--api-dir", $ApiDir,
    "--runtime-root", $RuntimeRoot,
    "--api", $TestApi,
    "--compiler", $Compiler,
    "--seed-base-url", $SeedBaseUrl,
    "--seed-api-key", $SeedApiKey,
    "--seed-model", $SeedModel,
    "--seed-endpoint-mode", $SeedEndpointMode,
    "--seed-samples-per-api", $SeedSamplesPerApi,
    "--seed-target-valid-per-api", $SeedTargetValidPerApi,
    "--mutation-llm-provider", $MutationProvider,
    "--mutation-max-valid", $MutationMaxValid,
    "--mutation-batch-size", $MutationBatchSize,
    "--mutation-timeout", $MutationTimeout
)

if ($ApiNameRegex) {
    $argsList += @("--api-name-regex", $ApiNameRegex)
}

if ($NoSeedRiskCard) {
    $argsList += "--no-seed-risk-card"
}

if ($MutationProvider -ne "mock") {
    $argsList += @("--mutation-model", $MutationModel)
}

if ($MutationProvider -in @("openai_compatible", "deepseek")) {
    $argsList += @(
        "--mutation-api-base", $MutationBaseUrl,
        "--mutation-api-key", $MutationApiKey
    )
}

if ($DryRun) {
    $argsList += "--dry-run"
}
if ($Resume) {
    $argsList += "--resume"
}
if ($EnableSanitizer) {
    $argsList += "--enable-sanitizer"
}
if ($RestArgs) {
    $argsList += $RestArgs
}

& $pythonBin (Join-Path $scriptDir "run_full_pipeline.py") @argsList
exit $LASTEXITCODE
