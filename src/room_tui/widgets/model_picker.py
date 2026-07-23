"""Model switcher modal (``/model``).

Lists models from the active catalog and returns the chosen ``ModelInfo``.
Provider/key setup is **Ctrl+M** / Ctrl+S, not this screen's primary job.

When the catalog is empty, offers first-run 连接模型 instead of a dead end.

Keyboard:
  - Up/Down always move the list highlight (even while filter Input is focused)
  - Typing printable chars focuses the filter for search
  - Enter confirms highlighted row
  - Ctrl+S opens 连接模型
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from room_tui.pi_catalog import ModelInfo, catalog_models


class ModelPickerScreen(ModalScreen[ModelInfo | None]):
    """Filterable list of pi-advertised models."""

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
        Binding("ctrl+c", "cancel", "取消", show=False),
        Binding("ctrl+s", "open_setup", "配置密钥", show=True),
    ]

    CSS = """
    ModelPickerScreen {
        align: center middle;
    }
    #model-picker-panel {
        width: 76;
        max-width: 96;
        height: auto;
        max-height: 30;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    #model-picker-title {
        text-style: bold;
        color: $text;
        margin-bottom: 0;
    }
    #model-picker-status {
        color: $text-muted;
        margin-bottom: 1;
        height: auto;
        min-height: 1;
    }
    #model-picker-filter {
        width: 100%;
        margin-bottom: 1;
    }
    #model-picker-list {
        height: 14;
        min-height: 8;
        max-height: 18;
        border: solid $primary 40%;
        padding: 0 1;
    }
    #model-picker-hint {
        color: $text-muted;
        margin-top: 1;
        height: 1;
    }
    """

    def __init__(
        self,
        *,
        pi_bin: str = "pi",
        current_spec: str = "",
        models: list[ModelInfo] | None = None,
        auto_setup_if_empty: bool = True,
    ) -> None:
        super().__init__()
        self._pi_bin = pi_bin
        self._current = (current_spec or "").strip()
        self._all: list[ModelInfo] = list(models) if models is not None else []
        self._filtered: list[ModelInfo] = []
        self._auto_setup = auto_setup_if_empty
        self._empty = False

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker-panel"):
            yield Static("选择模型  ·  /model", id="model-picker-title")
            yield Static("", id="model-picker-status")
            yield Input(
                placeholder="过滤 provider / model…",
                id="model-picker-filter",
            )
            yield OptionList(id="model-picker-list")
            yield Static(
                "↑↓ 选择  ·  输入过滤  ·  Enter 确认  ·  Ctrl+S 连接  ·  Esc 取消",
                id="model-picker-hint",
            )

    def on_mount(self) -> None:
        if not self._all:
            self._all = catalog_models(pi_bin=self._pi_bin)
        status = self.query_one("#model-picker-status", Static)
        ol = self.query_one("#model-picker-list", OptionList)
        if not self._all:
            self._empty = True
            status.update(
                "新装环境还没有可用模型\n"
                "请先连接服务商与 API 密钥，再回来切换模型"
            )
            ol.clear_options()
            ol.add_option(Option("  →  Ctrl+M / Enter  连接模型（配置密钥）", id="__setup__"))
            ol.add_option(
                Option("  ·  配置完成后用 /model 切换具体模型", id="__hint__", disabled=True)
            )
            try:
                ol.highlighted = 0
            except Exception:
                pass
            ol.focus()
            # Do NOT auto-jump to setup immediately — empty state must be readable.
            # User presses Enter / Ctrl+S to open 连接模型.
            return
        cur = f"  ·  当前 {self._current}" if self._current else ""
        status.update(f"{len(self._all)} 个可用模型{cur}")
        self._apply_filter("")
        # Focus the LIST so ↑↓ work immediately (filter still accepts typing via on_key)
        try:
            ol.focus()
        except Exception:
            try:
                self.query_one("#model-picker-filter", Input).focus()
            except Exception:
                pass

    def action_open_setup(self) -> None:
        """Replace picker with first-run API key setup."""
        from room_tui.widgets.model_setup import ModelSetupScreen

        def _after(result: object) -> None:
            self.dismiss(result if isinstance(result, ModelInfo) else None)

        self.app.push_screen(ModelSetupScreen(pi_bin=self._pi_bin), _after)

    def _list_len(self) -> int:
        if self._empty:
            return 1
        return min(len(self._filtered), 200)

    def _move_highlight(self, delta: int) -> None:
        """Move OptionList highlight; works even when filter Input has focus."""
        ol = self.query_one("#model-picker-list", OptionList)
        n = self._list_len()
        if n <= 0:
            return
        cur = ol.highlighted
        if cur is None:
            cur = 0 if delta >= 0 else n - 1
        else:
            cur = int(cur) + delta
        cur = max(0, min(n - 1, cur))
        try:
            ol.highlighted = cur
        except Exception:
            pass
        # Keep list focused for continuous arrow navigation
        try:
            if self.focused is None or getattr(self.focused, "id", None) != "model-picker-list":
                ol.focus()
        except Exception:
            pass

    def _apply_filter(self, q: str) -> None:
        ql = (q or "").strip().lower()
        if not ql:
            self._filtered = list(self._all)
        else:
            self._filtered = [
                m
                for m in self._all
                if ql in m.spec.lower()
                or ql in m.provider.lower()
                or ql in m.model.lower()
            ]
        ol = self.query_one("#model-picker-list", OptionList)
        ol.clear_options()
        if not self._filtered:
            ol.add_option(Option("（无匹配）", id="__empty__", disabled=True))
            self._filtered = []
            return
        # Align brand column across visible rows
        from room_tui.slash import _model_brand_label

        parts: list[tuple[str, str, object]] = []
        for m in self._filtered[:200]:
            brand = _model_brand_label(m.provider)
            mid = m.model
            if m.provider and mid.startswith(m.provider + "/"):
                mid = mid[len(m.provider) + 1 :]
            parts.append((brand, mid, m))
        brand_w = min(max((len(b) for b, _, _ in parts), default=8), 14)
        brand_w = max(brand_w, 8)

        highlight = 0
        for i, (brand, mid, m) in enumerate(parts):
            is_cur = self._current and (
                m.spec == self._current
                or m.model == self._current
                or self._current.endswith("/" + mid)
            )
            tag = f"[{brand}]"
            pad = " " * max(1, brand_w + 2 - len(tag) + 1)
            cur = "  ●" if is_cur else ""
            # OptionList uses plain text (no rich [brand] stripping issue for display
            # if we avoid markup — plain brackets are fine in Option label)
            ol.add_option(Option(f"  {tag}{pad}{mid}{cur}", id=f"m{i}"))
            if is_cur:
                highlight = i
        try:
            ol.highlighted = highlight
        except Exception:
            pass

    @on(Input.Changed, "#model-picker-filter")
    def _on_filter(self, event: Input.Changed) -> None:
        if self._empty:
            return
        self._apply_filter(event.value)

    @on(Input.Submitted, "#model-picker-filter")
    def _on_filter_submit(self, event: Input.Submitted) -> None:
        self._confirm_highlighted()

    @on(OptionList.OptionSelected, "#model-picker-list")
    def _on_selected(self, event: OptionList.OptionSelected) -> None:
        # OptionSelected may use option_id; prefer index into _filtered
        self._confirm_index(event.option_index)

    def _confirm_highlighted(self) -> None:
        ol = self.query_one("#model-picker-list", OptionList)
        idx = ol.highlighted
        if idx is None:
            if self._list_len() > 0:
                idx = 0
            else:
                return
        self._confirm_index(int(idx))

    def _confirm_index(self, idx: int) -> None:
        if self._empty:
            self.action_open_setup()
            return
        if idx < 0 or idx >= len(self._filtered):
            return
        self.dismiss(self._filtered[idx])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        key = event.key

        # Arrow keys always navigate the model list (even with filter focused)
        if key in ("up", "down", "pageup", "pagedown", "home", "end"):
            event.stop()
            event.prevent_default()
            if key == "up":
                self._move_highlight(-1)
            elif key == "down":
                self._move_highlight(1)
            elif key == "pageup":
                self._move_highlight(-8)
            elif key == "pagedown":
                self._move_highlight(8)
            elif key == "home":
                ol = self.query_one("#model-picker-list", OptionList)
                try:
                    ol.highlighted = 0
                    ol.focus()
                except Exception:
                    pass
            elif key == "end":
                ol = self.query_one("#model-picker-list", OptionList)
                n = self._list_len()
                if n > 0:
                    try:
                        ol.highlighted = n - 1
                        ol.focus()
                    except Exception:
                        pass
            return

        if key in ("enter", "return"):
            try:
                focused = self.focused
            except Exception:
                focused = None
            fid = getattr(focused, "id", None) if focused is not None else None
            # Enter on filter or list → confirm
            if fid in ("model-picker-list", "model-picker-filter", None):
                event.stop()
                if self._empty:
                    self.action_open_setup()
                else:
                    self._confirm_highlighted()
            return

        # Printable typing while list focused → jump to filter and type there
        char = getattr(event, "character", None)
        if char and char.isprintable() and not event.character.isspace() or (
            char and char == " "
        ):
            try:
                focused = self.focused
            except Exception:
                focused = None
            if focused is not None and getattr(focused, "id", None) == "model-picker-list":
                filt = self.query_one("#model-picker-filter", Input)
                filt.focus()
                # Let Input receive this key: don't stop — but focus change may drop it.
                # Append character ourselves for reliability.
                if char and char.isprintable():
                    event.stop()
                    event.prevent_default()
                    filt.value = (filt.value or "") + char
                    self._apply_filter(filt.value)
