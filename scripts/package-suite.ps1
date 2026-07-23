# Assemble Windows product suite zip: room + paper-derived + pi(+assets) + installers.
#
# Usage:
#   .\scripts\package-suite.ps1 -PaperDerived C:\pd.exe -Pi C:\pi\dist\pi.exe
#   .\scripts\package-suite.ps1 ... -PiAssetsDir C:\pi\packages\coding-agent\dist
#   .\scripts\package-suite.ps1 ... -AllowNoPi
#   .\scripts\package-suite.ps1 ... -SkipRoomBuild   # use existing dist\bin\room.exe (not recommended)
#
# By default this script REBUILDS room.exe first so the zip never ships a stale binary.
param(
    [Parameter(Mandatory = $true)]
    [string]$PaperDerived,

    [string]$Pi = "",

    [string]$PiAssetsDir = "",

    [string]$Room = "",

    [string]$Version = "",

    [string]$Out = "",

    # Portable LibreOffice root (must contain program\soffice.exe).
    # Default: vendor\libreoffice-windows if present (from fetch-libreoffice-windows.ps1).
    [string]$LibreOffice = "",

    # oob-divzero CLI binary (capability package #2)
    [string]$OobDivzero = "",

    # Portable C toolchain root (clang/gcc with ASan). Default: vendor\c-toolchain-windows
    [string]$CToolchain = "",

    [switch]$AllowNoPi,

    # Pack without oob-divzero.exe (skill may still ship; scan needs CLI)
    [switch]$AllowNoOob,

    # Skip build-binary.ps1 (only if you already built room.exe this minute)
    [switch]$SkipRoomBuild,

    # Pass -Clean through to build-binary when rebuilding
    [switch]$CleanRoom,

    # Fail pack if LibreOffice not available (full .doc-capable suite)
    [switch]$RequireLibreOffice,

    # Fail pack if tools\c-toolchain missing (full oob ASan suite)
    [switch]$RequireCToolchain
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

# macOS + Windows script encoding gate
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if ($py) {
    & $py.Source (Join-Path $Root "scripts\check-script-encoding.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Script encoding check failed. Run: python scripts/check-script-encoding.py --fix"
    }
}

function Copy-PiRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$PiBin,
        [Parameter(Mandatory = $true)][string]$DestBin,
        [string]$AssetsDir = ""
    )
    if (-not (Test-Path $PiBin)) { throw "pi binary missing: $PiBin" }
    New-Item -ItemType Directory -Path $DestBin -Force | Out-Null
    if (-not $AssetsDir) {
        $AssetsDir = Split-Path -Parent $PiBin
    }
    $names = @(
        "theme", "assets", "export-html", "docs", "examples",
        "package.json", "README.md", "CHANGELOG.md", "photon_rs_bg.wasm"
    )
    foreach ($name in $names) {
        $src = Join-Path $AssetsDir $name
        if (Test-Path $src) {
            $dst = Join-Path $DestBin $name
            if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
            Copy-Item -Recurse -Force $src $dst
            Write-Host "  + pi asset: $name"
        }
    }
    # Fallback: monorepo source themes
    $themeDark = Join-Path $DestBin "theme\dark.json"
    if (-not (Test-Path $themeDark)) {
        $candidates = @(
            (Join-Path $AssetsDir "..\src\modes\interactive\theme"),
            (Join-Path $AssetsDir "..\..\src\modes\interactive\theme"),
            (Join-Path $AssetsDir "modes\interactive\theme")
        )
        foreach ($try in $candidates) {
            $dark = Join-Path $try "dark.json"
            if (Test-Path $dark) {
                $td = Join-Path $DestBin "theme"
                New-Item -ItemType Directory -Path $td -Force | Out-Null
                Copy-Item -Force (Join-Path $try "*.json") $td
                Write-Host "  + pi asset: theme (from $try)"
                break
            }
        }
    }
    if (-not (Test-Path (Join-Path $DestBin "theme\dark.json"))) {
        throw @"
pi theme assets missing (need theme\dark.json next to pi.exe).
After ``npm run build:binary`` in packages\coding-agent, dist\ should contain theme\.
Or pass -PiAssetsDir to that dist folder.
"@
    }
}

