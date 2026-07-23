# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for product `room` binary (onefile).
# Build via: ./scripts/build-binary.sh
from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

ROOT = Path(SPECPATH).resolve().parent  # packaging/ → repo root is parent
SRC = ROOT / "src"

# Textual / Rich need their CSS, templates, and widget assets.
datas: list = []
binaries: list = []
hiddenimports: list = [
    "room_tui",
    "room_tui.cli",
    "room_tui.app",
    "textual",
    "textual.app",
    "textual.widgets",
    "rich",
    "click",
]

for pkg in ("textual", "rich"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        # Fallback: at least collect package data if collect_all fails
        try:
            datas += collect_data_files(pkg)
        except Exception:
            pass

# Required product skills (paper-derived docs). Seeded at runtime into
# ~/.config/room-tui/agent/skills so doctor/TUI work even if the
# Windows installer forgot suite skills/.
_skills_root = ROOT / "packaging" / "skills"
if _skills_root.is_dir():
    for _path in _skills_root.rglob("*"):
        if not _path.is_file():
            continue
        # skip fat binaries if any slipped into the skill tree
        if _path.name in (
            "paper-derived",
            "paper-derived.exe",
            "oob-divzero",
            "oob-divzero.exe",
        ) or _path.suffix.lower() in (
            ".exe",
            ".whl",
            ".dll",
        ):
            continue
        if "pkg" in _path.parts:
            continue
        _rel_parent = _path.relative_to(_skills_root).parent
        _dest = str(Path("skills") / _rel_parent).replace("\\", "/")
        if _dest.endswith("/."):
            _dest = _dest[:-2]
        datas.append((str(_path), _dest))

a = Analysis(
    [str(SRC / "room_tui" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pytest",
        "IPython",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="room",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
