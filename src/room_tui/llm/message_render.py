"""Render AgentBlock sequences as Rich renderables for RichLog.

Block chrome aligns with Grok Build (GrokNight / xai-grok-pager):
- fenced code: full-width ``md_code_bg`` band + neutral syntax (not monokai olive)
- execute: ``$ command`` in command yellow; tool diamond chrome
- read/edit: path orange; body as code band when source-like
- diff: insert/delete row colors from theme
- thinking: quiet muted panel, no loud border
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.markdown import Heading, Markdown
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from room_tui.llm.agent_blocks import AgentBlock
from room_tui.ui_state import (
    COLOR_CMD,
    COLOR_DIFF_DEL_BG,
    COLOR_DIFF_DEL_FG,
    COLOR_DIFF_INS_BG,
    COLOR_DIFF_INS_FG,
    COLOR_ERR,
    COLOR_MD_CODE,
    COLOR_MD_CODE_BG,
    COLOR_MSG_DIM,
    COLOR_MSG_HI,
    COLOR_MSG_LABEL,
    COLOR_MSG_MID,
    COLOR_MSG_TEXT,
    COLOR_MSG_USER,
    COLOR_OK,
    COLOR_PATH,
    COLOR_TOOL_BULLET,
)

# Grok Night fenced-block band (md_code_bg). Full column width via expand.
_CODE_BG = COLOR_MD_CODE_BG
# Neutral dark highlighter — monokai paints an olive band that fights GrokNight.
_CODE_THEME = "github-dark"

# glow-like Markdown palette (matches glow CLI dark theme screenshots):
# H1 = blue bar · H2/H3 = cyan ATX · body soft white · links purple · bullets light
_MD_H1_BG = "#3B82F6"
_MD_HEADING = "#38BDF8"
_MD_BODY = "#E5E5E5"
_MD_STRONG = "#FFFFFF"
_MD_LINK = "#C084FC"
_MD_LINK_URL = "#A78BFA"
_MD_QUOTE = "#A3A3A3"
_MD_HR = "#525252"
_MD_BULLET = "#E5E5E5"

GLOW_MD_THEME = Theme(
    {
        "markdown.paragraph": Style(color=_MD_BODY),
        "markdown.text": Style(color=_MD_BODY),
        "markdown.em": Style(italic=True, color=_MD_BODY),
        "markdown.emph": Style(italic=True, color=_MD_BODY),
        "markdown.strong": Style(bold=True, color=_MD_STRONG),
        "markdown.code": Style(bold=True, color=COLOR_MD_CODE, bgcolor=_CODE_BG),
        "markdown.code_block": Style(color=_MD_BODY, bgcolor=_CODE_BG),
        "markdown.block_quote": Style(color=_MD_QUOTE),
        "markdown.list": Style(color=_MD_BODY),
        "markdown.item": Style(color=_MD_BODY),
        "markdown.item.bullet": Style(bold=True, color=_MD_BULLET),
        "markdown.item.number": Style(color=_MD_HEADING),
        "markdown.hr": Style(color=_MD_HR, dim=True),
        "markdown.h1": Style(bold=True, color="#FFFFFF", bgcolor=_MD_H1_BG),
        "markdown.h1.border": Style(color=_MD_H1_BG),
        "markdown.h2": Style(bold=True, color=_MD_HEADING),
        "markdown.h3": Style(bold=True, color=_MD_HEADING),
        "markdown.h4": Style(color=_MD_HEADING),
        "markdown.h5": Style(color=_MD_HEADING, dim=True),
        "markdown.h6": Style(color=_MD_HEADING, dim=True),
        "markdown.link": Style(color=_MD_LINK),
        "markdown.link_url": Style(color=_MD_LINK_URL, underline=True),
        "markdown.s": Style(strike=True, dim=True),
        "markdown.table.border": Style(color=_MD_HEADING),
        "markdown.table.header": Style(bold=True, color=_MD_HEADING),
    }
)

# Tools that are shell execution (Grok ExecuteToolCallBlock)
_BASH_TOOLS = frozenset(
    {
        "bash",
        "shell",
        "run_shell",
        "run_terminal_command",
        "execute",
        "Bash",
        "Shell",
    }
)
# File read / view — includes pi-agent / ctx aliases so cat-like tools render as Grok Read.
_READ_TOOLS = frozenset(
    {
        "read",
        "Read",
        "read_file",
        "ReadFile",
        "cat",
        "ctx_execute_file",
        "execute_file",
        "get_file",
        "open_file",
        "view",
        "View",
        "view_file",
        "show_file",
        "fetch_file",
        "file_read",
    }
)
_EDIT_TOOLS = frozenset(
    {
        "edit",
        "Edit",
        "write",
        "Write",
        "search_replace",
        "apply_patch",
        "create_file",
        "WriteFile",
        "write_file",
    }
)

_DIAMOND = "\u25c6"  # ◆ — Grok tool bullet default


def _tool_name_key(tool: str) -> str:
    return (tool or "").strip()


def is_read_tool(tool: str) -> bool:
    """True for Read-family tools (incl. ctx_execute_file and *read_file* aliases)."""
    t = _tool_name_key(tool)
    if not t:
        return False
    lt = t.lower()
    if t in _READ_TOOLS or lt in {x.lower() for x in _READ_TOOLS}:
        return True
    if "execute_file" in lt or "read_file" in lt or lt.endswith("_read"):
        return True
    if lt in ("view", "open", "type"):  # type = shell type/cat on some systems
        return True
    return False


def is_bash_tool(tool: str) -> bool:
    t = _tool_name_key(tool)
    lt = t.lower()
    return t in _BASH_TOOLS or lt in {x.lower() for x in _BASH_TOOLS}


def is_edit_tool(tool: str) -> bool:
    t = _tool_name_key(tool)
    lt = t.lower()
    if t in _EDIT_TOOLS or lt in {x.lower() for x in _EDIT_TOOLS}:
        return True
    if lt.endswith("_write") or "write_file" in lt or "search_replace" in lt:
        return True
    return False


def looks_like_write_status(text: str) -> bool:
    """Write tool often returns a status line, not source — don't paint as code."""
    s = (text or "").strip()
    if not s:
        return False
    low = s.lower()
    if low.startswith("successfully wrote"):
        return True
    if "bytes to" in low and ("wrote" in low or "written" in low):
        return True
    if low.startswith("wrote ") and ("byte" in low or "file" in low):
        return True
    # single short status line
    if "\n" not in s and len(s) < 200 and any(
        k in low for k in ("created", "updated", "saved", "written")
    ):
        return True
    return False


def looks_like_source_content(text: str) -> bool:
    """Heuristic: multi-line body that should use Read gutter chrome."""
    s = (text or "").rstrip("\n")
    if not s or looks_like_write_status(s):
        return False
    lines = s.splitlines()
    if len(lines) < 2:
        return False
    if len(s) < 40:
        return False
    # Skip obvious JSON tool envelopes
    if s.lstrip().startswith(("{", "[")) and '"type"' in s[:80]:
        return False
    return True