if (-not $Version) {
    try {
        $Version = & python -c "import sys; sys.path.insert(0, r'src'); from room_tui import __version__; print(__version__)"
    } catch { $Version = "0.1.0" }
    if (-not $Version) { $Version = "0.1.0" }
}

# --- always rebuild room unless explicitly skipped ---
if (-not $SkipRoomBuild) {
    Write-Host "-> rebuilding room.exe (default; pass -SkipRoomBuild to reuse dist\bin\room.exe)" -ForegroundColor Cyan
    $buildScript = Join-Path $Root "scripts\build-binary.ps1"
    $bArgs = @("-ExecutionPolicy", "Bypass", "-File", $buildScript)
    if ($CleanRoom) { $bArgs += "-Clean" }
    & powershell @bArgs
    if ($LASTEXITCODE -ne 0) { throw "build-binary.ps1 failed (exit $LASTEXITCODE)" }
    $Room = Join-Path $Root "dist\bin\room.exe"
} elseif (-not $Room) {
    $cand = Join-Path $Root "dist\bin\room.exe"
    if (Test-Path $cand) { $Room = $cand }
}
if (-not $Room -or -not (Test-Path $Room)) {
    throw "room.exe not found. Run .\scripts\build-binary.ps1 first, or pass -Room"
}

# Staleness guard: room.exe must be newer than src/room_tui (unless forced skip rebuild just ran)
$roomItem = Get-Item $Room
$newestSrc = Get-ChildItem -Path (Join-Path $Root "src\room_tui") -Recurse -File -Filter "*.py" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($newestSrc -and $roomItem.LastWriteTime -lt $newestSrc.LastWriteTime.AddSeconds(-5)) {
    $msg = @"
room.exe is OLDER than source:
  room.exe mtime : $($roomItem.LastWriteTime)
  newer source   : $($newestSrc.FullName)  ($($newestSrc.LastWriteTime))
Re-run without -SkipRoomBuild, or run .\scripts\build-binary.ps1 first.
"@
    if ($SkipRoomBuild) { throw $msg }
    Write-Host "WARNING: $msg" -ForegroundColor Yellow
}
if (-not (Test-Path $PaperDerived)) {
    throw "paper-derived binary not found: $PaperDerived"
}
if (-not $Pi -or -not (Test-Path $Pi)) {
    if ($AllowNoPi) {
        Write-Host "WARNING: packing without pi" -ForegroundColor Yellow
        $Pi = ""
    } else {
        throw @"
Need -Pi path\to\pi.exe (from pi monorepo: packages\coding-agent npm run build:binary).
Or pass -AllowNoPi (not recommended).
"@
    }
}

$Arch = "x86_64"
try {
    $m = & python -c "import platform; print(platform.machine())"
    if ($m -match 'ARM|arm') { $Arch = "arm64" }
} catch { }

$OutRoot = if ($Out) { $Out } else { Join-Path $Root "dist\suite" }
$StageName = "room-suite-$Version-windows-$Arch"
$Stage = Join-Path $OutRoot $StageName
$StageBin = Join-Path $Stage "bin"

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Path $StageBin -Force | Out-Null

Write-Host "========================================"
Write-Host "  Room suite package (Windows)"
Write-Host "  version : $Version"
Write-Host "  target  : windows-$Arch"
Write-Host "  room    : $Room"
Write-Host "  engine  : $PaperDerived"
Write-Host "  pi      : $(if ($Pi) { $Pi } else { '(none)' })"
Write-Host "========================================"

$stageRoom = Join-Path $StageBin "room.exe"
Copy-Item -Force $Room $stageRoom
Copy-Item -Force $PaperDerived (Join-Path $StageBin "paper-derived.exe")

