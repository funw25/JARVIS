param(
    [string]$Proxy = '',
    [switch]$VerifyOnly
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'candidate-inputs.json') -Raw | ConvertFrom-Json
$base = Join-Path $repo '.jarvis-driver-g1'
$destination = Join-Path $base 'downloads'
foreach ($directory in @($base, $destination)) {
    if (-not (Test-Path -LiteralPath $directory)) {
        if ($VerifyOnly) { throw "Missing candidate directory: $directory" }
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    $item = Get-Item -LiteralPath $directory -Force
    if (-not $item.PSIsContainer -or $item.LinkType) { throw "Unsafe candidate directory: $directory" }
    if (-not (Resolve-Path -LiteralPath $directory).Path.StartsWith($repo + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Candidate directory escaped project: $directory"
    }
}

foreach ($kind in @('source', 'wheel', 'sqlite3mc_license')) {
    $entry = $lock.$kind
    $filename = [string]$entry.filename
    if (-not $filename -or [IO.Path]::GetFileName($filename) -ne $filename) { throw "Unsafe $kind filename" }
    $url = [string]$entry.url
    $allowed = $url.StartsWith('https://files.pythonhosted.org/', [StringComparison]::Ordinal) -or
               $url.StartsWith('https://raw.githubusercontent.com/utelle/SQLite3MultipleCiphers/', [StringComparison]::Ordinal)
    if (-not $allowed) {
        throw "Unapproved $kind download host"
    }
    $target = Join-Path $destination $filename
    if (-not (Test-Path -LiteralPath $target)) {
        if ($VerifyOnly) { throw "Missing $kind archive: $target" }
        $partial = Join-Path $destination ($filename + '.' + [Guid]::NewGuid().ToString('N') + '.partial')
        try {
            $request = @{ Uri = [string]$entry.url; OutFile = $partial; TimeoutSec = 180 }
            if ($Proxy) { $request.Proxy = $Proxy }
            Invoke-WebRequest @request
            if ((Get-Item -LiteralPath $partial).Length -ne [long]$entry.size -or
                (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$entry.sha256) {
                throw "$kind archive size or hash mismatch"
            }
            Move-Item -LiteralPath $partial -Destination $target -ErrorAction Stop
        } finally {
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
        }
    }
    $item = Get-Item -LiteralPath $target -Force
    if ($item.PSIsContainer -or $item.LinkType) { throw "Unsafe $kind archive" }
    if ($item.Length -ne [long]$entry.size -or
        (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$entry.sha256) {
        throw "$kind archive size or hash mismatch"
    }
    Write-Host "$($kind.ToUpperInvariant())_ARCHIVE=PASS file=$filename"
}
Write-Host 'G1_SOURCE_TO_WHEEL_BUILD=UNVERIFIED'
