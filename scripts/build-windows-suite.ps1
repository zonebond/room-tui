# One-shot: build room.exe + assemble Windows suite (room + paper-derived + pi).
#
# Prerequisites:
#   - Windows x64 (for x86_64 colleagues) or ARM (for ARM only)
#   - Python 3.10+ on PATH
#   - room-tui repo
#   - paper-derived.exe and/or source
#   - pi.exe (bun compile) and/or pi monorepo with bun
#
# Usage:
#   .\scripts\build-windows-suite.ps1 `
#     -PaperDerived C:\build\paper-derived.exe `
#     -Pi C:\build\pi.exe
#
#   .\scripts\build-windows-suite.ps1 `
#     -PaperDerivedRepo C:\src\paper-derived `
#     -PiRepo C:\src\pi
#
param(
    [string]$PaperDerived = "",
    [string]$PaperDerivedRepo = "",
    [string]$Pi = "",
    [string]$PiRepo = "",
    [string]$OobDivzero = "",
    [string]$OobDivzeroRepo = "",
    # Portable LO root (program\soffice.exe). Default: vendor\libreoffice-windows
    [string]$LibreOffice = "",
    # Portable C toolchain (bin\clang.exe). Default: vendor\c-toolchain-windows
    [string]$CToolchain = "",
    [switch]$RequireLibreOffice,
    [switch]$RequireCToolchain,
    [switch]$Clean,
    [switch]$AllowNoPi,
    [switch]$AllowNoOob,
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
Set-Location $Root

Write-Host ""
Write-Host "==== Room Windows suite (one-shot) ====" -ForegroundColor Cyan
Write-Host "repo: $Root"
Write-Host ""

function Find-FirstExisting([string[]]$Paths) {
    foreach ($c in $Paths) {
        if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path }
    }
    return $null
}

# --- paper-derived ---
if (-not $PaperDerived) {
    $PaperDerived = Find-FirstExisting @(
        (Join-Path $Root "..\paper-derived\dist\paper-derived.exe"),
        (Join-Path $Root "..\paper-derived\build\paper-derived.exe"),
        (Join-Path $Root "..\paper-derived\build\paper-derived-windows-x86_64.exe")
    )
}

