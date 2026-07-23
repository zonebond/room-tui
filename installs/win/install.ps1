# Room suite one-click installer (Windows).
# Run from the unzipped suite directory (sibling of bin\).
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Also run by Inno Setup after files are already under {app}:
#   SuiteDir == ROOM_HOME == install root (in-place). Must NOT delete skills source.

$ErrorActionPreference = "Stop"

# Prefer PSScriptRoot (reliable under -File and -Command & script.ps1)
$SuiteDir = if ($PSScriptRoot) { $PSScriptRoot } else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $SuiteDir) {
    Write-Host "ERROR: cannot resolve suite directory (PSScriptRoot empty)" -ForegroundColor Red
    exit 1
}

$BinSrc = Join-Path $SuiteDir "bin"
$Share = if ($env:ROOM_HOME) { $env:ROOM_HOME } else { Join-Path $env:LOCALAPPDATA "Programs\Room" }
$BinDir = Join-Path $Share "bin"
$CfgDir = Join-Path $env:USERPROFILE ".config\room-tui"

# Log every install so Setup.exe failures are diagnosable
$LogFile = Join-Path $env:TEMP "room-install.log"
try {
    Start-Transcript -Path $LogFile -Force | Out-Null
} catch {
    # older hosts / locked log: continue without transcript
}

