# Download LibreOffice (Windows x86_64) and extract a portable program/ tree
# for Room suite bundling. Does NOT install LO into the system Start Menu.
#
# Output (default):
#   vendor\libreoffice-windows\program\soffice.exe
#
# Usage:
#   .\scripts\fetch-libreoffice-windows.ps1
#   .\scripts\fetch-libreoffice-windows.ps1 -Version 24.8.4 -Out vendor\libreoffice-windows
#   .\scripts\fetch-libreoffice-windows.ps1 -MsiPath C:\cache\LibreOffice.msi
#
# Then pack:
#   .\scripts\package-suite.ps1 -PaperDerived ... -LibreOffice vendor\libreoffice-windows
#   # or build-windows-suite.ps1 -LibreOffice vendor\libreoffice-windows
#
param(
    [string]$Version = "24.8.4",
    [string]$Out = "",
    [string]$MsiPath = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
Set-Location $Root

if (-not $Out) {
    $Out = Join-Path $Root "vendor\libreoffice-windows"
}
$Out = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Out)
$Soffice = Join-Path $Out "program\soffice.exe"

if ((Test-Path $Soffice) -and -not $Force) {
    Write-Host "OK already present: $Soffice" -ForegroundColor Green
    Write-Host "  (pass -Force to re-download)"
    exit 0
}

$Work = Join-Path $Root "vendor\_lo-work"
New-Item -ItemType Directory -Path $Work -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $Out) -Force | Out-Null

if (-not $MsiPath) {
    # Official still/stable tree: https://download.documentfoundation.org/libreoffice/
    $file = "LibreOffice_${Version}_Win_x86-64.msi"
    $url = "https://download.documentfoundation.org/libreoffice/stable/$Version/win/x86_64/$file"
    $MsiPath = Join-Path $Work $file
    if (-not (Test-Path $MsiPath) -or $Force) {
        Write-Host "-> downloading $url"
        Write-Host "   ( ~300MB ; needs network )"
        try {
            Invoke-WebRequest -Uri $url -OutFile $MsiPath -UseBasicParsing
        } catch {
            Write-Host "ERROR: download failed: $_" -ForegroundColor Red
            Write-Host "  Manual: download the Win x86-64 MSI from" -ForegroundColor Yellow
            Write-Host "  https://www.libreoffice.org/download/download-libreoffice/" -ForegroundColor Yellow
            Write-Host "  then: .\scripts\fetch-libreoffice-windows.ps1 -MsiPath path\to.msi" -ForegroundColor Yellow
            exit 1
        }
    } else {
        Write-Host "-> reusing cached MSI: $MsiPath"
    }
}

if (-not (Test-Path $MsiPath)) {
    throw "MSI not found: $MsiPath"
}

$Extract = Join-Path $Work "extract"
if (Test-Path $Extract) { Remove-Item -Recurse -Force $Extract }
New-Item -ItemType Directory -Path $Extract -Force | Out-Null

Write-Host "-> administrative extract (msiexec /a) -> $Extract"
# /a = network/admin image: unpacks files without registering a system install
$msiArgs = @(
    "/a", "`"$MsiPath`"",
    "/qn",
    "TARGETDIR=`"$Extract`""
)
$p = Start-Process -FilePath "msiexec.exe" -ArgumentList $msiArgs -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) {
    throw "msiexec /a failed (exit $($p.ExitCode)). Run elevated? Or extract MSI manually."
}

# MSI admin image layout varies; find program\soffice.exe
$found = Get-ChildItem -Path $Extract -Recurse -Filter "soffice.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -match 'program$' } |
    Select-Object -First 1
if (-not $found) {
    throw "soffice.exe not found under extract tree: $Extract"
}
$programSrc = $found.Directory.FullName
$loRootSrc = Split-Path -Parent $programSrc

Write-Host "-> found LO root: $loRootSrc"
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Path $Out -Force | Out-Null
# Copy whole product tree (program + share + help... needed for filters)
Copy-Item -Recurse -Force (Join-Path $loRootSrc "*") $Out

if (-not (Test-Path $Soffice)) {
    throw "copy failed; missing $Soffice"
}

$sizeMb = [math]::Round(((Get-ChildItem -Recurse $Out | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Write-Host ""
Write-Host "OK LibreOffice staged for Room suite:" -ForegroundColor Green
Write-Host "  $Soffice"
Write-Host "  size ~ ${sizeMb} MB"
Write-Host ""
Write-Host "Next:"
Write-Host "  .\scripts\build-windows-suite.ps1 -LibreOffice `"$Out`" ..."
Write-Host "  # or package-suite.ps1 -LibreOffice `"$Out`""
Write-Host ""
Write-Host "License: MPL / LGPL - keep NOTICE when redistributing (see packaging/tools/libreoffice/README.md)"