if (-not $PaperDerived -and $PaperDerivedRepo) {
    $pdRoot = (Resolve-Path $PaperDerivedRepo).Path
    Write-Host "-> building paper-derived from $pdRoot"
    $py = "python"
    if (Test-Path (Join-Path $pdRoot ".venv\Scripts\python.exe")) {
        $py = Join-Path $pdRoot ".venv\Scripts\python.exe"
    }
    Push-Location $pdRoot
    try {
        & $py -m pip install -U pip pyinstaller -q
        if (Test-Path "pyproject.toml") {
            & $py -m pip install -e . -q
        }
        $entry = $null
        foreach ($p in @("cli\paper_derived\cli.py", "cli\src\paper_derived\cli.py", "paper_derived\cli.py")) {
            if (Test-Path $p) { $entry = $p; break }
        }
        if (-not $entry) { throw "Cannot find paper_derived cli.py under $pdRoot" }
        $pdDist = Join-Path $pdRoot "build"
        New-Item -ItemType Directory -Path $pdDist -Force | Out-Null
        $addData = @()
        if (Test-Path "cli\paper_derived\prompts") {
            $addData = @("--add-data", "cli\paper_derived\prompts;paper_derived\prompts")
        }
        & $py -m PyInstaller --name paper-derived --onefile --clean --noconfirm `
            --distpath $pdDist --workpath (Join-Path $pdDist "_work") @addData $entry
        $PaperDerived = Join-Path $pdDist "paper-derived.exe"
    } finally {
        Pop-Location
    }
}

if (-not $PaperDerived -or -not (Test-Path $PaperDerived)) {
    Write-Host "ERROR: paper-derived.exe not found. Pass -PaperDerived or -PaperDerivedRepo" -ForegroundColor Red
    exit 1
}
Write-Host "paper-derived: $PaperDerived" -ForegroundColor Green

# --- Room-branded pi (ONLY third_party\pi submodule - never code.research\pi) ---
if (-not $Pi) {
    $Pi = Find-FirstExisting @(
        (Join-Path $Root "dist\bin\pi.exe"),
        (Join-Path $Root "dist\bin\pi"),
        (Join-Path $Root "third_party\pi\packages\coding-agent\dist\pi.exe"),
        (Join-Path $Root "third_party\pi\packages\coding-agent\dist\pi")
    )
}

if (-not $Pi -and -not $PiRepo) {
    $defaultPi = Join-Path $Root "third_party\pi"
    if (Test-Path (Join-Path $defaultPi "packages\coding-agent\package.json")) {
        $PiRepo = $defaultPi
    }
}

if (-not $Pi -and $PiRepo) {
    $piRoot = (Resolve-Path $PiRepo).Path
    $norm = $piRoot.Replace('\', '/').ToLowerInvariant()
    if ($norm -match '/code\.research/pi/?$') {
        Write-Host "ERROR: Refusing PiRepo=$piRoot (shared research tree)" -ForegroundColor Red
        Write-Host "  Use room-tui\third_party\pi (git submodule)." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "-> building Room-branded pi from $piRoot"
    $buildRoomPi = Join-Path $Here "build-room-pi.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $buildRoomPi -PiRoomSrc $piRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $Pi = Find-FirstExisting @(
        (Join-Path $Root "dist\bin\pi.exe"),
        (Join-Path $Root "dist\bin\pi")
    )
}

if (-not $Pi -or -not (Test-Path $Pi)) {
    if ($AllowNoPi) {
        Write-Host "WARNING: continuing without pi" -ForegroundColor Yellow
        $Pi = ""
    } else {
        Write-Host @"

ERROR: Room-branded pi binary not found.

Provide:
  -Pi C:\path\to\pi.exe
  -PiRepo path\to\room-tui\third_party\pi

Setup (one product repo):
  git clone --recurse-submodules <room-tui>
  git submodule update --init --recursive
  .\scripts\build-room-pi.ps1

"@ -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "pi (Room-branded): $Pi" -ForegroundColor Green
}

# Hard gate: refuse packing stock/global pi (must have pi.ROOM.txt brand stamp)
if ($Pi) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $py) { throw "python required for verify-room-pi.py" }
    Write-Host "-> verify Room-branded pi"
    & $py.Source (Join-Path $Here "verify-room-pi.py") --binary $Pi --repo $Root
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pi is not Room-branded. Run .\scripts\build-room-pi.ps1 first." -ForegroundColor Red
        exit 1
    }
}

# --- build room.exe (always clean; package-suite uses -SkipRoomBuild after this) ---
$buildArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $Here "build-binary.ps1"),
    "-Clean"
)
& powershell @buildArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$RoomExe = Join-Path $Root "dist\bin\room.exe"
if (-not (Test-Path $RoomExe)) {
    throw "room.exe missing after build: $RoomExe"
}

Write-Host "-> smoke room --version"
& $RoomExe --version
$roomBuilt = Get-Item $RoomExe
$roomSha = (Get-FileHash -Algorithm SHA256 $RoomExe).Hash.ToLowerInvariant()
Write-Host ("   room.exe mtime={0}  size={1:N1} MB  sha={2}..." -f $roomBuilt.LastWriteTime, ($roomBuilt.Length/1MB), $roomSha.Substring(0,12)) -ForegroundColor Green

# --- oob-divzero capability ---
if (-not $OobDivzero) {
    $OobDivzero = Find-FirstExisting @(
        (Join-Path $Root "dist\bin\oob-divzero.exe"),
        (Join-Path $Root "..\oob-divzero\dist\oob-divzero.exe"),
        (Join-Path $Root "..\oob-divzero\cli\dist\oob-divzero.exe")
    )
}
if (-not $OobDivzero -and $OobDivzeroRepo) {
    $oobRoot = (Resolve-Path $OobDivzeroRepo).Path
    Write-Host "-> building oob-divzero from $oobRoot (PyInstaller if available)"
    $py = "python"
    if (Test-Path (Join-Path $oobRoot "cli\.venv\Scripts\python.exe")) {
        $py = Join-Path $oobRoot "cli\.venv\Scripts\python.exe"
    }
    Push-Location (Join-Path $oobRoot "cli")
    try {
        & $py -m pip install -U pip pyinstaller -q
        & $py -m pip install -e . -q
        $entry = "src\oob_divzero\cli.py"
        if (-not (Test-Path $entry)) { throw "missing $entry" }
        & $py -m PyInstaller --noconfirm --onefile --name oob-divzero $entry
        $OobDivzero = Join-Path $oobRoot "cli\dist\oob-divzero.exe"
        if (-not (Test-Path $OobDivzero)) { throw "oob-divzero.exe not produced" }
    } finally {
        Pop-Location
    }
}

# --- package (room already rebuilt above) ---
if (-not $LibreOffice) {
    $defaultLo = Join-Path $Root "vendor\libreoffice-windows"
    if (Test-Path (Join-Path $defaultLo "program\soffice.exe")) {
        $LibreOffice = $defaultLo
        Write-Host "libreoffice: $LibreOffice (auto)" -ForegroundColor Green
    }
} elseif (Test-Path (Join-Path $LibreOffice "program\soffice.exe")) {
    Write-Host "libreoffice: $LibreOffice" -ForegroundColor Green
}

if (-not $CToolchain) {
    $defaultTc = Join-Path $Root "vendor\c-toolchain-windows"
    if (Test-Path (Join-Path $defaultTc "bin\clang.exe")) {
        $CToolchain = $defaultTc
        Write-Host "c-toolchain: $CToolchain (auto)" -ForegroundColor Green
    }
} elseif (Test-Path (Join-Path $CToolchain "bin\clang.exe") -or (Test-Path (Join-Path $CToolchain "bin\clang"))) {
    Write-Host "c-toolchain: $CToolchain" -ForegroundColor Green
}

$pkgArgs = @(
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $Here "package-suite.ps1"),
    "-PaperDerived", $PaperDerived,
    "-Room", $RoomExe,
    "-SkipRoomBuild"
)
if ($Pi) {
    $pkgArgs += @("-Pi", $Pi)
    # Prefer assets next to pi.exe (coding-agent/dist after build:binary)
    $piDir = Split-Path -Parent $Pi
    if (Test-Path (Join-Path $piDir "theme\dark.json")) {
        $pkgArgs += @("-PiAssetsDir", $piDir)
    } elseif ($PiRepo) {
        $distTheme = Join-Path $PiRepo "packages\coding-agent\dist"
        if (Test-Path (Join-Path $distTheme "theme\dark.json")) {
            $pkgArgs += @("-PiAssetsDir", $distTheme)
        }
    }
}
if ($AllowNoPi -and -not $Pi) { $pkgArgs += "-AllowNoPi" }
if ($OobDivzero) { $pkgArgs += @("-OobDivzero", $OobDivzero) }
if ($AllowNoOob -and -not $OobDivzero) { $pkgArgs += "-AllowNoOob" }
if ($LibreOffice) { $pkgArgs += @("-LibreOffice", $LibreOffice) }
if ($RequireLibreOffice) { $pkgArgs += "-RequireLibreOffice" }
if ($CToolchain) { $pkgArgs += @("-CToolchain", $CToolchain) }
if ($RequireCToolchain) { $pkgArgs += "-RequireCToolchain" }

& powershell @pkgArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# --- copy paper-derived.exe + pi.exe + pi assets to dist/bin (dev / fallback) ---
# Skip when source already *is* dist\bin\… (Copy-Item errors: same as destination).
function Copy-FileIfDifferent {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string]$Label = "file"
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Copy $Label failed: source missing: $Source"
    }
    $destDir = Split-Path -Parent $Destination
    if ($destDir -and -not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    try {
        $srcFull = (Resolve-Path -LiteralPath $Source).Path
    } catch {
        $srcFull = [System.IO.Path]::GetFullPath($Source)
    }
    $dstFull = [System.IO.Path]::GetFullPath($Destination)
    if ($srcFull -and $dstFull -and (
            $srcFull.Equals($dstFull, [System.StringComparison]::OrdinalIgnoreCase)
        )) {
        Write-Host "-> $Label already at dist/bin (skip self-copy)" -ForegroundColor DarkGray
        return
    }
    try {
        Copy-Item -Force -LiteralPath $Source -Destination $Destination -ErrorAction Stop
    } catch {
        throw @"
Copy $Label failed:
  from: $Source
  to  : $Destination
  err : $($_.Exception.Message)
Common causes: file locked (room/pi still running), AV quarantine, path too long.
"@
    }
}

$PdInBin = Join-Path $Root "dist\bin\paper-derived.exe"
Copy-FileIfDifferent -Source $PaperDerived -Destination $PdInBin -Label "paper-derived.exe"
Write-Host "-> paper-derived.exe ready in dist/bin"

if ($Pi) {
    $PiInBin = Join-Path $Root "dist\bin\pi.exe"
    Copy-FileIfDifferent -Source $Pi -Destination $PiInBin -Label "pi.exe"
    $piDist = Split-Path -Parent $Pi
    $piTheme = Join-Path $piDist "theme"
    $dstTheme = Join-Path $Root "dist\bin\theme"
    if (Test-Path -LiteralPath $piTheme) {
        try {
            $themeSrcFull = (Resolve-Path -LiteralPath $piTheme).Path
        } catch {
            $themeSrcFull = [System.IO.Path]::GetFullPath($piTheme)
        }
        $themeDstFull = [System.IO.Path]::GetFullPath($dstTheme)
        if ($themeSrcFull.Equals($themeDstFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Host "-> pi theme already under dist/bin (skip self-copy)" -ForegroundColor DarkGray
        } else {
            if (Test-Path -LiteralPath $dstTheme) { Remove-Item -Recurse -Force -LiteralPath $dstTheme }
            try {
                Copy-Item -Recurse -Force -LiteralPath $piTheme -Destination $dstTheme -ErrorAction Stop
            } catch {
                throw "Copy pi theme failed: $($_.Exception.Message) ($piTheme -> $dstTheme)"
            }
            Write-Host "-> pi.exe + theme ready in dist/bin"
        }
    } else {
        Write-Host "-> pi.exe ready in dist/bin"
    }
}

# --- stage full payload for Inno Setup (must include engine + paper-derived skill) ---
$suiteDir = Get-ChildItem -Directory (Join-Path $Root "dist\suite") |
    Where-Object { $_.Name -like "room-suite-*-windows-*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $suiteDir) {
    throw "No windows suite folder under dist\suite after package-suite"
}
$payload = Join-Path $Root "dist\installer-payload"
if (Test-Path $payload) {
    try {
        Remove-Item -Recurse -Force -LiteralPath $payload -ErrorAction Stop
    } catch {
        throw @"
Cannot clear installer-payload (files locked?):
  $payload
  $($_.Exception.Message)
Close Room/pi if running, then retry.
"@
    }
}
New-Item -ItemType Directory -Path $payload -Force | Out-Null
try {
    Copy-Item -Recurse -Force -LiteralPath (Join-Path $suiteDir.FullName "*") -Destination $payload -ErrorAction Stop
} catch {
    throw @"
Copy suite -> installer-payload failed:
  from: $($suiteDir.FullName)
  to  : $payload
  err : $($_.Exception.Message)
"@
}

$payloadPd = Join-Path $payload "bin\paper-derived.exe"
$payloadSkill = Join-Path $payload "skills\paper-derived\SKILL.md"
$payloadOobSkill = Join-Path $payload "skills\oob-divzero\SKILL.md"
$payloadOob = Join-Path $payload "bin\oob-divzero.exe"
$payloadRoom = Join-Path $payload "bin\room.exe"
$payloadInstall = Join-Path $payload "install.ps1"
if (-not (Test-Path $payloadRoom)) { throw "installer-payload missing bin\room.exe" }
if (-not (Test-Path $payloadPd)) {
    throw "installer-payload missing bin\paper-derived.exe - Inno would ship a broken product"
}
if (-not (Test-Path $payloadSkill)) {
    throw "installer-payload missing skills\paper-derived\SKILL.md - Inno would ship without required skill"
}
if (-not (Test-Path $payloadOobSkill)) {
    throw "installer-payload missing skills\oob-divzero\SKILL.md - required capability skill missing"
}
if (-not (Test-Path $payloadInstall)) {
    throw "installer-payload missing install.ps1 - one-click Setup cannot finalize"
}
# Final suite-level Room-pi gate (same checks package-suite already ran)
$pyPay = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyPay) { $pyPay = Get-Command python3 -ErrorAction SilentlyContinue }
if ($pyPay -and (Test-Path (Join-Path $payload "bin\pi.exe"))) {
    & $pyPay.Source (Join-Path $Here "verify-room-pi.py") --suite $payload
    if ($LASTEXITCODE -ne 0) { throw "installer-payload Room-pi verify failed" }
}
$payloadLo = Join-Path $payload "tools\libreoffice\program\soffice.exe"
$payloadCc = Join-Path $payload "tools\c-toolchain\bin\clang.exe"
Write-Host "-> installer-payload ready from $($suiteDir.Name)" -ForegroundColor Green
Write-Host "   room.exe + paper-derived + oob skill + install.ps1 (+ Room-branded pi)" -ForegroundColor Green
if (Test-Path $payloadOob) {
    Write-Host "   + bin\oob-divzero.exe" -ForegroundColor Green
} else {
    Write-Host "   (no oob-divzero.exe - pack with -OobDivzero for full product)" -ForegroundColor Yellow
}
if (Test-Path $payloadLo) {
    Write-Host "   + tools\libreoffice (bundled .doc converter)" -ForegroundColor Green
} else {
    Write-Host "   (no tools\libreoffice - run fetch-libreoffice-windows.ps1 for full .doc support)" -ForegroundColor Yellow
}
if (Test-Path $payloadCc) {
    Write-Host "   + tools\c-toolchain (oob ASan)" -ForegroundColor Green
} else {
    Write-Host "   (no tools\c-toolchain - run fetch-c-toolchain-windows.ps1 for ASan)" -ForegroundColor Yellow
}

# --- build Inno Setup installer ---
$ISCC = $null
$candidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $ISCC = $c; break }
}

if ($ISCC) {
    $IssFile = Join-Path $Root "packaging\room-setup.iss"
    if (Test-Path $IssFile) {
        Write-Host ""
        Write-Host "==== Building Inno Setup installer ====" -ForegroundColor Cyan
        Write-Host "  payload: $payload"
        & $ISCC $IssFile
        if ($LASTEXITCODE -eq 0) {
            Write-Host ""
            Write-Host "OK installer: dist\installer\Room-Setup-*-windows-x86_64.exe" -ForegroundColor Green
            Write-Host "  (includes paper-derived.exe + skills\paper-derived)" -ForegroundColor Green
        } else {
            Write-Host "ERROR: Inno Setup compile failed (exit $LASTEXITCODE)" -ForegroundColor Red
            exit $LASTEXITCODE
        }
    } else {
        Write-Host "WARNING: $IssFile not found, skip installer" -ForegroundColor Yellow
    }
} else {
    Write-Host "WARNING: Inno Setup 6 not found, skip .exe installer." -ForegroundColor Yellow
    Write-Host "  Zip suite is still valid: dist\suite\ (use install.ps1)" -ForegroundColor Yellow
    Write-Host "  Optional: winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
}

# --- install locally ---
if ($Install) {
    $suiteDir = Get-ChildItem -Directory (Join-Path $Root "dist\suite") | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($suiteDir) {
        $installScript = Join-Path $suiteDir.FullName "install.ps1"
        if (Test-Path $installScript) {
            Write-Host ""
            Write-Host "==== Installing to %LOCALAPPDATA%\Programs\Room ====" -ForegroundColor Cyan
            & powershell -ExecutionPolicy Bypass -File $installScript
        } else {
            Write-Host "WARNING: install.ps1 not found in suite dir" -ForegroundColor Yellow
        }
    } else {
        Write-Host "WARNING: no suite dir found under dist\suite" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done. One-click for colleagues:" -ForegroundColor Green
Write-Host "  Setup.exe : dist\installer\Room-Setup-*-windows-x86_64.exe" -ForegroundColor Green
Write-Host "  (or zip)  : dist\suite\room-suite-*-windows-*.zip + install.ps1" -ForegroundColor Green
Write-Host "Colleague: run Setup (or install.ps1) once -> room doctor green." -ForegroundColor Green
Write-Host "NOTE: arch must match colleague PC (x86_64 vs arm64)." -ForegroundColor Yellow
Write-Host "NOTE: old installs without skills need this NEW Setup re-run (still one click)." -ForegroundColor Yellow
