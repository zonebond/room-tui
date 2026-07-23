"""Grok Build message-list layout with paint-able full-width backgrounds.

Critical: Textual RichLog only paints ``on <color>`` on segments that carry
that style. Table ``style="on …"`` does **not** put bg on child text (verified).

All elevated bands must pad each **visual** line with spaces styled
``on <bg>`` to the message column width.

Long logical lines must be **cell-wrapped first** then padded. If RichLog
soft-wraps a single long Text, only the glyph cells keep bg — the right
gutter of continuation rows stays unpainted (jagged chips).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rich._wrap import chop_cells
from rich.cells import cell_len
from rich.console import Console, RenderableType
from rich.table import Table
from rich.text import Text

# ── GrokNight ─────────────────────────────────────────────────────────
COLOR_BG_LIGHT = "#242424"
COLOR_BG_CODE = "#1c1c1c"
COLOR_TEXT_PRIMARY = "#e1e1e1"
COLOR_TEXT_SECONDARY = "#c8c8c8"
COLOR_GRAY = "#6c6c6c"
COLOR_GRAY_DIM = "#585858"
COLOR_ACCENT_SUCCESS = "#9ece6a"

# ── Grok Build process chrome (parity contract) ─────────────────────
# Grok thinking/tool accent is a continuous light vertical bar (screenshot):
#   • Tool success / running → KEEP green rail:  ``│  ◆ Run …``
#   • Tool error             → KEEP red rail:    ``│  ◆ …`` (rose)
#   • Live Thinking…         → gray continuous │ through header+body
#   • Thought for Xs         → NO rail:          ``   ◆ Thought for Xs``
#   • Assistant prose        → no process chrome
# Rule: if Grok keeps the bar, Room keeps it; if Grok omits it, Room omits it.
# Never drop the green rail when a tool finishes — Grok keeps it after done.
MSG_ACCENT_W = 1  # rail column (│ or blank)
MSG_RAIL_GAP = 2  # spaces between rail and ◆
MSG_DIA_GAP = 1  # space between ◆ and title
MSG_PAD_LEFT = 2  # legacy alias: content pad under assistant prose
# Content under process rows aligns with title start.
MSG_INDENT = MSG_ACCENT_W + MSG_RAIL_GAP + 1 + MSG_DIA_GAP  # 5
TIMESTAMP_COL = 10
# Cells reserved after the right-edge timestamp (Grok: not flush to the rim).
TIMESTAMP_RIGHT_PAD = 1

GLYPH_DIAMOND = "\u25c6"  # ◆
GLYPH_PROMPT = "\u276f"  # ❯
# Light box vertical — stacks into a continuous Grok accent bar (not heavy ┃ / dingbat ❙).
GLYPH_RAIL = "\u2502"  # │

# aliases
COLOR_PROMPT_BAND = COLOR_BG_LIGHT
COLOR_PROMPT_BODY = COLOR_TEXT_PRIMARY
COLOR_PROMPT_ARROW = COLOR_TEXT_SECONDARY
COLOR_MSG_DIM = COLOR_GRAY_DIM
COLOR_MSG_MID = COLOR_GRAY_DIM
COLOR_MSG_USER = COLOR_TEXT_SECONDARY

# Shared console for Text.wrap (no I/O).
_WRAP_CONSOLE = Console(force_terminal=True, color_system="truecolor", width=200)


def content_pad() -> str:
    return " " * MSG_INDENT


def block_gap_text() -> Text:
    """One blank row between process chrome and body / between message blocks.

    Grok Build keeps a breath line under ``❙  ◆ Read`` before the code band,
    and similarly between major message blocks.
    """
    return Text(" ")


def format_short_timestamp(when: datetime | None = None) -> str:
    dt = when or datetime.now().astimezone()
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{hour}:{dt.strftime('%M %p')}"


def _cell_wrap_plain(s: str, width: int) -> list[str]:
    """Hard-wrap *s* to *width* display cells (code/JSON-safe, no word breaks)."""
    if width <= 0:
        return [s] if s else [""]
    if not s:
        return [""]
    if cell_len(s) <= width:
        return [s]
    parts = chop_cells(s, width)
    return parts if parts else [""]


def _cell_wrap_text(src: Text, width: int) -> list[Text]:
    """Wrap a styled Text to *width* cells, preserving spans."""
    if width <= 0:
        return [src]
    plain = src.plain
    if not plain:
        return [Text(no_wrap=True)]
    if cell_len(plain) <= width:
        out = Text(no_wrap=True)
        out.append_text(src)
        return [out]
    # Text.wrap preserves styles on each visual line.
    return list(src.wrap(_WRAP_CONSOLE, width)) or [Text(no_wrap=True)]


def _pad_line_to_width(line: Text, width: int, bg: str) -> Text:
    """Pad with NBSP so trim/width logic cannot drop the trailing band cells.

    Uses U+00A0 (non-breaking space) so the pad still has a “character” for
    RichLog segment painting and is less likely to be collapsed than plain
    ASCII spaces in some paths.

    *line* must already be ≤ *width* cells (caller wraps first).
    """
    if width <= 0:
        return line
    used = cell_len(line.plain)
    if used < width:
        # NBSP keeps cell occupancy; style carries the visible band color.
        line.append("\u00a0" * (width - used), style=f"on {bg}")
    elif used == 0:
        line.append("\u00a0" * width, style=f"on {bg}")
    return line


def _append_visual_row(
    out: Text,
    *,
    indent: int,
    content: Text,
    content_w: int,
    bg: str,
    first: bool,
) -> None:
    """Append one already-wrapped visual row: indent + content + bg pad."""
    if not first:
        out.append("\n")
    if indent:
        out.append(" " * indent)
    row = Text(no_wrap=True)
    if content.plain:
        row.append_text(content)
    else:
        row.append(" ", style=f"on {bg}")
    _pad_line_to_width(row, content_w, bg)
    out.append_text(row)


def user_prompt_renderable(
    text: str,
    *,
    width: int | None = None,
    when: datetime | None = None,
    show_timestamp: bool = True,
    vpad: int = 1,
) -> RenderableType:
    """Full-width user band: ``   ❯ message`` with ❯ aligned to process ◆.

    Grok process chrome: diamond sits at col ``MSG_ACCENT_W + MSG_RAIL_GAP``.
    User prompt uses the same column for ``❯`` (blank rail gutter, no ❙)::

        ``   ❯ Show me the money``
        ``   ◆ Thought for 5.0s``
        ``❙  ◆ Run …``

    *vpad* (default 1): full-width ``bg_light`` blank rows above and below the
    text so every user block has the same vertical padding.
    """
    body = (text or "").rstrip()
    lines = body.splitlines() or [""]
    w = max(8, width or 80)
    ts = format_short_timestamp(when) if show_timestamp else ""
    # Leading gap before time + trailing right pad so "3:06 AM" is not flush.
    ts_part = f"  {ts}" if ts else ""
    ts_right = TIMESTAMP_RIGHT_PAD if ts_part else 0
    ts_w = cell_len(ts_part) + ts_right
    bg = COLOR_BG_LIGHT
    vpad_n = max(0, int(vpad))
    # Same prefix geometry as thought/tool: gutter + glyph + gap before body.
    gutter = (" " * MSG_ACCENT_W) + (" " * MSG_RAIL_GAP)  # aligns glyph with ◆
    prompt_glyph = f"{GLYPH_PROMPT}{' ' * MSG_DIA_GAP}"
    chrome_w = cell_len(gutter) + cell_len(prompt_glyph)  # = MSG_INDENT

    out = Text(no_wrap=False)
    first_row = True

    def _blank_bg_row() -> None:
        nonlocal first_row
        if not first_row:
            out.append("\n")
        first_row = False
        row = Text(no_wrap=True)
        _pad_line_to_width(row, w, bg)
        out.append_text(row)

    # Top band padding (same elevated bg as the message row).
    for _ in range(vpad_n):
        _blank_bg_row()

    for i, line in enumerate(lines):
        if i == 0:
            # First visual line: gutter + ❯ + body, optional right timestamp.
            if ts_part and w > chrome_w + ts_w + 2:
                first_body_w = max(1, w - chrome_w - ts_w)
            else:
                first_body_w = max(1, w - chrome_w)
            body_chunks = _cell_wrap_plain(line, first_body_w)
            for j, chunk in enumerate(body_chunks):
                if not first_row:
                    out.append("\n")
                first_row = False
                row = Text(no_wrap=True)
                if j == 0:
                    row.append(gutter, style=f"on {bg}")
                    row.append(prompt_glyph, style=f"{COLOR_TEXT_SECONDARY} on {bg}")
                    row.append(chunk, style=f"{COLOR_TEXT_PRIMARY} on {bg}")
                    if ts_part and w > chrome_w + ts_w + 2:
                        left_w = cell_len(row.plain)
                        pad_n = max(1, w - left_w - ts_w)
                        row.append(" " * pad_n, style=f"on {bg}")
                        row.append(ts_part, style=f"{COLOR_GRAY} on {bg}")
                        if ts_right:
                            row.append(" " * ts_right, style=f"on {bg}")
                    else:
                        _pad_line_to_width(row, w, bg)
                else:
                    # Soft-wrap: indent to body column (under message text).
                    row.append(" " * chrome_w, style=f"on {bg}")
                    row.append(chunk, style=f"{COLOR_TEXT_PRIMARY} on {bg}")
                    _pad_line_to_width(row, w, bg)
                if cell_len(row.plain) < w:
                    _pad_line_to_width(row, w, bg)
                out.append_text(row)
        else:
            # Further logical lines: align under message body.
            cont = " " * chrome_w
            body_w = max(1, w - chrome_w)
            for chunk in _cell_wrap_plain(line, body_w):
                if not first_row:
                    out.append("\n")
                first_row = False
                row = Text(no_wrap=True)
                row.append(cont, style=f"on {bg}")
                row.append(chunk, style=f"{COLOR_TEXT_PRIMARY} on {bg}")
                _pad_line_to_width(row, w, bg)
                out.append_text(row)
    if first_row or (vpad_n and not lines):
        # empty body (only possible if no lines painted yet)
        if first_row:
            row = Text(no_wrap=True)
            row.append(gutter, style=f"on {bg}")
            row.append(prompt_glyph, style=f"{COLOR_TEXT_SECONDARY} on {bg}")
            _pad_line_to_width(row, w, bg)
            out.append_text(row)
            first_row = False

    # Bottom band padding.
    for _ in range(vpad_n):
        _blank_bg_row()
    return out


def assistant_first_line_renderable(
    text: str,
    *,
    width: int | None = None,
    when: datetime | None = None,
    show_timestamp: bool = True,
) -> RenderableType:
    """Agent text + right timestamp (no elevated background).

    Left pad is ``MSG_INDENT`` so prose aligns with process titles / code bands
    (``│  ◆ title`` content column), not the old 2-cell legacy pad.
    """
    body = (text or "").rstrip()
    lines = body.splitlines() or [""]
    w = max(8, width or 80)
    pad = " " * MSG_INDENT
    ts = format_short_timestamp(when) if show_timestamp else ""
    ts_part = f"  {ts}" if ts else ""
    ts_right = TIMESTAMP_RIGHT_PAD if ts_part else 0
    ts_w = cell_len(ts_part) + ts_right

    out = Text(no_wrap=False)
    for i, line in enumerate(lines):
        if i:
            out.append("\n")
        if i == 0:
            left = pad + line
            out.append(left, style=COLOR_TEXT_PRIMARY)
            if ts_part and w > ts_w + 4:
                out.append(" " * max(1, w - cell_len(left) - ts_w))
                out.append(ts_part, style=COLOR_GRAY)
                if ts_right:
                    out.append(" " * ts_right)
            elif ts_part:
                out.append(ts_part, style=COLOR_GRAY)
                if ts_right:
                    out.append(" " * ts_right)
        else:
            out.append(pad + line, style=COLOR_TEXT_PRIMARY)
    return out


def assistant_plain_markup(text: str) -> list[str]:
    body = (text or "").rstrip()
    if not body:
        return []
    pad = " " * MSG_PAD_LEFT
    esc = r"\["
    return [f"{pad}{line.replace('[', esc)}" for line in (body.splitlines() or [""])]


def _process_chrome_prefix(
    *,
    rail_markup: str,
    diamond_markup: str,
) -> str:
    """Build ``{rail}  {◆} `` prefix so titles share one column."""
    return (
        f"{rail_markup}"
        f"{' ' * MSG_RAIL_GAP}"
        f"{diamond_markup}"
        f"{' ' * MSG_DIA_GAP}"
    )


def thought_markup(label: str) -> str:
    """Grok Thought: **no** accent rail (Grok omits bar here).

    Blank gutter keeps ◆ in the same column as tool rows:
    ``   ◆ Thought for Xs`` vs ``❙  ◆ Run …``.
    """
    esc = r"\["
    safe = (label or "Thought").replace("[", esc)
    dia = f"[{COLOR_GRAY_DIM}]{GLYPH_DIAMOND}[/{COLOR_GRAY_DIM}]"
    return (
        f"{_process_chrome_prefix(rail_markup=' ' * MSG_ACCENT_W, diamond_markup=dia)}"
        f"[{COLOR_GRAY}]{safe}[/{COLOR_GRAY}]"
    )


def system_markup(text: str, *, error: bool = False) -> str:
    """Quiet meta line(s) — same process-column chrome as task/system notices.

    First line: ``·`` (or ◆ on error) + text.
    Continuation lines: same column indent, **no** extra bullets (task-card style).
    """
    from room_tui.ui_state import COLOR_ERR

    esc = r"\["
    safe = (text or "").replace("[", esc)
    color = COLOR_ERR if error else COLOR_GRAY
    mark = GLYPH_DIAMOND if error else "·"
    dia = f"[{color}]{mark}[/{color}]"
    # Align continuation under first line text (after rail + diamond + space).
    cont = f"[{color}] [/{color}]"
    lines = safe.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        lead = dia if i == 0 else cont
        pre = _process_chrome_prefix(
            rail_markup=" " * MSG_ACCENT_W, diamond_markup=lead
        )
        out.append(f"{pre}[{color}]{line}[/{color}]")
    return "\n".join(out)


def tool_header_markup(
    label_left: str,
    label_right_markup: str,
    *,
    is_error: bool = False,
    success_rail: bool = True,
) -> str:
    """Grok tool process row: always keep accent rail (running + after done).

    Grok Build keeps the green bar on completed tools; Room must not drop it
    on finish. ``success_rail=False`` is only for rare non-tool chrome — normal
    tool headers should leave the default ``True``.
    """
    del label_left
    if is_error:
        from room_tui.ui_state import COLOR_ERR

        rail = f"[{COLOR_ERR}]{GLYPH_RAIL}[/{COLOR_ERR}]"
        dia = f"[{COLOR_ERR}]{GLYPH_DIAMOND}[/{COLOR_ERR}]"
    elif success_rail:
        # Grok: green rail stays after tool completes.
        rail = f"[{COLOR_ACCENT_SUCCESS}]{GLYPH_RAIL}[/{COLOR_ACCENT_SUCCESS}]"
        dia = f"[{COLOR_GRAY_DIM}]{GLYPH_DIAMOND}[/{COLOR_GRAY_DIM}]"
    else:
        # Explicit no-rail (not used for normal tool finish).
        rail = " " * MSG_ACCENT_W
        dia = f"[{COLOR_GRAY_DIM}]{GLYPH_DIAMOND}[/{COLOR_GRAY_DIM}]"
    return f"{_process_chrome_prefix(rail_markup=rail, diamond_markup=dia)}{label_right_markup}"


def indent_renderable(inner: RenderableType) -> RenderableType:
    table = Table(
        show_header=False,
        expand=True,
        box=None,
        padding=(0, 0),
        show_edge=False,
        pad_edge=False,
    )
    table.add_column(width=MSG_INDENT, no_wrap=True)
    table.add_column(ratio=1, overflow="fold", no_wrap=False)
    table.add_row("", inner)
    return table


def with_content_indent(inner: RenderableType, indent: int = MSG_INDENT) -> RenderableType:
    return indent_renderable(inner) if indent else inner


def indented_band(
    inner: RenderableType,
    *,
    bg: str,
    width: int | None = None,
) -> RenderableType:
    """Indent + full-width **painted** code/tool band."""
    w = max(8, width or 80)
    if isinstance(inner, Text):
        return _text_indented_band(inner, bg=bg, width=w)
    # Syntax etc.: convert via plain lines (lose highlight) then paint —
    # prefer callers pass Text. Best-effort: prefix indent as Text lines.
    try:
        plain = getattr(inner, "code", None) or str(inner)
    except Exception:
        plain = ""
    lines = str(plain).splitlines() or [""]
    return paint_output_band(lines, width=w, bg=bg, indent=MSG_INDENT)


def _paint_styled_line(lt: Text, *, bg: str, default_fg: str = "") -> Text:
    """Copy one logical line onto a new Text, forcing ``on bg`` on every span."""
    line = Text(no_wrap=True)
    if not lt.plain:
        return line
    if not lt.spans:
        fg = default_fg or COLOR_TEXT_PRIMARY
        line.append(lt.plain, style=f"{fg} on {bg}")
        return line
    # Cover gaps between spans with default style.
    plain = lt.plain
    covered = 0
    for start, end, st in lt.spans:
        if start > covered:
            gap = plain[covered:start]
            if gap:
                fg0 = default_fg or COLOR_TEXT_PRIMARY
                line.append(gap, style=f"{fg0} on {bg}")
        chunk = plain[start:end]
        if not chunk:
            covered = max(covered, end)
            continue
        fg = ""
        if st is not None:
            fg = str(st).split(" on ")[0].strip()
        if not fg:
            fg = default_fg or COLOR_TEXT_PRIMARY
        line.append(chunk, style=f"{fg} on {bg}")
        covered = max(covered, end)
    if covered < len(plain):
        tail = plain[covered:]
        if tail:
            fg0 = default_fg or COLOR_TEXT_PRIMARY
            line.append(tail, style=f"{fg0} on {bg}")
    return line


def _text_indented_band(inner: Text, *, bg: str, width: int) -> Text:
    """Indent + paint: wrap each logical line to content_w, pad every visual row."""
    content_w = max(1, width - MSG_INDENT)
    out = Text(no_wrap=False)
    try:
        parts = inner.split("\n")
    except Exception:
        parts = [Text(p) for p in inner.plain.split("\n")]
    first = True
    for lt in parts:
        painted = _paint_styled_line(lt, bg=bg)
        for vis in _cell_wrap_text(painted, content_w):
            # Re-apply on-bg after wrap (wrap keeps styles, but empty rows need bg).
            content = Text(no_wrap=True)
            if vis.plain:
                # Ensure every span still has on-bg (wrap preserves them).
                content.append_text(vis)
            _append_visual_row(
                out,
                indent=MSG_INDENT,
                content=content,
                content_w=content_w,
                bg=bg,
                first=first,
            )
            first = False
    if first:
        _append_visual_row(
            out,
            indent=MSG_INDENT,
            content=Text(no_wrap=True),
            content_w=content_w,
            bg=bg,
            first=True,
        )
    return out


def paint_output_band(
    lines: list[str],
    *,
    width: int,
    bg: str = COLOR_BG_CODE,
    indent: int = MSG_INDENT,
    gutter: bool = False,
    gutter_start: int = 1,
    fg: str = COLOR_TEXT_PRIMARY,
) -> Text:
    """Paint tool/code output: each **visual** line bg-padded to full width.

    Long logical lines are hard-wrapped to ``content_w`` first so RichLog never
    soft-wraps mid-band (which leaves unpainted right gutters = jagged chips).

    *fg*: body text color (e.g. error rose for failed fetch/index dumps).
    """
    w = max(8, width)
    content_w = max(1, w - indent)
    n = max(1, len(lines) if lines else 1)
    gw = len(str(gutter_start + n - 1)) if gutter else 0
    # Gutter occupies cells inside the band; body wraps into remaining cells.
    gutter_w = (gw + 2) if gutter else 0
    body_w = max(1, content_w - gutter_w)
    body_style = f"{fg} on {bg}"

    out = Text(no_wrap=False)
    first = True
    for i, src in enumerate(lines if lines else [""]):
        body_chunks = _cell_wrap_plain(src if src else "", body_w)
        for j, chunk in enumerate(body_chunks):
            content = Text(no_wrap=True)
            if gutter and j == 0:
                content.append(
                    f"{gutter_start + i:>{gw}}  ",
                    style=f"{COLOR_GRAY_DIM} on {bg}",
                )
            elif gutter and j > 0:
                # Continuation: blank gutter so columns stay aligned.
                content.append(" " * gutter_w, style=f"on {bg}")
            content.append(
                chunk if chunk else " ",
                style=body_style,
            )
            _append_visual_row(
                out,
                indent=indent,
                content=content,
                content_w=content_w,
                bg=bg,
                first=first,
            )
            first = False
    if first:
        _append_visual_row(
            out,
            indent=indent,
            content=Text(no_wrap=True),
            content_w=content_w,
            bg=bg,
            first=True,
        )
    return out


def full_width_band(
    inner: RenderableType,
    *,
    bg: str,
    indent: int = 0,
    width: int | None = None,
) -> RenderableType:
    w = max(8, width or 80)
    if isinstance(inner, Text):
        if indent:
            return _text_indented_band(inner, bg=bg, width=w)
        content_w = w
        out = Text(no_wrap=False)
        parts = inner.split("\n")
        first = True
        for lt in parts:
            painted = _paint_styled_line(lt, bg=bg, default_fg="")
            # When default_fg is empty and no spans, _paint_styled_line uses
            # COLOR_TEXT_PRIMARY — force plain ``on bg`` for unstyled.
            if not lt.plain:
                painted = Text(no_wrap=True)
            elif not lt.spans:
                painted = Text(no_wrap=True)
                painted.append(lt.plain, style=f"on {bg}")
            for vis in _cell_wrap_text(painted, content_w):
                content = Text(no_wrap=True)
                if vis.plain:
                    content.append_text(vis)
                _append_visual_row(
                    out,
                    indent=0,
                    content=content,
                    content_w=content_w,
                    bg=bg,
                    first=first,
                )
                first = False
        if first:
            _append_visual_row(
                out,
                indent=0,
                content=Text(no_wrap=True),
                content_w=content_w,
                bg=bg,
                first=True,
            )
        return out
    return indented_band(inner, bg=bg, width=w) if indent else indent_renderable(inner)
