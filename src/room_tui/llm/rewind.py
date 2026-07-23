"""Grok-like conversation rewind helpers (UI history + Pi agent session).

Selecting a user prompt rewinds to the state **before** that prompt was
entered: discard the prompt and everything after it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def preview_prompt(text: str, max_cells: int = 64) -> str:
    """Single-line preview for the rewind picker."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = " ".join(t.split())
    if len(t) <= max_cells:
        return t
    return t[: max(1, max_cells - 1)] + "…"


def format_rewind_dropdown(
    items: list[dict[str, Any]],
    *,
    selected: int = 0,
    max_rows: int = 10,
    width: int = 72,
) -> tuple[str, int]:
    """Render rewind picker markup (newest first). Returns (markup, row_count)."""
    if not items:
        return "[dim]  (no prompts to rewind)[/dim]", 1

    # Newest first for quick access to recent mistakes (Grok-like).
    ordered = list(reversed(items))
    n = len(ordered)
    sel = max(0, min(int(selected), n - 1))

    # Window around selection.
    if n <= max_rows:
        start, end = 0, n
    else:
        half = max_rows // 2
        start = max(0, sel - half)
        end = min(n, start + max_rows)
        start = max(0, end - max_rows)

    header = (
        f"[bold]Rewind[/bold]  [dim]·[/dim]  "
        f"[dim]↑↓ 选择  Enter 确认回退  Esc 取消[/dim]"
    )
    lines = [header]
    for i in range(start, end):
        it = ordered[i]
        text = preview_prompt(str(it.get("text") or ""), max_cells=max(20, width - 10))
        # Display index among all user prompts (1 = oldest, n = newest).
        # In reversed list, visual # = n - i.
        num = n - i
        safe = text.replace("[", "\\[")
        if i == sel:
            lines.append(
                f"[reverse bold]▸ {num:>2}. {safe}[/reverse bold]"
            )
        else:
            lines.append(f"  [dim]{num:>2}.[/dim] {safe}")

    if start > 0 or end < n:
        lines.append(f"[dim]  … {n} prompts · showing {start + 1}–{end}[/dim]")

    return "\n".join(lines), len(lines)


def resolve_rewind_selection(
    items: list[dict[str, Any]], selected: int
) -> dict[str, Any] | None:
    """Map picker selection (newest-first index) back to a rewind point."""
    if not items:
        return None
    ordered = list(reversed(items))
    sel = max(0, min(int(selected), len(ordered) - 1))
    return ordered[sel]


def truncate_pi_session_before_user(
    session_dir: Path,
    *,
    keep_user_count: int,
    session_id: str = "room-agent",
) -> bool:
    """Truncate Pi agent JSONL so only the first *keep_user_count* user turns remain.

    *keep_user_count* is the number of user messages to **keep** (0 = wipe all
    messages, leave session headers). Returns True if a file was modified.
    """
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return False

    candidates = sorted(
        list(session_dir.glob(f"*{session_id}*.jsonl"))
        or list(session_dir.glob("*.jsonl")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return False

    path = candidates[0]
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    keep_n = max(0, int(keep_user_count))
    out: list[str] = []
    user_seen = 0
    truncated = False

    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except json.JSONDecodeError:
            if not truncated:
                out.append(line)
            continue
        if not isinstance(row, dict):
            if not truncated:
                out.append(line)
            continue

        if row.get("type") == "message":
            msg = row.get("message")
            role = ""
            if isinstance(msg, dict):
                role = str(msg.get("role") or "")
            if role == "user":
                if user_seen >= keep_n:
                    truncated = True
                    break
                user_seen += 1
            elif truncated:
                break
            # assistant / toolResult after a kept user: keep while not past cut
            out.append(line)
            continue

        # Session headers / meta — keep only before first truncation.
        if not truncated:
            out.append(line)

    if not truncated and user_seen <= keep_n and len(out) == len(
        [ln for ln in raw_lines if ln.strip()]
    ):
        # Nothing to cut (already short enough).
        return False

    try:
        path.write_text(
            ("\n".join(out) + ("\n" if out else "")),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True
