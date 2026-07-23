"""Room-isolated Pi Agent config (separate from the user's interactive Pi).

System Pi Agent uses ``~/.pi/agent`` (settings, auth, skills).
Room ships a **Room-branded** ``pi`` built from ``c-checkers/pi-room`` with:

  piConfig.name = "room"
  piConfig.configDir = ".config/room-tui"

so the binary default is:

  ~/.config/room-tui/agent/   (settings.json, auth.json, models.json, skills/)

Env override (Room-branded binary): ``ROOM_CODING_AGENT_DIR``.
Also set ``ROOM_PI_AGENT_DIR`` / ``PI_CODING_AGENT_DIR`` for older tooling.

Rules:
- Never use ``code.research/pi`` for Room packaging (other products own that tree).
- Never read/write system ``~/.pi`` unless ``ROOM_SCAN_SYSTEM_PI_SKILLS=1``.
- Apply isolation process-wide at Room startup.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_isolation_ready: bool = False


def room_pi_agent_dir() -> Path:
    """Absolute path to Room's private agent config directory.

    Matches Room-branded pi default: ``~/.config/room-tui/agent``.
    """
    for key in ("ROOM_CODING_AGENT_DIR", "ROOM_PI_AGENT_DIR", "PI_CODING_AGENT_DIR"):
        if env := (os.environ.get(key) or "").strip():
            return Path(env).expanduser().resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return (Path(xdg) / "room-tui" / "agent").resolve()


def _legacy_room_pi_agent_dir() -> Path:
    """Pre-scheme-B path (kept for one-time migration)."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return (Path(xdg) / "room-tui" / "pi-agent").resolve()


def system_pi_agent_dir() -> Path:
    """Interactive Pi Agent home (never used by Room workers by default)."""
    return (Path.home() / ".pi" / "agent").resolve()


def ensure_room_pi_agent_dir() -> Path:
    """Create Room agent skeleton; migrate from legacy pi-agent/ if needed."""
    root = room_pi_agent_dir()
    # One-time migrate only for the *default* scheme-B path (not env overrides)
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    default_root = (Path(xdg) / "room-tui" / "agent").resolve()
    if root.resolve() == default_root and not root.exists():
        legacy = _legacy_room_pi_agent_dir()
        if legacy.is_dir() and legacy.resolve() != root.resolve():
            try:
                shutil.copytree(legacy, root)
            except OSError:
                pass
    (root / "skills").mkdir(parents=True, exist_ok=True)
    for name, content in (
        ("settings.json", "{}\n"),
        ("auth.json", "{}\n"),
    ):
        path = root / name
        if not path.exists():
            try:
                path.write_text(content, encoding="utf-8")
            except OSError:
                pass
    # If agent/ already existed empty but legacy pi-agent has skills, pull them
    _migrate_legacy_required_skills(root)
    # All platforms: seed UTF-8-friendly shell env for agent tool commands
    ensure_utf8_shell_settings(root)
    return root


def preferred_utf8_locale() -> str:
    """Locale string for agent shell children (platform-safe).

    - macOS: ``en_US.UTF-8`` (always present; ``C.UTF-8`` is not on older macOS)
    - Windows (Git Bash / MSYS): ``C.UTF-8`` is accepted by common MSYS builds
    - Linux: ``C.UTF-8``
    """
    if sys.platform == "darwin":
        return "en_US.UTF-8"
    return "C.UTF-8"


