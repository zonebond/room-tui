"""Smooth trackpad / mouse-wheel scrolling (Grok-like coast).

Textual defaults ``scroll_sensitivity_y = 2`` and its animated ``_scroll_to``
force-stops every tick (snappy jumps). Room wants:

  • Exactly **1 row** per user-perceived notch / trackpad unit
  • Ease-out without force-stop (retarget mid-flight)
  • Deduplicate multi-path input: many terminals emit **both** SGR
    ``MouseScroll*`` **and** key ``ScrollUp/Down`` (or 2× mouse events)
    for one physical gesture — that previously stacked to **2 lines**
"""

from __future__ import annotations

from time import monotonic
from typing import Any

from textual import events
from textual.actions import SkipAction
from textual.containers import VerticalScroll
from textual.geometry import clamp
from textual.message import Message
from textual.widgets import ListView, RichLog

# Exactly one terminal row per *accepted* vertical step.
_WHEEL_STEP_Y = 1.0
_WHEEL_STEP_X = 3.0
# Ease-out duration for a single-row coast.
_WHEEL_DURATION = 0.16
_WHEEL_EASING = "out_cubic"
# Min time between accepted 1-line steps (same direction).
# Absorbs: double CSI per notch, and MouseScroll + Keys.Scroll* pairs
# that arrive within a few ms. Continuous trackpad (~16ms) still passes
# every other event ≈ smooth 1-line steps without stacking to 2.
_WHEEL_MIN_INTERVAL_S = 0.02


class SmoothScrollMixin:
    """Wheel / trackpad: 1-line steps, multi-path dedup, ease-out coast."""

    class UserScrolled(Message):
        """User-driven vertical scroll (wheel/trackpad), not programmatic pin."""

        def __init__(self, sender: "SmoothScrollMixin", y: float, max_y: float) -> None:
            self.scroller = sender
            self.y = y
            self.max_y = max_y
            super().__init__()

        @property
        def control(self) -> "SmoothScrollMixin":
            return self.scroller

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_wheel_accept_mono: float = 0.0
        self._last_wheel_direction: float = 0.0

    @property
    def allow_vertical_scroll(self) -> bool:
        """Permit wheel even when the scrollbar is size-0 / invisible.

        Textual's default requires ``show_vertical_scrollbar``; we only need
        overflow content (``max_scroll_y > 0``) for trackpad scrolling.
        """
        try:
            if self._check_disabled():
                return False
        except Exception:
            pass
        if not self.is_scrollable:
            return False
        try:
            if self.max_scroll_y > 0:
                return True
        except Exception:
            pass
        return bool(getattr(self, "show_vertical_scrollbar", False))

    def _wheel_dedup_blocked(self, direction: float) -> bool:
        """True if same-direction step is too soon after the last accept."""
        now = monotonic()
        last = float(getattr(self, "_last_wheel_accept_mono", 0.0) or 0.0)
        last_dir = float(getattr(self, "_last_wheel_direction", 0.0) or 0.0)
        if last <= 0.0:
            return False
        if (now - last) >= _WHEEL_MIN_INTERVAL_S:
            return False
        # Same direction within the window → drop (mouse×2 / mouse+key).
        # Opposite direction always accepted (user reversed mid-gesture).
        return (direction > 0 and last_dir > 0) or (direction < 0 and last_dir < 0)

    def _mark_wheel_accepted(self, direction: float) -> None:
        self._last_wheel_accept_mono = monotonic()
        self._last_wheel_direction = 1.0 if direction > 0 else -1.0

    def _nudge_scroll_y(self, delta: float) -> bool:
        """Move by exactly one row (sign of *delta*) with ease-out animation."""
        if not self.allow_vertical_scroll:
            return False
        try:
            self.release_anchor()
        except Exception:
            pass
        max_y = float(self.max_scroll_y)
        direction = 1.0 if float(delta) > 0 else -1.0
        # One whole row from current target — never ±2 from sensitivity.
        base = float(self.scroll_target_y)
        target = clamp(float(int(round(base))) + direction, 0.0, max_y)
        if abs(target - float(self.scroll_target_y)) < 1e-6 and abs(
            target - float(self.scroll_y)
        ) < 1e-3:
            return False
        self.scroll_target_y = target
        # Animate from *current* scroll_y — do NOT force_stop first.
        self.animate(
            "scroll_y",
            target,
            duration=_WHEEL_DURATION,
            easing=_WHEEL_EASING,
            level="basic",
        )
        try:
            self.post_message(self.UserScrolled(self, target, max_y))
        except Exception:
            pass
        return True

    def _nudge_scroll_x(self, delta: float) -> bool:
        if not self.allow_horizontal_scroll:
            return False
        try:
            self.release_anchor()
        except Exception:
            pass
        max_x = float(self.max_scroll_x)
        target = clamp(self.scroll_target_x + delta, 0.0, max_x)
        if abs(target - self.scroll_target_x) < 1e-6 and abs(
            target - self.scroll_x
        ) < 1e-3:
            return False
        self.scroll_target_x = target
        self.animate(
            "scroll_x",
            target,
            duration=_WHEEL_DURATION,
            easing=_WHEEL_EASING,
            level="basic",
        )
        return True

    def _wheel_delta_y(self) -> float:
        """Vertical step — always 1 row (ignore app.scroll_sensitivity_y)."""
        return _WHEEL_STEP_Y

    def _wheel_delta_x(self) -> float:
        try:
            return float(self.app.scroll_sensitivity_x)
        except Exception:
            return _WHEEL_STEP_X

    def _accept_vertical_step(self, direction: float) -> bool:
        """Single entry for mouse / key / action / pointer — 1 line + dedup.

        Returns True when the input was consumed (including deduped duplicates)
        so callers can stop the event and prevent Textual's default ×2 path.
        """
        direction = 1.0 if float(direction) > 0 else -1.0
        if self._wheel_dedup_blocked(direction):
            return True
        moved = self._nudge_scroll_y(direction * _WHEEL_STEP_Y)
        if moved:
            self._mark_wheel_accepted(direction)
        else:
            # At end of range: still mark so a paired key event cannot add a
            # phantom step after a no-op mouse event at the boundary.
            self._mark_wheel_accepted(direction)
        return True

    # --- All Textual vertical entry points → one-line smooth path ----------

    def _scroll_down_for_pointer(self, **kwargs: Any) -> bool:
        self._accept_vertical_step(+1.0)
        return True

    def _scroll_up_for_pointer(self, **kwargs: Any) -> bool:
        self._accept_vertical_step(-1.0)
        return True

    def scroll_down(self, **kwargs: Any) -> None:
        """Override Widget.scroll_down (+1 via force-stop) with smooth 1-line."""
        self._accept_vertical_step(+1.0)

    def scroll_up(self, **kwargs: Any) -> None:
        """Override Widget.scroll_up with smooth 1-line."""
        self._accept_vertical_step(-1.0)

    def action_scroll_down(self) -> None:
        """Keys.ScrollDown / action — same path as mouse (deduped)."""
        if not self.allow_vertical_scroll:
            raise SkipAction()
        self._accept_vertical_step(+1.0)

    def action_scroll_up(self) -> None:
        """Keys.ScrollUp / action — same path as mouse (deduped)."""
        if not self.allow_vertical_scroll:
            raise SkipAction()
        self._accept_vertical_step(-1.0)

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if event.ctrl or event.shift:
            if self._nudge_scroll_x(+self._wheel_delta_x()):
                event.stop()
            return
        self._accept_vertical_step(+1.0)
        event.stop()
        try:
            event.prevent_default()
        except Exception:
            pass

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if event.ctrl or event.shift:
            if self._nudge_scroll_x(-self._wheel_delta_x()):
                event.stop()
            return
        self._accept_vertical_step(-1.0)
        event.stop()
        try:
            event.prevent_default()
        except Exception:
            pass

    def _on_mouse_scroll_right(self, event: events.MouseScrollRight) -> None:
        if self._nudge_scroll_x(+self._wheel_delta_x()):
            event.stop()

    def _on_mouse_scroll_left(self, event: events.MouseScrollLeft) -> None:
        if self._nudge_scroll_x(-self._wheel_delta_x()):
            event.stop()


