[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Run scripts\install-backend.ps1 first."
}

& $Python -m pytest (Join-Path $RepoRoot "backend\tests")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m ruff check (Join-Path $RepoRoot "backend")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot "build-plugin.ps1")
