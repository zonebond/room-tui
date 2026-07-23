"""Runtime configuration for room."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _default_config_path() -> Path:
    if env := os.environ.get("ROOM_CONFIG"):
        return Path(env)
    return Path.home() / ".config" / "room-tui" / "config.toml"


def _install_bin_dir() -> Path | None:
    """Directory that holds the ``room`` executable (suite bin/).

    Used so frozen product builds can find a sibling ``paper-derived`` without
    requiring PATH or env vars. Dev installs (source / venv) usually have no
    sibling binary and fall through to PATH.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # Optional: ROOM_INSTALL_BIN points at product install bin/
    if env := os.environ.get("ROOM_INSTALL_BIN"):
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()
    return None


def _default_paper_derived_bin() -> str:
    if env := os.environ.get("PAPER_DERIVED_BIN"):
        return env
    bin_dir = _install_bin_dir()
    if bin_dir is not None:
        name = "paper-derived.exe" if sys.platform == "win32" else "paper-derived"
        sibling = bin_dir / name
        if sibling.is_file():
            return str(sibling)
    return "paper-derived"


def _default_pi_bin() -> str:
    if env := os.environ.get("PI_BIN"):
        return env
    bin_dir = _install_bin_dir()
    if bin_dir is not None:
        name = "pi.exe" if sys.platform == "win32" else "pi"
        sibling = bin_dir / name
        if sibling.is_file():
            return str(sibling)
    return "pi"


def _default_oob_divzero_bin() -> str:
    if env := os.environ.get("OOB_DIVZERO_BIN"):
        return env
    bin_dir = _install_bin_dir()
    if bin_dir is not None:
        name = "oob-divzero.exe" if sys.platform == "win32" else "oob-divzero"
        sibling = bin_dir / name
        if sibling.is_file():
            return str(sibling)
    return "oob-divzero"


def resolve_bin(path: str) -> str | None:
    """Return an executable path if ``path`` is on PATH or is an existing file."""
    import shutil

    found = shutil.which(path)
    if found:
        return found
    p = Path(path)
    if p.is_file():
        return str(p.resolve())
    return None


def _as_soffice_binary(raw: Path) -> Path | None:
    """Accept soffice.exe or a program/ install directory."""
    try:
        p = raw.expanduser().resolve()
    except OSError:
        return None
    if p.is_file():
        return p
    if p.is_dir():
        for name in ("soffice.exe", "soffice", "libreoffice"):
            for cand in (p / name, p / "program" / name):
                if cand.is_file():
                    return cand.resolve()
    return None