def ensure_utf8_shell_settings(root: Path) -> None:
    """Seed ``shellCommandPrefix`` so bash tool commands prefer UTF-8.

    Applies on **macOS, Windows 10/11, and Linux** (idempotent via ``ROOM_UTF8=1``).

    Why platform-aware:
    - macOS: shells already UTF-8; we still pin LANG so nested tools don't drift
    - Win10: PowerShell 5.1 + system ACP (e.g. CP936) often mojibake without help
    - Win11: may enable system UTF-8 beta or still use ACP — prefix is safe either way
    - Pi binary also rewrites nested ``powershell``/``pwsh`` and multi-decodes pipes
    """
    sp = root / "settings.json"
    try:
        data: dict = {}
        if sp.is_file():
            import json

            raw = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        prefix = str(data.get("shellCommandPrefix") or "")
        if "ROOM_UTF8=1" in prefix:
            return
        loc = preferred_utf8_locale()
        room_pre = (
            f"export ROOM_UTF8=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "
            f"LANG={loc} LC_ALL={loc}; "
        )
        data["shellCommandPrefix"] = room_pre + prefix
        import json

        sp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, TypeError):
        pass


def _migrate_legacy_required_skills(root: Path) -> None:
    """Copy missing required skills from legacy ~/.config/room-tui/pi-agent."""
    from room_tui.pi_catalog import REQUIRED_SKILLS

    legacy_root = _legacy_room_pi_agent_dir()
    if not legacy_root.is_dir() or legacy_root.resolve() == root.resolve():
        return
    for name in REQUIRED_SKILLS:
        dest = root / "skills" / name
        if (dest / "SKILL.md").is_file():
            continue
        src = legacy_root / "skills" / name
        if not (src / "SKILL.md").is_file():
            continue
        try:
            _copy_skill_tree(src, dest)
        except OSError:
            continue


def apply_room_pi_isolation(*, seed_skills: bool = True) -> Path:
    """Force Room pi isolation for this process and all future children.

    Sets Room-branded + legacy env vars so both new and old pi binaries hit
    ``~/.config/room-tui/agent`` instead of ``~/.pi/agent``.
    """
    global _isolation_ready
    root = ensure_room_pi_agent_dir()
    root_s = str(root)
    # Room-branded binary (piConfig.name = room) reads ROOM_CODING_AGENT_DIR
    os.environ["ROOM_CODING_AGENT_DIR"] = root_s
    os.environ["ROOM_PI_AGENT_DIR"] = root_s
    # Older / stock pi binaries still honor PI_CODING_AGENT_DIR
    os.environ["PI_CODING_AGENT_DIR"] = root_s
    os.environ.pop("PI_HOME", None)

    # Seed whenever any required skill is missing (not only once per process)
    if seed_skills:
        from room_tui.pi_catalog import REQUIRED_SKILLS

        need = any(
            not (root / "skills" / name / "SKILL.md").is_file()
            for name in REQUIRED_SKILLS
        )
        if need or not _isolation_ready:
            try:
                seed_required_skills_into_room_pi()
            except Exception:
                pass
    # Capability env (oob CLI + bundled clang) for agent bash / oob ASan
    try:
        from room_tui.config import capability_subprocess_env

        for k, v in capability_subprocess_env().items():
            if k in (
                "PATH",
                "OOB_CC",
                "ROOM_CC",
                "CC",
                "ROOM_C_TOOLCHAIN",
                "OOB_TOOLCHAIN",
                "ROOM_INSTALL_BIN",
                "ROOM_HOME",
                "ROOM_LIBREOFFICE",
                "PAPER_DERIVED_LIBREOFFICE",
            ):
                os.environ[k] = v
    except Exception:
        pass
    _isolation_ready = True
    return root


