"""CLI: doctor / run / resume / tui."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import click

from room_tui import __version__
from room_tui.config import AppConfig, load_config
from room_tui.engine.adapter import EngineAdapter
from room_tui.engine.errors import EngineError
from room_tui.orch.session import RunSpec, SessionOrchestrator


def _apply_common(
    cfg: AppConfig,
    *,
    provider: str | None,
    model: str | None,
    budget: int | None,
    bin_path: str | None,
    pi_bin: str | None,
) -> AppConfig:
    if provider:
        cfg.provider = provider
    if model:
        cfg.model = model
    if budget is not None:
        cfg.budget = budget
    if bin_path:
        cfg.paper_derived_bin = bin_path
    if pi_bin:
        cfg.pi_bin = pi_bin
    return cfg


def _launch_tui(
    cfg: AppConfig,
    *,
    session_id: str | None = None,
    workspace: Path | None = None,
) -> None:
    """Start interactive TUI. Workspace defaults to current directory."""
    from room_tui.app import run_tui

    ws = workspace if workspace is not None else Path.cwd()
    cfg.workspace = ws.resolve()
    run_tui(cfg, session_id=session_id, workspace=cfg.workspace)


@click.group(invoke_without_command=True)
@click.option("--session", "-s", "session_id", default=None, help="Open Dashboard for session")
@click.option("--workspace", "-w", "workspace", default=None, type=click.Path(path_type=Path),
              help="Workspace (default: current directory)")
@click.option("--provider", default=None, help="Model provider override")
@click.option("--model", default=None, help="Model id override")
@click.option("--bin", "bin_path", default=None, help="paper-derived binary")
@click.option("--pi-bin", default=None, help="Room Agent runtime binary (internal)")
@click.version_option(__version__, prog_name="room")
@click.pass_context
def main(
    ctx: click.Context,
    session_id: str | None,
    workspace: Path | None,
    provider: str | None,
    model: str | None,
    bin_path: str | None,
    pi_bin: str | None,
) -> None:
    """Room — project workbench (TUI).

    \b
    Usage (from any project directory):
      room                 # open TUI in current directory
      room -s sess_xxx    # open a session
      room doctor         # environment check
      room run ...        # document generation
    """
    # Hard isolation BEFORE any subcommand: bundled pi must not use ~/.pi
    from room_tui.pi_env import apply_room_pi_isolation

    apply_room_pi_isolation(seed_skills=True)

    if ctx.invoked_subcommand is not None:
        return
    # Bare `room` → launch TUI (same ergonomics as pi / claude / grok)
    cfg = _apply_common(
        load_config(),
        provider=provider,
        model=model,
        budget=None,
        bin_path=bin_path,
        pi_bin=pi_bin,
    )
    _launch_tui(cfg, session_id=session_id, workspace=workspace)


@main.command("doctor")
@click.option("--bin", "bin_path", default=None, help="paper-derived binary")
@click.option("--pi-bin", default=None, help="Room Agent runtime binary (internal)")
def doctor_cmd(bin_path: str | None, pi_bin: str | None) -> None:
    """Check engine, Room Agent runtime, and config."""
    cfg = _apply_common(load_config(), provider=None, model=None, budget=None, bin_path=bin_path, pi_bin=pi_bin)
    ok = True

    click.echo(f"room-tui {__version__}  (cli: room)")
    from room_tui.config import resolve_bin

    # Help Win users spot PATH pointing at an old room.exe after reinstall
    try:
        import shutil as _shutil

        which_room = _shutil.which("room") or _shutil.which("room.exe")
        if which_room:
            click.echo(f"room bin: {which_room}")
    except Exception:
        pass

    eng_path = resolve_bin(cfg.paper_derived_bin) or cfg.paper_derived_bin
    click.echo(f"engine bin: {eng_path}")
    try:
        eng = EngineAdapter(cfg.paper_derived_bin, timeout_s=max(cfg.engine_timeout_s, 90))
        ver = eng.version()
        click.echo(f"  version: {ver.get('version')}")
        caps = ver.get("capabilities") or []
        click.echo(f"  capabilities ({len(caps)}): {', '.join(caps[:8])}…")
        need = {"out-text-prompt", "session-run"}
        missing = need - set(caps)
        if missing:
            click.echo(click.style(f"  missing recommended caps: {missing}", fg="yellow"))
    except Exception as e:
        ok = False
        msg = str(e)
        click.echo(click.style(f"  FAIL: {msg}", fg="red"))
        low = msg.lower()
        if "no such command" in low and "version" in low:
            click.echo(
                click.style(
                    "  hint: 套件中的 paper-derived.exe 过旧/不完整（缺少 version 命令）。\n"
                    "        请用含 `paper-derived version` 的引擎重打 suite 并重装。",
                    fg="yellow",
                )
            )
        elif "no such option" in low and "out" in low:
            click.echo(
                click.style(
                    "  hint: 引擎 CLI 与 Room 不匹配。请成对升级 room + paper-derived。",
                    fg="yellow",
                )
            )

    # .doc converter: suite-bundled LibreOffice (preferred) or system Word
    from room_tui.config import find_asan_cc, find_bundled_cc, find_bundled_soffice

    soffice = find_bundled_soffice()
    if soffice:
        click.echo(f"doc converter: LibreOffice  {soffice}")
        click.echo(click.style("  ok (.doc via headless soffice)", fg="green"))
    else:
        click.echo("doc converter: LibreOffice  (not found)")
        click.echo(
            click.style(
                "  warn: 旧版 .doc 需套件 tools/libreoffice、本机 LibreOffice，或 Word/WPS\n"
                "        发布者: scripts/fetch-libreoffice-windows.ps1 后重打 suite\n"
                "        用户: 可另存为 .docx 再 /template register",
                fg="yellow",
            )
        )

    # oob-divzero capability (scan CLI + ASan C toolchain)
    oob_path = resolve_bin(cfg.oob_divzero_bin) or resolve_bin("oob-divzero")
    click.echo(f"oob-divzero: {oob_path or '(not found)'}")
    if oob_path:
        try:
            import subprocess as _sp

            ver = _sp.run(
                [oob_path, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            line = (ver.stdout or ver.stderr or "").strip().splitlines()
            tip = line[0] if line else "ok"
            click.echo(click.style(f"  ok  {tip}", fg="green"))
        except Exception as e:
            err_s = str(e)
            click.echo(click.style(f"  warn: version probe failed: {e}", fg="yellow"))
            # WinError 193 / errno 8 usually = wrong arch or non-PE file as .exe
            if (
                "193" in err_s
                or "not a valid Win32" in err_s
                or "Exec format error" in err_s
                or "Bad CPU type" in err_s
            ):
                click.echo(
                    click.style(
                        "  hint: oob-divzero.exe 不是本机可运行的 Windows 程序\n"
                        "        常见：用了 Linux/mac 二进制、ARM/x64 架构不符、或损坏的 .exe\n"
                        "        发布者: 在目标 Windows 上用 PyInstaller 重打 oob-divzero.exe\n"
                        "          例: .\\scripts\\build-windows-suite.ps1 -OobDivzeroRepo ..\\oob-divzero\n"
                        "        用户: 重装与 CPU 架构匹配的 Room Setup（x86_64 vs arm64）",
                        fg="yellow",
                    )
                )
    else:
        ok = False
        click.echo(
            click.style(
                "  FAIL: 未找到 oob-divzero（套件 bin/ 同级或 PATH）\n"
                "        发布者: package-suite 传入 -OobDivzero / --oob-divzero",
                fg="red",
            )
        )

    bundled_cc = find_bundled_cc()
    any_cc = find_asan_cc()
    if bundled_cc:
        click.echo(f"asan toolchain: bundled  {bundled_cc}")
        click.echo(click.style("  ok (oob verify uses suite tools/c-toolchain)", fg="green"))
    elif any_cc:
        click.echo(f"asan toolchain: system  {any_cc}")
        click.echo(
            click.style(
                "  warn: 产品套件应捆绑 tools/c-toolchain；当前用本机编译器（开发可接受）\n"
                "        发布者: scripts/fetch-c-toolchain-*.ps1|sh 后 -RequireCToolchain 重打",
                fg="yellow",
            )
        )
    else:
        ok = False
        click.echo("asan toolchain: (not found)")
        click.echo(
            click.style(
                "  FAIL: oob ASan 验证需要 C 编译器（套件 tools/c-toolchain 或本机 clang/gcc）",
                fg="red",
            )
        )

    agent_path = resolve_bin(cfg.pi_bin)
    click.echo(f"room agent: {'ok' if agent_path else 'missing'}")
    if not agent_path:
        ok = False
        click.echo(
            click.style(
                "  FAIL: Room Agent 组件未找到（套件 bin/ 同级、PATH，或安装不完整）",
                fg="red",
            )
        )
    else:
        click.echo(click.style("  ok", fg="green"))
    # Isolated config (must not be system ~/.pi/agent)
    try:
        from room_tui.pi_env import isolation_status

        st = isolation_status()
        click.echo(f"room agent dir: {st['room_pi_agent']}")
        click.echo(
            f"  ROOM_CODING_AGENT_DIR={st.get('ROOM_CODING_AGENT_DIR') or '(unset)'}"
        )
        click.echo(f"  PI_CODING_AGENT_DIR={st['PI_CODING_AGENT_DIR'] or '(unset)'}")
        if not st["env_points_at_room"]:
            ok = False
            click.echo(
                click.style(
                    "  FAIL: agent env 未指向 Room 目录 — 内置 pi 可能读系统 ~/.pi",
                    fg="red",
                )
            )
        elif not st["isolated"]:
            ok = False
            click.echo(
                click.style(
                    "  FAIL: Room pi-agent 与系统 Pi Agent 同目录，配置会串线",
                    fg="red",
                )
            )
        else:
            click.echo(
                click.style(
                    f"  isolated from system Pi  ({st['system_pi_agent']})",
                    fg="green",
                )
            )
        if st["system_auth_exists"] and not st["room_auth_nonempty"]:
            click.echo(
                click.style(
                    "  note: 系统 ~/.pi 有 API 密钥，Room 不会读取；"
                    "请在 Room 内配置模型/密钥（~/.config/room-tui/agent/auth.json）",
                    fg="yellow",
                )
            )
    except Exception as e:
        click.echo(click.style(f"  room pi-agent: {e}", fg="yellow"))

    if cfg.provider or cfg.model:
        click.echo(f"model: {cfg.provider}/{cfg.model}")
    else:
        click.echo(
            click.style(
                "model: unset (set ROOM_PROVIDER/ROOM_MODEL or ~/.config/room-tui/config.toml)",
                fg="yellow",
            )
        )

    # Required product skills (suite installers place paper-derived, etc.)
    try:
        from room_tui.pi_catalog import (
            REQUIRED_SKILLS,
            list_skills,
            missing_required_skills,
        )
        from room_tui.pi_env import bundled_skills_root, seed_required_skills_into_room_pi

        # Self-heal: seed from suite dirs or skills embedded in room.exe
        seeded = seed_required_skills_into_room_pi()
        if seeded:
            click.echo(
                click.style(
                    f"skills: seeded {', '.join(seeded)} -> room pi-agent",
                    fg="cyan",
                )
            )

        skills = list_skills()
        missing = missing_required_skills()
        if missing:
            ok = False
            click.echo(
                click.style(
                    f"skills: FAIL missing required {', '.join(missing)}",
                    fg="red",
                )
            )
            from room_tui.pi_env import room_pi_skills_dir, skill_source_roots

            click.echo(f"  room pi-agent skills: {room_pi_skills_dir()}")
            click.echo("  looked for sources:")
            for root in skill_source_roots()[:8]:
                marks = []
                for req in REQUIRED_SKILLS:
                    marks.append(
                        f"{req}={'OK' if (root / req / 'SKILL.md').is_file() else 'miss'}"
                    )
                click.echo(f"    [{', '.join(marks)}] {root}")
            bundled = bundled_skills_root()
            if bundled is None:
                click.echo(
                    "  this room.exe has no embedded skills - rebuild with current room.spec"
                )
            click.echo(
                "  fix: re-run suite install (needs suite\\skills\\ for all required skills)"
            )
            click.echo(
                "    or copy skill trees to:  %USERPROFILE%\\.config\\room-tui\\agent\\skills\\"
            )
        else:
            names = ", ".join(s.name for s in skills[:8]) or "(none extra)"
            click.echo(
                click.style(
                    f"skills: ok  required={','.join(REQUIRED_SKILLS)}  discovered={len(skills)}  {names}",
                    fg="green",
                )
            )
    except Exception as e:
        click.echo(click.style(f"skills: FAIL {e}", fg="yellow"))

    try:
        templates = EngineAdapter(cfg.paper_derived_bin, timeout_s=90).template_list()
        if not templates:
            click.echo(
                click.style(
                    "templates: 0  (register in TUI: /template register <sample> [name])",
                    fg="yellow",
                )
            )
        else:
            click.echo(f"templates: {len(templates)}")
            for t in templates[:5]:
                click.echo(f"  - {t.get('id')}  ({t.get('section_count')} sections)")
    except Exception as e:
        msg = str(e)
        # Engine may print Chinese "暂无已注册模板" to stdout — not a hard failure.
        if "暂无" in msg or "not JSON" in msg:
            click.echo(
                click.style(
                    "templates: 0  (none registered · /template register …)",
                    fg="yellow",
                )
            )
        else:
            click.echo(click.style(f"templates: FAIL {e}", fg="yellow"))

    sys.exit(0 if ok else 1)


@main.command("setup")
@click.option("--provider", "-p", default="", help="Provider id (e.g. deepseek, openai)")
@click.option("--api-key", "-k", default="", help="API key (or set later interactively)")
@click.option("--model", "-m", default="", help="Default model id after key save")
def setup_cmd(provider: str, api_key: str, model: str) -> None:
    """Configure Room model API key into isolated agent auth.json (not ~/.pi)."""
    from room_tui.auth_setup import (
        PROVIDER_PRESETS,
        list_configured_providers,
        set_api_key,
        set_room_default_model,
        setup_status_lines,
    )
    from room_tui.pi_env import apply_room_pi_isolation, room_pi_agent_dir

    apply_room_pi_isolation(seed_skills=True)
    click.echo(f"room-tui {__version__}")
    for line in setup_status_lines():
        click.echo(f"  {line}")

    prov = (provider or "").strip()
    key = (api_key or "").strip()
    from room_tui.auth_setup import configure_local_provider, find_preset

    if not prov:
        click.echo("")
        click.echo("Room 支持的服务商:")
        for p in PROVIDER_PRESETS:
            kind = "local" if p.kind == "local" else "cloud"
            click.echo(f"  {p.id:22}  {p.label}  [{kind}]")
        click.echo("")
        prov = click.prompt("Provider id", default="deepseek").strip()
    preset = find_preset(prov)
    if preset and preset.kind == "local":
        base = click.prompt("Base URL", default=preset.default_base_url).strip()
        # Key optional: empty = no Authorization header
        if api_key:
            key = api_key.strip()
        else:
            key = click.prompt(
                "API Key (optional, empty if server has no auth)",
                default="",
                show_default=False,
            ).strip()
        mid = (model or "").strip() or preset.default_model_id
        mid = click.prompt("Model id", default=mid).strip()
        try:
            auth_p, models_p = configure_local_provider(
                prov, base_url=base, api_key=key, model_id=mid
            )
        except ValueError as e:
            click.echo(click.style(f"FAIL: {e}", fg="red"))
            sys.exit(1)
        click.echo(click.style(f"OK local provider {prov}", fg="green"))
        click.echo(f"  auth:   {auth_p}  ({'with key' if key else 'no key'})")
        click.echo(f"  models: {models_p}")
        try:
            set_room_default_model(prov, mid)
        except Exception as e:
            click.echo(click.style(f"WARN model default: {e}", fg="yellow"))
    else:
        if not key:
            key = click.prompt("API Key", hide_input=True).strip()
        try:
            path = set_api_key(prov, key)
        except ValueError as e:
            click.echo(click.style(f"FAIL: {e}", fg="red"))
            sys.exit(1)
        click.echo(click.style(f"OK wrote key for {prov} → {path}", fg="green"))
        mid = (model or "").strip()
        if not mid:
            mid = click.prompt(
                "Default model id (empty to skip)",
                default="",
                show_default=False,
            ).strip()
        if mid:
            try:
                cfg_path = set_room_default_model(prov, mid)
                click.echo(
                    click.style(f"OK default model {prov}/{mid} → {cfg_path}", fg="green")
                )
            except Exception as e:
                click.echo(click.style(f"WARN model default: {e}", fg="yellow"))
    click.echo(f"configured: {', '.join(list_configured_providers()) or '(none)'}")
    click.echo(f"agent dir: {room_pi_agent_dir()}")
    click.echo("Next: room   then Ctrl+M if needed")
    sys.exit(0)


@main.command("skills-seed")
def skills_seed_cmd() -> None:
    """Seed required skills into Room agent dir (install / repair helper)."""
    from room_tui.pi_catalog import REQUIRED_SKILLS, list_skills, missing_required_skills
    from room_tui.pi_env import apply_room_pi_isolation, room_pi_skills_dir

    apply_room_pi_isolation(seed_skills=True)
    seeded_dir = room_pi_skills_dir()
    missing = missing_required_skills()
    skills = list_skills()
    click.echo(f"room-tui {__version__}")
    click.echo(f"agent skills dir: {seeded_dir}")
    click.echo(f"discovered: {len(skills)}  {[s.name for s in skills[:12]]}")
    if missing:
        click.echo(
            click.style(
                f"FAIL missing required: {', '.join(missing)}",
                fg="red",
            )
        )
        click.echo(f"  expected under: {seeded_dir / 'paper-derived' / 'SKILL.md'}")
        click.echo("  suite must include skills/paper-derived; re-run install.ps1")
        sys.exit(1)
    click.echo(
        click.style(
            f"OK required skills present: {', '.join(REQUIRED_SKILLS)}",
            fg="green",
        )
    )
    sys.exit(0)


@main.command("run")
@click.option(
    "--workspace",
    "-w",
    type=click.Path(path_type=Path),
    default=None,
    help="Workspace (default: current directory)",
)
@click.option("--template", "-t", required=True, help="template id")
@click.option(
    "--input",
    "-i",
    "inputs",
    multiple=True,
    type=click.Path(exists=True, path_type=Path),
    help="input file(s)",
)
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None)
@click.option("--budget", type=int, default=None)
@click.option("--provider", default=None)
@click.option("--model", default=None)
@click.option("--bin", "bin_path", default=None)
@click.option("--pi-bin", default=None)
@click.option("--no-summarize", is_flag=True, default=False)
@click.option("--tui/--no-tui", default=False, help="open Dashboard while running")
def run_cmd(
    workspace: Path | None,
    template: str,
    inputs: tuple[Path, ...],
    output: Path | None,
    budget: int | None,
    provider: str | None,
    model: str | None,
    bin_path: str | None,
    pi_bin: str | None,
    no_summarize: bool,
    tui: bool,
) -> None:
    """Run session-driven generation (headless or with TUI).

    Run from your project directory; workspace defaults to cwd.
    """
    cfg = _apply_common(
        load_config(),
        provider=provider,
        model=model,
        budget=budget,
        bin_path=bin_path,
        pi_bin=pi_bin,
    )
    if no_summarize:
        cfg.summarize = False
    if not cfg.provider and not cfg.model:
        click.echo(
            click.style(
                "Warning: no provider/model set — Room Agent will use its default.",
                fg="yellow",
            )
        )
    if not inputs:
        raise click.UsageError("provide at least one --input")

    workspace = (workspace or Path.cwd()).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    out = (output or (workspace / "output.md")).resolve()

    orch = SessionOrchestrator(cfg)
    spec = RunSpec(
        workspace=workspace,
        template_id=template,
        inputs=list(inputs),
        output=out,
        budget=budget or cfg.budget,
    )

    if tui:
        from room_tui.app import RoomApp

        app = RoomApp(cfg)

        async def _bg() -> None:
            try:
                path = await orch.run(spec)
                app.call_from_thread(
                    app.notify, f"Complete: {path}", severity="information"
                )
            except Exception as e:
                app.call_from_thread(app.notify, f"Failed: {e}", severity="error")

        def _start() -> None:
            app.orch = orch
            asyncio.get_event_loop().create_task(_bg())

        # Run orch in worker after mount
        class RunApp(RoomApp):
            def on_mount(self) -> None:  # type: ignore[override]
                self.orch = orch
                self.push_screen(__import__("room_tui.screens.dashboard", fromlist=["DashboardScreen"]).DashboardScreen(orch))
                self.run_worker(orch.run(spec), exclusive=True)

        RunApp(cfg).run()
        return

    # headless
    click.echo(f"workspace: {workspace}")
    click.echo(f"template:  {template}")
    click.echo(f"inputs:    {len(inputs)}")
    click.echo(f"output:    {out}")
    click.echo(f"model:     {cfg.provider}/{cfg.model}")

    def on_ev(ev: dict) -> None:
        t = ev.get("type")
        if t in {
            "session_init",
            "step_start",
            "step_ok",
            "step_error",
            "session_next",
            "run_complete",
            "run_failed",
            "worker_done",
        }:
            click.echo(f"  · {t}: { {k: ev[k] for k in ev if k != 'type'} }")

    orch.subscribe(on_ev)
    try:
        path = asyncio.run(orch.run(spec))
    except Exception as e:
        click.echo(click.style(f"FAILED: {e}", fg="red"))
        sys.exit(1)
    click.echo(click.style(f"OK → {path}", fg="green"))


@main.command("resume")
@click.option(
    "--workspace",
    "-w",
    type=click.Path(path_type=Path),
    default=None,
    help="Workspace (default: current directory)",
)
@click.option("--session", "-s", "session_id", required=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None)
@click.option("--provider", default=None)
@click.option("--model", default=None)
@click.option("--budget", type=int, default=None)
@click.option("--bin", "bin_path", default=None)
@click.option("--pi-bin", default=None)
@click.option("--tui/--no-tui", default=False)
def resume_cmd(
    workspace: Path | None,
    session_id: str,
    output: Path | None,
    provider: str | None,
    model: str | None,
    budget: int | None,
    bin_path: str | None,
    pi_bin: str | None,
    tui: bool,
) -> None:
    """Resume an existing session (cwd = workspace by default)."""
    cfg = _apply_common(
        load_config(),
        provider=provider,
        model=model,
        budget=budget,
        bin_path=bin_path,
        pi_bin=pi_bin,
    )
    workspace = (workspace or Path.cwd()).resolve()
    out = (output or (workspace / "output.md")).resolve()
    orch = SessionOrchestrator(cfg)
    spec = RunSpec(
        workspace=workspace,
        template_id="",  # unused on resume
        inputs=[],
        output=out,
        session_id=session_id,
        budget=budget or cfg.budget,
    )

    if tui:
        from room_tui.app import RoomApp

        class ResumeApp(RoomApp):
            def on_mount(self) -> None:  # type: ignore[override]
                self.orch = orch
                self.orch.state.session_id = session_id
                from room_tui.screens.dashboard import DashboardScreen

                self.push_screen(DashboardScreen(orch, session_id=session_id))
                self.run_worker(orch.run(spec), exclusive=True)

        ResumeApp(cfg).run()
        return

    def on_ev(ev: dict) -> None:
        click.echo(f"  · {ev.get('type')}: {ev}")

    orch.subscribe(on_ev)
    try:
        path = asyncio.run(orch.run(spec))
    except Exception as e:
        click.echo(click.style(f"FAILED: {e}", fg="red"))
        sys.exit(1)
    click.echo(click.style(f"OK → {path}", fg="green"))


@main.command("tui")
@click.option("--session", "-s", "session_id", default=None, help="open Dashboard for session")
@click.option(
    "--workspace",
    "-w",
    type=click.Path(path_type=Path),
    default=None,
    help="Workspace (default: current directory)",
)
@click.option("--provider", default=None)
@click.option("--model", default=None)
@click.option("--bin", "bin_path", default=None)
@click.option("--pi-bin", default=None)
def tui_cmd(
    session_id: str | None,
    workspace: Path | None,
    provider: str | None,
    model: str | None,
    bin_path: str | None,
    pi_bin: str | None,
) -> None:
    """Launch interactive TUI (alias of bare `room`)."""
    cfg = _apply_common(
        load_config(),
        provider=provider,
        model=model,
        budget=None,
        bin_path=bin_path,
        pi_bin=pi_bin,
    )
    _launch_tui(cfg, session_id=session_id, workspace=workspace)


@main.command("snapshot")
@click.argument("session_id")
@click.option("--bin", "bin_path", default=None)
def snapshot_cmd(session_id: str, bin_path: str | None) -> None:
    """Print session section tree (debug)."""
    cfg = load_config()
    if bin_path:
        cfg.paper_derived_bin = bin_path
    eng = EngineAdapter(cfg.paper_derived_bin, timeout_s=90)
    try:
        snap = eng.session_snapshot(session_id)
    except EngineError as e:
        click.echo(e, err=True)
        sys.exit(1)
    click.echo(f"{snap.session_id}  {snap.template_id}  {snap.phase}  {snap.progress}")
    click.echo(f"next: {snap.next_action}")
    for s in snap.sections:
        pad = "  " * max(0, s.level - 1)
        click.echo(f"{pad}{s.status:12} {s.section_id:28} {s.title}")


if __name__ == "__main__":
    main()
