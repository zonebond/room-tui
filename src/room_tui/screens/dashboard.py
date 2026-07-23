"""Session Dashboard: section tree + status card + events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, Static

from room_tui.widgets.smooth_scroll import SmoothListView

if TYPE_CHECKING:
    from room_tui.orch.session import SessionOrchestrator


STATUS_GLYPH = {
    "done": "✓",
    "generating": "●",
    "ready": "○",
    "pending": "…",
    "failed": "!",
    "placeholder": "◇",
    "empty": "…",
    "human_modified": "✎",
}


class StatusCard(Static):
    """Center status card."""

    def update_from_orch(self, orch: "SessionOrchestrator") -> None:
        st = orch.state
        # Status card: no Agent / provider branding — model name only if useful
        model_line = f"  model   {st.model}" if st.model else "  model   —"
        lines = [
            f"  {st.card_status}",
            f"  focus   {st.focus_section or '—'}",
            f"  phase   {st.phase or '—'}",
            f"  progress {st.progress or '—'}",
            model_line,
            f"  attempt {st.attempt or '—'}",
            "",
            f"  {st.card_detail or ''}",
        ]
        if st.card_peek:
            lines.append(f"  peek    {st.card_peek[:70]}")
        if st.error:
            lines.append(f"  error   {st.error[:70]}")
        self.update("\n".join(lines))


class EventLog(Static):
    def set_events(self, events: list[dict]) -> None:
        lines: list[str] = []
        for e in events[-40:]:
            t = e.get("type", "?")
            extra = ""
            if "key" in e:
                extra = str(e["key"])
            elif "section" in e:
                extra = str(e["section"])
            elif "error" in e:
                extra = str(e["error"])[:40]
            elif "progress" in e:
                extra = str(e["progress"])
            lines.append(f"{t:16} {extra}")
        self.update("\n".join(lines) if lines else "(no events)")


class DashboardScreen(Screen):
    BINDINGS = [
        Binding("q", "app.pop_screen", "Back", show=True),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("p", "pause", "Pause", show=True),
        Binding("c", "cancel_run", "Cancel", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    CSS = """
    DashboardScreen {
        layout: vertical;
    }
    #body {
        height: 1fr;
    }
    #tree-pane {
        width: 36%;
        border: solid $primary;
        padding: 0 1;
    }
    #card-pane {
        width: 40%;
        border: solid $accent;
        padding: 1;
    }
    #event-pane {
        width: 24%;
        border: solid $surface;
        padding: 0 1;
    }
    #tree-title, #card-title, #event-title {
        text-style: bold;
        color: $text-muted;
        margin-bottom: 1;
    }
    StatusCard {
        height: auto;
    }
    EventLog {
        height: 1fr;
    }
    ListView {
        height: 1fr;
    }
    #status-bar {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, orch: "SessionOrchestrator", session_id: str = ""):
        super().__init__()
        self.orch = orch
        self.session_id = session_id or orch.state.session_id
        self._items: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="tree-pane"):
                yield Label("SECTION TREE", id="tree-title")
                yield SmoothListView(id="section-list")
            with Vertical(id="card-pane"):
                yield Label("STATUS CARD", id="card-title")
                yield StatusCard(id="status-card")
            with Vertical(id="event-pane"):
                yield Label("EVENTS", id="event-title")
                yield EventLog(id="event-log")
        yield Label("", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self.orch.subscribe(self._on_event)
        self.set_interval(1.0, self._tick_ui)
        self.run_worker(self._initial_refresh(), exclusive=True)

    async def _initial_refresh(self) -> None:
        if self.session_id:
            try:
                await self.orch.refresh_snapshot(self.session_id)
            except Exception as e:
                self.query_one("#status-bar", Label).update(f"refresh error: {e}")
        self._render_all()

    def _on_event(self, _event: dict) -> None:
        # schedule UI update on main thread
        self.call_later(self._render_all)

    def _tick_ui(self) -> None:
        self._render_card()
        self._render_events()
        bar = self.query_one("#status-bar", Label)
        st = self.orch.state
        # Bottom chrome: progress only — never Agent / Pi branding
        bits = [
            st.session_id or "—",
            st.phase or "—",
            st.progress or "—",
            "运行中" if st.running else "空闲",
        ]
        if st.paused:
            bits.append("已暂停")
        # model name only if present (not provider/agent)
        if st.model:
            bits.append(st.model)
        bar.update("  ·  ".join(bits))

    def _render_all(self) -> None:
        self._render_tree()
        self._render_card()
        self._render_events()

    def _render_tree(self) -> None:
        lv = self.query_one("#section-list", SmoothListView)
        snap = self.orch.state.snapshot
        focus = self.orch.state.focus_section
        # rebuild list
        lv.clear()
        self._items = []
        if not snap:
            lv.append(ListItem(Label("(no snapshot — press r)")))
            return
        for sec in snap.sections:
            glyph = STATUS_GLYPH.get(sec.status, "?")
            indent = "  " * max(0, sec.level - 1)
            mark = "►" if sec.section_id == focus or (
                focus and sec.section_id in focus
            ) else " "
            title = sec.title or sec.section_id
            line = f"{mark}{indent}{glyph} {sec.section_id}  {title}"
            if len(line) > 48:
                line = line[:45] + "…"
            self._items.append(sec.section_id)
            lv.append(ListItem(Label(line)))

    def _render_card(self) -> None:
        self.query_one("#status-card", StatusCard).update_from_orch(self.orch)

    def _render_events(self) -> None:
        self.query_one("#event-log", EventLog).set_events(self.orch.state.last_events)

    async def action_refresh(self) -> None:
        if self.session_id:
            await self.orch.refresh_snapshot(self.session_id)
            self._render_all()

    def action_pause(self) -> None:
        self.orch.request_pause()
        self._render_card()

    def action_cancel_run(self) -> None:
        self.orch.request_cancel()
        self._render_card()