# oob-divzero capability CLI
if (-not $OobDivzero) {
    foreach ($cand in @(
            (Join-Path $Root "dist\bin\oob-divzero.exe"),
            (Join-Path $Root "..\oob-divzero\dist\oob-divzero.exe"),
            (Join-Path $Root "..\oob-divzero\cli\dist\oob-divzero.exe")
        )) {
        if (Test-Path $cand) { $OobDivzero = $cand; break }
    }
}
if ($OobDivzero -and (Test-Path $OobDivzero)) {
    Copy-Item -Force $OobDivzero (Join-Path $StageBin "oob-divzero.exe")
    Write-Host "  oob-divzero.exe <- $OobDivzero" -ForegroundColor Green
} elseif ($AllowNoOob) {
    Write-Host "WARNING: packing without oob-divzero.exe (-AllowNoOob)" -ForegroundColor Yellow
} else {
    throw @"
Need -OobDivzero path\to\oob-divzero.exe (or place dist\bin\oob-divzero.exe).
Or pass -AllowNoOob (skill-only; scan CLI missing).
"@
}

if ($Pi) {
    # Refuse non-Room pi before copying into suite
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $py) { throw "python required for verify-room-pi.py" }
    & $py.Source (Join-Path $Root "scripts\verify-room-pi.py") --binary $Pi --repo $Root
    if ($LASTEXITCODE -ne 0) {
        throw "pi is not Room-branded (missing pi.ROOM.txt). Run scripts\build-room-pi.ps1"
    }
    Copy-Item -Force $Pi (Join-Path $StageBin "pi.exe")
    Write-Host "-> pi runtime assets (theme/ etc.)"
    Copy-PiRuntime -PiBin $Pi -DestBin $StageBin -AssetsDir $PiAssetsDir
    # Ship brand stamp into suite so install/doctor can trust origin
    $stampCandidates = @(
        (Join-Path (Split-Path -Parent $Pi) "pi.ROOM.txt"),
        (Join-Path $Root "dist\bin\pi.ROOM.txt")
    )
    $stampSrc = $stampCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $stampSrc) { throw "pi.ROOM.txt missing after verify (internal error)" }
    Copy-Item -Force $stampSrc (Join-Path $StageBin "pi.ROOM.txt")
}

# Carry build stamp into suite
$buildStampSrc = Join-Path $Root "dist\bin\room.BUILD.txt"
if (Test-Path $buildStampSrc) {
    Copy-Item -Force $buildStampSrc (Join-Path $StageBin "room.BUILD.txt")
}

# Required skills (paper-derived docs; engine binary is already bin\paper-derived.exe)
function Copy-RequiredSkills {
    param([string]$StageDir, [string]$RepoRoot)
    $listFile = Join-Path $RepoRoot "packaging\required-skills.txt"
    if (-not (Test-Path $listFile)) {
        Write-Host "WARNING: no packaging\required-skills.txt" -ForegroundColor Yellow
        return
    }
    $destRoot = Join-Path $StageDir "skills"
    New-Item -ItemType Directory -Path $destRoot -Force | Out-Null
    $names = Get-Content $listFile | ForEach-Object {
        $line = ($_ -replace '#.*$', '').Trim()
        if ($line) { $line }
    }
    foreach ($name in $names) {
        # packaging\skills is vendored in-repo so Windows pack always works offline
        $candidates = @(
            (Join-Path $RepoRoot "packaging\skills\$name"),
            $env:PAPER_DERIVED_SKILL,
            $env:OOB_DIVZERO_SKILL,
            (Join-Path $RepoRoot "..\paper-derived\skill"),
            (Join-Path $RepoRoot "..\paper-derived\installs\skill"),
            (Join-Path $RepoRoot "..\oob-divzero\skill"),
            (Join-Path $RepoRoot "..\oob-divzero\installs\skill"),
            (Join-Path $env:USERPROFILE ".claude\skills\$name"),
            (Join-Path $env:USERPROFILE ".pi\agent\skills\$name"),
            (Join-Path $env:USERPROFILE ".agents\skills\$name")
        ) | Where-Object { $_ }
        $src = $null
        foreach ($c in $candidates) {
            if ($c -and (Test-Path (Join-Path $c "SKILL.md"))) {
                $src = $c
                break
            }
        }
        if (-not $src) {
            throw "Required skill missing: $name (vendored packaging/skills/$name or sibling repo skill/)"
        }
        $dest = Join-Path $destRoot $name
        if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        Copy-Item -Force (Join-Path $src "SKILL.md") (Join-Path $dest "SKILL.md")
        foreach ($sub in @("workflows", "references", "examples", "prompts")) {
            $sd = Join-Path $src $sub
            if (Test-Path $sd) {
                $dd = Join-Path $dest $sub
                New-Item -ItemType Directory -Path $dd -Force | Out-Null
                Copy-Item -Recurse -Force (Join-Path $sd "*") $dd
            }
        }
        # Never ship capability binaries / host wheels inside the skill tree
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dest "paper-derived")
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dest "paper-derived.exe")
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dest "oob-divzero")
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dest "oob-divzero.exe")
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $dest "pkg")
        Write-Host "  + skill: $name  <-  $src"
    }
    Copy-Item -Force $listFile (Join-Path $StageDir "required-skills.txt")
}

