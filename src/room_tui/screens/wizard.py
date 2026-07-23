"""New document wizard — inputs | templates (+ light template ops); output + start."""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from room_tui.ui_state import COLOR_BRAND_ON_BAR
from room_tui.widgets.smooth_scroll import SmoothListView

if TYPE_CHECKING:
    from room_tui.app import RoomApp

_DEFAULT_BUDGET = 40000

_DOC_GLOBS = (
    "*.doc",
    "*.docx",
    "*.pdf",
    "*.md",
    "*.txt",
    "*.xlsx",
    "*.xls",
    "*.csv",
    "*.pptx",
    "*.html",
)


def _safe_filename_stem(name: str) -> str:
    """Template display name → safe file stem."""
    s = (name or "").strip()
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s)
    s = re.sub(r"_+", "_", s).strip("._")
    return s or "output"


class WizardScreen(Screen):
    """Left: inputs · Right: templates + register/detail/delete · Bottom: output + start."""

    BINDINGS = [
        Binding("escape", "cancel", "返回", show=True),
        Binding("q", "cancel", "返回", show=False),
    ]

    CSS = """
    WizardScreen {
        layout: vertical;
        background: $background;
    }

    #wiz-title-bar, #wiz-status-bar {
        height: 1;
        width: 100%;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
    }
    #wiz-title-bar { dock: top; }
    #wiz-status-bar { dock: bottom; }

    #wiz-main {
        height: 1fr;
        min-height: 0;
        layout: vertical;
        padding: 1 2;
    }

    #wiz-title {
        height: 1;
        text-style: bold;
        color: $text;
    }
    #wiz-sub {
        height: 1;
        color: $text-muted;
        margin-bottom: 1;
    }

    #wiz-split {
        height: 1fr;
        min-height: 10;
        max-height: 20;
        layout: horizontal;
        margin-bottom: 1;
    }
    #pane-inputs, #pane-templates {
        width: 1fr;
        min-width: 0;
        height: 1fr;
        layout: vertical;
    }
    #pane-templates {
        border-left: solid $foreground 12%;
        padding-left: 1;
        margin-left: 1;
    }

    .pane-title {
        height: 1;
        color: $text-muted;
    }

    /* List boxes: only the list lives inside the border.
       Toolbars sit *below* the box — height:1 + border-top collapses content to 0. */
    #input-frame, #template-frame {
        height: 1fr;
        min-height: 0;
        layout: vertical;
        border: solid $foreground 18%;
        background: $surface;
    }
    #input-list, #template-list {
        height: 1fr;
        min-height: 2;
        border: none;
        background: transparent;
        scrollbar-size: 1 1;
        scrollbar-background: transparent;
        scrollbar-color: $foreground 8%;
    }
    #path-row, #tpl-actions {
        height: 1;
        min-height: 1;
        max-height: 1;
        layout: horizontal;
        background: #252525;
        padding: 0;
        margin-top: 0;
    }
    #input-path, #reg-sample, #reg-name {
        width: 1fr;
        height: 1;
        min-height: 1;
        max-height: 1;
        border: none;
        background: #1a1a1a;
        color: $foreground;
        padding: 0 1;
        margin: 0;
    }
    #input-path:focus, #reg-sample:focus, #reg-name:focus {
        background: #2a2a2a;
    }
    #btn-add, #btn-remove {
        width: auto;
        min-width: 3;
        height: 1;
        min-height: 1;
        max-height: 1;
        border: none;
        background: #2a3a4a;
        color: #c8d8e8;
        margin: 0 0 0 1;
        padding: 0 1;
        content-align: center middle;
    }
    #btn-add:hover, #btn-remove:hover {
        color: #ffffff;
        background: #3a5a7a;
    }

    #tpl-actions Button {
        width: auto;
        min-width: 8;
        height: 1;
        min-height: 1;
        max-height: 1;
        border: none;
        margin: 0 0 0 1;
        padding: 0 1;
        content-align: center middle;
        text-style: bold;
    }
    #btn-tpl-reg {
        background: #2a4a3a;
        color: #9ed9b0;
    }
    #btn-tpl-reg:hover {
        background: #3a6a4a;
        color: #ffffff;
    }
    #btn-tpl-info {
        background: #2a3a4a;
        color: #a8c8e8;
    }
    #btn-tpl-info:hover {
        background: #3a5a7a;
        color: #ffffff;
    }
    #btn-tpl-del {
        background: #3a2a2a;
        color: #e8a8b0;
    }
    #btn-tpl-del:hover {
        background: #5a3a3a;
        color: #ffffff;
    }

    #reg-panel {
        display: none;
        height: auto;
        max-height: 4;
        layout: vertical;
        background: #1e1e1e;
        padding: 0;
        margin-top: 0;
    }
    #reg-panel.visible {
        display: block;
    }
    #reg-row1, #reg-row2 {
        height: 1;
        min-height: 1;
        layout: horizontal;
    }
    #reg-row2 Button {
        width: auto;
        min-width: 6;
        height: 1;
        border: none;
        background: #2a2a2a;
        color: #c8c8c8;
        padding: 0 1;
        margin: 0 0 0 1;
        content-align: center middle;
    }
    #reg-row2 Button.-active {
        color: #ffffff;
        text-style: bold;
        background: $primary-darken-2;
    }
    #reg-row2 Button:hover {
        color: #ffffff;
        background: #3a3a3a;
    }
    #btn-reg-go {
        background: #2a4a3a !important;
        color: #9ed9b0 !important;
        text-style: bold;
    }
    #btn-reg-go:hover {
        background: #3a6a4a !important;
        color: #ffffff !important;
    }

    ListView > ListItem {
        background: transparent;
        color: $text;
        padding: 0 1;
        height: 1;
    }
    ListView > ListItem.-highlight {
        background: $primary-darken-2;
        color: $text;
    }

    #foot-row {
        height: 1;
        min-height: 1;
        max-height: 1;
        layout: horizontal;
        margin-bottom: 1;
    }
    #output-label {
        width: auto;
        height: 1;
        color: $text-muted;
        padding: 0 1 0 0;
    }
    #input-output {
        width: 1fr;
        min-width: 8;
        height: 1;
        min-height: 1;
        max-height: 1;
        border: none;
        background: $surface;
        color: $foreground;
        padding: 0 1;
        margin: 0 1 0 0;
    }
    #btn-back, #btn-start {
        width: 12;
        min-width: 12;
        max-width: 12;
        height: 1;
        min-height: 1;
        max-height: 1;
        margin: 0 0 0 1;
        padding: 0;
        border: none;
        content-align: center middle;
    }
    #btn-back {
        background: $surface;
        color: $text-muted;
    }
    #btn-back:hover {
        color: $text;
    }
    #btn-start {
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }
    #confirm-text {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._templates: list[dict[str, Any]] = []
        self._template_id: str = ""
        self._template_name: str = ""
        self._inputs: list[Path] = []
        self._suggestions: list[Path] = []
        self._output_user_edited = False
        self._reg_fast = False  # default full quality
        self._reg_busy = False
        self._delete_pending_id: str = ""
        self._delete_pending_at: float = 0.0
        self._prefer_select_id: str = ""
        self._prefer_select_name: str = ""

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold {COLOR_BRAND_ON_BAR}]Room[/bold {COLOR_BRAND_ON_BAR}]"
            f"  [#A8C8E8]·[/#A8C8E8]  [dim]新建[/dim]",
            id="wiz-title-bar",
            markup=True,
        )
        with Vertical(id="wiz-main"):
            yield Label("新建文档生成", id="wiz-title")
            yield Label(
                "左栏 Enter 勾选资料  ·  右栏选模板 / 注册  ·  底栏开始生成",
                id="wiz-sub",
            )

            with Horizontal(id="wiz-split"):
                with Vertical(id="pane-inputs"):
                    yield Label(
                        "输入资料 · Enter 勾选 / 取消",
                        id="pane-inputs-title",
                        classes="pane-title",
                    )
                    with Vertical(id="input-frame"):
                        yield SmoothListView(id="input-list")
                    # Toolbar *below* the bordered list — not inside (border eats height:1)
                    with Horizontal(id="path-row"):
                        yield Input(
                            placeholder="路径  Enter 添加",
                            id="input-path",
                            compact=True,
                        )
                        yield Button("+", id="btn-add")
                        yield Button("−", id="btn-remove")
                with Vertical(id="pane-templates"):
                    yield Label(
                        "模板 · 点下方 +注册 / 详情 / 删除",
                        id="pane-tpl-title",
                        classes="pane-title",
                    )
                    with Vertical(id="template-frame"):
                        yield SmoothListView(id="template-list")
                    with Horizontal(id="tpl-actions"):
                        yield Button("+注册", id="btn-tpl-reg")
                        yield Button("详情", id="btn-tpl-info")
                        yield Button("删除", id="btn-tpl-del")
                    with Vertical(id="reg-panel"):
                        with Horizontal(id="reg-row1"):
                            yield Input(
                                placeholder="样例路径",
                                id="reg-sample",
                                compact=True,
                            )
                            yield Input(
                                placeholder="模板名称",
                                id="reg-name",
                                compact=True,
                            )
                        with Horizontal(id="reg-row2"):
                            yield Button("完整", id="btn-reg-full")
                            yield Button("快速", id="btn-reg-fast")
                            yield Button("开始注册", id="btn-reg-go")
                            yield Button("取消", id="btn-reg-cancel")

            with Horizontal(id="foot-row"):
                yield Label("输出", id="output-label")
                yield Input(value="output.md", id="input-output", compact=True)
                yield Button("返回", id="btn-back")
                yield Button("开始生成", id="btn-start")
            yield Static("", id="confirm-text")

        yield Static(
            "Esc 返回  ·  ↑↓ 移动  ·  Enter 勾选资料  ·  右栏 +注册 / 详情 / 删除",
            id="wiz-status-bar",
        )

    def on_mount(self) -> None:
        self._scan_suggestions()
        self._sync_reg_mode_buttons()
        self.run_worker(self._boot(), exclusive=True)
        try:
            self.query_one("#input-path", Input).focus()
        except Exception:
            pass

    async def _boot(self) -> None:
        await self._refresh_input_list()
        await self._load_templates()
        self._refresh_confirm()

    def _ws(self) -> Path:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        return Path(app.cfg.workspace or Path.cwd()).resolve()

    def _scan_suggestions(self) -> None:
        root = self._ws()
        found: list[Path] = []
        for pat in _DOC_GLOBS:
            for p in sorted(root.glob(pat)):
                if p.is_file() and p not in found:
                    found.append(p.resolve())
        try:
            for sub in sorted(root.iterdir()):
                if not sub.is_dir() or sub.name.startswith("."):
                    continue
                for pat in _DOC_GLOBS:
                    for p in sorted(sub.glob(pat)):
                        if p.is_file() and p.resolve() not in found:
                            found.append(p.resolve())
        except OSError:
            pass
        self._suggestions = found[:80]

    def _unselected_suggestions(self) -> list[Path]:
        selected = {p.resolve() for p in self._inputs}
        return [p for p in self._suggestions if p.resolve() not in selected]

    def _default_output_for_template(self) -> str:
        name = self._template_name or self._template_id or "output"
        return f"{_safe_filename_stem(name)}.md"

    def _apply_template_output(self) -> None:
        if self._output_user_edited:
            return
        out = self._default_output_for_template()
        try:
            self.query_one("#input-output", Input).value = out
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        try:
            self.query_one("#confirm-text", Static).update(text)
        except Exception:
            pass

    def _show_reg_panel(self, show: bool) -> None:
        panel = self.query_one("#reg-panel", Vertical)
        if show:
            panel.add_class("visible")
            # Prefill sample from first input or first suggestion
            sample = ""
            if self._inputs:
                sample = str(self._inputs[0])
            elif self._suggestions:
                sample = str(self._suggestions[0])
            try:
                self.query_one("#reg-sample", Input).value = sample
                if sample and not self.query_one("#reg-name", Input).value.strip():
                    self.query_one("#reg-name", Input).value = Path(sample).stem
                # md/txt → default fast
                if Path(sample).suffix.lower() in (".md", ".markdown", ".txt"):
                    self._reg_fast = True
                else:
                    self._reg_fast = False
                self._sync_reg_mode_buttons()
                self.query_one("#reg-sample", Input).focus()
            except Exception:
                pass
            self._set_status("填写样例路径与名称 · 完整=质量优先 · 快速=结构扫描")
        else:
            panel.remove_class("visible")
            self._refresh_confirm()

    def _sync_reg_mode_buttons(self) -> None:
        try:
            full_b = self.query_one("#btn-reg-full", Button)
            fast_b = self.query_one("#btn-reg-fast", Button)
            if self._reg_fast:
                full_b.remove_class("-active")
                fast_b.add_class("-active")
            else:
                fast_b.remove_class("-active")
                full_b.add_class("-active")
        except Exception:
            pass

    def _update_pane_titles(self) -> None:
        n = len(self._inputs)
        try:
            title = self.query_one("#pane-inputs-title", Label)
            if n == 0:
                title.update("输入资料 · Enter 勾选扫描项")
            else:
                title.update(f"输入资料 · 已选 {n} 份  ·  Enter 取消/再勾选")
        except Exception:
            pass
        try:
            nt = len(self._templates)
            ttitle = self.query_one("#pane-tpl-title", Label)
            if nt == 0:
                ttitle.update("模板 · 点下方「+注册」从样例创建")
            else:
                ttitle.update(f"模板 · {nt} 个  ·  下方 +注册 / 详情 / 删除")
        except Exception:
            pass

    async def _refresh_input_list(self) -> None:
        lv = self.query_one("#input-list", ListView)
        prev = lv.index
        await lv.clear()
        for p in self._inputs:
            try:
                rel = p.relative_to(self._ws())
                label = f"✓ {rel}"
            except ValueError:
                label = f"✓ {p.name}"
            await lv.append(ListItem(Label(label)))
        for p in self._unselected_suggestions():
            try:
                rel = p.relative_to(self._ws())
                label = f"○ {rel}"
            except ValueError:
                label = f"○ {p.name}"
            await lv.append(ListItem(Label(label)))
        if not self._inputs and not self._suggestions:
            await lv.append(ListItem(Label("  （无扫描结果 · 在底栏输入路径）")))
        elif not self._inputs and self._suggestions:
            # Keep a gentle first-row cue: nothing checked yet
            pass
        # Restore cursor near previous row when possible
        total = len(self._inputs) + len(self._unselected_suggestions())
        if total > 0 and prev is not None:
            lv.index = max(0, min(prev, total - 1))
        self._update_pane_titles()

    async def _load_templates(self, *, select_id: str = "", select_name: str = "") -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        lv = self.query_one("#template-list", ListView)
        await lv.clear()
        prefer_id = select_id or self._prefer_select_id
        prefer_name = select_name or self._prefer_select_name
        self._prefer_select_id = ""
        self._prefer_select_name = ""
        try:
            self._templates = await app.load_templates()
        except Exception as e:
            await lv.append(ListItem(Label(f"  加载失败: {e}")))
            self._template_id = ""
            self._template_name = ""
            return
        if not self._templates:
            await lv.append(ListItem(Label("  （无可用模板）")))
            await lv.append(ListItem(Label("  点底栏「+注册」从样例创建")))
            self._template_id = ""
            self._template_name = ""
            self._update_pane_titles()
            self._refresh_confirm()
            return
        # Select preferred or keep current or first
        idx = 0
        if prefer_id:
            for i, t in enumerate(self._templates):
                if str(t.get("id") or "") == prefer_id:
                    idx = i
                    break
        elif prefer_name:
            for i, t in enumerate(self._templates):
                if str(t.get("name") or "") == prefer_name:
                    idx = i
                    break
        elif self._template_id:
            for i, t in enumerate(self._templates):
                if str(t.get("id") or "") == self._template_id:
                    idx = i
                    break
        self._apply_template_from_index(idx, repaint=False)
        for i, t in enumerate(self._templates):
            name = str(t.get("name") or t.get("id") or "?")
            nsec = t.get("section_count", "?")
            mark = "● " if i == idx else "○ "
            await lv.append(ListItem(Label(f"{mark}{name}  ·  {nsec} 节")))
        lv.index = idx
        self._update_pane_titles()
        self._refresh_confirm()

    def _apply_template_from_index(self, idx: int, *, repaint: bool = True) -> None:
        if idx < 0 or idx >= len(self._templates):
            return
        t = self._templates[idx]
        self._template_id = str(t.get("id") or "")
        self._template_name = str(t.get("name") or self._template_id or "")
        self._apply_template_output()
        self._refresh_confirm()
        if repaint:
            self._repaint_template_marks(idx)

    def _repaint_template_marks(self, idx: int) -> None:
        """Update ●/○ marks without reloading template data."""
        try:
            lv = self.query_one("#template-list", ListView)
        except Exception:
            return
        items = list(lv.query(ListItem))
        for i, item in enumerate(items):
            if i >= len(self._templates):
                break
            t = self._templates[i]
            name = str(t.get("name") or t.get("id") or "?")
            nsec = t.get("section_count", "?")
            mark = "● " if i == idx else "○ "
            try:
                lab = item.query_one(Label)
                lab.update(f"{mark}{name}  ·  {nsec} 节")
            except Exception:
                pass

    def _resolve_path(self, raw: str) -> Path | None:
        s = (raw or "").strip().strip("'\"")
        if not s:
            return None
        path = Path(s).expanduser()
        if not path.is_absolute():
            path = (self._ws() / path).resolve()
        else:
            path = path.resolve()
        return path

    def _add_path(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.notify(f"文件不存在: {path}", severity="error")
            return
        rp = path.resolve()
        if rp in {p.resolve() for p in self._inputs}:
            self.notify("已在列表中", severity="warning")
            return
        self._inputs.append(rp)
        self.run_worker(self._refresh_input_list(), exclusive=False)
        self._refresh_confirm()
        self.query_one("#input-path", Input).value = ""

    def _remove_selected_input(self) -> None:
        lv = self.query_one("#input-list", ListView)
        idx = lv.index
        if idx is None or idx < 0:
            return
        if idx < len(self._inputs):
            self._inputs.pop(idx)
            self.run_worker(self._refresh_input_list(), exclusive=False)
            self._refresh_confirm()
            return
        sug_i = idx - len(self._inputs)
        cands = self._unselected_suggestions()
        if 0 <= sug_i < len(cands):
            self._add_path(cands[sug_i])

    def _toggle_input_row(self, lv: ListView) -> None:
        """Enter on a row: select suggestion → ✓, or deselect already-chosen input."""
        idx = lv.index
        if idx is None or idx < 0:
            return
        if idx < len(self._inputs):
            # Deselect chosen input
            self._inputs.pop(idx)
            self.run_worker(self._refresh_input_list(), exclusive=False)
            self._refresh_confirm()
            return
        sug_i = idx - len(self._inputs)
        cands = self._unselected_suggestions()
        if 0 <= sug_i < len(cands):
            self._add_path(cands[sug_i])

    def _selected_template(self) -> dict[str, Any] | None:
        if not self._templates:
            return None
        for t in self._templates:
            if str(t.get("id") or "") == self._template_id:
                return t
        idx = self.query_one("#template-list", ListView).index
        if idx is not None and 0 <= idx < len(self._templates):
            return self._templates[idx]
        return self._templates[0] if self._templates else None

    async def _show_template_detail(self) -> None:
        t = self._selected_template()
        if not t:
            self.notify("请先选择模板", severity="warning")
            return
        tid = str(t.get("id") or "")
        app: "RoomApp" = self.app  # type: ignore[assignment]
        try:
            data = await asyncio.to_thread(app.engine.template_show, tid)
        except Exception as e:
            self.notify(f"详情失败: {e}", severity="error")
            return
        name = str(data.get("name") or t.get("name") or tid)
        nsec = data.get("section_count")
        if nsec is None:
            secs = data.get("section_ids") or data.get("sections") or []
            nsec = len(secs) if isinstance(secs, list) else t.get("section_count", "?")
        desc = str(data.get("description") or "").strip()
        msg = f"{name}  [{tid}]  ·  {nsec} 节"
        if desc:
            msg += f"  ·  {desc[:40]}"
        self._set_status(msg)
        self.notify(msg, severity="information", timeout=6)

    async def _delete_selected_template(self) -> None:
        t = self._selected_template()
        if not t:
            self.notify("请先选择模板", severity="warning")
            return
        tid = str(t.get("id") or "")
        name = str(t.get("name") or tid)
        now = time.monotonic()
        if self._delete_pending_id != tid or (now - self._delete_pending_at) > 5.0:
            self._delete_pending_id = tid
            self._delete_pending_at = now
            self._set_status(f"再点一次「删除」确认删除「{name}」[{tid}]（5 秒内）")
            self.notify("再点一次删除以确认", severity="warning", timeout=4)
            return
        self._delete_pending_id = ""
        app: "RoomApp" = self.app  # type: ignore[assignment]
        self._set_status(f"正在删除 {name}…")
        try:
            await asyncio.to_thread(app.engine.template_delete, tid)
        except Exception as e:
            self.notify(f"删除失败: {e}", severity="error")
            self._refresh_confirm()
            return
        if self._template_id == tid:
            self._template_id = ""
            self._template_name = ""
        self.notify(f"已删除模板 {name}", severity="information")
        await self._load_templates()
        self._refresh_confirm()

    async def _run_register(self) -> None:
        if self._reg_busy:
            self.notify("注册进行中…", severity="warning")
            return
        sample_s = self.query_one("#reg-sample", Input).value.strip()
        name = self.query_one("#reg-name", Input).value.strip()
        sample = self._resolve_path(sample_s)
        if sample is None or not sample.is_file():
            self.notify("请填写有效的样例文件路径", severity="error")
            return
        if not name:
            name = sample.stem
        app: "RoomApp" = self.app  # type: ignore[assignment]
        self._reg_busy = True
        mode = "快速" if self._reg_fast else "完整"
        self._set_status(f"注册中（{mode}）「{name}」← {sample.name}…")
        try:
            if self._reg_fast:
                api_base = (
                    os.environ.get("ROOM_API_BASE")
                    or os.environ.get("PAPER_DERIVED_API_BASE")
                    or ""
                ).strip()
                api_key = (
                    os.environ.get("ROOM_API_KEY")
                    or os.environ.get("OPENAI_API_KEY")
                    or ""
                ).strip()
                parsed = await asyncio.to_thread(
                    app.engine.template_register_auto,
                    sample,
                    name,
                    description=f"Registered via Room wizard from {sample.name}",
                    model=(app.cfg.model or "").strip(),
                    api_base=api_base,
                    api_key=api_key,
                    compact=True,
                    timeout_s=float(getattr(app.cfg, "worker_timeout_s", 600) or 600),
                )
            else:
                from room_tui.config import PiTierConfig
                from room_tui.llm.pi_runner import WorkerRequest
                from room_tui.pi_catalog import check_model_status

                st = check_model_status(
                    app.cfg.provider, app.cfg.model, pi_bin=app.cfg.pi_bin
                )
                if not st.ok:
                    raise RuntimeError(
                        f"{st.reason or '模型未配置'} — 先返回主界面 Ctrl+M 连接模型 或 /model"
                    )
                ws = self._ws()
                t0 = time.time()
                prompt_path = ws / ".pd" / "tui" / "tmp" / f"wiz-reg-{int(t0)}.md"
                resp_path = ws / ".pd" / "responses" / f"wiz-reg-{int(t0)}.json"
                prompt_path.parent.mkdir(parents=True, exist_ok=True)
                resp_path.parent.mkdir(parents=True, exist_ok=True)
                handle = await asyncio.to_thread(
                    app.engine.template_register_build,
                    sample,
                    name,
                    prompt_path,
                    description=f"Registered via Room wizard from {sample.name}",
                )
                self._set_status(
                    f"注册中 · Room Agent 分析… tokens≈{handle.prompt_tokens or '?'}"
                )
                tier = PiTierConfig(
                    provider=app.cfg.provider,
                    model=app.cfg.model,
                    thinking=getattr(app.cfg, "thinking", None) or "off",
                )
                if app.cfg.strong_provider or app.cfg.strong_model:
                    tier = PiTierConfig(
                        provider=app.cfg.strong_provider or app.cfg.provider,
                        model=app.cfg.strong_model or app.cfg.model,
                        thinking=tier.thinking,
                    )
                result = await app.orch.runner.execute(
                    WorkerRequest(
                        key=f"wiz-reg-{int(t0)}",
                        prompt_file=handle.path,
                        response_file=resp_path,
                        tier=tier,
                        timeout_s=float(
                            getattr(app.cfg, "worker_timeout_s", 600) or 600
                        ),
                    ),
                )
                if not result.ok:
                    raise RuntimeError(result.error or "Room Agent 分析失败")
                parsed = await asyncio.to_thread(
                    app.engine.template_register_parse,
                    sample,
                    name,
                    resp_path,
                )
            tid = str(parsed.get("template_id") or parsed.get("id") or "")
            nsec = parsed.get("sections") or parsed.get("section_count") or "?"
            if isinstance(parsed.get("section_ids"), list):
                nsec = len(parsed["section_ids"])
            self._prefer_select_id = tid
            self._prefer_select_name = name
            self._show_reg_panel(False)
            await self._load_templates(select_id=tid, select_name=name)
            msg = f"✓ 已注册「{name}」" + (f" [{tid}]" if tid else "") + f" · {nsec} 节 · 已选中"
            self._set_status(msg)
            self.notify(msg, severity="information", timeout=8)
        except Exception as e:
            err = str(e)
            self.notify(f"注册失败: {err[:120]}", severity="error", timeout=10)
            hint = ""
            if self._reg_fast:
                hint = " · 可改「完整」重试"
            self._set_status(f"注册失败: {err[:80]}{hint}")
        finally:
            self._reg_busy = False

    @on(ListView.Highlighted, "#template-list")
    def _on_tpl_hi(self, event: ListView.Highlighted) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._templates):
            self._apply_template_from_index(idx)

    @on(ListView.Selected, "#template-list")
    def _on_tpl_sel(self, event: ListView.Selected) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._templates):
            self._apply_template_from_index(idx)

    @on(ListView.Selected, "#input-list")
    def _on_in_sel(self, event: ListView.Selected) -> None:
        self._toggle_input_row(event.list_view)

    @on(Input.Submitted, "#input-path")
    def _on_path_submit(self, event: Input.Submitted) -> None:
        event.stop()
        path = self._resolve_path(event.value)
        if path is not None:
            self._add_path(path)

    @on(Input.Submitted, "#reg-sample")
    def _on_reg_sample_submit(self, event: Input.Submitted) -> None:
        event.stop()
        p = self._resolve_path(event.value)
        if p and p.is_file() and not self.query_one("#reg-name", Input).value.strip():
            self.query_one("#reg-name", Input).value = p.stem
        try:
            self.query_one("#reg-name", Input).focus()
        except Exception:
            pass

    @on(Input.Submitted, "#reg-name")
    def _on_reg_name_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.run_worker(self._run_register(), exclusive=False)

    @on(Input.Changed, "#input-output")
    def _on_out(self, event: Input.Changed) -> None:
        expected = self._default_output_for_template()
        cur = (event.value or "").strip()
        if cur and cur != expected:
            self._output_user_edited = True
        elif cur == expected:
            self._output_user_edited = False
        self._refresh_confirm()

    def _refresh_confirm(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        out = self.query_one("#input-output", Input).value.strip() or "output.md"
        model = app.cfg.model or "—"
        n = len(self._inputs)
        if n == 0:
            files = "未选资料（左栏 Enter 勾选 ○）"
        elif n == 1:
            files = f"✓ {self._inputs[0].name}"
        else:
            files = f"✓ {self._inputs[0].name} 等{n}个"
        tname = self._template_name or self._template_id or "未选模板"
        if len(tname) > 20:
            tname = tname[:18] + "…"
        self.query_one("#confirm-text", Static).update(
            f"{tname}  ·  {files}  →  {out}  ·  {model}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-back":
            self.action_cancel()
        elif bid == "btn-start":
            self.action_start()
        elif bid == "btn-add":
            path = self._resolve_path(self.query_one("#input-path", Input).value)
            if path is not None:
                self._add_path(path)
        elif bid == "btn-remove":
            self._remove_selected_input()
        elif bid == "btn-tpl-reg":
            panel = self.query_one("#reg-panel", Vertical)
            self._show_reg_panel("visible" not in panel.classes)
        elif bid == "btn-tpl-info":
            self.run_worker(self._show_template_detail(), exclusive=False)
        elif bid == "btn-tpl-del":
            self.run_worker(self._delete_selected_template(), exclusive=False)
        elif bid == "btn-reg-full":
            self._reg_fast = False
            self._sync_reg_mode_buttons()
        elif bid == "btn-reg-fast":
            self._reg_fast = True
            self._sync_reg_mode_buttons()
        elif bid == "btn-reg-go":
            self.run_worker(self._run_register(), exclusive=False)
        elif bid == "btn-reg-cancel":
            self._show_reg_panel(False)

    def action_cancel(self) -> None:
        # Esc closes register panel first
        try:
            panel = self.query_one("#reg-panel", Vertical)
            if "visible" in panel.classes:
                self._show_reg_panel(False)
                return
        except Exception:
            pass
        self.app.pop_screen()

    def action_start(self) -> None:
        if not self._template_id:
            self.notify("请先选择或注册模板", severity="error")
            return
        if not self._inputs:
            path = self._resolve_path(self.query_one("#input-path", Input).value)
            if path is not None and path.exists():
                self._add_path(path)
        if not self._inputs:
            self.notify("请至少添加一份资料", severity="error")
            return
        for p in self._inputs:
            if not p.exists():
                self.notify(f"文件不存在: {p}", severity="error")
                return

        app: "RoomApp" = self.app  # type: ignore[assignment]
        ws = self._ws()
        out_name = (
            self.query_one("#input-output", Input).value.strip()
            or self._default_output_for_template()
        )
        output = (ws / out_name).resolve()

        self.notify("正在启动生成…", severity="information")
        app.start_run(
            template_id=self._template_id,
            inputs=list(self._inputs),
            output=output,
            budget=_DEFAULT_BUDGET,
        )
