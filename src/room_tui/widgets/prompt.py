"""CJK-safe multi-line prompt editor (Grok-style).

Custom buffer (not Textual Input) for IME / double-width CJK, with a painted
caret. Shift+Enter inserts ``\\n`` in-buffer; the field grows with line count
up to ``MAX_VISIBLE_LINES``, then scrolls internally.
"""

from __future__ import annotations

from rich.cells import cell_len, get_character_cell_size
from rich.style import Style
from rich.text import Text

from textual import events
from textual.geometry import Offset
from textual.keys import key_to_character
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widget import Widget

# Grok-like: grow with content, hard cap (scroll beyond).
MAX_VISIBLE_LINES = 8


class PromptField(Widget, can_focus=True):
    """Multi-line prompt with blink caret + CJK / IME support."""

    ALLOW_SELECT = True
    MAX_VISIBLE_LINES = MAX_VISIBLE_LINES

    DEFAULT_CSS = """
    PromptField {
        width: 100%;
        height: 1;
        min-height: 1;
        max-height: 8;
        padding: 0;
        background: transparent;
        color: $foreground;
        border: none;
        overflow-y: hidden;
    }
    PromptField:focus {
        background: transparent;
        color: $foreground;
    }
    """

    value: reactive[str] = reactive("", layout=False)
    cursor: reactive[int] = reactive(0, layout=False)
    placeholder: reactive[str] = reactive("")
    _cursor_visible: reactive[bool] = reactive(True, layout=False)

    def __init__(
        self,
        value: str = "",
        placeholder: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._blink_timer: Timer | None = None
        self._vscroll: int = 0  # first visible logical line
        self.value = value
        self.placeholder = placeholder
        self.cursor = len(value)

    @property
    def allow_select(self) -> bool:
        return bool(self.value)

    # Beam caret glyph (1 cell).
    _CARET = "|"
    _CARET_OFF = " "

    # Soft-newline keys (not submit).
    _NEWLINE_KEYS = frozenset(
        {
            "shift+enter",
            "alt+enter",
            "ctrl+enter",
            "ctrl+j",
        }
    )

    # ── geometry ────────────────────────────────────────────────

    def line_count(self) -> int:
        """Number of logical lines in *value* (at least 1 when empty)."""
        if not self.value:
            return 1
        return self.value.count("\n") + 1

    def visible_line_count(self) -> int:
        return max(1, min(self.MAX_VISIBLE_LINES, self.line_count()))

    def _lines(self) -> list[str]:
        """Logical lines (split on ``\\n``; trailing newline → empty last line)."""
        if not self.value:
            return [""]
        return self.value.split("\n")

    def _index_to_row_col(self, index: int) -> tuple[int, int]:
        """Map buffer index → (row, char_col)."""
        index = max(0, min(int(index), len(self.value)))
        row = 0
        col = 0
        for i, ch in enumerate(self.value):
            if i >= index:
                return row, col
            if ch == "\n":
                row += 1
                col = 0
            else:
                col += 1
        return row, col

    def _row_col_to_index(self, row: int, col: int) -> int:
        """Map (row, char_col) → buffer index (col clamped to line length)."""
        lines = self._lines()
        if not lines:
            return 0
        row = max(0, min(int(row), len(lines) - 1))
        line = lines[row]
        col = max(0, min(int(col), len(line)))
        idx = 0
        for r in range(row):
            idx += len(lines[r]) + 1  # + newline
        return idx + col

    def _cell_col_at_index(self, index: int) -> int:
        """Display cells from start of the cursor's line to *index*."""
        row, col = self._index_to_row_col(index)
        line = self._lines()[row] if self._lines() else ""
        return cell_len(line[:col])

    def _index_at_cell_col(self, row: int, cell_col: int) -> int:
        """Index on *row* nearest to display column *cell_col*."""
        lines = self._lines()
        if not lines:
            return 0
        row = max(0, min(row, len(lines) - 1))
        line = lines[row]
        if cell_col <= 0:
            return self._row_col_to_index(row, 0)
        cells = 0
        for i, ch in enumerate(line):
            w = get_character_cell_size(ch)
            if cells + w > cell_col:
                return self._row_col_to_index(row, i)
            cells += w
        return self._row_col_to_index(row, len(line))

    def _sync_height(self) -> None:
        """Grow/shrink widget height to match content (capped)."""
        n = self.visible_line_count()
        try:
            self.styles.height = n
            self.styles.min_height = n
            self.styles.max_height = self.MAX_VISIBLE_LINES
        except Exception:
            pass
        self._ensure_cursor_visible()
        try:
            self.refresh(layout=True)
        except Exception:
            self.refresh()

    def _ensure_cursor_visible(self) -> None:
        row, _ = self._index_to_row_col(self.cursor)
        vis = self.visible_line_count()
        max_scroll = max(0, self.line_count() - vis)
        if row < self._vscroll:
            self._vscroll = row
        elif row >= self._vscroll + vis:
            self._vscroll = row - vis + 1
        self._vscroll = max(0, min(self._vscroll, max_scroll))

    # ── terminal hardware cursor (IME) ──────────────────────────

    @property
    def _cursor_cell(self) -> int:
        """Display column of the beam on the current line."""
        return self._cell_col_at_index(self.cursor)

    @property
    def _visible_scroll_cells(self) -> int:
        """Horizontal scroll for the caret's line.

        When layout has not assigned a real width yet (0/1), never scroll —
        otherwise the whole line is cropped and CJK after paste looks "gone".
        """
        try:
            width = int(self.content_size.width or 0)
        except Exception:
            width = 0
        if width < 4:
            # Prefer region width during early layout / resize thrash.
            try:
                width = int(self.size.width or 0)
            except Exception:
                width = 0
        if width < 4:
            return 0
        row, _ = self._index_to_row_col(self.cursor)
        lines = self._lines()
        line = lines[row] if lines else ""
        # caret may sit after last char → +1 cell for beam
        total = cell_len(line) + 1
        if not self.value and row == 0:
            total = 2 + cell_len(self.placeholder or "")
        if total <= width:
            return 0
        caret = self._cursor_cell
        if caret >= total - 1:
            return max(0, total - width)
        scroll = max(0, caret - width + 2)
        return min(scroll, max(0, total - width))

    @property
    def cursor_screen_offset(self) -> Offset:
        try:
            rx, ry, _rw, rh = self.region
            pad = self.styles.padding
            top = int(pad.top) if pad is not None else 0
            left = int(pad.left) if pad is not None else 0
            width = max(1, self.content_size.width)
        except Exception:
            try:
                x, y, width, _h = self.content_region
                cell = min(self._cursor_cell, max(0, width - 1))
                return Offset(x + cell, y)
            except Exception:
                return Offset(0, 0)

        row, _ = self._index_to_row_col(self.cursor)
        vis_row = row - self._vscroll
        vis_row = max(0, min(vis_row, max(0, rh - 1)))
        cell = self._cursor_cell - self._visible_scroll_cells
        cell = max(0, min(cell, width - 1))
        return Offset(rx + left + cell, ry + top + vis_row)

    def _sync_terminal_cursor(self) -> None:
        if not self.has_focus:
            return
        try:
            self.app.cursor_position = self.cursor_screen_offset
        except Exception:
            pass

    # ── blink ───────────────────────────────────────────────────

    def _toggle_cursor(self) -> None:
        if self.screen.is_active and self.has_focus:
            self._cursor_visible = not self._cursor_visible
        else:
            self._cursor_visible = True

    def _restart_blink(self) -> None:
        self._cursor_visible = True
        timer = getattr(self, "_blink_timer", None)
        if timer is not None:
            timer.reset()

    def _pause_blink(self, *, visible: bool = True) -> None:
        self._cursor_visible = visible
        timer = getattr(self, "_blink_timer", None)
        if timer is not None:
            timer.pause()

    def on_mount(self) -> None:
        self._blink_timer = self.set_interval(0.5, self._toggle_cursor, pause=True)
        self._sync_height()
        if self.has_focus:
            self._blink_timer.resume()
            self.call_after_refresh(self._sync_terminal_cursor)

    def on_focus(self, event: events.Focus) -> None:
        self._restart_blink()
        if self._blink_timer is not None:
            self._blink_timer.resume()
        self.refresh()
        self._sync_terminal_cursor()
        self.call_after_refresh(self._sync_terminal_cursor)

    def on_blur(self, event: events.Blur) -> None:
        self._pause_blink(visible=True)
        self.refresh()

    def watch_cursor(self, _cursor: int) -> None:
        self._ensure_cursor_visible()
        self._restart_blink()
        self._sync_terminal_cursor()
        self.refresh()

    def watch__cursor_visible(self, _v: bool) -> None:
        self.refresh(layout=False)

    def watch_value(self, _value: str) -> None:
        self.cursor = max(0, min(self.cursor, len(self.value)))
        self._sync_height()
        self._sync_terminal_cursor()

    # ── messages ────────────────────────────────────────────────

    class Changed(Message):
        def __init__(self, prompt: "PromptField", value: str) -> None:
            self.prompt = prompt
            self.value = value
            super().__init__()

        @property
        def control(self) -> "PromptField":
            return self.prompt

    class Submitted(Message):
        def __init__(self, prompt: "PromptField", value: str) -> None:
            self.prompt = prompt
            self.value = value
            super().__init__()

        @property
        def control(self) -> "PromptField":
            return self.prompt

    class NewlineRequest(Message):
        """Legacy signal: soft-break was applied (or parent may insert)."""

        def __init__(self, prompt: "PromptField") -> None:
            self.prompt = prompt
            super().__init__()

        @property
        def control(self) -> "PromptField":
            return self.prompt

    class Navigate(Message):
        """Arrow at edge of buffer — parent: slash / history."""

        def __init__(self, prompt: "PromptField", direction: int) -> None:
            self.prompt = prompt
            self.direction = direction  # -1 up, +1 down
            super().__init__()

        @property
        def control(self) -> "PromptField":
            return self.prompt

    # ── public API ──────────────────────────────────────────────

    def clear(self) -> None:
        self.value = ""
        self.cursor = 0
        self._vscroll = 0
        self._emit_changed()

    def set_value(self, text: str, *, cursor_end: bool = True) -> None:
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        self.value = text
        self.cursor = len(text) if cursor_end else min(self.cursor, len(text))
        self._vscroll = 0
        self._emit_changed()

    def insert_text(self, text: str) -> None:
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        self._insert(text)

    def insert_newline(self) -> None:
        """Grok: soft line break at caret (no submit)."""
        self._insert("\n")
        self.post_message(self.NewlineRequest(self))

    # ── key / paste ─────────────────────────────────────────────

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if character is not None and character.isprintable():
            return True
        recovered = key_to_character(key) if key else None
        if recovered is not None and recovered.isprintable():
            return True
        return key in {
            "backspace",
            "delete",
            "left",
            "right",
            "up",
            "down",
            "home",
            "end",
            "enter",
            "shift+enter",
            "alt+enter",
            "ctrl+enter",
            "ctrl+j",
            "ctrl+a",
            "ctrl+e",
            "ctrl+u",
            "ctrl+k",
            "ctrl+w",
        }

    @classmethod
    def _insertable_text(cls, event: events.Key) -> str | None:
        """Resolve printable text from a Key event (CJK/IME safe).

        Prefer ``event.character`` (same as Textual Input). With Kitty disabled
        (Room default), IME commits arrive as UTF-8 with *character* set.

        When character is missing (Kitty edge cases):
        - single-char key → itself (``"中"``, ``"a"``)
        - multi-char pure-ASCII → only if ``key_to_character`` maps to a real
          glyph (``ideographic_full_stop`` → ``。``); never insert protocol
          names (``left_shift``, ``left_option``, ``control``, …)
        - multi-char with non-ASCII → IME commit string
        """
        # 1) Primary — Textual Input path
        try:
            if event.is_printable and event.character:
                ch = event.character
                if ch not in "\r\n\t":
                    return ch
        except Exception:
            pass
        ch = event.character
        if ch and ch.isprintable() and ch not in "\r\n\t":
            return ch

        key = event.key or ""
        if not key:
            return None
        if key == "space":
            return " "
        # Chords never insert raw text
        if "+" in key:
            return None

        # 2) Single-char key id (often equals the glyph)
        if len(key) == 1:
            if key.isprintable() and key not in "\r\n\t":
                return key
            return None

        # 3) Multi-char pure ASCII: protocol name OR unicode name
        if all(ord(c) < 128 for c in key):
            recovered = key_to_character(key)
            if not recovered or not recovered.isprintable():
                return None
            if recovered in "\r\n\t":
                return None
            # Accept only a real glyph (single char, or any non-ASCII)
            if len(recovered) == 1 or any(ord(c) > 127 for c in recovered):
                return recovered
            return None

        # 4) Multi-char with non-ASCII → IME commit
        if (
            key.isprintable()
            and "\r" not in key
            and "\n" not in key
            and "\t" not in key
        ):
            return key
        return None

    def on_key(self, event: events.Key) -> None:
        key = event.key or ""

        # Soft newline — insert in buffer (Grok multi-line).
        if key in self._NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.insert_newline()
            return
        if event.character in ("\n",) and key not in ("enter", "return"):
            event.stop()
            event.prevent_default()
            self.insert_newline()
            return

        if key in ("tab", "ctrl+c", "escape"):
            return

        # ↑/↓: move within multi-line buffer first; edge → Navigate (history/slash).
        if key in ("up", "down"):
            row, col = self._index_to_row_col(self.cursor)
            cell = self._cell_col_at_index(self.cursor)
            if key == "up" and row > 0:
                event.stop()
                event.prevent_default()
                self.cursor = self._index_at_cell_col(row - 1, cell)
                return
            if key == "down" and row < self.line_count() - 1:
                event.stop()
                event.prevent_default()
                self.cursor = self._index_at_cell_col(row + 1, cell)
                return
            event.stop()
            event.prevent_default()
            self.post_message(self.Navigate(self, -1 if key == "up" else 1))
            return

        if key in ("enter", "return"):
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.value))
            return

        if key in ("backspace", "ctrl+h"):
            event.stop()
            event.prevent_default()
            self._backspace()
            return

        if key == "delete":
            event.stop()
            event.prevent_default()
            self._delete()
            return

        if key == "left":
            event.stop()
            event.prevent_default()
            self.cursor = max(0, self.cursor - 1)
            return

        if key == "right":
            event.stop()
            event.prevent_default()
            self.cursor = min(len(self.value), self.cursor + 1)
            return

        if key == "home":
            event.stop()
            event.prevent_default()
            row, _ = self._index_to_row_col(self.cursor)
            self.cursor = self._row_col_to_index(row, 0)
            return

        if key == "end":
            event.stop()
            event.prevent_default()
            row, _ = self._index_to_row_col(self.cursor)
            line = self._lines()[row]
            self.cursor = self._row_col_to_index(row, len(line))
            return

        if key == "ctrl+a":
            event.stop()
            event.prevent_default()
            self.cursor = 0
            return

        if key == "ctrl+e":
            event.stop()
            event.prevent_default()
            self.cursor = len(self.value)
            return

        if key == "ctrl+u":
            event.stop()
            event.prevent_default()
            # Kill from line start to cursor (readline-ish).
            row, col = self._index_to_row_col(self.cursor)
            start = self._row_col_to_index(row, 0)
            self.value = self.value[:start] + self.value[self.cursor :]
            self.cursor = start
            self._emit_changed()
            return

        if key == "ctrl+k":
            event.stop()
            event.prevent_default()
            # Kill from cursor to end of line.
            row, col = self._index_to_row_col(self.cursor)
            line = self._lines()[row]
            end = self._row_col_to_index(row, len(line))
            self.value = self.value[: self.cursor] + self.value[end:]
            self._emit_changed()
            return

        if key == "ctrl+w":
            event.stop()
            event.prevent_default()
            self._delete_word_left()
            return

        ch = self._insertable_text(event)
        if ch is not None:
            event.stop()
            event.prevent_default()
            self._insert(ch)
            return

    @staticmethod
    def _sanitize_paste(text: str) -> str:
        """Keep newlines/tabs; drop other C0 controls that break rendering."""
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        return "".join(
            c
            for c in text
            if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127)
        )

    def on_paste(self, event: events.Paste) -> None:
        if not event.text:
            return
        text = self._sanitize_paste(event.text)
        if not text:
            return
        event.stop()
        event.prevent_default()
        self._insert(text)
        # After a long paste, ensure layout width is applied so h-scroll is sane.
        self.call_after_refresh(self._sync_height)
        self.call_after_refresh(self._sync_terminal_cursor)

    def on_click(self, event: events.Click) -> None:
        """Place caret under click; right-click pastes (Windows console parity)."""
        # Button 3 = right. When mouse tracking is on, the host terminal no
        # longer pastes on right-click — we must do it ourselves.
        try:
            btn = int(getattr(event, "button", 1) or 1)
        except Exception:
            btn = 1
        if btn == 3:
            event.stop()
            event.prevent_default()
            self._paste_from_system_clipboard()
            return
        try:
            offset = event.get_content_offset(self)
        except Exception:
            return
        if offset is None:
            return
        row = self._vscroll + max(0, offset.y)
        row = max(0, min(row, self.line_count() - 1))
        target_cell = max(0, offset.x) + self._hscroll_for_row(row)
        self.cursor = self._index_at_cell_col(row, target_cell)
        self._restart_blink()
        self._sync_terminal_cursor()

    def _paste_from_system_clipboard(self) -> None:
        """Insert OS clipboard text at the caret (right-click / menu paste)."""
        text = ""
        try:
            app = self.app
            if hasattr(app, "read_text_from_clipboard"):
                text = str(app.read_text_from_clipboard() or "")  # type: ignore[attr-defined]
        except Exception:
            text = ""
        text = self._sanitize_paste(text)
        if not text:
            return
        self._insert(text)
        self.call_after_refresh(self._sync_height)
        self.call_after_refresh(self._sync_terminal_cursor)

    def _hscroll_for_row(self, row: int) -> int:
        """Horizontal scroll used when painting *row* (caret line only for now)."""
        crow, _ = self._index_to_row_col(self.cursor)
        if row == crow:
            return self._visible_scroll_cells
        return 0

    # ── mutations ───────────────────────────────────────────────

    def _insert(self, text: str) -> None:
        if not text:
            return
        i = self.cursor
        self.value = self.value[:i] + text + self.value[i:]
        self.cursor = i + len(text)
        self._emit_changed()

    def _backspace(self) -> None:
        if self.cursor <= 0:
            return
        i = self.cursor
        self.value = self.value[: i - 1] + self.value[i:]
        self.cursor = i - 1
        self._emit_changed()

    def _delete(self) -> None:
        i = self.cursor
        if i >= len(self.value):
            return
        self.value = self.value[:i] + self.value[i + 1 :]
        self._emit_changed()

    def _delete_word_left(self) -> None:
        if self.cursor <= 0:
            return
        s = self.value
        i = self.cursor
        while i > 0 and s[i - 1] in " \t":
            i -= 1
        while i > 0 and s[i - 1] not in " \t\n":
            i -= 1
        self.value = s[:i] + s[self.cursor :]
        self.cursor = i
        self._emit_changed()

    def _emit_changed(self) -> None:
        self._ensure_cursor_visible()
        self._restart_blink()
        self._sync_height()
        self._sync_terminal_cursor()
        self.post_message(self.Changed(self, self.value))

    # ── render ──────────────────────────────────────────────────

    def _beam_style(self) -> Style:
        try:
            from room_tui.ui_state import COLOR_BRAND
            from textual.color import Color

            return Style(color=Color.parse(COLOR_BRAND).rich_color)
        except Exception:
            fg = self.styles.color.rich_color if self.styles.color else None
            return Style(color=fg)

    def _block_caret_style(self, base: Style) -> Style:
        return Style(reverse=True)

    def _placeholder_style(self) -> Style:
        try:
            from textual.color import Color

            vars_ = getattr(self.app, "theme_variables", None) or {}
            bg_raw = vars_.get("background") or vars_.get("surface") or "#121212"
            bg = Color.parse(str(bg_raw))
            ink = bg.get_contrast_text(0.2)
            solid = Color(
                int(bg.r + (ink.r - bg.r) * ink.a),
                int(bg.g + (ink.g - bg.g) * ink.a),
                int(bg.b + (ink.b - bg.b) * ink.a),
            )
            return Style(color=solid.rich_color)
        except Exception:
            return Style(color="#1f1f1f")

    def _caret_on(self) -> bool:
        return self._cursor_visible if self.has_focus else True

    def render(self) -> Text:
        """Paint visible lines (vertical window) with caret on the active line."""
        fg = self.styles.color.rich_color if self.styles.color else None
        base = Style(color=fg)
        dim = self._placeholder_style()
        beam = self._beam_style()
        on = self._caret_on()

        if not self.value:
            return self._render_empty(dim, base, on)

        lines = self._lines()
        crow, _ = self._index_to_row_col(self.cursor)
        vis = self.visible_line_count()
        self._ensure_cursor_visible()
        start = self._vscroll
        end = min(len(lines), start + vis)

        try:
            width = max(1, self.content_size.width)
        except Exception:
            width = 80

        out = Text(no_wrap=True, end="")
        for ri in range(start, end):
            if ri > start:
                out.append("\n")
            line = lines[ri]
            if ri == crow:
                # caret on this line
                row_start = self._row_col_to_index(ri, 0)
                local = self.cursor - row_start
                local = max(0, min(local, len(line)))
                left = line[:local]
                right = line[local:]
                hscroll = self._visible_scroll_cells
                painted = self._window_line(
                    left, right, base, beam, on, width, hscroll
                )
                out.append_text(painted)
            else:
                hscroll = 0
                painted = self._window_plain(line, base, width, hscroll)
                out.append_text(painted)
        return out

    def _render_empty(self, dim: Style, base: Style, on: bool) -> Text:
        text = Text(no_wrap=True, end="")
        if on:
            text.append(" ", style=self._block_caret_style(base))
        else:
            text.append(" ", style=base)
        text.append(" ", style=base)
        ph = self.placeholder or ""
        if ph:
            text.append(ph, style=dim)
        return text

    def _window_line(
        self,
        left: str,
        right: str,
        base: Style,
        beam: Style,
        on: bool,
        width: int,
        hscroll: int,
    ) -> Text:
        """One visual line: left + caret + right, horizontally scrolled."""
        chars: list[tuple[str, Style]] = []
        for ch in left:
            chars.append((ch, base))
        chars.append((self._CARET if on else self._CARET_OFF, beam if on else base))
        for ch in right:
            chars.append((ch, base))
        return self._crop_cells(chars, width, hscroll)

    def _window_plain(
        self, line: str, base: Style, width: int, hscroll: int
    ) -> Text:
        chars = [(ch, base) for ch in line] or [(" ", base)]
        return self._crop_cells(chars, width, hscroll)

    @staticmethod
    def _crop_cells(
        chars: list[tuple[str, Style]], width: int, hscroll: int
    ) -> Text:
        cell = 0
        visible: list[tuple[str, Style]] = []
        for ch, st in chars:
            w = get_character_cell_size(ch)
            if cell + w <= hscroll:
                cell += w
                continue
            visible.append((ch, st))
            cell += w
            if cell - hscroll >= width:
                break
        out = Text(no_wrap=True, end="")
        for ch, st in visible:
            out.append(ch, style=st)
        if not visible:
            out.append(" ", style=Style())
        return out
