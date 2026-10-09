[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "test.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot "build-backend.ps1")
exit $LASTEXITCODE
