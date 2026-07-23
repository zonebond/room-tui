"""Model readiness / catalog validation."""

from __future__ import annotations

from room_tui.pi_catalog import (
    ModelInfo,
    check_model_status,
    model_is_set,
    parse_model_spec,
    resolve_against_catalog,
)
from room_tui.llm.pi_runner import _prefer_actionable_stderr


def test_parse_model_spec() -> None:
    assert parse_model_spec("bailian/qwen3") == ("bailian", "qwen3")
    assert parse_model_spec("qwen3") == ("", "qwen3")


def test_model_is_set() -> None:
    assert model_is_set("a", "b")
    assert not model_is_set("", "b")
    assert not model_is_set("a", "")


def test_resolve_against_catalog() -> None:
    cat = [
        ModelInfo("deepseek", "deepseek-v4-pro"),
        ModelInfo("openai", "gpt-4o"),
    ]
    hit = resolve_against_catalog("deepseek", "deepseek-v4-pro", cat)
    assert hit is not None and hit.spec == "deepseek/deepseek-v4-pro"
    assert resolve_against_catalog("bailian", "qwen", cat) is None
    assert resolve_against_catalog("", "gpt-4o", cat) is not None


def test_check_model_status_unset() -> None:
    st = check_model_status("", "", catalog=[])
    assert not st.ok
    assert "未配置" in st.reason


def test_check_model_status_unknown_provider() -> None:
    cat = [ModelInfo("deepseek", "deepseek-v4-pro")]
    st = check_model_status("bailian", "qwen3.6-35b-a3b", catalog=cat)
    assert not st.ok
    assert "不支持" in st.reason or "不认识" in st.reason


def test_check_model_status_ok() -> None:
    cat = [ModelInfo("deepseek", "deepseek-v4-pro")]
    st = check_model_status("deepseek", "deepseek-v4-pro", catalog=cat)
    assert st.ok
    assert st.label == "deepseek/deepseek-v4-pro"


def test_prefer_actionable_stderr() -> None:
    raw = (
        "Warning: No project session found with id 'room-agent'; creating a new session.\n"
        'Error: Unknown provider "bailian". Use --list-models\n'
    )
    out = _prefer_actionable_stderr(raw)
    assert "Unknown provider" in out
    assert "No project session" not in out
