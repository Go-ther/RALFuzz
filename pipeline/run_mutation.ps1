$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $scriptDir "run_full_pipeline.ps1"

& $launcher -Mode mutation @args
exit $LASTEXITCODE
