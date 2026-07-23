"""Composer helpers + thin wrapper around compact Input.

Default height is always 1 (Input compact=True).
Multi-line: committed lines stored; height of wrapper grows via styles.
"""

from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static

MAX_LINES = 8


class Composer(Vertical):
    """1-row compact Input by default; Shift+Enter grows with preview lines."""

    DEFAULT_CSS = """
    Composer {
        width: 100%;
        height: auto;
        min-height: 2;
        max-height: 9;
        layout: vertical;
        background: $panel;
        border-top: solid $primary 40%;
        padding: 0;
    }
    #composer-preview {
        width: 100%;
        height: 0;
        padding: 0 1;
        color: $foreground;
        background: $panel;
    }
    #composer-input {
        width: 100%;
        height: 1;
        min-height: 1;
        max-height: 1;
        border: none !important;
        padding: 0 1;
        background: $panel;
        color: $foreground;
        margin: 0;
    }
    #composer-input:focus {
        background: $surface;
        color: $foreground;
    }
    #composer-input > .input--placeholder {
        color: $text-disabled;
    }
    #composer-input > .input--cursor {
        background: $accent;
        color: $background;
        text-style: none;
    }
    """

    class Submitted(Message):
        def __init__(self, composer: "Composer", text: str) -> None:
            self.composer = composer
            self.text = text
            super().__init__()

        @property
        def control(self) -> "Composer":
            return self.composer

    class Changed(Message):
        def __init__(self, composer: "Composer", text: str) -> None:
            self.composer = composer
            self.text = text
            super().__init__()

        @property
        def control(self) -> "Composer":
            return self.composer

    def __init__(self, *, placeholder: str = "", id: str | None = None) -> None:
        super().__init__(id=id)
        self._placeholder = placeholder
        self._committed: list[str] = []
        self.can_focus = False

    def compose(self) -> ComposeResult:
        yield Static("", id="composer-preview")
        yield Input(
            placeholder=self._placeholder,
            id="composer-input",
            compact=True,
        )

    def on_mount(self) -> None:
        prev = self.query_one("#composer-preview", Static)
        prev.styles.display = "none"
        prev.styles.height = 0
        self.query_one("#composer-input", Input).focus()

    @property
    def text(self) -> str:
        cur = self.query_one("#composer-input", Input).value or ""
        if self._committed:
            return "\n".join([*self._committed, cur])
        return cur

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, v: str) -> None:
        self.load_text(v or "")

    def load_text(self, v: str) -> None:
        parts = (v or "").split("\n")
        if not parts:
            self._committed = []
            self.query_one("#composer-input", Input).value = ""
        else:
            self._committed = parts[:-1][- (MAX_LINES - 1) :]
            self.query_one("#composer-input", Input).value = parts[-1]
        self._refresh_preview()
        self._sync_height()

    def clear(self) -> None:
        self._committed = []
        try:
            self.query_one("#composer-input", Input).value = ""
        except Exception:
            pass
        self._refresh_preview()
        self._sync_height()

    def focus(self, *args, **kwargs):  # type: ignore[override]
        return self.query_one("#composer-input", Input).focus(*args, **kwargs)

    @property
    def has_focus(self) -> bool:
        try:
            return self.query_one("#composer-input", Input).has_focus
        except Exception:
            return False

    def _refresh_preview(self) -> None:
        prev = self.query_one("#composer-preview", Static)
        n = len(self._committed)
        if n:
            prev.update("\n".join(self._committed))
            prev.styles.display = "block"
            prev.styles.height = n
            prev.styles.min_height = n
        else:
            prev.update("")
            prev.styles.display = "none"
            prev.styles.height = 0
            prev.styles.min_height = 0

    def _visual_line_count(self) -> int:
        return max(1, min(MAX_LINES, len(self._committed) + 1))

    def _sync_height(self) -> None:
        lines = self._visual_line_count()
        self.styles.height = lines
        self.styles.min_height = lines
        self.styles.max_height = MAX_LINES
        try:
            self.refresh(layout=True)
        except Exception:
            pass

    def action_submit_prompt(self) -> None:
        self.post_message(self.Submitted(self, self.text.rstrip("\n")))

    def action_insert_newline(self) -> None:
        if len(self._committed) + 1 >= MAX_LINES:
            return
        cur = self.query_one("#composer-input", Input)
        self._committed.append(cur.value)
        cur.value = ""
        self._refresh_preview()
        self._sync_height()
        self.post_message(self.Changed(self, self.text))

    @on(Input.Changed, "#composer-input")
    def _on_line_changed(self, _event: Input.Changed) -> None:
        self.post_message(self.Changed(self, self.text))

    @on(Input.Submitted, "#composer-input")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit_prompt()

    def on_key(self, event: events.Key) -> None:
        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            self.action_insert_newline()
