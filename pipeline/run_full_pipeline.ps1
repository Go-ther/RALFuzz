param(
    [ValidateSet("full", "seed", "mutation")]
    [string]$Mode = "full",

    [string]$ApiDir = "",
    [string]$RuntimeRoot = "",
    [string]$TestApi = "cJSON_Parse",
    [string]$ApiNameRegex = "^cJSON_Parse$",
    [string]$Compiler = "clang",
    [string]$CoverageTool = "llvm-cov gcov",

    [string]$SeedBaseUrl = "https://api.deepseek.com",
    [string]$SeedApiKey = "",
    [string]$SeedModel = "deepseek-v4-flash",
    [string]$SeedEndpointMode = "chat",
    [int]$SeedSamplesPerApi = 8,
    [int]$SeedTargetValidPerApi = 6,
    [int]$SeedNetworkRetries = 2,
    [double]$SeedNetworkRetryBackoffSec = 2.0,
    [switch]$NoSeedRiskCard,

    [string]$MutationProvider = "openai_compatible",
    [string]$MutationBaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [string]$MutationApiKey = "",
    [string]$MutationModel = "qwen3-coder-next",
    [int]$MutationMaxValid = 30,
    [int]$MutationBatchSize = 4,
    [int]$MutationTimeout = 1200,

    [switch]$DryRun,
    [switch]$Resume,
    [switch]$EnableSanitizer,
    [switch]$DisableSanitizer,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RestArgs
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$pythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
$launcherBoundParameters = @{} + $PSBoundParameters

function Get-EnvString {
    param(
        [string]$ParamName,
        [string]$EnvName,
        [string]$CurrentValue
    )
    if (-not $launcherBoundParameters.ContainsKey($ParamName)) {
        $value = [Environment]::GetEnvironmentVariable($EnvName)
        if ($null -ne $value -and $value -ne "") {
            return $value
        }
    }
    return $CurrentValue
}

function Get-EnvInt {
    param(
        [string]$ParamName,
        [string]$EnvName,
        [int]$CurrentValue
    )
    if (-not $launcherBoundParameters.ContainsKey($ParamName)) {
        $value = [Environment]::GetEnvironmentVariable($EnvName)
        if ($null -ne $value -and $value -ne "") {
            return [int]$value
        }
    }
    return $CurrentValue
}

function Get-EnvDouble {
    param(
        [string]$ParamName,
        [string]$EnvName,
        [double]$CurrentValue
    )
    if (-not $launcherBoundParameters.ContainsKey($ParamName)) {
        $value = [Environment]::GetEnvironmentVariable($EnvName)
        if ($null -ne $value -and $value -ne "") {
            return [double]$value
        }
    }
    return $CurrentValue
}

function Get-EnvSwitch {
    param(
        [string]$ParamName,
        [string]$EnvName,
        [bool]$CurrentValue
    )
    if (-not $launcherBoundParameters.ContainsKey($ParamName)) {
        $value = [Environment]::GetEnvironmentVariable($EnvName)
        if ($null -ne $value -and $value -ne "") {
            return $value -match '^(1|true|yes|on)$'
        }
    }
    return $CurrentValue
}

$Mode = Get-EnvString -ParamName "Mode" -EnvName "MODE" -CurrentValue $Mode
$ApiDir = Get-EnvString -ParamName "ApiDir" -EnvName "API_DIR" -CurrentValue $ApiDir
$RuntimeRoot = Get-EnvString -ParamName "RuntimeRoot" -EnvName "RUNTIME_ROOT" -CurrentValue $RuntimeRoot
$TestApi = Get-EnvString -ParamName "TestApi" -EnvName "TEST_API" -CurrentValue $TestApi
$ApiNameRegex = Get-EnvString -ParamName "ApiNameRegex" -EnvName "API_NAME_REGEX" -CurrentValue $ApiNameRegex
$Compiler = Get-EnvString -ParamName "Compiler" -EnvName "COMPILER" -CurrentValue $Compiler
$CoverageTool = Get-EnvString -ParamName "CoverageTool" -EnvName "COVERAGE_TOOL" -CurrentValue $CoverageTool
$SeedBaseUrl = Get-EnvString -ParamName "SeedBaseUrl" -EnvName "SEED_BASE_URL" -CurrentValue $SeedBaseUrl
$SeedApiKey = Get-EnvString -ParamName "SeedApiKey" -EnvName "SEED_API_KEY" -CurrentValue $SeedApiKey
$SeedModel = Get-EnvString -ParamName "SeedModel" -EnvName "SEED_MODEL" -CurrentValue $SeedModel
$SeedEndpointMode = Get-EnvString -ParamName "SeedEndpointMode" -EnvName "SEED_ENDPOINT_MODE" -CurrentValue $SeedEndpointMode
$SeedSamplesPerApi = Get-EnvInt -ParamName "SeedSamplesPerApi" -EnvName "SEED_SAMPLES_PER_API" -CurrentValue $SeedSamplesPerApi
$SeedTargetValidPerApi = Get-EnvInt -ParamName "SeedTargetValidPerApi" -EnvName "SEED_TARGET_VALID_PER_API" -CurrentValue $SeedTargetValidPerApi
$SeedNetworkRetries = Get-EnvInt -ParamName "SeedNetworkRetries" -EnvName "SEED_NETWORK_RETRIES" -CurrentValue $SeedNetworkRetries
$SeedNetworkRetryBackoffSec = Get-EnvDouble -ParamName "SeedNetworkRetryBackoffSec" -EnvName "SEED_NETWORK_RETRY_BACKOFF_SEC" -CurrentValue $SeedNetworkRetryBackoffSec
$MutationProvider = Get-EnvString -ParamName "MutationProvider" -EnvName "MUTATION_PROVIDER" -CurrentValue $MutationProvider
$MutationBaseUrl = Get-EnvString -ParamName "MutationBaseUrl" -EnvName "MUTATION_BASE_URL" -CurrentValue $MutationBaseUrl
$MutationApiKey = Get-EnvString -ParamName "MutationApiKey" -EnvName "MUTATION_API_KEY" -CurrentValue $MutationApiKey
$MutationModel = Get-EnvString -ParamName "MutationModel" -EnvName "MUTATION_MODEL" -CurrentValue $MutationModel
$MutationMaxValid = Get-EnvInt -ParamName "MutationMaxValid" -EnvName "MUTATION_MAX_VALID" -CurrentValue $MutationMaxValid
$MutationBatchSize = Get-EnvInt -ParamName "MutationBatchSize" -EnvName "MUTATION_BATCH_SIZE" -CurrentValue $MutationBatchSize
$MutationTimeout = Get-EnvInt -ParamName "MutationTimeout" -EnvName "MUTATION_TIMEOUT" -CurrentValue $MutationTimeout
$NoSeedRiskCard = Get-EnvSwitch -ParamName "NoSeedRiskCard" -EnvName "NO_SEED_RISK_CARD" -CurrentValue $NoSeedRiskCard
$DryRun = Get-EnvSwitch -ParamName "DryRun" -EnvName "DRY_RUN" -CurrentValue $DryRun
$Resume = Get-EnvSwitch -ParamName "Resume" -EnvName "RESUME" -CurrentValue $Resume

if (-not $ApiDir) {
    $ApiDir = Join-Path $repoRoot "api\\cJSON"
}
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $repoRoot "runtime_data\\illustrative_cjson_parse_v1"
}
if (-not $SeedApiKey) {
    $SeedApiKey = if ($env:DEEPSEEK_API_KEY) {
        $env:DEEPSEEK_API_KEY
    } elseif ($env:OPENAI_API_KEY) {
        $env:OPENAI_API_KEY
    } else {
        ""
    }
}
if (-not $MutationApiKey) {
    $MutationApiKey = if ($env:DASHSCOPE_API_KEY) {
        $env:DASHSCOPE_API_KEY
    } elseif ($env:QWEN_API_KEY) {
        $env:QWEN_API_KEY
    } elseif ($env:LLM_API_KEY) {
        $env:LLM_API_KEY
    } else {
        ""
    }
}
$runSeedGeneration = $Mode -in @("full", "seed")
$runMutation = $Mode -in @("full", "mutation")
if ($runSeedGeneration -and ($SeedBaseUrl -like "https://api.deepseek.com*") -and (-not $SeedApiKey)) {
    throw "Missing seed API key. Set SEED_API_KEY (or DEEPSEEK_API_KEY / OPENAI_API_KEY) or pass -SeedApiKey <key>."
}
if ($runMutation -and ($MutationProvider -in @("openai_compatible", "deepseek")) -and (-not $MutationApiKey)) {
    throw "Missing mutation API key. Set MUTATION_API_KEY (or DASHSCOPE_API_KEY / QWEN_API_KEY / LLM_API_KEY) or pass -MutationApiKey <key>."
}
if ($launcherBoundParameters.ContainsKey("EnableSanitizer") -and $launcherBoundParameters.ContainsKey("DisableSanitizer")) {
    throw "Use only one of -EnableSanitizer and -DisableSanitizer."
}

