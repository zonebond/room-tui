"""Suite install: sibling paper-derived resolution."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from room_tui import config as cfg_mod


def test_default_paper_derived_uses_sibling_when_install_bin_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PAPER_DERIVED_BIN", raising=False)
    name = "paper-derived.exe" if sys.platform == "win32" else "paper-derived"
    sibling = tmp_path / name
    sibling.write_text("#!/bin/sh\n", encoding="utf-8")
    sibling.chmod(0o755)
    monkeypatch.setenv("ROOM_INSTALL_BIN", str(tmp_path))
    assert cfg_mod._default_paper_derived_bin() == str(sibling)


def test_env_paper_derived_bin_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_DERIVED_BIN", "/custom/pd")
    monkeypatch.setenv("ROOM_INSTALL_BIN", str(tmp_path))
    assert cfg_mod._default_paper_derived_bin() == "/custom/pd"


def test_no_sibling_falls_back_to_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PAPER_DERIVED_BIN", raising=False)
    monkeypatch.setenv("ROOM_INSTALL_BIN", str(tmp_path))  # empty dir
    assert cfg_mod._default_paper_derived_bin() == "paper-derived"


def test_default_pi_uses_sibling_when_install_bin_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PI_BIN", raising=False)
    name = "pi.exe" if sys.platform == "win32" else "pi"
    sibling = tmp_path / name
    sibling.write_text("#!/bin/sh\n", encoding="utf-8")
    sibling.chmod(0o755)
    monkeypatch.setenv("ROOM_INSTALL_BIN", str(tmp_path))
    assert cfg_mod._default_pi_bin() == str(sibling)


def test_resolve_bin_absolute_file(tmp_path: Path) -> None:
    f = tmp_path / "tool"
    f.write_text("x", encoding="utf-8")
    f.chmod(0o755)
    assert cfg_mod.resolve_bin(str(f)) == str(f.resolve())


def test_find_bundled_soffice_from_install_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ROOM_LIBREOFFICE", raising=False)
    monkeypatch.delenv("PAPER_DERIVED_LIBREOFFICE", raising=False)
    monkeypatch.delenv("LIBREOFFICE_PROGRAM", raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    so = tmp_path / "tools" / "libreoffice" / "program" / "soffice.exe"
    so.parent.mkdir(parents=True)
    so.write_text("fake", encoding="utf-8")
    monkeypatch.setenv("ROOM_INSTALL_BIN", str(bin_dir))
    # clear which() noise: empty PATH
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    hit = cfg_mod.find_bundled_soffice()
    assert hit is not None
    assert Path(hit).resolve() == so.resolve()


def test_find_bundled_soffice_env_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    so = tmp_path / "custom" / "soffice"
    so.parent.mkdir(parents=True)
    so.write_text("x", encoding="utf-8")
    so.chmod(0o755)
    monkeypatch.setenv("ROOM_LIBREOFFICE", str(so))
    assert Path(cfg_mod.find_bundled_soffice()).resolve() == so.resolve()


def test_engine_subprocess_env_prepends_program_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    so = tmp_path / "tools" / "libreoffice" / "program" / "soffice.exe"
    so.parent.mkdir(parents=True)
    so.write_text("x", encoding="utf-8")
    monkeypatch.setenv("ROOM_LIBREOFFICE", str(so))
    monkeypatch.setenv("PATH", "/usr/bin")
    env = cfg_mod.engine_subprocess_env({"PATH": "/usr/bin", "FOO": "1"})
    assert env["FOO"] == "1"
    assert env["ROOM_LIBREOFFICE"] == str(so)
    assert str(so.parent) in env["PATH"].split(os.pathsep)
    assert env["PATH"].split(os.pathsep)[0] == str(so.parent)