Write-Host "-> required skills"
Copy-RequiredSkills -StageDir $Stage -RepoRoot $Root

# --- optional / recommended: bundled LibreOffice headless converter ---
if (-not $LibreOffice) {
    $defaultLo = Join-Path $Root "vendor\libreoffice-windows"
    if (Test-Path (Join-Path $defaultLo "program\soffice.exe")) {
        $LibreOffice = $defaultLo
    }
}
$loSoffice = $null
if ($LibreOffice) {
    if (Test-Path (Join-Path $LibreOffice "program\soffice.exe")) {
        $loSoffice = Join-Path $LibreOffice "program\soffice.exe"
    } elseif (Test-Path (Join-Path $LibreOffice "soffice.exe")) {
        # user passed ...\program
        $loSoffice = Join-Path $LibreOffice "soffice.exe"
        $LibreOffice = Split-Path -Parent $LibreOffice
    }
}
if ($loSoffice -and (Test-Path $loSoffice)) {
    $stageTools = Join-Path $Stage "tools\libreoffice"
    Write-Host "-> bundling LibreOffice converter -> tools\libreoffice"
    if (Test-Path $stageTools) { Remove-Item -Recurse -Force $stageTools }
    New-Item -ItemType Directory -Path $stageTools -Force | Out-Null
    Copy-Item -Recurse -Force (Join-Path $LibreOffice "*") $stageTools
    $stageSo = Join-Path $stageTools "program\soffice.exe"
    if (-not (Test-Path $stageSo)) {
        throw "LibreOffice copy failed; missing $stageSo"
    }
    $loMb = [math]::Round(((Get-Item $stageSo).Length) / 1MB, 1)
    Write-Host ("  soffice.exe staged (~ binary {0} MB; full tree larger)" -f $loMb) -ForegroundColor Green
    # LICENSE note for redistributors
    $notice = Join-Path $Stage "tools\libreoffice\ROOM-NOTICE.txt"
    @"
Room bundles LibreOffice for headless .doc conversion only.
LibreOffice is free software under MPL/LGPL (and other) licenses.
See https://www.libreoffice.org/ and the licenses/ folder shipped with this tree.
Room does not modify LibreOffice; binary is redistributed as-is for offline install.
"@ | Set-Content -Path $notice -Encoding UTF8
} else {
    if ($RequireLibreOffice) {
        throw @"
-RequireLibreOffice set but no portable LibreOffice found.
Run: .\scripts\fetch-libreoffice-windows.ps1
Or pass: -LibreOffice path\to\lo-root (with program\soffice.exe)
"@
    }
    Write-Host "WARNING: suite without tools\libreoffice - .doc needs system Word/LO" -ForegroundColor Yellow
    Write-Host "  Optional: .\scripts\fetch-libreoffice-windows.ps1 then re-pack" -ForegroundColor Yellow
}

