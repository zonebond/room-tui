"""Keep the host terminal tab/title branded as Room (not pi / agent host)."""

from __future__ import annotations

import subprocess
import sys
from typing import Any


ROOM_CONSOLE_TITLE = "Room"
_last_title: str = ROOM_CONSOLE_TITLE


def set_console_title(title: str = ROOM_CONSOLE_TITLE) -> None:
    """Set Windows console title + OSC 0 (Windows Terminal / iTerm / etc.)."""
    global _last_title
    title = (title or ROOM_CONSOLE_TITLE).strip() or ROOM_CONSOLE_TITLE
    _last_title = title
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    # OSC 0: set icon name and window title (ignored by many hosts; harmless)
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "write"):
                stream.write(f"\033]0;{title}\007")
                stream.flush()
                break
        except Exception:
            continue


def restore_console_title() -> None:
    """Re-apply last Room title (after a child like pi may have overwritten it)."""
    set_console_title(_last_title or ROOM_CONSOLE_TITLE)


def win_no_window_kwargs() -> dict[str, Any]:
    """Kwargs so child console apps (pi) do not hijack the parent console title.

    On Windows, a child sharing the console can call SetConsoleTitle /
    ``process.title = "pi"`` and rename the Windows Terminal tab. CREATE_NO_WINDOW
    avoids attaching a console while still allowing stdout/stderr pipes.
    """
    if sys.platform != "win32":
        return {}
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return {"creationflags": int(flag)}


def run_subprocess_no_console(
    cmd: list[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """``subprocess.run`` for Room children (pi / engines) without stealing the tab title.

    Always merges ``win_no_window_kwargs`` and restores the Room title afterwards.
    """
    merged = dict(kwargs)
    for k, v in win_no_window_kwargs().items():
        # Caller may pass creationflags; OR them with CREATE_NO_WINDOW
        if k == "creationflags" and "creationflags" in merged:
            merged["creationflags"] = int(merged["creationflags"]) | int(v)
        else:
            merged.setdefault(k, v)
    try:
        return subprocess.run(cmd, **merged)
    finally:
        restore_console_title()
