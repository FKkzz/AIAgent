[CmdletBinding()]
param(
    [ValidateSet("error", "warning", "info", "debug")]
    [string]$LogLevel = "info"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Packaged = Join-Path $RepoRoot "dist\ZoteroQuickRead.exe"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $Packaged) {
    & $Packaged serve --log-level $LogLevel
    exit $LASTEXITCODE
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Backend is not installed. Run scripts\install-backend.ps1 first."
}

& $VenvPython -m zotero_quick_read serve --log-level $LogLevel
exit $LASTEXITCODE
