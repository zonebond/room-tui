"""Workspace layout: project-local .pd/ is the only task scope."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunManifest:
    """Current (or last) run bound to this project directory."""

    client_version: str
    template_id: str
    session_id: str = ""
    workspace: str = ""
    output: str = ""
    budget: int = 40000
    provider: str = ""
    model: str = ""
    strategy: str = "session"
    inputs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    orchestrator: str = "tui"
    status: str = "created"  # created|running|paused|complete|failed
    phase: str = ""  # engine phase if known
    progress: str = ""  # e.g. 5/42
    title: str = ""  # optional display name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunManifest":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def is_active(self) -> bool:
        return self.status in ("created", "running", "paused") or (
            self.phase not in ("", "complete", "done") and self.status != "complete"
        )

    @property
    def is_complete(self) -> bool:
        return self.status == "complete" or self.phase in ("complete", "done")


class Workspace:
    """Project root workspace. Tasks live only under this directory's .pd/."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.pd = self.root / ".pd"
        self.prompts = self.pd / "prompts"
        self.responses = self.pd / "responses"
        self.assets = self.pd / "assets"
        self.tui = self.pd / "tui"
        self.tmp = self.tui / "tmp"
        self.events_path = self.tui / "events.jsonl"
        self.manifest_path = self.tui / "run-manifest.json"
        self.history_path = self.tui / "run-history.jsonl"
        # UI transcript (user / assistant / system) — survives exit/re-entry
        self.chat_history_path = self.tui / "chat-history.jsonl"

    def ensure(self) -> None:
        for p in (self.prompts, self.responses, self.assets, self.tui, self.tmp):
            p.mkdir(parents=True, exist_ok=True)

    def prompt_path(self, key: str) -> Path:
        safe = key.replace("/", "_")
        return self.prompts / f"{safe}.md"

    def response_path(self, key: str) -> Path:
        safe = key.replace("/", "_")
        return self.responses / f"{safe}.txt"

    def asset_path(self, name: str) -> Path:
        safe = name.replace("/", "_")
        return self.assets / f"{safe}.json"

    def append_event(self, event: dict[str, Any]) -> None:
        self.tui.mkdir(parents=True, exist_ok=True)
        payload = {"ts": utc_now(), **event}
        with self.events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_events(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text(encoding="utf-8").splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def save_manifest(self, manifest: RunManifest) -> None:
        self.ensure()
        manifest.workspace = str(self.root)
        manifest.updated_at = utc_now()
        self.manifest_path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # append history snapshot (project-local only)
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "ts": utc_now(),
                        "session_id": manifest.session_id,
                        "template_id": manifest.template_id,
                        "status": manifest.status,
                        "phase": manifest.phase,
                        "progress": manifest.progress,
                        "output": manifest.output,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def load_manifest(self) -> RunManifest | None:
        if not self.manifest_path.exists():
            return None
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return RunManifest.from_dict(data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def update_manifest_progress(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        progress: str | None = None,
    ) -> RunManifest | None:
        m = self.load_manifest()
        if m is None:
            return None
        if session_id is not None:
            m.session_id = session_id
        if status is not None:
            m.status = status
        if phase is not None:
            m.phase = phase
        if progress is not None:
            m.progress = progress
        # write without history spam for pure progress ticks
        self.tui.mkdir(parents=True, exist_ok=True)
        m.updated_at = utc_now()
        self.manifest_path.write_text(
            json.dumps(m.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return m

    def has_project_run(self) -> bool:
        return self.manifest_path.exists()

    def append_chat_message(
        self,
        role: str,
        text: str = "",
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        """Append one UI message. ``role``: user | assistant | system | error.

        Assistant turns may carry Grok-like ``blocks`` (thought / tool / text)
        so process rows survive exit/re-entry. Either ``text`` or ``blocks``
        must be non-empty.
        """
        body = (text or "").rstrip()
        bl = list(blocks or [])
        if not body and not bl:
            return
        self.tui.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "ts": utc_now(),
            "role": role,
        }
        if body:
            payload["text"] = body
        if bl:
            payload["blocks"] = bl
        with self.chat_history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def read_chat_history(self, limit: int = 400) -> list[dict[str, Any]]:
        """Load recent UI transcript entries (oldest → newest within limit)."""
        if not self.chat_history_path.exists():
            return []
        try:
            lines = self.chat_history_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "").strip()
            text = str(row.get("text") or "")
            blocks = row.get("blocks")
            if not isinstance(blocks, list):
                blocks = []
            if not role:
                continue
            if not text.strip() and not blocks:
                continue
            out.append(
                {
                    "role": role,
                    "text": text,
                    "blocks": blocks,
                    "ts": row.get("ts") or "",
                }
            )
        return out

    def clear_chat_history(self) -> None:
        """Wipe the UI transcript file (e.g. user clear-scrollback)."""
        try:
            if self.chat_history_path.exists():
                self.chat_history_path.unlink()
        except OSError:
            pass

    def write_chat_history(self, rows: list[dict[str, Any]]) -> None:
        """Replace chat-history.jsonl with *rows* (oldest → newest)."""
        self.tui.mkdir(parents=True, exist_ok=True)
        if not rows:
            self.clear_chat_history()
            return
        lines: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "").strip()
            text = str(row.get("text") or "")
            blocks = row.get("blocks")
            if not role:
                continue
            if not text.strip() and not (
                isinstance(blocks, list) and blocks
            ):
                continue
            payload: dict[str, Any] = {
                "ts": row.get("ts") or utc_now(),
                "role": role,
            }
            if text.strip():
                payload["text"] = text
            if isinstance(blocks, list) and blocks:
                payload["blocks"] = blocks
            lines.append(json.dumps(payload, ensure_ascii=False))
        if not lines:
            self.clear_chat_history()
            return
        self.chat_history_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def list_user_rewind_points(
        self, limit: int = 400
    ) -> list[dict[str, Any]]:
        """User turns as rewind targets (oldest → newest).

        Each item: ``history_index`` (index into full chat history),
        ``user_ordinal`` (0-based among user rows), ``text``, ``ts``.
        Selecting a point rewinds to the state **before** that user message.
        """
        hist = self.read_chat_history(limit=limit)
        out: list[dict[str, Any]] = []
        user_i = 0
        for i, row in enumerate(hist):
            if str(row.get("role") or "") != "user":
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "history_index": i,
                    "user_ordinal": user_i,
                    "text": text,
                    "ts": str(row.get("ts") or ""),
                }
            )
            user_i += 1
        return out

    def truncate_chat_before_user(
        self, history_index: int, *, limit: int = 400
    ) -> list[dict[str, Any]]:
        """Keep messages strictly before *history_index*; rewrite disk.

        Returns the kept rows (may be empty).
        """
        hist = self.read_chat_history(limit=limit)
        idx = max(0, min(int(history_index), len(hist)))
        kept = hist[:idx]
        self.write_chat_history(kept)
        return kept

    def seed_chat_history_from_pi_agent(self, limit: int = 80) -> int:
        """One-shot import from Pi ``.pd/tui/pi-agent/*.jsonl`` when UI log is empty.

        Returns number of messages written. Skips if chat-history already exists.
        """
        if self.chat_history_path.exists() and self.chat_history_path.stat().st_size > 0:
            return 0
        session_dir = self.tui / "pi-agent"
        if not session_dir.is_dir():
            return 0
        files = sorted(
            session_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return 0
        path = files[0]
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0

        # Build durable turns: user text + assistant structured blocks (Grok-like).
        from room_tui.llm.agent_blocks import AgentTurn, blocks_from_pi_message

        turns: list[tuple[str, str, list[dict[str, Any]] | None]] = []
        pending_user: str | None = None
        pending_asst: AgentTurn | None = None

        def _flush_assistant() -> None:
            nonlocal pending_asst
            if pending_asst is None:
                return
            hist = pending_asst.to_history_blocks(collapse_thinking=True)
            body = pending_asst.assistant_text() or pending_asst.raw_text or ""
            if hist or body.strip():
                turns.append(("assistant", body, hist or None))
            pending_asst = None

        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("type") != "message":
                continue
            msg = row.get("message")
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "")
            content = msg.get("content")
            if role == "user":
                text = _pi_user_text(content)
                if not text:
                    continue
                if pending_user is not None:
                    turns.append(("user", pending_user, None))
                    _flush_assistant()
                pending_user = text
            elif role == "assistant":
                # Accumulate multi-assistant steps in one turn (tools + final text).
                blocks = blocks_from_pi_message(msg)
                if pending_asst is None:
                    pending_asst = AgentTurn(blocks=list(blocks))
                else:
                    pending_asst.blocks.extend(blocks)
                # Also capture plain text fallbacks
                for t in _pi_assistant_texts(content):
                    if t and not any(
                        b.kind in ("text", "plain", "markdown") and b.text == t
                        for b in pending_asst.blocks
                    ):
                        # tool-call assistant steps may only have short text
                        pass
                flat = "\n\n".join(_pi_assistant_texts(content)).strip()
                if flat:
                    pending_asst.raw_text = flat
            elif role == "toolResult":
                # Merge tool results into last open tool block when possible.
                if pending_asst is None:
                    continue
                from room_tui.llm.agent_blocks import apply_tool_execution

                tool_name = str(msg.get("toolName") or msg.get("name") or "tool")
                apply_tool_execution(
                    pending_asst.blocks,
                    tool_name=tool_name,
                    args=None,
                    result=content,
                    is_error=bool(msg.get("isError") or msg.get("is_error")),
                )

        if pending_user is not None:
            turns.append(("user", pending_user, None))
        _flush_assistant()

        if not turns:
            return 0
        if len(turns) > limit:
            turns = turns[-limit:]
        n = 0
        for role, text, blocks in turns:
            self.append_chat_message(role, text, blocks=blocks)
            n += 1
        return n


def _pi_user_text(content: Any) -> str:
    """Extract user text; unwrap Pi ``@file`` / ``<file name=…>`` wrappers."""
    raw = _pi_content_text(content)
    if not raw:
        return ""
    # Pi often wraps ``@path`` as: <file name="…">\nuser text\n</file>
    m = re.search(
        r"<file\b[^>]*>\s*(.*?)\s*</file>",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return raw.strip()


def _pi_assistant_texts(content: Any) -> list[str]:
    """Collect plain text parts from an assistant message (skip tools)."""
    if isinstance(content, str):
        t = content.strip()
        return [t] if t else []
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") != "text":
            continue
        t = str(part.get("text") or "").strip()
        if t:
            out.append(t)
    return out


def _pi_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and str(part.get("type") or "") == "text":
            parts.append(str(part.get("text") or ""))
        elif isinstance(part, str):
            parts.append(part)
    return "\n".join(parts)
