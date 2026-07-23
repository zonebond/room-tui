"""Bundled C toolchain discovery for oob-divzero ASan."""

from __future__ import annotations

from pathlib import Path

import pytest

from room_tui.config import find_asan_cc, find_bundled_cc


def test_find_bundled_cc_prefers_tools_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "Room"
    bin_dir = home / "bin"
    tc_bin = home / "tools" / "c-toolchain" / "bin"
    tc_bin.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    clang = tc_bin / "clang"
    clang.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    clang.chmod(0o755)

    monkeypatch.delenv("OOB_CC", raising=False)
    monkeypatch.delenv("ROOM_CC", raising=False)
    monkeypatch.delenv("ROOM_C_TOOLCHAIN", raising=False)
    monkeypatch.delenv("OOB_TOOLCHAIN", raising=False)
    monkeypatch.setenv("ROOM_INSTALL_BIN", str(bin_dir))
    monkeypatch.setenv("ROOM_HOME", str(home))

    found = find_bundled_cc()
    assert found is not None
    assert Path(found).resolve() == clang.resolve()


def test_find_bundled_cc_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "my-clang"
    fake.write_text("x", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("OOB_CC", str(fake))
    assert Path(find_bundled_cc() or "").resolve() == fake.resolve()


def test_find_asan_cc_falls_back_to_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OOB_CC", raising=False)
    monkeypatch.delenv("ROOM_CC", raising=False)
    monkeypatch.delenv("ROOM_INSTALL_BIN", raising=False)
    monkeypatch.delenv("ROOM_HOME", raising=False)
    monkeypatch.delenv("ROOM_C_TOOLCHAIN", raising=False)
    # No suite layout — should still find system clang/gcc on macOS/Linux
    cc = find_asan_cc()
    assert cc is not None
