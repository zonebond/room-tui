"""Room isolated auth.json model setup helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from room_tui.auth_setup import (
    PROVIDER_PRESETS,
    auth_has_any_key,
    configure_local_provider,
    load_auth,
    set_api_key,
    set_room_default_model,
)


def test_presets_are_curated() -> None:
    ids = [p.id for p in PROVIDER_PRESETS]
    assert ids == [
        "deepseek",
        "minimax-cn",
        "zai",
        "moonshotai-cn",  # Moonshot (CN) / Kimi — after GLM, before 通义
        "qwen-token-plan-cn",
        "lmstudio",
        "ollama",
        "vllm",
    ]
    assert len(PROVIDER_PRESETS) == 8
    moon = next(p for p in PROVIDER_PRESETS if p.id == "moonshotai-cn")
    assert moon.label == "Moonshot (CN)"
    assert moon.env_var == "MOONSHOT_API_KEY"
    assert moon.kind == "cloud"


def test_set_api_key_writes_room_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    path = set_api_key("deepseek", "sk-test-key")
    assert path == agent / "auth.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["deepseek"]["type"] == "api_key"
    assert data["deepseek"]["key"] == "sk-test-key"
    assert auth_has_any_key()
    assert load_auth()["deepseek"]["key"] == "sk-test-key"


def test_set_room_default_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("ROOM_CONFIG", str(cfg_dir / "config.toml"))
    set_api_key("deepseek", "sk-ds")
    set_room_default_model("deepseek", "deepseek-chat")
    settings = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    assert settings.get("defaultProvider") == "deepseek"
    assert settings.get("defaultModel") == "deepseek-chat"


def test_configure_local_requires_real_model_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    with pytest.raises(ValueError, match="模型"):
        configure_local_provider(
            "lmstudio",
            base_url="http://127.0.0.1:1234/v1",
            model_id="local-model",  # placeholder — rejected
            api_key="",
        )


def test_configure_local_ollama_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    auth_p, models_p = configure_local_provider(
        "ollama",
        base_url="http://127.0.0.1:11434/v1",
        model_id="qwen2.5:7b",
        api_key="",  # optional — no auth
    )
    assert auth_p.is_file()
    auth = json.loads(auth_p.read_text(encoding="utf-8"))
    assert "ollama" not in auth
    models = json.loads(models_p.read_text(encoding="utf-8"))
    ol = models["providers"]["ollama"]
    assert ol["baseUrl"] == "http://127.0.0.1:11434/v1"
    assert "apiKey" not in ol
    assert any(m.get("id") == "qwen2.5:7b" for m in ol["models"])


def test_configure_local_with_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    auth_p, models_p = configure_local_provider(
        "lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        api_key="sk-real-token",
        model_id="qwen/qwen3",
    )
    auth = json.loads(auth_p.read_text(encoding="utf-8"))
    assert auth["lmstudio"]["key"] == "sk-real-token"
    models = json.loads(models_p.read_text(encoding="utf-8"))
    assert models["providers"]["lmstudio"]["apiKey"] == "sk-real-token"


def test_configure_local_clears_stale_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    configure_local_provider(
        "lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        api_key="lmstudio",  # bad placeholder once
        model_id="m",
    )
    # reconfigure without key → must remove placeholder
    auth_p, models_p = configure_local_provider(
        "lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        api_key="",
        model_id="m",
    )
    auth = json.loads(auth_p.read_text(encoding="utf-8"))
    assert "lmstudio" not in auth
    models = json.loads(models_p.read_text(encoding="utf-8"))
    assert "apiKey" not in models["providers"]["lmstudio"]


def test_configure_local_one_brand_multi_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One brand → one connection; multiple models allowed; re-edit upserts brand."""
    agent = tmp_path / "agent"
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("ROOM_CONFIG", str(cfg_dir / "config.toml"))
    configure_local_provider(
        "lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        api_key="",
        model_id="qwen/qwen3.5-35b-a3b",
    )
    # Re-edit connection + multi models (comma list)
    configure_local_provider(
        "lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        api_key="sk-real",
        model_id="qwen/qwen3.6-35b-a3b, other-model",
    )
    models = json.loads((agent / "models.json").read_text(encoding="utf-8"))
    assert list(models["providers"].keys()) == ["lmstudio"]
    lm = models["providers"]["lmstudio"]
    assert lm["apiKey"] == "sk-real"
    assert lm["models"] == [
        {"id": "qwen/qwen3.6-35b-a3b"},
        {"id": "other-model"},
    ]
    settings = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    enabled = settings.get("enabledModels") or []
    lm_specs = [s for s in enabled if str(s).startswith("lmstudio/")]
    assert "lmstudio/qwen/qwen3.6-35b-a3b" in lm_specs
    assert "lmstudio/other-model" in lm_specs
    assert len(lm_specs) == 2


def test_remove_provider_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from room_tui.auth_setup import remove_provider_config

    agent = tmp_path / "agent"
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("ROOM_CONFIG", str(cfg_dir / "config.toml"))
    configure_local_provider(
        "lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        api_key="sk-x",
        model_ids=["a", "b"],
    )
    assert remove_provider_config("lmstudio") is True
    assert "lmstudio" not in load_auth()
    models = json.loads((agent / "models.json").read_text(encoding="utf-8"))
    assert "lmstudio" not in (models.get("providers") or {})
    settings = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    enabled = settings.get("enabledModels") or []
    assert not any(str(s).startswith("lmstudio/") for s in enabled)


def test_set_room_default_keeps_sibling_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = tmp_path / "agent"
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("ROOM_CODING_AGENT_DIR", str(agent))
    monkeypatch.setenv("ROOM_CONFIG", str(cfg_dir / "config.toml"))
    configure_local_provider(
        "lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        model_ids=["a", "b"],
    )
    set_room_default_model("lmstudio", "b")
    settings = json.loads((agent / "settings.json").read_text(encoding="utf-8"))
    assert settings["defaultModel"] == "b"
    enabled = settings.get("enabledModels") or []
    assert enabled[0] == "lmstudio/b"
    assert "lmstudio/a" in enabled
