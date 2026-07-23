"""Discover Pi models and skills for Room slash commands."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    provider: str
    model: str

    @property
    def spec(self) -> str:
        if self.provider and self.model:
            if self.model.startswith(self.provider + "/"):
                return self.model
            return f"{self.provider}/{self.model}"
        return self.model or self.provider or ""


@dataclass(frozen=True)
class SkillInfo:
    name: str
    path: Path
    description: str = ""
    version: str = ""


def pi_settings() -> dict[str, Any]:
    """Load **Room-isolated** pi agent settings (not the system ~/.pi agent)."""
    from room_tui.pi_env import room_pi_settings_path

    p = room_pi_settings_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def enabled_model_specs() -> list[str]:
    raw = pi_settings().get("enabledModels") or []
    out: list[str] = []
    for item in raw:
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def parse_model_spec(spec: str) -> tuple[str, str]:
    """Return (provider, model) from ``provider/model`` or bare model id."""
    s = (spec or "").strip()
    if not s:
        return "", ""
    # thinking suffix: model:high
    if ":" in s and "/" not in s.split(":", 1)[0]:
        # e.g. sonnet:high — leave as model token; provider empty
        return "", s
    if "/" in s:
        # provider/model... (model may contain /)
        prov, _, rest = s.partition("/")
        return prov.strip(), rest.strip()
    return "", s


def list_models_via_pi(
    search: str = "",
    *,
    pi_bin: str = "pi",
    timeout_s: float = 20.0,
) -> list[ModelInfo]:
    """Parse ``pi --list-models [search]`` table output (Room pi-agent env).

    On Windows, runs without a console so ``process.title = "pi"`` cannot
    rename the parent Windows Terminal tab (see console_title helpers).
    """
    from room_tui.console_title import run_subprocess_no_console
    from room_tui.pi_env import pi_agent_environ

    cmd = [pi_bin, "--list-models"]
    if search.strip():
        cmd.append(search.strip())
    try:
        proc = run_subprocess_no_console(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=pi_agent_environ(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    rows: list[ModelInfo] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.lower().startswith("provider"):
            continue
        # Columns: provider  model  context  max-out  thinking  images
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 2:
            continue
        prov, model = parts[0].strip(), parts[1].strip()
        if not prov or not model:
            continue
        if prov.lower() in ("provider", "---"):
            continue
        rows.append(ModelInfo(provider=prov, model=model))
    return rows


def list_models(
    search: str = "",
    *,
    pi_bin: str = "pi",
    prefer_enabled: bool = True,
) -> list[ModelInfo]:
    """Prefer enabledModels from settings; fall back to pi --list-models.

    When ``prefer_enabled`` is True but settings list models that pi cannot
    serve, callers should validate with :func:`resolve_against_catalog` or
    list with ``prefer_enabled=False`` for the picker.
    """
    q = (search or "").strip().lower()
    enabled = enabled_model_specs()
    out: list[ModelInfo] = []
    if prefer_enabled and enabled:
        for spec in enabled:
            prov, model = parse_model_spec(spec)
            if not model:
                continue
            if q and q not in spec.lower() and q not in model.lower():
                continue
            out.append(ModelInfo(provider=prov, model=model))
        if out:
            return out
    return list_models_via_pi(search, pi_bin=pi_bin)


def catalog_models(
    search: str = "",
    *,
    pi_bin: str = "pi",
) -> list[ModelInfo]:
    """Models actually advertised by the active ``pi`` binary (source of truth)."""
    return list_models_via_pi(search, pi_bin=pi_bin)


def model_is_set(provider: str, model: str) -> bool:
    """True when both provider and model ids are non-empty."""
    return bool((provider or "").strip() and (model or "").strip())


def resolve_against_catalog(
    provider: str,
    model: str,
    catalog: list[ModelInfo],
) -> ModelInfo | None:
    """Match configured provider/model to a catalog row (flexible)."""
    prov = (provider or "").strip()
    mid = (model or "").strip()
    if not mid and not prov:
        return None
    # Exact spec
    want = f"{prov}/{mid}" if prov and mid and not mid.startswith(prov + "/") else (mid or prov)
    for m in catalog:
        if m.spec == want or (m.provider == prov and m.model == mid):
            return m
    # Bare model id
    if mid:
        for m in catalog:
            if m.model == mid or m.model.endswith("/" + mid):
                return m
    # Spec equality ignoring case
    wl = want.lower()
    for m in catalog:
        if m.spec.lower() == wl:
            return m
    return None


@dataclass(frozen=True)
class ModelStatus:
    """Whether Room may start an Agent turn with the current model config."""

    ok: bool
    provider: str = ""
    model: str = ""
    label: str = ""
    reason: str = ""  # empty when ok
    hint: str = ""  # user action
    catalog_count: int = 0


def check_model_status(
    provider: str,
    model: str,
    *,
    pi_bin: str = "pi",
    catalog: list[ModelInfo] | None = None,
) -> ModelStatus:
    """Validate model for chat (Grok-like readiness).

    - unset → not ok, prompt configure
    - set but not in pi catalog → not ok (e.g. bailian unknown to suite pi)
    - catalog empty (pi broken) → not ok if unset; if set, warn but allow?  
      Safer: if catalog empty and set, allow with reason warning only when
      we cannot list — still try chat. If catalog non-empty and missing → block.
    """
    prov = (provider or "").strip()
    mid = (model or "").strip()
    label = ""
    if prov and mid:
        label = mid if mid.startswith(prov + "/") or "/" in mid else f"{prov}/{mid}"
    elif mid:
        label = mid
    elif prov:
        label = prov

    if not model_is_set(prov, mid):
        return ModelStatus(
            ok=False,
            provider=prov,
            model=mid,
            label=label or "—",
            reason="未配置模型",
            hint="Ctrl+M 连接模型  ·  /model 切换  ·  /model list",
            catalog_count=0,
        )

    rows = catalog if catalog is not None else catalog_models(pi_bin=pi_bin)
    if not rows:
        # Cannot verify — leave configured values, mark ok with empty catalog
        # so offline/dev still works; chat may still fail at runtime.
        return ModelStatus(
            ok=True,
            provider=prov,
            model=mid,
            label=label,
            reason="",
            hint="",
            catalog_count=0,
        )

    hit = resolve_against_catalog(prov, mid, rows)
    if hit is None:
        return ModelStatus(
            ok=False,
            provider=prov,
            model=mid,
            label=label,
            reason=f"当前 Room 不支持模型 {label}",
            hint="Ctrl+M 从可用列表选择  ·  /model list",
            catalog_count=len(rows),
        )
    return ModelStatus(
        ok=True,
        provider=hit.provider,
        model=hit.model,
        label=hit.spec,
        reason="",
        hint="",
        catalog_count=len(rows),
    )


# Product required skills (suite installers place these for Room Agent).
REQUIRED_SKILLS: tuple[str, ...] = ("paper-derived", "oob-divzero")


def _product_skill_dirs() -> list[Path]:
    """Suite + Room-isolated agent skills (never system ~/.pi by default)."""
    import sys

    from room_tui.pi_env import bundled_skills_root, room_pi_skills_dir

    out: list[Path] = []
    # Room's private agent skills (~/.config/room-tui/agent/skills)
    try:
        out.append(room_pi_skills_dir())
    except OSError:
        pass
    # Skills embedded inside room.exe (PyInstaller _MEIPASS/skills)
    try:
        bundled = bundled_skills_root()
        if bundled is not None:
            out.append(bundled)
    except Exception:
        pass
    # Frozen product: room.exe lives in …/Room/bin/room.exe → …/Room/skills
    if getattr(sys, "frozen", False):
        try:
            bin_dir = Path(sys.executable).resolve().parent
            out.append(bin_dir.parent / "skills")
            out.append(bin_dir / "skills")
        except OSError:
            pass
    # Explicit install bin (install.ps1 sets User env ROOM_INSTALL_BIN)
    env_bin = (os.environ.get("ROOM_INSTALL_BIN") or "").strip()
    if env_bin:
        try:
            b = Path(env_bin).expanduser().resolve()
            out.append(b.parent / "skills")
            out.append(b / "skills")
        except OSError:
            pass
    # Windows product install (always, even without env)
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        out.append(Path(local) / "Programs" / "Room" / "skills")
        out.append(Path(local) / "Programs" / "Room" / "bin" / "skills")
    # macOS / Linux product install
    out.append(Path.home() / ".local" / "share" / "room" / "skills")
    # Config-local override + legacy Room path
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    out.append(Path(xdg) / "room-tui" / "skills")
    out.append(Path(xdg) / "room-tui" / "pi-agent" / "skills")  # legacy
    out.append(Path(xdg) / "room-tui" / "agent" / "skills")
    return out


def _skill_dirs() -> list[Path]:
    """Skill roots for Room: product + Room pi-agent first; optional extra dirs.

    System Pi Agent ``~/.pi/agent/skills`` is **not** scanned by default so
    Room's required skills and the interactive Pi product stay separated.
    Set ``ROOM_SCAN_SYSTEM_PI_SKILLS=1`` to also include ~/.pi (dev escape).
    """
    home = Path.home()
    candidates: list[Path] = []
    # Product + Room-isolated skills first
    candidates.extend(_product_skill_dirs())
    # Optional: scan system Pi / other agents (off by default)
    if (os.environ.get("ROOM_SCAN_SYSTEM_PI_SKILLS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        candidates.extend(
            [
                home / ".pi" / "agent" / "skills",
                home / ".agents" / "skills",
                home / ".claude" / "skills",
                home / ".grok" / "skills",
                home / ".grok" / "bundled" / "skills",
            ]
        )
    # project-local (workspace) still allowed
    cwd = Path.cwd()
    candidates.extend(
        [
            cwd / ".pi" / "skills",
            cwd / ".agents" / "skills",
            cwd / ".claude" / "skills",
            cwd / ".grok" / "skills",
            cwd / "skills",
        ]
    )
    seen: set[Path] = set()
    out: list[Path] = []
    for p in candidates:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen or not p.is_dir():
            continue
        seen.add(rp)
        out.append(p)
    return out


def missing_required_skills() -> list[str]:
    """Names from REQUIRED_SKILLS not discoverable on this machine."""
    have = {s.name.lower() for s in list_skills()}
    # also match folder keys
    for s in list_skills():
        try:
            have.add(s.path.parent.name.lower())
        except Exception:
            pass
    return [n for n in REQUIRED_SKILLS if n.lower() not in have]


def _parse_skill_md(path: Path) -> tuple[str, str, str]:
    """Return (name, description, version) from SKILL.md frontmatter."""
    name = path.parent.name
    desc = ""
    version = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return name, desc, version
    if text.lstrip().startswith("---"):
        parts = text.lstrip().split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            for line in fm.splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip().strip("\"'")
                if k == "name" and v:
                    name = v
                elif k == "description" and v:
                    desc = v
                elif k == "version" and v:
                    version = v
    if not desc:
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("---"):
                desc = s[:160]
                break
    return name, desc, version


def list_skills() -> list[SkillInfo]:
    """Discover skills (name → path), first hit wins."""
    seen: set[str] = set()
    out: list[SkillInfo] = []
    for root in _skill_dirs():
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            skill_md = entry / "SKILL.md" if entry.is_dir() else None
            if entry.is_file() and entry.name.endswith(".md"):
                # single-file skill
                skill_md = entry
                key = entry.stem
            elif skill_md and skill_md.is_file():
                key = entry.name
            else:
                continue
            if key in seen:
                continue
            name, desc, ver = _parse_skill_md(skill_md)
            seen.add(key)
            out.append(
                SkillInfo(
                    name=name or key,
                    path=skill_md if skill_md.is_file() else entry,
                    description=desc,
                    version=ver,
                )
            )
    out.sort(key=lambda s: s.name.lower())
    return out


def find_skill(name: str) -> SkillInfo | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    for s in list_skills():
        if s.name.lower() == key or s.path.parent.name.lower() == key:
            return s
        if s.path.stem.lower() == key:
            return s
    # prefix unique
    hits = [
        s
        for s in list_skills()
        if s.name.lower().startswith(key) or s.path.parent.name.lower().startswith(key)
    ]
    if len(hits) == 1:
        return hits[0]
    return None