def isolation_status() -> dict[str, object]:
    """Snapshot for doctor / diagnostics."""
    root = room_pi_agent_dir()
    sys_root = system_pi_agent_dir()
    env_room_coding = (os.environ.get("ROOM_CODING_AGENT_DIR") or "").strip()
    env_pi = (os.environ.get("PI_CODING_AGENT_DIR") or "").strip()
    env_room = (os.environ.get("ROOM_PI_AGENT_DIR") or "").strip()
    # Any set agent-dir env that does NOT point at Room root is a fail
    env_ok = False
    any_set = False
    for raw in (env_room_coding, env_pi, env_room):
        if not raw:
            continue
        any_set = True
        try:
            if Path(raw).expanduser().resolve() == root.resolve():
                env_ok = True
            else:
                env_ok = False
                break
        except OSError:
            env_ok = False
            break
    if not any_set:
        env_ok = False
    from room_tui.pi_catalog import REQUIRED_SKILLS

    skills_ok = {
        name: (root / "skills" / name / "SKILL.md").is_file()
        for name in REQUIRED_SKILLS
    }
    skill_md = root / "skills" / "paper-derived" / "SKILL.md"
    auth = root / "auth.json"
    sys_auth = sys_root / "auth.json"
    return {
        "room_pi_agent": str(root),
        "system_pi_agent": str(sys_root),
        "ROOM_CODING_AGENT_DIR": env_room_coding,
        "PI_CODING_AGENT_DIR": env_pi,
        "ROOM_PI_AGENT_DIR": env_room,
        "env_points_at_room": env_ok,
        "isolated": env_ok and root.resolve() != sys_root,
        "paper_derived_skill": skill_md.is_file(),
        "required_skills": skills_ok,
        "room_auth_exists": auth.is_file(),
        "room_auth_nonempty": auth.is_file() and auth.stat().st_size > 4,
        "system_auth_exists": sys_auth.is_file() and sys_auth.stat().st_size > 4,
    }


