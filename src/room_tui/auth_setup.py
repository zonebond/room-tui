"""First-run model auth for Room-isolated pi agent (not system ~/.pi).

Cloud: API keys in ``~/.config/room-tui/agent/auth.json``.
Local (Ollama / LM Studio / vLLM): auth placeholder + ``models.json`` provider.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


Kind = Literal["cloud", "local"]


@dataclass(frozen=True)
class ProviderPreset:
    """Known provider for Room onboarding UI (curated short list)."""

    id: str  # auth.json / models.json provider id
    label: str
    env_var: str
    hint: str = ""
    kind: Kind = "cloud"
    # local only — key is optional (empty = no auth header)
    default_base_url: str = ""
    default_api_key: str = ""
    default_model_id: str = ""


# Product-curated list only (user-selected).
#
# Flow by kind:
#   cloud  — API Key only → save → done; switch models with /model
#   local  — Base URL + optional key + real model ids (self-hosting)
PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "sk-…", kind="cloud"
    ),
    ProviderPreset(
        "minimax-cn", "MiniMax (CN)", "MINIMAX_CN_API_KEY", kind="cloud"
    ),
    ProviderPreset("zai", "GLM", "ZAI_API_KEY", "智谱 GLM", kind="cloud"),
    ProviderPreset(
        "moonshotai-cn",
        "Moonshot (CN)",
        "MOONSHOT_API_KEY",
        "sk-…  ·  月之暗面 Kimi 国内站",
        kind="cloud",
    ),
    ProviderPreset(
        "qwen-token-plan-cn",
        "通义千问 Token Plan (CN)",
        "QWEN_TOKEN_PLAN_CN_API_KEY",
        "sk-sp-…",
        kind="cloud",
    ),
    ProviderPreset(
        "lmstudio",
        "LM Studio (self-hosting)",
        "LMSTUDIO_API_KEY",
        hint="密钥可选；服务端未开鉴权请留空，开了再填真实 Token",
        kind="local",
        default_base_url="http://127.0.0.1:1234/v1",
        default_api_key="",
        # Never invent a fake model id — user must fill real ids from the server
        default_model_id="",
    ),
    ProviderPreset(
        "ollama",
        "Ollama (self-hosting)",
        "OLLAMA_API_KEY",
        hint="密钥可选；默认无需鉴权",
        kind="local",
        default_base_url="http://127.0.0.1:11434/v1",
        default_api_key="",
        default_model_id="",
    ),
    ProviderPreset(
        "vllm",
        "vLLM (self-hosting)",
        "VLLM_API_KEY",
        hint="密钥可选；服务端未开鉴权请留空",
        kind="local",
        default_base_url="http://127.0.0.1:8000/v1",
        default_api_key="",
        default_model_id="",
    ),
)

# Fake/placeholder model ids we used to prefill — never re-inject into the form
_PLACEHOLDER_MODEL_IDS = frozenset(
    {
        "local-model",
        "default",
        "llama3.1:8b",  # was a guess, not the user's machine
    }
)


def is_placeholder_model_id(mid: str) -> bool:
    return (mid or "").strip().lower() in _PLACEHOLDER_MODEL_IDS


def find_preset(provider_id: str) -> ProviderPreset | None:
    pid = (provider_id or "").strip()
    for p in PROVIDER_PRESETS:
        if p.id == pid:
            return p
    return None


def room_auth_path() -> Path:
    from room_tui.pi_env import room_pi_auth_path

    return room_pi_auth_path()


def room_settings_path() -> Path:
    from room_tui.pi_env import room_pi_settings_path

    return room_pi_settings_path()


def room_models_path() -> Path:
    from room_tui.pi_env import room_pi_agent_dir

    return room_pi_agent_dir() / "models.json"


def load_auth() -> dict[str, Any]:
    path = room_auth_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_auth(data: dict[str, Any]) -> Path:
    from room_tui.pi_env import ensure_room_pi_agent_dir

    ensure_room_pi_agent_dir()
    path = room_auth_path()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_models_json() -> dict[str, Any]:
    path = room_models_path()
    if not path.is_file():
        return {"providers": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"providers": {}}
        if "providers" not in data or not isinstance(data["providers"], dict):
            data["providers"] = {}
        return data
    except (OSError, json.JSONDecodeError):
        return {"providers": {}}


def save_models_json(data: dict[str, Any]) -> Path:
    from room_tui.pi_env import ensure_room_pi_agent_dir

    ensure_room_pi_agent_dir()
    path = room_models_path()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def auth_has_any_key(data: dict[str, Any] | None = None) -> bool:
    raw = data if data is not None else load_auth()
    for _k, v in raw.items():
        if not isinstance(v, dict):
            continue
        if v.get("type") == "api_key" and str(v.get("key") or "").strip():
            return True
        if v.get("type") == "oauth" and (
            v.get("access") or v.get("accessToken") or v.get("token")
        ):
            return True
    return False


def list_configured_providers() -> list[str]:
    out: list[str] = []
    for k, v in load_auth().items():
        if isinstance(v, dict) and v.get("type") == "api_key" and str(v.get("key") or "").strip():
            out.append(str(k))
        elif isinstance(v, dict) and v.get("type") == "oauth":
            out.append(str(k))
    # local providers may only appear in models.json
    models = load_models_json()
    for k in (models.get("providers") or {}):
        if k not in out:
            out.append(str(k))
    # only show curated ids in status
    known = {p.id for p in PROVIDER_PRESETS}
    return sorted(x for x in out if x in known or True)


def set_api_key(provider_id: str, api_key: str) -> Path:
    """Write/replace API key for *provider_id* in Room auth.json."""
    pid = (provider_id or "").strip()
    key = (api_key or "").strip()
    if not pid:
        raise ValueError("provider id required")
    if not key:
        raise ValueError("api key required")
    data = load_auth()
    data[pid] = {"type": "api_key", "key": key}
    return save_auth(data)


def get_local_provider_config(provider_id: str) -> dict[str, Any] | None:
    """Return existing models.json entry for *provider_id*, or None."""
    pid = (provider_id or "").strip()
    if not pid:
        return None
    providers = load_models_json().get("providers") or {}
    if not isinstance(providers, dict):
        return None
    entry = providers.get(pid)
    return entry if isinstance(entry, dict) else None


def local_provider_model_ids(entry: dict[str, Any] | None) -> list[str]:
    """All model ids stored for a local provider entry (order preserved)."""
    if not entry:
        return []
    models = entry.get("models")
    if not isinstance(models, list):
        return []
    out: list[str] = []
    for m in models:
        if isinstance(m, dict):
            mid = str(m.get("id") or "").strip()
        else:
            mid = str(m or "").strip()
        if mid and mid not in out:
            out.append(mid)
    return out


def local_provider_model_id(entry: dict[str, Any] | None) -> str:
    """First model id stored for a local provider entry (compat)."""
    ids = local_provider_model_ids(entry)
    return ids[0] if ids else ""


def parse_model_id_list(*parts: str | list[str] | None) -> list[str]:
    """Split comma / semicolon / newline separated model ids; dedupe order-preserving."""
    import re

    out: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, list):
            tokens = [str(x) for x in part]
        else:
            tokens = re.split(r"[,;\n]+", str(part))
        for raw in tokens:
            mid = raw.strip()
            if mid and mid not in out:
                out.append(mid)
    return out


def _model_spec(provider: str, model: str) -> str:
    prov = (provider or "").strip()
    mid = (model or "").strip()
    if not mid:
        return ""
    if prov and mid.startswith(prov + "/"):
        return mid
    if prov:
        return f"{prov}/{mid}"
    return mid


def _enabled_spec_provider(spec: str) -> str:
    s = (spec or "").strip()
    if "/" in s:
        return s.split("/", 1)[0].strip()
    return ""


def configure_local_provider(
    provider_id: str,
    *,
    base_url: str,
    api_key: str = "",
    model_id: str = "",
    model_ids: list[str] | None = None,
) -> tuple[Path, Path]:
    """Upsert a local OpenAI-compatible server — **one brand = one connection**.

    - Same *provider_id* always updates the single ``providers.<id>`` entry
      (Base URL + optional key), never creates a second brand slot.
    - *model_ids* (or comma-separated *model_id*) may list **multiple** models
      under that brand. Empty input keeps existing models when re-editing.

    Self-hosting key policy:
      - key empty  → no auth.json entry, no models.json apiKey (no Authorization)
      - key set    → standard api_key in auth.json and models.json apiKey
    """
    preset = find_preset(provider_id)
    pid = (provider_id or "").strip()
    if not pid:
        raise ValueError("provider id required")
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("base URL required for local provider")
    key = (api_key or "").strip()

    from room_tui.pi_env import ensure_room_pi_agent_dir

    ensure_room_pi_agent_dir()
    existing = get_local_provider_config(pid)
    mids = parse_model_id_list(model_ids, model_id)
    if not mids:
        # Keep prior real models when re-saving URL/key without touching model field
        mids = [
            x
            for x in local_provider_model_ids(existing)
            if not is_placeholder_model_id(x)
        ]
    else:
        mids = [x for x in mids if not is_placeholder_model_id(x)]
    if not mids:
        raise ValueError(
            "请填写至少一个真实模型 id（与本机服务中已加载的模型一致，勿用占位名）"
        )

    auth = load_auth()
    if key:
        auth[pid] = {"type": "api_key", "key": key}
    else:
        auth.pop(pid, None)
    auth_path = save_auth(auth)

    data = load_models_json()
    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        data["providers"] = providers
    # One brand → one connection entry; models list may have multiple ids
    entry: dict[str, Any] = {
        "baseUrl": url,
        "api": "openai-completions",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
        },
        "models": [{"id": mid} for mid in mids],
    }
    if key:
        entry["apiKey"] = key
    providers[pid] = entry
    models_path = save_models_json(data)

    # Sync enabledModels: all models under brand; first is default
    try:
        sync_provider_enabled_models(pid, mids, default_model=mids[0])
    except Exception:
        pass
    return auth_path, models_path


def sync_provider_enabled_models(
    provider: str,
    model_ids: list[str],
    *,
    default_model: str = "",
) -> Path | None:
    """Replace this brand's enabledModels slots with *model_ids* (multi ok)."""
    from room_tui.config import load_config, save_config
    from room_tui.pi_env import ensure_room_pi_agent_dir

    prov = (provider or "").strip()
    mids = parse_model_id_list(model_ids)
    if not prov or not mids:
        return None
    default = (default_model or mids[0]).strip()
    if default not in mids:
        mids = [default] + mids
    ensure_room_pi_agent_dir()
    sp = room_settings_path()
    settings: dict[str, Any] = {}
    if sp.is_file():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                settings = raw
        except (OSError, json.JSONDecodeError):
            settings = {}
    settings["defaultProvider"] = prov
    settings["defaultModel"] = default
    enabled = settings.get("enabledModels")
    if not isinstance(enabled, list):
        enabled = []
    # Drop previous specs for this brand only
    kept: list[Any] = []
    for x in enabled:
        s = str(x).strip()
        if not s:
            continue
        if _enabled_spec_provider(s) == prov or s == prov:
            continue
        kept.append(s)
    specs = [_model_spec(prov, mid) for mid in mids]
    # Default first
    default_spec = _model_spec(prov, default)
    ordered = [default_spec] + [s for s in specs if s and s != default_spec]
    settings["enabledModels"] = ordered + kept
    settings["enabledModels"] = settings["enabledModels"][:48]
    sp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    cfg = load_config()
    cfg.provider = prov
    cfg.model = default
    return save_config(cfg)


