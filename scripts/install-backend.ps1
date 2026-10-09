[CmdletBinding()]
param(
    [switch]$Upgrade
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & python -m venv (Join-Path $RepoRoot ".venv")
}

if ($Upgrade) {
    & $VenvPython -m pip install --upgrade pip
}

& $VenvPython -m pip install -e (Join-Path $RepoRoot "backend")
& $VenvPython -m zotero_quick_read init

Write-Host "Backend installed. Start it with scripts\start-backend.ps1"