def bundled_skills_root() -> Path | None:
    """Directory of required skills shipped inside the product (or dev tree).

    Frozen onefile: ``sys._MEIPASS/skills`` (embedded by packaging/room.spec).
    Dev: ``<repo>/packaging/skills``.
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "skills")
        try:
            bin_dir = Path(sys.executable).resolve().parent
            candidates.append(bin_dir / "skills")
            candidates.append(bin_dir.parent / "skills")
            candidates.append(bin_dir / "_internal" / "skills")
        except OSError:
            pass
    try:
        # src/room_tui/pi_env.py -> repo root / packaging / skills
        candidates.append(Path(__file__).resolve().parents[2] / "packaging" / "skills")
    except Exception:
        pass
    for c in candidates:
        try:
            if c.is_dir() and any(
                (p / "SKILL.md").is_file() for p in c.iterdir() if p.is_dir()
            ):
                return c
        except OSError:
            continue
    return None


def skill_source_roots() -> list[Path]:
    """Ordered skill source roots for seeding (first hit wins per name)."""
    sources: list[Path] = []
    # Prefer product install dirs, then bundled inside room.exe
    env_bin = (os.environ.get("ROOM_INSTALL_BIN") or "").strip()
    if env_bin:
        b = Path(env_bin).expanduser()
        sources.append(b.parent / "skills")
        sources.append(b / "skills")
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        sources.append(Path(local) / "Programs" / "Room" / "skills")
        sources.append(Path(local) / "Programs" / "Room" / "bin" / "skills")
    sources.append(Path.home() / ".local" / "share" / "room" / "skills")
    sources.append(Path.home() / ".local" / "share" / "room" / "bin" / "skills")
    if getattr(sys, "frozen", False):
        try:
            bin_dir = Path(sys.executable).resolve().parent
            sources.append(bin_dir.parent / "skills")
            sources.append(bin_dir / "skills")
        except OSError:
            pass
    bundled = bundled_skills_root()
    if bundled is not None:
        sources.append(bundled)
    # de-dupe while preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for p in sources:
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def _copy_skill_tree(src: Path, dest: Path) -> None:
    import shutil

    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    # never keep nested engine binary / wheel caches
    for junk in (
        "paper-derived",
        "paper-derived.exe",
        "oob-divzero",
        "oob-divzero.exe",
        "pkg",
    ):
        p = dest / junk
        if p.is_file():
            p.unlink(missing_ok=True)  # type: ignore[arg-type]
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def seed_required_skills_into_room_pi() -> list[str]:
    """Copy missing required skills into Room pi-agent/skills from product/bundled.

    Also mirrors into product ``Programs\\Room\\skills`` when that install root
    exists so discovery stays consistent. Does not touch system ``~/.pi``.

    Returns names that were seeded (or repaired).
    """
    from room_tui.pi_catalog import REQUIRED_SKILLS

    dest_root = ensure_room_pi_agent_dir() / "skills"
    sources = skill_source_roots()
    seeded: list[str] = []

    # Product skills mirror (Windows suite install root, if present)
    product_mirrors: list[Path] = []
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        product_mirrors.append(Path(local) / "Programs" / "Room" / "skills")
    env_bin = (os.environ.get("ROOM_INSTALL_BIN") or "").strip()
    if env_bin:
        product_mirrors.append(Path(env_bin).expanduser().parent / "skills")
    if getattr(sys, "frozen", False):
        try:
            product_mirrors.append(Path(sys.executable).resolve().parent.parent / "skills")
        except OSError:
            pass

    for name in REQUIRED_SKILLS:
        dest = dest_root / name
        src: Path | None = None
        for root in sources:
            cand = root / name
            # Do not treat dest itself as a source
            try:
                if cand.resolve() == dest.resolve():
                    continue
            except OSError:
                pass
            if (cand / "SKILL.md").is_file():
                src = cand
                break

        # Repair missing or empty dest
        need_dest = not (dest / "SKILL.md").is_file()
        if need_dest and src is not None:
            try:
                _copy_skill_tree(src, dest)
                seeded.append(name)
            except OSError:
                continue
        elif not need_dest:
            # dest already good
            pass
        else:
            # no source at all
            continue

        # Mirror into product install skills/ if that tree is under Room home
        if (dest / "SKILL.md").is_file():
            for mirror_root in product_mirrors:
                try:
                    # only write if parent Room dir already exists (real install)
                    if not mirror_root.parent.is_dir():
                        continue
                    mdest = mirror_root / name
                    if (mdest / "SKILL.md").is_file():
                        continue
                    mirror_root.mkdir(parents=True, exist_ok=True)
                    _copy_skill_tree(dest, mdest)
                    if name not in seeded:
                        seeded.append(name)
                except OSError:
                    continue
    return seeded


def pi_agent_environ(base: dict[str, str] | None = None) -> dict[str, str]:
    """Env mapping for spawning Room's pi binary (isolated from ~/.pi).

    Forces ``ROOM_CODING_AGENT_DIR`` (Room-branded pi) and legacy
    ``PI_CODING_AGENT_DIR`` to Room's private tree.

    Also sets platform-appropriate UTF-8 locale env so agent tool stdout
    (bash / PowerShell / curl / Python) stays readable on macOS, Win10, Win11.
    """
    root = apply_room_pi_isolation(seed_skills=False)
    try:
        from room_tui.config import capability_subprocess_env

        env = capability_subprocess_env(base)
    except Exception:
        env = dict(base if base is not None else os.environ)
    root_s = str(root)
    env["ROOM_CODING_AGENT_DIR"] = root_s
    env["ROOM_PI_AGENT_DIR"] = root_s
    env["PI_CODING_AGENT_DIR"] = root_s
    env.pop("PI_HOME", None)
    # Prefer UTF-8 for nested shells / Python / Node children (all platforms)
    loc = preferred_utf8_locale()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", loc)
    env.setdefault("LC_ALL", loc)
    if sys.platform == "darwin":
        env.setdefault("LC_CTYPE", loc)
    if sys.platform == "win32":
        # Hint for Room-branded pi; PS still needs OutputEncoding rewrite (patch)
        env.setdefault("ROOM_FORCE_UTF8", "1")
    return env


def room_pi_settings_path() -> Path:
    return room_pi_agent_dir() / "settings.json"


def room_pi_auth_path() -> Path:
    return room_pi_agent_dir() / "auth.json"


def room_pi_skills_dir() -> Path:
    return ensure_room_pi_agent_dir() / "skills"