class _GlowHeading(Heading):
    """glow-style headings: H1 blue bar; H2+ keep ATX ``#`` markers in cyan."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        text = self.text.copy()
        style = console.get_style(self.style_name, default="none")
        if self.tag == "h1":
            # Blue pill/bar around title (glow dark theme).
            plain = text.plain.strip() or " "
            yield Text(f" {plain} ", style=style)
            return
        # glow shows ``## Title`` / ``### Title`` with markers.
        try:
            level = int(self.tag[1:]) if len(self.tag) > 1 else 2
        except ValueError:
            level = 2
        level = max(1, min(6, level))
        hashes = Text("#" * level + " ", style=style)
        body = text
        body.stylize(style)
        yield Text.assemble(hashes, body)


class GlowMarkdown(Markdown):
    """Markdown with glow-like colors + ATX heading markers."""

    elements = {**Markdown.elements, "heading_open": _GlowHeading}

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        with console.use_theme(GLOW_MD_THEME, inherit=False):
            yield from super().__rich_console__(console, options)


def _md(text: str) -> Markdown:
    """Render markdown with glow-like palette (H1 bar, cyan headings, soft body)."""
    return GlowMarkdown(
        text,
        code_theme=_CODE_THEME,
        hyperlinks=False,
        justify="left",
    )


def _syntax(
    code: str,
    lang: str,
    *,
    line_numbers: bool = False,
    start_line: int = 1,
) -> Syntax:
    """Build a Syntax node with Grok-like band + theme."""
    body = (code or "").rstrip("\n") or " "
    theme = _CODE_THEME
    kwargs = dict(
        theme=theme,
        line_numbers=line_numbers,
        start_line=max(1, start_line),
        word_wrap=True,
        background_color=_CODE_BG,
        padding=(0, 1),
    )
    try:
        return Syntax(body, lang or "text", **kwargs)
    except Exception:
        kwargs["theme"] = "monokai"
        return Syntax(body, lang or "text", **kwargs)


# Grok ReadToolCallBlock: Truncated = first 5 + … + last 3 lines.
READ_FIRST_LINES = 5
READ_LAST_LINES = 3
# Hard cap so a multi-MB read cannot blow the TUI.
READ_MAX_CHARS = 120_000


def needs_fold(line_count: int) -> bool:
    """True when content is long enough for Grok-style collapse."""
    return line_count > READ_FIRST_LINES + READ_LAST_LINES


# Grok-style hover: one soft uniform lift on the elevated code band + footer.
# blend(bg_base #141414, code #1c1c1c) → mid floor; no dots / pixels / neon.
EXPAND_HOVER_SOFT_BG = "#252525"  # code band + footer soft floor
EXPAND_HOVER_FOOTER_LABEL = "#b8b8b8"  # secondary text slightly up
EXPAND_HOVER_EXPAND_ACCENT = "#b4d87a"  # Expand label (success, +1 step)
EXPAND_HOVER_COLLAPSE_ACCENT = "#7eb6e0"  # Collapse label (brand, +1 step)


def expand_footer_markup(
    *,
    expanded: bool,
    total_lines: int,
    block_id: str = "",
    hover: bool = False,
    width: int = 0,
) -> str:
    """Grok-like expand control under a truncated band.

    Whole foldable band toggles on **double-click** (or ``e``). *hover* is a
    quiet soft floor + slightly brighter labels — same language as Grok's
    dim hover wash, not a button slab or texture.
    """
    from room_tui.llm.msg_layout import COLOR_ACCENT_SUCCESS, content_pad
    from room_tui.ui_state import COLOR_BRAND

    pad = content_pad()
    # Trailing id token for click matching (dim, after a rare separator).
    tag = f"  ·:{block_id}" if block_id else ""
    if expanded:
        if hover:
            bg = EXPAND_HOVER_SOFT_BG
            core = (
                f"{pad}[bold {EXPAND_HOVER_COLLAPSE_ACCENT} on {bg}]▾ Collapse[/]"
                f"  [{EXPAND_HOVER_FOOTER_LABEL} on {bg}]"
                f"full {total_lines} lines · double-click or e[/]"
                f"[{COLOR_MSG_DIM} on {bg}]{tag}[/]"
            )
            return _pad_markup_row(core, width=width, bg=bg)
        return (
            f"{pad}[bold {COLOR_BRAND}]▾ Collapse[/]"
            f"  [{COLOR_MSG_DIM}]full {total_lines} lines · double-click or e[/]"
            f"[{COLOR_MSG_DIM}]{tag}[/]"
        )
    shown = READ_FIRST_LINES + READ_LAST_LINES
    more = max(0, total_lines - shown)
    if hover:
        bg = EXPAND_HOVER_SOFT_BG
        core = (
            f"{pad}[bold {EXPAND_HOVER_EXPAND_ACCENT} on {bg}]▸ Expand[/]"
            f"  [{EXPAND_HOVER_FOOTER_LABEL} on {bg}]"
            f"{more} more lines · double-click or e[/]"
            f"[{COLOR_MSG_DIM} on {bg}]{tag}[/]"
        )
        return _pad_markup_row(core, width=width, bg=bg)
    return (
        f"{pad}[bold {COLOR_ACCENT_SUCCESS}]▸ Expand[/]"
        f"  [{COLOR_MSG_DIM}]{more} more lines · double-click or e[/]"
        f"[{COLOR_MSG_DIM}]{tag}[/]"
    )


def _pad_markup_row(markup: str, *, width: int, bg: str) -> str:
    """Right-pad *markup* with background spaces so the wash spans *width* cells."""
    if width <= 0:
        return markup
    try:
        cell = Text.from_markup(markup).cell_len
    except Exception:
        # Fallback: rough strip of tags
        cell = len(re.sub(r"\[/?[^\]]*\]", "", markup))
    need = max(0, int(width) - int(cell))
    if need <= 0:
        return markup
    return f"{markup}[on {bg}]{' ' * need}[/]"


def is_expand_footer_line(plain_line: str) -> bool:
    """True when *plain_line* is an Expand / Collapse control row."""
    s = plain_line or ""
    return (
        "Expand" in s
        or "Collapse" in s
        or "▸" in s
        or "▾" in s
    )


def parse_expand_footer_id(plain_line: str) -> str | None:
    """Extract block id from footer plain text, if present."""
    s = plain_line or ""
    if not is_expand_footer_line(s):
        return None
    m = re.search(r"·:([A-Za-z0-9_-]+)\s*$", s)
    return m.group(1) if m else None


def _unwrap_json_content_envelope(text: str) -> str | None:
    """If *text* is a JSON tool/MCP content envelope, return the inner plain text.

    Handles common shapes::

        {"content":[{"type":"text","text":"# md\\n\\n..."}]}
        {"type":"text","text":"..."}
        [{"type":"text","text":"..."}]
        "\"# md\\n\\n...\""   (JSON-encoded string)

    Returns ``None`` when *text* is not such an envelope.
    """
    s = (text or "").strip()
    if not s:
        return None
    # Must look like JSON.
    if not (s[0] in "{[\"" or s.startswith("{\"")):
        return None
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    def _from_obj(o: Any, *, depth: int = 0) -> str | None:
        if depth > 6:
            return None
        if isinstance(o, str):
            # Double-encoded JSON string of content.
            if o.lstrip().startswith(("{", "[")) and depth < 4:
                try:
                    return _from_obj(json.loads(o), depth=depth + 1)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            return o
        if isinstance(o, list):
            parts: list[str] = []
            for item in o:
                got = _from_obj(item, depth=depth + 1)
                if got:
                    parts.append(got)
            return "\n".join(parts) if parts else None
        if isinstance(o, dict):
            # MCP / Pi content item
            t = o.get("type")
            if t in ("text", "output", "stdout") or (
                "text" in o and isinstance(o.get("text"), str)
            ):
                inner = o.get("text")
                if isinstance(inner, str):
                    return inner
            for k in ("content", "output", "stdout", "text", "result", "data", "body"):
                if k in o and o[k] is not None:
                    got = _from_obj(o[k], depth=depth + 1)
                    if got is not None and str(got).strip():
                        return got
        return None

    got = _from_obj(obj)
    if got is None:
        return None
    # Reject "unwrap" that is still the same opaque JSON dump with no real body.
    if got.strip() == s:
        return None
    return got


def _unescape_literal_newlines(text: str) -> str:
    """Turn dumped ``\\n`` / ``\\t`` sequences into real whitespace when appropriate.

    Only applied when the body has almost no real newlines but many escape
    sequences (JSON-string dump of a file). Avoids unicode_escape (breaks CJK).
    """
    s = text or ""
    if not s:
        return s
    real_nl = s.count("\n")
    lit_nl = s.count("\\n")
    if lit_nl >= 3 and real_nl <= 1 and lit_nl > real_nl:
        return (
            s.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
            .replace("\\'", "'")
        )
    return s


def sanitize_read_output(text: str) -> str:
    """Strip pi/ctx / MCP wrapper junk so Read body is real file content.

    Handles:
    - ``path=…`` + ``console.log(FILE_CONTENT)`` stubs
    - JSON MCP envelopes ``{"content":[{"type":"text","text":"…"}]}``
    - literal ``\\n`` dumps of multi-line files
    """
    s = (text or "").replace("\r\n", "\n")
    if not s.strip():
        return s

    # JSON content envelope first (most common for ctx / MCP reads of .md).
    unwrapped = _unwrap_json_content_envelope(s)
    if unwrapped is not None:
        s = unwrapped.replace("\r\n", "\n")

    lines = s.split("\n")
    # Leading path= metadata
    while lines and (
        lines[0].startswith("path=")
        or lines[0].startswith("file=")
        or lines[0].startswith("filepath=")
    ):
        lines = lines[1:]
    body = "\n".join(lines).lstrip("\n")
    # ```javascript\nconsole.log(FILE_CONTENT);\n```
    m = re.match(
        r"^\s*```(?:javascript|js)?[ \t]*\n"
        r"\s*console\.log\(\s*FILE_CONTENT\s*\)\s*;?\s*\n"
        r"```[ \t]*\n?",
        body,
        flags=re.IGNORECASE,
    )
    if m:
        body = body[m.end() :]
    # Alternate: only the stub without path=
    m2 = re.match(
        r"^\s*console\.log\(\s*FILE_CONTENT\s*\)\s*;?\s*\n?",
        body,
        flags=re.IGNORECASE,
    )
    if m2 and "```" not in body[: m2.end() + 20]:
        # only strip if followed by real content (not the whole file)
        rest = body[m2.end() :]
        if rest.strip():
            body = rest
    body = body.lstrip("\n")
    # Still a JSON envelope after path= strip?
    unwrapped2 = _unwrap_json_content_envelope(body)
    if unwrapped2 is not None:
        body = unwrapped2.replace("\r\n", "\n")
    body = _unescape_literal_newlines(body)
    return body.lstrip("\n")


def _head_tail_lines(
    lines: list[str],
    *,
    first: int = READ_FIRST_LINES,
    last: int = READ_LAST_LINES,
) -> tuple[list[str], bool]:
    """Return (display_lines_without_ellipsis, was_truncated)."""
    if len(lines) <= first + last:
        return list(lines), False
    return list(lines[:first]), True  # caller adds ellipsis + tail separately


def _digit_count(n: int) -> int:
    n = max(1, n)
    d = 1
    while n >= 10:
        n //= 10
        d += 1
    return d


def _pygments_theme() -> Any:
    try:
        from rich.syntax import PygmentsSyntaxTheme

        return PygmentsSyntaxTheme(_CODE_THEME)
    except Exception:
        try:
            from rich.syntax import PygmentsSyntaxTheme

            return PygmentsSyntaxTheme("ansi_dark")
        except Exception:
            return None


def _lexer_for_lang(lang: str) -> Any:
    try:
        from pygments.lexers import TextLexer, get_lexer_by_name

        if not lang or lang == "text":
            return TextLexer()
        return get_lexer_by_name(lang, stripnl=False)
    except Exception:
        return None


def _gutter_code_text(
    lines: list[str],
    *,
    start_line: int,
    max_line_no: int,
    lang: str = "text",
) -> Text:
    """Grok Read line paint: gray_dim gutter + primary body on bg_dark.

    Gutter format matches ReadToolCallBlock: ``{:>w$}  `` + line text.
    """
    rows: list[tuple[int | None, str]] = []
    base = max(1, start_line)
    for i, line in enumerate(lines):
        rows.append((base + i, line))
    return _paint_gutter_rows(rows, max_line_no=max_line_no, lang=lang)


def _paint_gutter_rows(
    rows: list[tuple[int | None, str]],
    *,
    max_line_no: int,
    lang: str = "text",
) -> Text:
    """Legacy: gutter + body as one Text (gutter may share styles). Prefer band painter."""
    gw = _digit_count(max(1, max_line_no))
    out = Text(no_wrap=False)
    lexer = _lexer_for_lang(lang)
    theme = _pygments_theme()
    gutter_style = COLOR_MSG_MID  # no code bg — outside elevated band
    for i, (num, line) in enumerate(rows):
        if i:
            out.append("\n")
        if num is None:
            out.append(f"{'':>{gw}}  ", style=gutter_style)
            out.append("…", style=COLOR_MSG_DIM)
            continue
        out.append(f"{num:>{gw}}  ", style=gutter_style)
        _append_highlighted_line(
            out,
            line,
            lexer=lexer,
            theme=theme,
            style_fallback=COLOR_MSG_USER,
        )
    return out


def paint_read_code_band(
    rows: list[tuple[int | None, str]],
    *,
    max_line_no: int,
    lang: str = "text",
    width: int,
    indent: int | None = None,
) -> Text:
    """Grok Read/Edit body: line numbers **outside** code bg, source inside band.

    Layout per visual row::

        [process indent][dim gutter no-bg][ source + pad on md_code_bg …… ]

    Gutter sits on the message background; only the code column is elevated.
    """
    from rich.cells import cell_len

    from room_tui.llm.msg_layout import (
        MSG_INDENT,
        _cell_wrap_plain,
        _pad_line_to_width,
    )

    w = max(8, width)
    ind = MSG_INDENT if indent is None else max(0, indent)
    gw = _digit_count(max(1, max_line_no))
    gutter_w = gw + 2  # ``{num:>gw}  ``
    # Remaining width is elevated code column only.
    code_w = max(1, w - ind - gutter_w)
    lexer = _lexer_for_lang(lang)
    theme = _pygments_theme()
    out = Text(no_wrap=False)
    first = True

    def _emit_row(gutter_txt: str, code_line: Text) -> None:
        nonlocal first
        if not first:
            out.append("\n")
        first = False
        # Process indent — message bg (no elevated band).
        if ind:
            out.append(" " * ind)
        # Line number gutter — outside code background.
        out.append(gutter_txt, style=COLOR_MSG_MID)
        # Code column with bg + right pad to full width.
        band = Text(no_wrap=True)
        if code_line.plain:
            band.append_text(code_line)
        else:
            band.append(" ", style=f"on {_CODE_BG}")
        _pad_line_to_width(band, code_w, _CODE_BG)
        out.append_text(band)

    for num, src in rows:
        if num is None:
            # Ellipsis: gutter blank + dim … inside code band.
            chunks = _cell_wrap_plain("…", code_w)
            for j, chunk in enumerate(chunks):
                g = (" " * gutter_w) if j == 0 else (" " * gutter_w)
                code = Text(no_wrap=True)
                code.append(chunk, style=f"{COLOR_MSG_DIM} on {_CODE_BG}")
                _emit_row(g if j > 0 else f"{'':>{gw}}  ", code)
            continue

        g0 = f"{num:>{gw}}  "
        body_chunks = _cell_wrap_plain(src if src else "", code_w)
        for j, chunk in enumerate(body_chunks):
            code = Text(no_wrap=True)
            if chunk:
                _append_highlighted_line(
                    code,
                    chunk,
                    lexer=lexer,
                    theme=theme,
                    style_fallback=COLOR_MSG_USER,
                )
            else:
                code.append(" ", style=f"on {_CODE_BG}")
            # Soft-wrap continuations: blank gutter (still outside bg).
            gutter = g0 if j == 0 else (" " * gutter_w)
            # Ensure highlight spans carry on-bg (append may have set it).
            # Re-pad after highlight in case token styles missed empty pad.
            if cell_len(code.plain) < code_w:
                _pad_line_to_width(code, code_w, _CODE_BG)
            _emit_row(gutter, code)

    if first:
        # empty
        code = Text(no_wrap=True)
        code.append(" ", style=f"on {_CODE_BG}")
        _pad_line_to_width(code, code_w, _CODE_BG)
        _emit_row(f"{1:>{gw}}  ", code)
    return out


def _append_highlighted_line(
    out: Text,
    line: str,
    *,
    lexer: Any = None,
    theme: Any = None,
    style_fallback: str,
) -> None:
    """Append one source line with light token colors (Grok syntect analogue)."""
    if not line:
        out.append(" ", style=f"{style_fallback} on {_CODE_BG}")
        return
    if lexer is None or theme is None:
        out.append(line, style=f"{style_fallback} on {_CODE_BG}")
        return
    try:
        from pygments import lex

        for _tok, text in lex(line, lexer):
            if not text:
                continue
            # Pygments often emits a trailing "\n" even when the input has none —
            # that would double-space when we also join lines ourselves.
            if text == "\n" or text == "\r\n":
                continue
            text = text.replace("\n", "")
            if not text:
                continue
            style = theme.get_style_for_token(_tok)
            color = getattr(style, "color", None) or style_fallback
            # Normalize Color objects to hex/name string for Rich style.
            if color is not None and not isinstance(color, str):
                try:
                    color = color.name or str(color)
                except Exception:
                    color = style_fallback
            out.append(text, style=f"{color} on {_CODE_BG}")
    except Exception:
        out.append(line, style=f"{style_fallback} on {_CODE_BG}")


def render_read_body(
    content: str,
    path: str = "",
    *,
    start_line: int = 1,
    expanded: bool = False,
    first_lines: int = READ_FIRST_LINES,
    last_lines: int = READ_LAST_LINES,
    width: int | None = None,
) -> RenderableType | None:
    """Grok Read body: dim line numbers **outside** code bg + elevated source.

    Matches Grok ReadToolCallBlock chrome:
    - gutter numbers on message bg (not inside elevated band)
    - source lightly highlighted on ``md_code_bg``
    - truncated as first N + … + last M
    """
    body = (content or "").rstrip("\n")
    if not body:
        return None
    if len(body) > READ_MAX_CHARS:
        body = body[: READ_MAX_CHARS - 1] + "…"

    raw_lines = body.splitlines() or [""]
    total = len(raw_lines)
    lang = _lang_from_path(path) if path else "text"
    base = max(1, start_line)
    w = max(8, width or 80)

    # Markdown files: Grok-style rendered Markdown (not a JSON/code dump).
    if lang == "markdown":
        from room_tui.llm.msg_layout import indent_renderable

        if expanded or total <= first_lines + last_lines:
            text = body
        else:
            shown, _trunc = _head_tail_lines(
                raw_lines, first=first_lines, last=last_lines
            )
            text = "\n".join(shown + ["…"] + raw_lines[-last_lines:])
        # Indent to process content column; Markdown itself paints headings etc.
        return indent_renderable(_md(text))

    # Build (line_no | None, source) for head / ellipsis / tail.
    rows: list[tuple[int | None, str]] = []
    if expanded or total <= first_lines + last_lines:
        for i, src in enumerate(raw_lines):
            rows.append((base + i, src))
    else:
        for i, src in enumerate(raw_lines[:first_lines]):
            rows.append((base + i, src))
        rows.append((None, "…"))
        tail_start = total - last_lines
        for i, src in enumerate(raw_lines[-last_lines:]):
            rows.append((base + tail_start + i, src))

    max_no = base + total - 1
    return paint_read_code_band(
        rows, max_line_no=max_no, lang=lang, width=w
    )


def read_line_count(content: str) -> int:
    body = (content or "").rstrip("\n")
    if not body:
        return 0
    return body.count("\n") + 1


def format_tool_header_markup(
    tool: str,
    args: Any = None,
    *,
    is_error: bool = False,
    line_count: int | None = None,
    empty: bool = False,
    short_result: str = "",
    file_count: int | None = None,
) -> str:
    """Grok process-row header: green/red ``❙  ◆ title``.

    Option A: titles are process labels (Run/Read/Edit), not raw argv dumps.
    """
    from room_tui.llm.msg_layout import tool_header_markup

    tool = (tool or "tool").strip()
    ltool = tool.lower()
    path = extract_path(args) if args is not None else ""
    cmd = extract_command(args) if args is not None else ""
    path_disp = _shorten_path(path) if path else ""

    esc = r"\["

    def _esc(s: str) -> str:
        return (s or "").replace("[", esc)

    if is_read_tool(tool):
        title = read_process_title(path, file_count=file_count or 1)
        # Quiet process label — path already folded into title when short.
        detail = ""
        if empty:
            detail = f" [{COLOR_MSG_DIM}](empty)[/{COLOR_MSG_DIM}]"
        label = f"[{COLOR_MSG_HI}]{_esc(title)}[/{COLOR_MSG_HI}]{detail}"
        return tool_header_markup("", label, is_error=is_error, success_rail=not is_error)

    if is_bash_tool(tool):
        title = bash_process_title(cmd or tool)
        # Grok process rows: muted title, not gold $ argv dump.
        label = f"[{COLOR_MSG_HI}]{_esc(title)}[/{COLOR_MSG_HI}]"
        return tool_header_markup("", label, is_error=is_error, success_rail=not is_error)

    if is_edit_tool(tool):
        title = edit_process_title(path, tool=tool)
        # Split name / path for orange path accent when possible.
        if path_disp:
            name = "Write" if ltool in ("write", "write_file", "create_file") or "write" in ltool else "Edit"
            if name != "Write" and ltool == "write":
                name = "Write"
            label = (
                f"[bold {COLOR_MSG_HI}]{name} [/bold {COLOR_MSG_HI}]"
                f"[{COLOR_PATH}]{_esc(path_disp)}[/{COLOR_PATH}]"
            )
        else:
            label = f"[bold {COLOR_MSG_HI}]{_esc(title)}[/{COLOR_MSG_HI}]"
        return tool_header_markup("", label, is_error=is_error, success_rail=not is_error)

    # Search-like tools
    if ltool in ("grep", "search", "rg", "glob", "list_dir", "find"):
        q = ""
        if isinstance(args, dict):
            for k in ("pattern", "query", "glob", "path"):
                if args.get(k):
                    q = str(args[k])
                    break
        bit = _cell_short(q, 40) if q else ""
        if ltool in ("grep", "search", "rg"):
            title = "Searched 1 pattern" + (f" {bit}" if bit else "")
        elif ltool in ("glob", "find"):
            title = "Searched files" + (f" {bit}" if bit else "")
        else:
            title = "Listed" + (f" {bit}" if bit else " directory")
        label = f"[{COLOR_MSG_HI}]{_esc(_cell_short(title, 60))}[/{COLOR_MSG_HI}]"
        return tool_header_markup("", label, is_error=is_error, success_rail=not is_error)

    # Fetch / index / web-ish tools — short process title, no URL dump in header.
    if any(
        k in ltool
        for k in (
            "fetch",
            "index",
            "crawl",
            "scrape",
            "http",
            "web_search",
            "browse",
        )
    ):
        title = tool.replace("_", " ").strip() or "tool"
        # Prefer human-ish short name
        if "fetch" in ltool and "index" in ltool:
            title = "Fetch and index"
        elif "fetch" in ltool:
            title = "Fetch"
        label = f"[{COLOR_MSG_HI}]{_esc(_cell_short(title, 48))}[/{COLOR_MSG_HI}]"
        if path_disp:
            label += f" [{COLOR_PATH}]{_esc(_cell_short(path_disp, 36))}[/{COLOR_PATH}]"
        return tool_header_markup("", label, is_error=is_error, success_rail=not is_error)

    extra = path_disp or _cell_short(cmd, 40) or short_result
    if extra:
        label = (
            f"[{COLOR_MSG_HI}]{_esc(tool)}[/{COLOR_MSG_HI}] "
            f"[{COLOR_MSG_DIM}]{_esc(str(extra))}[/{COLOR_MSG_DIM}]"
        )
    else:
        label = f"[{COLOR_MSG_HI}]{_esc(tool)}[/{COLOR_MSG_HI}]"
    return tool_header_markup("", label, is_error=is_error, success_rail=not is_error)


def _content_band(inner: RenderableType, *, indent: int = 0) -> RenderableType:
    """Full-width code band (``md_code_bg``) to the message-column edge.

    Optional ``indent`` leaves a clear gutter (for content under ``◆`` headers)
    while the band still expands to the right edge — not content-chip width.
    """
    from room_tui.llm.msg_layout import full_width_band

    return full_width_band(inner, bg=_CODE_BG, indent=indent)


def _full_width_band(inner: RenderableType) -> RenderableType:
    """Expand a renderable to the full message-list width with code-band fill."""
    return _content_band(inner, indent=0)


def _strip_residual_fences(code: str, lang: str = "") -> tuple[str, str]:
    """Drop accidental ``` / lang tag lines left inside a code body."""
    raw = (code or "").replace("\r\n", "\n")
    lines = raw.split("\n")
    # Opening: ```python or ```
    if lines and re.match(r"^```(\S*)\s*$", lines[0].strip()):
        m = re.match(r"^```(\S*)\s*$", lines[0].strip())
        if m and m.group(1) and not (lang or "").strip():
            lang = m.group(1)
        lines = lines[1:]
    # Closing fence
    while lines and re.match(r"^```\s*$", lines[-1].strip()):
        lines = lines[:-1]
    return "\n".join(lines), (lang or "text")