$argsList = @(
    "--mode", $Mode,
    "--api-dir", $ApiDir,
    "--runtime-root", $RuntimeRoot,
    "--api", $TestApi,
    "--compiler", $Compiler,
    "--seed-base-url", $SeedBaseUrl,
    "--seed-model", $SeedModel,
    "--seed-endpoint-mode", $SeedEndpointMode,
    "--seed-samples-per-api", $SeedSamplesPerApi,
    "--seed-target-valid-per-api", $SeedTargetValidPerApi,
    "--seed-network-retries", $SeedNetworkRetries,
    "--seed-network-retry-backoff-sec", $SeedNetworkRetryBackoffSec,
    "--mutation-llm-provider", $MutationProvider,
    "--mutation-max-valid", $MutationMaxValid,
    "--mutation-batch-size", $MutationBatchSize,
    "--mutation-timeout", $MutationTimeout,
    "--coverage-tool", $CoverageTool
)

if ($ApiNameRegex) {
    $argsList += @("--api-name-regex", $ApiNameRegex)
}
if ($SeedApiKey) {
    $argsList += @("--seed-api-key", $SeedApiKey)
}

if ($NoSeedRiskCard) {
    $argsList += "--no-seed-risk-card"
}

if ($MutationProvider -ne "mock") {
    $argsList += @("--mutation-model", $MutationModel)
}

