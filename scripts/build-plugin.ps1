[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$pluginDir = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "plugin"))
$distDir = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "dist"))

if (-not $pluginDir.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Plugin directory resolved outside the repository: $pluginDir"
}
if (-not $distDir.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Distribution directory resolved outside the repository: $distDir"
}

$requiredFiles = @(
    "manifest.json",
    "bootstrap.js",
    "prefs.js",
    "locale/en-US/zotero-quick-read.ftl",
    "locale/zh-CN/zotero-quick-read.ftl"
)
foreach ($relativePath in $requiredFiles) {
    $candidate = Join-Path $pluginDir $relativePath
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Required plugin file is missing: $relativePath"
    }
}

$manifest = Get-Content -LiteralPath (Join-Path $pluginDir "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.version -ne "2.0.2") {
    throw "Expected plugin version 2.0.2, found '$($manifest.version)'"
}
if ($manifest.applications.zotero.strict_min_version -ne "9.0.6" -or
    $manifest.applications.zotero.strict_max_version -ne "9.0.*") {
    throw "manifest.json must target Zotero 9.0.6 through 9.0.*"
}

New-Item -ItemType Directory -Path $distDir -Force | Out-Null
$archiveBase = "zotero-quick-read-$($manifest.version)"
$temporaryZip = Join-Path $distDir "$archiveBase.zip"
$outputXpi = Join-Path $distDir "$archiveBase.xpi"

foreach ($path in @($temporaryZip, $outputXpi)) {
    $resolvedCandidate = [System.IO.Path]::GetFullPath($path)
    if (-not $resolvedCandidate.StartsWith($distDir, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a build artifact outside dist: $resolvedCandidate"
    }
    if (Test-Path -LiteralPath $resolvedCandidate) {
        Remove-Item -LiteralPath $resolvedCandidate -Force
    }
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zipStream = [System.IO.File]::Open(
    $temporaryZip,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
)
$buildZip = [System.IO.Compression.ZipArchive]::new(
    $zipStream,
    [System.IO.Compression.ZipArchiveMode]::Create,
    $false
)
$archiveTimestamp = [System.DateTimeOffset]::new(
    2000, 1, 1, 0, 0, 0, [System.TimeSpan]::Zero
)
try {
    # Zotero discovers Fluent resources by enumerating locale directories in the
    # XPI. Compress-Archive omits directory entries, which makes valid .ftl files
    # invisible to Zotero 9.0.6 even though the files themselves are present.
    foreach ($directory in Get-ChildItem -LiteralPath $pluginDir -Directory -Recurse | Sort-Object FullName) {
        $relativeDirectory = $directory.FullName.Substring($pluginDir.Length).TrimStart([char[]]"\/")
        $relativeDirectory = $relativeDirectory.Replace("\", "/") + "/"
        $directoryEntry = $buildZip.CreateEntry($relativeDirectory)
        $directoryEntry.LastWriteTime = $archiveTimestamp
    }
    foreach ($file in Get-ChildItem -LiteralPath $pluginDir -File -Recurse | Sort-Object FullName) {
        $relativeFile = $file.FullName.Substring($pluginDir.Length).TrimStart([char[]]"\/")
        $relativeFile = $relativeFile.Replace("\", "/")
        $fileEntry = $buildZip.CreateEntry(
            $relativeFile,
            [System.IO.Compression.CompressionLevel]::Optimal
        )
        $fileEntry.LastWriteTime = $archiveTimestamp
        $sourceStream = [System.IO.File]::OpenRead($file.FullName)
        $entryStream = $fileEntry.Open()
        try {
            $sourceStream.CopyTo($entryStream)
        }
        finally {
            $entryStream.Dispose()
            $sourceStream.Dispose()
        }
    }
}
finally {
    $buildZip.Dispose()
    $zipStream.Dispose()
}

$zip = [System.IO.Compression.ZipFile]::OpenRead($temporaryZip)
try {
    $entries = @($zip.Entries | ForEach-Object { $_.FullName.Replace("\", "/") })
    foreach ($requiredPath in $requiredFiles) {
        if ($entries -notcontains $requiredPath) {
            throw "Archive is missing root entry '$requiredPath'"
        }
    }
    foreach ($requiredDirectory in @("locale/", "locale/en-US/", "locale/zh-CN/")) {
        if ($entries -notcontains $requiredDirectory) {
            throw "Archive is missing directory entry '$requiredDirectory' required for Fluent discovery"
        }
    }
    if ($entries | Where-Object { $_ -like "plugin/*" -or $_ -match "^[A-Za-z]:" -or $_.StartsWith("/") }) {
        throw "Archive contains an invalid enclosing or absolute path"
    }
}
finally {
    $zip.Dispose()
}

Move-Item -LiteralPath $temporaryZip -Destination $outputXpi
$hash = (Get-FileHash -LiteralPath $outputXpi -Algorithm SHA256).Hash.ToLowerInvariant()
$hashFile = "$outputXpi.sha256"
Set-Content -LiteralPath $hashFile -Value "$hash  $([IO.Path]::GetFileName($outputXpi))" -Encoding ascii
Write-Output "Built $outputXpi"
Write-Output "SHA256 $hash"
