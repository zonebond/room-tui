# Download a portable Windows C toolchain (clang) for oob-divzero ASan.
# Output: vendor\c-toolchain-windows\bin\clang.exe  (not committed to git)
#
# Default source: llvm-mingw (portable zip, clang + runtime).
# Override: -Url <zip-url>  -Force
param(
    [string]$Version = "20241217",
    [string]$Url = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Vendor = Join-Path $Root "vendor\c-toolchain-windows"
$Stamp = Join-Path $Vendor "ROOM-FETCH.txt"

if ((Test-Path (Join-Path $Vendor "bin\clang.exe")) -and -not $Force) {
    Write-Host "Already present: $Vendor\bin\clang.exe (pass -Force to re-download)" -ForegroundColor Green
    exit 0
}

if (-not $Url) {
    # llvm-mingw ucrt x86_64 - portable clang with ASan-capable builds
    $Url = "https://github.com/mstorsjo/llvm-mingw/releases/download/$Version/llvm-mingw-$Version-ucrt-x86_64.zip"
}

$Tmp = Join-Path $env:TEMP ("room-c-toolchain-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Path $Tmp -Force | Out-Null
$Zip = Join-Path $Tmp "toolchain.zip"

try {
    Write-Host "-> download $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
    Write-Host "-> expand"
    Expand-Archive -Path $Zip -DestinationPath $Tmp -Force
    $inner = Get-ChildItem -Directory $Tmp | Where-Object {
        Test-Path (Join-Path $_.FullName "bin\clang.exe")
    } | Select-Object -First 1
    if (-not $inner) {
        # some zips nest one more level
        $inner = Get-ChildItem -Directory -Recurse $Tmp -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName "bin\clang.exe") } |
            Select-Object -First 1
    }
    if (-not $inner) {
        throw "zip has no bin\clang.exe - check -Url / -Version"
    }
    if (Test-Path $Vendor) { Remove-Item -Recurse -Force $Vendor }
    New-Item -ItemType Directory -Path (Split-Path $Vendor) -Force | Out-Null
    Copy-Item -Recurse -Force $inner.FullName $Vendor
    @"
source=$Url
version=$Version
fetched=$((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))
"@ | Set-Content -Path $Stamp -Encoding UTF8
    Write-Host "OK $Vendor\bin\clang.exe" -ForegroundColor Green
} finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}
