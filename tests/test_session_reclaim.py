"""Session store path + reclaim orphaned generating sections."""

from __future__ import annotations

import json
from pathlib import Path

from room_tui.engine.adapter import EngineAdapter


def _write_session(path: Path, *, generating: list[str], ready: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sp: dict[str, dict] = {}
    for sid in generating:
        sp[sid] = {
            "section_id": sid,
            "status": "generating",
            "depends_on": [],
            "attempt_count": 1,
            "last_attempt_at": "",
        }
    for sid in ready or []:
        sp[sid] = {
            "section_id": sid,
            "status": "ready",
            "depends_on": [],
            "attempt_count": 0,
            "last_attempt_at": "",
        }
    path.write_text(
        json.dumps(
            {
                "session_id": path.parent.name,
                "template_id": "tpl-x",
                "section_progress": sp,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_session_store_prefers_project_pd(tmp_path: Path) -> None:
    """Engine prefers {cwd}/.pd/sessions — reclaim must hit the same path."""
    sid = "sess_test_local"
    local = tmp_path / ".pd" / "sessions" / sid / "session.json"
    _write_session(
        local,
        generating=["external-interface-requirements", "maintainability-requirements"],
    )
    # Global decoy with different content — must NOT be preferred when local exists
    global_dir = tmp_path / "home" / ".paper-derived" / "sessions" / sid
    # monkeypatch home via only checking local through cwd
    eng = EngineAdapter(cwd=tmp_path)
    store = eng.session_store_dir(sid)
    assert store is not None
    assert store == local.parent
    assert eng.session_json_path(sid) == local


def test_reclaim_generating_project_local(tmp_path: Path) -> None:
    sid = "sess_bafbc6732208"
    local = tmp_path / ".pd" / "sessions" / sid / "session.json"
    stuck = [
        "external-interface-requirements",
        "maintainability-requirements",
    ]
    _write_session(local, generating=stuck, ready=["scope"])

    eng = EngineAdapter(cwd=tmp_path)
    reclaimed = eng.reclaim_generating(sid, stuck)
    assert set(reclaimed) == set(stuck)

    data = json.loads(local.read_text(encoding="utf-8"))
    sp = data["section_progress"]
    for s in stuck:
        assert sp[s]["status"] == "ready"
    assert sp["scope"]["status"] == "ready"  # untouched


def test_reclaim_all_generating_when_ids_omitted(tmp_path: Path) -> None:
    sid = "sess_all"
    local = tmp_path / ".pd" / "sessions" / sid / "session.json"
    _write_session(local, generating=["a", "b"], ready=["c"])
    eng = EngineAdapter(cwd=tmp_path)
    reclaimed = eng.reclaim_generating(sid)
    assert set(reclaimed) == {"a", "b"}
    data = json.loads(local.read_text(encoding="utf-8"))
    assert data["section_progress"]["a"]["status"] == "ready"
    assert data["section_progress"]["c"]["status"] == "ready"


def test_reclaim_missing_session_returns_empty(tmp_path: Path) -> None:
    eng = EngineAdapter(cwd=tmp_path)
    assert eng.reclaim_generating("sess_missing") == []