def render_fenced_code(
    code: str,
    lang: str = "text",
    *,
    width: int | None = None,
    indent: int | None = None,
    line_numbers: bool = False,
) -> Text:
    """Grok fenced / content code band: light syntax + full-width ``md_code_bg``.

    Used for assistant `` ``` `` blocks, json, and any content-block code —
    same visual language as Read body (minus required line numbers).
    Never paints the fence markers themselves.
    """
    from room_tui.llm.msg_layout import MSG_INDENT, full_width_band

    w = max(8, width or 80)
    ind = MSG_INDENT if indent is None else max(0, indent)
    code, lang = _strip_residual_fences(code, lang)
    lines = (code or "").splitlines() or [""]
    lang_key = (lang or "text").strip().lower() or "text"
    if lang_key in ("", "text", "plain", "txt"):
        lang_key = "text"

    if line_numbers:
        rows: list[tuple[int | None, str]] = [
            (i + 1, ln) for i, ln in enumerate(lines)
        ]
        styled = _paint_gutter_rows(
            rows, max_line_no=max(1, len(lines)), lang=lang_key
        )
    else:
        styled = Text(no_wrap=False)
        lexer = _lexer_for_lang(lang_key)
        theme = _pygments_theme()
        for i, line in enumerate(lines):
            if i:
                styled.append("\n")
            _append_highlighted_line(
                styled,
                line,
                lexer=lexer,
                theme=theme,
                style_fallback=COLOR_MSG_USER,
            )
    return full_width_band(styled, bg=_CODE_BG, indent=ind, width=w)  # type: ignore[return-value]


