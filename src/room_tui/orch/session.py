"""Session-driven orchestrator: engine build/parse + Pi worker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from room_tui import __version__
from room_tui.config import AppConfig
from room_tui.engine.adapter import EngineAdapter, SessionSnapshot
from room_tui.engine.errors import EngineError
from room_tui.llm.pi_runner import PiRunner, WorkerRequest
from room_tui.workspace import RunManifest, Workspace

EventCb = Callable[[dict[str, Any]], None]


@dataclass
class RunSpec:
    workspace: Path
    template_id: str
    inputs: list[Path]
    output: Path
    session_id: str = ""
    budget: int | None = None


@dataclass
class OrchState:
    phase: str = "idle"
    session_id: str = ""
    focus_section: str = ""
    card_status: str = "IDLE"
    card_detail: str = ""
    card_peek: str = ""
    model: str = ""
    provider: str = ""
    attempt: int = 0
    progress: str = ""
    error: str = ""
    running: bool = False
    paused: bool = False
    snapshot: SessionSnapshot | None = None
    last_events: list[dict[str, Any]] = field(default_factory=list)


class SessionOrchestrator:
    def __init__(
        self,
        cfg: AppConfig,
        engine: EngineAdapter | None = None,
        runner: PiRunner | None = None,
    ):
        self.cfg = cfg
        self.engine = engine or EngineAdapter(
            cfg.paper_derived_bin, timeout_s=cfg.engine_timeout_s
        )
        self.runner = runner or PiRunner(cfg.pi_bin)
        self.state = OrchState()
        self._cancel = asyncio.Event()
        self._pause = asyncio.Event()
        self._listeners: list[EventCb] = []

    def subscribe(self, cb: EventCb) -> None:
        self._listeners.append(cb)

    def emit(self, event: dict[str, Any]) -> None:
        self.state.last_events.append(event)
        self.state.last_events = self.state.last_events[-100:]
        for cb in self._listeners:
            try:
                cb(event)
            except Exception:
                pass

    def request_cancel(self) -> None:
        self._cancel.set()

    def request_pause(self) -> None:
        self._pause.set()
        self.state.paused = True

    def clear_pause(self) -> None:
        self._pause.clear()
        self.state.paused = False

    async def refresh_snapshot(self, session_id: str | None = None) -> SessionSnapshot:
        sid = session_id or self.state.session_id
        snap = await asyncio.to_thread(self.engine.session_snapshot, sid)
        self.state.snapshot = snap
        self.state.progress = snap.progress
        self.state.session_id = snap.session_id
        # Once finished/cancelled/failed, never regress phase from engine lag
        # (engine often still reports generating/assembling after assemble).
        terminal = {"complete", "done", "cancelled", "canceled", "failed", "error"}
        if self.state.phase not in terminal:
            eng = str(snap.phase or "").lower()
            if self.state.phase == "assembling" and eng not in terminal:
                pass  # keep assembling until run_complete
            elif eng:
                self.state.phase = snap.phase
        self.emit(
            {
                "type": "snapshot",
                "progress": snap.progress,
                "phase": self.state.phase or snap.phase,
            }
        )
        return snap

    def _sync_ws_progress(self, ws: Workspace, sid: str) -> None:
        """Best-effort: mirror engine progress into project .pd/tui manifest."""
        try:
            st = self.engine.session_status(sid)
            ws.update_manifest_progress(
                session_id=sid,
                phase=str(st.get("phase") or ""),
                progress=str(st.get("progress") or ""),
                status="complete"
                if st.get("phase") in ("complete", "done")
                else "running",
            )
        except Exception:
            pass

    def _reset_ui_session_state(self, *, keep_session_id: str = "") -> None:
        """Drop previous-run snapshot/progress so sidebar cannot show stale大纲.

        Called at the start of a brand-new ``/new`` run (no resume session).
        """
        sid = (keep_session_id or "").strip()
        self.state.snapshot = None
        self.state.progress = ""
        self.state.focus_section = ""
        self.state.phase = "init"
        self.state.card_status = "RUNNING"
        self.state.card_detail = ""
        self.state.card_peek = ""
        self.state.error = ""
        self.state.session_id = sid

    async def run(self, spec: RunSpec) -> str:
        """Full session pipeline. Returns output path."""
        self._cancel.clear()
        self.clear_pause()
        self.state.running = True
        self.state.error = ""

        ws = Workspace(spec.workspace)
        ws.ensure()
        self.engine.cwd = ws.root

        # Brand-new generation: wipe last run's snapshot (otherwise step events
        # re-paint the old 大纲 with all ✓ while 注册资料 is still running).
        if not (spec.session_id or "").strip():
            self._reset_ui_session_state()

        # Project-local manifest: this directory's only "current task"
        if spec.session_id:
            existing = ws.load_manifest()
            if existing is None:
                manifest = RunManifest(
                    client_version=__version__,
                    template_id=spec.template_id,
                    session_id=spec.session_id,
                    workspace=str(ws.root),
                    output=str(spec.output),
                    budget=spec.budget or self.cfg.budget,
                    provider=self.cfg.provider,
                    model=self.cfg.model,
                    status="running",
                )
            else:
                manifest = existing
                manifest.session_id = spec.session_id
                manifest.output = str(spec.output)
                manifest.status = "running"
                manifest.provider = self.cfg.provider
                manifest.model = self.cfg.model
                if spec.template_id:
                    manifest.template_id = spec.template_id
        else:
            manifest = RunManifest(
                client_version=__version__,
                template_id=spec.template_id,
                workspace=str(ws.root),
                output=str(spec.output),
                budget=spec.budget or self.cfg.budget,
                provider=self.cfg.provider,
                model=self.cfg.model,
                inputs=[str(p) for p in spec.inputs],
                status="running",
                session_id="",
            )
        ws.save_manifest(manifest)
        run_tpl = spec.template_id or manifest.template_id
        ws.append_event({"type": "run_start", "template": run_tpl})
        # Must emit so sidebar switches to document pipeline (was only on disk)
        self.emit({"type": "run_start", "template": run_tpl})

        try:
            if spec.session_id:
                sid = spec.session_id
                self.state.session_id = sid
                self.state.phase = "resume"
                self.emit({"type": "resume", "session_id": sid})
                ws.update_manifest_progress(session_id=sid, status="running", phase="generating")
            else:
                init = await asyncio.to_thread(
                    self.engine.session_init,
                    spec.template_id,
                    budget=spec.budget or self.cfg.budget,
                    output=str(spec.output),
                    fmt=spec.output.suffix.lstrip(".") or "md",
                )
                sid = str(init["session_id"])
                self.state.session_id = sid
                total = init.get("total_sections", "?")
                self.state.progress = f"0/{total}"
                self.state.phase = str(init.get("phase") or "init")
                # Ensure no leftover snapshot from a previous complete run.
                self.state.snapshot = None
                self.state.focus_section = ""
                manifest.session_id = sid
                manifest.phase = str(init.get("phase") or "init")
                manifest.progress = f"0/{total}"
                ws.save_manifest(manifest)
                self.emit({"type": "session_init", "session_id": sid, **init})

                # register + feed each input
                for i, path in enumerate(spec.inputs):
                    if self._cancel.is_set():
                        raise RuntimeError("cancelled")
                    await self._register_and_feed(ws, sid, path, i)

            # generation loop
            await self._generation_loop(ws, sid, spec.output)

            manifest = ws.load_manifest() or manifest
            manifest.status = "complete"
            manifest.phase = "complete"
            ws.save_manifest(manifest)
            self.state.phase = "complete"
            self.state.card_status = "COMPLETE"
            self.state.card_detail = f"output → {spec.output}"
            self.emit({"type": "run_complete", "output": str(spec.output)})
            return str(spec.output)
        except Exception as e:
            manifest = ws.load_manifest() or manifest
            err = str(e)
            cancelled = self._cancel.is_set() or "cancel" in err.lower()
            # Always try to unstick generating sections so /continue can resume.
            try:
                sid_fix = (
                    self.state.session_id
                    or (manifest.session_id if manifest else "")
                    or ""
                ).strip()
                if sid_fix:
                    self.engine.reclaim_generating(sid_fix)
            except Exception:
                pass
            if cancelled:
                manifest.status = "cancelled"
                self.state.error = "cancelled"
                self.state.card_status = "CANCELLED"
                self.state.card_detail = "user cancelled"
                self.state.phase = "cancelled"
                ws.save_manifest(manifest)
                ws.append_event({"type": "run_cancelled", "error": "cancelled"})
                self.emit({"type": "run_cancelled"})
            else:
                manifest.status = "failed"
                self.state.error = err
                self.state.card_status = "FAILED"
                self.state.card_detail = err[:200]
                ws.save_manifest(manifest)
                ws.append_event({"type": "run_failed", "error": err})
                self.emit({"type": "run_failed", "error": err})
            raise
        finally:
            self.state.running = False

    async def _register_and_feed(
        self, ws: Workspace, sid: str, path: Path, index: int
    ) -> None:
        name = path.stem or f"input-{index}"
        key = f"reg-{index}-{name}"
        # Title/sidebar: "注册资料" while registering, "喂入中" only during feed
        self.state.phase = "registering"
        self.state.focus_section = key
        self.state.card_status = "RUNNING"
        self.state.card_detail = f"register input: {path.name}"
        self.emit({"type": "step_start", "key": key, "kind": "input_register"})

        prompt_out = ws.prompt_path(key)
        asset = ws.asset_path(name)

        handle = await asyncio.to_thread(
            self.engine.input_register_build, path, name, prompt_out
        )
        prompt_files = handle.all_prompt_files
        n_chunks = len(prompt_files)
        response_files: list[Path] = []

        for ci, pfile in enumerate(prompt_files):
            if self._cancel.is_set():
                raise RuntimeError("cancelled")
            ckey = key if n_chunks == 1 else f"{key}-c{ci}"
            rfile = ws.response_path(ckey)
            if n_chunks > 1:
                self.state.card_detail = (
                    f"register input: {path.name}  ·  分块 {ci + 1}/{n_chunks}"
                )
                self.state.phase = "registering"
                self.emit(
                    {
                        "type": "step_start",
                        "key": ckey,
                        "kind": "input_register",
                        "chunk": ci + 1,
                        "chunks": n_chunks,
                        "file": path.name,
                    }
                )
            ok = await self._worker_step(ckey, pfile, rfile, tier="default")
            if not ok:
                detail = (self.state.error or "worker failed").strip()
                # User-facing: no raw worker host brand
                raise RuntimeError(
                    f"资料注册失败  {path.name}"
                    + (f"  ·  分块 {ci + 1}/{n_chunks}" if n_chunks > 1 else "")
                    + (f"  ·  {detail[:160]}" if detail else "")
                )
            response_files.append(rfile)
            if n_chunks > 1:
                ev = {
                    "type": "step_ok",
                    "key": ckey,
                    "kind": "input_register",
                    "chunk": ci + 1,
                    "chunks": n_chunks,
                    "file": path.name,
                }
                ws.append_event(ev)
                # Do not advance pipeline on mid-file chunks — only final reg ok
                self.emit({**ev, "partial": True})

        if n_chunks > 1:
            await asyncio.to_thread(
                self.engine.input_register_parse_chunks,
                path,
                name,
                response_files,
                asset,
            )
        else:
            await asyncio.to_thread(
                self.engine.input_register_parse,
                path,
                name,
                response_files[0],
                asset,
            )
        ws.append_event({"type": "step_ok", "key": key, "kind": "input_register"})
        self.emit({"type": "step_ok", "key": key, "kind": "input_register"})

        fkey = f"feed-{index}-{name}"
        self.state.phase = "feeding"
        self.state.card_detail = f"feed: {name}"
        self.emit({"type": "step_start", "key": fkey, "kind": "session_feed"})
        fprompt = ws.prompt_path(fkey)
        fresp = ws.response_path(fkey)
        await asyncio.to_thread(
            self.engine.session_feed_build, sid, [asset], fprompt
        )
        ok = await self._worker_step(fkey, fprompt, fresp, tier="default")
        if not ok:
            detail = (self.state.error or "worker failed").strip()
            raise RuntimeError(
                f"资料注入失败  {path.name}"
                + (f"  ·  {detail[:160]}" if detail else "")
            )
        await asyncio.to_thread(
            self.engine.session_feed_parse, sid, [asset], fresp
        )
        ws.append_event({"type": "step_ok", "key": fkey, "kind": "session_feed"})
        self.emit({"type": "step_ok", "key": fkey, "kind": "session_feed"})
        await self.refresh_snapshot(sid)

    async def _reclaim_orphans(
        self, sid: str, section_ids: list[str] | None = None
    ) -> list[str]:
        """Reset orphaned ``generating`` sections (cancel/crash mid-step)."""
        try:
            reclaimed = await asyncio.to_thread(
                self.engine.reclaim_generating, sid, section_ids
            )
        except Exception:
            return []
        if reclaimed:
            self.emit(
                {
                    "type": "session_reclaim",
                    "sections": reclaimed,
                    "to": "ready",
                }
            )
            try:
                await self.refresh_snapshot(sid)
            except Exception:
                pass
        return list(reclaimed or [])

    async def _generation_loop(self, ws: Workspace, sid: str, output: Path) -> None:
        self.state.phase = "generating"
        # Orphaned generating sections (cancel/crash mid-step) make engine
        # return action=wait forever. Reclaim when wait appears; retry a few times.
        wait_reclaim_rounds = 0
        wait_ticks = 0
        while True:
            if self._cancel.is_set():
                # Leave session resumable: stuck generating → ready
                await self._reclaim_orphans(sid)
                raise RuntimeError("cancelled")
            if self._pause.is_set():
                self.state.card_status = "PAUSED"
                self.state.card_detail = "paused after current step"
                self.emit({"type": "paused"})
                while self._pause.is_set() and not self._cancel.is_set():
                    await asyncio.sleep(0.2)
                if self._cancel.is_set():
                    await self._reclaim_orphans(sid)
                    raise RuntimeError("cancelled")

            nxt = await asyncio.to_thread(self.engine.session_next, sid)
            action = str(nxt.get("action") or "")
            self.emit({"type": "session_next", **nxt})

            if action == "assemble":
                self.state.card_status = "ASSEMBLING"
                self.state.card_detail = "assembling document"
                self.state.phase = "assembling"
                self.emit(
                    {
                        "type": "step_start",
                        "key": "assemble",
                        "kind": "assemble",
                        "output": str(output),
                    }
                )
                self.emit({"type": "session_next", "action": "assemble", "phase": "assembling"})
                await asyncio.to_thread(
                    self.engine.session_assemble,
                    sid,
                    output=str(output),
                    fmt=output.suffix.lstrip(".") or "md",
                )
                self.emit(
                    {
                        "type": "step_ok",
                        "key": "assemble",
                        "kind": "assemble",
                        "output": str(output),
                    }
                )
                # Snapshot for chapters only — do not let engine lag clobber
                # our terminal phase before run_complete marks complete.
                snap = await asyncio.to_thread(self.engine.session_snapshot, sid)
                self.state.snapshot = snap
                self.state.progress = snap.progress or self.state.progress
                self.state.session_id = snap.session_id or sid
                self.state.phase = "assembling"
                self.emit(
                    {
                        "type": "snapshot",
                        "progress": self.state.progress,
                        "phase": "assembling",
                    }
                )
                return

            if action == "feed_more":
                self.state.card_status = "FEED_MORE"
                pending = nxt.get("pending_sections") or []
                self.state.card_detail = f"need more input: {pending[:5]}"
                self.emit({"type": "gate", "gate": "feed_more", **nxt})
                # M0: stop for user — resume later
                raise RuntimeError(
                    "session needs more input (feed_more). "
                    "Add assets and resume this session."
                )

            if action == "wait":
                in_progress = [
                    str(x) for x in (nxt.get("in_progress") or []) if x
                ]
                msg = str(nxt.get("message") or "waiting for in-progress sections")
                self.state.card_status = "WAITING"
                self.state.card_detail = msg
                self.emit(
                    {
                        "type": "session_wait",
                        "message": msg,
                        "in_progress": in_progress,
                        **{k: v for k, v in nxt.items() if k not in ("type",)},
                    }
                )
                # No local worker owns these — reclaim orphaned generating rows.
                # Retry a few rounds: wrong session path used to make reclaim a
                # permanent no-op; also a second pass helps after partial fixes.
                if in_progress and wait_reclaim_rounds < 3:
                    reclaimed = await self._reclaim_orphans(sid, in_progress)
                    wait_reclaim_rounds += 1
                    if reclaimed:
                        wait_ticks = 0
                        continue
                wait_ticks += 1
                # ~15s of pure wait after reclaim attempts → hard fail
                if wait_ticks >= 30:
                    stuck = ", ".join(in_progress[:8]) + (
                        "…" if len(in_progress) > 8 else ""
                    )
                    raise RuntimeError(
                        "session stuck waiting for sections that never complete: "
                        + stuck
                        + "  ·  可再试 /continue；若仍失败请 /new 重新开始"
                    )
                await asyncio.sleep(0.5)
                continue

            if action == "generate":
                wait_ticks = 0
                sections = list(nxt.get("parallel_batch") or [])
                if not sections and nxt.get("section_id"):
                    sections = [str(nxt["section_id"])]
                # M0: serial for stability
                for section_id in sections[: max(1, self.cfg.parallel)]:
                    await self._generate_section(ws, sid, str(section_id))
                await self.refresh_snapshot(sid)
                self._sync_ws_progress(ws, sid)
                continue

            raise RuntimeError(f"unknown session next action: {action} ({nxt})")

    async def _generate_section(self, ws: Workspace, sid: str, section_id: str) -> None:
        key = f"sec-{section_id}"
        self.state.focus_section = section_id
        self.state.card_status = "RUNNING"
        self.state.card_detail = f"generating {section_id}"
        self.state.card_peek = ""
        attempts = 0
        last_err = ""

        while attempts < self.cfg.max_attempts:
            if self._cancel.is_set():
                raise RuntimeError("cancelled")
            attempts += 1
            self.state.attempt = attempts
            tier = "default" if attempts < self.cfg.max_attempts else "strong"
            self.emit(
                {
                    "type": "step_start",
                    "key": key,
                    "kind": "session_prompt",
                    "section": section_id,
                    "attempt": attempts,
                }
            )
            prompt = ws.prompt_path(key)
            response = ws.response_path(f"{key}-a{attempts}")
            try:
                # Phase cues so UI never looks frozen during silent engine work.
                self.emit(
                    {
                        "type": "step_phase",
                        "key": key,
                        "section": section_id,
                        "phase": "build_prompt",
                    }
                )
                handle = await asyncio.to_thread(
                    self.engine.session_prompt_build, sid, section_id, prompt
                )
                if handle.prompt_tokens:
                    self.state.card_detail = (
                        f"generating {section_id} · ~{handle.prompt_tokens} tok"
                    )
                    self.emit(
                        {
                            "type": "step_phase",
                            "key": key,
                            "section": section_id,
                            "phase": "build_prompt",
                            "detail": f"~{handle.prompt_tokens} tok",
                        }
                    )
            except EngineError as e:
                last_err = str(e)
                continue

            self.emit(
                {
                    "type": "step_phase",
                    "key": key,
                    "section": section_id,
                    "phase": "llm",
                }
            )
            ok = await self._worker_step(key, prompt, response, tier=tier)
            if not ok:
                last_err = self.state.error or "worker failed"
                # Do not retry cancellation — abort the whole run immediately.
                if self._cancel.is_set() or "cancelled" in (last_err or "").lower():
                    raise RuntimeError("cancelled")
                continue
            if self._cancel.is_set():
                raise RuntimeError("cancelled")
            try:
                self.emit(
                    {
                        "type": "step_phase",
                        "key": key,
                        "section": section_id,
                        "phase": "parse",
                    }
                )
                result = await asyncio.to_thread(
                    self.engine.session_prompt_parse, sid, section_id, response
                )
                self.emit(
                    {
                        "type": "step_ok",
                        "key": key,
                        "kind": "session_prompt",
                        "section": section_id,
                        "result": {
                            k: result.get(k)
                            for k in ("status", "progress", "section_id", "all_done")
                            if k in result
                        },
                    }
                )
                ws.append_event(
                    {"type": "step_ok", "key": key, "section": section_id, "attempt": attempts}
                )
                # summarize
                if self.cfg.summarize:
                    await self._summarize_section(ws, sid, section_id)
                self.state.card_status = "DONE"
                self.state.card_detail = f"{section_id} done"
                return
            except EngineError as e:
                last_err = str(e)
                self.emit(
                    {
                        "type": "step_error",
                        "key": key,
                        "section": section_id,
                        "attempt": attempts,
                        "error": last_err,
                    }
                )

        self.state.card_status = "NEEDS_DECISION"
        self.state.card_detail = f"{section_id} failed after {attempts}: {last_err[:120]}"
        raise RuntimeError(
            f"section {section_id} failed after {attempts} attempts: {last_err}"
        )

    async def _summarize_section(self, ws: Workspace, sid: str, section_id: str) -> None:
        key = f"sum-{section_id}"
        self.emit(
            {
                "type": "step_start",
                "key": key,
                "kind": "summarize",
                "section": section_id,
            }
        )
        try:
            prompt = ws.prompt_path(key)
            response = ws.response_path(key)
            self.emit(
                {
                    "type": "step_phase",
                    "key": key,
                    "section": section_id,
                    "phase": "summarize_build",
                }
            )
            await asyncio.to_thread(
                self.engine.session_summarize_build, sid, section_id, prompt
            )
            if self._cancel.is_set():
                return
            self.emit(
                {
                    "type": "step_phase",
                    "key": key,
                    "section": section_id,
                    "phase": "summarize_llm",
                }
            )
            ok = await self._worker_step(key, prompt, response, tier="fast")
            if self._cancel.is_set() or (
                not ok and "cancelled" in (self.state.error or "").lower()
            ):
                return
            if ok:
                self.emit(
                    {
                        "type": "step_phase",
                        "key": key,
                        "section": section_id,
                        "phase": "summarize_parse",
                    }
                )
                await asyncio.to_thread(
                    self.engine.session_summarize_parse, sid, section_id, response
                )
                self.emit(
                    {
                        "type": "step_ok",
                        "key": key,
                        "kind": "summarize",
                        "section": section_id,
                    }
                )
        except Exception as e:
            # summarize is best-effort
            self.emit(
                {
                    "type": "step_warn",
                    "key": key,
                    "section": section_id,
                    "error": str(e),
                }
            )

    async def _worker_step(
        self, key: str, prompt: Path, response: Path, *, tier: str
    ) -> bool:
        tcfg = self.cfg.tier(tier)
        self.state.provider = tcfg.provider
        self.state.model = tcfg.model

        def on_ev(ev: dict[str, Any]) -> None:
            if ev.get("type") == "worker_progress" and ev.get("peek"):
                self.state.card_peek = str(ev["peek"])
            self.emit(ev)
            # also persist light events
            # (full file log done by caller workspace when available)

        result = await self.runner.execute(
            WorkerRequest(
                key=key,
                prompt_file=prompt,
                response_file=response,
                tier=tcfg,
                timeout_s=self.cfg.worker_timeout_s,
            ),
            on_event=on_ev,
            cancel=self._cancel,
        )
        if not result.ok:
            self.state.error = result.error or "worker failed"
            return False
        self.state.card_peek = result.peek
        self.state.error = ""
        return True
