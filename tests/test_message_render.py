"""Code / tool message render helpers (GrokNight-aligned)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.table import Table

from room_tui.llm.agent_blocks import AgentBlock
from room_tui.llm.message_render import (
    _CODE_BG,
    _CODE_THEME,
    _diff_renderable,
    _full_width_code,
    render_block,
)
from room_tui.ui_state import COLOR_MD_CODE_BG


def _measure_width(renderable, console_width: int = 72) -> int:
    """Return max printed line width (cells) for a renderable."""
    buf = StringIO()
    c = Console(file=buf, width=console_width, force_terminal=True, color_system=None)
    c.print(renderable)
    lines = buf.getvalue().splitlines()
    return max((len(line) for line in lines), default=0)


def test_code_theme_and_bg_are_groknight() -> None:
    assert _CODE_BG == COLOR_MD_CODE_BG == "#1c1c1c"
    assert _CODE_THEME == "github-dark"


def test_render_read_body_truncates_head_tail() -> None:
    from room_tui.llm.message_render import READ_FIRST_LINES, READ_LAST_LINES, render_read_body

    lines = [f"line-{i}" for i in range(1, 21)]
    body = "\n".join(lines)
    r = render_read_body(body, "foo.c", expanded=False)
    assert r is not None
    # Expanded shows all (still width-expanded wrapper).
    full = render_read_body(body, "foo.c", expanded=True)
    assert full is not None
    short = render_read_body("\n".join(lines[:3]), "foo.c", expanded=False)
    assert short is not None
    assert READ_FIRST_LINES == 5 and READ_LAST_LINES == 3


def test_read_line_count() -> None:
    from room_tui.llm.message_render import read_line_count

    assert read_line_count("") == 0
    assert read_line_count("a") == 1
    assert read_line_count("a\nb\nc") == 3


def test_full_width_code_expands_short_snippet() -> None:
    from rich.cells import cell_len
    from rich.text import Text

    r = _full_width_code("import akshare as ak\ndf = 1", "python")
    assert isinstance(r, Text)
    # Full-width band: rows pad to default width 80.
    for row in r.plain.split("\n"):
        assert cell_len(row) == 80
    assert "import" in r.plain


def test_render_block_code_and_json() -> None:
    from rich.cells import cell_len
    from rich.text import Text

    r = render_block(
        AgentBlock(kind="code", text="print(1)", language="python"), width=40
    )
    assert r is not None
    assert isinstance(r, Text)
    assert "print(1)" in r.plain
    # Full-width painted band: every visual row fills *width* cells.
    for row in r.plain.split("\n"):
        assert cell_len(row) == 40
    # Grok style: syntax spans present (not mono-only plain).
    assert r.spans, "fenced code must carry highlight/bg spans"
    j = render_block(AgentBlock(kind="json", text='{"a":1}'), width=40)
    assert j is not None
    assert isinstance(j, Text)
    assert "a" in j.plain


def test_render_block_diff() -> None:
    from rich.cells import cell_len
    from rich.text import Text

    d = render_block(AgentBlock(kind="diff", text="-a\n+b"), width=40)
    assert d is not None
    assert isinstance(d, Text)
    for row in d.plain.split("\n"):
        assert cell_len(row) == 40
    assert _measure_width(_diff_renderable("-x\n+y"), 64) >= 1


def test_classify_and_render_fenced_python() -> None:
    """Assistant ``` fences become Grok code bands, not raw backticks."""
    from room_tui.llm.agent_blocks import classify_plain_text

    body = "intro\n\n```python\ndef hello():\n    return 1\n```\n\noutro"
    parts = classify_plain_text(body)
    kinds = [p.kind for p in parts]
    assert "code" in kinds
    code_b = next(p for p in parts if p.kind == "code")
    assert "def hello" in code_b.text
    r = render_block(code_b, width=48)
    assert r is not None
    assert "def hello" in getattr(r, "plain", "")
    assert "```" not in getattr(r, "plain", "")


def test_unclosed_fence_still_code_block() -> None:
    from room_tui.llm.agent_blocks import classify_plain_text

    body = 'before\n\n```javascript\nconsole.log(FILE_CONTENT);\n"""\nmailbox.py'
    parts = classify_plain_text(body)
    assert any(p.kind == "code" for p in parts)
    code_b = next(p for p in parts if p.kind == "code")
    assert "console.log" in code_b.text or "mailbox" in code_b.text


def test_render_read_body_paints_bg() -> None:
    from io import StringIO

    from rich.console import Console

    from room_tui.llm.message_render import render_read_body

    body = "\n".join(f"line {i}" for i in range(1, 20))
    r = render_read_body(body, "foo.c", width=48)
    assert r is not None
    c = Console(file=StringIO(), width=48, force_terminal=True, color_system="truecolor")
    segs = list(c.render(r, c.options.update_width(48)))
    assert any(s.style and s.style.bgcolor for s in segs)


