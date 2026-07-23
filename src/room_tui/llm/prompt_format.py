"""Parse paper-derived --out prompt files (text or JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PromptParts:
    system: str
    user: str
    raw_path: Path


def parse_prompt_file(path: Path) -> PromptParts:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and ("system" in data or "user" in data):
                return PromptParts(
                    system=str(data.get("system", "")),
                    user=str(data.get("user", "")),
                    raw_path=path,
                )
        except json.JSONDecodeError:
            pass

    system, user = _split_system_user(text)
    return PromptParts(system=system, user=user, raw_path=path)


def _split_system_user(text: str) -> tuple[str, str]:
    """Split ==== SYSTEM ==== / ==== USER ==== text prompt."""
    markers = [
        ("==== SYSTEM ====", "==== USER ===="),
        ("=== SYSTEM ===", "=== USER ==="),
        ("## SYSTEM", "## USER"),
    ]
    for sys_m, user_m in markers:
        if sys_m in text and user_m in text:
            after_sys = text.split(sys_m, 1)[1]
            system, user = after_sys.split(user_m, 1)
            return system.strip(), user.strip()
    # whole file as user
    return "", text.strip()
