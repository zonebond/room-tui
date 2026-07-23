"""PromptHandle chunked input-register helpers."""

from pathlib import Path

from room_tui.engine.adapter import PromptHandle


def test_prompt_handle_single() -> None:
    p = Path("/tmp/reg.md")
    h = PromptHandle(path=p)
    assert h.all_prompt_files == [p]
    assert not h.is_chunked


def test_prompt_handle_chunked() -> None:
    chunks = [Path(f"/tmp/reg.chunk-{i}.md") for i in range(3)]
    h = PromptHandle(path=chunks[0], chunk_paths=chunks)
    assert h.is_chunked
    assert h.all_prompt_files == chunks
