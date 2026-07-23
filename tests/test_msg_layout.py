"""Grok-like message list layout helpers."""

from datetime import datetime
from io import StringIO

from rich.console import Console
from rich.text import Text

from room_tui.llm.msg_layout import (
    GLYPH_PROMPT,
    MSG_INDENT,
    content_pad,
    format_short_timestamp,
    indented_band,
    paint_output_band,
    thought_markup,
    user_prompt_renderable,
)


def test_indent_is_grok_default() -> None:
    # ``❙  ◆ title`` → title starts at col 5
    assert MSG_INDENT == 5
    assert len(content_pad()) == 5


def test_block_gap_text_is_blank_row() -> None:
    from room_tui.llm.msg_layout import block_gap_text

    g = block_gap_text()
    assert g.plain.strip() == "" or g.plain == " "


def test_user_prompt_paints_full_width_bg() -> None:
    r = user_prompt_renderable("ls", width=40, when=datetime(2026, 7, 18, 15, 9))
    assert isinstance(r, Text)
    c = Console(file=StringIO(), width=40, force_terminal=True, color_system="truecolor")
    segs = list(c.render(r, c.options.update_width(40)))
    with_bg = [s for s in segs if s.text and s.text != "\n" and s.style and s.style.bgcolor]
    assert with_bg, "user prompt must paint background on segments"
    plain = r.plain
    assert "ls" in plain
    assert "3:09" in plain or "PM" in plain


def test_user_prompt_uses_grok_arrow() -> None:
    r = user_prompt_renderable("hello", width=40, show_timestamp=False)
    assert GLYPH_PROMPT in r.plain or "\u276f" in r.plain


def test_user_prompt_has_vertical_pad() -> None:
    """Every user block includes 1 full-width bg row above and below text."""
    r = user_prompt_renderable("hello", width=24, show_timestamp=False, vpad=1)
    assert isinstance(r, Text)
    rows = r.plain.split("\n")
    # top pad + content + bottom pad
    assert len(rows) >= 3
    # Top/bottom rows are full-width blanks (NBSP pad), content has ❯.
    assert GLYPH_PROMPT not in rows[0]
    assert GLYPH_PROMPT in rows[1] or "\u276f" in rows[1]
    assert GLYPH_PROMPT not in rows[-1]


def test_user_prompt_arrow_aligns_with_diamond() -> None:
    """❯ sits in the same column as process ◆ (blank rail gutter)."""
    from rich.markup import render as render_markup

    from room_tui.llm.msg_layout import (
        GLYPH_DIAMOND,
        MSG_ACCENT_W,
        MSG_RAIL_GAP,
        thought_markup,
        tool_header_markup,
    )

    r = user_prompt_renderable("Show me the money", width=48, show_timestamp=False)
    assert isinstance(r, Text)
    # Content row (skip top vpad blank).
    plain = next(row for row in r.plain.split("\n") if GLYPH_PROMPT in row)
    arrow_col = plain.index(GLYPH_PROMPT)
    dia_col = MSG_ACCENT_W + MSG_RAIL_GAP
    assert arrow_col == dia_col, f"❯ at {arrow_col}, expected {dia_col}"
    # Align with Thought / tool diamond columns.
    thought_plain = render_markup(thought_markup("Thought for 1s")).plain
    tool_plain = render_markup(
        tool_header_markup("", "Run demo", success_rail=True)
    ).plain
    assert thought_plain.index(GLYPH_DIAMOND) == dia_col
    assert tool_plain.index(GLYPH_DIAMOND) == dia_col


def test_paint_output_band_has_bg() -> None:
    r = paint_output_band(["hello", "world"], width=40)
    assert isinstance(r, Text)
    c = Console(file=StringIO(), width=40, force_terminal=True, color_system="truecolor")
    segs = list(c.render(r, c.options.update_width(40)))
    bgs = [s for s in segs if s.style and s.style.bgcolor]
    assert bgs, "output band must include bgcolor segments"