def _full_width_code(code: str, lang: str = "text") -> RenderableType:
    """Fenced code block — GrokNight band + syntax (legacy helper)."""
    return render_fenced_code(code, lang, width=80, indent=0)


def _diff_renderable(code: str) -> Text:
    """Grok-like unified diff text (caller wraps with full-width / indented band)."""
    body = Text(no_wrap=False)
    lines = (code or "").splitlines() or [""]
    for i, line in enumerate(lines):
        if i:
            body.append("\n")
        if line.startswith("+++") or line.startswith("---") or line.startswith("diff "):
            body.append(line, style=f"{COLOR_MSG_HI} on {_CODE_BG}")
        elif line.startswith("@@"):
            body.append(line, style=f"{COLOR_MSG_MID} on {_CODE_BG}")
        elif line.startswith("+"):
            body.append(line, style=f"{COLOR_DIFF_INS_FG} on {COLOR_DIFF_INS_BG}")
        elif line.startswith("-"):
            body.append(line, style=f"{COLOR_DIFF_DEL_FG} on {COLOR_DIFF_DEL_BG}")
        else:
            body.append(line, style=f"{COLOR_MSG_LABEL} on {_CODE_BG}")
    return body


def _paint_diff_band(code: str, *, width: int | None = None) -> RenderableType:
    from room_tui.llm.msg_layout import MSG_INDENT, full_width_band

    return full_width_band(
        _diff_renderable(code),
        bg=_CODE_BG,
        indent=MSG_INDENT,
        width=max(8, width or 80),
    )


