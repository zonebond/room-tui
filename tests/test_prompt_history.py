"""Grok-like ↑/↓ prompt history navigation."""

from __future__ import annotations

from room_tui.widgets.prompt_history import PromptHistoryNav


def test_push_dedupes_consecutive() -> None:
    h = PromptHistoryNav()
    h.push("hello")
    h.push("hello")
    h.push("world")
    h.push("world")
    assert h.entries == ["hello", "world"]


def test_push_skips_blank() -> None:
    h = PromptHistoryNav()
    h.push("")
    h.push("  \n  ")
    h.push("ok")
    assert h.entries == ["ok"]


def test_down_never_opens() -> None:
    h = PromptHistoryNav()
    h.push("a")
    handled, text = h.navigate(+1, composer_empty=True, draft_text="")
    assert handled is False
    assert text is None
    assert not h.browsing


def test_up_on_non_empty_does_not_open() -> None:
    h = PromptHistoryNav()
    h.push("a")
    handled, text = h.navigate(-1, composer_empty=False, draft_text="draft")
    assert handled is False
    assert text is None


def test_empty_up_opens_newest() -> None:
    h = PromptHistoryNav()
    h.push("one")
    h.push("two")
    handled, text = h.navigate(-1, composer_empty=True, draft_text="")
    assert handled is True
    assert text == "two"
    assert h.browsing
    assert h.index == 1


def test_browse_up_down_and_close() -> None:
    h = PromptHistoryNav()
    h.push("one")
    h.push("two")
    h.push("three")

    ok, t = h.navigate(-1, composer_empty=True, draft_text="")
    assert (ok, t) == (True, "three")

    ok, t = h.navigate(-1, composer_empty=False, draft_text="three")
    assert (ok, t) == (True, "two")

    ok, t = h.navigate(-1, composer_empty=False, draft_text="two")
    assert (ok, t) == (True, "one")

    # Clamp at oldest
    ok, t = h.navigate(-1, composer_empty=False, draft_text="one")
    assert (ok, t) == (True, "one")

    ok, t = h.navigate(+1, composer_empty=False, draft_text="one")
    assert (ok, t) == (True, "two")

    ok, t = h.navigate(+1, composer_empty=False, draft_text="two")
    assert (ok, t) == (True, "three")

    # ↓ at newest closes → restore draft
    ok, t = h.navigate(+1, composer_empty=False, draft_text="three")
    assert ok is True
    assert t == ""
    assert not h.browsing


def test_close_restores_saved_draft() -> None:
    """If we ever opened with a draft, ↓ at newest restores it.

    (Open only happens when empty in the UI; draft is usually "".)
    """
    h = PromptHistoryNav()
    h.push("hist")
    # Force open path with a non-empty draft (API allows it).
    h.draft = "kept"
    h.index = 0
    ok, t = h.navigate(+1, composer_empty=False, draft_text="hist")
    assert ok is True
    assert t == "kept"
    assert not h.browsing


def test_seed_order_and_cap() -> None:
    h = PromptHistoryNav(max_entries=3)
    h.seed(["a", "b", "c", "d"])
    assert h.entries == ["b", "c", "d"]
    assert not h.browsing


def test_push_resets_browse() -> None:
    h = PromptHistoryNav()
    h.push("a")
    h.navigate(-1, composer_empty=True, draft_text="")
    assert h.browsing
    h.push("b")
    assert not h.browsing
    assert h.entries == ["a", "b"]


def test_multiline_entry_preserved() -> None:
    h = PromptHistoryNav()
    h.push("line1\nline2")
    ok, t = h.navigate(-1, composer_empty=True, draft_text="")
    assert ok is True
    assert t == "line1\nline2"
