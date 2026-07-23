"""Pi Agent runner.

Two modes:
- ``execute`` (document worker): print mode, **no tools** — deterministic
  structured generation for paper-derived.
- ``chat`` (Room agent): full Pi Agent — tools, skills, extensions, context
  files, and a persistent session under the workspace.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from room_tui.config import PiTierConfig
from room_tui.console_title import (
    restore_console_title,
    set_console_title,
    win_no_window_kwargs,
)
from room_tui.pi_env import pi_agent_environ
from room_tui.llm.agent_blocks import (
    AgentBlock,
    AgentTurn,
    apply_tool_execution,
    blocks_from_pi_message,
    classify_plain_text,
)
from room_tui.llm.prompt_format import parse_prompt_file


@dataclass
class WorkerRequest:
    key: str
    prompt_file: Path
    response_file: Path
    tier: PiTierConfig = field(default_factory=PiTierConfig)
    timeout_s: float = 600.0


@dataclass
class WorkerResult:
    ok: bool
    response_file: Path
    bytes_written: int = 0
    latency_ms: int = 0
    model: str = ""
    provider: str = ""
    error: str | None = None
    peek: str = ""
    # Structured multi-type agent content (chat / JSON mode)
    agent_turn: AgentTurn | None = None


RunnerEvent = dict[str, Any]
EventCb = Callable[[RunnerEvent], None]


def _prefer_actionable_stderr(stderr: str) -> str:
    """Prefer real Error lines over benign session-create Warnings.

    The agent worker may print session-create Warnings even on the success
    path; if a later Error follows, Room must surface that (e.g. Unknown
    provider) instead of the truncated Warning.
    """
    text = (stderr or "").strip()
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errors = [
        ln
        for ln in lines
        if ln.lower().startswith("error:")
        or "unknown provider" in ln.lower()
        or "enoent" in ln.lower()
    ]
    if errors:
        return "\n".join(errors[-3:])
    # Drop pure session-create warnings if something else remains
    rest = [
        ln
        for ln in lines
        if "no project session found" not in ln.lower()
        and not ln.lower().startswith("warning: no project session")
    ]
    if rest:
        return "\n".join(rest[-5:])
    return text


def _extract_error_message_from_ndjson(text: str) -> str:
    """Pull errorMessage / message from agent JSON event stream (not full dump)."""
    import json as _json
    import re

    t = (text or "").strip()
    if not t:
        return ""
    # Prefer explicit errorMessage fields in any line
    for m in re.finditer(r'"errorMessage"\s*:\s*"((?:\\.|[^"\\])*)"', t):
        raw = m.group(1)
        try:
            # unescape JSON string fragment
            s = _json.loads(f'"{raw}"')
        except Exception:
            s = raw.encode("utf-8", "backslashreplace").decode("unicode_escape", "replace")
        if s.strip():
            return s.strip()
    for line in t.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = _json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        if isinstance(msg, dict):
            em = msg.get("errorMessage") or msg.get("error")
            if em:
                return str(em).strip()
            if str(msg.get("stopReason") or "") == "error":
                return "模型调用失败（stopReason=error）"
        em = ev.get("errorMessage") or ev.get("error")
        if em:
            return str(em).strip()
    return ""


def _user_facing_agent_error(err: str) -> str:
    """Rewrite backend worker tool names so UI never exposes them (e.g. pi)."""
    t = (err or "").strip()
    if not t:
        return "Room Agent 失败"
    # Prefer single errorMessage over raw NDJSON dumps
    extracted = _extract_error_message_from_ndjson(t)
    if extracted:
        t = extracted
    # Prefer actionable stderr first when callers pass raw dumps
    t = _prefer_actionable_stderr(t) or t
    low = t.lower()

    # LM Studio rejects placeholder tokens like "lmstudio"
    if "malformed lm studio api token" in low or (
        "lm studio" in low and "invalid_api_key" in low
    ):
        return (
            "LM Studio 拒绝了 API Token（占位密钥无效）。\n"
            "处理：在 LM Studio → Developer / Server 关闭鉴权，"
            "或填入真实 Token 后执行 /setup 重新配置 lmstudio；"
            "密钥请用 sk-… 形式，不要用纯单词 lmstudio。"
        )
    if "401" in low and ("api" in low or "auth" in low or "token" in low or "key" in low):
        return (
            f"模型鉴权失败（401）。请检查 Room agent auth.json / models.json 中的 Key 与 Base URL。\n"
            f"详情: {t[:280]}"
        )

    # Never leak binary / product name of the agent host.
    for needle in (
        "pi not found",
        "pi timeout",
        "pi exit",
        "pi returned",
        "pi:",
        "pi ",
        "\npi",
        "`pi`",
        "coding-agent",
        "@mariozechner/pi",
    ):
        if needle in low:
            # Generic rewrites for common cases
            if "not found" in low or "enoent" in low:
                return "Room Agent 不可用（未找到执行组件）"
            if "timeout" in low:
                return "Room Agent 超时"
            if "unknown provider" in low:
                # Keep the useful part, strip tool brand if present
                import re

                m = re.search(r"unknown provider[^\n]*", t, flags=re.I)
                return (m.group(0) if m else "Unknown provider")[:200]
            # Fall through with scrubbed text
            break
    # Scrub remaining bare "pi" tokens that look like tool branding
    import re

    # If it still looks like NDJSON event soup, collapse
    if t.count('{"type"') >= 2 or t.count('"type":"session"') >= 1:
        extracted2 = _extract_error_message_from_ndjson(t)
        if extracted2:
            t = extracted2
        else:
            t = "模型调用失败（Agent 返回了错误事件，无文本回复）"
    scrubbed = re.sub(r"(?i)\bpi\b", "Room Agent", t)
    scrubbed = re.sub(r"(?i)room agent agent", "Room Agent", scrubbed)
    return scrubbed[:2000]


class PiRunner:
    """Pi subprocess host for Room.

    Document workers stay tool-less; free-form chat is a full agent.
    """

    # Stable session id so multi-turn chat continues in the same workspace.
    CHAT_SESSION_ID = "room-agent"

    def __init__(self, pi_bin: str = "pi"):
        self.pi_bin = pi_bin
        # In-flight chat process (for Esc×2 / /cancel to kill immediately).
        self._active_chat_proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def agent_session_dir(work_dir: Path) -> Path:
        d = Path(work_dir) / ".pd" / "tui" / "pi-agent"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _kill_proc_tree(proc: asyncio.subprocess.Process | None) -> None:
        """Terminate pi and its children (tools/LLM helpers).

        Chat starts with ``start_new_session=True`` so ``pid`` is the process
        group leader — killpg covers bash/read child processes too.
        """
        if proc is None or proc.returncode is not None:
            return
        pid = proc.pid
        if not pid:
            return
        # SIGTERM process group first, then SIGKILL.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    if sig == signal.SIGTERM:
                        proc.terminate()
                    else:
                        proc.kill()
                except ProcessLookupError:
                    return
            if sig == signal.SIGTERM:
                # brief grace before escalate (sync; cancel path is urgent)
                time.sleep(0.05)
                if proc.returncode is not None:
                    return

    def kill_active_chat(self) -> bool:
        """Kill the current Agent chat subprocess (callable from UI handlers)."""
        proc = self._active_chat_proc
        if proc is None:
            return False
        self._kill_proc_tree(proc)
        return True

    async def execute(
        self,
        req: WorkerRequest,
        *,
        on_event: EventCb | None = None,
        cancel: asyncio.Event | None = None,
    ) -> WorkerResult:
        def emit(ev: RunnerEvent) -> None:
            if on_event:
                on_event(ev)

        t0 = time.monotonic()
        provider = req.tier.provider
        model = req.tier.model

        if not req.prompt_file.exists():
            return WorkerResult(
                ok=False,
                response_file=req.response_file,
                error=f"prompt missing: {req.prompt_file}",
                provider=provider,
                model=model,
            )

        parts = parse_prompt_file(req.prompt_file)
        req.response_file.parent.mkdir(parents=True, exist_ok=True)
        if req.response_file.exists():
            req.response_file.unlink()

        # Avoid ARG_MAX: system via --append-system-prompt file, user via @file
        tmp_dir = req.response_file.parent.parent / "tui" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        sys_file = tmp_dir / f"{req.key}.system.txt"
        user_file = tmp_dir / f"{req.key}.user.txt"
        worker_preamble = (
            "You are a document-generation worker for paper-derived. "
            "Follow the system instructions exactly. "
            "Output ONLY the required response body (usually JSON or structured text). "
            "No markdown fences unless required. No chatter."
        )
        sys_file.write_text(
            (worker_preamble + "\n\n" + parts.system).strip() + "\n",
            encoding="utf-8",
        )
        user_file.write_text(parts.user.strip() + "\n", encoding="utf-8")

        cmd = [
            self.pi_bin,
            "-p",
            "--no-tools",
            "--no-session",
            "--no-skills",
            "--no-extensions",
            "--no-context-files",
            "--thinking",
            req.tier.thinking or "off",
            "--system-prompt",
            worker_preamble,
            "--append-system-prompt",
            str(sys_file),
            f"@{user_file}",
            "Execute the USER task from the attached content according to SYSTEM. "
            "Output only the final response body.",
        ]
        if provider:
            cmd.extend(["--provider", provider])
        if model:
            cmd.extend(["--model", model])

        emit(
            {
                "type": "worker_start",
                "key": req.key,
                "provider": provider,
                "model": model,
                "prompt_file": str(req.prompt_file),
            }
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=pi_agent_environ(),
                start_new_session=True,
                **win_no_window_kwargs(),
            )
        except FileNotFoundError:
            return WorkerResult(
                ok=False,
                response_file=req.response_file,
                error="Room Agent 不可用（未找到执行组件）",
                provider=provider,
                model=model,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def read_stream(stream: asyncio.StreamReader | None, bucket: list[bytes], kind: str) -> None:
            if stream is None:
                return
            while True:
                if cancel and cancel.is_set():
                    break
                try:
                    chunk = await asyncio.wait_for(stream.read(4096), timeout=0.4)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    break
                bucket.append(chunk)
                if kind == "stdout" and on_event:
                    peek = chunk.decode("utf-8", errors="replace")[-80:]
                    on_event({"type": "worker_progress", "key": req.key, "peek": peek})

        async def watch_cancel() -> None:
            """Kill pi as soon as cancel is set (do not wait for natural exit)."""
            if cancel is None:
                # still wait until process ends so gather doesn't hang
                await proc.wait()
                return
            while True:
                if cancel.is_set():
                    self._kill_proc_tree(proc)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        self._kill_proc_tree(proc)
                    return
                if proc.returncode is not None:
                    return
                await asyncio.sleep(0.05)

        async def heartbeat() -> None:
            """Periodic alive signal while LLM is silent (buffered stdout).

            Without this, multi-minute generations look frozen even though pi
            is healthy. UI uses it to refresh elapsed / phase cues.
            """
            if on_event is None:
                await proc.wait()
                return
            # First beat after 8s; then every 10s until process ends.
            delay = 8.0
            while True:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=delay)
                    return
                except asyncio.TimeoutError:
                    pass
                if cancel and cancel.is_set():
                    return
                if proc.returncode is not None:
                    return
                on_event(
                    {
                        "type": "worker_heartbeat",
                        "key": req.key,
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
                        "bytes": sum(len(c) for c in stdout_chunks),
                    }
                )
                delay = 10.0

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    read_stream(proc.stdout, stdout_chunks, "stdout"),
                    read_stream(proc.stderr, stderr_chunks, "stderr"),
                    proc.wait(),
                    watch_cancel(),
                    heartbeat(),
                ),
                timeout=req.timeout_s,
            )
        except asyncio.TimeoutError:
            self._kill_proc_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            return WorkerResult(
                ok=False,
                response_file=req.response_file,
                error=f"Room Agent 超时（{int(req.timeout_s)}s）",
                provider=provider,
                model=model,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        if cancel and cancel.is_set():
            self._kill_proc_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            emit({"type": "worker_error", "key": req.key, "error": "cancelled"})
            return WorkerResult(
                ok=False,
                response_file=req.response_file,
                error="cancelled",
                provider=provider,
                model=model,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        latency = int((time.monotonic() - t0) * 1000)
        # Child may have renamed the console; restore Room branding.
        restore_console_title()

        if proc.returncode != 0:
            err = stderr.strip() or stdout.strip() or f"Room Agent 异常退出 ({proc.returncode})"
            err = _user_facing_agent_error(err)
            emit({"type": "worker_error", "key": req.key, "error": err[:500]})
            return WorkerResult(
                ok=False,
                response_file=req.response_file,
                error=err[:2000],
                provider=provider,
                model=model,
                latency_ms=latency,
            )

        body = stdout.strip()
        if not body:
            return WorkerResult(
                ok=False,
                response_file=req.response_file,
                error="Room Agent 返回空响应",
                provider=provider,
                model=model,
                latency_ms=latency,
                peek=(stderr[-120:] if stderr else ""),
            )

        req.response_file.write_text(body + "\n", encoding="utf-8")
        peek = body[:120].replace("\n", " ")
        emit(
            {
                "type": "worker_done",
                "key": req.key,
                "bytes": len(body),
                "latency_ms": latency,
            }
        )
        return WorkerResult(
            ok=True,
            response_file=req.response_file,
            bytes_written=len(body.encode("utf-8")),
            latency_ms=latency,
            provider=provider,
            model=model,
            peek=peek,
        )

    async def chat(
        self,
        user_text: str,
        *,
        system: str = "",
        tier: PiTierConfig | None = None,
        timeout_s: float = 900.0,
        on_event: EventCb | None = None,
        cancel: asyncio.Event | None = None,
        work_dir: Path | None = None,
        full_agent: bool = True,
        skill_paths: list[Path] | None = None,
    ) -> WorkerResult:
        """One Room chat turn via Pi.

        When ``full_agent`` is True (default), this is a **real Pi Agent** run:
        built-in tools (read/bash/edit/write), skills, extensions, AGENTS.md /
        CLAUDE.md, and a durable session under ``.pd/tui/pi-agent/``.

        ``skill_paths`` forces extra ``--skill`` loads (in addition to discovery).

        Document-pipeline workers still use :meth:`execute` with ``--no-tools``.
        """
        t0 = time.monotonic()
        tier = tier or PiTierConfig()
        provider, model = tier.provider, tier.model
        work = (work_dir or Path.cwd()).resolve()
        tmp = work / ".pd" / "tui" / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        key = f"chat-{int(t0 * 1000)}"
        append_file = tmp / f"{key}.append-system.txt"
        user_file = tmp / f"{key}.user.txt"
        resp_file = work / ".pd" / "responses" / f"{key}.txt"
        resp_file.parent.mkdir(parents=True, exist_ok=True)
        if resp_file.exists():
            resp_file.unlink()

        # Append Room workbench notes — do NOT replace Pi's default agent system
        # prompt when full_agent, so coding tools/skills behave normally.
        room_notes = (system or "").strip()
        if not room_notes:
            room_notes = (
                "You are Room Agent inside Room TUI, a workbench for the project "
                "workspace. Use tools freely when needed."
            )
        append_file.write_text(room_notes + "\n", encoding="utf-8")
        user_file.write_text(user_text.strip() + "\n", encoding="utf-8")

        session_dir = self.agent_session_dir(work)
        thinking = tier.thinking or "off"

        use_json = bool(full_agent)
        if full_agent:
            # Full Pi Agent + JSON event stream for multi-type rendering.
            cmd = [
                self.pi_bin,
                "-p",
                "--mode",
                "json",
                "--approve",
                "--session-dir",
                str(session_dir),
                "--session-id",
                self.CHAT_SESSION_ID,
                "--name",
                "Room",
                "--thinking",
                thinking,
                "--append-system-prompt",
                str(append_file),
                f"@{user_file}",
            ]
            for sp in skill_paths or []:
                p = Path(sp)
                if p.exists():
                    cmd.extend(["--skill", str(p.resolve())])
        else:
            cmd = [
                self.pi_bin,
                "-p",
                "--no-tools",
                "--no-session",
                "--no-skills",
                "--no-extensions",
                "--no-context-files",
                "--thinking",
                thinking,
                "--system-prompt",
                "Concise workbench assistant. No tools.",
                "--append-system-prompt",
                str(append_file),
                f"@{user_file}",
            ]
        if provider:
            cmd.extend(["--provider", provider])
        if model:
            cmd.extend(["--model", model])

        if on_event:
            on_event(
                {
                    "type": "worker_start",
                    "key": key,
                    "kind": "chat",
                    "agent": full_agent,
                    "json_mode": use_json,
                    "session_id": self.CHAT_SESSION_ID if full_agent else "",
                    "cwd": str(work),
                }
            )

        env = pi_agent_environ()
        env.setdefault("PWD", str(work))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work),
                env=env,
                start_new_session=True,
                **win_no_window_kwargs(),
            )
        except FileNotFoundError:
            return WorkerResult(
                ok=False,
                response_file=resp_file,
                error="Room Agent 不可用（未找到执行组件）",
                provider=provider,
                model=model,
            )

        self._active_chat_proc = proc
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        # JSON stream assembly
        turn = AgentTurn(provider=provider, model=model)
        line_buf = ""
        saw_agent_text = False
        stream_error: str = ""

        def _handle_json_line(line: str) -> None:
            nonlocal provider, model, saw_agent_text, stream_error
            line = line.strip()
            if not line:
                return
            import json as _json

            try:
                ev = _json.loads(line)
            except _json.JSONDecodeError:
                return
            if not isinstance(ev, dict):
                return
            et = str(ev.get("type") or "")

            if et == "tool_execution_start":
                name = str(ev.get("toolName") or ev.get("tool_name") or "tool")
                args = ev.get("args")
                turn.blocks.append(
                    AgentBlock(
                        kind="tool",
                        tool_name=name,
                        tool_args=args,
                        text=name,
                    )
                )
                if on_event:
                    on_event(
                        {
                            "type": "agent_tool_start",
                            "key": key,
                            "kind": "chat",
                            "tool": name,
                            "args": args,
                        }
                    )
            elif et == "tool_execution_end":
                name = str(ev.get("toolName") or ev.get("tool_name") or "tool")
                apply_tool_execution(
                    turn.blocks,
                    tool_name=name,
                    args=ev.get("args"),
                    result=ev.get("result"),
                    is_error=bool(ev.get("isError") or ev.get("is_error")),
                )
                if on_event:
                    on_event(
                        {
                            "type": "agent_tool_end",
                            "key": key,
                            "kind": "chat",
                            "tool": name,
                            "args": ev.get("args"),
                            "result": ev.get("result"),
                            "is_error": bool(ev.get("isError") or ev.get("is_error")),
                        }
                    )
            elif et == "message_end":
                msg = ev.get("message")
                if not isinstance(msg, dict):
                    return
                role = str(msg.get("role") or "")
                if msg.get("provider"):
                    provider = str(msg["provider"])
                    turn.provider = provider
                if msg.get("model"):
                    model = str(msg["model"])
                    turn.model = model
                # Surface API errors without dumping the whole NDJSON stream
                em = msg.get("errorMessage") or msg.get("error")
                if em:
                    stream_error = str(em).strip() or stream_error
                elif str(msg.get("stopReason") or "") == "error":
                    stream_error = stream_error or "模型调用失败（stopReason=error）"
                if role != "assistant":
                    return
                for b in blocks_from_pi_message(msg):
                    # Prefer tool_execution_* for tool details; skip bare toolCall
                    # if we already have tool_execution_start for same name.
                    if b.kind == "tool":
                        exists = any(
                            x.kind == "tool" and x.tool_name == b.tool_name
                            for x in turn.blocks
                        )
                        if exists:
                            continue
                    turn.blocks.append(b)
                    if b.kind == "text" and b.text.strip():
                        saw_agent_text = True
            elif et == "message_update" and on_event:
                # Live streams: thinking body + answer text (Grok token-by-token).
                ame = ev.get("assistantMessageEvent") or {}
                at = str(ame.get("type") or "")
                delta = str(ame.get("delta") or "")
                if at == "thinking_delta":
                    on_event(
                        {
                            "type": "agent_thinking_delta",
                            "key": key,
                            "kind": "chat",
                            "delta": delta,
                            "peek": delta[-80:],
                        }
                    )
                elif at == "text_delta":
                    # Full delta for scrollback stream (not only footer peek).
                    on_event(
                        {
                            "type": "agent_text_delta",
                            "key": key,
                            "kind": "chat",
                            "delta": delta,
                            "peek": delta[-80:],
                        }
                    )
                    on_event(
                        {
                            "type": "worker_progress",
                            "key": key,
                            "kind": "chat",
                            "phase": "writing",
                            "peek": delta[-80:],
                        }
                    )

        async def _read_stdout() -> None:
            nonlocal line_buf
            stream = proc.stdout
            if stream is None:
                return
            while True:
                if cancel and cancel.is_set():
                    break
                try:
                    chunk = await asyncio.wait_for(stream.read(4096), timeout=0.4)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    break
                stdout_chunks.append(chunk)
                if use_json:
                    text = chunk.decode("utf-8", errors="replace")
                    line_buf += text
                    while "\n" in line_buf:
                        line, line_buf = line_buf.split("\n", 1)
                        _handle_json_line(line)
                elif on_event:
                    peek = chunk.decode("utf-8", errors="replace")[-160:]
                    on_event(
                        {
                            "type": "worker_progress",
                            "key": key,
                            "kind": "chat",
                            "stream": "stdout",
                            "peek": peek,
                        }
                    )

        async def _read_stderr() -> None:
            stream = proc.stderr
            if stream is None:
                return
            while True:
                if cancel and cancel.is_set():
                    break
                try:
                    chunk = await asyncio.wait_for(stream.read(4096), timeout=0.4)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    break
                stderr_chunks.append(chunk)

        async def watch_cancel() -> None:
            if cancel is None:
                await proc.wait()
                return
            while True:
                if cancel.is_set():
                    self._kill_proc_tree(proc)
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        self._kill_proc_tree(proc)
                    return
                if proc.returncode is not None:
                    return
                await asyncio.sleep(0.05)

        async def heartbeat() -> None:
            if on_event is None:
                await proc.wait()
                return
            delay = 8.0
            while True:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=delay)
                    return
                except asyncio.TimeoutError:
                    pass
                if cancel and cancel.is_set():
                    return
                if proc.returncode is not None:
                    return
                on_event(
                    {
                        "type": "worker_heartbeat",
                        "key": key,
                        "kind": "chat",
                        "elapsed_ms": int((time.monotonic() - t0) * 1000),
                        "bytes": sum(len(c) for c in stdout_chunks),
                    }
                )
                delay = 10.0

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read_stdout(),
                    _read_stderr(),
                    proc.wait(),
                    watch_cancel(),
                    heartbeat(),
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            self._kill_proc_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            if self._active_chat_proc is proc:
                self._active_chat_proc = None
            return WorkerResult(
                ok=False,
                response_file=resp_file,
                error=f"Room Agent 超时（{int(timeout_s)}s）",
                provider=provider,
                model=model,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        finally:
            # Always drop the handle when leaving the wait (cancel or success).
            # Note: success path continues below; clear only if still current.
            pass

        if self._active_chat_proc is proc:
            self._active_chat_proc = None

        if cancel and cancel.is_set():
            self._kill_proc_tree(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            if self._active_chat_proc is proc:
                self._active_chat_proc = None
            restore_console_title()
            return WorkerResult(
                ok=False,
                response_file=resp_file,
                error="cancelled",
                provider=provider,
                model=model,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        # Flush trailing JSON line
        if use_json and line_buf.strip():
            _handle_json_line(line_buf)

        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace").strip()
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        latency = int((time.monotonic() - t0) * 1000)
        restore_console_title()

        if use_json:
            # Expand text blocks into code/diff/md segments where useful
            expanded: list[AgentBlock] = []
            for b in turn.blocks:
                if b.kind == "text" and b.text:
                    parts = classify_plain_text(b.text)
                    expanded.extend(parts if parts else [b])
                else:
                    expanded.append(b)
            turn.blocks = expanded
            flat = turn.assistant_text()
            turn.raw_text = flat
            # Never dump NDJSON event soup into the chat as "assistant text"
            if not turn.blocks and stdout and not stdout.lstrip().startswith("{"):
                turn.blocks = classify_plain_text(stdout)
                turn.raw_text = stdout

            has_content = bool(turn.blocks or saw_agent_text or flat)
            if stream_error or (not has_content and (stdout or stderr or proc.returncode != 0)):
                err_src = (
                    stream_error
                    or _extract_error_message_from_ndjson(stdout)
                    or _prefer_actionable_stderr(stderr)
                    or (f"Room Agent 异常退出 ({proc.returncode})" if proc.returncode else "")
                    or "模型调用失败（无文本回复）"
                )
                err = _user_facing_agent_error(err_src)
                if on_event:
                    on_event(
                        {
                            "type": "worker_error",
                            "key": key,
                            "kind": "chat",
                            "error": err[:500],
                        }
                    )
                return WorkerResult(
                    ok=False,
                    response_file=resp_file,
                    error=err[:2000],
                    provider=provider,
                    model=model,
                    latency_ms=latency,
                    agent_turn=turn,
                )
            # Persist human-readable flat text + structured side file
            resp_file.write_text((turn.raw_text or flat or "") + "\n", encoding="utf-8")
            try:
                import json as _json

                side = resp_file.with_suffix(".blocks.json")
                # Full block payload (incl. tool args/result) for history restore.
                side.write_text(
                    _json.dumps(
                        [b.to_dict() for b in turn.blocks],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            if on_event:
                on_event(
                    {
                        "type": "worker_done",
                        "key": key,
                        "kind": "chat",
                        "bytes": len(turn.raw_text or ""),
                        "latency_ms": latency,
                        "blocks": len(turn.blocks),
                    }
                )
            return WorkerResult(
                ok=True,
                response_file=resp_file,
                bytes_written=len((turn.raw_text or "").encode("utf-8")),
                latency_ms=latency,
                provider=provider,
                model=model,
                peek=(turn.raw_text or "")[:200].replace("\n", " "),
                agent_turn=turn,
            )

        # Text mode (legacy / no-tools)
        if proc.returncode != 0 or not stdout:
            err = _user_facing_agent_error(
                stderr or stdout or f"Room Agent 异常退出 ({proc.returncode})"
            )
            if on_event:
                on_event(
                    {
                        "type": "worker_error",
                        "key": key,
                        "kind": "chat",
                        "error": err[:500],
                    }
                )
            return WorkerResult(
                ok=False,
                response_file=resp_file,
                error=err[:2000],
                provider=provider,
                model=model,
                latency_ms=latency,
            )
        resp_file.write_text(stdout + "\n", encoding="utf-8")
        turn.blocks = classify_plain_text(stdout)
        turn.raw_text = stdout
        if on_event:
            on_event(
                {
                    "type": "worker_done",
                    "key": key,
                    "kind": "chat",
                    "bytes": len(stdout),
                    "latency_ms": latency,
                }
            )
        return WorkerResult(
            ok=True,
            response_file=resp_file,
            bytes_written=len(stdout.encode("utf-8")),
            latency_ms=latency,
            provider=provider,
            model=model,
            peek=stdout[:200].replace("\n", " "),
            agent_turn=turn,
        )