def test_paint_output_band_long_line_paints_every_visual_row() -> None:
    """Long JSON/bash lines must hard-wrap then pad — no jagged mid-width chips."""
    from rich.cells import cell_len

    from room_tui.llm.msg_layout import MSG_INDENT

    long = '{"id":"' + ("x" * 120) + '","name":"help"}'
    width = 40
    r = paint_output_band([long, "short"], width=width)
    assert isinstance(r, Text)
    content_w = width - MSG_INDENT
    # Every visual line (split on \n) must be exactly full band width in cells.
    rows = r.plain.split("\n")
    assert len(rows) >= 3, f"expected hard-wrap into multiple rows, got {len(rows)}"
    for row in rows:
        # indent (no requirement on bg) + content padded to content_w
        assert cell_len(row) == width, (
            f"row cell_len={cell_len(row)} != width={width}: {row!r}"
        )
    c = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
    segs = list(c.render(r, c.options.update_width(width)))
    # Collect non-newline segments that are in the band (not the 3-col indent).
    # All band cells should carry bgcolor.
    bg_rows = 0
    for s in segs:
        if s.text == "\n" or not s.text:
            continue
        if s.style and s.style.bgcolor:
            bg_rows += 1
    assert bg_rows >= len(rows), "each visual row must contribute bg-styled segments"


def test_indented_band_text() -> None:
    r = indented_band(Text("code"), bg="#1c1c1c", width=40)
    assert isinstance(r, Text)
    assert "code" in r.plain


def test_format_short_timestamp() -> None:
    s = format_short_timestamp(datetime(2026, 1, 1, 9, 5, 0))
    assert "9:05" in s
    assert "AM" in s or "PM" in s


def test_tool_header_has_green_rail() -> None:
    from room_tui.llm.msg_layout import (
        COLOR_ACCENT_SUCCESS,
        GLYPH_DIAMOND,
        GLYPH_RAIL,
        tool_header_markup,
    )
    from rich.markup import render as render_markup

    s = tool_header_markup("", "[bold]Read[/bold] path", success_rail=True)
    # Grok continuous accent bar = │ (U+2502)
    assert GLYPH_RAIL in s or "\u2502" in s
    assert COLOR_ACCENT_SUCCESS in s
    plain = render_markup(s).plain
    # ``│  ◆ title``
    assert plain.startswith(GLYPH_RAIL) or plain[0] == "\u2502"
    assert plain[1:3] == "  "
    assert plain[3] == GLYPH_DIAMOND
    assert plain[4] == " "


def test_thought_markup_aligned() -> None:
    """Thought reserves blank rail so ◆ column matches tool headers."""
    from rich.markup import render as render_markup

    from room_tui.llm.msg_layout import (
        GLYPH_DIAMOND,
        GLYPH_RAIL,
        MSG_ACCENT_W,
        MSG_RAIL_GAP,
        tool_header_markup,
    )

    s = thought_markup("Thought for 3.9s")
    assert "Thought for 3.9s" in s
    assert "◆" in s or "\u25c6" in s
    plain = render_markup(s).plain
    tool_plain = render_markup(
        tool_header_markup("", "Run demo", success_rail=True)
    ).plain
    dia = GLYPH_DIAMOND
    dia_col = MSG_ACCENT_W + MSG_RAIL_GAP
    assert plain.index(dia) == dia_col
    assert tool_plain.index(dia) == dia_col
    assert plain.index(dia) == tool_plain.index(dia)
    # Grok parity: Thought has NO rail char; finished tool KEEPS rail.
    assert GLYPH_RAIL not in plain and "\u2502" not in plain
    assert tool_plain.startswith(GLYPH_RAIL) or tool_plain[0] == "\u2502"


def test_grok_tool_finish_keeps_rail() -> None:
    """Grok keeps green bar after tool done — Room must too (not ``   ◆ Run``)."""
    from rich.markup import render as render_markup

    from room_tui.llm.message_render import format_tool_header_markup
    from room_tui.llm.msg_layout import GLYPH_RAIL

    # Same header used for live finish + history restore.
    h = format_tool_header_markup(
        "bash", {"command": "paper-derived session list"}, is_error=False
    )
    plain = render_markup(h).plain
    assert plain.startswith(GLYPH_RAIL) or plain[0] == "\u2502"
    assert "Run" in plain
    # Must not be blank-gutter form.
    assert not plain.startswith("   ◆")
