"""Grok-like rewind: picker + chat/session truncate."""

from __future__ import annotations

import json
from pathlib import Path

from room_tui.llm.rewind import (
    format_rewind_dropdown,
    preview_prompt,
    resolve_rewind_selection,
    truncate_pi_session_before_user,
)
from room_tui.workspace import Workspace


def test_preview_prompt_collapses_ws() -> None:
    assert preview_prompt("a\n  b\tc") == "a b c"
    assert preview_prompt("x" * 100, max_cells=10).endswith("…")


def test_resolve_newest_first() -> None:
    items = [
        {"history_index": 0, "user_ordinal": 0, "text": "first"},
        {"history_index": 2, "user_ordinal": 1, "text": "second"},
        {"history_index": 4, "user_ordinal": 2, "text": "third"},
    ]
    # selected 0 = newest
    assert resolve_rewind_selection(items, 0)["text"] == "third"
    assert resolve_rewind_selection(items, 2)["text"] == "first"


def test_format_rewind_dropdown_has_header() -> None:
    items = [{"history_index": 0, "user_ordinal": 0, "text": "hi"}]
    text, rows = format_rewind_dropdown(items, selected=0)
    assert "Rewind" in text
    assert "hi" in text
    assert rows >= 2


def test_workspace_truncate_before_user(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.append_chat_message("user", "u1")
    ws.append_chat_message("assistant", "a1")
    ws.append_chat_message("user", "u2")
    ws.append_chat_message("assistant", "a2")
    points = ws.list_user_rewind_points()
    assert len(points) == 2
    assert points[1]["text"] == "u2"
    # Rewind before u2 → keep u1 + a1
    kept = ws.truncate_chat_before_user(points[1]["history_index"])
    assert [r["role"] for r in kept] == ["user", "assistant"]
    assert kept[0]["text"] == "u1"
    reloaded = ws.read_chat_history()
    assert len(reloaded) == 2
    assert reloaded[-1]["text"] == "a1"


def test_workspace_truncate_before_first_wipes(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.append_chat_message("user", "only")
    points = ws.list_user_rewind_points()
    kept = ws.truncate_chat_before_user(points[0]["history_index"])
    assert kept == []
    assert ws.read_chat_history() == []


def test_truncate_pi_session_before_user(tmp_path: Path) -> None:
    session = tmp_path / "pi-agent"
    session.mkdir()
    path = session / "2026_room-agent.jsonl"
    rows = [
        {"type": "session", "id": "room-agent"},
        {"type": "session_info", "id": "s1", "parentId": None},
        {
            "type": "message",
            "id": "m1",
            "parentId": "s1",
            "message": {"role": "user", "content": [{"type": "text", "text": "u1"}]},
        },
        {
            "type": "message",
            "id": "m2",
            "parentId": "m1",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "a1"}],
            },
        },
        {
            "type": "message",
            "id": "m3",
            "parentId": "m2",
            "message": {"role": "user", "content": [{"type": "text", "text": "u2"}]},
        },
        {
            "type": "message",
            "id": "m4",
            "parentId": "m3",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "a2"}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    ok = truncate_pi_session_before_user(
        session, keep_user_count=1, session_id="room-agent"
    )
    assert ok is True
    kept = [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    roles = []
    for r in kept:
        if r.get("type") == "message":
            roles.append(r["message"]["role"])
    assert roles == ["user", "assistant"]
    # only u1 remains
    user_text = kept[2]["message"]["content"][0]["text"]
    assert user_text == "u1"
