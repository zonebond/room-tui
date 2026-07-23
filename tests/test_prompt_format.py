from pathlib import Path

from room_tui.llm.prompt_format import parse_prompt_file


def test_parse_text_prompt(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text(
        "==== SYSTEM ====\nYou are sys\n==== USER ====\nDo the thing\n",
        encoding="utf-8",
    )
    parts = parse_prompt_file(p)
    assert "sys" in parts.system
    assert "Do the thing" in parts.user


def test_parse_json_prompt(tmp_path: Path) -> None:
    p = tmp_path / "p.json"
    p.write_text('{"system":"S","user":"U"}', encoding="utf-8")
    parts = parse_prompt_file(p)
    assert parts.system == "S"
    assert parts.user == "U"
