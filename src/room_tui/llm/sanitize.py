"""Strip model tool-protocol leakage (DSML / function_calls) from display text."""

from __future__ import annotations

import re

# Pi / DeepSeek / similar tool envelopes that sometimes leak as plain text
# when the runner is started with --no-tools.
_TOOL_BLOCK_RES = (
    re.compile(
        r"<\|?\s*DSML\s*\|?[^>]*>.*?</\|?\s*DSML\s*\|?[^>]*>",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"<\|?\s*DSML\s*\|?[^>]*>.*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"<tool_calls?>.*?</tool_calls?>",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"<tool_calls?>.*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"<function_calls?>.*?</function_calls?>",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"<function_calls?>.*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"<invoke\b[^>]*>.*?</invoke>",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"<minimax:tool_call>.*?</minimax:tool_call>",
        re.IGNORECASE | re.DOTALL,
    ),
)

# Lone DSML / tool lines that survive incomplete tags
_TOOL_LINE_RE = re.compile(
    r"^\s*(?:</?\|?\s*DSML\s*\|?[^>]*>|"
    r"</?tool_calls?>|"
    r"</?invoke\b|"
    r"<parameter\b|"
    r"</parameter>|"
    r"name=\"(?:read|run_shell|bash|Write|Edit|Search)\")\s*.*$",
    re.IGNORECASE | re.MULTILINE,
)


def sanitize_model_text(text: str) -> str:
    """Remove leaked tool-call XML/DSML; return cleaned prose (may be empty)."""
    if not text:
        return ""
    out = text.replace("\r\n", "\n").replace("\r", "\n")
    for rx in _TOOL_BLOCK_RES:
        out = rx.sub("", out)
    out = _TOOL_LINE_RE.sub("", out)
    # Collapse leftover blank runs
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def looks_like_tool_dump(text: str) -> bool:
    """True when the reply is mostly tool protocol, not user-facing prose."""
    if not text:
        return False
    low = text.lower()
    markers = (
        "dsml",
        "<tool_call",
        "<function_call",
        "<invoke",
        "tool_calls>",
        'name="read"',
        'name="run_shell"',
    )
    hits = sum(1 for m in markers if m in low)
    if hits >= 2:
        return True
    cleaned = sanitize_model_text(text)
    if not cleaned and hits >= 1:
        return True
    if len(text) > 80 and len(cleaned) < max(20, len(text) * 0.15):
        return True
    return False