class SmoothVerticalScroll(SmoothScrollMixin, VerticalScroll):
    """``VerticalScroll`` with trackpad-friendly coasting scroll."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Ensure the pane itself can take wheel focus under the pointer.
        kwargs.setdefault("can_focus", True)
        super().__init__(*args, **kwargs)


class SmoothRichLog(SmoothScrollMixin, RichLog):
    """``RichLog`` with trackpad coast + Grok-style drag-select → copy.

    Stock ``RichLog`` never stamps ``Strip.apply_offsets``, so Textual cannot
    map pointer coords to content lines and selection is a no-op. We mirror
    ``textual.widgets.Log``: content offsets on each paint, ``get_selection``
    over joined plain lines, and refresh on selection change.

    Double-click: Textual's ``Widget._on_click`` does ``text_select_all`` on
    ``chain == 2``. Foldable bands must expand/collapse instead — we intercept
    before SELECT_ALL when the screen reports a hit on an expandable band.
    """

    ALLOW_SELECT = True

    async def _on_click(self, event: events.Click) -> None:
        """Prefer fold expand on double-click; else Textual select-all."""
        chain = int(getattr(event, "chain", 1) or 1)
        if event.widget is self and chain >= 2:
            try:
                screen = self.screen
                activate = getattr(screen, "_activate_expand_at_event", None)
                if callable(activate) and activate(event):
                    # Handled as Expand/Collapse — do not SELECT_ALL the log.
                    try:
                        event.stop()
                        event.prevent_default()
                    except Exception:
                        pass
                    return
            except Exception:
                pass
        # Default: chain==2 select-all, chain==3 container select-all, then broker.
        await super()._on_click(event)

    def _plain_lines(self) -> list[str]:
        """Plain text of every log strip (one string per visual line)."""
        plains: list[str] = []
        try:
            lines = self.lines
        except Exception:
            return plains
        for line in lines:
            plain = getattr(line, "text", None)
            if isinstance(plain, str):
                plains.append(plain)
                continue
            try:
                plains.append("".join(getattr(s, "text", "") or "" for s in line))
            except Exception:
                plains.append(str(line))
        return plains

    def get_selection(self, selection: Any) -> tuple[str, str] | None:
        """Extract plain text under a screen selection (Log-compatible)."""
        try:
            from textual.selection import Selection
        except Exception:
            Selection = None  # type: ignore[misc, assignment]
        plains = self._plain_lines()
        if not plains:
            return None
        blob = "\n".join(plains)
        if not blob:
            return None
        try:
            if Selection is not None and isinstance(selection, Selection):
                return selection.extract(blob), "\n"
        except Exception:
            pass
        # SELECT_ALL / unknown shape — still return something useful.
        return blob, "\n"

    def selection_updated(self, selection: Any) -> None:
        """Repaint so selection highlight tracks drag (and clears on release)."""
        try:
            self._line_cache.clear()
        except Exception:
            pass
        self.refresh()

    def render_line(self, y: int) -> Any:
        """Paint one viewport row with selection offsets (required for copy).

        Textual's compositor reads ``style.meta['offset']`` to resolve content
        coordinates. Without ``apply_offsets``, drag-select never binds to lines.
        """
        scroll_x, scroll_y = self.scroll_offset
        abs_y = int(scroll_y) + int(y)
        width = self.scrollable_content_region.width
        line = self._render_line(abs_y, int(scroll_x), width)
        strip = line.apply_style(self.rich_style)

        # Optional highlight while dragging (same component class as Screen).
        try:
            selection = self.text_selection
        except Exception:
            selection = None
        if selection is not None:
            try:
                span = selection.get_span(abs_y)
            except Exception:
                span = None
            if span is not None:
                start, end = span
                plain = getattr(strip, "text", None)
                if not isinstance(plain, str):
                    try:
                        plain = "".join(getattr(s, "text", "") or "" for s in strip)
                    except Exception:
                        plain = ""
                if end == -1:
                    end = len(plain)
                start = max(0, int(start))
                end = max(start, int(end))
                if end > start and plain:
                    sel_style = self._selection_paint_style()
                    if sel_style is not None:
                        strip = self._stylize_strip_range(strip, start, end, sel_style)

        # Stamp absolute content coords (x starts at horizontal scroll, like Log).
        return strip.apply_offsets(int(scroll_x), abs_y)

    def _selection_paint_style(self) -> Any | None:
        """Rich style for selection highlight that keeps glyphs readable.

        Textual's default ``screen--selection`` uses a *transparent* foreground.
        ``get_component_rich_style`` folds that into the same RGB as the
        background, so selected text becomes an opaque block with no letters.
        Prefer bg-only (preserve original colors) when fg collapses onto bg.
        """
        from rich.style import Style as RichStyle

        try:
            sel = self.screen.get_component_rich_style("screen--selection")
        except Exception:
            return None
        if sel is None:
            return None
        color = getattr(sel, "color", None)
        bgcolor = getattr(sel, "bgcolor", None)
        if color is not None and bgcolor is not None:
            try:
                same = color == bgcolor or (
                    getattr(color, "triplet", None) is not None
                    and color.triplet == bgcolor.triplet
                )
            except Exception:
                same = str(color) == str(bgcolor)
            if same:
                # Keep original segment colors; only wash the background.
                return RichStyle(bgcolor=bgcolor)
        return sel

    @staticmethod
    def _stylize_strip_range(
        strip: Any, start: int, end: int, style: Any
    ) -> Any:
        """Apply *style* to character range [start, end) of a Strip."""
        from rich.segment import Segment
        from textual.strip import Strip

        out: list[Any] = []
        pos = 0
        for seg in strip:
            text = getattr(seg, "text", "") or ""
            seg_style = getattr(seg, "style", None)
            control = getattr(seg, "control", None)
            n = len(text)
            seg_end = pos + n
            if n == 0 or seg_end <= start or pos >= end:
                out.append(seg)
                pos = seg_end
                continue
            # Leading unselected
            if pos < start:
                cut = start - pos
                out.append(Segment(text[:cut], seg_style, control))
                text = text[cut:]
                pos = start
                n = len(text)
                seg_end = pos + n
            # Selected middle
            mid_end = min(seg_end, end)
            mid_len = mid_end - pos
            if mid_len > 0:
                mid = text[:mid_len]
                merged = (seg_style + style) if seg_style else style
                out.append(Segment(mid, merged, control))
                text = text[mid_len:]
                pos = mid_end
            # Trailing unselected
            if text:
                out.append(Segment(text, seg_style, control))
                pos = seg_end
        try:
            cell_len = strip.cell_length
        except Exception:
            cell_len = None
        return Strip(out, cell_len)


class SmoothListView(SmoothScrollMixin, ListView):
    """``ListView`` with trackpad-friendly coasting scroll."""