def find_bundled_soffice() -> str | None:
    """Locate suite-bundled LibreOffice (headless .doc converter).

    Product layout::

        {ROOM_HOME}/
          bin/room.exe
          bin/paper-derived.exe
          tools/libreoffice/program/soffice.exe

    Env overrides (any may point at binary or program dir):
    ``ROOM_LIBREOFFICE``, ``PAPER_DERIVED_LIBREOFFICE``, ``LIBREOFFICE_PROGRAM``.
    """
    for key in (
        "ROOM_LIBREOFFICE",
        "PAPER_DERIVED_LIBREOFFICE",
        "LIBREOFFICE_PROGRAM",
    ):
        v = (os.environ.get(key) or "").strip()
        if not v:
            continue
        hit = _as_soffice_binary(Path(v))
        if hit is not None:
            return str(hit)

    bases: list[Path] = []
    bin_dir = _install_bin_dir()
    if bin_dir is not None:
        bases.append(bin_dir)
        bases.append(bin_dir.parent)
    if getattr(sys, "frozen", False):
        try:
            exe_dir = Path(sys.executable).resolve().parent
            bases.extend([exe_dir, exe_dir.parent])
        except OSError:
            pass
    room_home = (os.environ.get("ROOM_HOME") or "").strip()
    if room_home:
        try:
            bases.append(Path(room_home).expanduser().resolve())
        except OSError:
            pass

    rels = (
        Path("tools") / "libreoffice" / "program" / "soffice.exe",
        Path("tools") / "libreoffice" / "program" / "soffice",
        Path("tools") / "LibreOffice" / "program" / "soffice.exe",
    )
    seen: set[str] = set()
    for base in bases:
        for rel in rels:
            cand = base / rel
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            hit = _as_soffice_binary(cand)
            if hit is not None:
                return str(hit)

    # System install (optional; doctor can report this too)
    import shutil

    for name in ("soffice", "soffice.exe", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for root_key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(root_key) or ""
            if not root:
                continue
            base = Path(root)
            direct = base / "LibreOffice" / "program" / "soffice.exe"
            if direct.is_file():
                return str(direct.resolve())
            try:
                for p in base.glob("LibreOffice*/program/soffice.exe"):
                    if p.is_file():
                        return str(p.resolve())
            except OSError:
                pass
    elif sys.platform == "darwin":
        mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if mac.is_file():
            return str(mac)
    return None


def _prepend_path(env: dict[str, str], directory: str) -> None:
    """Prepend *directory* to PATH/Path if not already present."""
    if not directory:
        return
    prev = env.get("PATH") or env.get("Path") or ""
    parts = prev.split(os.pathsep) if prev else []
    if directory in parts:
        return
    env["PATH"] = directory + (os.pathsep + prev if prev else "")


def find_bundled_cc() -> str | None:
    """Locate suite-bundled C toolchain (clang/gcc) for oob-divzero ASan.

    Product layout::

        {ROOM_HOME}/
          bin/room[.exe]
          bin/oob-divzero[.exe]
          tools/c-toolchain/bin/clang[.exe]

    Env overrides: ``OOB_CC``, ``ROOM_CC``, ``ROOM_C_TOOLCHAIN``, ``OOB_TOOLCHAIN``.
    """
    import shutil

    for key in ("OOB_CC", "ROOM_CC"):
        v = (os.environ.get(key) or "").strip()
        if not v:
            continue
        p = Path(v).expanduser()
        try:
            if p.is_file():
                return str(p.resolve())
        except OSError:
            pass
        found = shutil.which(v)
        if found:
            return found

    search_dirs: list[Path] = []
    for key in ("ROOM_C_TOOLCHAIN", "OOB_TOOLCHAIN"):
        v = (os.environ.get(key) or "").strip()
        if not v:
            continue
        try:
            p = Path(v).expanduser().resolve()
        except OSError:
            continue
        if p.is_file():
            return str(p)
        search_dirs.append(p)
        search_dirs.append(p / "bin")

    roots: list[Path] = []
    bin_dir = _install_bin_dir()
    if bin_dir is not None:
        roots.extend([bin_dir, bin_dir.parent])
    if getattr(sys, "frozen", False):
        try:
            exe_dir = Path(sys.executable).resolve().parent
            roots.extend([exe_dir, exe_dir.parent])
        except OSError:
            pass
    room_home = (os.environ.get("ROOM_HOME") or "").strip()
    if room_home:
        try:
            roots.append(Path(room_home).expanduser().resolve())
        except OSError:
            pass

    for root in roots:
        search_dirs.append(root / "tools" / "c-toolchain" / "bin")
        search_dirs.append(root / "tools" / "c-toolchain")
        search_dirs.append(root / "c-toolchain" / "bin")

    names = ("clang", "clang.exe", "gcc", "gcc.exe", "cc", "cc.exe")
    seen: set[str] = set()
    for d in search_dirs:
        key_d = str(d)
        if key_d in seen:
            continue
        seen.add(key_d)
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        for name in names:
            cand = d / name
            try:
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand.resolve())
            except OSError:
                continue
    return None


def find_asan_cc() -> str | None:
    """Bundled C toolchain, else system clang/gcc/cc (dev fallback)."""
    bundled = find_bundled_cc()
    if bundled:
        return bundled
    import shutil

    for name in ("clang", "gcc", "cc"):
        found = shutil.which(name)
        if found:
            return found
    return None