def _short(s: str, limit: int) -> str:
    s = s or ""
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "…"


def _cell_short(s: str, limit: int) -> str:
    """Truncate by display cells (CJK-safe), append … if needed."""
    from rich.cells import cell_len, set_cell_size

    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return ""
    if cell_len(s) <= limit:
        return s
    if limit <= 1:
        return "…"
    return set_cell_size(s, limit - 1).rstrip() + "…"


def bash_process_title(cmd: str, *, limit: int = 60) -> str:
    """Grok process-row title for shell tools: ``Run <short>`` (not raw $ argv).

    Strips common redirects/noise and collapses whitespace so scrollback stays
    a compact timeline instead of a terminal dump.
    """
    raw = re.sub(r"\s+", " ", (cmd or "").strip())
    if not raw:
        return "Run"
    # Drop common shell redirects / noise tails.
    raw = re.sub(r"\s+2>&1\b", "", raw)
    raw = re.sub(r"\s+1>&2\b", "", raw)
    raw = re.sub(r"\s+>/dev/null\b", "", raw)
    raw = re.sub(r"\s+2>/dev/null\b", "", raw)
    raw = re.sub(r"\s+&>/dev/null\b", "", raw)
    raw = raw.strip()
    body = _cell_short(raw, max(8, limit - 4))  # room for "Run "
    return f"Run {body}" if body else "Run"


