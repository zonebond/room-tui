"""Home — project decision desk; bottom status bar; Agent hidden."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, Static

from room_tui.widgets.smooth_scroll import SmoothVerticalScroll
from room_tui.workspace import Workspace

if TYPE_CHECKING:
    from room_tui.app import RoomApp


PHASE_CN = {
    "generating": "生成中",
    "feeding": "喂入资料中",
    "complete": "已完成",
    "assembling": "组装中",
    "init": "初始化",
    "running": "运行中",
    "paused": "已暂停",
    "failed": "失败",
    "created": "已创建",
}


def _project_label(root: Path) -> str:
    """Short project name + path for bottom bar."""
    name = root.name or str(root)
    path = str(root)
    # keep bar readable
    if len(path) > 48:
        path = "…" + path[-46:]
    return f"{name}  ·  {path}"


def _model_label(provider: str, model: str) -> str:
    """Model only — never Agent / Pi / provider-as-agent branding."""
    if model:
        # show model id only; provider is infrastructure, not "which agent"
        return f"模型  {model}"
    return "模型  未配置"


class HomeScreen(Screen):
    """Project workbench home: one current task + bottom status bar."""

    BINDINGS = [
        Binding("q", "app.quit", "退出", show=True),
        Binding("r", "refresh", "刷新", show=True),
        Binding("n", "new_run", "新建", show=True),
        Binding("c", "continue_current", "继续", show=True),
        Binding("o", "open_dashboard", "查看", show=True),
        Binding("?", "show_help", "帮助", show=True),
    ]

    CSS = """
    HomeScreen {
        layout: vertical;
    }
    #hero {
        height: auto;
        padding: 1 2 0 2;
    }
    #hero-title {
        text-style: bold;
        text-align: center;
        width: 100%;
    }
    #hero-tag {
        text-align: center;
        color: $text-muted;
        width: 100%;
        margin-bottom: 1;
    }
    #main {
        height: 1fr;
        padding: 0 2 1 2;
    }
    #task-card {
        border: round $primary;
        padding: 1 2;
        min-height: 12;
        margin-bottom: 1;
    }
    #task-card.empty {
        border: round $surface;
    }
    #task-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    #task-body {
        margin-bottom: 1;
        min-height: 5;
    }
    #cta-row {
        height: auto;
        margin-top: 1;
    }
    #cta-row Button {
        width: 100%;
        margin-bottom: 1;
    }
    #btn-primary {
        text-style: bold;
    }
    #hints {
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 2;
        border-top: solid $primary 20%;
    }
    #status-bar.error {
        color: $error;
        background: $error 10%;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._ready = False  # model + engine usable (internal; not shown as Agent)
        self._has_task = False
        self._task_active = False
        self._session_id = ""
        self._manifest_status = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="hero"):
            yield Label("Room", id="hero-title")
            yield Label("模板 + 资料 → 结构化文档", id="hero-tag")
        with SmoothVerticalScroll(id="main"):
            with Vertical(id="task-card", classes="empty"):
                yield Label("当前任务", id="task-title")
                yield Static("加载中…", id="task-body")
                with Vertical(id="cta-row"):
                    yield Button("…", id="btn-primary", variant="success")
                    yield Button("仅查看进度  [o]", id="btn-open", variant="default")
                    yield Button("新建文档  [n]", id="btn-new", variant="default")
            yield Label("n 新建  ·  c 继续  ·  o 查看  ·  r 刷新  ·  q 退出", id="hints")
        # Bottom status bar: project + model only (no Agent)
        yield Static("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    def _ws(self) -> Workspace:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        return Workspace(Path(app.cfg.workspace or Path.cwd()))

    def _set_status_bar(self, root: Path, *, model: str, provider: str, ok: bool, err: str = "") -> None:
        bar = self.query_one("#status-bar", Static)
        left = _project_label(root)
        right = _model_label(provider, model)
        if not ok and err:
            bar.update(f"{left}    ·    {err}")
            bar.add_class("error")
        else:
            bar.update(f"{left}    ·    {right}")
            bar.remove_class("error")

    async def _load(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        ws = self._ws()
        root = ws.root

        # readiness (internal) — never paint "Pi" / "Agent" on UI
        ok_engine = False
        ok_model_runtime = False
        err_msg = ""
        try:
            await app.load_engine_version()
            ok_engine = True
        except Exception:
            err_msg = "服务未就绪"
        try:
            ok_model_runtime = await app.check_pi()
            if not ok_model_runtime and not err_msg:
                err_msg = "模型运行时未就绪"
        except Exception:
            if not err_msg:
                err_msg = "模型运行时未就绪"

        self._ready = ok_engine and ok_model_runtime
        self._set_status_bar(
            root,
            model=app.cfg.model,
            provider=app.cfg.provider,
            ok=self._ready,
            err=err_msg,
        )

        task_card = self.query_one("#task-card", Vertical)
        task_body = self.query_one("#task-body", Static)
        btn_primary = self.query_one("#btn-primary", Button)
        btn_open = self.query_one("#btn-open", Button)

        manifest = ws.load_manifest()

        if manifest is None or not manifest.session_id:
            self._has_task = False
            self._task_active = False
            self._session_id = ""
            self._manifest_status = "empty"
            task_card.add_class("empty")
            task_body.update(
                "本项目还没有工程任务。\n"
                "\n"
                "选择模板、添加资料后即可生成。\n"
                "过程文件保存在本目录 .pd/，交付物默认 output.md。"
            )
            btn_primary.label = "新建文档  [n]"
            btn_primary.variant = "success"
            btn_open.disabled = True
            return

        self._has_task = True
        self._session_id = manifest.session_id
        phase = manifest.phase
        progress = manifest.progress
        status = manifest.status

        try:
            st = await app.load_session_status(manifest.session_id)
            if isinstance(st, dict) and st:
                phase = str(st.get("phase") or phase)
                progress = str(st.get("progress") or progress)
                status = (
                    "complete"
                    if phase in ("complete", "done")
                    else ("running" if phase else status)
                )
                ws.update_manifest_progress(
                    phase=phase,
                    progress=progress,
                    status=status,
                )
        except Exception:
            pass

        self._manifest_status = status
        phase_cn = PHASE_CN.get(phase, phase or status or "未知")
        complete = status == "complete" or phase in ("complete", "done")
        failed = status == "failed"
        self._task_active = not complete

        task_card.remove_class("empty")
        lines = [
            f"模板    {manifest.template_id or '—'}",
            f"状态    {phase_cn}" + (f"  ·  {progress}" if progress else ""),
            f"输出    {Path(manifest.output).name if manifest.output else 'output.md'}",
        ]
        if complete:
            lines += ["", "已完成。可查看结果，或新建下一份文档。"]
        elif failed:
            lines += ["", "上次未成功结束。可继续重试，或新建任务。"]
        else:
            lines += ["", "有未完成的生成。继续，或先查看进度。"]

        task_body.update("\n".join(lines))

        btn_open.disabled = False
        if complete:
            btn_primary.label = "查看结果  [o]"
            btn_primary.variant = "primary"
        elif failed:
            btn_primary.label = "继续 / 重试  [c]"
            btn_primary.variant = "error"
        else:
            btn_primary.label = "继续生成  [c]"
            btn_primary.variant = "success"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-primary":
            if not self._has_task or self._manifest_status == "empty":
                self.action_new_run()
            elif self._manifest_status == "complete":
                self.action_open_dashboard()
            else:
                self.action_continue_current()
        elif bid == "btn-open":
            self.action_open_dashboard()
        elif bid == "btn-new":
            self.action_new_run()

    def action_refresh(self) -> None:
        self.run_worker(self._load(), exclusive=True)

    def action_show_help(self) -> None:
        self.notify(
            "本页只服务当前项目。n 新建 · c 继续 · o 查看 · q 退出",
            title="快捷键",
            timeout=5,
        )

    def action_new_run(self) -> None:
        if not self._ready:
            self.notify("环境未就绪，请检查模型配置后重试", severity="error")
            return
        if self._task_active:
            self.notify("当前有未完成任务；新建将开启新的生成", severity="warning", timeout=5)
        app: "RoomApp" = self.app  # type: ignore[assignment]
        app.open_wizard()

    def action_continue_current(self) -> None:
        if not self._session_id:
            self.notify("还没有任务，请先新建", severity="warning")
            return
        app: "RoomApp" = self.app  # type: ignore[assignment]
        app.resume_session(self._session_id)

    def action_open_dashboard(self) -> None:
        if not self._session_id:
            self.notify("还没有可查看的任务", severity="warning")
            return
        app: "RoomApp" = self.app  # type: ignore[assignment]
        app.open_dashboard(self._session_id)
