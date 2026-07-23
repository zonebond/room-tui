"""Textual application entry — main shell workbench."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Keyboard protocol policy (macOS CJK IME + Shift+Enter):
#
# Textual's default Kitty flags include REPORT_ALL_KEYS, which:
#   • emits left_shift / left_option as bare key events (got typed into the buffer)
#   • breaks many CJK IMEs (committed glyphs never become Key.character)
#
# Room keeps CSI-u **parsing** on (do not set TEXTUAL_DISABLE_KITTY_KEY=1 by
# default) but patches the driver enable mask to:
#   DISAMBIGUATE | ASSOCIATED_TEXT
# so Shift+Enter is distinct and IME text can ride along, without reporting
# every modifier keydown.
#
# Escape hatches:
#   TEXTUAL_DISABLE_KITTY_KEY=1 room   → classic bytes (IME-safe; use Alt+Enter)
#   ROOM_KITTY_FULL=1 room             → Textual stock flags (debug)
if os.environ.get("TEXTUAL_DISABLE_KITTY_KEY") is None:
    # Explicitly enable parsing/enable path (unset would also work; set 0 for clarity).
    os.environ["TEXTUAL_DISABLE_KITTY_KEY"] = "0"


def _install_gentle_kitty_keyboard() -> None:
    """Patch Textual LinuxDriver Kitty progressive-enhancement flags."""
    if os.environ.get("TEXTUAL_DISABLE_KITTY_KEY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    if os.environ.get("ROOM_KITTY_FULL", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    try:
        from textual.drivers import linux_driver as ld
    except Exception:
        return

    disamb = getattr(ld, "KITTY_DISAMBIGUATE_ESCAPE_CODES", 0b1)
    assoc = getattr(ld, "KITTY_REPORT_ASSOCIATED_TEXT", 0b10000)
    gentle = int(disamb) | int(assoc)
    gentle_seq = f"\x1b[>{gentle}u"

    if getattr(ld.LinuxDriver, "_room_gentle_kitty", False):
        return

    orig = ld.LinuxDriver.start_application_mode

    def start_application_mode(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        real_write = self.write

        def write(data):  # type: ignore[no-untyped-def]
            # Rewrite Textual's full-flag enable to our gentler mask.
            if (
                isinstance(data, str)
                and len(data) >= 4
                and data.startswith("\x1b[>")
                and data.endswith("u")
                and data[3:-1].isdigit()
            ):
                data = gentle_seq
            return real_write(data)

        self.write = write  # type: ignore[method-assign]
        try:
            return orig(self, *args, **kwargs)
        finally:
            self.write = real_write  # type: ignore[method-assign]

    ld.LinuxDriver.start_application_mode = start_application_mode  # type: ignore[method-assign]
    ld.LinuxDriver._room_gentle_kitty = True  # type: ignore[attr-defined]


_install_gentle_kitty_keyboard()

from textual.app import App
from textual.binding import Binding

from room_tui.config import AppConfig, load_config
from room_tui.engine.adapter import EngineAdapter
from room_tui.orch.session import RunSpec, SessionOrchestrator
from room_tui.screens.shell import ShellScreen



class RoomApp(App[None]):
    TITLE = "Room"
    SUB_TITLE = ""
    # Intercept Ctrl+C at app level so Input/driver cannot swallow it
    BINDINGS = [
        Binding("ctrl+c", "app_ctrl_c", show=False, priority=True),
        Binding("ctrl+q", "quit", "退出", show=False),
    ]
    CSS = """
    Screen {
        background: $background;
    }
    """

    def __init__(self, cfg: AppConfig | None = None, *, session_id: str | None = None):
        super().__init__()
        # Textual default is 2 lines/tick — force 1 before any screen mounts.
        # SmoothScrollMixin also hardcodes ±1 and ignores this for Y, but keep
        # app state consistent for any code path still reading sensitivity.
        self.scroll_sensitivity_y = 1.0
        self.scroll_sensitivity_x = 3.0
        self.cfg = cfg or load_config()
        if self.cfg.workspace is None:
            self.cfg.workspace = Path.cwd()
        self.engine = EngineAdapter(
            self.cfg.paper_derived_bin,
            cwd=self.cfg.workspace,
            timeout_s=self.cfg.engine_timeout_s,
        )
        self.orch = SessionOrchestrator(self.cfg, engine=self.engine)
        self._open_session = session_id
        self._shell: ShellScreen | None = None

    def action_app_ctrl_c(self) -> None:
        """Delegate Ctrl+C to active shell (cancel → clear → quit)."""
        if self._shell is not None:
            try:
                self._shell.action_interrupt()
                return
            except Exception:
                pass
        self.exit()

    def copy_to_clipboard(self, text: str) -> None:
        """Copy to the real system clipboard (+ OSC 52 when the terminal allows it).

        Textual's default only writes OSC 52, which macOS Terminal (and many
        locked-down emulators) ignore — selection would show “已复制” with an
        empty pasteboard. Prefer OS tools first, then fall back to OSC 52.
        """
        self.copy_text_to_clipboard(text)

    def copy_text_to_clipboard(self, text: str) -> bool:
        """Copy *text*; return whether the OS pasteboard was updated."""
        payload = text if isinstance(text, str) else str(text)
        try:
            self._clipboard = payload
        except Exception:
            pass
        os_ok = self._copy_system_clipboard(payload)
        try:
            # OSC 52 for terminals that honor it (iTerm, WezTerm, …).
            if self._driver is not None:
                import base64

                b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
                self._driver.write(f"\x1b]52;c;{b64}\a")
        except Exception:
            pass
        return os_ok

    def read_text_from_clipboard(self) -> str:
        """Read system clipboard text (for right-click paste on Windows/macOS).

        When Textual enables mouse tracking, the host terminal no longer
        intercepts right-click → paste. Room must pull the OS clipboard itself.
        Falls back to the last text we copied into ``self._clipboard``.
        """
        text = self._read_system_clipboard()
        if text:
            return text
        try:
            cached = getattr(self, "_clipboard", None)
            if isinstance(cached, str) and cached:
                return cached
        except Exception:
            pass
        return ""

    @staticmethod
    def _copy_system_clipboard(text: str) -> bool:
        """Write *text* via pbcopy / wl-copy / xclip / clip. True on success."""
        data = text.encode("utf-8")
        candidates: list[list[str]] = []
        if sys.platform == "darwin":
            if shutil.which("pbcopy"):
                candidates.append(["pbcopy"])
        elif sys.platform == "win32":
            # `clip` expects UTF-16LE on modern Windows consoles.
            if shutil.which("clip"):
                candidates.append(["clip"])
        else:
            if shutil.which("wl-copy"):
                candidates.append(["wl-copy", "--type", "text/plain"])
            if shutil.which("xclip"):
                candidates.append(["xclip", "-selection", "clipboard"])
            if shutil.which("xsel"):
                candidates.append(["xsel", "--clipboard", "--input"])

        for cmd in candidates:
            try:
                if cmd[0] == "clip":
                    # Windows clip.exe: stdin as UTF-16LE with BOM works broadly.
                    proc = subprocess.run(
                        cmd,
                        input=text.encode("utf-16-le"),
                        check=False,
                        capture_output=True,
                        timeout=3,
                    )
                else:
                    proc = subprocess.run(
                        cmd,
                        input=data,
                        check=False,
                        capture_output=True,
                        timeout=3,
                    )
                if proc.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _read_windows_clipboard_win32() -> str:
        """Read CF_UNICODETEXT via Win32 API (correct CJK; no code-page 乱码)."""
        if sys.platform != "win32":
            return ""
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return ""

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13

        # Prototypes (avoid truncated handles on 64-bit).
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                text = ctypes.wstring_at(ptr)
                return text if isinstance(text, str) else ""
            finally:
                kernel32.GlobalUnlock(handle)
        except Exception:
            return ""
        finally:
            try:
                user32.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def _read_windows_clipboard_powershell() -> str:
        """Fallback: Get-Clipboard as UTF-16LE base64 (avoids console CP 乱码)."""
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return ""
        # Unicode = UTF-16LE in .NET — round-trip safe for Chinese.
        script = (
            "$t = Get-Clipboard -Raw; "
            "if ($null -eq $t) { '' } "
            "else { [Convert]::ToBase64String("
            "[Text.Encoding]::Unicode.GetBytes([string]$t)) }"
        )
        try:
            proc = subprocess.run(
                [
                    ps,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except Exception:
            return ""
        if proc.returncode != 0 or not proc.stdout:
            return ""
        import base64
        import re

        raw = (proc.stdout or b"").strip()
        # Strip BOM / whitespace / CR
        try:
            b64 = raw.decode("ascii", errors="ignore").strip()
        except Exception:
            b64 = ""
        b64 = re.sub(r"\s+", "", b64)
        if not b64:
            return ""
        try:
            data = base64.b64decode(b64, validate=False)
            return data.decode("utf-16-le", errors="strict")
        except Exception:
            return ""

    @staticmethod
    def _read_system_clipboard() -> str:
        """Read OS clipboard as text (best-effort). Empty string if unavailable."""
        try:
            if sys.platform == "darwin" and shutil.which("pbpaste"):
                proc = subprocess.run(
                    ["pbpaste"],
                    check=False,
                    capture_output=True,
                    timeout=3,
                )
                if proc.returncode == 0:
                    return (proc.stdout or b"").decode("utf-8", errors="replace")
            elif sys.platform == "win32":
                # 1) Win32 CF_UNICODETEXT — no console code-page issues
                text = RoomApp._read_windows_clipboard_win32()
                if text:
                    return text
                # 2) PowerShell + base64 UTF-16LE — safe across CP936/UTF-8
                text = RoomApp._read_windows_clipboard_powershell()
                if text:
                    return text
            else:
                for cmd in (
                    ["wl-paste", "-n"],
                    ["xclip", "-selection", "clipboard", "-o"],
                    ["xsel", "--clipboard", "--output"],
                ):
                    if not shutil.which(cmd[0]):
                        continue
                    proc = subprocess.run(
                        cmd,
                        check=False,
                        capture_output=True,
                        timeout=3,
                    )
                    if proc.returncode == 0:
                        return (proc.stdout or b"").decode("utf-8", errors="replace")
        except Exception:
            pass
        return ""

    def on_mount(self) -> None:
        # Keep Windows Terminal / console tab titled Room (not pi).
        from room_tui.console_title import set_console_title
        from room_tui.pi_env import apply_room_pi_isolation

        self.title = "Room"
        set_console_title("Room")
        # Process-wide isolation: bundled pi uses Room tree, never ~/.pi
        try:
            apply_room_pi_isolation(seed_skills=True)
        except Exception:
            pass
        # Keep 1-row wheel step (also set in __init__).
        self.scroll_sensitivity_y = 1.0
        self.scroll_sensitivity_x = 3.0
        shell = ShellScreen()
        self._shell = shell
        self.push_screen(shell)
        if self._open_session:
            # auto-resume into shell message stream
            self.set_timer(0.3, lambda: self.resume_session(self._open_session or ""))

    def open_wizard(self) -> None:
        """Start inline /new flow on the main shell (no dual-pane wizard)."""
        if self._shell is not None:
            self._shell.action_new_run()
            return
        # Fallback: rare if shell not mounted yet
        shell = ShellScreen()
        self._shell = shell
        self.push_screen(shell)
        self.set_timer(0.15, lambda: shell.action_new_run() if self._shell else None)

    def open_dashboard(self, session_id: str) -> None:
        """Compat: open shell focus + optional resume view (shell is the dashboard)."""
        self.orch.state.session_id = session_id
        if self._shell is not None:
            self._shell._msg(f"· 查看会话  {session_id}")
            self.run_worker(self._shell_refresh(session_id), exclusive=False)

    async def _shell_refresh(self, session_id: str) -> None:
        try:
            snap = await self.orch.refresh_snapshot(session_id)
            if self._shell:
                self._shell._render_chapters(
                    snap.sections, focus=self.orch.state.focus_section
                )
        except Exception as e:
            if self._shell:
                self._shell._msg(f"[提示] {e}")

    def start_run(
        self,
        *,
        template_id: str,
        inputs: list[Path],
        output: Path,
        budget: int,
        template_name: str = "",
    ) -> None:
        """Start session generation; messages stream on main shell."""
        ws = Path(self.cfg.workspace or Path.cwd()).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        self.cfg.budget = budget
        self.engine.cwd = ws
        spec = RunSpec(
            workspace=ws,
            template_id=template_id,
            inputs=inputs,
            output=output,
            budget=budget,
        )

        # Inline /new no longer pushes a wizard screen; keep pop for any
        # legacy modal still on the stack.
        try:
            if self.screen is not self._shell:
                self.pop_screen()
        except Exception:
            pass

        if self._shell is not None:
            self._shell.notify_run_started(
                template_id, template_name=template_name or template_id
            )

        async def _run() -> None:
            try:
                await self.orch.run(spec)
                self.notify(f"完成 → {output}", severity="information", timeout=8)
            except Exception as e:
                msg = str(e)
                if "cancel" in msg.lower():
                    self.notify("已取消", severity="warning", timeout=5)
                else:
                    self.notify(f"失败: {e}", severity="error", timeout=12)

        self.run_worker(_run(), exclusive=True)

    def resume_session(self, session_id: str, *, output: Path | None = None) -> None:
        """Continue session; stream on main shell."""
        if not session_id:
            return
        ws = Path(self.cfg.workspace or Path.cwd()).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        self.engine.cwd = ws
        out = (output or (ws / "output.md")).resolve()

        # Guard: manifest / session may reference a deleted template id.
        try:
            from room_tui.workspace import Workspace

            m = Workspace(ws).load_manifest()
            tid = (m.template_id if m else "") or ""
            if tid and not self.engine.template_exists(tid):
                if self._shell is not None:
                    self._shell._append_notice_block(
                        "没有可继续的进行中任务",
                        "上次文档生成已无法恢复。请先 /new 开始新的文档生成。",
                    )
                else:
                    self.notify("没有可继续的任务 · 请 /new", severity="warning", timeout=10)
                return
        except Exception:
            pass

        spec = RunSpec(
            workspace=ws,
            template_id="",
            inputs=[],
            output=out,
            session_id=session_id,
            budget=self.cfg.budget,
        )
        self.orch.state.session_id = session_id
        if self._shell is not None:
            # Status is the live step row opened by /continue — do not stack a
            # second system line for the same action.
            self._shell._pipe.done_keys.update({"template", "register", "feed"})
            self._shell._pipe.current_key = "generate"
            self._shell._render_steps()

        async def _run() -> None:
            try:
                await self.orch.refresh_snapshot(session_id)
                await self.orch.run(spec)
                self.notify(f"完成 → {out}", severity="information", timeout=8)
            except Exception as e:
                msg = str(e)
                if "cancel" in msg.lower():
                    self.notify("已取消", severity="warning", timeout=5)
                elif "模板不存在" in msg or "template not found" in msg.lower():
                    if self._shell is not None:
                        self._shell._append_notice_block(
                            "没有可继续的进行中任务",
                            "上次文档生成已无法恢复。请先 /new 开始新的文档生成。",
                        )
                    else:
                        self.notify("没有可继续的任务 · 请 /new", severity="warning", timeout=10)
                elif "stuck waiting" in msg.lower() or "never complete" in msg.lower():
                    if self._shell is not None:
                        self._shell._append_notice_block(
                            "续跑卡住：有章节仍标记为生成中（常见于上次取消/中断）",
                            "已尝试自动回收；请再试一次 /continue",
                            "若仍失败: /new 重新生成  ·  或删除 .pd/sessions 后 /new",
                        )
                    brief = msg.split("Traceback")[0].strip() or msg
                    if len(brief) > 160:
                        brief = brief[:157] + "…"
                    self.notify(f"失败: {brief}", severity="error", timeout=12)
                else:
                    # Never dump multi-line Traceback into toast
                    brief = msg.split("Traceback")[0].strip() or msg
                    if len(brief) > 160:
                        brief = brief[:157] + "…"
                    self.notify(f"失败: {brief}", severity="error", timeout=12)

        self.run_worker(_run(), exclusive=True)

    async def load_engine_version(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.engine.version)

    async def check_pi(self) -> bool:
        """True if Room Agent binary is resolvable (PATH or absolute path).

        Must match doctor: use resolve_bin, not bare shutil.which (absolute
        paths and suite bin/ siblings fail with which-only on some Windows setups).
        """
        from room_tui.config import resolve_bin

        return await asyncio.to_thread(
            lambda: resolve_bin(self.cfg.pi_bin) is not None
        )

    def probe_environment_sync(self) -> tuple[bool, str]:
        """Re-check engine + Room Agent (same bar as room doctor).

        Returns (ready, error_message). Used by bootstrap and chat gate so a
        green doctor cannot disagree with a red TUI.
        """
        from room_tui.config import resolve_bin
        from room_tui.engine.errors import humanize_engine_error

        try:
            ver = self.engine.version()
            if not isinstance(ver, dict) or not ver.get("version"):
                return False, "引擎 version 响应异常"
            caps = set(ver.get("capabilities") or [])
            need = {"out-text-prompt", "session-run"}
            missing = need - caps
            if missing:
                return (
                    False,
                    f"引擎缺少能力 {', '.join(sorted(missing))} · 请用 claude0 重打 paper-derived",
                )
        except Exception as e:
            raw = str(e).lower()
            if "no such command" in raw and "version" in raw:
                return (
                    False,
                    "引擎过旧（缺少 version）· 请用 claude0 重装 paper-derived",
                )
            if "not found" in raw or "找不到" in str(e):
                return False, "找不到 paper-derived 引擎"
            return False, humanize_engine_error(str(e))[:120] or "服务未就绪"

        if resolve_bin(self.cfg.pi_bin) is None:
            return (
                False,
                "缺少 Room Agent (pi) · 请重装套件 bin/pi.exe",
            )
        return True, ""

    async def probe_environment(self) -> tuple[bool, str]:
        return await asyncio.to_thread(self.probe_environment_sync)

    async def load_sessions(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.engine.session_list)

    async def load_session_status(self, session_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.engine.session_status, session_id)

    async def load_templates(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.engine.template_list)


def run_tui(
    cfg: AppConfig | None = None,
    *,
    session_id: str | None = None,
    workspace: Path | None = None,
) -> None:
    from room_tui.console_title import set_console_title

    # Windows Terminal tab often shows the last process that set the title
    # (e.g. pi). Force Room before Textual takes over.
    set_console_title("Room")
    if cfg is None:
        cfg = load_config()
    if workspace is not None:
        cfg.workspace = workspace
    elif cfg.workspace is None:
        cfg.workspace = Path.cwd()
    app = RoomApp(cfg, session_id=session_id)
    app.run()