def read_process_title(
    path: str = "",
    *,
    file_count: int | None = None,
    limit: int = 60,
) -> str:
    """Grok Read process title: ``Read 1 file`` or ``Read <short-path>``."""
    n = file_count if file_count is not None else 1
    if n != 1:
        return f"Read {n} files"
    # Title-only style like screenshot #1: "Read 1 file".
    # Path is optional detail when very short; otherwise keep count form.
    p = _shorten_path(path) if path else ""
    if p and len(p) <= 36:
        return _cell_short(f"Read {p}", limit)
    return "Read 1 file"


def edit_process_title(path: str = "", *, tool: str = "Edit", limit: int = 60) -> str:
    name = (tool or "Edit").strip()
    # Normalize write/edit labels to Grok casing.
    low = name.lower()
    if low in ("write", "edit", "search_replace", "apply_patch"):
        name = "Write" if low == "write" else "Edit"
    p = _shorten_path(path) if path else ""
    if p:
        return _cell_short(f"{name} {p}", limit)
    return name


def extract_command(args: Any) -> str:
    """Pull shell command string from Pi tool args."""
    if args is None:
        return ""
    if isinstance(args, str):
        # Sometimes args is already the command
        t = args.strip()
        if t.startswith("{"):
            try:
                return extract_command(json.loads(t))
            except json.JSONDecodeError:
                return t
        return t
    if isinstance(args, dict):
        for k in ("command", "cmd", "bash", "script", "input"):
            if k in args and args[k] is not None and str(args[k]).strip():
                return str(args[k]).strip()
        # nested
        for k in ("arguments", "args", "params"):
            if k in args:
                inner = extract_command(args[k])
                if inner:
                    return inner
    return ""


def extract_path(args: Any) -> str:
    """Pull a filesystem path from tool args (dict or bare string)."""
    if args is None:
        return ""
    if isinstance(args, str):
        t = args.strip()
        if not t:
            return ""
        if t.startswith("{"):
            try:
                return extract_path(json.loads(t))
            except json.JSONDecodeError:
                pass
        # Bare path / home-relative path
        if t.startswith(("/", "~", "./", "../")) or (
            len(t) < 260 and "\n" not in t and " " not in t and ("/" in t or t.endswith(
                (".py", ".c", ".h", ".ts", ".js", ".rs", ".go", ".md", ".json", ".txt")
            ))
        ):
            return t
        return ""
    if not isinstance(args, dict):
        return ""
    for k in (
        "path",
        "filePath",
        "file_path",
        "filename",
        "file",
        "target_file",
        "target",
        "uri",
        "filepath",
        "name",
    ):
        if k in args and args[k]:
            return str(args[k])
    # Nested envelopes
    for k in ("arguments", "args", "params", "input"):
        if k in args and args[k] is not None:
            inner = extract_path(args[k])
            if inner:
                return inner
    return ""


def extract_output(result: Any) -> str:
    """Normalize Pi tool results to plain terminal/file text.

    Handles:
    - plain strings (incl. JSON-string MCP envelopes)
    - ``[{type: text, text: "..."}]`` content blocks
    - ``{content|output|stdout|text: ...}``
    """
    if result is None:
        return ""
    if isinstance(result, str):
        s = result
        # Tool layer sometimes returns a JSON string instead of a parsed object.
        unwrapped = _unwrap_json_content_envelope(s)
        if unwrapped is not None:
            return unwrapped
        return s
    if isinstance(result, (int, float, bool)):
        return str(result)
    if isinstance(result, list):
        parts: list[str] = []
        for item in result:
            if isinstance(item, dict):
                t = item.get("type")
                if t in ("text", "output", "stdout") or "text" in item:
                    parts.append(str(item.get("text") or item.get("content") or ""))
                elif "content" in item:
                    parts.append(extract_output(item["content"]))
                else:
                    # skip pure metadata objects
                    if set(item.keys()) <= {"type", "mimeType", "mime_type"}:
                        continue
                    parts.append(extract_output(item))
            else:
                parts.append(str(item))
        return "".join(parts)
    if isinstance(result, dict):
        # error shapes
        if result.get("isError") or result.get("is_error"):
            for k in ("message", "error", "stderr", "text", "content"):
                if result.get(k):
                    return extract_output(result[k])
        for k in (
            "content",
            "output",
            "stdout",
            "text",
            "result",
            "data",
            "body",
            "value",
        ):
            if k in result and result[k] is not None:
                return extract_output(result[k])
        if "stderr" in result and result["stderr"]:
            return extract_output(result["stderr"])
        # last resort: compact json without dumping whole nested mess if huge
        try:
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        except Exception:
            return str(result)
    return str(result)


def coalesce_tool_blocks(blocks: list[AgentBlock]) -> list[AgentBlock]:
    """Merge tool start stubs into later completed calls with same name/command."""
    out: list[AgentBlock] = []
    for b in blocks:
        if b.kind != "tool":
            out.append(b)
            continue
        cmd = extract_command(b.tool_args)
        # Find prior incomplete same tool
        merged = False
        for i in range(len(out) - 1, -1, -1):
            prev = out[i]
            if prev.kind != "tool":
                continue
            if prev.tool_name != b.tool_name:
                continue
            prev_cmd = extract_command(prev.tool_args)
            same = (cmd and prev_cmd and cmd == prev_cmd) or (not cmd and not prev_cmd)
            if not same:
                continue
            # Prefer block that has a result; fill args/result into earlier stub
            if prev.tool_result is None and b.tool_result is not None:
                prev.tool_result = b.tool_result
                prev.is_error = b.is_error or prev.is_error
                if b.tool_args is not None:
                    prev.tool_args = b.tool_args
                merged = True
                break
            if prev.tool_result is not None and b.tool_result is None:
                # drop empty duplicate
                merged = True
                break
            if prev.tool_result is None and b.tool_result is None:
                # keep one stub
                if b.tool_args is not None:
                    prev.tool_args = b.tool_args
                merged = True
                break
            if prev.tool_result is not None and b.tool_result is not None:
                # same completed tool twice — keep one (prefer error if any)
                if b.is_error and not prev.is_error:
                    out[i] = b
                merged = True
                break
        if not merged:
            out.append(b)
    return out