def set_room_default_model(provider: str, model: str) -> Path | None:
    """Update Room default model; keeps other models of the same brand."""
    from room_tui.config import load_config, save_config
    from room_tui.pi_env import ensure_room_pi_agent_dir

    prov = (provider or "").strip()
    mid = (model or "").strip()
    if not mid:
        return None
    ensure_room_pi_agent_dir()
    sp = room_settings_path()
    settings: dict[str, Any] = {}
    if sp.is_file():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                settings = raw
        except (OSError, json.JSONDecodeError):
            settings = {}
    if prov:
        settings["defaultProvider"] = prov
    settings["defaultModel"] = mid
    spec = _model_spec(prov, mid)
    enabled = settings.get("enabledModels")
    if not isinstance(enabled, list):
        enabled = []
    # Keep other brands + other models under this brand; put default first
    kept: list[Any] = []
    for x in enabled:
        s = str(x).strip()
        if not s or s == spec:
            continue
        kept.append(s)
    settings["enabledModels"] = ([spec] if spec else []) + kept
    settings["enabledModels"] = settings["enabledModels"][:48]
    sp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    cfg = load_config()
    cfg.provider = prov
    cfg.model = mid
    return save_config(cfg)


def remove_provider_config(provider_id: str) -> bool:
    """Delete one brand's Room config (auth + models.json + enabledModels).

    Returns True if anything was removed.
    """
    from room_tui.config import load_config, save_config
    from room_tui.pi_env import ensure_room_pi_agent_dir

    pid = (provider_id or "").strip()
    if not pid:
        return False
    ensure_room_pi_agent_dir()
    changed = False

    auth = load_auth()
    if pid in auth:
        auth.pop(pid, None)
        save_auth(auth)
        changed = True

    data = load_models_json()
    providers = data.get("providers")
    if isinstance(providers, dict) and pid in providers:
        providers.pop(pid, None)
        data["providers"] = providers
        save_models_json(data)
        changed = True

    sp = room_settings_path()
    settings: dict[str, Any] = {}
    if sp.is_file():
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                settings = raw
        except (OSError, json.JSONDecodeError):
            settings = {}
    enabled = settings.get("enabledModels")
    if isinstance(enabled, list):
        new_en = [
            x
            for x in enabled
            if str(x).strip()
            and _enabled_spec_provider(str(x)) != pid
            and str(x).strip() != pid
        ]
        if new_en != list(enabled):
            settings["enabledModels"] = new_en
            changed = True
    if str(settings.get("defaultProvider") or "").strip() == pid:
        settings.pop("defaultProvider", None)
        settings.pop("defaultModel", None)
        changed = True
    if settings:
        sp.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    try:
        cfg = load_config()
        if (cfg.provider or "").strip() == pid:
            cfg.provider = ""
            cfg.model = ""
            save_config(cfg)
            changed = True
    except Exception:
        pass
    return changed