function Test-SamePath {
    param([string]$A, [string]$B)
    if (-not $A -or -not $B) { return $false }
    if (-not (Test-Path -LiteralPath $A)) { return $false }
    if (-not (Test-Path -LiteralPath $B)) { return $false }
    try {
        $ra = (Resolve-Path -LiteralPath $A).Path.TrimEnd('\', '/').ToLowerInvariant()
        $rb = (Resolve-Path -LiteralPath $B).Path.TrimEnd('\', '/').ToLowerInvariant()
        return ($ra -eq $rb)
    } catch {
        return $false
    }
}

function Write-Fail {
    param([string]$Message)
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host "  log: $LogFile" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Room - product installer (Windows)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "-> suite dir : $SuiteDir"
Write-Host "-> install to: $Share"
Write-Host "-> log       : $LogFile"

if (-not (Test-Path -LiteralPath $BinSrc)) {
    Write-Fail "missing suite bin\: $BinSrc"
}

$RoomSrc = Join-Path $BinSrc "room.exe"
$PdSrc = Join-Path $BinSrc "paper-derived.exe"
$PiSrc = Join-Path $BinSrc "pi.exe"
if (-not (Test-Path -LiteralPath $RoomSrc)) {
    $alt = Join-Path $BinSrc "room"
    if (Test-Path -LiteralPath $alt) { $RoomSrc = $alt } else {
        Write-Fail "missing bin\room.exe under $BinSrc"
    }
}
if (-not (Test-Path -LiteralPath $PdSrc)) {
    $alt = Join-Path $BinSrc "paper-derived"
    if (Test-Path -LiteralPath $alt) { $PdSrc = $alt } else {
        Write-Fail "missing bin\paper-derived.exe under $BinSrc"
    }
}
$HasPi = $false
if (Test-Path -LiteralPath $PiSrc) {
    $HasPi = $true
} else {
    $altPi = Join-Path $BinSrc "pi"
    if (Test-Path -LiteralPath $altPi) { $PiSrc = $altPi; $HasPi = $true }
}

New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
New-Item -ItemType Directory -Path $CfgDir -Force | Out-Null

$suiteVer = Join-Path $SuiteDir "VERSION"
if (Test-Path -LiteralPath $suiteVer) {
    Write-Host "-> suite VERSION:" -ForegroundColor Cyan
    Get-Content $suiteVer | ForEach-Object { Write-Host "    $_" }
}
$suiteRoomInfo = Get-Item -LiteralPath $RoomSrc
$suiteRoomSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $RoomSrc).Hash.ToLowerInvariant()
Write-Host ("-> suite room.exe  mtime={0}  size={1:N2} MB  sha256={2}..." -f `
    $suiteRoomInfo.LastWriteTime, ($suiteRoomInfo.Length / 1MB), $suiteRoomSha.Substring(0, 12)) -ForegroundColor Cyan

$running = Get-Process -Name "room" -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "-> stopping running room.exe so install can overwrite..." -ForegroundColor Yellow
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 400
}

# Copy bin/ only when suite source != install target (Inno is often in-place)
$inPlaceBin = Test-SamePath $BinSrc $BinDir
if ($inPlaceBin) {
    Write-Host "-> bin already in place (in-place Setup); skip self-copy" -ForegroundColor Cyan
} else {
    Write-Host "-> copying bin\ -> $BinDir"
    Get-ChildItem -Force -LiteralPath $BinSrc | ForEach-Object {
        $dst = Join-Path $BinDir $_.Name
        if ($_.PSIsContainer) {
            if (Test-Path -LiteralPath $dst) { Remove-Item -Recurse -Force -LiteralPath $dst }
            Copy-Item -Recurse -Force -LiteralPath $_.FullName -Destination $dst
        } else {
            Copy-Item -Force -LiteralPath $_.FullName -Destination $dst
        }
    }
}

if (-not (Test-Path (Join-Path $BinDir "room.exe")) -and (Test-Path (Join-Path $BinDir "room"))) {
    Copy-Item -Force (Join-Path $BinDir "room") (Join-Path $BinDir "room.exe")
}
if (-not (Test-Path (Join-Path $BinDir "paper-derived.exe")) -and (Test-Path (Join-Path $BinDir "paper-derived"))) {
    Copy-Item -Force (Join-Path $BinDir "paper-derived") (Join-Path $BinDir "paper-derived.exe")
}
if (-not (Test-Path (Join-Path $BinDir "oob-divzero.exe")) -and (Test-Path (Join-Path $BinDir "oob-divzero"))) {
    Copy-Item -Force (Join-Path $BinDir "oob-divzero") (Join-Path $BinDir "oob-divzero.exe")
}
if ($HasPi -and -not (Test-Path (Join-Path $BinDir "pi.exe")) -and (Test-Path (Join-Path $BinDir "pi"))) {
    Copy-Item -Force (Join-Path $BinDir "pi") (Join-Path $BinDir "pi.exe")
}

$verSrc = Join-Path $SuiteDir "VERSION"
if ((Test-Path -LiteralPath $verSrc) -and -not (Test-SamePath $verSrc (Join-Path $Share "VERSION"))) {
    Copy-Item -Force -LiteralPath $verSrc -Destination (Join-Path $Share "VERSION")
}

# Bundled tools: LibreOffice (.doc) + C toolchain (oob ASan)
$ToolsSrc = Join-Path $SuiteDir "tools"
$ToolsDst = Join-Path $Share "tools"
$LoSoffice = Join-Path $ToolsDst "libreoffice\program\soffice.exe"
$CcClang = Join-Path $ToolsDst "c-toolchain\bin\clang.exe"
if (Test-Path -LiteralPath $ToolsSrc) {
    $inPlaceTools = Test-SamePath $ToolsSrc $ToolsDst
    if ($inPlaceTools) {
        Write-Host "-> tools\ already in place (in-place Setup)" -ForegroundColor Cyan
    } else {
        Write-Host "-> copying tools\ (LibreOffice / c-toolchain) -> $ToolsDst"
        if (Test-Path -LiteralPath $ToolsDst) {
            Remove-Item -Recurse -Force -LiteralPath $ToolsDst
        }
        Copy-Item -Recurse -Force -LiteralPath $ToolsSrc -Destination $ToolsDst
    }
} else {
    Write-Host "-> no suite tools\ (optional)" -ForegroundColor DarkGray
}
if (Test-Path -LiteralPath $LoSoffice) {
    try {
        [Environment]::SetEnvironmentVariable("ROOM_LIBREOFFICE", $LoSoffice, "User")
        [Environment]::SetEnvironmentVariable("PAPER_DERIVED_LIBREOFFICE", $LoSoffice, "User")
        $env:ROOM_LIBREOFFICE = $LoSoffice
        $env:PAPER_DERIVED_LIBREOFFICE = $LoSoffice
        Write-Host "OK doc converter: $LoSoffice" -ForegroundColor Green
    } catch {
        Write-Host "WARNING: could not set ROOM_LIBREOFFICE: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "-> no tools\libreoffice. .doc may need Word or system LibreOffice" -ForegroundColor DarkGray
}
if (-not (Test-Path -LiteralPath $CcClang)) {
    $CcClang = Join-Path $ToolsDst "c-toolchain\bin\clang"
}
if (Test-Path -LiteralPath $CcClang) {
    try {
        $tcRoot = Join-Path $ToolsDst "c-toolchain"
        $tcBin = Join-Path $tcRoot "bin"
        [Environment]::SetEnvironmentVariable("OOB_CC", $CcClang, "User")
        [Environment]::SetEnvironmentVariable("ROOM_CC", $CcClang, "User")
        [Environment]::SetEnvironmentVariable("ROOM_C_TOOLCHAIN", $tcRoot, "User")
        [Environment]::SetEnvironmentVariable("ROOM_HOME", $Share, "User")
        $env:OOB_CC = $CcClang
        $env:ROOM_CC = $CcClang
        $env:ROOM_C_TOOLCHAIN = $tcRoot
        $env:ROOM_HOME = $Share
        # Prepend toolchain bin to user PATH once
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($null -eq $userPath) { $userPath = "" }
        if ($userPath -notlike "*$tcBin*") {
            $newPath = if ($userPath.Trim().Length -eq 0) { $tcBin } else { "$tcBin;$userPath" }
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            $env:Path = "$tcBin;$env:Path"
        }
        Write-Host "OK asan toolchain: $CcClang" -ForegroundColor Green
    } catch {
        Write-Host "WARNING: could not set OOB_CC: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "-> no tools\c-toolchain. oob ASan needs clang (re-pack with fetch-c-toolchain)" -ForegroundColor Yellow
}

$themeDark = Join-Path $BinDir "theme\dark.json"
if ($HasPi -and -not (Test-Path -LiteralPath $themeDark)) {
    Write-Host "WARNING: pi.exe present but theme\dark.json missing - re-pack suite with pi assets" -ForegroundColor Yellow
}

$cfgDst = Join-Path $CfgDir "config.toml"
$cfgSrc = Join-Path $SuiteDir "config.example.toml"
if (-not (Test-Path -LiteralPath $cfgDst) -and (Test-Path -LiteralPath $cfgSrc)) {
    Copy-Item -LiteralPath $cfgSrc -Destination $cfgDst
    Write-Host "-> wrote $cfgDst (edit provider/model)" -ForegroundColor Yellow
}

function Install-SuiteSkills {
    $srcRoot = Join-Path $SuiteDir "skills"
    if (-not (Test-Path -LiteralPath $srcRoot)) {
        Write-Fail "suite has no skills\ folder at $srcRoot"
    }
    # Room-branded pi default: ~/.config/room-tui/agent (NOT system ~/.pi, NOT old pi-agent)
    $roomPiAgent = Join-Path $env:USERPROFILE ".config\room-tui\agent"
    $roomPiSkills = Join-Path $roomPiAgent "skills"
    $productSkills = Join-Path $Share "skills"
    $binSkills = Join-Path $BinDir "skills"
    $destRoots = @(
        $roomPiSkills,
        $productSkills,
        $binSkills
    )
    New-Item -ItemType Directory -Path $roomPiAgent -Force | Out-Null
    $roomSettings = Join-Path $roomPiAgent "settings.json"
    if (-not (Test-Path -LiteralPath $roomSettings)) {
        Set-Content -Path $roomSettings -Value "{}" -Encoding UTF8
    }
    $installed = 0
    $skillDirs = @(Get-ChildItem -Directory -LiteralPath $srcRoot -ErrorAction SilentlyContinue)
    foreach ($entry in $skillDirs) {
        $name = $entry.Name
        $src = $entry.FullName
        if (-not (Test-Path -LiteralPath (Join-Path $src "SKILL.md"))) {
            Write-Host "WARNING: skip skill $name (no SKILL.md)" -ForegroundColor Yellow
            continue
        }
        foreach ($root in $destRoots) {
            $dst = Join-Path $root $name
            # CRITICAL: Inno in-place install has product skills == suite skills.
            # Never Remove-Item the source tree.
            if (Test-SamePath $src $dst) {
                Write-Host "  skill $name already at $dst (skip self-copy)" -ForegroundColor DarkGray
                continue
            }
            New-Item -ItemType Directory -Path $root -Force | Out-Null
            if (Test-Path -LiteralPath $dst) {
                Remove-Item -Recurse -Force -LiteralPath $dst
            }
            # Copy whole skill directory (NOT -LiteralPath with "*":
            # LiteralPath does not expand wildcards on Windows PS 5.1, so
            # Join-Path $src "*" copies nothing and SKILL.md check fails).
            Copy-Item -Recurse -Force -LiteralPath $src -Destination $dst
            if (-not (Test-Path -LiteralPath (Join-Path $dst "SKILL.md"))) {
                Write-Host "  src listing:" -ForegroundColor Yellow
                Get-ChildItem -LiteralPath $src -ErrorAction SilentlyContinue |
                    ForEach-Object { Write-Host "    $($_.Name)" }
                Write-Fail "skill copy failed: $dst (from $src)"
            }
        }
        $installed++
        Write-Host "OK skill $name" -ForegroundColor Green
        Write-Host "    -> $roomPiSkills\$name  (Room pi-agent, isolated)" -ForegroundColor DarkGray
        Write-Host "    -> $productSkills\$name" -ForegroundColor DarkGray
        Write-Host "    -> $binSkills\$name" -ForegroundColor DarkGray
    }
    if ($installed -lt 1) {
        Write-Fail "no skills installed from suite\skills ($srcRoot)"
    }
    foreach ($req in @("paper-derived", "oob-divzero")) {
        $check = Join-Path $roomPiSkills "$req\SKILL.md"
        $check2 = Join-Path $productSkills "$req\SKILL.md"
        if (-not (Test-Path -LiteralPath $check)) {
            Write-Fail "required skill missing in Room pi-agent: $check"
        }
        if (-not (Test-Path -LiteralPath $check2)) {
            Write-Fail "required skill missing in product skills: $check2"
        }
        Write-Host "OK skill $req (product + pi-agent)" -ForegroundColor Green
    }
    try {
        # Room-branded pi (piConfig.name=room) reads ROOM_CODING_AGENT_DIR
        [Environment]::SetEnvironmentVariable("ROOM_CODING_AGENT_DIR", $roomPiAgent, "User")
        [Environment]::SetEnvironmentVariable("ROOM_PI_AGENT_DIR", $roomPiAgent, "User")
        [Environment]::SetEnvironmentVariable("PI_CODING_AGENT_DIR", $roomPiAgent, "User")
        $env:ROOM_CODING_AGENT_DIR = $roomPiAgent
        $env:ROOM_PI_AGENT_DIR = $roomPiAgent
        $env:PI_CODING_AGENT_DIR = $roomPiAgent
        Write-Host "OK Room agent dir -> $roomPiAgent" -ForegroundColor Green
        Write-Host "   ROOM_CODING_AGENT_DIR set (NOT system ~/.pi)" -ForegroundColor Green
    } catch {
        Write-Host "WARNING: could not set Room agent env vars: $_" -ForegroundColor Yellow
    }
}

Write-Host "-> installing required skills"
Install-SuiteSkills

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $userPath) { $userPath = "" }
if ($userPath -notlike "*$BinDir*") {
    Write-Host "-> adding to user PATH: $BinDir" -ForegroundColor Yellow
    $newPath = if ($userPath.Trim().Length -eq 0) { $BinDir } else { "$userPath;$BinDir" }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$env:Path;$BinDir"
    Write-Host "  PATH updated. Open a NEW terminal for it to apply everywhere." -ForegroundColor Yellow
} else {
    Write-Host "-> PATH already contains $BinDir"
}

$env:ROOM_INSTALL_BIN = $BinDir
try {
    [Environment]::SetEnvironmentVariable("ROOM_INSTALL_BIN", $BinDir, "User")
    if ($HasPi) {
        [Environment]::SetEnvironmentVariable("PI_BIN", (Join-Path $BinDir "pi.exe"), "User")
    }
} catch {
    Write-Host "WARNING: could not set User env ROOM_INSTALL_BIN: $_" -ForegroundColor Yellow
}
if ($HasPi) {
    $env:PI_BIN = (Join-Path $BinDir "pi.exe")
}

Write-Host ""
$roomExe = Join-Path $BinDir "room.exe"
$pdExe = Join-Path $BinDir "paper-derived.exe"
if (-not (Test-Path -LiteralPath $roomExe)) {
    Write-Fail "install failed - $roomExe missing after copy"
}
if (-not (Test-Path -LiteralPath $pdExe)) {
    Write-Fail "install failed - $pdExe missing after copy"
}
$instInfo = Get-Item -LiteralPath $roomExe
$instSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $roomExe).Hash.ToLowerInvariant()
if ($instSha -ne $suiteRoomSha) {
    Write-Fail "installed room.exe sha256 does not match suite (close room and re-run)"
}
$pdInfo = Get-Item -LiteralPath $pdExe
if ($pdInfo.Length -lt 1MB) {
    Write-Fail "paper-derived.exe looks corrupt/too small ($($pdInfo.Length) bytes)"
}
Write-Host ("OK room.exe          -> {0}" -f $roomExe) -ForegroundColor Green
Write-Host ("    mtime={0}  size={1:N2} MB  sha256={2}..." -f `
    $instInfo.LastWriteTime, ($instInfo.Length / 1MB), $instSha.Substring(0, 12)) -ForegroundColor Green
Write-Host ("OK paper-derived.exe -> {0}" -f $pdExe) -ForegroundColor Green
Write-Host ("    size={0:N2} MB" -f ($pdInfo.Length / 1MB)) -ForegroundColor Green

$oobExe = Join-Path $BinDir "oob-divzero.exe"
if (Test-Path -LiteralPath $oobExe) {
    Write-Host ("OK oob-divzero.exe   -> {0}" -f $oobExe) -ForegroundColor Green
} else {
    Write-Host "WARNING: oob-divzero.exe missing - re-pack suite with -OobDivzero" -ForegroundColor Yellow
}

# Smoke engine (do not fail install on non-zero if binary runs; only fail if cannot start)
Write-Host "-> smoke paper-derived version" -ForegroundColor Cyan
$smokeOk = $false
try {
    $pdOut = & $pdExe version 2>&1 | Out-String
    $smokeOk = $true
    $first = ($pdOut -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
    if ($first) { Write-Host "    $first" -ForegroundColor Green }
    else { Write-Host "    (version ran)" -ForegroundColor Green }
} catch {
    Write-Fail "paper-derived.exe failed to run: $_"
}
if (-not $smokeOk) {
    Write-Fail "paper-derived.exe smoke did not run"
}

if (Test-Path -LiteralPath $oobExe) {
    Write-Host "-> smoke oob-divzero --version" -ForegroundColor Cyan
    try {
        $oobOut = & $oobExe --version 2>&1 | Out-String
        $first = ($oobOut -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
        if ($first) { Write-Host "    $first" -ForegroundColor Green }
        else { Write-Host "    (version ran)" -ForegroundColor Green }
    } catch {
        Write-Host "WARNING: oob-divzero --version failed: $_" -ForegroundColor Yellow
    }
}

foreach ($req in @("paper-derived", "oob-divzero")) {
    $skillCheck = Join-Path $env:USERPROFILE ".config\room-tui\agent\skills\$req\SKILL.md"
    $skillProd = Join-Path $Share "skills\$req\SKILL.md"
    if (-not (Test-Path -LiteralPath $skillCheck) -or -not (Test-Path -LiteralPath $skillProd)) {
        Write-Fail "$req skill incomplete (pi-agent=$(Test-Path -LiteralPath $skillCheck) product=$(Test-Path -LiteralPath $skillProd))"
    }
    Write-Host "OK $req skill (one-click complete)" -ForegroundColor Green
}

if ($HasPi) {
    Write-Host "OK room agent        -> $BinDir\pi.exe (bundled)" -ForegroundColor Green
    if (Test-Path -LiteralPath $themeDark) {
        Write-Host "OK agent theme       -> $BinDir\theme\" -ForegroundColor Green
    }
    $piStamp = Join-Path $BinDir "pi.ROOM.txt"
    if (Test-Path -LiteralPath $piStamp) {
        $brandLine = Get-Content -LiteralPath $piStamp | Where-Object { $_ -like "brand=*" } | Select-Object -First 1
        if ($brandLine -eq "brand=room") {
            Write-Host "OK Room-branded pi   -> $piStamp" -ForegroundColor Green
        } else {
            Write-Host "WARNING: pi.ROOM.txt brand is not room ($brandLine)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "WARNING: pi.ROOM.txt missing - suite may not be Room-branded pi" -ForegroundColor Yellow
    }
} else {
    Write-Host "WARNING: room agent binary not in suite" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "Next (new terminal - required so PATH refreshes):"
Write-Host "  room --version"
Write-Host "  room doctor"
Write-Host "  cd C:\path\to\your-project"
Write-Host "  room"
Write-Host ""

# Seed skills via room.exe itself (uses embedded packaging/skills if suite copy missed)
# Hard-fail install if required skills still missing after seed.
if (Test-Path -LiteralPath $roomExe) {
    $env:ROOM_INSTALL_BIN = $BinDir
    if ($HasPi) { $env:PI_BIN = (Join-Path $BinDir "pi.exe") }
    try {
        & $roomExe --version 2>&1 | Out-Host
        Write-Host "-> room skills-seed (required skills: paper-derived, oob-divzero)" -ForegroundColor Cyan
        & $roomExe skills-seed 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            Write-Fail "room skills-seed failed (exit $LASTEXITCODE) - required skills not installed"
        }
        Write-Host "-- room doctor (preview; ignored for install exit code) --"
        & $roomExe doctor 2>&1 | Out-Host
    } catch {
        Write-Fail "room post-install check failed: $_"
    }
}

Write-Host ""
Write-Host "OK Room install complete" -ForegroundColor Green
Write-Host "  log: $LogFile" -ForegroundColor DarkGray
try { Stop-Transcript | Out-Null } catch { }

# Explicit success - do not leak native command exit codes
exit 0