def _status_mark(is_error: bool) -> tuple[str, str]:
    """Grok tool chrome: diamond bullet; status tint on completion."""
    if is_error:
        return _DIAMOND, COLOR_ERR
    return _DIAMOND, COLOR_OK


def _truncate_tool_lines(lines: list[str]) -> list[str]:
    """Grok-style head/tail truncate for long tool stdout."""
    if len(lines) <= READ_FIRST_LINES + READ_LAST_LINES:
        return lines or [""]
    return lines[:READ_FIRST_LINES] + ["…"] + lines[-READ_LAST_LINES:]


def _bash_stdout_lines(out: str) -> list[str]:
    """Normalize bash stdout for painted band (pretty JSON when single blob)."""
    text = (out or "").rstrip("\n")
    if not text:
        return [""]
    sample = text.lstrip()
    if sample.startswith(("{", "[")) and "\n" not in text[:200]:
        try:
            parsed = json.loads(text)
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    text = _short(text, 4000)
    return _truncate_tool_lines(text.splitlines() or [""])


def _header_body_group(header: RenderableType, body: RenderableType) -> Group:
    """Grok: blank row between tool title and body band."""
    from room_tui.llm.msg_layout import block_gap_text

    return Group(header, block_gap_text(), body)


def _bash_renderable(
    block: AgentBlock, *, width: int | None = None
) -> RenderableType:
    """Grok Execute: ``❙  ◆ Run …`` + stdout band (truncated)."""
    from rich.markup import render as render_markup

    from room_tui.llm.msg_layout import indent_renderable, paint_output_band

    header_mk = format_tool_header_markup(
        block.tool_name or "bash",
        block.tool_args,
        is_error=block.is_error,
    )
    try:
        header = render_markup(header_mk)
    except Exception:
        header = Text.from_markup(header_mk)
    out = extract_output(block.tool_result).rstrip("\n")
    if not out:
        if block.is_error:
            return _header_body_group(
                header, indent_renderable(Text("(failed)", style=COLOR_ERR))
            )
        return header
    w = max(8, width or 80)
    body = paint_output_band(
        _bash_stdout_lines(out), width=w, bg=_CODE_BG
    )
    return _header_body_group(header, body)


def _shorten_path(path: str) -> str:
    path_disp = path.replace("\\", "/")
    home = __import__("os").path.expanduser("~")
    if path_disp.startswith(home):
        path_disp = "~" + path_disp[len(home) :]
    return _short(path_disp, 80)


def _lang_from_path(path_disp: str) -> str:
    p = (path_disp or "").lower()
    if p.endswith((".py", ".pyi")):
        return "python"
    if p.endswith((".ts", ".tsx")):
        return "typescript"
    if p.endswith((".js", ".jsx", ".mjs", ".cjs")):
        return "javascript"
    if p.endswith((".rs",)):
        return "rust"
    if p.endswith((".go",)):
        return "go"
    if p.endswith((".c", ".h")):
        return "c"
    if p.endswith((".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")):
        return "cpp"
    if p.endswith((".java",)):
        return "java"
    if p.endswith((".json",)):
        return "json"
    if p.endswith((".toml",)):
        return "toml"
    if p.endswith((".yaml", ".yml")):
        return "yaml"
    if p.endswith((".sh", ".bash", ".zsh")):
        return "bash"
    if p.endswith((".md", ".mdx")):
        return "markdown"
    if p.endswith((".html", ".htm")):
        return "html"
    if p.endswith((".css",)):
        return "css"
    if p.endswith((".sql",)):
        return "sql"
    return "text"


# Edit snippet: Grok shows a few numbered lines under Edit path (not full dump).
EDIT_SNIPPET_LINES = 6


def edit_snippet_lines(content: str, *, max_lines: int = EDIT_SNIPPET_LINES) -> list[str]:
    """Cap edit/write result to a short Grok-style preview."""
    body = (content or "").rstrip("\n")
    if not body:
        return []
    lines = body.splitlines()
    if len(lines) <= max_lines:
        return lines
    return lines[:max_lines] + ["…"]


def _edit_snippet_renderable(
    content: str,
    *,
    width: int | None = None,
    start_line: int = 1,
    path: str = "",
) -> RenderableType | None:
    """Numbered short snippet under Edit — gutter outside code bg (Grok)."""
    lines = edit_snippet_lines(content)
    if not lines:
        return None
    w = max(8, width or 80)
    lang = _lang_from_path(path) if path else "text"
    rows: list[tuple[int | None, str]] = []
    num = max(1, start_line)
    max_no = num
    for src in lines:
        if src == "…":
            rows.append((None, "…"))
            continue
        rows.append((num, src))
        max_no = num
        num += 1
    return paint_read_code_band(
        rows, max_line_no=max_no, lang=lang, width=w
    )


def _read_renderable(
    block: AgentBlock, *, width: int | None = None
) -> RenderableType:
    """Grok Read: header + Truncated body (5+…+3 gutter preview)."""
    from rich.markup import render as render_markup

    path = extract_path(block.tool_args) or extract_command(block.tool_args) or ""
    path_disp = _shorten_path(path) if path else ""
    body = sanitize_read_output(extract_output(block.tool_result)).rstrip("\n")
    n = read_line_count(body)
    header_mk = format_tool_header_markup(
        "read",
        {"path": path} if path else None,
        is_error=block.is_error,
        line_count=n or None,
        empty=not body,
    )
    try:
        header = render_markup(header_mk)
    except Exception:
        header = Text.from_markup(header_mk)
    if not body:
        if block.is_error:
            from room_tui.llm.msg_layout import indent_renderable

            return _header_body_group(
                header, indent_renderable(Text("(failed)", style=COLOR_ERR))
            )
        return header
    content = render_read_body(
        body, path_disp or path, expanded=False, width=max(8, width or 80)
    )
    if content is None:
        return header
    return _header_body_group(header, content)


def _generic_tool_renderable(
    block: AgentBlock, *, width: int | None = None
) -> RenderableType:
    """Grok process title only — hide tool result body after success.

    Errors: full-width code band (aligned indent), not a raw multi-line Text
    that only pads the first line.
    """
    from rich.markup import render as render_markup

    from room_tui.llm.msg_layout import indent_renderable, paint_output_band

    w = max(8, width or 80)
    header_mk = format_tool_header_markup(
        block.tool_name or "tool",
        block.tool_args,
        is_error=block.is_error,
    )
    try:
        header = render_markup(header_mk)
    except Exception:
        header = Text.from_markup(header_mk)
    if block.is_error:
        out = extract_output(block.tool_result).rstrip("\n")
        if not out.strip():
            return _header_body_group(
                header, indent_renderable(Text("(failed)", style=COLOR_ERR))
            )
        lines = _truncate_tool_lines(_short(out, 4000).splitlines() or [""])
        body = paint_output_band(
            lines, width=w, bg=_CODE_BG, fg=COLOR_ERR
        )
        return _header_body_group(header, body)
    # Success: header only (no "Fetched and indexed…" dump).
    return header