def capability_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Env for capability CLIs (paper-derived, oob-divzero) and agent bash tools.

    Injects:
    - suite bin/ on PATH (oob-divzero, paper-derived siblings)
    - bundled LibreOffice (paper-derived .doc)
    - bundled C toolchain (oob ASan): OOB_CC / ROOM_CC / CC + PATH
    """
    env = dict(base if base is not None else os.environ)

    bin_dir = _install_bin_dir()
    if bin_dir is not None:
        _prepend_path(env, str(bin_dir))
        env.setdefault("ROOM_INSTALL_BIN", str(bin_dir))
        env.setdefault("ROOM_HOME", str(bin_dir.parent))

    soffice = find_bundled_soffice()
    if soffice:
        prog = str(Path(soffice).resolve().parent)
        env["ROOM_LIBREOFFICE"] = soffice
        env["PAPER_DERIVED_LIBREOFFICE"] = soffice
        _prepend_path(env, prog)

    cc = find_bundled_cc()
    if cc:
        env["OOB_CC"] = cc
        env["ROOM_CC"] = cc
        env.setdefault("CC", cc)
        try:
            tc_bin = str(Path(cc).resolve().parent)
            _prepend_path(env, tc_bin)
            # toolchain root (parent of bin/) for compiler-rt discovery
            if Path(tc_bin).name == "bin":
                env["ROOM_C_TOOLCHAIN"] = str(Path(tc_bin).parent)
                env["OOB_TOOLCHAIN"] = str(Path(tc_bin).parent)
        except OSError:
            pass

    return env


def engine_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Env for paper-derived: LibreOffice + suite PATH (see capability_subprocess_env)."""
    return capability_subprocess_env(base)


@dataclass
class PiTierConfig:
    provider: str = ""
    model: str = ""
    thinking: str = "off"


@dataclass
class AppConfig:
    paper_derived_bin: str = field(default_factory=_default_paper_derived_bin)
    oob_divzero_bin: str = field(default_factory=_default_oob_divzero_bin)
    pi_bin: str = field(default_factory=_default_pi_bin)
    workspace: Path | None = field(
        default_factory=lambda: Path(os.environ["ROOM_WORKSPACE"]).resolve()
        if os.environ.get("ROOM_WORKSPACE")
        else None
    )
    engine_timeout_s: float = 120.0
    worker_timeout_s: float = 600.0
    # Free-form chat = full Pi Agent (tools/skills/session); may run long.
    agent_timeout_s: float = 900.0
    max_attempts: int = 3
    parallel: int = 1
    summarize: bool = True
    budget: int = 40000
    # model tiers
    provider: str = field(
        default_factory=lambda: os.environ.get("ROOM_PROVIDER", "")
    )
    model: str = field(default_factory=lambda: os.environ.get("ROOM_MODEL", ""))
    thinking: str = "off"
    # Agent chat thinking (Pi levels: off/minimal/low/medium/high/xhigh/max)
    agent_thinking: str = "high"
    fast_provider: str = ""
    fast_model: str = ""
    strong_provider: str = ""
    strong_model: str = ""
    # Active skill name for Agent turns (empty = discovery only)
    active_skill: str = ""
    # UI: task sidebar collapsed (persisted across sessions)
    sidebar_collapsed: bool = False

    def tier(self, name: str = "default") -> PiTierConfig:
        if name == "fast":
            return PiTierConfig(
                provider=self.fast_provider or self.provider,
                model=self.fast_model or self.model,
                thinking=self.thinking,
            )
        if name == "strong":
            return PiTierConfig(
                provider=self.strong_provider or self.provider,
                model=self.strong_model or self.model,
                thinking=self.thinking,
            )
        return PiTierConfig(
            provider=self.provider,
            model=self.model,
            thinking=self.thinking,
        )


def _load_pi_agent_defaults(cfg: AppConfig) -> None:
    """Fall back to **Room-isolated** pi-agent settings when provider/model unset.

    Never reads system Pi Agent ``~/.pi/agent/settings.json`` — that product
    stays separate. Room uses ``~/.config/room-tui/agent/settings.json``.
    """
    if cfg.provider and cfg.model:
        return
    from room_tui.pi_env import room_pi_settings_path

    settings = room_pi_settings_path()
    if not settings.exists():
        return
    try:
        import json

        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not cfg.provider and data.get("defaultProvider"):
        cfg.provider = str(data["defaultProvider"])
    if not cfg.model and data.get("defaultModel"):
        cfg.model = str(data["defaultModel"])
    # Agent chat can use Pi default thinking; workers stay off unless set.
    if data.get("defaultThinkingLevel") and cfg.agent_thinking == "high":
        cfg.agent_thinking = str(data["defaultThinkingLevel"])


