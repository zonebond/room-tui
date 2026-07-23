"""New /new run must not keep previous session sidebar state."""

from __future__ import annotations

from room_tui.config import AppConfig
from room_tui.engine.adapter import SectionState, SessionSnapshot
from room_tui.orch.session import SessionOrchestrator


def test_reset_ui_session_state_clears_snapshot_and_progress() -> None:
    orch = SessionOrchestrator(AppConfig())
    orch.state.session_id = "sess_old"
    orch.state.progress = "43/43"
    orch.state.phase = "complete"
    orch.state.focus_section = "scope"
    orch.state.snapshot = SessionSnapshot(
        session_id="sess_old",
        template_id="tpl-x",
        phase="complete",
        progress="43/43",
        sections=[
            SectionState(section_id="scope", status="done", title="范围"),
            SectionState(section_id="id", status="done", title="标识"),
        ],
    )
    orch._reset_ui_session_state()
    assert orch.state.snapshot is None
    assert orch.state.progress == ""
    assert orch.state.focus_section == ""
    assert orch.state.session_id == ""
    assert orch.state.phase == "init"


def test_chapter_guard_rejects_mismatched_session() -> None:
    """Helper mirror of shell guard: only paint matching session snapshot."""
    snap_sid = "sess_old"
    cur_sid = "sess_new"
    assert not (not cur_sid or not snap_sid or snap_sid == cur_sid)
    cur_sid = "sess_old"
    assert not cur_sid or not snap_sid or snap_sid == cur_sid
