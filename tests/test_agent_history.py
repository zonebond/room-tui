"""Structured agent turn history (Grok-like process rows)."""

from __future__ import annotations

from pathlib import Path

from room_tui.llm.agent_blocks import AgentBlock, AgentTurn
from room_tui.llm.message_render import render_block
from room_tui.workspace import Workspace


def test_to_history_blocks_collapses_thinking() -> None:
    turn = AgentTurn(
        blocks=[
            AgentBlock(kind="thinking", text="I should list files"),
            AgentBlock(
                kind="tool",
                tool_name="bash",
                tool_args={"command": "ls"},
                tool_result="a\nb\n",
            ),
            AgentBlock(kind="text", text="目录是空的。"),
        ]
    )
    hist = turn.to_history_blocks(thought_elapsed_s=5.3, collapse_thinking=True)
    kinds = [h["kind"] for h in hist]
    assert kinds == ["thought", "tool", "text"]
    assert hist[0]["elapsed_s"] == 5.3
    assert hist[1]["tool_name"] == "bash"
    assert "目录" in hist[2]["text"]


def test_thought_render_header() -> None:
    r = render_block(AgentBlock(kind="thought", elapsed_s=5.3))
    assert r is not None
    # Text assemble → Text
    s = str(r)
    assert "Thought" in s
    assert "5.3" in s


def test_chat_history_roundtrip_blocks(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.ensure()
    ws.append_chat_message("user", "有代码吗？")
    ws.append_chat_message(
        "assistant",
        "没有。",
        blocks=[
            {"kind": "thought", "elapsed_s": 2.1},
            {
                "kind": "tool",
                "tool_name": "bash",
                "tool_args": {"command": "find . -name '*.py'"},
                "tool_result": "",
            },
            {"kind": "text", "text": "没有。"},
        ],
    )
    rows = ws.read_chat_history()
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[1]["blocks"]
    assert rows[1]["blocks"][0]["kind"] == "thought"
    assert rows[1]["blocks"][1]["kind"] == "tool"
    assert rows[1]["text"] == "没有。"


def test_block_to_dict_truncates_result() -> None:
    b = AgentBlock(kind="tool", tool_name="bash", tool_result="x" * 10000)
    d = b.to_dict(max_result=100)
    assert len(str(d["tool_result"])) <= 100