if ($MutationProvider -in @("openai_compatible", "deepseek")) {
    $argsList += @("--mutation-api-base", $MutationBaseUrl)
    if ($MutationApiKey) {
        $argsList += @("--mutation-api-key", $MutationApiKey)
    }
}

if ($DryRun) {
    $argsList += "--dry-run"
}
if ($Resume) {
    $argsList += "--resume"
}
$enableSanitizerFlag = $true
if ($launcherBoundParameters.ContainsKey("DisableSanitizer")) {
    $enableSanitizerFlag = $false
} elseif ($launcherBoundParameters.ContainsKey("EnableSanitizer")) {
    $enableSanitizerFlag = [bool]$EnableSanitizer
} else {
    $disableSanitizerEnv = [Environment]::GetEnvironmentVariable("DISABLE_SANITIZER")
    if ($null -ne $disableSanitizerEnv -and $disableSanitizerEnv -ne "" -and $disableSanitizerEnv -match '^(1|true|yes|on)$') {
        $enableSanitizerFlag = $false
    } else {
        $enableSanitizerEnv = [Environment]::GetEnvironmentVariable("ENABLE_SANITIZER")
        if ($null -ne $enableSanitizerEnv -and $enableSanitizerEnv -ne "") {
            $enableSanitizerFlag = $enableSanitizerEnv -match '^(1|true|yes|on)$'
        }
    }
}
if ($enableSanitizerFlag) {
    $argsList += "--enable-sanitizer"
}
if ($RestArgs) {
    $argsList += $RestArgs
}

& $pythonBin (Join-Path $scriptDir "run_full_pipeline.py") @argsList
exit $LASTEXITCODE
