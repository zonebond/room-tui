"""Grok Build-aligned pin / follow_preserve_scroll semantics.

Mirrors xai-grok-pager ``enable_follow_with_preserve`` +
``follow_scroll_to_bottom`` (scrollback/state/nav.rs):

1. After submit: prompt stays at viewport top while content still fits.
2. When content overflows: clear preserve → track the tail.
3. Explicit tail tracking: always follow max scroll.
"""

from __future__ import annotations


def _pin_follow_y(
    *,
    pin: int,
    content_end: int,
    vh: int,
    preserve: bool,
) -> tuple[float, bool]:
    """Pure port of ShellScreen._pin_follow_scroll_y decision."""
    turn_h = max(0, content_end - pin)
    fits = turn_h <= max(1, vh - 1)
    if preserve:
        if fits:
            return float(pin), True
        # overflow → drop preserve, track tail
        return float(max(pin, content_end - vh)), False
    return float(max(pin, content_end - vh)), False


def test_preserve_keeps_prompt_top_while_fits() -> None:
    y, preserve = _pin_follow_y(pin=10, content_end=20, vh=15, preserve=True)
    assert y == 10.0
    assert preserve is True


def test_overflow_clears_preserve_and_tracks_tail() -> None:
    y, preserve = _pin_follow_y(pin=10, content_end=40, vh=15, preserve=True)
    assert preserve is False
    assert y == float(max(10, 40 - 15))  # 25


def test_without_preserve_always_tail() -> None:
    y, preserve = _pin_follow_y(pin=10, content_end=18, vh=15, preserve=False)
    assert preserve is False
    assert y == float(max(10, 18 - 15))  # 10 (still pin when short)


def test_tall_without_preserve_tracks_bottom() -> None:
    y, preserve = _pin_follow_y(pin=5, content_end=100, vh=20, preserve=False)
    assert preserve is False
    assert y == 80.0
