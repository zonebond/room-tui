"""paper-derived CLI adapter (subprocess)."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from room_tui.engine.errors import EngineError, humanize_engine_error


@dataclass
class PromptHandle:
    path: Path
    prompt_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # Large ``input register`` may emit multiple chunk prompts instead of one file.
    chunk_paths: list[Path] = field(default_factory=list)

    @property
    def all_prompt_files(self) -> list[Path]:
        if self.chunk_paths:
            return list(self.chunk_paths)
        return [self.path]

    @property
    def is_chunked(self) -> bool:
        return len(self.all_prompt_files) > 1


@dataclass
class SectionState:
    section_id: str
    status: str
    title: str = ""
    attempt_count: int = 0
    depends_on: list[str] = field(default_factory=list)
    level: int = 1
    number: str = ""


@dataclass
class SessionSnapshot:
    session_id: str
    template_id: str
    phase: str
    progress: str
    sections: list[SectionState]
    raw_status: dict[str, Any] = field(default_factory=dict)
    next_action: dict[str, Any] = field(default_factory=dict)


class EngineAdapter:
    def __init__(
        self,
        bin_path: str = "paper-derived",
        *,
        cwd: Path | None = None,
        timeout_s: float = 120.0,
    ):
        self.bin_path = bin_path
        self.cwd = cwd
        self.timeout_s = timeout_s

    # ── low-level ──────────────────────────────────────────────

    def run(
        self,
        args: list[str],
        *,
        timeout: float | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.bin_path, *args]
        try:
            from room_tui.config import engine_subprocess_env

            proc = subprocess.run(
                cmd,
                cwd=str(self.cwd) if self.cwd else None,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.timeout_s,
                env=engine_subprocess_env(),
            )
        except subprocess.TimeoutExpired as e:
            raise EngineError(
                f"engine timeout: {' '.join(cmd)}",
                cmd=cmd,
                stderr=str(e),
            ) from e
        except FileNotFoundError as e:
            raise EngineError(
                f"paper-derived not found: {self.bin_path}",
                cmd=cmd,
            ) from e
        if check and proc.returncode != 0:
            raw = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            # Keep structured dump on stderr/stdout; surface a human one-liner.
            nice = humanize_engine_error(
                f"engine failed ({proc.returncode}): {raw}",
                stderr=proc.stderr or "",
                stdout=proc.stdout or "",
            )
            raise EngineError(
                nice,
                cmd=cmd,
                returncode=proc.returncode,
                stderr=proc.stderr,
                stdout=proc.stdout,
            )
        return proc

    def run_json(self, args: list[str], **kwargs: Any) -> Any:
        proc = self.run(args, **kwargs)
        text = proc.stdout.strip()
        if not text:
            return None
        # engine sometimes prints a human line before/after JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # try last JSON object/array in output
            for m in re.finditer(r"(\{[\s\S]*\}|\[[\s\S]*\])", text):
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
            raise EngineError(
                humanize_engine_error(
                    f"engine stdout is not JSON: {text[:300]}",
                    stdout=text,
                ),
                cmd=[self.bin_path, *args],
                stdout=text,
            )

    # ── meta ───────────────────────────────────────────────────

    def version(self) -> dict[str, Any]:
        return self.run_json(["version"], timeout=max(self.timeout_s, 60))

    def template_list(self) -> list[dict[str, Any]]:
        """List registered templates.

        paper-derived sometimes prints a Chinese empty banner even with
        ``--json`` (e.g. ``(暂无已注册模板)``) instead of ``[]``. Exit code is
        still 0 — treat that as empty, not a hard failure.
        """
        try:
            data = self.run_json(
                ["template", "list", "--json"],
                timeout=max(self.timeout_s, 60),
            )
        except EngineError as e:
            # rc=0 but non-JSON: empty banner or human-only output
            if "is not JSON" in str(e) or "暂无" in f"{e.stdout or ''}{e}":
                return []
            raise
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("templates", "items", "data"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
        return []

    def template_show(self, template_id: str) -> dict[str, Any]:
        data = self.run_json(["template", "show", template_id], timeout=max(self.timeout_s, 60))
        return data if isinstance(data, dict) else {}

    def template_exists(self, template_id: str) -> bool:
        """True if template id is still in the engine registry."""
        tid = (template_id or "").strip()
        if not tid:
            return False
        # Prefer list (cheap, already handles empty-banner)
        try:
            rows = self.template_list()
            for t in rows:
                if str(t.get("id") or "") == tid:
                    return True
        except EngineError:
            pass
        # Fallback: show (may print human error to stdout)
        try:
            proc = self.run(
                ["template", "show", tid],
                timeout=max(self.timeout_s, 30),
                check=False,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                return False
            if "不存在" in out or "not found" in out.lower():
                return False
            # JSON object with matching id, or non-empty structured output
            text = (proc.stdout or "").strip()
            if not text:
                return False
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return str(data.get("id") or tid) == tid or bool(data.get("section_ids"))
            except json.JSONDecodeError:
                return "不存在" not in text
            return True
        except EngineError:
            return False

    def template_register_build(
        self,
        sample: Path,
        name: str,
        out: Path,
        *,
        description: str = "",
    ) -> PromptHandle:
        """Write template-register prompt file (LLM must fill response)."""
        out.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "template",
            "register",
            str(sample),
            "-n",
            name,
            "--prompt-file",
            str(out),
        ]
        if description.strip():
            args.extend(["-d", description.strip()])
        try:
            data = self.run_json(args, timeout=max(self.timeout_s, 120))
        except EngineError as e:
            raise EngineError(
                humanize_engine_error(
                    str(e),
                    stderr=e.stderr,
                    stdout=e.stdout,
                    sample_suffix=sample.suffix,
                ),
                cmd=e.cmd,
                returncode=e.returncode,
                stderr=e.stderr,
                stdout=e.stdout,
            ) from e
        tokens = None
        if isinstance(data, dict):
            tokens = data.get("prompt_tokens")
        return PromptHandle(path=out, prompt_tokens=tokens, raw=data or {})

    @staticmethod
    def stable_template_id(name: str) -> str:
        """User-facing name → stable template id.

        paper-derived's kebab for pure CJK collapses to ``template``; LLM often
        invents an English id from the *sample content* (same .doc → same id),
        so re-register or another machine that already has that sample-derived
        id fails with template_id_exists. Prefer the name the user typed.
        """
        import hashlib
        import re

        s = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip()).strip("-").lower()
        if not s or s == "template":
            h = hashlib.sha1((name or "tpl").encode("utf-8")).hexdigest()[:12]
            s = f"tpl-{h}"
        return s

    def _rewrite_register_response_id(self, response: Path, name: str) -> Path:
        """Force response JSON ``id`` to :meth:`stable_template_id` before parse."""
        text = response.read_text(encoding="utf-8")
        wanted = self.stable_template_id(name)
        data: Any = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # response may be prose + JSON
            for m in re.finditer(r"\{[\s\S]*\}", text):
                try:
                    data = json.loads(m.group(0))
                    break
                except json.JSONDecodeError:
                    continue
        if not isinstance(data, dict):
            return response
        old = str(data.get("id") or "")
        data["id"] = wanted
        # Keep human name as the user-provided label when present
        if name.strip():
            data["name"] = name.strip()
        out = response.with_suffix(response.suffix + ".room-id.json")
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    def template_register_parse(
        self,
        sample: Path,
        name: str,
        response: Path,
    ) -> dict[str, Any]:
        """Parse LLM response and persist template profile.

        Rewrites the LLM-proposed ``id`` to a stable id from *name* so the same
        sample document can be registered under different names, and so pure
        Chinese names do not all collapse to id ``template``.
        """
        parse_path = self._rewrite_register_response_id(response, name)
        try:
            data = self.run_json(
                [
                    "template",
                    "register",
                    str(sample),
                    "-n",
                    name,
                    "--parse",
                    str(parse_path),
                ],
                timeout=max(self.timeout_s, 120),
            )
        except EngineError as e:
            raise self._template_register_error(e, sample_suffix=sample.suffix) from e
        if isinstance(data, dict) and data.get("error"):
            # Some builds return error JSON with rc=0
            raise EngineError(
                self._format_template_register_err(data),
                stdout=json.dumps(data, ensure_ascii=False),
            )
        return data if isinstance(data, dict) else {"raw": data}

    @staticmethod
    def _format_template_register_err(data: dict[str, Any]) -> str:
        code = str(data.get("error") or "")
        msg = str(data.get("message") or "").strip()
        if code == "template_id_exists" or "已存在" in msg:
            eid = str(data.get("existing_id") or "")
            ename = str(data.get("existing_name") or "")
            base = msg or f"模板 id 已存在「{eid}」"
            hint = " · 可 /template delete " + (eid or "<id>") + " 后重试，或换个名称"
            return base + hint
        return msg or code or "模板写入失败"

    def _template_register_error(
        self, e: EngineError, *, sample_suffix: str = ""
    ) -> EngineError:
        """Lift structured engine errors (e.g. template_id_exists) out of wrappers."""
        blob = (e.stdout or e.stderr or str(e) or "").strip()
        # Prefer last JSON object in the dump
        for m in re.finditer(r"\{[\s\S]*\}", blob):
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and (data.get("error") or data.get("message")):
                return EngineError(
                    self._format_template_register_err(data),
                    cmd=e.cmd,
                    returncode=e.returncode,
                    stderr=e.stderr,
                    stdout=e.stdout or blob,
                )
        nice = humanize_engine_error(
            str(e),
            stderr=e.stderr,
            stdout=e.stdout,
            sample_suffix=sample_suffix,
        )
        return EngineError(
            nice,
            cmd=e.cmd,
            returncode=e.returncode,
            stderr=e.stderr,
            stdout=e.stdout,
        )

    def template_delete(self, template_id: str) -> dict[str, Any]:
        data = self.run_json(
            ["template", "delete", template_id],
            timeout=max(self.timeout_s, 60),
        )
        return data if isinstance(data, dict) else {}

    def template_register_auto(
        self,
        sample: Path,
        name: str,
        *,
        description: str = "",
        model: str = "",
        api_base: str = "",
        api_key: str = "",
        compact: bool = True,
        timeout_s: float = 600.0,
    ) -> dict[str, Any]:
        """Fast path: ``template register-auto`` (structure deterministic + 3 small LLM calls).

        Prefer when sample has clear numbered / markdown headings. Uses engine-side
        LLM (``--api-base`` / local), not Room's PiRunner build→parse loop.
        """
        args: list[str] = [
            "template",
            "register-auto",
            str(sample),
            "-n",
            name,
            "--progress",
        ]
        if description.strip():
            args.extend(["-d", description.strip()])
        if model.strip():
            args.extend(["-m", model.strip()])
        if api_base.strip():
            args.extend(["--api-base", api_base.strip()])
        if api_key.strip():
            args.extend(["--api-key", api_key.strip()])
        if compact:
            args.append("--compact")
        # Human progress on stderr; final JSON often last stdout line.
        result = self.run(
            args,
            timeout=max(timeout_s, self.timeout_s, 120),
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            raise EngineError(err[:2000] or "template register-auto failed")
        text = (result.stdout or "").strip()
        # Prefer last JSON object in stdout
        data: Any = None
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if data is None and text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"status": "ok", "stdout": text[-500:]}
        if not isinstance(data, dict):
            data = {"status": "ok", "stdout": text[-500:]}
        data.setdefault("_progress", (result.stderr or "")[-800:])
        return data

    # ── input ──────────────────────────────────────────────────

    def input_register_build(self, file: Path, name: str, out: Path) -> PromptHandle:
        """Build input-register prompt(s).

        Large docs may auto-chunk: engine writes ``out.stem.chunk-N.md`` and
        returns JSON ``{mode: chunked, prompt_files: [...]}`` instead of a
        single ``out`` file. Callers must run the worker once per chunk.
        """
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.run_json(
            [
                "input",
                "register",
                str(file),
                "-n",
                name,
                "--prompt-file",
                str(out),
            ]
        )
        tokens = None
        chunks: list[Path] = []
        if isinstance(data, dict):
            tokens = data.get("prompt_tokens") or data.get("total_prompt_tokens")
            mode = str(data.get("mode") or "")
            raw_files = data.get("prompt_files") or []
            if mode == "chunked" or raw_files:
                for p in raw_files:
                    pp = Path(str(p))
                    if pp.exists():
                        chunks.append(pp)
                if not chunks:
                    # Infer from naming convention: stem.chunk-0.md …
                    chunks = sorted(out.parent.glob(f"{out.stem}.chunk-*.md"))
        if not chunks and out.exists():
            chunks = [out]
        elif not chunks:
            chunks = sorted(out.parent.glob(f"{out.stem}.chunk-*.md"))
        if not chunks:
            raise EngineError(
                f"input register produced no prompt file for {file.name}",
                stdout=str(data)[:500] if data else "",
            )
        primary = chunks[0] if chunks else out
        return PromptHandle(
            path=primary,
            prompt_tokens=tokens if isinstance(tokens, int) else None,
            raw=data if isinstance(data, dict) else {},
            chunk_paths=chunks,
        )

    def input_register_parse(
        self, file: Path, name: str, response: Path, asset_out: Path
    ) -> dict[str, Any]:
        asset_out.parent.mkdir(parents=True, exist_ok=True)
        data = self.run_json(
            [
                "input",
                "register",
                str(file),
                "-n",
                name,
                "--parse",
                str(response),
                "-O",
                str(asset_out),
            ]
        )
        return data if isinstance(data, dict) else {"status": "ok", "raw": data}

    def input_register_parse_chunks(
        self,
        file: Path,
        name: str,
        responses: list[Path],
        asset_out: Path,
    ) -> dict[str, Any]:
        """Merge multi-chunk LLM responses into one InputAsset JSON."""
        asset_out.parent.mkdir(parents=True, exist_ok=True)
        if not responses:
            raise EngineError("input register parse-chunks: no response files")
        if len(responses) == 1:
            return self.input_register_parse(file, name, responses[0], asset_out)
        args: list[str] = [
            "input",
            "register",
            str(file),
            "-n",
            name,
            "-O",
            str(asset_out),
        ]
        for r in responses:
            args.extend(["--parse-chunks", str(r)])
        data = self.run_json(args, timeout=max(self.timeout_s, 180))
        return data if isinstance(data, dict) else {"status": "ok", "raw": data}

    # ── session ────────────────────────────────────────────────

    def session_init(
        self,
        template_id: str,
        *,
        budget: int = 40000,
        output: str = "",
        fmt: str = "md",
    ) -> dict[str, Any]:
        args = ["session", "init", "-t", template_id, "--budget", str(budget)]
        if output:
            args += ["-O", output]
        if fmt:
            args += ["-f", fmt]
        data = self.run_json(args)
        if not isinstance(data, dict) or "session_id" not in data:
            raise EngineError(f"session init unexpected: {data}")
        return data

    def session_feed_build(
        self, session_id: str, asset_files: list[Path], out: Path
    ) -> PromptHandle:
        out.parent.mkdir(parents=True, exist_ok=True)
        args = ["session", "feed", "-s", session_id, "--prompt-file", str(out)]
        for a in asset_files:
            args += ["-i", str(a)]
        data = self.run_json(args)
        tokens = data.get("prompt_tokens") if isinstance(data, dict) else None
        return PromptHandle(path=out, prompt_tokens=tokens, raw=data or {})

    def session_feed_parse(
        self, session_id: str, asset_files: list[Path], response: Path
    ) -> dict[str, Any]:
        args = ["session", "feed", "-s", session_id, "--parse", str(response)]
        for a in asset_files:
            args += ["-i", str(a)]
        data = self.run_json(args)
        return data if isinstance(data, dict) else {"raw": data}

    def session_next(self, session_id: str) -> dict[str, Any]:
        data = self.run_json(["session", "next", "-s", session_id])
        return data if isinstance(data, dict) else {"action": "unknown", "raw": data}

    def session_store_dir(self, session_id: str) -> Path | None:
        """Resolve paper-derived session directory for *session_id*.

        Engine stores under (see paper_derived.session_store._sessions_root):
          1. ``{project}/.pd/sessions/{id}/`` when cwd walks up to a .pd / .git root
          2. ``~/.paper-derived/sessions/{id}/`` as fallback

        Prefer an existing ``session.json``; otherwise return the first
        project-local candidate (for writes that create the file).
        """
        sid = (session_id or "").strip()
        if not sid:
            return None
        candidates: list[Path] = []
        if self.cwd is not None:
            root = Path(self.cwd).resolve()
            candidates.append(root / ".pd" / "sessions" / sid)
            cur = root
            for _ in range(12):
                if (cur / ".pd").is_dir() or (cur / ".git").exists():
                    p = cur / ".pd" / "sessions" / sid
                    if p not in candidates:
                        candidates.append(p)
                    break
                parent = cur.parent
                if parent == cur:
                    break
                cur = parent
        candidates.append(Path.home() / ".paper-derived" / "sessions" / sid)
        for d in candidates:
            if (d / "session.json").is_file():
                return d
        return candidates[0] if candidates else None

    def session_json_path(self, session_id: str) -> Path | None:
        d = self.session_store_dir(session_id)
        return (d / "session.json") if d is not None else None

    def reclaim_generating(
        self,
        session_id: str,
        section_ids: list[str] | None = None,
        *,
        to_status: str = "ready",
    ) -> list[str]:
        """Reset orphaned ``generating`` sections so session next can proceed.

        Cancel/crash mid-generate leaves status=generating with no parse pending.
        The engine then returns action=wait forever. Room reclaims those rows.

        **Must** use the same session store as paper-derived (project ``.pd/sessions``
        preferred) — historically only ``~/.paper-derived`` was checked, so
        reclaim silently no-oped and ``/continue`` hard-failed after ~15s.
        """
        path = self.session_json_path(session_id)
        if path is None or not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        sp = data.get("section_progress") or {}
        if not isinstance(sp, dict):
            return []
        want = set(section_ids) if section_ids else None
        reclaimed: list[str] = []
        for sid, meta in sp.items():
            if not isinstance(meta, dict):
                continue
            if want is not None and sid not in want:
                continue
            if str(meta.get("status") or "") != "generating":
                continue
            meta["status"] = to_status
            reclaimed.append(sid)
        if not reclaimed:
            return []
        from datetime import datetime, timezone

        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return reclaimed

    def session_prompt_build(
        self, session_id: str, section_id: str, out: Path
    ) -> PromptHandle:
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.run_json(
            [
                "session",
                "prompt",
                "-s",
                session_id,
                "--section",
                section_id,
                "--prompt-file",
                str(out),
            ]
        )
        tokens = data.get("prompt_tokens") if isinstance(data, dict) else None
        return PromptHandle(path=out, prompt_tokens=tokens, raw=data or {})

    def session_prompt_parse(
        self, session_id: str, section_id: str, response: Path
    ) -> dict[str, Any]:
        data = self.run_json(
            [
                "session",
                "prompt",
                "-s",
                session_id,
                "--section",
                section_id,
                "--parse",
                str(response),
            ]
        )
        return data if isinstance(data, dict) else {"raw": data}

    def session_summarize_build(
        self, session_id: str, section_id: str, out: Path
    ) -> PromptHandle:
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.run_json(
            [
                "session",
                "summarize",
                "-s",
                session_id,
                "--section",
                section_id,
                "--prompt-file",
                str(out),
            ]
        )
        tokens = data.get("prompt_tokens") if isinstance(data, dict) else None
        return PromptHandle(path=out, prompt_tokens=tokens, raw=data or {})

    def session_summarize_parse(
        self, session_id: str, section_id: str, response: Path
    ) -> dict[str, Any]:
        data = self.run_json(
            [
                "session",
                "summarize",
                "-s",
                session_id,
                "--section",
                section_id,
                "--parse",
                str(response),
            ]
        )
        return data if isinstance(data, dict) else {"raw": data}

    def session_assemble(
        self, session_id: str, *, output: str, fmt: str = "md"
    ) -> str:
        args = ["session", "assemble", "-s", session_id, "-O", output]
        if fmt:
            args += ["-f", fmt]
        proc = self.run(args)
        return proc.stdout.strip() or output

    def session_status(self, session_id: str) -> dict[str, Any]:
        data = self.run_json(["session", "status", "-s", session_id])
        return data if isinstance(data, dict) else {}

    def session_list(self) -> list[dict[str, Any]]:
        data = self.run_json(["session", "list"], timeout=max(self.timeout_s, 60))
        if isinstance(data, list):
            return data
        return []

    # ── rich snapshot for TUI tree ─────────────────────────────

    def session_snapshot(self, session_id: str) -> SessionSnapshot:
        status = self.session_status(session_id)
        nxt = self.session_next(session_id)
        template_id = str(status.get("template_id") or "")
        titles, levels = self._section_titles(session_id)
        progress_map = self._load_section_progress(session_id)
        sections: list[SectionState] = []

        # prefer ordered ids from template
        ordered_ids: list[str] = []
        if template_id:
            try:
                t = self.template_show(template_id)
                ordered_ids = list(t.get("section_ids") or [])
            except EngineError:
                ordered_ids = []
        if not ordered_ids:
            ordered_ids = list(progress_map.keys()) or list(titles.keys())

        for sid in ordered_ids:
            meta = progress_map.get(sid, {})
            st = str(meta.get("status") or self._infer_status(sid, status, nxt))
            sections.append(
                SectionState(
                    section_id=sid,
                    status=st,
                    title=titles.get(sid, sid),
                    attempt_count=int(meta.get("attempt_count") or 0),
                    depends_on=list(meta.get("depends_on") or []),
                    level=int(meta.get("level") or levels.get(sid, 1)),
                    number=str(meta.get("number") or ""),
                )
            )

        return SessionSnapshot(
            session_id=session_id,
            template_id=template_id,
            phase=str(status.get("phase") or ""),
            progress=str(status.get("progress") or ""),
            sections=sections,
            raw_status=status,
            next_action=nxt,
        )

    def _infer_status(
        self, sid: str, status: dict[str, Any], nxt: dict[str, Any]
    ) -> str:
        ready = set(status.get("ready_for_generation") or [])
        if sid in ready:
            return "ready"
        batch = nxt.get("parallel_batch") or []
        if sid == nxt.get("section_id") or sid in batch:
            return "ready"
        return "pending"

    def _load_section_progress(self, session_id: str) -> dict[str, dict[str, Any]]:
        """Read engine session store for per-section status (CLI status is aggregate-only)."""
        path = self.session_json_path(session_id)
        if path is None or not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        sp = data.get("section_progress") or {}
        if isinstance(sp, dict):
            return {k: v for k, v in sp.items() if isinstance(v, dict)}
        return {}

    def _section_titles(
        self, session_id: str
    ) -> tuple[dict[str, str], dict[str, int]]:
        titles: dict[str, str] = {}
        levels: dict[str, int] = {}
        store = self.session_store_dir(session_id)
        doc_path = (store / "document.json") if store is not None else None
        if doc_path is not None and doc_path.exists():
            try:
                doc = json.loads(doc_path.read_text(encoding="utf-8"))
                self._walk_doc_sections(doc.get("sections") or [], titles, levels)
            except (OSError, json.JSONDecodeError):
                pass
        return titles, levels

    def _walk_doc_sections(
        self,
        nodes: list[Any],
        titles: dict[str, str],
        levels: dict[str, int],
        level: int = 1,
    ) -> None:
        for n in nodes:
            if not isinstance(n, dict):
                continue
            sid = str(n.get("id") or n.get("section_id") or "")
            if sid:
                titles[sid] = str(n.get("title") or sid)
                levels[sid] = int(n.get("level") or level)
            children = n.get("children") or []
            if children:
                self._walk_doc_sections(children, titles, levels, level + 1)
