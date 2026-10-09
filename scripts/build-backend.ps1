[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DistDir = Join-Path $RepoRoot "dist"
$WorkDir = Join-Path $RepoRoot "backend\build\pyinstaller"
$SpecDir = Join-Path $RepoRoot "backend\build"
$Entry = Join-Path $PSScriptRoot "backend_entry.py"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Create the development environment with scripts\install-backend.ps1 first."
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $SpecDir | Out-Null

& $VenvPython -m pip install -e "$RepoRoot\backend[build]"
if ($LASTEXITCODE -ne 0) {
    throw "Installing backend build dependencies failed with exit code $LASTEXITCODE"
}
& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name ZoteroQuickRead `
    --distpath $DistDir `
    --workpath $WorkDir `
    --specpath $SpecDir `
    --collect-data zotero_quick_read `
    --collect-submodules uvicorn `
    --hidden-import socks `
    $Entry
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Exe = Join-Path $DistDir "ZoteroQuickRead.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "PyInstaller did not create ZoteroQuickRead.exe"
}

$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Exe).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $DistDir "ZoteroQuickRead.exe.sha256") `
    -Value "$Hash  ZoteroQuickRead.exe" -Encoding ascii
Write-Host "Built $Exe"