def test_render_read_body_grok_gutter_and_highlight() -> None:
    """Grok Read: dim line numbers + syntax spans (not mono primary)."""
    from rich.text import Text

    from room_tui.llm.message_render import render_read_body
    from room_tui.ui_state import COLOR_MSG_MID

    src = "/* comment */\nint main(void) {\n  return 0;\n}\n"
    # pad to force head/tail truncation path
    src = src + "\n".join(f"  // pad {i}" for i in range(20))
    r = render_read_body(src, "ring_buffer.c", width=56)
    assert r is not None
    assert isinstance(r, Text)
    plain = r.plain
    assert "1" in plain  # gutter
    assert "main" in plain or "return" in plain or "comment" in plain
    # Gutter style uses gray_dim / mid — present in style strings on Text.
    style_blob = " ".join(str(sp.style) for sp in r.spans if sp.style)
    assert COLOR_MSG_MID in style_blob or "585858" in style_blob or "on #1c1c1c" in style_blob
    # Truncation ellipsis present for long file.
    assert "…" in plain


def test_read_gutter_outside_code_background() -> None:
    """Line numbers must NOT carry code-band bgcolor (Grok: gutter outside bg)."""
    from room_tui.llm.message_render import render_read_body
    from room_tui.llm.msg_layout import MSG_INDENT
    from room_tui.ui_state import COLOR_MD_CODE_BG, COLOR_MSG_MID

    r = render_read_body("int x = 1;\nreturn x;\n", "foo.c", width=48)
    assert r is not None
    # Each logical line: indent + gutter (no bg) + code (with bg).
    # Locate gutter span right after indent spaces.
    for line in r.plain.split("\n"):
        # skip empty
        if not line.strip():
            continue
        # Find first digit run after leading spaces (line number).
        i = 0
        while i < len(line) and line[i] == " ":
            i += 1
        j = i
        while j < len(line) and line[j].isdigit():
            j += 1
        if j == i:
            continue
        # Map this line's gutter substring to global offsets in r.plain
        # Prefer style check via spans covering gutter digits only.
        break
    # Spans whose text is only whitespace+digits+spaces (gutter token) and
    # style is COLOR_MSG_MID without code bg.
    gutter_ok = False
    for start, end, st in r.spans:
        chunk = r.plain[start:end]
        if not chunk.strip() or not chunk.strip().isdigit():
            continue
        # Prefer exact gutter tokens like "1  " / " 1  " not code "1"
        if not chunk.endswith(" ") and " " not in chunk:
            # bare digit may be code — skip if has code bg
            if st and "on #1c1c1c" in str(st):
                continue
        style_s = str(st) if st else ""
        if COLOR_MSG_MID in style_s or "585858" in style_s:
            assert "on #1c1c1c" not in style_s and f"on {COLOR_MD_CODE_BG}" not in style_s
            gutter_ok = True
    assert gutter_ok, "expected at least one gutter span without code bg"
    # Code column still has band bg somewhere.
    blob = " ".join(str(st) for *_, st in r.spans if st)
    assert "on #1c1c1c" in blob or COLOR_MD_CODE_BG in blob
    # Indent is present (process column).
    assert r.plain.startswith(" " * MSG_INDENT) or ("\n" + " " * MSG_INDENT) in r.plain



def test_needs_fold_and_expand_footer() -> None:
    from room_tui.llm.message_render import (
        expand_footer_markup,
        needs_fold,
        parse_expand_footer_id,
        sanitize_read_output,
    )

    assert not needs_fold(3)
    assert needs_fold(20)
    foot = expand_footer_markup(expanded=False, total_lines=100, block_id="xb3")
    assert "Expand" in foot
    assert "more lines" in foot
    # Whole-band toggle is double-click (single-click stays free for selection).
    assert "double-click" in foot
    # Strip markup for id parse
    from rich.markup import render as render_markup

    plain = render_markup(foot).plain
    assert parse_expand_footer_id(plain) == "xb3"
    col = expand_footer_markup(expanded=True, total_lines=100, block_id="xb3")
    assert "Collapse" in col
    assert "double-click" in col

    wrapped = (
        "path=/tmp/mailbox.py\n"
        "```javascript\n"
        "console.log(FILE_CONTENT);\n"
        "```\n"
        '"""real file"""\n'
        "def x():\n"
        "    pass\n"
    )
    clean = sanitize_read_output(wrapped)
    assert "FILE_CONTENT" not in clean
    assert "path=" not in clean
    assert "real file" in clean

    # MCP / Pi JSON content envelope with escaped newlines (screenshot case).
    import json as _json

    md = "# 软件需求规格\n\n## 范围\n\n本文档依据 GJB。\n"
    envelope = _json.dumps(
        {"content": [{"type": "text", "text": md}]},
        ensure_ascii=False,
    )
    clean2 = sanitize_read_output(envelope)
    assert clean2.startswith("# 软件需求规格")
    assert "\n## 范围\n" in clean2
    assert "\\n" not in clean2
    assert '{"content"' not in clean2

    # Already a JSON string of the file with literal \\n dumps.
    dumped = md.replace("\n", "\\n")
    clean3 = sanitize_read_output(dumped)
    assert "\n## 范围\n" in clean3 or clean3.count("\n") >= 2


