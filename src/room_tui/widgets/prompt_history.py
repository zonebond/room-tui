"""Grok-like prompt history navigation.

Behaviour (matches Grok Build keyboard docs):

- ``↑`` on an **empty** prompt opens history at the newest entry (fills input).
- ``↑`` / ``↓`` step through entries; each lands in the input.
- ``↓`` at the newest entry closes history and restores the draft (usually empty).
- ``↓`` never opens history.
- Typing edits the recalled prompt in place (store is not mutated until submit).
- Submit / clear push the text into history (dedupe consecutive duplicates).
"""

from __future__ import annotations


def _normalize_entry(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    # Keep internal newlines; drop outer blank lines / edge whitespace.
    return t.strip("\n").strip()


class PromptHistoryNav:
    """Session prompt history with index-based ↑/↓ browse."""

    def __init__(self, max_entries: int = 200) -> None:
        self.entries: list[str] = []
        self.index: int | None = None  # None = not browsing
        self.draft: str = ""
        self.max_entries = max(1, max_entries)

    @property
    def browsing(self) -> bool:
        return self.index is not None

    def push(self, text: str) -> None:
        """Append a submitted / cleared prompt. Drops consecutive duplicates."""
        t = _normalize_entry(text)
        if not t:
            return
        if self.entries and self.entries[-1] == t:
            self.reset_browse()
            return
        self.entries.append(t)
        if len(self.entries) > self.max_entries:
            del self.entries[: len(self.entries) - self.max_entries]
        self.reset_browse()

    def seed(self, texts: list[str]) -> None:
        """Bulk-load older → newer (e.g. from chat-history user rows)."""
        for t in texts:
            self.push(t)
        self.reset_browse()

    def reset_browse(self) -> None:
        self.index = None
        self.draft = ""

    def navigate(
        self,
        direction: int,
        *,
        composer_empty: bool,
        draft_text: str = "",
    ) -> tuple[bool, str | None]:
        """Step history.

        Args:
            direction: ``-1`` up (older), ``+1`` down (newer).
            composer_empty: whether the composer is empty (open gate for ↑).
            draft_text: current composer text when opening (saved for restore).

        Returns:
            ``(handled, new_text)``. ``new_text`` is the string to put in the
            composer when handled; may be ``""`` when closing. ``None`` means
            the caller should leave the composer unchanged (only when not
            handled).
        """
        if not self.entries:
            return False, None

        if self.index is None:
            # ↓ never opens; ↑ only on empty prompt.
            if direction >= 0 or not composer_empty:
                return False, None
            self.draft = draft_text
            self.index = len(self.entries) - 1
            return True, self.entries[self.index]

        if direction < 0:
            # Older; clamp at oldest.
            if self.index > 0:
                self.index -= 1
            return True, self.entries[self.index]

        # Newer
        if self.index < len(self.entries) - 1:
            self.index += 1
            return True, self.entries[self.index]

        # At newest + ↓ → close, restore draft.
        draft = self.draft
        self.reset_browse()
        return True, draft
