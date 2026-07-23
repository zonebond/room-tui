"""Room pi must use isolated agent dir, never system ~/.pi by default."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from room_tui import pi_env
from room_tui.pi_env import (
    apply_room_pi_isolation,
    isolation_status,
    pi_agent_environ,
    system_pi_agent_dir,
)


@pytest.fixture(autouse=True)
def _reset_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pi_env, "_isolation_ready", False)
    for key in (
        "ROOM_CODING_AGENT_DIR",
        "ROOM_PI_AGENT_DIR",
        "PI_CODING_AGENT_DIR",
        "PI_HOME",
    ):
        monkeypatch.delenv(key, raising=False)


def test_apply_room_pi_isolation_sets_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "room-agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("PI_HOME", "/should/be/removed")
    monkeypatch.setattr(pi_env, "seed_required_skills_into_room_pi", lambda: [])

    root = apply_room_pi_isolation(seed_skills=True)
    assert root == agent.resolve()
    assert os.environ["ROOM_CODING_AGENT_DIR"] == str(agent.resolve())
    assert os.environ["PI_CODING_AGENT_DIR"] == str(agent.resolve())
    assert os.environ["ROOM_PI_AGENT_DIR"] == str(agent.resolve())
    assert "PI_HOME" not in os.environ
    assert (agent / "skills").is_dir()
    assert (agent / "settings.json").is_file()
    assert (agent / "auth.json").is_file()


def test_pi_agent_environ_forces_coding_agent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "room-agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    # Stale wrong value must be overwritten
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    monkeypatch.setattr(pi_env, "seed_required_skills_into_room_pi", lambda: [])

    env = pi_agent_environ({"PATH": "/usr/bin", "PI_HOME": "/tmp/nope"})
    assert env["ROOM_CODING_AGENT_DIR"] == str(agent.resolve())
    assert env["PI_CODING_AGENT_DIR"] == str(agent.resolve())
    assert "PI_HOME" not in env
    assert env["PI_CODING_AGENT_DIR"] != str(system_pi_agent_dir())


def test_isolation_status_detects_mispointed_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "room-agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    monkeypatch.setattr(pi_env, "seed_required_skills_into_room_pi", lambda: [])
    apply_room_pi_isolation(seed_skills=False)
    # Break isolation: point one env at system pi
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(Path.home() / ".pi" / "agent"))
    st = isolation_status()
    assert st["env_points_at_room"] is False
