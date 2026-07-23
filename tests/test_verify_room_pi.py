"""verify-room-pi.py packaging hard gates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify-room-pi.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_stamp_ok(tmp_path: Path) -> None:
    stamp = tmp_path / "pi.ROOM.txt"
    stamp.write_text(
        "\n".join(
            [
                "brand=room",
                "configDir=.config/room-tui",
                "env=ROOM_CODING_AGENT_DIR",
                "source=/repo/third_party/pi",
                "binary=/repo/dist/bin/pi",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    proc = _run("--stamp", str(stamp))
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_stamp_rejects_wrong_brand(tmp_path: Path) -> None:
    stamp = tmp_path / "pi.ROOM.txt"
    stamp.write_text(
        "brand=pi\nconfigDir=.pi\nenv=PI_CODING_AGENT_DIR\nsource=/x/third_party/pi\n",
        encoding="utf-8",
    )
    proc = _run("--stamp", str(stamp))
    assert proc.returncode != 0
    assert "brand=" in (proc.stderr + proc.stdout)


def test_binary_requires_stamp(tmp_path: Path) -> None:
    pi = tmp_path / "pi"
    pi.write_bytes(b"\0" * 100)
    proc = _run("--binary", str(pi), "--repo", str(ROOT))
    assert proc.returncode != 0
    assert "pi.ROOM.txt" in (proc.stderr + proc.stdout)


def test_suite_ok(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "pi").write_bytes(b"\0" * 50)
    (bin_dir / "theme").mkdir()
    (bin_dir / "theme" / "dark.json").write_text("{}", encoding="utf-8")
    (bin_dir / "pi.ROOM.txt").write_text(
        "brand=room\nconfigDir=.config/room-tui\nenv=ROOM_CODING_AGENT_DIR\n"
        "source=/r/third_party/pi\n",
        encoding="utf-8",
    )
    proc = _run("--suite", str(tmp_path))
    assert proc.returncode == 0, proc.stderr + proc.stdout