def _tool_panel(block: AgentBlock, *, width: int | None = None) -> RenderableType:
    name = (block.tool_name or "tool").strip()
    if is_bash_tool(name):
        return _bash_renderable(block, width=width)
    if is_read_tool(name):
        return _read_renderable(block, width=width)
    # Edit/write: process header + short snippet (or dim status line).
    if is_edit_tool(name):
        from rich.markup import render as render_markup

        from room_tui.llm.msg_layout import indent_renderable

        header_mk = format_tool_header_markup(
            name,
            block.tool_args,
            is_error=block.is_error,
        )
        try:
            header = render_markup(header_mk)
        except Exception:
            header = Text.from_markup(header_mk)
        out = extract_output(block.tool_result).rstrip("\n")
        if not out:
            if block.is_error:
                return Group(
                    header, indent_renderable(Text("(failed)", style=COLOR_ERR))
                )
            return header
        if looks_like_write_status(out):
            from room_tui.llm.msg_layout import content_pad

            return _header_body_group(
                header,
                Text(content_pad() + _short(out, 160), style=COLOR_MSG_DIM),
            )
        path = extract_path(block.tool_args) or ""
        snippet = _edit_snippet_renderable(out, width=width, path=path)
        if snippet is None:
            return header
        return _header_body_group(header, snippet)
    # Unknown tool that returned file-like body → Read chrome (cat via weird names).
    out = extract_output(block.tool_result).rstrip("\n")
    path = extract_path(block.tool_args) or ""
    if path and looks_like_source_content(out):
        return _read_renderable(block, width=width)
    return _generic_tool_renderable(block, width=width)


def _thinking_panel(block: AgentBlock) -> RenderableType:
    """Grok thinking: muted header + breath + dim summary (≤3 lines)."""
    from rich.markup import render as render_markup
    from rich.style import Style

    from room_tui.llm.msg_layout import MSG_INDENT, thought_markup

    body = (block.text or "").strip()
    # truncate_lines=3 — keep a short tail like live Thinking.
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) > 3:
        lines = ["…"] + lines[-3:]
    preview = "\n".join(lines)
    if len(preview) > 400:
        preview = _short(preview, 400)
    try:
        header = render_markup(thought_markup("Thinking…"))
    except Exception:
        header = Text(f"   {_DIAMOND} Thinking…", style=COLOR_MSG_MID)
    if not preview:
        return header
    pad = " " * MSG_INDENT
    body_style = Style(color=COLOR_MSG_DIM)
    content = Text(no_wrap=True)
    content.append(" ")  # breath under header
    for ln in preview.split("\n"):
        content.append("\n")
        content.append(pad + ln, style=body_style)
    return Group(header, content)


def _thought_header(block: AgentBlock) -> RenderableType:
    """Grok collapsed thinking: ``◆  Thought for Xs`` in content layout."""
    from rich.markup import render as render_markup

    from room_tui.llm.msg_layout import thought_markup

    elapsed = block.elapsed_s
    if elapsed is not None and elapsed >= 0.05:
        if elapsed < 60:
            label = f"Thought for {elapsed:.1f}s"
        else:
            label = f"Thought for {int(elapsed)}s"
    else:
        label = "Thought"
    try:
        return render_markup(thought_markup(label))
    except Exception:
        return Text.from_markup(thought_markup(label))


def render_block(
    block: AgentBlock, *, width: int | None = None
) -> RenderableType | None:
    """Return a Rich renderable for one block, or None if empty.

    *width* is the message-list column width — required for solid full-width
    tool/code bands (otherwise long lines soft-wrap into jagged chips).
    """
    kind = block.kind
    if kind == "thought":
        # Collapsed Grok thinking header — process stays after turn/re-entry.
        return _thought_header(block)
    if kind == "thinking":
        if not (block.text or "").strip():
            # Empty body still shows a quiet header if elapsed known.
            if block.elapsed_s is not None:
                return _thought_header(block)
            return None
        # Prefer collapsed header when elapsed is set (post-turn / history).
        if block.elapsed_s is not None and block.elapsed_s >= 0.05:
            return _thought_header(block)
        return _thinking_panel(block)
    if kind == "tool":
        return _tool_panel(block, width=width)
    if kind == "diff":
        code = block.text or ""
        if not code.strip():
            return None
        return _paint_diff_band(code, width=width)
    if kind == "code":
        code = block.text or ""
        if not code.strip():
            return None
        return render_fenced_code(
            code,
            block.language or "text",
            width=width,
        )
    if kind == "json":
        code = block.text or ""
        if not code.strip():
            return None
        return render_fenced_code(code, "json", width=width)
    if kind in ("text", "markdown", "plain"):
        t = (block.text or "").rstrip()
        if not t:
            return None
        if kind == "plain":
            return Text(t, style=COLOR_MSG_TEXT)
        # If fences remain, re-split so code gets Grok full-width bands — never
        # fall through to Rich Markdown (chip Syntax + visible ``` markers).
        if "```" in t:
            from room_tui.llm.agent_blocks import classify_plain_text

            parts = classify_plain_text(t)
            if parts and not (
                len(parts) == 1
                and parts[0].kind in ("text", "markdown", "plain")
                and parts[0].text == t
            ):
                from rich.console import Group

                rendered: list[RenderableType] = []
                for p in parts:
                    if p.kind in ("text", "markdown", "plain"):
                        pt = (p.text or "").rstrip()
                        if not pt:
                            continue
                        # Nested fences already classified; plain prose only.
                        if "```" in pt and pt != t:
                            rr = render_block(p, width=width)
                        else:
                            # Strip any leftover fence lines from prose leaks.
                            pt2 = re.sub(
                                r"^```\w*[ \t]*\n?", "", pt, count=1
                            )
                            pt2 = re.sub(r"\n```[ \t]*$", "", pt2)
                            rr = Text(pt2, style=COLOR_MSG_TEXT) if pt2.strip() else None
                        if rr is not None:
                            rendered.append(rr)
                    else:
                        rr = render_block(p, width=width)
                        if rr is not None:
                            rendered.append(rr)
                if len(rendered) == 1:
                    return rendered[0]
                if rendered:
                    return Group(*rendered)
            # Single unparsed blob still containing fences — strip markers, show as code.
            body, lang = _strip_residual_fences(t, "text")
            if body.strip():
                return render_fenced_code(body, lang or "text", width=width)
        return _md(t)
    if kind == "error":
        t = (block.text or "error").strip()
        return Text(t, style=COLOR_ERR)
    t = (block.text or "").rstrip()
    return _md(t) if t else None


def render_blocks(
    blocks: Iterable[AgentBlock], *, width: int | None = None
) -> list[RenderableType]:
    """Render a sequence of blocks; coalesces tool stubs; skips empties."""
    coalesced = coalesce_tool_blocks(list(blocks))
    out: list[RenderableType] = []
    for b in coalesced:
        r = render_block(b, width=width)
        if r is not None:
            out.append(r)
    return out