def save_config(cfg: AppConfig) -> Path:
    """Persist provider/model/thinking/skill to ~/.config/room-tui/config.toml."""
    path = _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve unknown sections if file already exists
    existing: dict = {}
    if path.exists():
        try:
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib  # type: ignore
            existing = tomllib.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    pi = dict(existing.get("pi") or {})
    pi["provider"] = cfg.provider
    pi["model"] = cfg.model
    pi["thinking"] = cfg.thinking
    pi["agent_thinking"] = cfg.agent_thinking
    if cfg.active_skill:
        pi["active_skill"] = cfg.active_skill
    elif "active_skill" in pi:
        del pi["active_skill"]
    tiers = dict(pi.get("tiers") or {})
    if cfg.fast_provider or cfg.fast_model:
        tiers["fast"] = {
            "provider": cfg.fast_provider,
            "model": cfg.fast_model,
        }
    if cfg.strong_provider or cfg.strong_model:
        tiers["strong"] = {
            "provider": cfg.strong_provider,
            "model": cfg.strong_model,
        }
    if tiers:
        pi["tiers"] = tiers
    existing["pi"] = pi
    ui = dict(existing.get("ui") or {})
    ui["sidebar_collapsed"] = bool(cfg.sidebar_collapsed)
    existing["ui"] = ui
    # Minimal TOML writer (no external dep)
    lines: list[str] = []
    for section, body in existing.items():
        if not isinstance(body, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in body.items():
            if isinstance(v, dict):
                lines.append(f"\n[{section}.{k}]")
                for kk, vv in v.items():
                    lines.append(_toml_assign(kk, vv))
                lines.append("")
            else:
                lines.append(_toml_assign(k, v))
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _toml_assign(key: str, value: object) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{key} = "{s}"'


def load_config() -> AppConfig:
    """Load config from env + optional TOML; fall back to Pi agent settings."""
    cfg = AppConfig()
    _load_pi_agent_defaults(cfg)
    path = _default_config_path()
    if not path.exists():
        return cfg
    try:
        import tomllib
    except ModuleNotFoundError:  # py3.10
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError:
            return cfg
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    pi = data.get("pi", {})
    if "provider" in pi:
        cfg.provider = str(pi["provider"])
    if "model" in pi:
        cfg.model = str(pi["model"])
    if "thinking" in pi:
        cfg.thinking = str(pi["thinking"])
    if "agent_thinking" in pi:
        cfg.agent_thinking = str(pi["agent_thinking"])
    if "active_skill" in pi:
        cfg.active_skill = str(pi["active_skill"])
    tiers = pi.get("tiers", {})
    if "fast" in tiers:
        cfg.fast_provider = str(tiers["fast"].get("provider", cfg.fast_provider))
        cfg.fast_model = str(tiers["fast"].get("model", cfg.fast_model))
    if "strong" in tiers:
        cfg.strong_provider = str(tiers["strong"].get("provider", cfg.strong_provider))
        cfg.strong_model = str(tiers["strong"].get("model", cfg.strong_model))
    eng = data.get("engine", {})
    if "bin" in eng:
        cfg.paper_derived_bin = str(eng["bin"])
    if "timeout_s" in eng:
        cfg.engine_timeout_s = float(eng["timeout_s"])
    run = data.get("run", {})
    if "budget" in run:
        cfg.budget = int(run["budget"])
    if "parallel" in run:
        cfg.parallel = int(run["parallel"])
    if "summarize" in run:
        cfg.summarize = bool(run["summarize"])
    if "max_attempts" in run:
        cfg.max_attempts = int(run["max_attempts"])
    if "worker_timeout_s" in run:
        cfg.worker_timeout_s = float(run["worker_timeout_s"])
    if "agent_timeout_s" in run:
        cfg.agent_timeout_s = float(run["agent_timeout_s"])
    ui = data.get("ui", {})
    if "sidebar_collapsed" in ui:
        cfg.sidebar_collapsed = bool(ui["sidebar_collapsed"])
    return cfg
