# Build product `room.exe` (PyInstaller onefile) on Windows.
# Dev install is NOT this path.
#
# Usage (PowerShell, from repo root or any cwd):
#   powershell -ExecutionPolicy Bypass -File .\scripts\build-binary.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\build-binary.ps1 -Clean
#
# -Clean removes only room PyInstaller workdir + room/room-*.exe outputs.
# It does NOT wipe dist\bin entirely (keeps paper-derived.exe / pi.exe / theme/).
#
# Always reinstalls editable room-tui before PyInstaller so the onefile binary
# embeds the current src/ tree (avoids packaging yesterday's room.exe).
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Version = "0.1.0"
try {
    $Version = & python -c "import sys; sys.path.insert(0, r'src'); from room_tui import __version__; print(__version__)"
    if (-not $Version) { $Version = "0.1.0" }
} catch {
    $Version = "0.1.0"
}

$Arch = "x86_64"
try {
    $m = & python -c "import platform; print(platform.machine())"
    if ($m -match 'ARM|arm') { $Arch = "arm64" } else { $Arch = "x86_64" }
} catch { }

$OutDir = Join-Path $Root "dist\bin"
$WorkDir = Join-Path $Root "build\pyinstaller"
$Spec = Join-Path $Root "packaging\room.spec"
$Name = "room-$Version-windows-$Arch"

Write-Host "========================================"
Write-Host "  Room binary build (Windows)"
Write-Host "  version : $Version"
Write-Host "  target  : windows-$Arch"
Write-Host "========================================"

if ($Clean) {
    # Only room PyInstaller workdir + room outputs.
    # Keep paper-derived.exe / pi.exe / theme/ already staged in dist\bin.
    Write-Host "-> cleaning room build artifacts (preserving sidecars in dist\bin)"
    if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
    if (Test-Path $OutDir) {
        Get-ChildItem -Path $OutDir -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $n = $_.Name
            $isRoom =
                ($n -eq "room") -or
                ($n -eq "room.exe") -or
                ($n -like "room-*") -or
                ($n -eq "room_tui") -or
                ($n -like "room_tui*")
            if ($isRoom) {
                Remove-Item -Recurse -Force $_.FullName
                Write-Host "   removed $n"
            }
        }
    }
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "-> creating .venv"
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv venv .venv
    } else {
        & python -m venv .venv
    }
}

if (-not (Test-Path $VenvPython)) {
    throw "venv python not found: $VenvPython (is Python installed and on PATH?)"
}

Write-Host "-> reinstall room-tui (editable) + pyinstaller  [fresh src/]"
# Force reinstall so .venv never keeps a stale egg-link / wheel of room-tui.
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --reinstall -e ".[dev]" "pyinstaller>=6.0"
} else {
    & $VenvPython -m pip install -U pip -q
    & $VenvPython -m pip install --force-reinstall --no-deps -e .
    & $VenvPython -m pip install -e ".[dev]" "pyinstaller>=6.0"
}

$srcInfo = & $VenvPython -c @"
import room_tui, pathlib
p = pathlib.Path(room_tui.__file__).resolve()
print(room_tui.__version__)
print(p)
"@
Write-Host "   import room_tui: $srcInfo"
& $VenvPython -c "import PyInstaller"

# Wipe previous room outputs so we never leave a half-old binary if build fails mid-way
foreach ($old in @("room.exe", "room", "$Name.exe")) {
    $p = Join-Path $OutDir $old
    if (Test-Path $p) { Remove-Item -Force $p }
}

Write-Host "-> pyinstaller (always --clean)"
& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $OutDir `
    --workpath $WorkDir `
    $Spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Raw = Join-Path $OutDir "room.exe"
if (-not (Test-Path $Raw)) {
    Write-Host "ERROR: binary not found under $OutDir" -ForegroundColor Red
    Get-ChildItem $OutDir -ErrorAction SilentlyContinue
    exit 1
}

$Tagged = Join-Path $OutDir "$Name.exe"
Copy-Item -Force $Raw $Tagged

# Build stamp - package/install scripts verify this
$git = ""
try { $git = (& git -C $Root rev-parse --short HEAD 2>$null) } catch { }
if (-not $git) { $git = "nogit" }
$builtUtc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$sha = (Get-FileHash -Algorithm SHA256 $Raw).Hash.ToLowerInvariant()
$item = Get-Item $Raw
$stamp = @"
room_version=$Version
git=$git
built=$builtUtc
sha256=$sha
size=$($item.Length)
mtime_local=$($item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
path=$Raw
"@
$stampPath = Join-Path $OutDir "room.BUILD.txt"
Set-Content -Path $stampPath -Value $stamp -Encoding UTF8
Write-Host "   stamp: $stampPath"

$size = $item.Length / 1MB
Write-Host ""
Write-Host ("OK built: {0} ({1:N1} MB)" -f $Raw, $size) -ForegroundColor Green
Write-Host "  sha256 : $sha"
Write-Host "  built  : $builtUtc  git=$git"
Write-Host "  tagged : $Tagged"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Prepare paper-derived.exe (same Windows machine)"
Write-Host "  2. .\scripts\package-suite.ps1 -PaperDerived path\to\paper-derived.exe"
Write-Host "     (package-suite rebuilds room by default)"
Write-Host "  3. Smoke: $Raw --version"
