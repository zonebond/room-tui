"""Required skill seed from packaging/bundled sources."""

from __future__ import annotations

from pathlib import Path

import pytest

from room_tui.pi_catalog import REQUIRED_SKILLS, list_skills, missing_required_skills
from room_tui.pi_env import (
    bundled_skills_root,
    seed_required_skills_into_room_pi,
)


def test_bundled_skills_root_finds_packaging_skills() -> None:
    root = bundled_skills_root()
    assert root is not None
    assert (root / "paper-derived" / "SKILL.md").is_file()
    assert (root / "oob-divzero" / "SKILL.md").is_file()
    assert "oob-divzero" in REQUIRED_SKILLS
    assert "paper-derived" in REQUIRED_SKILLS


def test_seed_required_skills_into_tmp_pi_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from room_tui import pi_env

    agent = tmp_path / "agent"
    monkeypatch.setattr(pi_env, "_isolation_ready", False)
    for key in (
        "ROOM_CODING_AGENT_DIR",
        "ROOM_PI_AGENT_DIR",
        "PI_CODING_AGENT_DIR",
        "ROOM_INSTALL_BIN",
        "LOCALAPPDATA",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))

    seeded = seed_required_skills_into_room_pi()
    skill_md = agent / "skills" / "paper-derived" / "SKILL.md"
    oob_md = agent / "skills" / "oob-divzero" / "SKILL.md"
    assert skill_md.is_file(), f"expected skill after seed={seeded!r}"
    assert oob_md.is_file(), f"expected oob skill after seed={seeded!r}"
    # First seed may copy or report empty if already present; file is the contract
    if seeded:
        assert "paper-derived" in seeded or "oob-divzero" in seeded
    text = skill_md.read_text(encoding="utf-8")
    assert "paper-derived" in text.lower() or "Paper" in text
    assert "oob" in oob_md.read_text(encoding="utf-8").lower()

    # second call is idempotent (already present)
    seeded2 = seed_required_skills_into_room_pi()
    assert seeded2 == []

    missing = missing_required_skills()
    assert missing == []
    names = {s.name.lower() for s in list_skills()}
    assert "paper-derived" in names
    assert "oob-divzero" in names
    for req in REQUIRED_SKILLS:
        assert req.lower() in names or any(
            s.path.parent.name.lower() == req.lower() for s in list_skills()
        )
