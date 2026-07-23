# Build Room-branded pi from third_party\pi submodule only.
# Never use code.research\pi.
#
# Usage:
#   .\scripts\build-room-pi.ps1
#   .\scripts\build-room-pi.ps1 -PiRoomSrc D:\src\room-tui\third_party\pi

param(
    [string]$PiRoomSrc = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$DefaultSrc = Join-Path $Root "third_party\pi"
if (-not $PiRoomSrc) {
    if ($env:PI_ROOM_SRC) { $PiRoomSrc = $env:PI_ROOM_SRC } else { $PiRoomSrc = $DefaultSrc }
}
if (-not (Test-Path -LiteralPath $PiRoomSrc)) {
    Write-Host "ERROR: Room pi source not found: $PiRoomSrc" -ForegroundColor Red
    Write-Host "  git submodule update --init --recursive" -ForegroundColor Yellow
    exit 1
}
$PiRoomSrc = (Resolve-Path -LiteralPath $PiRoomSrc).Path

$norm = $PiRoomSrc.Replace('\', '/').ToLowerInvariant()
if ($norm -match '/code\.research/pi/?$') {
    Write-Host "ERROR: Refusing PiRoomSrc=$PiRoomSrc" -ForegroundColor Red
    Write-Host "  Use room-tui\third_party\pi only." -ForegroundColor Yellow
    exit 1
}

$Ca = Join-Path $PiRoomSrc "packages\coding-agent"
if (-not (Test-Path -LiteralPath (Join-Path $Ca "package.json"))) {
    throw "Missing packages\coding-agent\package.json under $PiRoomSrc"
}

Write-Host "-> apply Room pi brand (scheme B)"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) { throw "python required for apply-room-pi-brand.py" }
$verify = Join-Path $Root "scripts\verify-room-pi.py"
& $py.Source (Join-Path $Root "scripts\apply-room-pi-brand.py") $PiRoomSrc
if ($LASTEXITCODE -ne 0) { throw "apply-room-pi-brand failed" }

# Cross-platform shell UTF-8 (macOS locale + Win10/11 PS multi-decode)
$Patch = Join-Path $Root "scripts\patches\room-pi-utf8-shell.patch"
if (Test-Path -LiteralPath $Patch) {
    Write-Host "-> apply Room pi UTF-8 shell patch (macOS + Win10/11)"
    Push-Location $PiRoomSrc
    try {
        git apply --check $Patch 2>$null
        if ($LASTEXITCODE -eq 0) {
            git apply $Patch
            Write-Host "   applied room-pi-utf8-shell.patch"
        } else {
            git apply --reverse --check $Patch 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "   already applied room-pi-utf8-shell.patch"
            } else {
                Write-Host "WARN: could not apply room-pi-utf8-shell.patch (source drift?)" -ForegroundColor Yellow
            }
        }
    } finally {
        Pop-Location
    }
}

& $py.Source $verify --source $PiRoomSrc
if ($LASTEXITCODE -ne 0) { throw "verify-room-pi --source failed" }

if (-not (Get-Command bun -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: bun not on PATH" -ForegroundColor Red
    exit 1
}

Write-Host "========================================"
Write-Host "  Room-branded pi build"
Write-Host "  source: $PiRoomSrc"
Write-Host "========================================"

$rootPkg = Join-Path $PiRoomSrc "package.json"
if (Test-Path -LiteralPath $rootPkg) {
    Write-Host "-> npm install (monorepo root)"
    Push-Location $PiRoomSrc
    try { npm install 2>&1 | Out-Host } finally { Pop-Location }
}

Write-Host "-> npm run build:binary"
Push-Location $Ca
try {
    npm run build:binary 2>&1 | Out-Host
} finally {
    Pop-Location
}

$Out = $null
foreach ($cand in @((Join-Path $Ca "dist\pi.exe"), (Join-Path $Ca "dist\pi"))) {
    if (Test-Path -LiteralPath $cand) { $Out = $cand; break }
}
if (-not $Out) { throw "build:binary did not produce dist\pi[.exe]" }

$StageDir = Join-Path $Root "dist\bin"
New-Item -ItemType Directory -Path $StageDir -Force | Out-Null
$StageName = if ($Out -like "*.exe") { "pi.exe" } else { "pi" }
Copy-Item -Force -LiteralPath $Out (Join-Path $StageDir $StageName)
$theme = Join-Path $Ca "dist\theme"
if (Test-Path -LiteralPath $theme) {
    $dstTheme = Join-Path $StageDir "theme"
    if (Test-Path $dstTheme) { Remove-Item -Recurse -Force $dstTheme }
    Copy-Item -Recurse -Force $theme $dstTheme
}

@"
brand=room
configDir=.config/room-tui
default_agent=~/.config/room-tui/agent
env=ROOM_CODING_AGENT_DIR
source=$PiRoomSrc
binary=$Out
built=$((Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"))
"@ | Set-Content -Path (Join-Path $StageDir "pi.ROOM.txt") -Encoding UTF8

$stagePi = Join-Path $StageDir $StageName
& $py.Source $verify --dist $StageDir --binary $stagePi --repo $Root
if ($LASTEXITCODE -ne 0) { throw "verify-room-pi after build failed" }

Write-Host ""
Write-Host "OK Room pi: $stagePi (verified)" -ForegroundColor Green
Write-Host "   default agent dir: ~/.config/room-tui/agent"
