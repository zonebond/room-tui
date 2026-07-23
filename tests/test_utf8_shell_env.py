"""Cross-platform UTF-8 shell env helpers (macOS / Win10 / Win11)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from room_tui.pi_env import (
    ensure_utf8_shell_settings,
    pi_agent_environ,
    preferred_utf8_locale,
)


def test_preferred_locale_is_utf8() -> None:
    loc = preferred_utf8_locale()
    assert "UTF-8" in loc or "utf-8" in loc
    if sys.platform == "darwin":
        assert loc == "en_US.UTF-8"
    else:
        assert loc == "C.UTF-8"


def test_ensure_utf8_shell_settings_all_platforms(tmp_path: Path) -> None:
    root = tmp_path / "agent"
    root.mkdir()
    (root / "settings.json").write_text("{}\n", encoding="utf-8")
    ensure_utf8_shell_settings(root)
    data = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    prefix = data.get("shellCommandPrefix") or ""
    assert "ROOM_UTF8=1" in prefix
    assert "PYTHONUTF8=1" in prefix
    assert preferred_utf8_locale() in prefix
    # idempotent
    ensure_utf8_shell_settings(root)
    data2 = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert data2["shellCommandPrefix"].count("ROOM_UTF8=1") == 1


def test_pi_agent_environ_utf8_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(tmp_path / "agent"))
    env = pi_agent_environ({"PATH": "/usr/bin"})
    assert env.get("PYTHONUTF8") == "1"
    assert env.get("PYTHONIOENCODING") == "utf-8"
    assert env.get("LANG") == preferred_utf8_locale()
    assert env.get("LC_ALL") == preferred_utf8_locale()
    if sys.platform == "win32":
        assert env.get("ROOM_FORCE_UTF8") == "1"