def needs_model_setup(*, pi_bin: str = "pi") -> bool:
    """True when Room has no usable auth and catalog is empty."""
    if auth_has_any_key():
        return False
    for p in PROVIDER_PRESETS:
        if p.env_var and (os.environ.get(p.env_var) or "").strip():
            return False
    try:
        from room_tui.pi_catalog import catalog_models

        if catalog_models(pi_bin=pi_bin):
            return False
    except Exception:
        pass
    return True


def setup_status_lines(*, pi_bin: str = "pi") -> list[str]:
    """Human lines for empty model picker / doctor (short, product tone)."""
    configured = list_configured_providers()
    curated = {p.id for p in PROVIDER_PRESETS}
    shown = [c for c in configured if c in curated] or configured
    if shown:
        labels = []
        for c in shown[:6]:
            p = find_preset(c)
            labels.append(p.label if p else c)
        return [f"已配置: {', '.join(labels)}"]
    return ["尚未配置模型 · 选服务商并完成密钥 / 本机服务设置"]


def provider_is_configured(provider_id: str) -> bool:
    """True when Room has saved auth and/or models.json entry for *provider_id*."""
    pid = (provider_id or "").strip()
    if not pid:
        return False
    return pid in list_configured_providers()


def provider_list_label(p: ProviderPreset) -> str:
    """Display label for OptionList (no raw id clutter)."""
    configured = provider_is_configured(p.id)
    badge = "  ·  已配置" if configured else ""
    return f"{p.label}{badge}"