# --- C toolchain for oob-divzero ASan (required for full product) ---
if (-not $CToolchain) {
    $defaultTc = Join-Path $Root "vendor\c-toolchain-windows"
    if ((Test-Path (Join-Path $defaultTc "bin\clang.exe")) -or (Test-Path (Join-Path $defaultTc "bin\clang"))) {
        $CToolchain = $defaultTc
    }
}
$ccExe = $null
if ($CToolchain) {
    foreach ($n in @("clang.exe", "clang", "gcc.exe", "gcc", "cc.exe", "cc")) {
        $cand = Join-Path $CToolchain "bin\$n"
        if (Test-Path $cand) { $ccExe = $cand; break }
        $cand2 = Join-Path $CToolchain $n
        if (Test-Path $cand2) { $ccExe = $cand2; break }
    }
}
if ($ccExe -and (Test-Path $ccExe)) {
    $stageTc = Join-Path $Stage "tools\c-toolchain"
    Write-Host "-> bundling C toolchain (ASan) -> tools\c-toolchain"
    if (Test-Path $stageTc) { Remove-Item -Recurse -Force $stageTc }
    New-Item -ItemType Directory -Path $stageTc -Force | Out-Null
    # If user pointed at ...\bin, copy parent tree
    $tcRoot = $CToolchain
    if ((Split-Path -Leaf $CToolchain) -eq "bin") {
        $tcRoot = Split-Path -Parent $CToolchain
    }
    Copy-Item -Recurse -Force (Join-Path $tcRoot "*") $stageTc
    $stageCc = $null
    foreach ($n in @("clang.exe", "clang", "gcc.exe", "gcc")) {
        $c = Join-Path $stageTc "bin\$n"
        if (Test-Path $c) { $stageCc = $c; break }
    }
    if (-not $stageCc) {
        throw "C toolchain copy failed; no bin\clang|gcc under tools\c-toolchain"
    }
    Write-Host ("  compiler staged: {0}" -f $stageCc) -ForegroundColor Green
    $notice = Join-Path $stageTc "ROOM-NOTICE.txt"
    @"
Room bundles a C/C++ toolchain (clang/gcc + runtime) for oob-divzero ASan verification.
This is redistributed as a portable toolchain for offline install; see upstream licenses
in this tree. Room does not claim ownership of LLVM/MinGW/etc. components.
"@ | Set-Content -Path $notice -Encoding UTF8
} else {
    if ($RequireCToolchain) {
        throw @"
-RequireCToolchain set but no portable C toolchain found.
Run: .\scripts\fetch-c-toolchain-windows.ps1
Or pass: -CToolchain path\to\toolchain-root (with bin\clang.exe)
"@
    }
    Write-Host "WARNING: suite without tools\c-toolchain - oob ASan needs system clang/gcc" -ForegroundColor Yellow
    Write-Host "  Product path: .\scripts\fetch-c-toolchain-windows.ps1 then -RequireCToolchain" -ForegroundColor Yellow
}

# Hard gate: suite is incomplete without engine binary + required skill docs
$stagePd = Join-Path $StageBin "paper-derived.exe"
$stageSkill = Join-Path $Stage "skills\paper-derived\SKILL.md"
$stageOobSkill = Join-Path $Stage "skills\oob-divzero\SKILL.md"
if (-not (Test-Path $stagePd)) {
    throw "suite missing bin\paper-derived.exe after copy (source was: $PaperDerived)"
}
$pdItem = Get-Item $stagePd
if ($pdItem.Length -lt 1MB) {
    throw "bin\paper-derived.exe looks too small ($($pdItem.Length) bytes) - wrong file?"
}
if (-not (Test-Path $stageSkill)) {
    throw "suite missing skills\paper-derived\SKILL.md - pack failed (check packaging\skills)"
}
if (-not (Test-Path $stageOobSkill)) {
    throw "suite missing skills\oob-divzero\SKILL.md - pack failed (check packaging\skills)"
}
Write-Host ("  paper-derived.exe : {0:N1} MB" -f ($pdItem.Length / 1MB)) -ForegroundColor Green
Write-Host "  skill paper-derived: $stageSkill" -ForegroundColor Green
Write-Host "  skill oob-divzero  : $stageOobSkill" -ForegroundColor Green
$stageOob = Join-Path $StageBin "oob-divzero.exe"
if (Test-Path $stageOob) {
    Write-Host ("  oob-divzero.exe   : {0:N1} MB" -f ((Get-Item $stageOob).Length / 1MB)) -ForegroundColor Green
}

