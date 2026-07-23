"""First-run / edit model setup modal — curated providers only (Room agent)."""

from __future__ import annotations

from typing import Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from room_tui.auth_setup import (
    PROVIDER_PRESETS,
    ProviderPreset,
    configure_local_provider,
    find_preset,
    get_local_provider_config,
    is_placeholder_model_id,
    load_auth,
    local_provider_model_ids,
    parse_model_id_list,
    provider_is_configured,
    provider_list_label,
    remove_provider_config,
    set_api_key,
    set_room_default_model,
    setup_status_lines,
)
from room_tui.pi_catalog import ModelInfo, catalog_models

# Match /model slash accent
_ACCENT = "#57A5E2"
_DANGER = "#E06C75"
_WARN = "#E5C07B"  # confirmation / caution (yellow)
_MUTED = "dim"


def find_preset_safe(pid: str) -> ProviderPreset | None:
    return find_preset(pid)


def _display_brand(label: str) -> str:
    """Short brand line (drop self-hosting noise)."""
    return (
        (label or "")
        .replace(" (self-hosting)", "")
        .replace("(self-hosting)", "")
        .strip()
    )


class ModelSetupScreen(ModalScreen[ModelInfo | None]):
    """连接模型：选服务商 → 填密钥/本机服务。"""

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
        # priority=True: Input steals ctrl+d as delete_right — must win over focused field
        Binding("ctrl+r", "reset_provider", "重置", show=True, priority=True),
        Binding("ctrl+d", "delete_provider", "删除", show=True, priority=True),
    ]

    CSS = """
    ModelSetupScreen {
        align: center middle;
    }
    #model-setup-panel {
        width: 82;
        max-width: 96;
        height: auto;
        max-height: 32;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }
    #model-setup-title {
        text-style: bold;
        width: 100%;
        text-align: left;
        margin-bottom: 0;
        padding: 0;
        height: 1;
    }
    #model-setup-step {
        color: $text-muted;
        width: 100%;
        text-align: left;
        margin-bottom: 0;
        padding: 0;
        height: 1;
    }
    /* Always reserve 1 row so confirm/status never jumps the form */
    #model-setup-status {
        color: $text-muted;
        width: 100%;
        text-align: left;
        margin: 0 0 1 0;
        padding: 0 0;
        height: 1;
        min-height: 1;
        max-height: 1;
        overflow: hidden;
    }
    #model-setup-status.warn {
        color: #E5C07B;
        text-style: bold;
    }
    #model-setup-status.ok {
        color: $text-muted;
        text-style: none;
    }
    #model-setup-list {
        height: 10;
        max-height: 11;
        width: 100%;
        border: solid $primary 40%;
        margin: 1 0 1 0;
    }
    .ms-field {
        width: 100%;
        height: auto;
        margin: 0 0 0 0;
        padding: 0;
        layout: vertical;
    }
    .ms-label {
        width: 100%;
        color: $text-muted;
        text-align: left;
        margin: 0;
        padding: 0;
        height: 1;
    }
    .ms-field Input {
        width: 100%;
        margin: 0 0 1 0;
        padding: 0 1;
    }
    #model-setup-hint {
        color: $text-muted;
        width: 100%;
        text-align: left;
        padding: 0;
        margin-top: 0;
        height: 1;
        max-height: 1;
        overflow: hidden;
    }
    """

    def __init__(self, *, pi_bin: str = "pi") -> None:
        super().__init__()
        self._pi_bin = pi_bin
        self._step = "provider"  # provider | key | local
        self._preset: ProviderPreset | None = None
        self._catalog: list[ModelInfo] = []
        # Pending dangerous action awaiting second keypress
        self._confirm: Literal["reset", "delete"] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="model-setup-panel"):
            yield Static("连接模型", id="model-setup-title")
            yield Static("选择服务商", id="model-setup-step")
            yield Static("", id="model-setup-status")
            yield OptionList(id="model-setup-list")
            with Vertical(id="model-setup-url-field", classes="ms-field"):
                yield Static("Base URL", id="model-setup-url-label", classes="ms-label")
                yield Input(
                    placeholder="http://127.0.0.1:1234/v1",
                    id="model-setup-url",
                )
            with Vertical(id="model-setup-key-field", classes="ms-field"):
                yield Static("API Key", id="model-setup-key-label", classes="ms-label")
                yield Input(
                    placeholder="可选 · 未开鉴权请留空",
                    password=True,
                    id="model-setup-key",
                )
            with Vertical(id="model-setup-model-field", classes="ms-field"):
                yield Static("模型", id="model-setup-model-label", classes="ms-label")
                yield Input(
                    placeholder="多个 id 用逗号分隔",
                    id="model-setup-model",
                )
            yield Static("", id="model-setup-hint")

    def on_mount(self) -> None:
        for wid in (
            "#model-setup-url-field",
            "#model-setup-key-field",
            "#model-setup-model-field",
        ):
            self.query_one(wid).display = False
        # Reserve status row immediately (empty), then optional info
        self._set_status("")
        lines = setup_status_lines(pi_bin=self._pi_bin)
        if lines:
            self._set_status(lines[0])
        self._reload_provider_list()
        self._refresh_hint()
        self.query_one("#model-setup-list", OptionList).focus()

    # ── footer ──────────────────────────────────────────────────────────

    def _target_provider_id(self) -> str:
        if self._step == "provider":
            return self._highlighted_provider_id()
        if self._preset is not None:
            return self._preset.id
        return ""

    def _can_reset_or_delete(self) -> bool:
        """Dangerous actions only when this brand already has saved config."""
        return provider_is_configured(self._target_provider_id())

    def _refresh_hint(self) -> None:
        """Single-line footer: Esc · Enter · Ctrl+R · Ctrl+D (stateful)."""
        hint = self.query_one("#model-setup-hint", Static)

        if self._confirm == "reset":
            hint.update(
                f"[dim]Esc 取消[/dim]  ·  "
                f"[bold {_DANGER}]再按 Ctrl+R 确认重置[/bold {_DANGER}]"
            )
            return
        if self._confirm == "delete":
            hint.update(
                f"[dim]Esc 取消[/dim]  ·  "
                f"[bold {_DANGER}]再按 Ctrl+D 确认删除[/bold {_DANGER}]"
            )
            return

        can = self._can_reset_or_delete()
        if self._step == "provider":
            enter = f"[bold {_ACCENT}]Enter 配置[/bold {_ACCENT}]"
            lead = "[dim]Esc 取消[/dim]"
        elif self._step == "key":
            enter = f"[bold {_ACCENT}]Enter 保存[/bold {_ACCENT}]"
            lead = "[dim]Esc 取消[/dim]"
        else:
            # local form — Tab switches fields; Enter still means save on model
            enter = f"[bold {_ACCENT}]Enter 保存[/bold {_ACCENT}]"
            lead = "[dim]Esc 取消[/dim]"

        if can:
            # Same danger accent for both destructive actions
            reset_s = f"[bold {_DANGER}]Ctrl+R 重置[/bold {_DANGER}]"
            delete_s = f"[bold {_DANGER}]Ctrl+D 删除[/bold {_DANGER}]"
        else:
            reset_s = f"[{_MUTED}]Ctrl+R 重置[/{_MUTED}]"
            delete_s = f"[{_MUTED}]Ctrl+D 删除[/{_MUTED}]"

        # Order fixed: Esc · Enter · Ctrl+R · Ctrl+D  — one line, no wrap
        hint.update(f"{lead}  ·  {enter}  ·  {reset_s}  ·  {delete_s}")

    # ── cancel / confirm ────────────────────────────────────────────────

    def action_cancel(self) -> None:
        if self._confirm is not None:
            self._confirm = None
            self._set_status("")
            self._refresh_hint()
            return
        self.dismiss(None)

    def action_reset_provider(self) -> None:
        """Ctrl+R — confirm then wipe brand config + form."""
        if self._confirm == "delete":
            # switch intent
            self._confirm = None
        if self._confirm == "reset":
            self._confirm = None
            self._do_reset()
            return
        if not self._can_reset_or_delete():
            self._set_status("尚无已保存配置 · 重置不可用")
            self._refresh_hint()
            return
        # Resolve preset for provider list step
        if self._step == "provider":
            pid = self._highlighted_provider_id()
            if not pid:
                self._set_status("请先选择服务商")
                return
            self._preset = next((p for p in PROVIDER_PRESETS if p.id == pid), None)
        if self._preset is None:
            return
        self._confirm = "reset"
        label = _display_brand(self._preset.label)
        self._set_status(
            f"确认重置 {label}？将清空已保存配置",
            kind="warn",
        )
        self._refresh_hint()

    def action_delete_provider(self) -> None:
        """Ctrl+D — confirm then delete brand config and return to list."""
        if self._confirm == "reset":
            self._confirm = None
        if self._confirm == "delete":
            self._confirm = None
            self._do_delete()
            return
        if not self._can_reset_or_delete():
            self._set_status("尚无已保存配置 · 删除不可用")
            self._refresh_hint()
            return
        if self._step == "provider":
            pid = self._highlighted_provider_id()
            if not pid:
                self._set_status("请先选择服务商")
                return
            self._preset = next((p for p in PROVIDER_PRESETS if p.id == pid), None)
        if self._preset is None and not self._target_provider_id():
            return
        self._confirm = "delete"
        pid = self._target_provider_id()
        preset = find_preset_safe(pid)
        label = _display_brand(preset.label) if preset else pid
        self._set_status(
            f"确认删除 {label}？不可撤销",
            kind="warn",
        )
        self._refresh_hint()

    def _do_reset(self) -> None:
        if self._preset is None:
            pid = self._target_provider_id()
            self._preset = next((p for p in PROVIDER_PRESETS if p.id == pid), None)
        if self._preset is None:
            return
        remove_provider_config(self._preset.id)
        if self._preset.kind == "local":
            if self._step != "local":
                self._enter_local_step()
            self.query_one("#model-setup-url", Input).value = (
                self._preset.default_base_url or ""
            )
            self.query_one("#model-setup-key", Input).value = ""
            self.query_one("#model-setup-model", Input).value = ""
            self._set_status("已重置 · 请重新填写", kind="ok")
        else:
            if self._step != "key":
                self._enter_cloud_key_step()
            self.query_one("#model-setup-key", Input).value = ""
            self._set_status("已重置", kind="ok")
        self._refresh_hint()

    def _do_delete(self) -> None:
        pid = self._target_provider_id()
        if not pid and self._preset is not None:
            pid = self._preset.id
        if not pid:
            self._set_status("请先选择要删除的服务商")
            return
        label = pid
        preset = find_preset_safe(pid)
        if preset:
            label = _display_brand(preset.label)
        if not remove_provider_config(pid):
            self._set_status("该服务商尚无配置")
            self._refresh_hint()
            return
        self._preset = None
        self._step = "provider"
        self._set_field_visible()
        self.query_one("#model-setup-list", OptionList).display = True
        self._set_step_label("选择服务商")
        self._reload_provider_list()
        self._set_status(f"已删除 {label}", kind="ok")
        self._refresh_hint()
        self.query_one("#model-setup-list", OptionList).focus()

    def _highlighted_provider_id(self) -> str:
        ol = self.query_one("#model-setup-list", OptionList)
        if not ol.display:
            return ""
        try:
            idx = ol.highlighted
            if idx is None:
                return ""
            opt = ol.get_option_at_index(int(idx))
            return str(getattr(opt, "id", None) or "")
        except Exception:
            return ""

    def _reload_provider_list(self) -> None:
        ol = self.query_one("#model-setup-list", OptionList)
        ol.clear_options()
        cloud = [p for p in PROVIDER_PRESETS if p.kind == "cloud"]
        local = [p for p in PROVIDER_PRESETS if p.kind == "local"]
        for p in cloud + local:
            ol.add_option(Option(provider_list_label(p), id=p.id))
        try:
            ol.highlighted = 0
        except Exception:
            pass

    def _set_step_label(self, text: str) -> None:
        self.query_one("#model-setup-step", Static).update(text)

    def _set_status(
        self,
        text: str = "",
        *,
        kind: Literal["normal", "warn", "ok"] = "normal",
    ) -> None:
        """Status line — always occupies 1 row (no layout jump when text appears).

        kind:
          warn  — confirm / destructive prompt (yellow, bold)
          ok    — success after action
          normal — neutral info / empty reserved space
        """
        st = self.query_one("#model-setup-status", Static)
        t = (text or "").strip()
        st.remove_class("warn")
        st.remove_class("ok")
        # Always show the row so Base URL etc. do not shift
        st.display = True
        if not t:
            st.update(" ")  # non-empty keeps height stable in some terminals
            return
        if kind == "warn":
            st.add_class("warn")
            # Yellow markup as well (works even if CSS theme differs)
            st.update(f"[bold {_WARN}]{t}[/bold {_WARN}]")
        elif kind == "ok":
            st.add_class("ok")
            st.update(t)
        else:
            st.update(f"[dim]{t}[/dim]")

    def _set_field_visible(
        self, *, url: bool = False, key: bool = False, model: bool = False
    ) -> None:
        self.query_one("#model-setup-url-field").display = url
        self.query_one("#model-setup-key-field").display = key
        self.query_one("#model-setup-model-field").display = model

    def _move_list(self, delta: int) -> None:
        ol = self.query_one("#model-setup-list", OptionList)
        if not ol.display:
            return
        try:
            n = ol.option_count
        except Exception:
            n = 0
        if n <= 0:
            return
        cur = ol.highlighted
        if cur is None:
            cur = 0 if delta >= 0 else n - 1
        else:
            cur = int(cur) + delta
        cur = max(0, min(n - 1, cur))
        try:
            ol.highlighted = cur
            ol.focus()
        except Exception:
            pass
        # Clear pending confirm when browsing list
        if self._confirm is not None:
            self._confirm = None
            self._set_status("")
        self._refresh_hint()

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key in ("up", "down"):
            if self.query_one("#model-setup-list", OptionList).display:
                event.stop()
                event.prevent_default()
                self._move_list(-1 if event.key == "up" else 1)

    @on(OptionList.OptionHighlighted, "#model-setup-list")
    def _on_highlight(self, _event: OptionList.OptionHighlighted) -> None:
        if self._step == "provider" and self._confirm is None:
            self._refresh_hint()

    @on(OptionList.OptionSelected, "#model-setup-list")
    def _on_option(self, event: OptionList.OptionSelected) -> None:
        if self._step != "provider":
            return
        self._confirm = None
        oid = str(event.option_id or "")
        self._preset = next((p for p in PROVIDER_PRESETS if p.id == oid), None)
        if self._preset is None:
            return
        if self._preset.kind == "local":
            self._enter_local_step()
        else:
            self._enter_cloud_key_step()

    def _enter_cloud_key_step(self) -> None:
        """Cloud (non-self-hosting): only API Key. No model pick / hand-fill."""
        assert self._preset is not None
        assert self._preset.kind != "local"
        self._confirm = None
        self._step = "key"
        self._set_step_label(_display_brand(self._preset.label))
        self.query_one("#model-setup-list", OptionList).display = False
        self._set_status("")
        self._set_field_visible(key=True)
        key_in = self.query_one("#model-setup-key", Input)
        key_in.placeholder = "粘贴 API Key"
        self.query_one("#model-setup-key-label", Static).update("API Key")
        env_val = __import__("os").environ.get(self._preset.env_var, "")
        if env_val.strip():
            key_in.value = env_val.strip()
        else:
            existing = load_auth().get(self._preset.id)
            if isinstance(existing, dict) and existing.get("type") == "api_key":
                key_in.value = str(existing.get("key") or "")
            else:
                key_in.value = ""
        self._refresh_hint()
        key_in.focus()

    def _enter_key_step(self) -> None:
        """Compat alias — cloud key step only."""
        self._enter_cloud_key_step()

    def _enter_local_step(self) -> None:
        assert self._preset is not None
        assert self._preset.kind == "local"
        self._confirm = None
        self._step = "local"
        existing = get_local_provider_config(self._preset.id)
        self._set_step_label(_display_brand(self._preset.label))
        self._set_status("")
        self.query_one("#model-setup-list", OptionList).display = False
        self._set_field_visible(url=True, key=True, model=True)
        url_in = self.query_one("#model-setup-url", Input)
        key_in = self.query_one("#model-setup-key", Input)
        model_in = self.query_one("#model-setup-model", Input)
        self.query_one("#model-setup-url-label", Static).update("Base URL")
        self.query_one("#model-setup-key-label", Static).update("API Key · 可选")
        self.query_one("#model-setup-model-label", Static).update(
            "模型（多个用逗号隔开）"
        )
        url_in.placeholder = self._preset.default_base_url or "http://127.0.0.1:1234/v1"
        key_in.placeholder = "未开鉴权请留空"
        model_in.placeholder = "填本机已加载的模型 id"
        url_in.value = str(
            (existing or {}).get("baseUrl") or self._preset.default_base_url
        )
        auth_entry = load_auth().get(self._preset.id)
        if isinstance(auth_entry, dict) and auth_entry.get("type") == "api_key":
            key_in.value = str(auth_entry.get("key") or "")
        elif existing and existing.get("apiKey"):
            key_in.value = str(existing.get("apiKey") or "")
        else:
            key_in.value = ""
        key_in.password = True
        existing_ids = [
            x
            for x in local_provider_model_ids(existing)
            if not is_placeholder_model_id(x)
        ]
        model_in.value = ", ".join(existing_ids) if existing_ids else ""
        self._refresh_hint()
        # First config → URL; re-edit → models
        if existing_ids or provider_is_configured(self._preset.id):
            model_in.focus()
        else:
            url_in.focus()

    @on(Input.Submitted, "#model-setup-key")
    def _on_key_submit(self, event: Input.Submitted) -> None:
        if self._confirm is not None:
            return
        if self._step == "local":
            self.query_one("#model-setup-model", Input).focus()
            return
        if self._step != "key" or self._preset is None:
            return
        if self._preset.kind == "local":
            self.query_one("#model-setup-model", Input).focus()
            return
        key = (event.value or "").strip()
        if not key:
            self._set_status("API Key 不能为空")
            return
        try:
            set_api_key(self._preset.id, key)
        except ValueError as e:
            self._set_status(str(e))
            return
        self._finish_cloud_connected()

    def _finish_cloud_connected(self) -> None:
        """Cloud brand: key saved → done. Model pick is /model only."""
        assert self._preset is not None
        assert self._preset.kind != "local"
        try:
            catalog = catalog_models(pi_bin=self._pi_bin)
            mids = [
                m.model
                for m in catalog
                if m.provider == self._preset.id and (m.model or "").strip()
            ]
            if mids:
                from room_tui.auth_setup import sync_provider_enabled_models

                default = mids[0]
                try:
                    from room_tui.config import load_config

                    cfg = load_config()
                    if (cfg.provider or "").strip() == self._preset.id and (
                        cfg.model or ""
                    ).strip() in mids:
                        default = (cfg.model or "").strip()
                except Exception:
                    pass
                sync_provider_enabled_models(
                    self._preset.id, mids, default_model=default
                )
                self.dismiss(ModelInfo(provider=self._preset.id, model=default))
                return
        except Exception:
            pass
        self.dismiss(ModelInfo(provider=self._preset.id, model=""))

    @on(Input.Submitted, "#model-setup-url")
    def _on_url_submit(self, event: Input.Submitted) -> None:
        if self._confirm is not None:
            return
        if self._step == "local":
            self.query_one("#model-setup-key", Input).focus()

    @on(Input.Submitted, "#model-setup-model")
    def _on_model_submit(self, event: Input.Submitted) -> None:
        if self._confirm is not None:
            return
        if self._step == "local":
            self._save_local_and_finish()

    def _save_local_and_finish(self) -> None:
        if self._preset is None:
            return
        url = self.query_one("#model-setup-url", Input).value.strip()
        key = self.query_one("#model-setup-key", Input).value.strip()
        raw_models = self.query_one("#model-setup-model", Input).value.strip()
        mids = [
            x
            for x in parse_model_id_list(raw_models)
            if not is_placeholder_model_id(x)
        ]
        if not mids:
            self._set_status("请填写至少一个模型 id（与本机已加载模型一致）")
            return
        try:
            configure_local_provider(
                self._preset.id,
                base_url=url or self._preset.default_base_url,
                api_key=key,
                model_ids=mids,
            )
        except ValueError as e:
            self._set_status(str(e))
            return
        self._finish(self._preset.id, mids[0])

    def _finish(self, provider: str, model: str) -> None:
        mid = (model or "").strip()
        prov = (provider or "").strip()
        if mid:
            try:
                set_room_default_model(prov, mid)
            except Exception:
                pass
            self.dismiss(ModelInfo(provider=prov, model=mid))
        elif self._preset:
            self.dismiss(ModelInfo(provider=self._preset.id, model=""))
        else:
            self.dismiss(None)