def test_bash_process_title_strips_noise() -> None:
    from room_tui.llm.message_render import bash_process_title

    t = bash_process_title("paper-derived session --help 2>&1")
    assert t.startswith("Run ")
    assert "2>&1" not in t
    assert "paper-derived" in t


def test_bash_tool_header_is_run_title_not_raw_argv() -> None:
    from rich.markup import render as render_markup

    from room_tui.llm.message_render import format_tool_header_markup
    from room_tui.llm.msg_layout import GLYPH_RAIL

    h = format_tool_header_markup(
        "bash", {"command": "paper-derived session list 2>&1"}
    )
    assert "Run " in h
    assert "$" not in h or "Run " in h
    assert "2>&1" not in h
    # Gold $ argv dump should be gone.
    assert "paper-derived session list" in h
    # Finished success row must keep green │ (not blank gutter).
    plain = render_markup(h).plain
    assert plain.startswith(GLYPH_RAIL) or plain[0] == "\u2502"
    assert "◆" in plain
    assert plain.index("◆") > 0  # diamond is not column 0


def test_bash_success_has_stdout_band() -> None:
    """Grok: successful bash keeps header + painted stdout body."""
    from rich.console import Group

    r = render_block(
        AgentBlock(
            kind="tool",
            tool_name="bash",
            tool_args={"command": "ls -la"},
            tool_result="ok\nline2\nline3\n",
        ),
        width=40,
    )
    assert r is not None
    assert isinstance(r, Group)
    plain = ""
    for part in r.renderables:
        plain += getattr(part, "plain", str(part)) + "\n"
    assert "Run" in plain
    assert "line2" in plain


def test_read_success_has_body_preview() -> None:
    """Grok: Read header + truncated body (not title-only)."""
    from rich.console import Group

    r = render_block(
        AgentBlock(
            kind="tool",
            tool_name="read",
            tool_args={"path": "foo.c"},
            tool_result="line1\nline2\nline3\n",
        ),
        width=40,
    )
    assert r is not None
    assert isinstance(r, Group)
    plain = ""
    for part in r.renderables:
        plain += getattr(part, "plain", str(part)) + "\n"
    assert "Read" in plain
    assert "line1" in plain


def test_ctx_execute_file_maps_to_read() -> None:
    """pi-agent ctx_execute_file must render as Grok Read, not generic dump."""
    from rich.console import Group
    from rich.markup import render as render_markup

    from room_tui.llm.message_render import (
        format_tool_header_markup,
        is_read_tool,
    )

    assert is_read_tool("ctx_execute_file")
    assert is_read_tool("execute_file")
    h = format_tool_header_markup(
        "ctx_execute_file",
        {"path": "/tmp/mailbox.py"},
        is_error=False,
    )
    plain = render_markup(h).plain
    assert "Read" in plain
    assert "ctx_execute_file" not in plain

    body = '"""mailbox"""\n\ndef send():\n    pass\n' + "\n".join(
        f"# line {i}" for i in range(20)
    )
    r = render_block(
        AgentBlock(
            kind="tool",
            tool_name="ctx_execute_file",
            tool_args={"path": "mailbox.py"},
            tool_result=body,
        ),
        width=48,
    )
    assert r is not None
    assert isinstance(r, Group)
    joined = ""
    for part in r.renderables:
        joined += getattr(part, "plain", str(part)) + "\n"
    assert "Read" in joined
    assert "send" in joined or "mailbox" in joined
    assert "ctx_execute_file" not in joined


def test_write_status_not_code_snippet() -> None:
    from rich.console import Group

    from room_tui.llm.message_render import looks_like_write_status

    status = "Successfully wrote 4826 bytes to /tmp/mailbox.py"
    assert looks_like_write_status(status)
    r = render_block(
        AgentBlock(
            kind="tool",
            tool_name="Write",
            tool_args={"path": "mailbox.py"},
            tool_result=status,
        ),
        width=48,
    )
    assert r is not None
    assert isinstance(r, Group)
    # Status is plain text, not gutter line "1  Successfully…"
    joined = ""
    for part in r.renderables:
        joined += getattr(part, "plain", str(part)) + "\n"
    assert "Successfully wrote" in joined
    assert not joined.lstrip().startswith("1  Successfully")


def test_edit_keeps_short_snippet() -> None:
    from rich.console import Group

    body = "\n".join(f"code line {i}" for i in range(20))
    r = render_block(
        AgentBlock(
            kind="tool",
            tool_name="edit",
            tool_args={"path": "src/room_tui/llm/msg_layout.py"},
            tool_result=body,
        ),
        width=48,
    )
    assert r is not None
    assert isinstance(r, Group)
    # Snippet is capped — should not contain late lines.
    plain = ""
    for part in r.renderables:
        plain += getattr(part, "plain", str(part))
    assert "Edit" in plain or "msg_layout" in plain
    assert "code line 0" in plain
    assert "code line 19" not in plain