# Hard gate: Room bootstrap needs `paper-derived version` JSON + capabilities
Write-Host "-> verify paper-derived version (Room-compatible)"
$verOut = & $stagePd version 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    throw @"
paper-derived.exe does not support 'version' (Room requires it).
Output: $verOut
Rebuild engine from paper-derived branch claude0 (product line; not master) and re-pack.
"@
}
if ($verOut -notmatch '"capabilities"' -or $verOut -notmatch 'out-text-prompt' -or $verOut -notmatch 'session-run') {
    throw @"
paper-derived version JSON missing required capabilities (out-text-prompt, session-run).
Output: $verOut
"@
}
Write-Host "  paper-derived version: OK" -ForegroundColor Green

if ($Pi) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
    & $py.Source (Join-Path $Root "scripts\verify-room-pi.py") --suite $Stage
    if ($LASTEXITCODE -ne 0) { throw "suite Room-pi verify failed" }
}

Copy-Item -Force (Join-Path $Root "installs\win\install.ps1") (Join-Path $Stage "install.ps1")
Copy-Item -Force (Join-Path $Root "installs\win\install.bat") (Join-Path $Stage "install.bat")
Copy-Item -Force (Join-Path $Root "installs\README.md") (Join-Path $Stage "README.md")
Copy-Item -Force (Join-Path $Root "packaging\config.example.toml") (Join-Path $Stage "config.example.toml")

$built = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$pdBase = [System.IO.Path]::GetFileName($PaperDerived)
$piBase = if ($Pi) { [System.IO.Path]::GetFileName($Pi) } else { "" }
$hasTheme = if (Test-Path (Join-Path $StageBin "theme\dark.json")) { "yes" } else { "no" }
$roomSha = (Get-FileHash -Algorithm SHA256 $stageRoom).Hash.ToLowerInvariant()
$roomInfo = Get-Item $stageRoom
$srcSha = (Get-FileHash -Algorithm SHA256 $Room).Hash.ToLowerInvariant()
if ($roomSha -ne $srcSha) {
    throw "suite room.exe sha256 mismatch vs source $Room - copy failed?"
}
$pdSha = (Get-FileHash -Algorithm SHA256 $stagePd).Hash.ToLowerInvariant()
@"
room=$Version
os=windows
arch=$Arch
paper-derived=$pdBase
paper-derived_sha256=$pdSha
paper-derived_size=$($pdItem.Length)
pi=$piBase
pi_theme=$hasTheme
pi_brand=$(if ($Pi) { "room" } else { "" })
skills=paper-derived,oob-divzero
oob-divzero=$(if (Test-Path $stageOob) { "bin/oob-divzero.exe" } else { "none" })
libreoffice=$(if ($loSoffice) { "tools/libreoffice" } else { "none" })
c-toolchain=$(if ($ccExe) { "tools/c-toolchain" } else { "none" })
built=$built
room_sha256=$roomSha
room_size=$($roomInfo.Length)
room_mtime=$($roomInfo.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
"@ | Set-Content -Path (Join-Path $Stage "VERSION") -Encoding UTF8
if (Test-Path $buildStampSrc) {
    Get-Content $buildStampSrc | Add-Content (Join-Path $Stage "VERSION")
}

New-Item -ItemType Directory -Path $OutRoot -Force | Out-Null
$ZipPath = Join-Path $OutRoot "$StageName.zip"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

Push-Location $OutRoot
try {
    Compress-Archive -Path $StageName -DestinationPath $ZipPath -Force
} finally {
    Pop-Location
}

$zipMb = (Get-Item $ZipPath).Length / 1MB
Write-Host ""
Write-Host "OK suite folder: $Stage" -ForegroundColor Green
Write-Host ("OK archive     : {0} ({1:N1} MB)" -f $ZipPath, $zipMb) -ForegroundColor Green
Write-Host ("  room.exe     : {0:N2} MB  mtime={1}  sha256={2}..." -f ($roomInfo.Length/1MB), $roomInfo.LastWriteTime, $roomSha.Substring(0,12)) -ForegroundColor Green
Write-Host ""
Write-Host "Ship to colleague:"
Write-Host "  1. Copy $ZipPath  (or the suite folder)"
Write-Host "  2. Unzip, then: powershell -ExecutionPolicy Bypass -File .\install.ps1"
Write-Host "  3. New terminal: room doctor"
Write-Host "  4. Confirm install printed the SAME room sha256 as above"
