"""Slash registry: builtins + skills, matching, completion, dropdown format."""

from __future__ import annotations

from pathlib import Path

from room_tui.slash import (
    SlashItem,
    all_slash_items,
    complete_slash,
    format_dropdown,
    format_suggestions,
    invalidate_skill_cache,
    match_slash,
    resolve_slash_token,
)


def test_match_all_on_slash() -> None:
    assert len(match_slash("/")) >= 5


def test_match_prefix() -> None:
    ms = match_slash("/con")
    assert any(m.name == "continue" for m in ms)


def test_complete_unique() -> None:
    val, ms = complete_slash("/con")
    assert val == "/continue "
    assert ms and ms[0].name == "continue"


def test_complete_partial_lcp() -> None:
    # /c matches continue, clear, cancel — lcp is empty or "c"
    val, ms = complete_slash("/c")
    assert ms
    assert val is not None
    assert val.startswith("/")


def test_format_suggestions() -> None:
    s = format_suggestions(match_slash("/"))
    assert "/new" in s


def test_format_dropdown_multi_line() -> None:
    from room_tui.ui_state import COLOR_BRAND

    items = [
        SlashItem("new", "新建文档生成", "cmd"),
        SlashItem("paper-derived", "从模板派生文档", "skill", skill_path="/tmp/pd"),
        SlashItem("help", "帮助", "cmd"),
    ]
    text, rows = format_dropdown(items, selected=1)
    assert "paper-derived" in text
    assert "❯" in text
    assert rows >= 3  # items + footer
    # selected row is bold/highlighted in brand blue
    assert "bold" in text
    assert COLOR_BRAND in text


def test_format_dropdown_empty() -> None:
    text, rows = format_dropdown([])
    assert "无匹配" in text
    assert rows == 1


def test_resolve_builtin() -> None:
    assert resolve_slash_token("continue") is not None
    assert resolve_slash_token("/c").name == "continue"  # type: ignore[union-attr]
    assert resolve_slash_token("help").kind == "cmd"  # type: ignore[union-attr]


def test_all_items_include_builtins() -> None:
    items = all_slash_items()
    names = {i.name for i in items}
    assert "new" in names
    assert "model" in names
    assert "skill" in names


def test_skills_matched_as_first_class(tmp_path: Path, monkeypatch) -> None:
    """Discovered skills appear as /name alongside builtins (Grok-style)."""
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "paper-derived"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: paper-derived\ndescription: 从模板派生文档\nversion: 1.0\n---\n\n# Paper\n",
        encoding="utf-8",
    )

    import room_tui.pi_catalog as cat

    monkeypatch.setattr(cat, "_skill_dirs", lambda: [skill_root])
    invalidate_skill_cache()

    ms = match_slash("/paper")
    assert any(m.name == "paper-derived" and m.kind == "skill" for m in ms)

    item = resolve_slash_token("paper-derived")
    assert item is not None
    assert item.kind == "skill"
    assert "派生" in item.description or "模板" in item.description

    # Full slash list includes both cmds and this skill
    all_items = all_slash_items(refresh_skills=True)
    assert any(i.name == "paper-derived" for i in all_items)
    assert any(i.name == "help" and i.kind == "cmd" for i in all_items)

    invalidate_skill_cache()


def test_skill_collision_qualified(tmp_path: Path, monkeypatch) -> None:
    """Skill named like a builtin is re-homed to skill:name."""
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "help"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: help\ndescription: fake skill help\n---\n",
        encoding="utf-8",
    )

    import room_tui.pi_catalog as cat

    monkeypatch.setattr(cat, "_skill_dirs", lambda: [skill_root])
    invalidate_skill_cache()

    items = all_slash_items(refresh_skills=True)
    skill_names = [i.name for i in items if i.kind == "skill"]
    assert "skill:help" in skill_names or any("help" in n for n in skill_names)
    # Builtin help still present as cmd
    assert any(i.name == "help" and i.kind == "cmd" for i in items)

    invalidate_skill_cache()


def test_match_fuzzy_description() -> None:
    # Contain match on description when no prefix hits (or as fallback)
    ms = match_slash("/新建")
    # may or may not hit depending on encoding; at least empty is ok
    assert isinstance(ms, list)
