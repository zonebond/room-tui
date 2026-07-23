"""Main shell — Grok-like left (scrollback + 1-line prompt that grows) + right status."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from rich.cells import cell_len, set_cell_size
from textual import on, events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.geometry import Size
from textual.screen import Screen
from textual.widgets import Static

from room_tui.slash import (
    MAX_DROPDOWN_ROWS,
    SlashItem,
    complete_slash,
    format_arg_dropdown,
    format_dropdown,
    help_text,
    match_slash,
    resolve_slash_token,
    invalidate_skill_cache,
)
from room_tui.widgets.prompt import PromptField
from room_tui.widgets.prompt_history import PromptHistoryNav
from room_tui.widgets.smooth_scroll import SmoothRichLog, SmoothVerticalScroll
from room_tui.ui_state import (
    COLOR_BRAND,
    COLOR_BRAND_ON_BAR,
    COLOR_ERR,
    COLOR_MSG_BG,
    COLOR_MSG_DIM,
    COLOR_MSG_HI,
    COLOR_MSG_LABEL,
    COLOR_MSG_MID,
    COLOR_MSG_USER,
    COLOR_OK,
    COLOR_WARN,
    PHASE_CN,
    PipelineState,
    map_event_to_pipeline,
    mode_label,
    normalize_mode_display,
    render_chapter_lines,
)
from room_tui.workspace import RunManifest, Workspace


class ChromeStatic(Static):
    """UI chrome Static — never part of drag-select.

    Textual multi-line selection walks every selectable leaf in the Y-band of
    the common ancestor. Without this, dragging in #msg-log also paints the
    task sidebar / title / footer (Grok: only the conversation selects).
    """

    ALLOW_SELECT = False


@dataclass
class ExpandableBlock:
    """Foldable band in #msg-log (Grok Read/code collapse)."""

    id: str
    kind: str  # read | bash | code
    content: str
    path: str = ""
    language: str = "text"
    expanded: bool = False
    start: int = 0  # index into log.lines
    count: int = 0  # strips for body + footer
    width: int = 80


@dataclass
class UserPromptSection:
    """One submitted user prompt as a scroll section (Grok sticky header).

    Like iOS contacts letter groups: while the viewport sits in this section's
    body, the prompt stays sticky at the top; the next user prompt pushes it off.
    """

    start: int  # first strip index in #msg-log
    count: int  # strips occupied by the user band (+ trailing gap row)
    text: str
    when: Any = None

if TYPE_CHECKING:
    from room_tui.app import RoomApp


SIDEBAR_WIDTH = 34
MAX_PROMPT_LINES = 8

# Appended onto Pi's default agent system prompt (tools/skills stay enabled).
CHAT_SYSTEM = """你同时运行在「Room」工程间 TUI 中（面向用户时不要强调底层品牌名）。
当前工作目录即项目根；你拥有完整的编程 Agent 能力（读/写/改文件、shell、skills 等），请按任务需要正常使用工具。

Room 额外能力（斜杠命令，用户可在输入框使用）：
- /new 新建文档生成任务  ·  /continue 继续未完成任务
- /template register <样例> [名称] 注册模板  ·  /template list 查看
- /pause /cancel 暂停/取消生成  ·  /status 查看任务状态
- 文档流水线由 paper-derived 引擎驱动；你也可直接帮用户分析 output.md、模板与源码。

回答以中文为主，简洁可执行。工具调用请走真实 tool 接口，不要把工具协议 XML 当正文输出。"""


class ShellScreen(Screen):
    """Grok-style scrollback + compact prompt (grows) + fixed right sidebar."""

    # Enable Textual text selection on the scrollback (Grok: drag-select → copy).
    ALLOW_SELECT = True

    BINDINGS = [
        Binding("ctrl+q", "app.quit", "退出", show=True),
        Binding("ctrl+c", "interrupt", "中断", show=False, priority=True),
        Binding("escape", "blur_or_idle", "Esc", show=False, priority=True),
        Binding("ctrl+p", "focus_input", "输入", show=False),
        Binding("ctrl+r", "refresh", "刷新", show=True),
        Binding("ctrl+l", "clear_scrollback", "清屏", show=False),
        # Task sidebar: Ctrl+B (Win/Linux), Cmd+B / Super+B (macOS).
        Binding("ctrl+b,super+b", "toggle_sidebar", "侧栏", show=True),
        # Ctrl+M → 连接模型 (provider/key). Switch among models: /model
        Binding("ctrl+m", "model_setup", "连接模型", show=True, priority=True),
        # Grok: PageUp/Down scroll conversation even while the prompt is focused.
        Binding("pageup", "scroll_log_page_up", "上翻", show=False),
        Binding("pagedown", "scroll_log_page_down", "下翻", show=False),
        Binding("e", "toggle_expand", "展开/折叠", show=False),
        # Do NOT bind bare letters (n/c/…) — they steal pinyin IME keys.
    ]

    CSS = f"""
    ShellScreen {{
        layout: vertical;
        background: $background;
    }}
    /*
     * Drag-select highlight on the message list.
     * Default $screen-selection-foreground is transparent → rich_style collapses
     * fg onto bg (solid bar, glyphs invisible). Force a readable pair.
     */
    ShellScreen > .screen--selection {{
        background: $primary 45%;
        color: $text;
    }}
    /*
     * Header — muted system primary (pair with footer).
     * No border with height:1 (eats the only content row).
     * Title/mode colors still come from Rich markup in _set_title.
     */
    #title-bar {{
        dock: top;
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        background: $primary-darken-2;
        color: $text;
        padding: 0 1;
        text-style: none;
        border: none;
        margin: 0;
    }}
    #body {{
        height: 1fr;
        min-height: 0;
        width: 100%;
    }}
    #messages {{
        width: 1fr;
        height: 1fr;
        min-height: 0;
        min-width: 0;
        layout: vertical;
        border-right: none;
        background: $background;
    }}
    #msg-log {{
        height: 1fr;
        min-height: 0;
        background: transparent;
        /* Grok outer_vpad=1; L/R 1 for edge breath. Scroll via wheel/trackpad. */
        padding: 1 1 1 1;
        /* Hide scrollbar — reclaim the gutter for message content (Grok-like). */
        scrollbar-gutter: auto;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
        scrollbar-visibility: hidden;
        scrollbar-background: transparent;
        scrollbar-color: transparent;
        scrollbar-corner-color: transparent;
    }}
    /*
     * Sticky user-prompt header (contacts-style section title).
     * Docked over the message list; shown only when the natural band has
     * scrolled off the top of the viewport.
     */
    #sticky-user {{
        dock: top;
        width: 100%;
        height: auto;
        max-height: 8;
        display: none;
        overflow-y: hidden;
        padding: 0 1;
        background: $background;
        layer: sticky-user;
    }}
    #sticky-user.-active {{
        display: block;
    }}
    /*
     * Right rail — Grok list/todo feel:
     * neutral lift, hairline edge, quiet titles.
     */
    #sidebar {{
        width: {SIDEBAR_WIDTH};
        min-width: {SIDEBAR_WIDTH};
        max-width: {SIDEBAR_WIDTH};
        height: 1fr;
        min-height: 0;
        layout: vertical;
        background: $background-lighten-1;
        border-left: solid $foreground 10%;
        /* Left pad only: breathe off the message|sidebar hairline.
         * Right pad 0 so the rail flushes the window edge (no dead gutter). */
        padding: 0 0 0 1;
        overflow: hidden;
    }}
    /* Collapsed: fully hide rail so messages flush to the window edge.
     * Re-open via Ctrl+B / Cmd+B (no residual 1-col gutter). */
    #sidebar.-collapsed {{
        display: none;
        width: 0;
        min-width: 0;
        max-width: 0;
        padding: 0;
        border-left: none;
    }}
    #sidebar.-collapsed #steps-pane,
    #sidebar.-collapsed #chapters-pane,
    #sidebar.-collapsed #sidebar-rail {{
        display: none;
    }}
    /* Legacy rail widget (kept in tree; unused when fully collapsed). */
    #sidebar-rail {{
        display: none;
        width: 0;
        height: 1fr;
        min-height: 0;
        padding: 0;
        content-align: center middle;
        color: $text-muted;
        background: $background-lighten-1;
        text-style: none;
    }}
    /* min-height:0 so fr panes shrink and let children scroll. */
    #steps-pane {{
        height: 2fr;
        min-height: 0;
        padding: 0;
        border-bottom: solid $foreground 10%;
        background: transparent;
        layout: vertical;
        overflow: hidden;
    }}
    #chapters-pane {{
        height: 3fr;
        min-height: 0;
        padding: 0;
        background: transparent;
        layout: vertical;
        overflow: hidden;
    }}
    /* Tab-style section headers — left inset only; right flushes window edge.
     * No border with height:1 (would eat the label). */
    .side-tab {{
        height: 1;
        min-height: 1;
        max-height: 1;
        padding: 0 0 0 1;
        background: $surface-lighten-1;
        color: $text-muted;
        text-style: none;
        border: none;
    }}
    /*
     * Sidebar lists: scroll with mouse wheel / trackpad, no visible scrollbar.
     * Visible bars in a ~34-col rail look like dual gutters and waste width.
     * Left text pad only; no right gutter (window-edge flush).
     */
    #steps-scroll, #chapters-scroll {{
        height: 1fr;
        min-height: 0;
        padding: 0 0 0 1;
        overflow-x: hidden;
        overflow-y: auto;
        /* NOTE: scrollbar-gutter: stable + size-vertical: 0 collapses child
         * width to 0 in Textual (blank sidebar). Keep gutter auto. */
        scrollbar-gutter: auto;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
        scrollbar-visibility: hidden;
    }}
    #steps-body, #chapters-body {{
        width: 100%;
        height: auto;
        color: $text;
        padding: 0;
        margin: 0;
        overflow: hidden;
    }}
    /* Chapter tree: no wrap — lines are pre-truncated by cell width. */
    #chapters-body {{
        text-wrap: nowrap;
    }}
    /*
     * Prompt — full-width solid box (all four sides).
     * No side margin (was a dead gutter); keep padding for content inset.
     */
    /*
     * Live run indicator — sits at bottom of #messages only.
     * height 0 when idle (no phantom bar); height 1 + surface when active.
     * Never use border with height:1 (eats content).
     */
    #activity {{
        height: 0;
        min-height: 0;
        max-height: 0;
        width: 100%;
        margin: 0;
        padding: 0 1;
        background: transparent;
        color: $text;
        border: none;
        overflow: hidden;
    }}
    #activity.active {{
        height: 1;
        min-height: 1;
        max-height: 1;
        background: $surface-lighten-1;
    }}
    /* Grok-style multi-line slash dropdown (cmds + skills + /model). */
    #slash-suggest {{
        display: none;
        height: 0;
        min-height: 0;
        max-height: 0;
        width: 100%;
        margin: 0;
        padding: 0 1;
        color: $text;
        background: $surface-lighten-1;
        border-top: solid $primary 35%;
        overflow-y: auto;
        overflow-x: hidden;
    }}
    #slash-suggest.active {{
        display: block;
        height: auto;
        min-height: 1;
        max-height: 16;
    }}
    /* Grok rewind picker (Esc Esc / /rewind) — same chrome as slash. */
    #rewind-suggest {{
        display: none;
        height: 0;
        min-height: 0;
        max-height: 0;
        width: 100%;
        margin: 0;
        padding: 0 1;
        color: $text-muted;
        background: $surface-lighten-1;
        border-top: solid $foreground 12%;
        overflow: hidden;
    }}
    #rewind-suggest.active {{
        display: block;
        height: auto;
        min-height: 1;
        max-height: 14;
    }}
    /* Inline /new three-step picker (same chrome as rewind). */
    #new-suggest {{
        display: none;
        height: 0;
        min-height: 0;
        max-height: 0;
        width: 100%;
        margin: 0;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
        border-top: solid $primary 30%;
    }}
    #new-suggest.active {{
        display: block;
        height: auto;
        min-height: 1;
        max-height: 16;
    }}
    #prompt-row {{
        width: 100%;
        height: auto;
        margin: 0;
        padding: 0 1;
        background: transparent;
        border: solid $foreground 22%;
    }}
    #prompt-row:focus-within {{
        border: solid $foreground 22%;
    }}
    /* Legacy preview strip — unused (Grok multi-line lives inside #cmd-input). */
    #composer-preview {{
        display: none;
        height: 0;
        max-height: 0;
        margin: 0;
        padding: 0;
        border: none;
    }}
    #cmd-input {{
        width: 100%;
        height: 1;
        min-height: 1;
        max-height: 8;
        border: none;
        margin: 0;
        /* Inset comes from #prompt-row padding only (avoid double gap). */
        padding: 0;
        background: transparent;
        color: $foreground;
    }}
    #cmd-input:focus {{
        background: transparent;
        color: $foreground;
    }}
    /*
     * Footer — same muted primary as title-bar (scheme B).
     * No border-top: with height:1 a top border eats the only row.
     * Prefer $text over $text-muted so keys stay readable on blue.
     */
    #status-bar {{
        dock: bottom;
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        margin: 0;
        layout: horizontal;
        background: $primary-darken-2;
        color: $text;
        padding: 0;
        border: none;
    }}
    #status-left {{
        width: 1fr;
        height: 1;
        padding: 0 1;
        color: $text;
        text-style: none;
    }}
    #status-right {{
        width: auto;
        min-width: 16;
        max-width: 55%;
        height: 1;
        padding: 0 1;
        color: $text;
        text-align: right;
        text-wrap: nowrap;
        overflow: hidden;
    }}
    #status-bar.hint {{
        background: $warning 25%;
    }}
    #status-bar.hint #status-left {{
        color: $warning;
        text-style: none;
    }}
    #status-bar.error {{
        background: $error 25%;
    }}
    #status-bar.error #status-right {{
        color: $error;
        text-style: none;
    }}
    #status-bar.model-warn {{
        background: $warning 20%;
    }}
    #status-bar.model-warn #status-right {{
        color: $warning;
        text-style: bold;
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self._pipe = PipelineState()
        self._ready = False
        self._env_err: str = ""
        self._subscribed = False
        self._ws_root: Path | None = None
        self._chat_busy = False
        self._chat_cancel = None  # asyncio.Event for in-flight Agent turn
        # Grok-style FIFO: free-form messages submitted while busy wait here.
        # Each item: {"text": str, "skill_name": str|None, "painted": bool}
        self._msg_queue: list[dict[str, Any]] = []
        # Agent turn blocks (Grok-like): Thinking… / Thought for Xs / one row per tool.
        # Distinct from doc-gen in-place ``┃ ◆ 生成`` rows.
        self._agent_tool_seq = 0
        self._agent_live_tool = ""
        self._agent_live_args: Any = None
        self._agent_streamed_tools: list[str] = []  # keys already finalized in scrollback
        self._agent_think_t0: float = 0.0
        # Streaming Thinking body (Grok live Thinking block text).
        self._agent_thinking_buf: str = ""
        self._agent_thinking_dirty: bool = False
        self._agent_thinking_last_paint: float = 0.0
        # Streaming answer body (final prose — token-by-token, not one-shot flash).
        self._agent_answer_buf: str = ""
        self._agent_answer_dirty: bool = False
        self._agent_answer_last_paint: float = 0.0
        # True if we inserted a blank above the current live Thinking… card.
        self._thinking_gap_above: bool = False
        # Last finished Thought duration (for durable history = Grok "Thought for Xs")
        self._agent_last_thought_s: float | None = None
        self._prompt_focused = True
        self._slash_cycle = 0
        # Grok-like slash dropdown state (cmds + skills)
        self._slash_open = False
        self._slash_matches: list[SlashItem] = []
        self._slash_selected = 0
        self._slash_mode: str = "cmd"  # "cmd" | "arg"
        # Grok rewind picker (Esc Esc / /rewind)
        self._rewind_open = False
        self._rewind_items: list[dict[str, Any]] = []
        self._rewind_selected = 0
        # Inline /new flow (template → inputs → confirm) — no full-screen wizard
        self._new_open = False
        self._new_step: str = ""  # template | inputs | confirm
        self._new_templates: list[dict[str, Any]] = []
        self._new_cursor = 0
        self._new_template_id = ""
        self._new_template_name = ""
        self._new_inputs: list[Path] = []
        self._new_suggestions: list[Path] = []
        self._new_output = "output.md"
        self._ctrl_c_at = 0.0
        self._esc_at = 0.0  # double-Esc: clear / rewind / cancel
        self._footer_hint = ""
        self._footer_timer = None
        # Last painted footer state — skip Static.update (→ full re-layout)
        # when nothing changed (it runs on every keystroke).
        self._footer_paint_cache: tuple | None = None
        self._env_err = ""
        # Model readiness (Grok-like): unset / unknown-to-pi blocks chat with guidance.
        self._model_ok: bool = True
        self._model_issue: str = ""
        self._bootstrap_done: bool = False
        self._committed_lines: list[str] = []
        # Grok-like ↑/↓ prompt history (empty ↑ opens, ↓ at newest closes).
        self._prompt_hist = PromptHistoryNav()
        # While replaying chat-history.jsonl, skip re-append to disk.
        self._restoring_history = False
        # Live activity — Grok-like status (not raw model dump)
        self._activity_label = ""
        self._activity_phase = "thinking"  # thinking | writing | parsing
        self._activity_on = False
        self._activity_t0 = 0.0
        self._spin_i = 0
        self._spin_timer = None
        # Cached suffix after the spinner glyph so ticks only swap 1 cell
        self._activity_tail = " Working..."
        self._activity_elapsed_i = -1
        # Live step row: rewrite the last N strips in place (spinner → ✓/✗).
        # Prefer absolute start index for multi-line streaming bodies (answer /
        # thinking); strip count alone was capped and could leave stale rows.
        self._live_step_active: bool = False
        self._live_step_strips: int = 0
        self._live_step_start: int = -1  # first strip index of open live region
        self._live_step_key: str = ""  # logical unit, e.g. section id
        self._live_step_text: str = ""  # base label (no phase/elapsed)
        self._live_step_phase: str = ""  # e.g. 模型生成 / 准备提示
        self._live_step_t0: float = 0.0  # when this live row opened
        self._live_step_elapsed_i: int = -1  # last painted second
        # Foldable bands (Read / long bash / long fenced code) — Grok expand.
        self._expandables: list[ExpandableBlock] = []
        self._expand_seq: int = 0
        # User prompts as section headers (sticky while scrolling their turn).
        self._user_sections: list[UserPromptSection] = []
        self._sticky_section_key: str | None = None  # avoid redundant repaint
        # Grok Build scrollback model (xai-grok-pager scrollback/state/nav.rs):
        #   • On submit: pin user prompt to viewport top + enable follow with
        #     ``follow_preserve_scroll`` (page-flip: stay put while content fits).
        #   • When content overflows viewport: clear preserve → stick to bottom
        #     so streaming tokens stay visible (follow_scroll_to_bottom).
        #   • User scroll-up: follow off. Overscroll / near-bottom: follow on.
        #   • Sticky user header (iOS contacts) when browsing past a prompt.
        # Pad blanks under the turn let short answers stay top-aligned (not
        # glued to the composer).
        self._user_pin_active: bool = False
        self._user_pin_follow: bool = False
        self._follow_preserve_scroll: bool = False
        self._user_pin_start: int = 0
        self._scroll_pad_count: int = 0
        self._pin_scroll_guard: bool = False
        # Task sidebar (进度 / 大纲) collapsed → messages reclaim width.
        self._sidebar_collapsed: bool = False
        # Last measured #msg-log content width (reflow when sidebar/resize changes it).
        self._msg_log_layout_width: int = 0
        self._reflow_last_w: int = 0
        self._reflow_last_ts: float = 0.0
        # Sidebar toggle while live: reflow immediately (force) + again when idle
        self._pending_reflow_w: int = 0
        # Detect drag-select vs plain click on #msg-log (expand vs copy).
        self._msg_log_mouse_down: tuple[int, int] | None = None
        self._msg_log_did_drag: bool = False
        # Last absolute line under the pointer (double-click expand vs copy toast).
        self._msg_log_last_abs_y: int = -1
        # Expand/Collapse hover: soft blend wash on body band + footer (Grok).
        self._expand_hover_id: str | None = None
        self._expand_hover_start: int = -1
        self._expand_hover_count: int = 0
        self._expand_hover_cache: list[Any] | None = None

    def compose(self) -> ComposeResult:
        yield ChromeStatic(
            f"[bold {COLOR_BRAND_ON_BAR}]Room[/bold {COLOR_BRAND_ON_BAR}]"
            f"  [dim]·[/dim]  [dim]空闲[/dim]",
            id="title-bar",
        )
        with Horizontal(id="body"):
            with Vertical(id="messages"):
                # Contacts-style sticky user prompt (shown when natural band is off-top).
                yield ChromeStatic("", id="sticky-user")
                yield SmoothRichLog(
                    id="msg-log",
                    highlight=False,
                    markup=True,
                    wrap=True,
                    auto_scroll=True,
                    max_lines=3000,
                )
                # Live status only under scrollback (not full-width under sidebar)
                yield ChromeStatic("", id="activity")
            with Vertical(id="sidebar"):
                # Kept for DOM stability; fully hidden when collapsed (no edge gutter).
                yield ChromeStatic("›", id="sidebar-rail")
                with Vertical(id="steps-pane"):
                    yield ChromeStatic(
                        " 进度  [dim]‹[/dim]", id="steps-tab", classes="side-tab"
                    )
                    with SmoothVerticalScroll(id="steps-scroll"):
                        yield ChromeStatic(
                            "[dim] ○  —[/dim]",
                            id="steps-body",
                            markup=True,
                        )
                with Vertical(id="chapters-pane"):
                    yield ChromeStatic(
                        " 大纲  [dim]‹[/dim]", id="chapters-tab", classes="side-tab"
                    )
                    with SmoothVerticalScroll(id="chapters-scroll"):
                        yield ChromeStatic(
                            "[dim]  尚无章节\n  生成后显示大纲[/dim]",
                            id="chapters-body",
                            markup=True,
                        )
        yield ChromeStatic("", id="slash-suggest")
        yield ChromeStatic("", id="rewind-suggest")
        yield ChromeStatic("", id="new-suggest")
        # Full-width prompt strip (top/bottom rules): preview + field.
        with Vertical(id="prompt-row"):
            yield ChromeStatic("", id="composer-preview")
            yield PromptField(
                placeholder="Message…  (Shift+Enter 换行 · Enter 发送 · / · ↑↓)",
                id="cmd-input",
            )
        with Horizontal(id="status-bar"):
            yield ChromeStatic("", id="status-left")
            yield ChromeStatic("", id="status-right")

    def on_mount(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        root = Path(app.cfg.workspace or Path.cwd()).resolve()
        self._ws_root = root
        # Restore last sidebar open/closed before first footer paint
        self._sidebar_collapsed = bool(getattr(app.cfg, "sidebar_collapsed", False))
        self._paint_footer()
        # Not ready until bootstrap finishes (do not pretend OK with model label)
        self._set_bottom_bar(root, app.cfg.model, ok=False, err="正在初始化…")
        self._set_title(mode="空闲")
        self._render_steps()
        if not self._subscribed:
            app.orch.subscribe(self._on_orch_event)
            self._subscribed = True
        # Focus input immediately; re-sync hardware cursor after first layout.
        self._focus_prompt()
        self.call_after_refresh(self._focus_prompt)
        self.set_timer(0.05, self._focus_prompt)
        self.run_worker(self._bootstrap(), exclusive=True)
        # Apply CSS class after mount (widgets exist); no toast on restore.
        self.call_after_refresh(self._restore_sidebar_layout)
        # Seed layout width after first paint (sidebar open by default).
        self.call_after_refresh(self._seed_msg_log_layout_width)

    def _restore_sidebar_layout(self) -> None:
        """Re-apply persisted sidebar collapse without re-writing config."""
        self._set_sidebar_collapsed(
            self._sidebar_collapsed,
            persist=False,
            announce=False,
        )

    def _seed_msg_log_layout_width(self) -> None:
        try:
            self._msg_log_layout_width = self._msg_log_width()
        except Exception:
            self._msg_log_layout_width = 0

    def on_resize(self, event: events.Resize) -> None:
        """Re-flow sticky header + message bands when the terminal size changes."""
        self.call_after_refresh(self._after_column_layout_change)

    def _focus_prompt(self) -> None:
        """Focus the prompt and park the terminal hardware cursor there (IME)."""
        try:
            inp = self.query_one("#cmd-input", PromptField)
            inp.focus()
            self._prompt_focused = True
            # content_region is valid only after layout; sync for IME popup.
            inp._sync_terminal_cursor()
        except Exception:
            pass

    # ── scrollback ──────────────────────────────────────────

    def _expected_msg_log_width(self) -> int:
        """Column width from #body layout + sidebar state (no laggy content_size).

        Used after sidebar toggle when #msg-log still reports the *old* width
        for one or more frames.
        """
        try:
            body_w = int(self.query_one("#body").size.width or 0)
        except Exception:
            body_w = 0
        if body_w <= 0:
            try:
                body_w = int(self.size.width or 0)
            except Exception:
                body_w = 80
        # #msg-log has padding L=1 R=1; collapsed sidebar takes no columns.
        if self._sidebar_collapsed:
            return max(20, body_w - 2)
        return max(20, body_w - SIDEBAR_WIDTH - 2)

    def _msg_log_width(self) -> int:
        """Visible content width of #msg-log (for full-row background bands).

        Must match the width RichLog uses when painting strips. #msg-log has
        padding L=1 R=1 so content stops one cell short of the scrollbar.
        Prefer ``scrollable_content_region`` (already excludes padding).

        After sidebar toggle, prefer the larger of measured vs expected so we
        never paint narrow bands into a wide column (or vice versa).
        """
        expected = self._expected_msg_log_width()
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return max(20, int(self._msg_log_layout_width or expected or 80))
        w = 0
        try:
            w = int(log.scrollable_content_region.width or 0)
        except Exception:
            w = 0
        if w <= 0:
            try:
                w = int(log.content_size.width or 0)
            except Exception:
                w = 0
        if w <= 0:
            try:
                w = int(log.size.width or 0) - 2
            except Exception:
                w = 0
        if w <= 0:
            try:
                w = int(self.query_one("#messages").size.width or 0) - 2
            except Exception:
                w = 0
        if w <= 0:
            w = int(self._msg_log_layout_width or expected or 80)
        # If measured lags behind sidebar layout, trust expected geometry
        if abs(w - expected) >= 4:
            # Prefer expected when sidebar was just toggled (layout lag)
            if expected >= 20:
                w = expected
        return max(20, w)

    def _write(self, markup: str, *, scroll_end: bool | None = None) -> None:
        """Write markup at full log width (keeps alignment with band rows).

        *scroll_end*:
          - ``None`` (default): scroll to end unless user-pin is active
          - ``False``: never auto-scroll (expand/collapse must keep viewport)
          - ``True``: scroll to end when already at bottom (and not pin-active)
        """
        self._prepare_write_under_user_pin()
        log = self.query_one("#msg-log", SmoothRichLog)
        w = self._msg_log_width()
        if scroll_end is None:
            do_end = not self._user_pin_active
        elif self._user_pin_active:
            do_end = False
        elif scroll_end:
            try:
                do_end = log.scroll_y >= max(0, log.max_scroll_y - 1)
            except Exception:
                do_end = True
        else:
            do_end = False
        log.write(
            markup,
            width=w,
            expand=False,
            shrink=False,
            scroll_end=do_end,
            animate=False,
        )
        if scroll_end is not False:
            self._refresh_user_pin_after_write()

    def _write_renderable(self, renderable: Any, *, scroll_end: bool = True) -> None:
        """Write a renderable pre-sized to the full message column width.

        Always pass the same ``width`` we used to pad background spaces, so
        RichLog does not re-pad with blank (no-bg) cells on the right.

        Pass ``scroll_end=False`` for in-place rewrites (expand/collapse) so the
        viewport does not jump to the bottom.
        """
        self._prepare_write_under_user_pin()
        log = self.query_one("#msg-log", SmoothRichLog)
        try:
            at_bottom = log.scroll_y >= max(0, log.max_scroll_y - 1)
        except Exception:
            at_bottom = True
        w = self._msg_log_width()
        # While pinning the latest user turn to the top (Grok), never auto-jump
        # to the bottom — new Thinking/tools grow under the pinned prompt.
        if self._user_pin_active or not scroll_end:
            do_end = False
        else:
            do_end = at_bottom
        log.write(
            renderable,
            width=w,
            expand=False,
            shrink=False,
            scroll_end=do_end,
            animate=False,
        )
        if scroll_end:
            self._refresh_user_pin_after_write()

    def _msg_viewport_height(self) -> int:
        """Visible row budget of #msg-log (for Grok top-pin pad)."""
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            h = int(log.scrollable_content_region.height or 0)
            if h <= 0:
                h = int(log.size.height or 0)
            return max(8, h)
        except Exception:
            return 24

    def _prepare_write_under_user_pin(self) -> None:
        """Remove trailing scroll pad so real content appends in order.

        Pad is always cleared before a real write (whether pin is active or a
        leftover breath pad from a finished short turn).
        """
        if self._scroll_pad_count > 0:
            self._clear_scroll_pad()

    def _content_end_line(self) -> int:
        """First line index after real content (excludes pin scroll-pad blanks)."""
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            return max(0, len(log.lines) - max(0, int(self._scroll_pad_count)))
        except Exception:
            return 0

    def _follow_max_scroll_y(self) -> float:
        """Max scroll Y that keeps the content tail in the viewport (Grok max_offset)."""
        pin = max(0, int(self._user_pin_start))
        try:
            vh = max(1, self._msg_viewport_height())
            content_end = self._content_end_line()
            # Prefer content tail; never force above the pin row.
            return float(max(pin, content_end - vh))
        except Exception:
            return float(pin)

    def _pin_follow_scroll_y(self) -> float:
        """Target scroll Y while ``follow_mode`` is on (Grok follow_scroll_to_bottom).

        With ``follow_preserve_scroll`` (page-flip after submit): keep the user
        prompt at the top until the turn no longer fits; then switch to tail.
        Without preserve: always pin the content tail (normal follow).
        """
        pin = max(0, int(self._user_pin_start))
        try:
            vh = max(1, self._msg_viewport_height())
            content_end = self._content_end_line()
            turn_h = max(0, content_end - pin)
            fits = turn_h <= max(1, vh - 1)
            if self._follow_preserve_scroll:
                if fits:
                    return float(pin)
                # Overflowed past the page-flip pin → track the tail.
                self._follow_preserve_scroll = False
                return self._follow_max_scroll_y()
            return self._follow_max_scroll_y()
        except Exception:
            return float(pin)

    def _refresh_user_pin_after_write(self) -> None:
        """Re-pad under the turn; apply Grok follow when still tracking the turn."""
        if not self._user_pin_active:
            self._update_sticky_user_prompt()
            return
        self._apply_scroll_pad()
        # Grok: user scrolled away → do not yank them back.
        if self._user_pin_follow:
            target = int(self._pin_follow_scroll_y())
            # During preserve + fits: only re-pin if we drifted (avoid jitter).
            if self._follow_preserve_scroll:
                try:
                    log = self.query_one("#msg-log", SmoothRichLog)
                    cur = float(log.scroll_y)
                    if abs(cur - float(target)) > 0.75:
                        self._scroll_line_to_top(target)
                except Exception:
                    self._scroll_line_to_top(target)
            else:
                self._scroll_line_to_top(target)
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            self._update_sticky_user_prompt(float(log.scroll_y))
        except Exception:
            self._update_sticky_user_prompt()

    def _clear_scroll_pad(self) -> None:
        """Drop blank trailing pad lines used to pin user message to top."""
        if self._scroll_pad_count <= 0:
            return
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            n = len(log.lines)
            k = min(self._scroll_pad_count, n)
            if k > 0:
                del log.lines[n - k :]
                try:
                    log._line_cache.clear()
                except Exception:
                    pass
                try:
                    log.virtual_size = Size(
                        getattr(log, "_widest_line_width", 0) or 0,
                        len(log.lines),
                    )
                except Exception:
                    pass
        except Exception:
            pass
        self._scroll_pad_count = 0

    def _apply_scroll_pad(self) -> None:
        """Ensure enough blank rows after content so user turn can sit at top."""
        if not self._user_pin_active:
            return
        self._clear_scroll_pad()
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        vh = self._msg_viewport_height()
        # Rows from pinned user message to current end.
        after = max(0, len(log.lines) - self._user_pin_start)
        # Need ~full viewport of space under the pin line so it can rest at top.
        need = max(0, vh - after)
        if need <= 0:
            return
        w = self._msg_log_width()
        # One write for all pad rows — this runs after every streamed write,
        # and N separate write() calls each re-render / refresh the log.
        from rich.text import Text

        log.write(
            Text("\n" * (need - 1)) if need > 1 else Text(""),
            width=w,
            expand=False,
            shrink=False,
            scroll_end=False,
            animate=False,
        )
        self._scroll_pad_count = need

    def _scroll_line_to_top(self, line_index: int) -> None:
        """Scroll #msg-log so ``line_index`` is at the top of the viewport."""
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            y = max(0, int(line_index))
            # Prefer scroll_to / scroll_y (Textual versions vary).
            try:
                log.auto_scroll = False
            except Exception:
                pass
            self._pin_scroll_guard = True
            try:
                try:
                    log.scroll_to(y=y, animate=False, immediate=True)
                except TypeError:
                    try:
                        log.scroll_to(y=y, animate=False)
                    except Exception:
                        log.scroll_y = float(y)
                except Exception:
                    try:
                        log.scroll_y = float(y)
                    except Exception:
                        pass
                try:
                    log.scroll_target_y = float(y)
                except Exception:
                    pass
            finally:
                # Clear guard on next frame so wheel events can mark unfollow.
                try:
                    self.set_timer(0.05, self._clear_pin_scroll_guard)
                except Exception:
                    self._pin_scroll_guard = False
            try:
                log.refresh()
            except Exception:
                pass
        except Exception:
            pass

    def _clear_pin_scroll_guard(self) -> None:
        self._pin_scroll_guard = False

    def _on_msg_log_user_scrolled(self, y: float) -> None:
        """Wheel/trackpad: sticky header + Grok follow engage/disengage."""
        # Contacts-style sticky user prompt while browsing any turn.
        self._update_sticky_user_prompt(y)
        if self._pin_scroll_guard or not self._user_pin_active:
            return
        pin = float(self._user_pin_start)
        # Scrolled above the pinned user line → free browse.
        if y < pin - 0.5:
            self._user_pin_follow = False
            self._follow_preserve_scroll = False
            return
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            max_y = float(log.max_scroll_y or 0)
            tail = self._follow_max_scroll_y()
        except Exception:
            max_y = 0.0
            tail = pin
        # Mid-stream: leave the auto-follow path → stop yanking.
        if self._user_pin_follow:
            expected = (
                pin if self._follow_preserve_scroll else tail
            )
            if abs(float(y) - expected) > 3.5:
                self._user_pin_follow = False
                self._follow_preserve_scroll = False
            return
        # Re-arm follow (Grok: near pin top, or overscroll / near bottom).
        if y <= pin + 2.0:
            self._user_pin_follow = True
            # Short page-flip again only if the turn still fits.
            try:
                vh = max(1, self._msg_viewport_height())
                turn_h = max(0, self._content_end_line() - int(self._user_pin_start))
                self._follow_preserve_scroll = turn_h <= max(1, vh - 1)
            except Exception:
                self._follow_preserve_scroll = True
        elif max_y > 0 and y >= max_y - 1.5:
            # Overscroll / at absolute bottom → track tail.
            self._user_pin_follow = True
            self._follow_preserve_scroll = False
        elif abs(float(y) - tail) <= 2.0:
            self._user_pin_follow = True
            self._follow_preserve_scroll = False

    def _begin_user_pin(self, start_line: int) -> None:
        """Grok Build: pin user prompt to top + follow with page-flip preserve.

        Mirrors ``scroll_to_entry_top`` + ``enable_follow_with_preserve``:
        the prompt stays at the top while the reply fills the screen; once
        content overflows, follow switches to the tail automatically.
        """
        self._user_pin_active = True
        self._user_pin_follow = True
        self._follow_preserve_scroll = True
        self._user_pin_start = max(0, int(start_line))
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            log.auto_scroll = False
        except Exception:
            pass
        self._apply_scroll_pad()
        # Page-flip pin: always the prompt top on begin (not the tail).
        self._scroll_line_to_top(self._user_pin_start)
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            self._update_sticky_user_prompt(float(log.scroll_y))
        except Exception:
            self._update_sticky_user_prompt(float(self._user_pin_start))

    def _scroll_msg_log_to_end(self) -> None:
        """Bring the latest rows into view (short slash results, no pin)."""
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            try:
                log.auto_scroll = False
            except Exception:
                pass
            max_y = float(log.max_scroll_y or 0)
            if max_y <= 0:
                return
            self._pin_scroll_guard = True
            try:
                try:
                    log.scroll_to(y=max_y, animate=False, immediate=True)
                except TypeError:
                    try:
                        log.scroll_to(y=max_y, animate=False)
                    except Exception:
                        log.scroll_y = max_y
                try:
                    log.scroll_target_y = max_y
                except Exception:
                    pass
            finally:
                try:
                    self.set_timer(0.05, self._clear_pin_scroll_guard)
                except Exception:
                    self._pin_scroll_guard = False
            try:
                log.refresh()
            except Exception:
                pass
            self._update_sticky_user_prompt(max_y)
        except Exception:
            pass

    @staticmethod
    def _slash_should_pin(cmd_tok: str, raw: str) -> bool:
        """Long-running slash → pin user row to top so live steps stay visible.

        Matches free-form chat (Grok): new command at top, process grows below.
        Instant slash (/status, /template list, …) scrolls to end instead.
        """
        c = (cmd_tok or "").strip().lower()
        if c in ("continue", "c", "resume"):
            return True
        # /template register …  ·  multi-step live timeline
        if c in ("template", "tpl", "t"):
            body = (raw or "").strip()
            if body.startswith("/"):
                body = body[1:].strip()
            # drop command token
            parts = body.split(None, 1)
            rest = parts[1].lower() if len(parts) > 1 else ""
            if rest.startswith("register") or rest.startswith("register-fast"):
                return True
            if rest == "fast" or rest.startswith("fast "):
                return True
        return False

    def _pin_latest_user_section(self) -> None:
        """Re-arm top-pin on the most recent user band (e.g. /new → start run)."""
        if self._restoring_history:
            return
        if not self._user_sections:
            self._scroll_msg_log_to_end()
            return
        try:
            start = int(self._user_sections[-1].start)
        except Exception:
            self._scroll_msg_log_to_end()
            return
        self._begin_user_pin(start)

    def _end_user_pin(self, *, keep_pad: bool = True) -> None:
        """Stop forcing pin-follow after the agent turn finishes.

        Grok: short answers stay top-aligned under the user prompt — blank
        space remains above the input. Do **not** ``scroll_end`` or strip pad
        (that pulls the last line against the composer).
        """
        if not self._user_pin_active and self._scroll_pad_count <= 0:
            return
        was_pinned = self._user_pin_active
        pin = int(self._user_pin_start)
        follow = bool(self._user_pin_follow)
        if not keep_pad:
            self._clear_scroll_pad()
        # keep_pad=True: leave trailing blank rows so the turn stays top-aligned.
        self._user_pin_active = False
        self._user_pin_follow = False
        self._follow_preserve_scroll = False
        self._user_pin_start = 0
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            # Stay free-scroll; auto_scroll would yank short content to the bar.
            log.auto_scroll = False
        except Exception:
            pass
        if keep_pad and was_pinned:
            # Ending the turn collapses the 1-row activity strip, growing the
            # log viewport — the pad written during the turn is now one row
            # short and the view would clamp one line off the pinned prompt.
            # Re-pad (and re-align while still following) after layout settles.
            def _final_align() -> None:
                if self._user_pin_active:
                    return  # a new turn re-pinned meanwhile — leave it alone
                self._user_pin_active = True
                self._user_pin_start = pin
                try:
                    self._apply_scroll_pad()
                    if follow:
                        self._scroll_line_to_top(int(self._pin_follow_scroll_y()))
                finally:
                    self._user_pin_active = False
                    self._user_pin_start = 0

            try:
                self.call_after_refresh(_final_align)
            except Exception:
                _final_align()
        self._update_sticky_user_prompt()

    def _register_user_section(
        self, start: int, count: int, text: str, when: Any = None
    ) -> None:
        """Record a user prompt band as a sticky section header."""
        sec = UserPromptSection(
            start=max(0, int(start)),
            count=max(1, int(count)),
            text=text or "",
            when=when,
        )
        self._user_sections.append(sec)
        self._update_sticky_user_prompt()

    def _clear_user_sections(self) -> None:
        self._user_sections.clear()
        self._sticky_section_key = None
        try:
            sticky = self.query_one("#sticky-user", ChromeStatic)
            sticky.update("")
            sticky.remove_class("-active")
        except Exception:
            pass

    def _resolve_sticky_user_section(self, scroll_y: float) -> UserPromptSection | None:
        """Which user prompt should stick (contacts-style section header).

        Pick the last section whose start is at or above the viewport top
        (``start <= scroll_y``). Show the overlay only once that natural band
        has scrolled *off* the top (``scroll_y > start``), so we never double
        the header. When the next user prompt reaches the top, it becomes the
        new sticky section (pushing the previous one away).
        """
        if not self._user_sections:
            return None
        y = float(scroll_y)
        active: UserPromptSection | None = None
        for sec in self._user_sections:
            if float(sec.start) <= y + 1e-6:
                active = sec
            else:
                break
        if active is None:
            return None
        # Natural band still sits at the top of the viewport → no overlay.
        if y <= float(active.start) + 0.01:
            return None
        return active

    def _update_sticky_user_prompt(self, scroll_y: float | None = None) -> None:
        """Paint or hide the sticky user-prompt strip (Grok / contacts style)."""
        from room_tui.llm.msg_layout import user_prompt_renderable

        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            sticky = self.query_one("#sticky-user", ChromeStatic)
        except Exception:
            return
        if scroll_y is None:
            try:
                scroll_y = float(log.scroll_y)
            except Exception:
                scroll_y = 0.0
        # Drop sections that fell off a truncated log.
        try:
            n = len(log.lines)
            self._user_sections = [
                s for s in self._user_sections if 0 <= s.start < n
            ]
        except Exception:
            pass

        sec = self._resolve_sticky_user_section(scroll_y)
        w = self._msg_log_width()
        key = (
            None
            if sec is None
            else f"{sec.start}:{sec.count}:{hash(sec.text) & 0xFFFF}:{w}"
        )
        if key == self._sticky_section_key:
            return
        self._sticky_section_key = key
        if sec is None:
            try:
                sticky.update("")
                sticky.remove_class("-active")
            except Exception:
                pass
            return
        try:
            sticky.update(
                user_prompt_renderable(
                    sec.text,
                    width=w,
                    when=sec.when,
                    show_timestamp=True,
                    vpad=1,
                )
            )
            sticky.add_class("-active")
        except Exception:
            try:
                sticky.update(f"❯ {sec.text}")
                sticky.add_class("-active")
            except Exception:
                pass

    def _log_line_count(self) -> int:
        try:
            return len(self.query_one("#msg-log", SmoothRichLog).lines)
        except Exception:
            return 0

    def _alloc_expand_id(self) -> str:
        self._expand_seq += 1
        return f"xb{self._expand_seq}"

    def _write_foldable_band(
        self,
        *,
        kind: str,
        content: str,
        path: str = "",
        language: str = "text",
        width: int | None = None,
        render_body: Callable[[bool], Any],
        total_lines: int,
    ) -> None:
        """Write body (+ expand footer when foldable) and register for toggle.

        Grok: truncated by default; footer ``▸ show N more`` / ``▾ collapse``.
        """
        from room_tui.llm.message_render import (
            expand_footer_markup,
            needs_fold,
        )

        w = width or self._msg_log_width() or 80
        fold = needs_fold(total_lines)
        bid = self._alloc_expand_id() if fold else ""
        start = self._log_line_count()
        # Body: collapsed by default when foldable.
        body = render_body(not fold)
        if body is not None:
            self._write_renderable(body, scroll_end=True)
        if fold and bid:
            self._write(
                expand_footer_markup(
                    expanded=False, total_lines=total_lines, block_id=bid
                )
            )
            count = self._log_line_count() - start
            self._expandables.append(
                ExpandableBlock(
                    id=bid,
                    kind=kind,
                    content=content,
                    path=path or "",
                    language=language or "text",
                    expanded=False,
                    start=start,
                    count=max(0, count),
                    width=w,
                )
            )

    @staticmethod
    def _sidebar_hotkey_label() -> str:
        """Platform-native modifier for sidebar toggle (Cmd on macOS, Ctrl elsewhere)."""
        import sys

        if sys.platform == "darwin":
            return "⌘B"
        return "Ctrl+B"

    def action_toggle_sidebar(self) -> None:
        """Collapse / expand the task sidebar (进度 + 大纲)."""
        self._set_sidebar_collapsed(not self._sidebar_collapsed)

    def _set_sidebar_collapsed(
        self,
        collapsed: bool,
        *,
        persist: bool = True,
        announce: bool = True,
    ) -> None:
        """Apply sidebar collapsed state; reflow main column; optional persist."""
        self._sidebar_collapsed = bool(collapsed)
        try:
            sb = self.query_one("#sidebar", Vertical)
        except Exception:
            return
        hk = self._sidebar_hotkey_label()
        if self._sidebar_collapsed:
            sb.add_class("-collapsed")
            if announce:
                self._show_footer_hint(f"侧栏已收起 · {hk} 展开", seconds=1.4)
        else:
            sb.remove_class("-collapsed")
            if announce:
                self._show_footer_hint(f"侧栏已展开 · {hk} 收起", seconds=1.2)
        if persist:
            try:
                app: "RoomApp" = self.app  # type: ignore[assignment]
                from room_tui.config import save_config

                app.cfg.sidebar_collapsed = self._sidebar_collapsed
                save_config(app.cfg)
            except Exception:
                pass
        # Keep footer state label in sync when not showing a transient hint
        if not announce and not self._footer_hint:
            try:
                self._paint_footer()
            except Exception:
                pass
        # Force Horizontal #body to recompute 1fr messages + rail widths.
        for sel in ("#sidebar", "#messages", "#body", "#msg-log"):
            try:
                self.query_one(sel).refresh(layout=True)
            except Exception:
                pass
        try:
            self.refresh(layout=True)
        except Exception:
            pass
        # After styles apply, reflow *all* painted bands (history + sticky).
        # Multiple ticks: content_size often lags one+ frames after CSS class flip.
        # force=True: also reflow while Working/chat (user toggles sidebar mid-run).
        self.call_after_refresh(lambda: self._after_column_layout_change(force=True))
        self.set_timer(0.05, lambda: self._after_column_layout_change(force=True))
        self.set_timer(0.12, lambda: self._after_column_layout_change(force=True))
        self.set_timer(0.22, lambda: self._force_msg_log_reflow(force=True))
        self.set_timer(0.4, lambda: self._force_msg_log_reflow(force=True))

    def _force_msg_log_reflow(self, *, force: bool = True) -> None:
        """Sidebar toggle: rebuild scrollback at current column width."""
        try:
            # Prefer geometry-based width (does not lag after CSS class flip)
            new_w = self._expected_msg_log_width()
            measured = self._msg_log_width()
            # Take the value consistent with collapsed state intent
            if self._sidebar_collapsed:
                new_w = max(new_w, measured)
            else:
                # Expanding: prefer smaller expected if measured still wide
                new_w = min(new_w, measured) if measured >= 20 else new_w
                new_w = max(new_w, self._expected_msg_log_width())
        except Exception:
            return
        if new_w < 20:
            return
        self._msg_log_layout_width = new_w
        self._reflow_msg_log_for_width(new_w, force=force)

    def _after_column_layout_change(self, *, force: bool = False) -> None:
        """Sidebar/resize finished — adapt sticky header and message wrap width."""
        self._sticky_section_key = None
        try:
            self._update_sticky_user_prompt()
        except Exception:
            pass
        try:
            self._render_steps()
        except Exception:
            pass
        try:
            new_w = self._expected_msg_log_width()
            m = self._msg_log_width()
            if self._sidebar_collapsed:
                new_w = max(new_w, m)
            else:
                new_w = max(20, self._expected_msg_log_width())
        except Exception:
            return
        if new_w < 20:
            return
        old_w = int(self._msg_log_layout_width or 0)
        if force or not old_w or abs(new_w - old_w) >= 2:
            self._msg_log_layout_width = new_w
            self._reflow_msg_log_for_width(new_w, force=force)
        else:
            self._msg_log_layout_width = new_w
            try:
                self._sticky_section_key = None
                self._update_sticky_user_prompt()
            except Exception:
                pass

    def _reflow_msg_log_for_width(self, new_w: int, *, force: bool = False) -> None:
        """Rebuild scrollback at *new_w* so bands fill the message column.

        *force*: reflow even while Agent/doc-gen is live (sidebar toggle mid-run).
        Live step label is restored after rebuild so Working… continues.
        """
        import time as _time

        new_w = max(20, int(new_w or 0))
        # Debounce identical rebuilds (multiple timers fire after sidebar toggle)
        if (
            not force
            and getattr(self, "_reflow_last_w", 0) == new_w
            and getattr(self, "_reflow_last_ts", 0.0)
            and (_time.monotonic() - float(self._reflow_last_ts)) < 0.25
        ):
            return
        # Soft debounce for force too (0.12s) so 4 timers don't thrash
        if (
            force
            and getattr(self, "_reflow_last_w", 0) == new_w
            and getattr(self, "_reflow_last_ts", 0.0)
            and (_time.monotonic() - float(self._reflow_last_ts)) < 0.12
        ):
            return

        live_busy = False
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            live_busy = bool(
                self._chat_busy
                or app.orch.state.running
                or self._live_step_active
                or self._is_agent_block_live()
            )
        except Exception:
            live_busy = bool(
                self._chat_busy
                or self._live_step_active
                or self._is_agent_block_live()
            )

        if live_busy and not force:
            # Remember to reflow when idle (e.g. mid-run collapse without force)
            self._pending_reflow_w = new_w
            self._msg_log_layout_width = new_w
            for rec in self._expandables:
                rec.width = new_w
            try:
                self._sticky_section_key = None
                self._update_sticky_user_prompt()
            except Exception:
                pass
            return

        # Snapshot live chrome to restore after rebuild
        saved_live = None
        if live_busy and force:
            saved_live = {
                "text": self._live_step_text,
                "key": self._live_step_key,
                "phase": self._live_step_phase,
                "t0": self._live_step_t0,
                "active": self._live_step_active,
            }

        root = self._ws_root
        if root is None:
            return
        try:
            history = Workspace(root).read_chat_history()
        except Exception:
            return
        if not history:
            for rec in self._expandables:
                rec.width = new_w
            self._msg_log_layout_width = new_w
            if saved_live and saved_live.get("active") and saved_live.get("text"):
                try:
                    self._turn_step(
                        str(saved_live["text"]),
                        key=str(saved_live.get("key") or "run"),
                    )
                except Exception:
                    pass
            return

        # Preserve relative scroll position.
        scroll_ratio = 0.0
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            max_y = float(log.max_scroll_y or 0)
            if max_y > 0:
                scroll_ratio = float(log.scroll_y) / max_y
        except Exception:
            log = None  # type: ignore[assignment]

        self._end_user_pin(keep_pad=False)
        self._clear_live_step()
        self._expandables.clear()
        self._clear_user_sections()
        self._sticky_section_key = None
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            log.clear()
            log.auto_scroll = False
        except Exception:
            return

        # Pin width so every _turn_user / assistant write uses new_w
        self._msg_log_layout_width = new_w
        prev = self._restoring_history
        self._restoring_history = True
        try:
            for row in history:
                self._replay_history_entry(
                    str(row.get("role") or "system"),
                    str(row.get("text") or ""),
                    blocks=row.get("blocks")
                    if isinstance(row.get("blocks"), list)
                    else None,
                    ts=str(row.get("ts") or ""),
                )
        finally:
            self._restoring_history = prev

        # Restore live Working row after history rebuild
        if saved_live and saved_live.get("text"):
            try:
                self._live_step_phase = str(saved_live.get("phase") or "")
                self._live_step_t0 = float(saved_live.get("t0") or 0) or __import__(
                    "time"
                ).monotonic()
                self._turn_step(
                    str(saved_live["text"]),
                    key=str(saved_live.get("key") or "run"),
                )
                self._ensure_spin_timer()
            except Exception:
                pass

        try:
            log = self.query_one("#msg-log", SmoothRichLog)

            def _restore_scroll() -> None:
                try:
                    lg = self.query_one("#msg-log", SmoothRichLog)
                    max_y2 = float(lg.max_scroll_y or 0)
                    target = scroll_ratio * max_y2 if max_y2 > 0 else 0.0
                    lg.scroll_to(y=target, animate=False)
                    # Keep auto_scroll off while run is live so pin/live grows naturally
                    live_now = False
                    try:
                        app2: "RoomApp" = self.app  # type: ignore[assignment]
                        live_now = bool(
                            self._chat_busy or app2.orch.state.running
                        )
                    except Exception:
                        live_now = self._chat_busy
                    lg.auto_scroll = not live_now
                    self._sticky_section_key = None
                    self._update_sticky_user_prompt()
                except Exception:
                    pass

            self.call_after_refresh(_restore_scroll)
            self.set_timer(0.05, _restore_scroll)
        except Exception:
            pass

        self._reflow_last_w = new_w
        self._reflow_last_ts = _time.monotonic()
        self._msg_log_layout_width = new_w
        self._pending_reflow_w = 0

    def action_toggle_expand(self) -> None:
        """Toggle the nearest foldable band (Grok expand/collapse)."""
        # Don't steal keys while typing in the prompt.
        try:
            focused = self.focused
            if focused is not None and (
                isinstance(focused, PromptField)
                or getattr(focused, "id", None) in ("prompt", "composer")
            ):
                return
        except Exception:
            pass
        if not self._expandables:
            return
        # Prefer block under viewport center; else last foldable.
        target = self._expandables[-1]
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            # Rough: last expandable whose start is above current scroll bottom.
            y = int(log.scroll_y + log.size.height // 2)
            for rec in reversed(self._expandables):
                if rec.start <= y:
                    target = rec
                    break
        except Exception:
            pass
        self._toggle_expandable(target.id)

    def _band_renderable_and_footer(
        self, rec: ExpandableBlock, *, expanded: bool, width: int
    ) -> tuple[Any | None, str, int]:
        """Build body renderable + footer markup + total line count for *rec*."""
        from room_tui.llm.message_render import (
            READ_FIRST_LINES,
            READ_LAST_LINES,
            READ_MAX_CHARS,
            _bash_stdout_lines,
            _short,
            expand_footer_markup,
            render_fenced_code,
            render_read_body,
        )
        from room_tui.llm.msg_layout import COLOR_BG_CODE, paint_output_band

        body: Any | None = None
        if rec.kind == "read":
            body = render_read_body(
                rec.content, rec.path, expanded=expanded, width=width
            )
        elif rec.kind == "bash":
            if expanded:
                text = _short(rec.content, READ_MAX_CHARS)
                lines = text.splitlines() or [""]
            else:
                lines = _bash_stdout_lines(rec.content)
            body = paint_output_band(lines, width=width, bg=COLOR_BG_CODE)
        elif rec.kind == "code":
            code = rec.content
            if not expanded:
                ls = code.splitlines() or [""]
                if len(ls) > READ_FIRST_LINES + READ_LAST_LINES:
                    code = "\n".join(
                        ls[:READ_FIRST_LINES] + ["…"] + ls[-READ_LAST_LINES:]
                    )
            body = render_fenced_code(code, rec.language, width=width)
        total = rec.content.count("\n") + (1 if rec.content.strip() else 0)
        footer = expand_footer_markup(
            expanded=expanded, total_lines=total, block_id=rec.id, width=width
        )
        return body, footer, total

    def _renderable_to_strips(self, renderable: Any, width: int) -> list[Any]:
        """Render a Rich renderable to a list of fixed-width Strips (offline)."""
        try:
            import io

            from rich.console import Console
            from textual.strip import Strip

            console = Console(
                file=io.StringIO(),
                width=max(8, width),
                force_terminal=True,
                color_system="truecolor",
                highlight=False,
            )
            options = console.options.update(width=max(8, width))
            # pad=True so full-width code bands match initial RichLog.write paint.
            lines = console.render_lines(renderable, options, pad=True)
            out: list[Any] = []
            for segs in lines:
                strip = Strip(list(segs))
                try:
                    if strip.cell_length < width:
                        strip = strip.extend_cell_length(width)
                    elif strip.cell_length > width:
                        strip = strip.crop(0, width)
                except Exception:
                    pass
                out.append(strip)
            return out
        except Exception:
            return []

    def _toggle_expandable(self, block_id: str) -> None:
        """Replace a foldable band in-place (no tail delete → no full-list flash).

        Must **not** scroll the message list — preserve the user's viewport so
        Expand/Collapse does not jump to the bottom.
        """
        rec = next((x for x in self._expandables if x.id == block_id), None)
        if rec is None:
            return
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        # Drop hover without restoring (band is about to be replaced).
        self._expand_hover_id = None
        self._expand_hover_start = -1
        self._expand_hover_count = 0
        self._expand_hover_cache = None

        try:
            saved_y = float(log.scroll_y)
        except Exception:
            saved_y = 0.0
        try:
            log.auto_scroll = False
        except Exception:
            pass

        n = len(log.lines)
        if rec.start < 0 or rec.start >= n:
            return
        end = min(n, rec.start + max(1, rec.count))
        old_count = end - rec.start
        if old_count <= 0:
            return

        new_expanded = not rec.expanded
        w = rec.width or self._msg_log_width() or 80
        body, footer_markup, _total = self._band_renderable_and_footer(
            rec, expanded=new_expanded, width=w
        )

        # Offline render → single in-place splice. Never delete the tail first
        # (that blanked the whole conversation for a frame).
        new_strips: list[Any] = []
        if body is not None:
            new_strips.extend(self._renderable_to_strips(body, w))
        foot = self._markup_to_strip(footer_markup, w)
        if foot is not None:
            new_strips.append(foot)
        if not new_strips:
            return

        log.lines[rec.start:end] = new_strips
        rec.expanded = new_expanded
        new_count = len(new_strips)
        delta = new_count - old_count
        rec.count = new_count
        if delta:
            for other in self._expandables:
                if other is not rec and other.start >= end:
                    other.start += delta
            for sec in self._user_sections:
                if sec.start >= end:
                    sec.start += delta
            self._sticky_section_key = None
        try:
            log._line_cache.clear()
        except Exception:
            pass
        try:
            log.virtual_size = Size(
                getattr(log, "_widest_line_width", 0) or 0,
                len(log.lines),
            )
        except Exception:
            pass
        try:
            max_y = float(log.max_scroll_y)
            y = max(0.0, min(saved_y, max_y))
            try:
                log.scroll_to(y=y, animate=False, immediate=True)
            except TypeError:
                try:
                    log.scroll_to(y=y, animate=False)
                except Exception:
                    log.scroll_y = y
            try:
                log.scroll_target_y = y
            except Exception:
                pass
            log.refresh()
        except Exception:
            try:
                log.refresh()
            except Exception:
                pass
        self._update_sticky_user_prompt()

    def _strip_plain(self, line_obj: Any) -> str:
        """Best-effort plain text from a RichLog line/Strip."""
        plain = getattr(line_obj, "text", None)
        if isinstance(plain, str):
            return plain
        try:
            # textual.strip.Strip
            if hasattr(line_obj, "render"):
                segs = list(line_obj)
                return "".join(getattr(s, "text", "") for s in segs)
        except Exception:
            pass
        try:
            return "".join(getattr(s, "text", str(s)) for s in line_obj)
        except Exception:
            return str(line_obj)

    @on(events.Click, "#sidebar-rail")
    def _on_sidebar_rail_click(self, event: events.Click) -> None:
        """Legacy rail click (rail is hidden when collapsed; expand if reachable)."""
        event.stop()
        self._set_sidebar_collapsed(False)

    @on(events.Click, "#steps-tab")
    @on(events.Click, "#chapters-tab")
    def _on_sidebar_tab_click(self, event: events.Click) -> None:
        """Click tab ‹ — collapse task sidebar (message area gains width)."""
        # Only treat click on the dim ‹ region as collapse? Whole tab is fine.
        event.stop()
        self._set_sidebar_collapsed(True)

    # Widgets allowed to participate in drag-select / copy (conversation only).
    _SELECTABLE_IDS = frozenset({"msg-log", "cmd-input"})

    def _is_conversation_selectable(self, widget: Any) -> bool:
        """True if *widget* may keep a text selection (not sidebar chrome)."""
        if widget is None:
            return False
        wid = getattr(widget, "id", None)
        if wid in self._SELECTABLE_IDS:
            return True
        # Named types in case id is missing after a remount.
        return isinstance(widget, (SmoothRichLog, PromptField))

    def _filter_conversation_selections(
        self, selections: dict[Any, Any]
    ) -> dict[Any, Any]:
        """Drop non-conversation widgets from a selections map."""
        return {
            w: s
            for w, s in selections.items()
            if self._is_conversation_selectable(w)
        }

    def _watch__select_state(self, select_state: Any) -> None:
        """Build selections, but never include the task sidebar / chrome.

        Textual's default multi-line path uses full-width Y-bands on the common
        ancestor and ``_apply_content_selections`` re-stamps the drag endpoint
        even when that leaf has ``ALLOW_SELECT = False``. Clamp here so chrome
        never enters ``self.selections`` (avoids a paint frame of rail highlight).
        """
        from textual.selection import SELECT_ALL, Selection

        if select_state is None:
            self._selecting = False
            self.refresh()
            return
        self._selecting = True
        if select_state.end is None:
            return
        if not select_state.is_attached_to_dom:
            self._select_state = None
            return

        if select_state.is_single_content_widget:
            start_index, end_offset = select_state.content_offsets
            widget = select_state.start.content_widget
            if widget is not None and self._is_conversation_selectable(widget):
                self.selections = {
                    widget: Selection.from_offsets(
                        start_index,
                        end_offset + (1, 0),
                    )
                }
            else:
                # Started on chrome (shouldn't) — clear rather than paint the rail.
                self.selections = {}
            return

        selections: dict[Any, Any] = {
            widget: SELECT_ALL
            for widget in select_state._walk_selected_widgets()
            if self._is_conversation_selectable(widget)
        }
        # May re-add non-selectable endpoints — strip after.
        try:
            select_state._apply_content_selections(selections)
        except Exception:
            pass
        self.selections = self._filter_conversation_selections(selections)

    def _watch_selections(
        self,
        old_selections: dict[Any, Any],
        selections: dict[Any, Any],
    ) -> None:
        """Sync filter + notify (must NOT be async — async is deferred via call_next).

        Order matters: ``text_selection`` reads ``screen.selections`` on refresh.
        Assign the filtered map *before* any ``selection_updated`` → refresh.
        """
        filtered = self._filter_conversation_selections(selections)
        if filtered != selections:
            # Replace first; re-enters this watcher with filtered == selections.
            self.selections = filtered
            return
        for widget in set(old_selections) | set(selections):
            try:
                widget.selection_updated(selections.get(widget, None))
            except Exception:
                pass

    def _msg_log_abs_y(self, event: events.MouseEvent) -> int | None:
        """Map a mouse event to an absolute line index in #msg-log.lines."""
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return None
        try:
            # Prefer content-area offset (excludes padding/border) when available.
            try:
                co = event.get_content_offset(log)
            except Exception:
                co = None
            if co is not None:
                y = int(co.y) + int(getattr(log, "scroll_y", 0) or 0)
            else:
                y = int(event.y + getattr(log, "scroll_y", 0))
                try:
                    pad_top = int(log.styles.padding.top or 0)
                except Exception:
                    pad_top = 1
                y = max(0, y - pad_top)
            if y < 0 or y >= len(log.lines):
                return None
            return y
        except Exception:
            return None

    def _expand_target_at_y(self, y: int) -> str | None:
        """Block id when absolute line *y* sits in a foldable band (body + footer).

        Hit target is the **whole** collapsed/expanded code band (not only the
        ▸ Expand footer row) so double-click anywhere on the block toggles.
        """
        for rec in reversed(self._expandables):
            n = max(int(rec.count or 0), 0)
            if n <= 0:
                continue
            if rec.start <= y < rec.start + n:
                return rec.id
        return None

    @staticmethod
    def _expand_footer_y(rec: ExpandableBlock) -> int:
        """Absolute line index of the Expand/Collapse footer strip for *rec*."""
        return rec.start + max(int(rec.count or 1), 1) - 1

    @staticmethod
    def _expand_code_band_x0(rec: ExpandableBlock) -> int:
        """Cell/column index where the hover **body block** begins.

        Read layout: ``[indent][line-no gutter][ source on md_code_bg … ]``.
        The body block **includes the line-number gutter** (user-visible unit
        is numbers + source). Only process indent / rail is left out.

        Bash/code bands: from process indent (no separate gutter column).
        """
        from room_tui.llm.msg_layout import MSG_INDENT

        # Always start after process indent — include line numbers for read.
        return MSG_INDENT

    def _clear_expand_hover(self) -> None:
        """Restore body + footer strips after mouse leaves the foldable band."""
        cache = self._expand_hover_cache
        start = self._expand_hover_start
        count = self._expand_hover_count
        self._expand_hover_id = None
        self._expand_hover_start = -1
        self._expand_hover_count = 0
        self._expand_hover_cache = None
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        try:
            log.styles.pointer = "default"
        except Exception:
            pass
        if not cache or start < 0 or count <= 0:
            return
        try:
            n = len(log.lines)
            for i, strip in enumerate(cache):
                idx = start + i
                if 0 <= idx < n:
                    log.lines[idx] = strip
            try:
                log._line_cache.clear()
            except Exception:
                pass
            log.refresh()
        except Exception:
            pass

    def _set_expand_hover(self, y: int) -> None:
        """Quiet Grok hover: uniform soft lift on **body block** + footer.

        Body: one bgcolor wash from process indent through line numbers and
        source (the whole read/code rectangle). Process rail/indent left of
        the block is untouched. Footer: same soft floor + brighter labels.
        """
        bid = self._expand_target_at_y(y)
        if not bid:
            self._clear_expand_hover()
            return
        if bid == self._expand_hover_id:
            try:
                log = self.query_one("#msg-log", SmoothRichLog)
                log.styles.pointer = "pointer"
            except Exception:
                pass
            return
        rec = next((x for x in self._expandables if x.id == bid), None)
        if rec is None:
            self._clear_expand_hover()
            return
        self._clear_expand_hover()
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        start = int(rec.start)
        count = max(int(rec.count or 0), 0)
        if count <= 0 or start < 0 or start >= len(log.lines):
            return
        end = min(len(log.lines), start + count)
        count = end - start
        if count <= 0:
            return

        from rich.style import Style
        from room_tui.llm.message_render import (
            EXPAND_HOVER_SOFT_BG,
            expand_footer_markup,
        )

        originals = list(log.lines[start:end])
        self._expand_hover_id = bid
        self._expand_hover_start = start
        self._expand_hover_count = count
        self._expand_hover_cache = originals

        footer_y = start + count - 1
        # Body block: skip process indent only; include line-number gutter.
        band_x0 = self._expand_code_band_x0(rec)
        body_wash = Style(bgcolor=EXPAND_HOVER_SOFT_BG)
        for i in range(start, footer_y):
            try:
                strip = log.lines[i]
                try:
                    cell_end = int(strip.cell_length)
                except Exception:
                    cell_end = 10_000
                if band_x0 >= cell_end:
                    continue
                log.lines[i] = SmoothRichLog._stylize_strip_range(
                    strip, band_x0, cell_end, body_wash
                )
            except Exception:
                pass

        total = rec.content.count("\n") + (1 if rec.content.strip() else 0)
        w = rec.width or self._msg_log_width() or 80
        hover_strip = self._markup_to_strip(
            expand_footer_markup(
                expanded=rec.expanded,
                total_lines=total,
                block_id=rec.id,
                hover=True,
                width=w,
            ),
            w,
        )
        if hover_strip is not None:
            log.lines[footer_y] = hover_strip
        try:
            log._line_cache.clear()
        except Exception:
            pass
        try:
            log.styles.pointer = "pointer"
        except Exception:
            pass
        try:
            log.refresh()
        except Exception:
            pass

    def _markup_to_strip(self, markup: str, width: int) -> Any | None:
        """Render one markup row to a fixed-width Strip (for in-place hover)."""
        try:
            import io

            from rich.console import Console
            from rich.text import Text
            from textual.strip import Strip

            text = Text.from_markup(markup)
            console = Console(
                file=io.StringIO(),
                width=max(8, width),
                force_terminal=True,
                color_system="truecolor",
                highlight=False,
            )
            options = console.options.update(width=max(8, width))
            lines = console.render_lines(text, options, pad=True)
            if not lines:
                return None
            strip = Strip(list(lines[0]))
            # Ensure full cell width so the hover bar spans the column.
            try:
                if strip.cell_length < width:
                    strip = strip.extend_cell_length(width)
                elif strip.cell_length > width:
                    strip = strip.crop(0, width)
            except Exception:
                pass
            return strip
        except Exception:
            return None

    def _rewrite_log_line(self, y: int, markup: str) -> None:
        """Replace one log strip with markup (hover fallback / control chrome)."""
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        if y < 0 or y >= len(log.lines):
            return
        w = self._msg_log_width()
        strip = self._markup_to_strip(markup, w)
        if strip is not None:
            log.lines[y] = strip
            try:
                log._line_cache.clear()
            except Exception:
                pass
            try:
                log.refresh()
            except Exception:
                pass
            return
        # Legacy del/write path if Strip render unavailable.
        try:
            saved_y = float(log.scroll_y)
        except Exception:
            saved_y = 0.0
        try:
            log.auto_scroll = False
        except Exception:
            pass
        tail = list(log.lines[y + 1 :])
        del log.lines[y:]
        try:
            log._line_cache.clear()
        except Exception:
            pass
        before = len(log.lines)
        log.write(
            markup,
            width=w,
            expand=False,
            shrink=False,
            scroll_end=False,
            animate=False,
        )
        # Keep exactly one new strip + tail (preserve band line counts).
        new_lines = log.lines[before:]
        del log.lines[before:]
        if new_lines:
            log.lines.append(new_lines[0])
        log.lines.extend(tail)
        try:
            log._line_cache.clear()
            log.virtual_size = Size(
                getattr(log, "_widest_line_width", 0) or 0,
                len(log.lines),
            )
            log.scroll_to(y=saved_y, animate=False, immediate=True)
        except Exception:
            try:
                log.scroll_y = saved_y
            except Exception:
                pass
        try:
            log.refresh()
        except Exception:
            pass

    def _activate_expand_at_event(self, event: events.MouseEvent) -> bool:
        """If pointer is on a foldable band, toggle and clear selection.

        True when handled. Caller must gate on double-click (chain ≥ 2) so
        single-click drag-select on the body is not stolen.
        """
        y = self._msg_log_abs_y(event)
        if y is None:
            return False
        bid = self._expand_target_at_y(y)
        if not bid:
            return False
        # Double-click may SELECT_WORD / SELECT_ALL — clear so we fold, not copy.
        try:
            self.clear_selection()
        except Exception:
            try:
                self.selections = {}
            except Exception:
                pass
        # Toggle splices the band; drop hover without restore (avoids double paint).
        self._expand_hover_id = None
        self._expand_hover_start = -1
        self._expand_hover_count = 0
        self._expand_hover_cache = None
        self._toggle_expandable(bid)
        try:
            event.stop()
            event.prevent_default()
        except Exception:
            pass
        return True

    def _update_expand_hover_from_event(self, event: events.MouseEvent) -> None:
        """Shared hover path for MouseMove (and Enter) over #msg-log."""
        y = self._msg_log_abs_y(event)
        if y is not None:
            self._msg_log_last_abs_y = y
        # Only skip hover while the user is actively drag-selecting.
        if self._msg_log_mouse_down is not None and self._msg_log_did_drag:
            return
        if y is None:
            self._clear_expand_hover()
            return
        if self._expand_target_at_y(y):
            self._set_expand_hover(y)
        else:
            self._clear_expand_hover()

    @on(events.MouseDown, "#msg-log")
    def _on_msg_log_mouse_down(self, event: events.MouseDown) -> None:
        """Track press origin for drag-vs-click; do not clear selection here.

        Foldable bands use the **whole** body as the double-click hit target.
        Clearing selection on mouse-down would break single-click drag-select
        over code. Selection is cleared only when a double-click activates
        expand/collapse.
        """
        self._msg_log_mouse_down = (int(event.screen_x), int(event.screen_y))
        self._msg_log_did_drag = False
        y = self._msg_log_abs_y(event)
        self._msg_log_last_abs_y = y if y is not None else -1

    @on(events.MouseMove, "#msg-log")
    def _on_msg_log_mouse_move(self, event: events.MouseMove) -> None:
        if self._msg_log_mouse_down is not None:
            dx = abs(int(event.screen_x) - self._msg_log_mouse_down[0])
            dy = abs(int(event.screen_y) - self._msg_log_mouse_down[1])
            if dx > 2 or dy > 2:
                self._msg_log_did_drag = True
                # Dragging: drop footer hover so selection paint is clean.
                if self._expand_hover_id is not None:
                    self._clear_expand_hover()
                return
        self._update_expand_hover_from_event(event)

    @on(events.MouseUp, "#msg-log")
    def _on_msg_log_mouse_up(self, event: events.MouseUp) -> None:
        # Do not clear _msg_log_did_drag / mouse_down here — Click consumes them
        # next to distinguish drag-select vs double-click expand. Hover updates
        # on the following MouseMove (or after Click clears the drag flag).
        if not self._msg_log_did_drag:
            self._update_expand_hover_from_event(event)

    @on(events.Leave, "#msg-log")
    def _on_msg_log_leave(self, event: events.Leave) -> None:
        self._clear_expand_hover()

    @on(events.Enter, "#msg-log")
    def _on_msg_log_enter(self, event: events.Enter) -> None:
        # Enter has no line coords in some Textual versions — MouseMove will paint.
        pass

    @on(events.TextSelected)
    def _on_text_selected(self, event: events.TextSelected) -> None:
        """Grok: releasing a drag-selection copies text to the system clipboard."""
        # Expand-control clicks must not trigger "copied all" noise.
        if self._msg_log_did_drag is False and self._msg_log_mouse_down is not None:
            # Still resolving click — skip auto-copy if it was expand.
            pass
        try:
            text = self.get_selected_text()
        except Exception:
            text = None
        if not text or not str(text).strip():
            return
        # Double-click on a foldable band selects a word then our Click(chain≥2)
        # expands — suppress the spurious "已复制" toast for that word-select.
        # Only short single-line selections (typical of dbl-click), not drag copies.
        try:
            raw_chk = str(text)
            if (
                self._msg_log_did_drag is False
                and "\n" not in raw_chk
                and len(raw_chk.strip()) < 64
            ):
                ly = int(getattr(self, "_msg_log_last_abs_y", -1) or -1)
                if ly >= 0 and self._expand_target_at_y(ly):
                    return
        except Exception:
            pass
        # Suppress copy toast when the "selection" is a single expand footer.
        try:
            if "Expand" in str(text) or "Collapse" in str(text):
                if str(text).count("\n") <= 1 and (
                    "more lines" in str(text)
                    or "double-click" in str(text)
                    or "click or" in str(text)
                ):
                    return
        except Exception:
            pass
        raw = str(text)
        preview = raw.replace("\n", " ").strip()
        if len(preview) > 36:
            preview = preview[:35] + "…"
        try:
            app = self.app
            # Prefer OS pasteboard (pbcopy/…); Textual OSC 52 alone fails on
            # macOS Terminal and reports success while the clipboard stays empty.
            if hasattr(app, "copy_text_to_clipboard"):
                ok = bool(app.copy_text_to_clipboard(raw))  # type: ignore[attr-defined]
            else:
                app.copy_to_clipboard(raw)
                ok = True
            if ok:
                self._show_footer_hint(f"已复制 · {preview}", seconds=1.0)
            else:
                self._show_footer_hint(
                    "复制失败 · 系统剪贴板不可用", seconds=1.6
                )
        except Exception:
            try:
                self._show_footer_hint("复制失败", seconds=1.2)
            except Exception:
                pass

    @on(events.Click, "#msg-log")
    def _on_msg_log_click(self, event: events.Click) -> None:
        """Expand/Collapse: double-click anywhere on the foldable band.

        Single-click / drag-select still select and copy body text (Grok).
        Double-click on the **whole** band (body + ▸ Expand footer) toggles
        fold — not only the thin footer row — and does not fight single-click.
        Keyboard: press ``e`` (see action_toggle_expand).

        Note: primary double-click expand is handled in
        ``SmoothRichLog._on_click`` (before Textual SELECT_ALL). This handler
        is a fallback when the click bubbles without being stopped.
        """
        chain = int(getattr(event, "chain", 1) or 1)

        # Double-click expand wins: ignore drag micro-jitter and clear residue.
        if chain >= 2:
            self._msg_log_did_drag = False
            self._msg_log_mouse_down = None
            if self._activate_expand_at_event(event):
                return
            return

        # Drag-select on the log must not toggle fold (single-click path).
        if self._msg_log_did_drag:
            self._msg_log_did_drag = False
            self._msg_log_mouse_down = None
            return
        if self._msg_log_mouse_down is not None:
            dx = abs(int(event.screen_x) - self._msg_log_mouse_down[0])
            dy = abs(int(event.screen_y) - self._msg_log_mouse_down[1])
            self._msg_log_mouse_down = None
            if dx > 2 or dy > 2:
                return

        # Active selection on body → leave alone (TextSelected already auto-copied).
        try:
            sel = self.get_selected_text()
            if sel and sel.strip():
                return
        except Exception:
            pass

    def _msg(self, text: str, *, error: bool = False) -> None:
        """App-facing helper: post a system line into the message list."""
        plain = text
        # strip simple brackets used by older callers
        if plain.startswith("[提示]"):
            plain = plain.removeprefix("[提示]").strip()
        if plain.startswith("·"):
            plain = plain.lstrip("·").strip()
        self._turn_system(plain, error=error)

    def _persist_chat(
        self,
        role: str,
        text: str = "",
        *,
        blocks: list[dict[str, Any]] | None = None,
    ) -> None:
        """Append one durable UI message (skip while replaying history).

        Always writes under the active workspace ``.pd/tui/chat-history.jsonl``
        so slash-command results (template list/register, /status, step finals)
        survive quit/re-entry and sidebar reflow rebuilds.
        """
        if self._restoring_history:
            return
        body = (text or "").rstrip()
        bl = list(blocks or [])
        if not body and not bl:
            return
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
            if root is None:
                return
            root = Path(root).resolve()
            # Keep _ws_root aligned so reflow/read hit the same file we write.
            if self._ws_root is None:
                self._ws_root = root
            Workspace(root).append_chat_message(role, body, blocks=bl or None)
        except Exception:
            # Never break the UI on disk errors; best-effort only.
            pass

    def _parse_when(self, when: Any = None) -> Any:
        from datetime import datetime

        if when is None:
            return datetime.now().astimezone()
        if isinstance(when, str) and when:
            try:
                return datetime.fromisoformat(when.replace("Z", "+00:00"))
            except Exception:
                return datetime.now().astimezone()
        return when

    def _turn_user(
        self, text: str, *, when: Any = None, pin_to_top: bool = False
    ) -> None:
        """User turn — Grok UserPrompt: full-width band + ``❯ `` + right time.

        *pin_to_top*: after submit, pin this message to the top of the viewport
        (Grok Build: new prompt at top, empty space below while waiting).
        """
        from room_tui.llm.msg_layout import user_prompt_renderable

        self._commit_live_step()
        body = (text or "").rstrip()
        self._persist_chat("user", body)
        stamp = self._parse_when(when)
        # Band has inner vpad=1; margin-bottom 1 is a blank row *outside* the band
        # (no bg) so the next process/answer row breathes.
        w = self._msg_log_width()
        # New submit: drop any leftover breath pad so history is contiguous.
        if pin_to_top:
            self._end_user_pin(keep_pad=False)
        start = self._log_line_count()
        # Bypass pin hooks for the user band itself (write raw).
        log = self.query_one("#msg-log", SmoothRichLog)
        log.write(
            user_prompt_renderable(
                body,
                width=w,
                when=stamp,
                show_timestamp=True,
                vpad=1,
            ),
            width=w,
            expand=False,
            shrink=False,
            scroll_end=False,
            animate=False,
        )
        log.write(
            "",
            width=w,
            expand=False,
            shrink=False,
            scroll_end=False,
            animate=False,
        )
        # Register section for sticky header (history restore + live turns).
        self._register_user_section(
            start, self._log_line_count() - start, body, when=stamp
        )
        if pin_to_top and not self._restoring_history:
            self._begin_user_pin(start)

    def _ensure_block_gap(self) -> None:
        """One blank row before a *non-timeline* block if last content is non-empty.

        Grok process timeline (Thought for Xs / tools / Thinking header) is
        **tight** — never insert blanks between those chrome lines. Use this
        only for final answer body (and similar prose) after process chrome.

        Ignores trailing user-pin scroll-pad blanks so pad is not mistaken for a gap.
        """
        if self._scroll_pad_count > 0:
            self._clear_scroll_pad()
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            if not log.lines:
                return
            if self._strip_plain(log.lines[-1]).strip():
                self._write("")
        except Exception:
            self._write("")

    def _trim_trailing_blank_lines(self, *, max_trim: int = 8) -> None:
        """Remove trailing blank content rows (not pin-pad) so the process timeline stays tight.

        Clears pin pad first, then drops up to *max_trim* empty strips at the end.
        """
        if self._scroll_pad_count > 0:
            self._clear_scroll_pad()
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        trimmed = 0
        while log.lines and trimmed < max_trim:
            if self._strip_plain(log.lines[-1]).strip():
                break
            del log.lines[-1]
            trimmed += 1
        if trimmed:
            try:
                log._line_cache.clear()
            except Exception:
                pass
            try:
                log.virtual_size = Size(
                    getattr(log, "_widest_line_width", 0) or 0,
                    len(log.lines),
                )
            except Exception:
                pass

    def _turn_assistant(self, text: str, *, when: Any = None) -> None:
        """Agent message — plain body + right timestamp (Grok AgentMessage)."""
        from room_tui.llm.agent_blocks import classify_plain_text
        from room_tui.llm.message_render import render_block
        from room_tui.llm.msg_layout import (
            assistant_first_line_renderable,
            assistant_plain_markup,
            indent_renderable,
        )

        self._commit_live_step()
        body = (text or "").rstrip()
        if not body:
            return
        self._persist_chat("assistant", body)
        # Breath under process chrome (Thought / tools) before answer.
        self._ensure_block_gap()
        stamp = self._parse_when(when)
        w = self._msg_log_width()
        blocks = classify_plain_text(body)
        any_out = False
        first_text_done = False
        prev_kind = ""
        for b in blocks:
            r = render_block(b, width=w)
            if r is None:
                continue
            # Grok: gap before code/diff/json bands (and after prior prose).
            if any_out and (
                b.kind in ("code", "diff", "json")
                or prev_kind in ("code", "diff", "json")
            ):
                self._write("")
            any_out = True
            try:
                if b.kind in ("code", "diff", "json"):
                    from room_tui.llm.message_render import (
                        READ_FIRST_LINES,
                        READ_LAST_LINES,
                        needs_fold,
                        render_fenced_code,
                    )

                    code = b.text or ""
                    nlines = code.count("\n") + (1 if code.strip() else 0)
                    if b.kind == "code" and needs_fold(nlines):
                        lang = b.language or "text"

                        def _code_body(expanded: bool, _code=code, _lang=lang):
                            text = _code
                            if not expanded:
                                ls = _code.splitlines() or [""]
                                text = "\n".join(
                                    ls[:READ_FIRST_LINES]
                                    + ["…"]
                                    + ls[-READ_LAST_LINES:]
                                )
                            return render_fenced_code(text, _lang, width=w)

                        self._write_foldable_band(
                            kind="code",
                            content=code,
                            language=lang,
                            width=w,
                            total_lines=nlines,
                            render_body=_code_body,
                        )
                    else:
                        # render_block already returns a painted full-width band.
                        self._write_renderable(r)
                elif b.kind in ("text", "markdown", "plain") and not first_text_done:
                    # First prose block carries the right-side timestamp.
                    first_text_done = True
                    self._write_renderable(
                        assistant_first_line_renderable(
                            b.text or body,
                            width=w,
                            when=stamp,
                            show_timestamp=True,
                        )
                    )
                else:
                    self._write_renderable(indent_renderable(r))
            except Exception:
                for line in assistant_plain_markup(str(r)):
                    self._write(line)
            prev_kind = b.kind
        if not any_out:
            self._write_renderable(
                assistant_first_line_renderable(
                    body, width=w, when=stamp, show_timestamp=True
                )
            )
        # Margin under assistant prose (Grok: breath before next user/tool turn).
        self._write("")

    def _turn_agent(self, result: Any, *, skip_streamed_tools: bool = True) -> None:
        """Render Pi Agent turn body (answer / extra blocks).

        Grok Build model: process rows (Thought for Xs, tools) stay in the
        scrollback after the turn. Live-streamed tools/thinking are not
        re-drawn; only the final answer body is appended. Full structure is
        persisted so re-entry restores process + answer.
        """
        from room_tui.llm.agent_blocks import AgentTurn, classify_plain_text

        if self._live_step_active and self._is_agent_tool_live():
            self._agent_finish_tool_row(
                self._agent_live_tool or "tool",
                is_error=False,
                args=self._agent_live_args,
            )
        if self._live_step_active and self._is_agent_thinking_live():
            self._agent_finish_thinking()
        # Drop live streamed prose — polished answer is painted below
        # (keeps markdown/tables; stream was the progressive preview).
        streamed_answer = bool(self._agent_answer_buf) or self._is_agent_answer_live()
        if streamed_answer:
            # Final paint of stream buffer (no cursor) then replace with polish.
            if self._is_agent_answer_live() and self._agent_answer_buf.strip():
                try:
                    self._paint_live_answer()
                except Exception:
                    pass
            self._agent_discard_live_answer()
        elif self._live_step_active:
            self._freeze_live_step()

        turn = getattr(result, "agent_turn", None)
        if not isinstance(turn, AgentTurn) or not turn.blocks:
            try:
                raw = result.response_file.read_text(encoding="utf-8").strip()
            except Exception:
                raw = getattr(result, "peek", "") or ""
            self._turn_assistant(raw)
            return

        # Durable Grok-like turn: Thought + tools + final text (for re-entry).
        hist_blocks = turn.to_history_blocks(
            thought_elapsed_s=self._agent_last_thought_s,
            collapse_thinking=True,
        )
        persist_text = turn.assistant_text() or turn.raw_text or ""
        if hist_blocks or persist_text.strip():
            self._persist_chat(
                "assistant",
                persist_text.strip(),
                blocks=hist_blocks or None,
            )

        # Live UI: tools/thinking already on screen — only paint answer body.
        blocks = [
            b
            for b in turn.blocks
            if not (b.kind == "thinking" and not b.text.strip())
        ]
        if skip_streamed_tools:
            # Grok: process rows stay; do not re-dump thinking body or tools.
            blocks = [b for b in blocks if b.kind not in ("tool", "thinking", "thought")]
        expanded = []
        for b in blocks:
            # Split fenced code out of any prose kind — never paint fences via Markdown.
            if (
                b.kind in ("text", "markdown", "plain")
                and b.text
                and "```" in b.text
            ):
                expanded.extend(classify_plain_text(b.text) or [b])
            else:
                expanded.append(b)
        from room_tui.llm.message_render import (
            READ_FIRST_LINES,
            READ_LAST_LINES,
            needs_fold,
            render_block,
            render_fenced_code,
        )
        from room_tui.llm.msg_layout import indent_renderable

        ww = self._msg_log_width() or 80
        any_body = False
        prev_kind = ""
        # Grok: blank row between last process chrome and answer prose.
        if expanded:
            self._ensure_block_gap()
        for b in expanded:
            if any_body and (
                b.kind in ("code", "diff", "json")
                or prev_kind in ("code", "diff", "json")
            ):
                self._write("")
            any_body = True
            try:
                # Code/diff/json: full-width Grok band (already content-indented).
                if b.kind == "code":
                    code = b.text or ""
                    lang = b.language or "text"
                    nlines = code.count("\n") + (1 if code.strip() else 0)
                    if needs_fold(nlines):

                        def _code_body(
                            expanded: bool, _code=code, _lang=lang, _w=ww
                        ):
                            text = _code
                            if not expanded:
                                ls = _code.splitlines() or [""]
                                text = "\n".join(
                                    ls[:READ_FIRST_LINES]
                                    + ["…"]
                                    + ls[-READ_LAST_LINES:]
                                )
                            return render_fenced_code(text, _lang, width=_w)

                        self._write_foldable_band(
                            kind="code",
                            content=code,
                            language=lang,
                            width=ww,
                            total_lines=nlines,
                            render_body=_code_body,
                        )
                    else:
                        self._write_renderable(
                            render_fenced_code(code, lang, width=ww)
                        )
                elif b.kind in ("diff", "json"):
                    r = render_block(b, width=ww)
                    if r is not None:
                        self._write_renderable(r)
                else:
                    r = render_block(b, width=ww)
                    if r is not None:
                        # Prose only — never wrap a code band in indent_renderable.
                        self._write_renderable(indent_renderable(r))
            except Exception:
                self._write(str(b.text or "").replace("[", "\\["))
            prev_kind = b.kind
        if not any_body:
            text = turn.assistant_text() or turn.raw_text
            if text:
                prev = self._restoring_history
                self._restoring_history = True
                try:
                    self._turn_assistant(text)
                finally:
                    self._restoring_history = prev
            return
        self._write("")

    def _turn_system(
        self, text: str, *, error: bool = False, persist: bool = True
    ) -> None:
        """System meta — quiet content-column line(s).

        *persist*: set False for ephemeral chrome (startup tips) so restarts
        do not re-dump status into the scrollback.
        """
        from room_tui.llm.msg_layout import system_markup

        self._commit_live_step()
        body = (text or "").rstrip()
        if body and persist and not self._is_chrome_history_noise(body):
            self._persist_chat("error" if error else "system", body)
        self._write(system_markup(body, error=error))
        self._write("")

    def _replay_history_entry(
        self,
        role: str,
        text: str = "",
        *,
        blocks: list[dict[str, Any]] | None = None,
        ts: str = "",
    ) -> None:
        """Paint one stored message without re-persisting.

        Grok-like: assistant rows may include structured process blocks
        (Thought for Xs, tools, final text) so re-entry matches live scrollback.
        """
        if self._is_chrome_history_noise(text) and not blocks:
            return
        prev = self._restoring_history
        self._restoring_history = True
        try:
            if role == "user":
                self._turn_user(text, when=ts or None)
            elif role == "assistant":
                if blocks:
                    self._replay_assistant_blocks(blocks, fallback_text=text)
                elif text:
                    self._turn_assistant(text)
            elif role == "error":
                self._turn_system(text, error=True)
            else:
                if text:
                    # Multi-line notices (template register / list / new) → rail
                    # Single-line step finals (完成 / 失败 …) → system row
                    body = text.strip()
                    if "\n" in body:
                        self._append_notice_block(
                            *body.splitlines(), persist=False
                        )
                    elif body.startswith(("完成", "失败", "✓", "×", "✗")):
                        # Timeline step result — quiet system line
                        self._turn_system(
                            body,
                            error=body.startswith(("失败", "×", "✗")),
                        )
                    else:
                        self._turn_system(text)
        finally:
            self._restoring_history = prev

    def _replay_assistant_blocks(
        self, blocks: list[dict[str, Any]], *, fallback_text: str = ""
    ) -> None:
        """Re-render a durable assistant turn (process + answer)."""
        from room_tui.llm.agent_blocks import AgentBlock, classify_plain_text

        parsed: list[AgentBlock] = []
        for raw in blocks:
            if not isinstance(raw, dict):
                continue
            try:
                b = AgentBlock.from_dict(raw)
            except Exception:
                continue
            if (
                b.kind in ("text", "markdown", "plain")
                and b.text
                and "```" in b.text
            ):
                parsed.extend(classify_plain_text(b.text) or [b])
            else:
                parsed.append(b)
        if not parsed:
            if fallback_text:
                self._turn_assistant(fallback_text)
            return
        from room_tui.llm.message_render import (
            format_tool_header_markup,
            is_read_tool,
            read_line_count,
            render_block,
            sanitize_read_output,
        )
        from room_tui.llm.msg_layout import indent_renderable
        from room_tui.llm.message_render import extract_output

        ww = self._msg_log_width() or 80
        any_out = False
        saw_process = False
        answer_gap_done = False
        process_kinds = {"thought", "thinking", "tool"}
        answer_kinds = {"text", "markdown", "plain", "code", "diff", "json"}
        for b in parsed:
            # Breath between process chrome and final answer (Grok).
            if (
                saw_process
                and not answer_gap_done
                and b.kind in answer_kinds
            ):
                self._ensure_block_gap()
                answer_gap_done = True
            # Tools: same live paint path so fold/expand footer is registered.
            if b.kind == "tool":
                any_out = True
                saw_process = True
                try:
                    out = extract_output(b.tool_result).rstrip("\n")
                    if is_read_tool(b.tool_name or ""):
                        out = sanitize_read_output(out)
                    n = (
                        read_line_count(out)
                        if is_read_tool(b.tool_name or "")
                        else None
                    )
                    header = format_tool_header_markup(
                        b.tool_name or "tool",
                        b.tool_args,
                        is_error=bool(b.is_error),
                        line_count=n or None,
                        empty=is_read_tool(b.tool_name or "") and not out,
                    )
                    self._write(header)
                    self._paint_tool_result_body(
                        b.tool_name or "tool",
                        b.tool_args,
                        b.tool_result,
                        is_error=bool(b.is_error),
                    )
                except Exception:
                    r = render_block(b, width=ww)
                    if r is not None:
                        self._write_renderable(r)
                continue
            r = render_block(b, width=ww)
            if r is None:
                continue
            any_out = True
            if b.kind in process_kinds:
                saw_process = True
            try:
                if b.kind in ("thought", "thinking", "code", "diff", "json"):
                    self._write_renderable(r)
                else:
                    self._write_renderable(indent_renderable(r))
            except Exception:
                self._write(str(r).replace("[", "\\["))
        if not any_out and fallback_text:
            self._turn_assistant(fallback_text)
            return
        self._write("")

    @staticmethod
    def _is_chrome_history_noise(text: str) -> bool:
        """Skip *auto* launch chrome only — never hide user-triggered results.

        User-triggered rows (template register success, /status, /template list,
        /new 开始生成, step finals) MUST survive quit/re-entry. Only filter
        tips that Room injects on every cold start without a matching user turn.
        """
        t = (text or "").strip()
        if not t:
            return True
        # Explicit history separator (if ever written)
        if t.startswith("── 以上为历史消息"):
            return True
        # Cold-start incomplete-task banner (re-derived from manifest each launch)
        if t.startswith("未完成  ") or t.startswith("未完成\t"):
            return True
        # One-shot onboarding / welcome
        if "这里是 Room 工程间" in t:
            return True
        # Auto model-setup nag on empty install (not /status)
        if t.startswith("⚠ ") and ("未配置模型" in t or "不认识模型" in t):
            return True
        if "按 Ctrl+M 打开选择器" in t and "若列表为空" in t:
            return True
        # Env bootstrap tip (persist=False normally; belt-and-suspenders)
        if "终端自检: room doctor" in t and "claude0" in t:
            return True
        return False

    @staticmethod
    def _task_title(manifest: RunManifest) -> str:
        title = (manifest.title or "").strip()
        if not title and manifest.inputs:
            try:
                title = Path(manifest.inputs[0]).stem
            except Exception:
                title = ""
        if not title:
            title = (manifest.template_id or "未命名任务").strip()
        return title

    @staticmethod
    def _task_phase_cn(manifest: RunManifest) -> str:
        phase = (manifest.phase or "").strip().lower()
        status = (manifest.status or "").strip().lower()
        if status == "complete" or phase in ("complete", "done"):
            return "已完成"
        if status == "failed":
            return "失败"
        if status in ("cancelled", "canceled") or phase in ("cancelled", "canceled"):
            return "已取消"
        return (
            PHASE_CN.get(phase)
            or PHASE_CN.get(status)
            or (phase or status or "进行中")
        )

    @staticmethod
    def _is_incomplete_task(manifest: RunManifest | None) -> bool:
        """True when this project has a document run the user may still care about.

        Shows for created/running/paused/failed/cancelled — only pure complete is hidden.
        """
        if manifest is None:
            return False
        if not str(manifest.session_id or "").strip():
            return False
        status = (manifest.status or "").strip().lower()
        phase = (manifest.phase or "").strip().lower()
        if status == "complete" or phase in ("complete", "done"):
            return False
        # Explicit non-success outcomes still get the resume notice.
        if status in (
            "created",
            "running",
            "paused",
            "failed",
            "cancelled",
            "canceled",
        ):
            return True
        # Unknown status with a session → show (safer than silent hide).
        return True

    # Soft notice block — barely above msg bg (#141414), no border.
    # Pale-yellow rail + muted label text (entrance tips / incomplete task / template meta).
    _TASK_BLOCK_BG = "#1a1a1a"
    _TASK_BLOCK_RAIL = "#C9B87A"  # pale yellow accent bar
    _TASK_BLOCK_FG = COLOR_MSG_LABEL  # soft muted (not bright white)

    @staticmethod
    def _task_block_lines(
        manifest: RunManifest, *, resumable: bool = True
    ) -> tuple[str, str]:
        """≤2 plain lines for the incomplete-task notice block."""
        title = ShellScreen._task_title(manifest)
        phase_cn = ShellScreen._task_phase_cn(manifest)
        progress = (manifest.progress or "").strip()
        status = phase_cn + (f" {progress}" if progress else "")

        out_name = ""
        if manifest.output:
            try:
                out_name = Path(manifest.output).name or ""
            except Exception:
                out_name = ""

        tpl = (manifest.template_id or "").strip()

        def _clip(s: str, n: int) -> str:
            s = (s or "").strip()
            return s if len(s) <= n else s[: n - 1] + "…"

        title = _clip(title, 22)
        status = _clip(status, 16)
        tpl = _clip(tpl, 16)
        out_name = _clip(out_name, 18)

        # L1: identity + status; L2: template/output + action.
        # Prefer human title (input stem) so the block is recognizable.
        if not title or title == (manifest.template_id or "").strip():
            if manifest.inputs:
                try:
                    title = _clip(Path(manifest.inputs[0]).stem, 22)
                except Exception:
                    pass
        if not resumable:
            line1 = f"上次生成无法继续  {title or '文档任务'}"
            line2 = "没有可恢复的进行中任务  ·  请 /new 开始文档生成"
            return line1, line2
        line1 = f"未完成  {title or '文档任务'}  ·  {status}"
        tail: list[str] = []
        if tpl:
            tail.append(tpl)
        if out_name:
            tail.append(out_name)
        st = (manifest.status or "").strip().lower()
        if st in ("failed",):
            tail.append("/continue 重试")
        else:
            tail.append("/continue 继续")
        line2 = "  ·  ".join(tail) if tail else "/continue 继续"
        return line1, line2

    def _append_notice_block(self, *lines: str, persist: bool = True) -> None:
        """Quiet tip block: pale-yellow ``│`` + soft fill + muted text (not assistant).

        *persist* (default True): write to chat-history.jsonl so re-entry shows
        template register results / errors. Use ``persist=False`` for one-shot
        chrome (incomplete-task tip on every launch, ephemeral status).
        """
        from rich.padding import Padding
        from rich.table import Table
        from rich.text import Text

        cleaned = [(ln or "").rstrip() for ln in lines if (ln or "").strip() or ln == ""]
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()
        if not cleaned:
            return
        if len(cleaned) > 12:
            head = cleaned[:10]
            head.append(f"  … +{len(cleaned) - 10} 行")
            cleaned = head

        self._commit_live_step()
        body_txt = "\n".join(cleaned)
        if persist and not self._restoring_history:
            # Durable: survive quit / re-open (user + notice pair stays intact)
            self._persist_chat("system", body_txt)
        fg = self._TASK_BLOCK_FG
        bg = self._TASK_BLOCK_BG
        body = Text(body_txt, style=f"{fg} on {bg}")
        block = Padding(body, (0, 1), style=f"on {bg}")
        n = max(1, body_txt.count("\n") + 1)
        rail = Text(
            "\n".join([self._MSG_RAIL] * n),
            style=self._TASK_BLOCK_RAIL,
        )
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(width=1, no_wrap=True, vertical="middle")
        grid.add_column(ratio=1)
        grid.add_row(rail, block)
        self._write_renderable(grid)
        self._write("")

    def _manifest_resumable(self, manifest: RunManifest) -> bool:
        """False when the bound template is gone — cannot /continue, need /new."""
        tid = (manifest.template_id or "").strip()
        if not tid:
            return True
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            return bool(app.engine.template_exists(tid))
        except Exception:
            return True

    def _append_incomplete_task_block(
        self, manifest: RunManifest, *, resumable: bool | None = None
    ) -> None:
        """Append incomplete-task notice (pale-yellow rail block, not persisted)."""
        ok = self._manifest_resumable(manifest) if resumable is None else resumable
        line1, line2 = self._task_block_lines(manifest, resumable=ok)
        # Ephemeral: re-shown from manifest on each launch, not history
        self._append_notice_block(line1, line2, persist=False)

    @staticmethod
    def _task_summary_text(manifest: RunManifest) -> str:
        """Plain-text task summary (tests). Single-line compress."""
        a, b = ShellScreen._task_block_lines(manifest)
        return f"{a}  |  {b}"

    # Bottom activity bar: braille arc (Grok status strip).
    _SPIN = tuple("⠋⠙⠹⠸⠼⠴⠦⠧")
    # Live message chrome — Grok Thinking/tool accent (screenshot parity):
    #   continuous light vertical ``│`` (U+2502) + diamond ``◆`` (U+25C6)
    #   Thinking: gray │ spans header + breath + body as one block
    #   Tools: green/red │ (kept after finish)
    #   brightness wave on live rows; label itself never animates
    _MSG_RAIL = "\u2502"  # │ continuous Grok accent bar
    _MSG_DIAMOND = "\u25c6"  # ◆ filled diamond bullet
    _WAVE_SPEED = 0.15  # radians/tick — EntryRenderer::WAVE_SPEED
    _WAVE_ROWS = 32  # appearance.animation.wave_rows default
    _SPIN_INTERVAL = 1.0 / 30.0  # AnimationConfig.fps = 30

    def _clear_live_step(self) -> None:
        """Drop open live-step bookkeeping (does not edit the log)."""
        self._live_step_active = False
        self._live_step_strips = 0
        self._live_step_start = -1
        self._live_step_key = ""
        self._live_step_text = ""
        self._live_step_phase = ""
        self._live_step_t0 = 0.0
        self._live_step_elapsed_i = -1
        self._maybe_stop_spin_timer()

    @staticmethod
    def _fmt_elapsed(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        m, s = divmod(seconds, 60)
        if m < 60:
            return f"{m}m{s:02d}s" if s else f"{m}m"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"

    @staticmethod
    def _short_error(err: str, *, max_cells: int = 42, sample_suffix: str = "") -> str:
        """One-line, short error for step rows (no Traceback walls)."""
        from room_tui.engine.errors import humanize_engine_error

        t = humanize_engine_error(err or "", sample_suffix=sample_suffix)
        # Additional product aliases for non-engine paths
        low = t.lower()
        if "资料注册失败" in t or "input register worker failed" in low:
            t = "资料注册失败"
        elif "资料注入失败" in t or "session feed worker failed" in low:
            t = "资料注入失败"
        elif "prompt missing" in low:
            t = "资料注册失败（prompt 缺失）"
        elif "模板不存在" in t or "template not found" in low:
            t = "任务无法恢复 · 请 /new 开始生成"
        elif t.lower().startswith("pi timeout") or t.lower().startswith("room agent timeout"):
            t = "模型超时"
        elif t.lower().startswith("pi exit") or t.lower().startswith("room agent exit"):
            t = "模型进程异常"
        elif t.lower().startswith("pi not found") or "room agent not found" in low:
            t = "Room Agent 不可用"
        t = " ".join(t.split())
        if cell_len(t) <= max_cells:
            return t
        return set_cell_size(t, max(8, max_cells - 1)).rstrip() + "…"

    _PHASE_LABEL = {
        "build_prompt": "准备提示",
        "llm": "模型生成",
        "parse": "解析写入",
        "summarize": "写摘要",
        "summarize_build": "准备摘要",
        "summarize_llm": "生成摘要",
        "summarize_parse": "保存摘要",
        "thinking": "处理中",
        "writing": "模型生成",
        "parsing": "解析写入",
        "waiting": "排队中",
    }

    def _live_display_label(self) -> str:
        """In-list live label: base + optional phase (no timer on Thinking…).

        Grok Build: live header is just ``Thinking…``; duration appears only
        after collapse as ``Thought for Xs``.
        """
        base = self._live_step_text or "生成中"
        if self._is_agent_thinking_live() or (
            self._live_step_key == "agent-thinking"
        ):
            if base.startswith("Thinking"):
                return "Thinking…"
        phase = (self._live_step_phase or "").strip()
        if phase and phase not in base:
            return f"{base}  ·  {phase}"
        return base

    def _commit_live_step(self) -> None:
        """Stop rewriting the open live row; leave its current text in the log.

        If a step was still in-progress (never finalized with ok=True/False),
        persist the last label so quit/reflow does not drop it. Finalized steps
        already wrote via ``_turn_step(..., ok=...)`` and cleared live state.
        """
        if self._is_agent_answer_live():
            # Streamed answer is finalized by _turn_agent / _turn_assistant.
            self._clear_live_step()
            return
        if (
            self._live_step_active
            and not self._restoring_history
            and (self._live_step_text or "").strip()
            and not self._is_agent_thinking_live()
            and not self._is_agent_tool_live()
        ):
            line = self._live_step_text.strip()
            try:
                # Keep as system row; restore paints ✓/完成 lines and plain labels.
                self._persist_chat("system", line)
            except Exception:
                pass
        self._clear_live_step()

    def _ensure_spin_timer(self) -> None:
        if self._spin_timer is None:
            self._spin_timer = self.set_interval(
                self._SPIN_INTERVAL, self._tick_spinner
            )

    def _maybe_stop_spin_timer(self) -> None:
        if self._activity_on or self._live_step_active:
            return
        self._force_stop_spin_timer()

    def _force_stop_spin_timer(self) -> None:
        """Stop the spin interval regardless of live-step bookkeeping."""
        if self._spin_timer is not None:
            try:
                self._spin_timer.stop()
            except Exception:
                pass
            self._spin_timer = None

    @staticmethod
    def _clean_step_label(text: str) -> str:
        """Drop redundant status suffixes; icon carries running/done state."""
        t = (text or "").strip()
        for suffix in (
            " · 进行中…",
            " · 进行中",
            "  ·  进行中…",
            "  ·  进行中",
            " · 收尾…",
            "  ·  收尾…",
        ):
            if t.endswith(suffix):
                t = t[: -len(suffix)].rstrip()
        return t

    @staticmethod
    def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
        h = hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    @classmethod
    def _blend_hex(cls, base: str, original: str, opacity: float) -> str:
        """Grok ``blend_color``: opacity 0 → base, 1 → original."""
        o = max(0.0, min(1.0, float(opacity)))
        br, bg, bb = cls._hex_rgb(base)
        or_, og, ob = cls._hex_rgb(original)
        r = int(br + (or_ - br) * o + 0.5)
        g = int(bg + (og - bg) * o + 0.5)
        b = int(bb + (ob - bb) * o + 0.5)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _wave_brightness(self, *, row: int = 0) -> float:
        """Grok ``theme::wave_brightness`` — sin² traveling wave, 0..1."""
        rows = max(1, self._WAVE_ROWS)
        phase = (row / rows) * 2.0 * math.pi
        t = float(self._spin_i) * self._WAVE_SPEED
        s = math.sin(t + phase)
        return s * s

    def _is_agent_tool_live(self) -> bool:
        return bool(self._live_step_key.startswith("agent-tool:"))

    def _is_agent_thinking_live(self) -> bool:
        """Grok Thinking block live row (not doc-gen, not a tool)."""
        return self._live_step_key in ("agent-thinking", "chat")

    def _is_agent_answer_live(self) -> bool:
        """Live streaming answer prose (between process chrome and final freeze)."""
        return self._live_step_key == "agent-answer"

    def _is_agent_block_live(self) -> bool:
        return (
            self._is_agent_tool_live()
            or self._is_agent_thinking_live()
            or self._is_agent_answer_live()
        )

    def _agent_tool_cmd_label(self, tool: str, args: Any = None) -> str:
        """Grok process-row title (matches finished header)."""
        from room_tui.llm.message_render import (
            bash_process_title,
            edit_process_title,
            extract_command,
            extract_path,
            is_bash_tool,
            is_edit_tool,
            is_read_tool,
            read_process_title,
        )

        tool = (tool or "tool").strip()
        cmd = extract_command(args) if args is not None else ""
        path = extract_path(args) if args is not None else ""
        ltool = tool.lower()
        if is_bash_tool(tool):
            return bash_process_title(cmd or tool)
        if is_read_tool(tool):
            return read_process_title(path or cmd)
        if is_edit_tool(tool):
            return edit_process_title(path, tool=tool)
        if ltool in ("grep", "search", "rg"):
            q = ""
            if isinstance(args, dict):
                q = str(args.get("pattern") or args.get("query") or "")
            return f"Searched 1 pattern" + (f" {q}" if q else "")
        if any(k in ltool for k in ("fetch", "index", "crawl", "scrape", "browse")):
            if "fetch" in ltool and "index" in ltool:
                return "Fetch and index"
            if "fetch" in ltool:
                return "Fetch"
            return tool.replace("_", " ").strip() or tool
        if path:
            return f"{tool} {path}"
        if cmd:
            return f"{tool} {cmd}"
        return tool

    def _format_agent_block_line(self, text: str, ok: bool | None) -> str:
        """Grok process chrome.

        - Tools: green/red ``┃  ◆ title`` (rail kept after finish)
        - Live Thinking: gray animated ``┃  ◆ Thinking…`` (accent_enabled)
        - Thought for Xs: no rail ``   ◆ Thought for Xs``
        """
        from room_tui.llm.msg_layout import (
            COLOR_ACCENT_SUCCESS,
            MSG_ACCENT_W,
            MSG_DIA_GAP,
            MSG_RAIL_GAP,
        )

        label = self._truncate_step_label(self._clean_step_label(text))
        gap = " " * MSG_RAIL_GAP
        post = " " * MSG_DIA_GAP
        # Prefer tool key over label heuristics — never drop ┃ on Run rows.
        is_tool = self._is_agent_tool_live() or str(self._live_step_key).startswith(
            "agent-tool:"
        )
        is_live_thinking = (not is_tool) and self._is_agent_thinking_live()
        # Collapsed Thought for Xs (and non-live Thinking labels).
        is_thought_done = (not is_tool) and (not is_live_thinking) and (
            label.startswith("Thought") or label.startswith("Thinking")
        )

        # ── Live Thinking… (Grok: continuous gray │ + muted header) ──
        if is_live_thinking:
            # Soft pulse; stay near gray_mid so the stacked │ reads as one bar.
            brightness = self._wave_brightness(row=0)
            peak = self._blend_hex(COLOR_MSG_MID, COLOR_MSG_HI, 0.35)
            opacity = 0.72 + 0.28 * brightness
            fade = self._blend_hex(COLOR_MSG_BG, peak, opacity)
            rail = f"[{fade}]{self._MSG_RAIL}[/{fade}]"
            # Diamond slightly brighter than rail (matches Grok screenshot).
            dia_c = self._blend_hex(COLOR_MSG_BG, COLOR_MSG_LABEL, 0.9)
            mark = f"[{dia_c}]{self._MSG_DIAMOND}[/{dia_c}]"
            # header_bright=false
            return (
                f"{rail}{gap}{mark}{post}"
                f"[{COLOR_MSG_LABEL}]{label}[/{COLOR_MSG_LABEL}]"
            )

        # ── Thought for Xs — no rail, dim diamond ──
        if is_thought_done:
            pre = (" " * MSG_ACCENT_W) + gap
            if ok is False:
                return (
                    f"{pre}[{COLOR_ERR}]✗[/{COLOR_ERR}]{post}"
                    f"[{COLOR_ERR}]{label}[/{COLOR_ERR}]"
                )
            return (
                f"{pre}[{COLOR_MSG_DIM}]{self._MSG_DIAMOND}[/{COLOR_MSG_DIM}]{post}"
                f"[{COLOR_MSG_LABEL}]{label}[/{COLOR_MSG_LABEL}]"
            )

        # Tools — always ``❙  ◆ title`` (green/red rail stays after finish).
        if ok is False:
            rail = f"[{COLOR_ERR}]{self._MSG_RAIL}[/{COLOR_ERR}]"
            mark = f"[{COLOR_ERR}]{self._MSG_DIAMOND}[/{COLOR_ERR}]"
            return (
                f"{rail}{gap}{mark}{post}"
                f"[bold {COLOR_ERR}]{label}[/bold {COLOR_ERR}]"
            )
        if ok is True:
            rail = f"[{COLOR_ACCENT_SUCCESS}]{self._MSG_RAIL}[/{COLOR_ACCENT_SUCCESS}]"
            mark = f"[{COLOR_MSG_DIM}]{self._MSG_DIAMOND}[/{COLOR_MSG_DIM}]"
            return (
                f"{rail}{gap}{mark}{post}"
                f"[{COLOR_MSG_LABEL}]{label}[/{COLOR_MSG_LABEL}]"
            )
        # Running tool: pulse rail + diamond, keep ❙ glyph
        brightness = self._wave_brightness(row=0)
        peak = self._blend_hex(COLOR_MSG_MID, COLOR_MSG_HI, 0.45)
        opacity = 0.28 + 0.72 * brightness
        fade = self._blend_hex(COLOR_MSG_BG, peak, opacity)
        # Rail holds success green so the finished look matches after freeze.
        rail = f"[{COLOR_ACCENT_SUCCESS}]{self._MSG_RAIL}[/{COLOR_ACCENT_SUCCESS}]"
        mark = f"[{fade}]{self._MSG_DIAMOND}[/{fade}]"
        return (
            f"{rail}{gap}{mark}{post}"
            f"[bold {COLOR_MSG_LABEL}]{label}[/bold {COLOR_MSG_LABEL}]"
        )

    def _format_step_line(self, text: str, ok: bool | None) -> str:
        """Doc-gen Thinking row: accent rail + diamond/check + label.

        Running (chrome only — label never animates):
        - rail: fixed ``┃``, color = blend(bg, accent, sin²(tick))
        - diamond: fixed ``◆``, same brightness (synced with accent row 0)
        Done / fail: static green/rose ``┃ ✓`` / ``┃ ✗``.

        Agent tool rows use :meth:`_format_agent_tool_line` instead.
        """
        # Agent turn blocks (Thinking / tools): Grok diamond chrome, not doc rail.
        if self._is_agent_block_live() or str(self._live_step_key).startswith(
            "agent-tool:"
        ):
            return self._format_agent_block_line(text, ok)
        if text.strip().startswith(("$ ", "Thinking", "Thought ", "Read ")):
            # Finalized agent lines written via helpers may not still be "live"
            return self._format_agent_block_line(text, ok)

        from room_tui.llm.msg_layout import MSG_DIA_GAP, MSG_RAIL_GAP

        label = self._clean_step_label(text)
        gap = " " * MSG_RAIL_GAP
        post = " " * MSG_DIA_GAP
        if ok is True:
            rail = f"[{COLOR_OK}]{self._MSG_RAIL}[/{COLOR_OK}]"
            mark = f"[{COLOR_OK}]✓[/{COLOR_OK}]"
            return f"{rail}{gap}{mark}{post}[{COLOR_OK}]{label}[/{COLOR_OK}]"
        if ok is False:
            rail = f"[{COLOR_ERR}]{self._MSG_RAIL}[/{COLOR_ERR}]"
            mark = f"[{COLOR_ERR}]✗[/{COLOR_ERR}]"
            return f"{rail}{gap}{mark}{post}[{COLOR_ERR}]{label}[/{COLOR_ERR}]"

        # ThinkingConfig.accent = gray_dim; blend toward bg_base like Grok.
        # Peak slightly lifts gray_dim→gray_bright so the pulse stays readable
        # on single-row live steps (full multi-row wave uses pure gray_dim).
        brightness = self._wave_brightness(row=0)
        peak = self._blend_hex(COLOR_MSG_MID, COLOR_MSG_HI, 0.45)
        # Minimum opacity ≈ dim gutter so trough is still a faint rail.
        opacity = 0.22 + 0.78 * brightness
        fade = self._blend_hex(COLOR_MSG_BG, peak, opacity)
        rail = f"[{fade}]{self._MSG_RAIL}[/{fade}]"
        mark = f"[{fade}]{self._MSG_DIAMOND}[/{fade}]"
        # Header label: theme.muted().bold() — steady, no brightness pulse
        return (
            f"{rail}{gap}{mark}{post}"
            f"[bold {COLOR_MSG_LABEL}]{label}[/bold {COLOR_MSG_LABEL}]"
        )

    def _freeze_live_step(self) -> None:
        """Stop pulsing the live row; leave its last paint in the log as history.

        Agent tools already paint ``❙  ◆ title`` while running — do **not**
        pop/rewrite here (that path used to leave blank-gutter ``   ◆ Run``).
        """
        if not self._live_step_active:
            return
        key = self._live_step_key
        if key.startswith("agent-tool:"):
            tool = (self._agent_live_tool or key.rsplit(":", 1)[-1] or "tool").strip()
            # Keep the last live strip (with green ❙) as the finished row.
            self._agent_streamed_tools.append(f"agent-tool:done:{tool}")
            self._agent_live_tool = ""
            self._agent_live_args = None
        self._clear_live_step()

    def _thinking_body_preview_lines(
        self, text: str, *, max_lines: int = 3, max_chars: int = 1200
    ) -> list[str]:
        """Last N lines of streaming thinking (Grok ``truncate_lines = 3``).

        Hard-reflow to content column so RichLog never soft-wraps (orphans lose
        indent). No leading/trailing blanks — breath row is added by the painter.
        """
        from room_tui.llm.msg_layout import MSG_INDENT

        body = (text or "").replace("\r\n", "\n").strip()
        if not body:
            return []
        if len(body) > max_chars:
            body = "…" + body[-(max_chars - 1) :]
        try:
            w = self._msg_log_width()
        except Exception:
            w = 80
        # Title column under ``❙  ◆ `` / ``   ◆ ``.
        budget = max(12, w - MSG_INDENT)

        raw = [ln.rstrip() for ln in body.split("\n")]
        while raw and not raw[0].strip():
            raw.pop(0)
        while raw and not raw[-1].strip():
            raw.pop()

        visual: list[str] = []
        for para in raw:
            if not para.strip():
                continue  # no mid-body blank rows in the truncated preview
            text = para.lstrip(" ")
            while text:
                if cell_len(text) <= budget:
                    visual.append(text)
                    break
                chunk = set_cell_size(text, budget)
                sp = chunk.rfind(" ")
                if sp >= max(8, budget // 3):
                    take = chunk[:sp]
                    rest = text[len(take) :].lstrip(" ")
                else:
                    take = chunk.rstrip()
                    if not take:
                        take = text[: max(1, budget)]
                        rest = text[len(take) :]
                    else:
                        rest = text[len(take) :].lstrip(" ")
                        if rest == text:
                            rest = text[max(1, len(take)) :]
                visual.append(take.rstrip())
                text = rest

        while visual and not visual[0].strip():
            visual.pop(0)
        while visual and not visual[-1].strip():
            visual.pop()
        if len(visual) > max_lines:
            visual = ["…"] + visual[-max_lines:]
        return visual

    def _paint_live_thinking(self) -> None:
        """Grok Thinking block — continuous left ``│`` accent through the block.

        Screenshot parity::

            │  ◆ Thinking…
            │
            │  summary line…
        """
        if not self._live_step_active and self._live_step_key != "agent-thinking":
            if not self._agent_thinking_buf:
                return
        from rich.markup import render as render_markup
        from rich.style import Style
        from rich.text import Text

        from room_tui.llm.msg_layout import MSG_INDENT

        # Pad then pop so we rewrite the thinking block, not pad+stale body.
        self._prepare_write_under_user_pin()
        if self._live_step_active:
            self._pop_live_step_strips()
        self._live_step_active = True
        self._live_step_key = "agent-thinking"
        if not (self._live_step_text or "").startswith("Thinking"):
            self._live_step_text = "Thinking…"

        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        w = self._msg_log_width()
        before = len(log.lines)
        self._live_step_start = before

        display = self._truncate_step_label(self._live_display_label())
        header_mk = self._format_step_line(display, None)
        preview = self._thinking_body_preview_lines(self._agent_thinking_buf)

        # Steady rail color so stacked │ cells form one continuous bar.
        brightness = self._wave_brightness(row=0)
        peak = self._blend_hex(COLOR_MSG_MID, COLOR_MSG_HI, 0.35)
        opacity = 0.72 + 0.28 * brightness
        rail_hex = self._blend_hex(COLOR_MSG_BG, peak, opacity)
        rail_style = Style(color=rail_hex)
        body_style = Style(color=COLOR_MSG_DIM)

        # One write: header → breath → body; every row starts with │.
        block = Text(no_wrap=True)
        try:
            block.append_text(Text.from_markup(header_mk))
        except Exception:
            try:
                block.append_text(render_markup(header_mk))
            except Exception:
                block.append(str(header_mk))

        def _append_rail_row(content_after_rail: str, *, style: Style) -> None:
            """Append newline + ``│`` + content (content already includes pad)."""
            block.append("\n")
            block.append(self._MSG_RAIL, style=rail_style)
            if content_after_rail:
                plain = content_after_rail
                if cell_len(self._MSG_RAIL + plain) > w:
                    plain = set_cell_size(plain, max(4, w - 1)).rstrip() + "…"
                block.append(plain, style=style)

        if preview:
            # Breath: │ alone (continuous accent, empty content).
            _append_rail_row("", style=body_style)
            # Body: │ + spaces to title column + text.
            gutter = " " * (MSG_INDENT - 1)  # after │ → title column
            for pl in preview:
                text = pl if pl.strip() else " "
                _append_rail_row(gutter + text, style=body_style)

        log.write(
            block,
            width=w,
            expand=False,
            shrink=False,
            scroll_end=False,
            animate=False,
        )

        after = len(log.lines)
        self._live_step_strips = max(1, after - before)
        self._live_step_start = before
        try:
            log._line_cache.clear()
            log.virtual_size = Size(
                getattr(log, "_widest_line_width", 0) or 0,
                len(log.lines),
            )
            log.refresh()
        except Exception:
            pass
        self._refresh_user_pin_after_write()
        self._agent_thinking_dirty = False
        import time as _time

        self._agent_thinking_last_paint = _time.monotonic()
        self._ensure_spin_timer()

    def _open_thinking_live_row(self) -> None:
        """Open live Thinking… with one blank above prior content (Grok card).

        The breath is temporary: when Thinking collapses to ``Thought for Xs``,
        ``_agent_finish_thinking`` trims that blank so the process timeline
        stays tight (Thought/tools flush).
        """
        if self._scroll_pad_count > 0:
            self._clear_scroll_pad()
        need_gap = False
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            need_gap = bool(
                log.lines and self._strip_plain(log.lines[-1]).strip()
            )
        except Exception:
            need_gap = True
        if need_gap:
            self._ensure_block_gap()
        self._thinking_gap_above = need_gap
        self._turn_step("Thinking…", key="agent-thinking")

    def _agent_on_thinking_delta(self, delta: str) -> None:
        """Append streaming thinking tokens and refresh live Thinking body."""
        import time as _time

        d = delta or ""
        if not d:
            return
        # Ensure we are in a Thinking live row (re-open between tool rounds).
        if self._is_agent_tool_live():
            # Thinking should not interleave mid-tool; ignore until tool ends.
            return
        if self._is_agent_answer_live():
            # Answer already streaming — ignore late thinking crumbs.
            return
        if not self._is_agent_thinking_live():
            if self._live_step_active:
                self._freeze_live_step()
            self._agent_think_t0 = _time.monotonic()
            self._agent_thinking_buf = ""
            self._open_thinking_live_row()
        self._agent_thinking_buf += d
        self._agent_thinking_dirty = True
        now = _time.monotonic()
        # Throttle full redraws (~12fps) so huge deltas stay smooth.
        if now - self._agent_thinking_last_paint >= 0.08:
            self._paint_live_thinking()
        self._set_activity(phase="thinking")

    def _paint_live_answer(self) -> None:
        """Rewrite growing answer prose in place (Grok token stream).

        Always replace the previous live region entirely — never append the
        full buffer on top of leftover lines (that caused full-text spam).
        """
        from room_tui.llm.msg_layout import assistant_first_line_renderable

        if not self._agent_answer_buf and not self._is_agent_answer_live():
            return
        # Pad first, then pop previous live body (order matters).
        self._prepare_write_under_user_pin()
        if self._live_step_active and self._is_agent_answer_live():
            self._pop_live_step_strips()
        self._live_step_active = True
        self._live_step_key = "agent-answer"
        self._live_step_text = "answer"

        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        w = self._msg_log_width()
        before = len(log.lines)
        self._live_step_start = before
        body = self._agent_answer_buf
        # Soft cursor while the turn is still open.
        if self._chat_busy and body and not body.endswith("\n"):
            display = body + "▌"
        else:
            display = body or "…"
        try:
            r = assistant_first_line_renderable(
                display, width=w, show_timestamp=False
            )
            log.write(
                r,
                width=w,
                expand=False,
                shrink=False,
                scroll_end=False,
                animate=False,
            )
        except Exception:
            from room_tui.llm.msg_layout import content_pad

            pad = content_pad()
            for line in (display.splitlines() or [""]):
                log.write(
                    f"{pad}{line}",
                    width=w,
                    expand=False,
                    shrink=False,
                    scroll_end=False,
                    animate=False,
                )
        after = len(log.lines)
        self._live_step_strips = max(1, after - before)
        self._live_step_start = before
        try:
            log._line_cache.clear()
            log.virtual_size = Size(
                getattr(log, "_widest_line_width", 0) or 0,
                len(log.lines),
            )
            log.refresh()
        except Exception:
            pass
        self._refresh_user_pin_after_write()
        self._agent_answer_dirty = False
        import time as _time

        self._agent_answer_last_paint = _time.monotonic()
        self._ensure_spin_timer()

    def _agent_on_text_delta(self, delta: str) -> None:
        """Stream final answer tokens into the message list (not only footer)."""
        import time as _time

        d = delta or ""
        if not d:
            return
        if self._is_agent_tool_live():
            # Wait for tool row to finish; rare interleave — ignore.
            return
        # Collapse Thinking… before answer body (Grok).
        if self._is_agent_thinking_live():
            self._agent_finish_thinking()
        if not self._is_agent_answer_live():
            if self._live_step_active:
                self._freeze_live_step()
            self._agent_answer_buf = ""
            self._ensure_block_gap()
            self._live_step_active = True
            self._live_step_key = "agent-answer"
            self._live_step_text = "answer"
            self._live_step_strips = 0
            self._live_step_t0 = _time.monotonic()
        self._agent_answer_buf += d
        self._agent_answer_dirty = True
        now = _time.monotonic()
        # ~20fps — snappier than thinking so prose feels continuous.
        if now - self._agent_answer_last_paint >= 0.05:
            self._paint_live_answer()
        self._set_activity(phase="writing")

    def _agent_discard_live_answer(self) -> None:
        """Remove in-progress answer stream strips (before polished re-render)."""
        if self._live_step_active and self._is_agent_answer_live():
            self._pop_live_step_strips()
            self._clear_live_step()
        self._agent_answer_buf = ""
        self._agent_answer_dirty = False

    def _agent_finish_thinking(self) -> None:
        """Close Grok Thinking block as ``Thought for Xs`` (or drop if empty).

        The collapsed header **stays** in scrollback (Grok Build model) — it
        is not removed when the turn finishes. Duration is kept for history.
        Streaming body is discarded on collapse (Grok default).
        """
        import time as _time

        if not self._live_step_active or not self._is_agent_thinking_live():
            self._agent_thinking_buf = ""
            self._agent_thinking_dirty = False
            return
        elapsed = 0.0
        if self._agent_think_t0:
            elapsed = max(0.0, _time.monotonic() - self._agent_think_t0)
        had_body = bool(self._agent_thinking_buf.strip())
        if elapsed < 0.05 and not had_body and not self._live_step_text:
            # never really thought — just clear
            self._pop_live_step_strips()
            self._clear_live_step()
            self._agent_thinking_buf = ""
            self._agent_thinking_dirty = False
            if self._thinking_gap_above:
                self._trim_trailing_blank_lines(max_trim=2)
                self._thinking_gap_above = False
            return
        self._agent_last_thought_s = elapsed
        # Grok: "Thought for 6.6s" — remains as a process row after the turn.
        if elapsed < 60:
            label = f"Thought for {elapsed:.1f}s"
        else:
            label = f"Thought for {self._fmt_elapsed(int(elapsed))}"
        # Prefer content-aligned diamond row (same column as tools / answers).
        from room_tui.llm.msg_layout import thought_markup

        if self._pop_live_step_strips():
            pass
        self._clear_live_step()
        self._agent_thinking_buf = ""
        self._agent_thinking_dirty = False
        # Only remove the temporary blank *we* inserted for live Thinking…
        # Never strip user/assistant message margins (that glued process chrome
        # under the user band and answer under the previous prose).
        if self._thinking_gap_above:
            self._trim_trailing_blank_lines(max_trim=1)
            self._thinking_gap_above = False
        self._write(thought_markup(label))

    def _agent_open_tool_row(self, tool: str, args: Any = None) -> None:
        """Start a **new** scrollback block for this tool (Grok tool entry)."""
        # Close Thinking… as Thought for Xs, or freeze previous tool as history.
        if self._live_step_active and self._is_agent_thinking_live():
            self._agent_finish_thinking()
        elif self._live_step_active:
            self._freeze_live_step()
        # Do not trim blanks here — that ate the breath under user/assistant
        # messages. Process timeline stays tight via no *extra* inserts.
        self._agent_tool_seq += 1
        tool = (tool or "tool").strip()
        key = f"agent-tool:{self._agent_tool_seq}:{tool}"
        self._agent_live_tool = tool
        self._agent_live_args = args
        label = self._agent_tool_cmd_label(tool, args)
        self._turn_step(label, key=key)
        self._set_activity(phase="writing")

    def _paint_tool_result_body(
        self,
        tool: str,
        args: Any = None,
        result: Any = None,
        *,
        is_error: bool = False,
    ) -> None:
        """Paint tool body under process header (Grok parity).

        - Bash: stdout band (head/tail truncate; pretty JSON when needed)
        - Read: Truncated gutter preview (5+…+3)
        - Edit/Write: short numbered snippet
        - Error without body: one dim/error line
        """
        from room_tui.llm.message_render import (
            _bash_stdout_lines,
            _edit_snippet_renderable,
            _short,
            _shorten_path,
            extract_output,
            extract_path,
            is_bash_tool,
            is_edit_tool,
            is_read_tool,
            looks_like_source_content,
            looks_like_write_status,
            needs_fold,
            read_line_count,
            render_read_body,
        )
        from room_tui.llm.msg_layout import (
            COLOR_BG_CODE,
            content_pad,
            paint_output_band,
        )

        tool = (tool or "tool").strip()
        out = extract_output(result).rstrip("\n")
        pad = content_pad()
        w = self._msg_log_width() or 80
        path = extract_path(args) if args is not None else ""

        def _gap_then(write_fn) -> None:
            """Grok tool body spacing.

            - Blank between ``│  ◆`` title and the body band
            - Blank **after** the body so the next process row / message is not
              glued under Expand / stdout (screenshot: body → empty → next tool)

            Header-only tools (no body) never call this — timeline stays tight.
            """
            self._write("")
            write_fn()
            self._write("")

        # Bash / shell → Grok Execute stdout band.
        if is_bash_tool(tool):
            if not out:
                if is_error:
                    _gap_then(
                        lambda: self._write(
                            f"{pad}[{COLOR_ERR}](failed)[/{COLOR_ERR}]"
                        )
                    )
                return
            nlines = out.count("\n") + 1

            def _bash_body() -> None:
                if needs_fold(nlines):
                    self._write_foldable_band(
                        kind="bash",
                        content=out,
                        width=w,
                        total_lines=nlines,
                        render_body=lambda expanded: paint_output_band(
                            (
                                out.splitlines()
                                if expanded
                                else _bash_stdout_lines(out)
                            )
                            or [""],
                            width=w,
                            bg=COLOR_BG_CODE,
                        ),
                    )
                else:
                    self._write_renderable(
                        paint_output_band(
                            _bash_stdout_lines(out), width=w, bg=COLOR_BG_CODE
                        )
                    )

            _gap_then(_bash_body)
            return

        # Read / ctx_execute_file / … → Grok Read gutter preview.
        if is_read_tool(tool) or (
            path and looks_like_source_content(out) and not is_edit_tool(tool)
        ):
            from room_tui.llm.message_render import sanitize_read_output

            if not out:
                if is_error:
                    _gap_then(
                        lambda: self._write(
                            f"{pad}[{COLOR_ERR}](failed)[/{COLOR_ERR}]"
                        )
                    )
                return
            out = sanitize_read_output(out)
            if not out.strip():
                if is_error:
                    _gap_then(
                        lambda: self._write(
                            f"{pad}[{COLOR_ERR}](failed)[/{COLOR_ERR}]"
                        )
                    )
                return
            path_disp = _shorten_path(path) if path else ""
            nlines = read_line_count(out)

            def _read_body() -> None:
                if needs_fold(nlines):
                    self._write_foldable_band(
                        kind="read",
                        content=out,
                        path=path_disp or path,
                        width=w,
                        total_lines=nlines,
                        render_body=lambda expanded: render_read_body(
                            out,
                            path_disp or path,
                            expanded=expanded,
                            width=w,
                        ),
                    )
                else:
                    body = render_read_body(
                        out, path_disp or path, expanded=True, width=w
                    )
                    if body is not None:
                        self._write_renderable(body)

            _gap_then(_read_body)
            return

        # Edit/Write — status line or short snippet.
        if is_edit_tool(tool):
            if not out:
                if is_error:
                    _gap_then(
                        lambda: self._write(
                            f"{pad}[{COLOR_ERR}](failed)[/{COLOR_ERR}]"
                        )
                    )
                return
            if looks_like_write_status(out):
                safe = _short(out, 160).replace("[", r"\[")
                _gap_then(
                    lambda: self._write(
                        f"{pad}[{COLOR_MSG_DIM}]{safe}[/{COLOR_MSG_DIM}]"
                    )
                )
                return
            snippet = _edit_snippet_renderable(out, width=w, path=path or "")
            if snippet is not None:
                _gap_then(lambda: self._write_renderable(snippet))
            return

        # Generic tools (ctx_fetch_and_index, …): Grok keeps **header only**
        # after success. Errors show a proper code band (never raw multi-line
        # markup — that only pads the first line and looks broken).
        if is_error:
            if not out.strip():
                _gap_then(
                    lambda: self._write(
                        f"{pad}[{COLOR_ERR}](failed)[/{COLOR_ERR}]"
                    )
                )
                return
            from room_tui.llm.message_render import _truncate_tool_lines

            # Same head/tail policy as bash stdout; keep full-width band align.
            lines = _truncate_tool_lines(
                _short(out, 4000).splitlines() or [""]
            )

            def _err_body() -> None:
                self._write_renderable(
                    paint_output_band(
                        lines,
                        width=w,
                        bg=COLOR_BG_CODE,
                        fg=COLOR_ERR,
                    )
                )

            _gap_then(_err_body)
            return
        # Success: no body.
        return

    def _agent_finish_tool_row(
        self,
        tool: str,
        *,
        is_error: bool = False,
        args: Any = None,
        result: Any = None,
    ) -> None:
        """Finalize open tool block + paint result body once (no duplicate headers)."""
        from room_tui.llm.message_render import (
            extract_output,
            format_tool_header_markup,
            is_read_tool,
            read_line_count,
        )

        tool = (tool or "tool").strip()
        use_args = args if args is not None else self._agent_live_args

        was_live = bool(
            self._live_step_active
            and self._is_agent_tool_live()
            and (tool in self._live_step_key or self._agent_live_tool == tool)
        )
        # Guard: only match finished markers — not open keys ``agent-tool:1:bash``.
        already_done = f"agent-tool:done:{tool}" in self._agent_streamed_tools
        body_key = f"agent-tool:body:{tool}"

        out = extract_output(result).rstrip("\n")
        n = None
        empty = False
        if is_read_tool(tool):
            n = read_line_count(out) or None
            empty = not out

        # Finished chrome MUST keep green ❙ (success_rail=True when not error).
        header = format_tool_header_markup(
            tool,
            use_args,
            is_error=is_error,
            line_count=n,
            empty=empty,
        )

        if was_live:
            # Replace pulsing live row with static finished header (still ❙  ◆).
            if self._pop_live_step_strips():
                pass
            self._clear_live_step()
            self._agent_live_tool = ""
            self._agent_live_args = None
            self._write(header)
            self._agent_streamed_tools.append(f"agent-tool:done:{tool}")
        elif already_done:
            # Freeze left the live ``❙  ◆`` row in place — do not rewrite
            # (avoids blank-gutter ``   ◆ Run``). Only paint body if needed.
            pass
        else:
            # No prior row (e.g. restore edge) — write full finished header.
            self._write(header)
            self._agent_streamed_tools.append(f"agent-tool:done:{tool}")

        if body_key not in self._agent_streamed_tools:
            self._agent_streamed_tools.append(body_key)
            self._paint_tool_result_body(
                tool, use_args, result, is_error=is_error
            )
        # No trailing process-timeline gap (Grok stacks Thought/tools tightly).
        # Tool *bodies* (Read/bash bands) still use their own header↔body breath.

    def _step_label_budget(self) -> int:
        """Max display cells for step label so the row never wraps (1 strip)."""
        try:
            w = int(self.query_one("#msg-log", SmoothRichLog).size.width or 0)
        except Exception:
            w = 0
        # rail(1)+sp+diamond(1)+sp+sp ≈ 5; leave margin
        return max(12, (w or 72) - 8)

    def _truncate_step_label(self, label: str) -> str:
        budget = self._step_label_budget()
        if cell_len(label) <= budget:
            return label
        if budget <= 1:
            return "…"
        return set_cell_size(label, budget - 1).rstrip() + "…"

    def _pop_live_step_strips(self) -> bool:
        """Remove the open live-step region so it can be rewritten in place.

        Pin pad always trails content — clear it first so we never pop pad
        instead of the live body. Prefer absolute ``_live_step_start`` for
        multi-line answer/thinking streams (a 2-line cap used to leave old
        rows and stack the full buffer each paint → duplicate spam).
        """
        if not self._live_step_active:
            return False
        if self._live_step_strips <= 0 and self._live_step_start < 0:
            return False
        # Pad sits after live strips; drop it before measuring the tail.
        if self._scroll_pad_count > 0:
            self._clear_scroll_pad()
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            self._clear_live_step()
            return False
        n = len(log.lines)
        if n <= 0:
            self._clear_live_step()
            return False
        start = int(self._live_step_start)
        if start >= 0 and start <= n:
            # Absolute region: delete from start to end (entire live body).
            k = n - start
        else:
            # Fallback: count from tail. Multi-line live streams need full count;
            # short tool/step rows stay modest as a safety cap.
            if self._is_agent_thinking_live() or self._is_agent_answer_live():
                k = min(max(1, int(self._live_step_strips)), n)
            else:
                k = min(max(1, int(self._live_step_strips)), 4, n)
            start = n - k
        if k <= 0 or start < 0 or start > n:
            self._clear_live_step()
            return False
        del log.lines[start:]
        self._live_step_strips = 0
        self._live_step_start = -1
        try:
            log._line_cache.clear()
        except Exception:
            pass
        log.virtual_size = Size(
            getattr(log, "_widest_line_width", 0) or 0,
            len(log.lines),
        )
        log.refresh()
        return True

    def _append_step_markup(
        self, markup: str, *, follow: bool, max_strips: int | None = None
    ) -> int:
        """Write a step row; prefer a single strip (truncate prevents wrap)."""
        try:
            self._prepare_write_under_user_pin()
            log = self.query_one("#msg-log", SmoothRichLog)
            before = len(log.lines)
            # Under user-pin (Grok), never auto-scroll to bottom — pin follow
            # re-aligns the user command to the top after the write.
            if self._user_pin_active:
                do_end = False
            elif follow:
                # Prefer stick-to-bottom when following; use a small slack so
                # mid-flight pad/layout lag does not freeze the viewport mid-run.
                try:
                    at_bottom = log.scroll_y >= max(0, log.max_scroll_y - 3)
                except Exception:
                    at_bottom = True
                do_end = at_bottom
            else:
                do_end = False
            log.write(markup, scroll_end=do_end, animate=False)
            added = len(log.lines) - before
            # Cap bookkeeping for short step rows; multi-line streams use paint paths.
            cap = 2 if max_strips is None else max(1, max_strips)
            n = max(1, min(cap, added))
            # Absolute start so later pop removes exactly this write.
            self._live_step_start = before
            self._refresh_user_pin_after_write()
            return n
        except Exception:
            self._write(markup)
            return 1

    def _paint_live_step(self) -> None:
        """Pulse rail + diamond; refresh phase/elapsed suffix once per second.

        Runs from the 30fps spin timer. Rewriting log strips invalidates the
        whole line cache, so only repaint when there is new content (dirty) —
        pure cosmetic pulses run at ~10fps (every 3rd tick).
        """
        import time as _time

        if not self._live_step_active or not self._live_step_text:
            return
        pulse_tick = (int(self._spin_i) % 3) == 0
        # Streaming Thinking: redraw header + muted body together on new
        # tokens; otherwise pulse the rail at reduced rate.
        if self._is_agent_thinking_live():
            if self._agent_thinking_dirty or (
                self._agent_thinking_buf and pulse_tick
            ):
                self._paint_live_thinking()
                return
            if self._agent_thinking_buf:
                # Body unchanged and not a pulse tick — skip this frame.
                return
            # Header-only Thinking… (no tokens yet) — pulse diamond only.
        # Streaming answer: rewrite prose only when new tokens arrived
        # (nothing in the answer body animates on its own).
        if self._is_agent_answer_live():
            if self._agent_answer_dirty:
                self._paint_live_answer()
            return
        # Chrome pulse (~10fps); text suffix only when the second rolls
        # (or phase just changed — caller sets elapsed_i = -1).
        elapsed = 0
        if self._live_step_t0:
            elapsed = max(0, int(_time.monotonic() - self._live_step_t0))
        text_changed = elapsed != self._live_step_elapsed_i
        if not pulse_tick and not text_changed:
            return
        if text_changed:
            self._live_step_elapsed_i = elapsed
        display = self._truncate_step_label(self._live_display_label())
        markup = self._format_step_line(display, None)
        if not self._pop_live_step_strips():
            return
        self._live_step_active = True
        self._live_step_strips = self._append_step_markup(markup, follow=True)

    @staticmethod
    def _humanize_input_step_key(key: str, event: dict[str, Any] | None = None) -> str:
        """reg-0-软件任务书-c1 → 软件任务书 · 分块 2/3"""
        import re

        raw = (key or "").strip()
        name = raw
        # feed-0-name / reg-0-name / reg-0-name-c1
        m = re.match(r"^(?:reg|feed)-\d+-(.+)$", raw)
        if m:
            name = m.group(1)
        name = re.sub(r"-c\d+$", "", name)
        ev = event or {}
        chunk = ev.get("chunk")
        chunks = ev.get("chunks")
        try:
            if chunk is not None and chunks is not None and int(chunks) > 1:
                return f"{name}  ·  分块 {int(chunk) + 1}/{int(chunks)}"
        except (TypeError, ValueError):
            pass
        return name or raw or "资料"

    def _turn_step(
        self,
        text: str,
        *,
        ok: bool | None = None,
        key: str = "",
    ) -> None:
        """Step status row — in-progress updates **in place**; ok finalizes it.

        Running: rail + diamond animate; label steady (+ phase/elapsed suffix).
        Done freezes as ✓/✗.

        Shared by /new · /continue · template register · agent chat.
        """
        import time as _time

        raw = self._clean_step_label(text)
        # Failures: keep name short, never dump Traceback into the list.
        if ok is False:
            body = raw
            if body.startswith("失败"):
                body = body[2:].strip()
            name, err = body, ""
            for marker in (
                "engine failed",
                "EngineError",
                "Traceback",
                "pi timeout",
                "pi exit",
                "room agent timeout",
                "room agent exit",
                "room agent not found",
                "Error:",
                "error:",
            ):
                i = body.find(marker)
                if i < 0:
                    i = body.lower().find(marker.lower())
                if i >= 0:
                    name, err = body[:i].strip(), body[i:].strip()
                    break
            if not err and "  " in body:
                # "NAME  short reason"
                name, err = body.rsplit("  ", 1)
            if err:
                raw = f"失败  {name}  ·  {self._short_error(err)}" if name else f"失败  {self._short_error(err)}"
            else:
                raw = f"失败  {self._short_error(name or body)}"

        base = self._truncate_step_label(raw)
        if ok is None:
            display = self._truncate_step_label(
                # include current phase/elapsed if same live row is refreshed
                base
                if not self._live_step_active
                else self._live_display_label()
            )
            # When opening/retargeting, base text is the new label
            if not self._live_step_active or (
                key and key != self._live_step_key
            ) or base != self._live_step_text:
                self._live_step_text = base
                display = self._truncate_step_label(base)
            markup = self._format_step_line(display, None)
        else:
            markup = self._format_step_line(base, ok)

        if self._live_step_active:
            self._pop_live_step_strips()

        strips = self._append_step_markup(markup, follow=True)

        if ok is None:
            self._live_step_active = True
            self._live_step_strips = strips
            self._live_step_text = base
            if key:
                # New logical step → reset timer/phase
                if key != self._live_step_key:
                    self._live_step_phase = ""
                    self._live_step_t0 = _time.monotonic()
                    self._live_step_elapsed_i = -1
                self._live_step_key = key
            elif not self._live_step_t0:
                self._live_step_t0 = _time.monotonic()
            self._ensure_spin_timer()
        else:
            # Final step outcome survives restart (template register / run steps).
            # Persist *before* clearing live state so a concurrent reflow cannot
            # rebuild from a history that is missing this row.
            if not self._restoring_history and base.strip():
                try:
                    tag = "完成" if ok is True else "失败"
                    line = base.strip()
                    if not (line.startswith("完成") or line.startswith("失败")):
                        line = f"{tag}  {line}"
                    self._persist_chat(
                        "error" if ok is False else "system",
                        line,
                    )
                except Exception:
                    pass
            # Finalized into history — next step_start opens a fresh row.
            self._clear_live_step()

    def _ensure_live_for_focus(self) -> None:
        """If a run is active but the live row died, re-open from orch focus.

        Heals missed step_start events and strip-count corruption so the list
        keeps moving while the title still shows 生成中.
        """
        if self._live_step_active:
            return
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            st = app.orch.state
        except Exception:
            return
        if not st.running:
            return
        focus = str(st.focus_section or "").strip()
        if not focus:
            return
        # Skip if snapshot says this section is already done.
        try:
            snap = st.snapshot
            if snap and snap.sections:
                for sec in snap.sections:
                    if isinstance(sec, dict):
                        sid = str(sec.get("section_id") or "")
                        status = str(sec.get("status") or "")
                    else:
                        sid = str(getattr(sec, "section_id", "") or "")
                        status = str(getattr(sec, "status", "") or "")
                    if sid == focus and status.lower() in {
                        "done",
                        "complete",
                        "completed",
                        "ok",
                    }:
                        # Focus lags; show generic generating state.
                        self._set_activity("生成中…", phase="thinking")
                        return
        except Exception:
            pass
        name = self._section_display_name(focus)
        label = f"生成  {name}"
        self._turn_step(label, key=f"sec:{focus}")
        self._set_activity(label, phase="thinking")

    def _set_activity(
        self,
        label: str = "",
        *,
        phase: str | None = None,
        reset_timer: bool = False,
    ) -> None:
        """Grok-like live status: spinner + phase + step + elapsed (+ progress).

        Does **not** dump raw model tokens (those look like JSON garbage).
        """
        import time as _time

        if label:
            self._activity_label = label
        if phase:
            self._activity_phase = phase
        if reset_timer or not self._activity_on:
            self._activity_t0 = _time.monotonic()
            self._activity_phase = phase or "thinking"
            self._activity_elapsed_i = -1
        self._activity_on = True
        try:
            bar = self.query_one("#activity", Static)
            bar.add_class("active")
            bar.styles.height = 1
            bar.styles.min_height = 1
            bar.styles.max_height = 1
        except Exception:
            pass
        self._rebuild_activity_tail(force=True)
        self._paint_activity()
        self._ensure_spin_timer()

    def _flush_pending_reflow(self) -> None:
        """If sidebar was toggled mid-run, reflow once when idle."""
        w = int(getattr(self, "_pending_reflow_w", 0) or 0)
        if w < 20:
            return
        self._pending_reflow_w = 0
        try:
            self._reflow_msg_log_for_width(w, force=True)
        except Exception:
            pass

    def _clear_activity(self) -> None:
        self._activity_on = False
        self._activity_label = ""
        self._activity_phase = "thinking"
        self._activity_t0 = 0.0
        self._activity_elapsed_i = -1
        self._activity_tail = " Working..."
        try:
            bar = self.query_one("#activity", Static)
            bar.update("")
            bar.remove_class("active")
            bar.styles.height = 0
            bar.styles.min_height = 0
            bar.styles.max_height = 0
        except Exception:
            pass
        # Keep timer running if the live message row still needs its spinner.
        self._maybe_stop_spin_timer()
        # Sidebar may have been toggled mid-run — reflow when we become idle
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            if not app.orch.state.running and not self._chat_busy:
                self._flush_pending_reflow()
        except Exception:
            if not self._chat_busy:
                self._flush_pending_reflow()

    def _section_display_name(self, section_id: str) -> str:
        """Prefer document title over bare section id (Grok-like readable status)."""
        sid = str(section_id or "").strip()
        if not sid:
            return ""
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            snap = app.orch.state.snapshot
            if snap and snap.sections:
                for sec in snap.sections:
                    if isinstance(sec, dict):
                        s = str(sec.get("section_id") or "")
                        title = str(sec.get("title") or "")
                    else:
                        s = str(getattr(sec, "section_id", "") or "")
                        title = str(getattr(sec, "title", "") or "")
                    if s == sid and title and title != sid:
                        return title
        except Exception:
            pass
        return sid

    def _activity_headline(self) -> str:
        """Status strip: Agent uses Grok ``Thinking...``; doc-gen uses ``Working...``."""
        p = (self._activity_phase or "").lower()
        if p == "cancel":
            return "Cancelling..."
        # Chat submit → first response: preflight probes + request in flight
        # (Grok "Waiting for response…" — before _chat_busy is even set).
        # NB: bare "waiting" is the doc-gen pipeline's phase → "Working...".
        if p == "waiting-response":
            return "Waiting for response..."
        # Agent turn → Thinking... (matches Grok status strip in screenshot)
        if self._chat_busy or self._is_agent_block_live():
            return "Thinking..."
        return "Working..."

    def _rebuild_activity_tail(self, *, force: bool = False) -> None:
        """Rebuild after spinner: `` Working...  ·  14s``."""
        import time as _time

        elapsed = 0
        if self._activity_t0:
            elapsed = max(0, int(_time.monotonic() - self._activity_t0))
        if not force and elapsed == self._activity_elapsed_i:
            return
        self._activity_elapsed_i = elapsed

        head = self._activity_headline()
        if elapsed > 0:
            self._activity_tail = f" {head}  ·  {self._fmt_elapsed(elapsed)}"
        else:
            self._activity_tail = f" {head}"

    def _paint_activity(self) -> None:
        if not self._activity_on:
            return
        glyph = self._SPIN[self._spin_i % len(self._SPIN)]
        # Brand-colored single cell — fixed width, no layout shift.
        line = f"[bold {COLOR_BRAND}]{glyph}[/bold {COLOR_BRAND}]{self._activity_tail}"
        try:
            # #activity height is fixed by CSS (0 / 1 via .active) — repaint
            # only; the default layout=True would reflow the screen at 30fps.
            bar = self.query_one("#activity", Static)
            try:
                bar.update(line, layout=False)
            except TypeError:
                bar.update(line)
        except Exception:
            pass

    def _tick_spinner(self) -> None:
        if not self._activity_on and not self._live_step_active:
            self._maybe_stop_spin_timer()
            return
        # Free-running tick (Grok animation_tick++); do not wrap on braille len.
        self._spin_i = int(self._spin_i) + 1
        if self._activity_on:
            # Refresh elapsed/progress only when the second rolls; spin every tick.
            self._rebuild_activity_tail(force=False)
            self._paint_activity()
        if self._live_step_active:
            # Grok: redraw live Thinking chrome each animation tick (~30fps).
            self._paint_live_step()

    async def _bootstrap(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        ws = Workspace(Path(app.cfg.workspace or Path.cwd()))
        root = ws.root
        self._ws_root = root
        self._bootstrap_done = False
        # Same probe as room doctor (engine version + pi resolve)
        try:
            ready, err = await app.probe_environment()
        except Exception as e:
            ready, err = False, f"启动检查失败: {e}"
        self._ready = bool(ready)
        self._set_bottom_bar(
            root,
            app.cfg.model,
            ok=self._ready,
            err=err if not self._ready else "",
        )
        self._set_title(mode="空闲")
        self._clear_live_step()
        self.query_one("#msg-log", SmoothRichLog).clear()

        # Restore durable UI transcript (user/assistant/system only).
        history = ws.read_chat_history()
        if not history:
            try:
                if ws.seed_chat_history_from_pi_agent():
                    history = ws.read_chat_history()
            except Exception:
                pass
        if history:
            user_prompts: list[str] = []
            for row in history:
                bl = row.get("blocks")
                role = str(row.get("role") or "system")
                text = str(row.get("text") or "")
                self._replay_history_entry(
                    role,
                    text,
                    blocks=bl if isinstance(bl, list) else None,
                    ts=str(row.get("ts") or ""),
                )
                # Seed ↑/↓ prompt history from prior user turns (oldest → newest).
                if role == "user" and text.strip():
                    user_prompts.append(text)
            if user_prompts:
                self._prompt_hist.seed(user_prompts)

        if not self._ready and err:
            prev = self._restoring_history
            self._restoring_history = True
            try:
                self._append_notice_block(
                    err,
                    "终端自检: room doctor",
                    "引擎需 paper-derived version 成功（产品分支 claude0）",
                    "可输入 /help",
                    persist=False,
                )
            finally:
                self._restoring_history = prev

        # Model readiness → footer only. Never dump model/pi into the message list.
        # (May spawn ``pi --list-models``; title restore is handled in pi_catalog.)
        self._refresh_model_status(announce=False)

        # Fresh install: no Room auth / empty catalog → first-run model setup
        try:
            from room_tui.auth_setup import needs_model_setup

            if needs_model_setup(pi_bin=app.cfg.pi_bin) and not history:
                self._show_footer_hint(
                    "首次使用 · 即将打开模型配置（也可 Ctrl+M / /setup）",
                    seconds=4.0,
                )
                self.set_timer(0.35, self._open_model_setup)
        except Exception:
            pass

        # Re-assert host tab title after any bootstrap pi spawn
        self._set_title(mode="空闲")

        manifest = ws.load_manifest()
        has_task = bool(manifest and str(manifest.session_id or "").strip())

        if not has_task or manifest is None:
            self._pipe = PipelineState()
            self._render_steps()
            self._render_chapters([])
            self._bootstrap_done = True
            self._set_title(mode="空闲")
            self._focus_prompt()
            self.call_after_refresh(self._focus_prompt)
            return

        # Refresh engine snapshot first, then decide complete vs incomplete notice.
        try:
            snap = await app.orch.refresh_snapshot(manifest.session_id)
            self._render_chapters(snap.sections, focus=app.orch.state.focus_section)
            if snap.progress:
                manifest.progress = snap.progress
            eng_phase = str(snap.phase or "").strip().lower()
            if eng_phase in ("complete", "done"):
                manifest.phase = "complete"
                if (manifest.status or "").lower() not in (
                    "cancelled",
                    "canceled",
                    "failed",
                ):
                    manifest.status = "complete"
            elif eng_phase and (manifest.status or "").lower() not in (
                "complete",
                "cancelled",
                "canceled",
                "failed",
            ):
                manifest.phase = snap.phase
            if snap.template_id and not manifest.template_id:
                manifest.template_id = snap.template_id
        except Exception:
            pass

        complete = not self._is_incomplete_task(manifest)
        if complete:
            self._pipe.complete_all()
            self._set_title(
                mode=mode_label(False, "complete", manifest.progress),
                template=manifest.template_id,
            )
            app.orch.state.phase = "complete"
            app.orch.state.running = False
        else:
            self._pipe.done_keys.update({"template", "register", "feed"})
            self._pipe.current_key = "generate"
            self._set_title(
                mode=mode_label(
                    False,
                    manifest.phase or manifest.status or "paused",
                    manifest.progress,
                ),
                template=manifest.template_id,
            )
        self._render_steps()

        # Pale-yellow rail: unfinished document task only (status / template / out /continue).
        # Defer one frame so #msg-log has a real width (bootstrap can run pre-layout).
        if self._is_incomplete_task(manifest):
            m_notice = manifest
            can_resume = self._manifest_resumable(manifest)

            def _show_task_notice() -> None:
                try:
                    if self._is_incomplete_task(m_notice):
                        self._append_incomplete_task_block(
                            m_notice, resumable=can_resume
                        )
                except Exception:
                    pass

            self.call_after_refresh(_show_task_notice)

        self._bootstrap_done = True
        # Final title re-assert (pi --list-models during bootstrap must not stick)
        try:
            from room_tui.console_title import restore_console_title

            restore_console_title()
        except Exception:
            pass
        self._focus_prompt()
        self.call_after_refresh(self._focus_prompt)

    def _project_name(self) -> str:
        """Workspace / project directory name for chrome."""
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
        except Exception:
            root = self._ws_root or Path.cwd()
        name = (root.name or str(root)).strip()
        return name or "—"

    def _set_title(self, *, mode: str, template: str = "") -> None:
        """Title bar: brand · mode · [project]. ``template`` is ignored (compat)."""
        del template  # was template id; not useful in chrome
        mode_s = normalize_mode_display(mode)
        if "失败" in mode_s:
            mode_mk = f"[bold {COLOR_ERR}]{mode_s}[/bold {COLOR_ERR}]"
        elif "取消" in mode_s:
            mode_mk = f"[bold {COLOR_WARN}]{mode_s}[/bold {COLOR_WARN}]"
        elif any(k in mode_s for k in ("生成", "喂入", "组装", "初始化")):
            mode_mk = f"[bold {COLOR_OK}]{mode_s}[/bold {COLOR_OK}]"
        elif "完成" in mode_s:
            mode_mk = f"[{COLOR_OK}]{mode_s}[/{COLOR_OK}]"
        elif "暂停" in mode_s:
            mode_mk = f"[{COLOR_WARN}]{mode_s}[/{COLOR_WARN}]"
        else:
            mode_mk = f"[dim]{mode_s}[/dim]"

        # White brand + white project on primary bar; mode keeps semantic colors.
        project = self._project_name()
        if len(project) > 28:
            project = project[:26] + "…"
        bits = [
            f"[bold {COLOR_BRAND_ON_BAR}]Room[/bold {COLOR_BRAND_ON_BAR}]",
            mode_mk,
            f"[bold {COLOR_BRAND_ON_BAR}][{project}][/bold {COLOR_BRAND_ON_BAR}]",
        ]
        self.query_one("#title-bar", Static).update(
            "  [#A8C8E8]·[/#A8C8E8]  ".join(bits)
        )
        # Host terminal tab: always just "Room" (not mode/project — keeps WT tab short)
        try:
            from room_tui.console_title import set_console_title

            set_console_title("Room")
            if hasattr(self.app, "title"):
                self.app.title = "Room"
        except Exception:
            pass

    def _set_bottom_bar(
        self,
        root: Path,
        model: str,
        *,
        ok: bool = True,
        err: str | None = None,
    ) -> None:
        """Update workspace root + readiness for footer.

        When *ok* is False and *err* is omitted, keep the previous ``_env_err``
        so orch/event repaints do not wipe the bootstrap failure reason
        (which previously left the model name on the right while chat stayed blocked).
        """
        del model  # reserved for future footer; readiness is source of truth
        self._ws_root = root
        self._ready = ok
        if ok:
            self._env_err = ""
        elif err is not None:
            self._env_err = (err or "").strip()
        # else: keep existing _env_err
        if not self._footer_hint:
            self._paint_footer()

    def _footer_keys_text(self) -> str:
        """Left: ≤3 contextual hints, Grok-style `Key action  |  …`."""
        app: "RoomApp" = self.app  # type: ignore[assignment]
        # Transient double-press / clear hints use _footer_hint instead.
        if app.orch.state.running or self._chat_busy:
            return "Esc×2 / Ctrl+C cancel  |  Ctrl+Q quit"
        draft = ""
        try:
            draft = self._composer_text()
        except Exception:
            draft = ""
        if draft.startswith("/"):
            return "Tab complete  |  Enter run  |  Esc clear"
        if draft.strip():
            return "Enter send  |  Shift+Enter newline  |  Esc clear"
        if not self._ready:
            return "room doctor  |  /help  |  Ctrl+Q quit"
        if not self._model_ok:
            return "Ctrl+M 连接模型  |  /model 切换  |  /help"
        # Idle: essentials + live sidebar state (Cmd+B on macOS, Ctrl+B elsewhere)
        side = "收起" if self._sidebar_collapsed else "展开"
        return (
            f"Enter send  |  Ctrl+M 连接  |  /model 切换  |  "
            f"{self._sidebar_hotkey_label()} 侧栏 · [{side}]"
        )

    def _current_llm_label(self) -> str:
        """Active provider/model for footer (orch worker overrides cfg when set)."""
        app: "RoomApp" = self.app  # type: ignore[assignment]
        st = app.orch.state
        provider = (st.provider or app.cfg.provider or "").strip()
        model = (st.model or app.cfg.model or "").strip()
        if provider and model:
            if model.startswith(provider + "/") or "/" in model:
                return model
            return f"{provider}/{model}"
        return model or provider or "—"

    def _footer_meta_text(self) -> str:
        """Right: readiness error, or model warning, or provider/model."""
        if not self._ready or self._env_err:
            reason = (self._env_err or "环境未就绪").strip()
            # Keep short for the status bar; full text is in the notice block
            if len(reason) > 36:
                reason = reason[:34] + "…"
            return f"[⚠ {reason}]"
        if not self._model_ok:
            label = self._current_llm_label()
            if label in ("—", ""):
                return "[⚠ 未配置模型 · Ctrl+M 连接]"
            return f"[⚠ {label} · /model]"
        return f"[{self._current_llm_label()}]"

    def _paint_footer(self) -> None:
        bar = self.query_one("#status-bar", Horizontal)
        left = self.query_one("#status-left", Static)
        right = self.query_one("#status-right", Static)
        if self._footer_hint:
            key = ("hint", self._footer_hint, self._footer_meta_text())
            if key == self._footer_paint_cache:
                return
            self._footer_paint_cache = key
            left.update(self._footer_hint)
            right.update(key[2])
            bar.remove_class("error")
            bar.remove_class("model-warn")
            bar.add_class("hint")
            return
        # Static.update always forces a screen re-layout; this runs per
        # keystroke, so skip it entirely while the texts are unchanged.
        key = (
            "keys",
            self._footer_keys_text(),
            self._footer_meta_text(),
            bool(self._env_err),
            bool(self._model_ok),
        )
        if key == self._footer_paint_cache:
            return
        self._footer_paint_cache = key
        bar.remove_class("hint")
        left.update(key[1])
        right.update(key[2])
        if self._env_err:
            bar.add_class("error")
            bar.remove_class("model-warn")
        elif not self._model_ok:
            bar.remove_class("error")
            bar.add_class("model-warn")
        else:
            bar.remove_class("error")
            bar.remove_class("model-warn")

    def _show_footer_hint(self, text: str, *, seconds: float = 1.2) -> None:
        self._footer_hint = text
        self._paint_footer()
        if self._footer_timer is not None:
            try:
                self._footer_timer.stop()
            except Exception:
                pass
            self._footer_timer = None

        def _clear() -> None:
            self._footer_hint = ""
            self._paint_footer()
            self._footer_timer = None

        self._footer_timer = self.set_timer(seconds, _clear)

    def _render_steps(self) -> None:
        self.query_one("#steps-body", Static).update(
            "\n".join(self._pipe.render_lines()) or " ○  —"
        )

    def _chapter_width_cells(self) -> int:
        """Display cells available for one chapter line (avoid wrap → blank rows).

        Budget: sidebar width minus left border, left pads, plus 1 cell slack.
        Prefer live content width after layout.
        """
        try:
            body = self.query_one("#chapters-body", Static)
            live = int(body.content_size.width or 0)
        except Exception:
            live = 0
        if live >= 12:
            return max(12, live - 1)
        # border(1) + sidebar L pad(1) + scroll L pad(1) + slack(1)
        return max(12, SIDEBAR_WIDTH - 4)

    def _render_chapters(self, sections: list[Any], focus: str = "") -> None:
        body = self.query_one("#chapters-body", Static)
        if not sections:
            body.update("[dim]  尚无章节\n  生成后显示大纲[/dim]")
            return
        body.update(
            "\n".join(
                render_chapter_lines(
                    sections,
                    focus=focus,
                    width_cells=self._chapter_width_cells(),
                )
            )
        )

    # ── multi-line composer (Grok: in-buffer \\n, field grows to max 8) ──

    def _composer_text(self) -> str:
        return self.query_one("#cmd-input", PromptField).value or ""

    def _clear_composer(self) -> None:
        self._committed_lines = []
        self._prompt_hist.reset_browse()
        self.query_one("#cmd-input", PromptField).set_value("")
        self._hide_composer_preview()

    def _composer_is_empty(self) -> bool:
        return not (self.query_one("#cmd-input", PromptField).value or "").strip()

    def _set_composer_text(self, text: str) -> None:
        """Replace composer contents (history recall / rewind fill)."""
        raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        self._committed_lines = []
        self.query_one("#cmd-input", PromptField).set_value(raw)
        self._hide_composer_preview()
        self._update_slash_suggest(self._composer_text())

    def _prompt_history_navigate(self, direction: int) -> bool:
        """Grok-like ↑/↓ prompt history. Returns True if the key was handled."""
        handled, new_text = self._prompt_hist.navigate(
            direction,
            composer_empty=self._composer_is_empty(),
            draft_text=self._composer_text(),
        )
        if not handled or new_text is None:
            return handled
        self._set_composer_text(new_text)
        return True

    def _hide_composer_preview(self) -> None:
        """Keep legacy #composer-preview collapsed (multi-line is in-field)."""
        try:
            prev = self.query_one("#composer-preview", ChromeStatic)
            prev.update("")
            prev.remove_class("active")
            prev.styles.display = "none"
            prev.styles.height = 0
            prev.styles.min_height = 0
            prev.styles.max_height = 0
        except Exception:
            pass

    def _refresh_composer_preview(self) -> None:
        self._hide_composer_preview()

    def _insert_composer_newline(self) -> None:
        """Grok: insert ``\\n`` at caret inside the multi-line field."""
        inp = self.query_one("#cmd-input", PromptField)
        inp.insert_newline()
        self._update_slash_suggest(self._composer_text())

    def action_focus_input(self) -> None:
        self._focus_prompt()

    def _scroll_msg_log_by(self, delta_rows: float) -> None:
        """Programmatic page/line scroll of #msg-log (works while prompt focused)."""
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
        except Exception:
            return
        try:
            max_y = float(log.max_scroll_y)
        except Exception:
            max_y = 0.0
        if max_y <= 0:
            return
        try:
            cur = float(getattr(log, "scroll_target_y", None) or log.scroll_y)
        except Exception:
            cur = float(log.scroll_y)
        target = max(0.0, min(max_y, cur + delta_rows))
        if abs(target - cur) < 1e-6:
            return
        try:
            log.auto_scroll = False
        except Exception:
            pass
        try:
            log.scroll_to(y=target, animate=False, immediate=True)
        except TypeError:
            try:
                log.scroll_to(y=target, animate=False)
            except Exception:
                log.scroll_y = target
        except Exception:
            try:
                log.scroll_y = target
            except Exception:
                return
        try:
            log.scroll_target_y = target
        except Exception:
            pass
        self._on_msg_log_user_scrolled(target)

    def action_scroll_log_page_up(self) -> None:
        """PageUp — scroll conversation up (Grok; works with prompt focused)."""
        vh = float(self._msg_viewport_height())
        self._scroll_msg_log_by(-(max(4.0, vh - 2.0)))

    def action_scroll_log_page_down(self) -> None:
        """PageDown — scroll conversation down."""
        vh = float(self._msg_viewport_height())
        self._scroll_msg_log_by(max(4.0, vh - 2.0))

    @on(SmoothRichLog.UserScrolled, "#msg-log")
    def _on_smooth_log_user_scrolled(self, event: SmoothRichLog.UserScrolled) -> None:
        self._on_msg_log_user_scrolled(event.y)

    def action_blur_or_idle(self) -> None:
        """Esc policy (Grok Build + Room cancel).

        Steal first: rewind picker → slash menu.
        Running: Esc×2 cancels (Room; Grok uses Ctrl+C only).
        Idle + non-empty: Esc×2 within 800ms clears (saves to prompt history).
        Idle + empty + messages: Esc×2 within 800ms opens rewind picker.
        Idle + empty + no messages: no-op.
        """
        import time

        app: "RoomApp" = self.app  # type: ignore[assignment]
        now = time.monotonic()
        # Grok double-Esc window is 800ms; keep slightly looser for cancel.
        esc_window = 0.8

        # Steal-Esc: close new-run / rewind / slash overlays first.
        if self._new_open:
            self._close_new_flow(cancelled=True)
            self._esc_at = 0.0
            return
        if self._rewind_open:
            self._close_rewind_picker()
            self._esc_at = 0.0
            self._show_footer_hint("Rewind cancelled", seconds=0.8)
            return
        if self._slash_open:
            self._close_slash_dropdown()
            self._esc_at = 0.0
            self._show_footer_hint("Slash menu closed", seconds=0.8)
            return

        # Running: double-Esc aborts generation / Agent turn.
        if app.orch.state.running or self._chat_busy:
            again = (now - self._esc_at) < 1.2
            self._esc_at = now
            if again:
                self._esc_at = 0.0
                self._ctrl_c_at = 0.0
                self.action_cancel_run()
            else:
                what = "Agent" if self._chat_busy and not app.orch.state.running else "任务"
                self._show_footer_hint(f"Esc: 再按一次取消{what}")
            return

        empty = not self._composer_text().strip()
        again = (now - self._esc_at) < esc_window
        self._esc_at = now

        if not empty:
            # Grok: first Esc toast, second clears (+ save to prompt history).
            if again:
                self._prompt_hist.push(self._composer_text())
                self._clear_composer()
                self._update_slash_suggest("")
                self._esc_at = 0.0
                self._show_footer_hint("Input cleared", seconds=0.8)
            else:
                self._show_footer_hint("Esc: 再按一次清空输入", seconds=0.9)
            return

        # Empty prompt — rewind if we have conversation messages.
        if self._has_conversation_for_rewind():
            if again:
                self._esc_at = 0.0
                self._open_rewind_picker()
            # First press is silent (Grok).
            return

        # Empty + no messages: swallowed no-op (Grok).

    def action_interrupt(self) -> None:
        import time

        app: "RoomApp" = self.app  # type: ignore[assignment]
        now = time.monotonic()
        again = (now - self._ctrl_c_at) < 1.2
        self._ctrl_c_at = now

        if app.orch.state.running or self._chat_busy:
            if again:
                self._footer_hint = ""
                self._paint_footer()
                self._ctrl_c_at = 0.0
                self._esc_at = 0.0
                self.action_cancel_run()
            else:
                what = "Agent" if self._chat_busy and not app.orch.state.running else "任务"
                self._show_footer_hint(f"Ctrl+C: 再按一次取消{what}")
            return
            return
        if self._composer_text():
            # Same as Esc clear: stash draft in ↑/↓ history before wiping.
            self._prompt_hist.push(self._composer_text())
            self._clear_composer()
            self._update_slash_suggest("")
            self._ctrl_c_at = 0.0
            self._show_footer_hint("Input cleared", seconds=1.0)
            return
        if again:
            self._footer_hint = ""
            self._ctrl_c_at = 0.0
            self.app.exit()
            return
        self._show_footer_hint("Ctrl+C: press again to quit")

    def on_paste(self, event: events.Paste) -> None:
        """Fallback when IME commit paste reaches the screen (no focused field, or bubble)."""
        if not event.text:
            return
        # Focused PromptField already handles paste and stops propagation.
        inp = self.query_one("#cmd-input", PromptField)
        if inp.has_focus:
            return
        inp.focus()
        line = event.text.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0]
        if line:
            inp.insert_text(line)
            self._update_slash_suggest(self._composer_text())
        event.prevent_default()
        event.stop()

    def _paste_clipboard_into_composer(self) -> bool:
        """Insert OS clipboard into #cmd-input. True if any text was inserted."""
        app: "RoomApp" = self.app  # type: ignore[assignment]
        text = ""
        try:
            if hasattr(app, "read_text_from_clipboard"):
                text = str(app.read_text_from_clipboard() or "")
        except Exception:
            text = ""
        text = PromptField._sanitize_paste(text)
        if not text:
            return False
        try:
            inp = self.query_one("#cmd-input", PromptField)
            inp.focus()
            # Multi-line paste: keep newlines in the multi-line composer.
            if hasattr(inp, "_insert"):
                inp._insert(text)  # type: ignore[attr-defined]
            else:
                first = text.split("\n", 1)[0]
                if first:
                    inp.insert_text(first)
            self._update_slash_suggest(self._composer_text())
            return True
        except Exception:
            return False

    @on(events.Click)
    def _on_shell_right_click_paste(self, event: events.Click) -> None:
        """Windows console parity: right-click pastes into the composer.

        Textual mouse tracking disables the host terminal's right-click paste
        (WT / conhost). Reconstruct that behavior app-side.
        """
        try:
            btn = int(getattr(event, "button", 1) or 1)
        except Exception:
            btn = 1
        if btn != 3:
            return
        # Don't steal right-click from dedicated controls if we add any later.
        if self._paste_clipboard_into_composer():
            event.stop()
            event.prevent_default()
        else:
            # Empty clipboard — still consume so selection doesn't get weird.
            event.stop()

    def on_key(self, event: events.Key) -> None:
        inp = self.query_one("#cmd-input", PromptField)
        if event.key == "ctrl+c":
            event.prevent_default()
            event.stop()
            self.action_interrupt()
            return
        # Newline fallbacks (primary path is PromptField.NewlineRequest).
        if event.key in PromptField._NEWLINE_KEYS:
            event.prevent_default()
            event.stop()
            self._insert_composer_newline()
            inp.focus()
            return

        # Slash dropdown: Tab accept selected (↑↓ via PromptField.Navigate)
        if self._slash_open and inp.has_focus and event.key == "tab":
            event.prevent_default()
            event.stop()
            self._slash_accept_selected(run=False)
            return

        if event.key == "tab":
            line = inp.value or ""
            if inp.has_focus and line.startswith("/"):
                event.prevent_default()
                event.stop()
                self._slash_tab_complete()
                return
            event.prevent_default()
            event.stop()
            if inp.has_focus:
                self.query_one("#msg-log", SmoothRichLog).focus()
                self._prompt_focused = False
            else:
                inp.focus()
                self._prompt_focused = True
            return
        if inp.has_focus:
            return
        # Same CJK fallback as PromptField (Kitty IME → character may be missing).
        ch = PromptField._insertable_text(event)
        if ch is not None:
            inp.focus()
            inp.insert_text(ch)
            self._update_slash_suggest(self._composer_text())
            event.prevent_default()
            event.stop()

    def _close_slash_dropdown(self) -> None:
        self._slash_open = False
        self._slash_matches = []
        self._slash_selected = 0
        self._slash_mode = "cmd"
        try:
            sug = self.query_one("#slash-suggest", Static)
            # Called on every keystroke — skip the widget/style churn (and the
            # re-layout it triggers) when the panel is already closed.
            if not sug.has_class("active"):
                return
            sug.update("")
            sug.remove_class("active")
            sug.styles.display = "none"
            sug.styles.height = 0
            sug.styles.min_height = 0
            sug.styles.max_height = 0
        except Exception:
            pass

    # ── Grok rewind picker (Esc Esc / /rewind) ──────────────────

    def _has_conversation_for_rewind(self) -> bool:
        """True when there is at least one user prompt to rewind to."""
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
            return bool(Workspace(root).list_user_rewind_points())
        except Exception:
            return False

    def _close_rewind_picker(self) -> None:
        self._rewind_open = False
        self._rewind_items = []
        self._rewind_selected = 0
        try:
            sug = self.query_one("#rewind-suggest", Static)
            sug.update("")
            sug.remove_class("active")
            sug.styles.display = "none"
            sug.styles.height = 0
            sug.styles.min_height = 0
            sug.styles.max_height = 0
        except Exception:
            pass

    def _open_rewind_picker(self) -> None:
        """Open Grok-style rewind list (one row per user prompt)."""
        if self._chat_busy:
            self._show_footer_hint("Agent 运行中，无法 rewind", seconds=1.2)
            return
        self._close_slash_dropdown()
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
            items = Workspace(root).list_user_rewind_points()
        except Exception:
            items = []
        if not items:
            self._show_footer_hint("没有可回退的用户消息", seconds=1.2)
            return
        self._rewind_items = items
        # Default highlight: newest (index 0 in newest-first list).
        self._rewind_selected = 0
        self._rewind_open = True
        self._paint_rewind_picker()
        try:
            self.query_one("#cmd-input", PromptField).focus()
        except Exception:
            pass
        self._show_footer_hint("Rewind · Enter 确认 · Esc 取消", seconds=2.0)

    def _paint_rewind_picker(self) -> None:
        from room_tui.llm.rewind import format_rewind_dropdown

        sug = self.query_one("#rewind-suggest", Static)
        if not self._rewind_open or not self._rewind_items:
            self._close_rewind_picker()
            return
        w = 72
        try:
            w = max(40, int(self.size.width or 80) - 4)
        except Exception:
            pass
        text, rows = format_rewind_dropdown(
            self._rewind_items,
            selected=self._rewind_selected,
            max_rows=10,
            width=w,
        )
        sug.update(text)
        sug.add_class("active")
        sug.styles.display = "block"
        sug.styles.height = "auto"
        sug.styles.min_height = rows
        sug.styles.max_height = max(rows, 14)
        try:
            self.refresh(layout=True)
        except Exception:
            pass

    def _rewind_move_selection(self, direction: int) -> None:
        """direction: -1 up / +1 down — move highlight on screen (same as slash).

        List is newest-first (index 0 at top). Up decreases index, Down increases.
        """
        if not self._rewind_open or not self._rewind_items:
            return
        n = len(self._rewind_items)
        self._rewind_selected = (self._rewind_selected + direction) % n
        self._paint_rewind_picker()

    def _confirm_rewind(self) -> None:
        """Apply selected rewind point: truncate UI + agent session to before it."""
        from room_tui.llm.rewind import (
            resolve_rewind_selection,
            truncate_pi_session_before_user,
        )
        from room_tui.llm.pi_runner import PiRunner

        if not self._rewind_open or not self._rewind_items:
            return
        point = resolve_rewind_selection(
            self._rewind_items, self._rewind_selected
        )
        self._close_rewind_picker()
        if not point:
            return

        hist_idx = int(point.get("history_index") or 0)
        user_ord = int(point.get("user_ordinal") or 0)
        selected_text = str(point.get("text") or "")

        app: "RoomApp" = self.app  # type: ignore[assignment]
        root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
        ws = Workspace(root)

        try:
            kept = ws.truncate_chat_before_user(hist_idx)
        except Exception as e:
            self._turn_system(f"Rewind 失败: {e}", error=True)
            return

        # Best-effort: truncate Pi agent session to the same user-turn prefix.
        try:
            truncate_pi_session_before_user(
                PiRunner.agent_session_dir(root),
                keep_user_count=user_ord,
                session_id=PiRunner.CHAT_SESSION_ID,
            )
        except Exception:
            pass

        # Rebuild scrollback from kept history.
        self._end_user_pin(keep_pad=False)
        self._clear_live_step()
        self._expandables.clear()
        self._clear_user_sections()
        self._agent_last_thought_s = None
        try:
            log = self.query_one("#msg-log", SmoothRichLog)
            log.clear()
            log.auto_scroll = False
        except Exception:
            pass

        prev = self._restoring_history
        self._restoring_history = True
        try:
            for row in kept:
                self._replay_history_entry(
                    str(row.get("role") or "system"),
                    str(row.get("text") or ""),
                    blocks=row.get("blocks")
                    if isinstance(row.get("blocks"), list)
                    else None,
                    ts=str(row.get("ts") or ""),
                )
        finally:
            self._restoring_history = prev

        # Rebuild ↑/↓ prompt history from kept user rows.
        self._prompt_hist = PromptHistoryNav()
        user_prompts = [
            str(r.get("text") or "")
            for r in kept
            if str(r.get("role") or "") == "user" and str(r.get("text") or "").strip()
        ]
        if user_prompts:
            self._prompt_hist.seed(user_prompts)

        # Grok: after rewind, put the discarded prompt back so you can re-edit.
        # Do NOT append a system row into the message list — only fill the input
        # and show a transient footer hint (matches Grok Build UX).
        self._clear_composer()
        if selected_text:
            self._set_composer_text(selected_text)
        try:
            self.query_one("#cmd-input", PromptField).focus()
        except Exception:
            pass

        self._show_footer_hint("Rewound", seconds=1.2)

    def _slash_move_selection(self, delta: int) -> None:
        if not self._slash_matches:
            return
        n = len(self._slash_matches)
        self._slash_selected = (self._slash_selected + delta) % n
        self._slash_cycle = self._slash_selected
        # Re-paint dropdown with new highlight
        self._paint_slash_dropdown()

    def _slash_accept_selected(self, *, run: bool = False) -> None:
        """Tab: fill prompt with selected; Enter path may set run=True later."""
        if not self._slash_matches:
            return
        sel = max(0, min(self._slash_selected, len(self._slash_matches) - 1))
        m = self._slash_matches[sel]
        inp = self.query_one("#cmd-input", PromptField)
        line = inp.value or ""

        if self._slash_mode == "arg":
            # Empty-model guide / hint rows → open 连接模型
            if (
                m.kind == "hint"
                or (m.name or "").startswith("__")
                or not self._slash_matches
            ):
                body0 = line[1:] if line.startswith("/") else line
                if body0.strip().lower().startswith(("model", "m ", "m\t")) or (
                    body0.strip().lower() in ("model", "m", "models")
                ):
                    self._close_slash_dropdown()
                    self._open_model_setup()
                    return
            # /model <spec>  or  /skill <name>
            body = line[1:] if line.startswith("/") else line
            cmd, _, _arg = body.partition(" ")
            cmd = cmd.strip() or "model"
            new_val = f"/{cmd} {m.name} "
            inp.set_value(new_val)
            self._update_slash_suggest(new_val)
            return

        # Command/skill name stage
        new_val = f"/{m.name} "
        inp.set_value(new_val)
        self._slash_cycle = sel
        self._update_slash_suggest(new_val)
        if run:
            # Execute immediately (used if we ever intercept Enter)
            self._on_submit(PromptField.Submitted(inp, new_val.rstrip()))

    def _slash_tab_complete(self) -> None:
        """Tab without open selection: LCP / unique complete; with selection: accept."""
        if self._slash_open and self._slash_matches:
            self._slash_accept_selected(run=False)
            return
        inp = self.query_one("#cmd-input", PromptField)
        val = self._composer_text() if hasattr(self, "_composer_text") else (inp.value or "")
        if not (inp.value or "").startswith("/") and not val.startswith("/"):
            return
        line = inp.value or ""
        if not line.startswith("/"):
            line = val if val.startswith("/") else line
        new_val, matches = complete_slash(line)
        if new_val is None:
            matches = match_slash(line)
            self._update_slash_suggest(line, matches=matches)
            if not matches:
                self._show_footer_hint("无匹配命令/Skill", seconds=1.0)
            return
        # Cycle among matches when already fully typed one of them
        if line.rstrip() in {f"/{m.name}" for m in matches} and len(matches) > 1:
            self._slash_cycle = (self._slash_cycle + 1) % len(matches)
            m = matches[self._slash_cycle]
            new_val = f"/{m.name} "
            self._slash_selected = self._slash_cycle
        else:
            self._slash_cycle = 0
        inp.set_value(new_val)
        self._update_slash_suggest(new_val, matches=matches)

    def _show_slash_panel(self, text: str, rows: int) -> None:
        """Force-visible multi-line slash panel (never a blank 1px strip)."""
        sug = self.query_one("#slash-suggest", Static)
        # Ensure non-empty high-contrast content
        body = (text or "").strip() or "[bold]（无内容）[/bold]"
        sug.update(body)
        sug.add_class("active")
        sug.styles.display = "block"
        # CSS max-height is 16; keep room for empty-state copy (5–7 lines)
        h = max(3, min(int(rows) if rows else 3, 16))
        sug.styles.height = h
        sug.styles.min_height = h
        sug.styles.max_height = h
        try:
            self.refresh(layout=True)
        except Exception:
            pass

    def _paint_model_empty_slash(self) -> None:
        """Explicit empty-model guide — do not depend on catalog completion."""
        from room_tui.slash import _format_model_empty_dropdown

        text, rows = _format_model_empty_dropdown()
        self._slash_matches = []  # no selection; Enter handled specially
        self._slash_mode = "arg"
        self._slash_open = True
        self._slash_selected = 0
        # Synthetic hint so Enter still opens setup
        from room_tui.slash import _empty_model_hints

        self._slash_matches = list(_empty_model_hints())
        self._show_slash_panel(text, max(rows, 6))

    def _paint_slash_dropdown(self) -> None:
        """Render multi-line Grok-style list into #slash-suggest."""
        ms = self._slash_matches
        if not self._slash_open or not ms:
            self._close_slash_dropdown()
            return
        sel = max(0, min(self._slash_selected, len(ms) - 1))
        if self._slash_mode == "arg":
            if ms and ms[0].kind == "skill":
                kind = "skill"
            elif ms and ms[0].kind in ("model", "hint"):
                kind = "model"
            else:
                # Heuristic: /model completion names look like provider/model
                kind = "model" if any("/" in (x.name or "") for x in ms[:3]) else "arg"
            # All hints / no real models → dedicated empty panel (never blank bar)
            if kind == "model" and all(m.kind == "hint" for m in ms):
                self._paint_model_empty_slash()
                return
            cur = ""
            try:
                cur = self._current_llm_label()
            except Exception:
                cur = ""
            text, rows = format_arg_dropdown(
                ms,
                selected=sel,
                kind_label=kind,
                current_spec=cur if cur not in ("—", "") else "",
            )
        else:
            text, rows = format_dropdown(ms, selected=sel)
        self._show_slash_panel(text, rows)

    def _update_slash_suggest(
        self, value: str, matches: list | None = None
    ) -> None:
        """Grok multi-line dropdown: builtins + skills, ↑↓ selected row."""
        sug = self.query_one("#slash-suggest", Static)
        line = self.query_one("#cmd-input", PromptField).value or ""
        v = line if line.startswith("/") else (value if value.startswith("/") else "")
        if not v.startswith("/"):
            self._close_slash_dropdown()
            return

        body = v[1:]
        # Argument stage: /model x  or  /skill name
        if " " in body.strip() or (body.endswith(" ") and body.strip()):
            cmd, _, arg = body.partition(" ")
            cmd_l = cmd.strip().lower()
            if cmd_l in ("model", "models", "m"):
                # Always paint something visible for /model  (never a blank strip)
                try:
                    from room_tui.slash import _complete_model_arg

                    _, arg_matches = _complete_model_arg(arg)
                    real = [
                        m
                        for m in (arg_matches or [])
                        if getattr(m, "kind", "") == "model"
                    ]
                    if not real:
                        self._paint_model_empty_slash()
                        return
                    prev_names = [m.name for m in self._slash_matches]
                    new_names = [m.name for m in real]
                    if prev_names != new_names or self._slash_mode != "arg":
                        self._slash_selected = 0
                    self._slash_matches = list(real)
                    self._slash_mode = "arg"
                    self._slash_open = True
                    self._paint_slash_dropdown()
                    return
                except Exception:
                    self._paint_model_empty_slash()
                    return
            if cmd_l in ("skill", "sk"):
                _, arg_matches = complete_slash(v)
                if arg_matches:
                    prev_names = [m.name for m in self._slash_matches]
                    new_names = [m.name for m in arg_matches]
                    if prev_names != new_names or self._slash_mode != "arg":
                        self._slash_selected = 0
                    self._slash_matches = list(arg_matches)
                    self._slash_mode = "arg"
                    self._slash_open = True
                    self._paint_slash_dropdown()
                    return
            item = resolve_slash_token(cmd)
            if item is not None:
                # Command resolved with free-form args (e.g. /paper-derived do X)
                # Show single-row confirm, not a full match list
                kind = "skill" if item.kind == "skill" else "cmd"
                sug.update(
                    f"[{COLOR_BRAND}]/{item.name}[/{COLOR_BRAND}]  "
                    f"[dim]{kind}[/dim]  —  {item.description}"
                )
                sug.add_class("active")
                sug.styles.display = "block"
                sug.styles.height = 1
                sug.styles.min_height = 1
                sug.styles.max_height = 1
                self._slash_open = False
                self._slash_matches = [item]
                self._slash_mode = "cmd"
                return
            self._close_slash_dropdown()
            return

        ms = list(matches) if matches is not None else match_slash(v)
        if not ms:
            self._slash_matches = []
            self._slash_open = True
            self._slash_mode = "cmd"
            sug.update("[dim]无匹配命令或 Skill  ·  /help[/dim]")
            sug.add_class("active")
            sug.styles.display = "block"
            sug.styles.height = 1
            sug.styles.min_height = 1
            sug.styles.max_height = 1
            return

        # Keep selection if filter still contains previous pick
        prev = None
        if self._slash_matches and 0 <= self._slash_selected < len(self._slash_matches):
            prev = self._slash_matches[self._slash_selected].name
        self._slash_matches = ms
        self._slash_mode = "cmd"
        self._slash_open = True
        if prev:
            for i, m in enumerate(ms):
                if m.name == prev:
                    self._slash_selected = i
                    break
            else:
                self._slash_selected = 0
        else:
            self._slash_selected = 0
        self._paint_slash_dropdown()

    @on(PromptField.Changed, "#cmd-input")
    def _on_input_changed(self, event: PromptField.Changed) -> None:
        # During /new confirm, composer holds the output filename — no slash menu.
        if self._new_open:
            self._close_slash_dropdown()
            if self._new_step == "confirm":
                self._new_output = self._composer_text().strip() or self._new_output
                self._paint_new_flow()
            if not self._footer_hint:
                self._paint_footer()
            return
        self._update_slash_suggest(self._composer_text())
        # Contextual footer keys (slash / draft / idle).
        if not self._footer_hint:
            self._paint_footer()

    @on(PromptField.NewlineRequest, "#cmd-input")
    def _on_prompt_newline(self, event: PromptField.NewlineRequest) -> None:
        """Soft-break already applied in PromptField; refresh slash + layout."""
        event.stop()
        self._update_slash_suggest(self._composer_text())
        try:
            self.refresh(layout=True)
        except Exception:
            pass

    @on(PromptField.Navigate, "#cmd-input")
    def _on_prompt_navigate(self, event: PromptField.Navigate) -> None:
        """↑↓: new-run → rewind → history while browsing → slash → prompt history."""
        if self._new_open:
            self._new_move_selection(event.direction)
            event.stop()
            return
        if self._rewind_open and self._rewind_items:
            self._rewind_move_selection(event.direction)
            event.stop()
            return
        # Recalling a `/…` entry may open the slash menu — keep arrows on history.
        if self._prompt_hist.browsing:
            if self._prompt_history_navigate(event.direction):
                event.stop()
            return
        if self._slash_open and self._slash_matches:
            self._slash_move_selection(event.direction)
            event.stop()
            return
        if self._prompt_history_navigate(event.direction):
            event.stop()

    @on(PromptField.Submitted, "#cmd-input")
    def _on_submit(self, event: PromptField.Submitted) -> None:
        # Inline /new flow takes Enter first.
        if self._new_open:
            event.stop()
            self._new_on_enter()
            return
        # Rewind picker: Enter confirms selection (Grok).
        if self._rewind_open and self._rewind_items:
            event.stop()
            self._confirm_rewind()
            return

        raw = self._composer_text().strip()

        # Expand partial slash token / arg to the highlighted dropdown row (Grok).
        if self._slash_open and self._slash_matches and raw.startswith("/"):
            sel = max(0, min(self._slash_selected, len(self._slash_matches) - 1))
            chosen = self._slash_matches[sel]
            if self._slash_mode == "arg":
                body = raw[1:]
                cmd, _, arg = body.partition(" ")
                cmd = cmd.strip()
                arg_tok = arg.strip().split(" ", 1)[0] if arg.strip() else ""
                # Replace partial arg with selected name; keep trailing prompt if any
                rest = ""
                if arg.strip() and " " in arg.strip():
                    rest = " " + arg.strip().split(" ", 1)[1]
                if not arg_tok or arg_tok.lower() != chosen.name.lower():
                    raw = f"/{cmd} {chosen.name}{rest}".rstrip()
            elif " " not in raw.strip():
                token = raw[1:].lower()
                exact = any(
                    token == f.lower() for m in self._slash_matches for f in m.forms
                )
                if not exact:
                    raw = f"/{chosen.name}"
                elif token != chosen.name.lower() and token not in {
                    a.lower() for a in chosen.aliases
                }:
                    # Exact match of a different item than selected → honor selection
                    raw = f"/{chosen.name}"

        if raw:
            # Remember for ↑/↓ (including slash commands the user ran).
            self._prompt_hist.push(raw)
        self._clear_composer()
        self._close_slash_dropdown()
        if not raw:
            return
        # Chat turns: pin new user message to top of viewport (Grok Build).
        slash_or_meta = raw.startswith("/") or raw.lower() in (
            "new", "continue", "help", "quit", "exit",
            "status", "clear", "refresh", "rewind", "rw", "undo",
        )
        if slash_or_meta:
            cmd_tok = (
                raw[1:].strip().split()[0].lower()
                if raw.startswith("/")
                else raw.lower()
            )
            slash_line = raw if raw.startswith("/") else f"/{raw.lower()}"
            # UI-only slash: open pickers without echoing as a user chat turn (Grok).
            if cmd_tok in ("rewind", "rw", "undo"):
                self._dispatch_slash(slash_line)
                return
            # Bare /model (or /model pick) → model picker, never "send" as chat.
            if cmd_tok in ("model", "models", "m"):
                body = slash_line[1:].strip()
                _, _, marg = body.partition(" ")
                marg = marg.strip().lower()
                if not marg or marg in ("pick", "ui", "menu", "select"):
                    self._dispatch_slash(slash_line)
                    return
            # Pipeline slash (/continue, /template register): pin like chat so
            # live steps stay under the command. Instant slash: scroll into view
            # (user write uses scroll_end=False — without this, long history
            # leaves the new command + results below the fold).
            pin = self._slash_should_pin(cmd_tok, raw)
            # Paint the user row *now*, then defer slash work so a slow
            # preflight (e.g. template_exists → paper-derived subprocess)
            # cannot block the first frame (1–2s “prompt not showing”).
            self._turn_user(raw, pin_to_top=pin)
            if not pin and not self._restoring_history:
                self._scroll_msg_log_to_end()
            line = slash_line
            need_end = not pin and not self._restoring_history

            def _run_slash() -> None:
                self._dispatch_slash(line)
                if need_end:
                    self._scroll_msg_log_to_end()

            self.call_after_refresh(_run_slash)
            return
        # Grok Build: if a turn/doc-gen is in flight, queue the message
        # instead of dropping or rejecting it.
        self._start_chat_turn(raw)

    def _start_chat_turn(
        self,
        text: str,
        *,
        skill_name: str | None = None,
        paint_user: bool = True,
    ) -> None:
        """Start an agent turn now, or FIFO-queue if busy (Grok Build)."""
        text = (text or "").strip()
        if not text:
            return
        if self._is_send_busy():
            self._enqueue_chat_message(
                text, skill_name=skill_name, paint_user=paint_user
            )
            return
        if paint_user:
            self._turn_user(text, pin_to_top=True)
        self.run_worker(
            self._chat_turn(text, skill_name=skill_name, from_queue=not paint_user),
            exclusive=False,
        )

    def _is_send_busy(self) -> bool:
        """True when a new free-form send must wait (agent turn or doc pipeline)."""
        if self._chat_busy:
            return True
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            return bool(getattr(app.orch.state, "running", False))
        except Exception:
            return False

    def _enqueue_chat_message(
        self,
        text: str,
        *,
        skill_name: str | None = None,
        paint_user: bool = True,
    ) -> None:
        """Queue a free-form message for after the current run (Grok FIFO)."""
        text = (text or "").strip()
        if not text:
            return
        # Cap runaway queues (accidental paste/spam while agent runs long).
        max_q = 20
        if len(self._msg_queue) >= max_q:
            self._show_footer_hint(
                f"队列已满（{max_q}）· 请等待当前任务结束", seconds=2.0
            )
            return
        painted = False
        if paint_user:
            # Show the user row immediately so it feels accepted, not lost.
            self._turn_user(text, pin_to_top=False)
            painted = True
        self._msg_queue.append(
            {
                "text": text,
                "skill_name": skill_name,
                "painted": painted,
            }
        )
        n = len(self._msg_queue)
        self._show_footer_hint(
            f"已排队 · 当前结束后发送（队列 {n}）",
            seconds=2.5,
        )
        if not self._footer_hint:
            self._paint_footer()

    def _pump_message_queue(self) -> None:
        """Start the next queued free-form turn if idle (FIFO)."""
        if self._is_send_busy():
            return
        if not self._msg_queue:
            return
        item = self._msg_queue.pop(0)
        text = str(item.get("text") or "").strip()
        skill = item.get("skill_name")
        skill_s = str(skill).strip() if skill else None
        painted = bool(item.get("painted"))
        if not text:
            self.call_later(self._pump_message_queue)
            return
        # Already painted at enqueue time → do not duplicate user bubble.
        # Not painted (rare) → show user row then run.
        if not painted:
            self._turn_user(text, pin_to_top=True)
        self.run_worker(
            self._chat_turn(
                text,
                skill_name=skill_s,
                from_queue=painted,
            ),
            exclusive=False,
        )
        remaining = len(self._msg_queue)
        if remaining:
            self._show_footer_hint(
                f"发送队列下一条 · 还剩 {remaining}",
                seconds=1.5,
            )

    def _clear_message_queue(self, *, announce: bool = False) -> None:
        n = len(self._msg_queue)
        self._msg_queue.clear()
        if announce and n:
            self._show_footer_hint(f"已清空队列（{n}）", seconds=1.5)

    def _dispatch_slash(self, raw: str) -> None:
        body = raw[1:].strip() if raw.startswith("/") else raw
        cmd, _, arg = body.partition(" ")
        cmd_raw = cmd.strip()
        cmd = cmd_raw.lower()
        arg = arg.strip()

        # Resolve: exact builtin/skill, then unique prefix among all slash items
        item = resolve_slash_token(cmd_raw)
        if item is None and cmd:
            matches = match_slash("/" + cmd)
            if len(matches) == 1:
                item = matches[0]
            elif matches:
                # Prefer exact form match among hits
                for m in matches:
                    if cmd in {f.lower() for f in m.forms}:
                        item = m
                        break

        # First-class skill slash: /paper-derived [prompt]  (Grok-style)
        if item is not None and item.kind == "skill":
            self._invoke_skill_slash(item.name, arg, skill_path=item.skill_path)
            return

        if item is None and cmd:
            # Maybe user typed a skill that only matches as contain
            from room_tui.pi_catalog import find_skill

            sk = find_skill(cmd_raw)
            if sk:
                self._invoke_skill_slash(sk.name, arg)
                return
            self._turn_system(
                f"未知 /{cmd_raw}  ·  Tab 查看命令与 Skills  ·  /help",
                error=True,
            )
            return

        primary = item.name if item else cmd
        # normalize aliases to primary builtin name
        cmd = primary

        if cmd == "help":
            invalidate_skill_cache()
            self._write(help_text())
            self._write("")
        elif cmd == "new":
            self.action_new_run()
        elif cmd == "continue":
            self.action_continue_run()
        elif cmd == "pause":
            self.action_pause()
        elif cmd == "cancel":
            self.action_cancel_run()
        elif cmd == "status":
            self._cmd_status()
        elif cmd in ("model", "models"):
            self._cmd_model(arg)
        elif cmd in ("setup", "auth", "login"):
            self._open_model_setup()
        elif cmd in ("template", "templates", "tpl"):
            self._cmd_template(arg)
        elif cmd in ("skills", "skill-list"):
            invalidate_skill_cache()
            self._cmd_skills_list()
        elif cmd == "skill":
            self._cmd_skill(arg)
        elif cmd == "refresh":
            self.action_refresh()
        elif cmd == "clear":
            self.action_clear_scrollback()
        elif cmd in ("rewind", "rw", "undo"):
            self._open_rewind_picker()
        elif cmd == "quit":
            self.app.exit()
        else:
            self._turn_system(f"未知命令 /{cmd}  ·  输入 /help", error=True)

    def _invoke_skill_slash(
        self, name: str, prompt: str = "", *, skill_path: str = ""
    ) -> None:
        """Grok-style: ``/skill-name`` enables skill; with args runs Agent immediately."""
        from room_tui.config import save_config
        from room_tui.pi_catalog import find_skill

        app: "RoomApp" = self.app  # type: ignore[assignment]
        # Strip skill: prefix used on builtin collisions
        bare = name.removeprefix("skill:")
        info = find_skill(bare)
        if not info and skill_path:
            from room_tui.pi_catalog import SkillInfo
            from pathlib import Path as _P

            p = _P(skill_path)
            info = SkillInfo(name=bare, path=p, description="")
        if not info:
            self._turn_system(f"未找到 Skill「{bare}」", error=True)
            return
        app.cfg.active_skill = info.name
        try:
            save_config(app.cfg)
        except Exception:
            pass
        ver = f" v{info.version}" if info.version else ""
        self._turn_system(f"Skill  /{info.name}{ver}")
        if info.description:
            self._turn_system(info.description[:180])
        if prompt.strip():
            # Slash path already painted `/skill …` as the user row.
            self._start_chat_turn(
                prompt.strip(), skill_name=info.name, paint_user=False
            )
        else:
            self._turn_system(
                "已启用 — 直接输入问题，或 /"
                + info.name
                + " <问题> 立即调用  ·  /skill clear 清除"
            )

    def _refresh_model_status(
        self, *, announce: bool | str = False
    ) -> None:
        """Recompute model readiness for footer + chat gate.

        Never writes model / runtime / catalog tips into the message list.
        (Product: entrance notices are task-status only; model lives in footer.)
        *announce* is ignored for scrollback — kept for call-site compat.
        """
        del announce  # no message-list model banners
        from room_tui.pi_catalog import check_model_status

        app: "RoomApp" = self.app  # type: ignore[assignment]
        st = check_model_status(
            app.cfg.provider,
            app.cfg.model,
            pi_bin=app.cfg.pi_bin,
        )
        self._model_ok = st.ok
        self._model_issue = st.reason
        self._paint_footer()

    async def _refresh_model_status_async(self) -> None:
        """Same gate as ``_refresh_model_status`` but off the event loop.

        ``check_model_status`` may spawn ``pi --list-models`` — blocking the
        loop with that freezes every frame (spinner included) after submit.
        """
        import asyncio

        from room_tui.pi_catalog import check_model_status

        app: "RoomApp" = self.app  # type: ignore[assignment]
        st = await asyncio.to_thread(
            lambda: check_model_status(
                app.cfg.provider,
                app.cfg.model,
                pi_bin=app.cfg.pi_bin,
            )
        )
        self._model_ok = st.ok
        self._model_issue = st.reason
        self._paint_footer()

    def action_model_setup(self) -> None:
        """Ctrl+M — 连接模型（配置服务商 / 密钥 / 本机）。"""
        self._open_model_setup()

    def action_model_picker(self) -> None:
        """Open model switcher (also used by legacy bindings)."""
        self._open_model_picker()

    def _open_model_picker(self) -> None:
        """选择模型 — switch among catalog models (``/model``)."""
        from room_tui.widgets.model_picker import ModelPickerScreen

        app: "RoomApp" = self.app  # type: ignore[assignment]
        self.app.push_screen(
            ModelPickerScreen(
                pi_bin=app.cfg.pi_bin,
                current_spec=self._current_llm_label(),
                auto_setup_if_empty=True,
            ),
            self._on_model_picked,
        )

    def _open_model_setup(self) -> None:
        """连接模型：选服务商、填密钥 / 本机服务（Ctrl+M / /setup）。"""
        from room_tui.widgets.model_setup import ModelSetupScreen

        app: "RoomApp" = self.app  # type: ignore[assignment]
        self.app.push_screen(
            ModelSetupScreen(pi_bin=app.cfg.pi_bin),
            self._on_model_picked,
        )

    def _on_model_picked(self, choice: object) -> None:
        from room_tui.pi_catalog import ModelInfo

        if not isinstance(choice, ModelInfo):
            self._show_footer_hint("已取消", seconds=1.0)
            self._refresh_model_status()
            self._focus_prompt()
            return
        if not (choice.model or "").strip():
            # Config removed or cloud key saved without picking (use /model next)
            self._refresh_model_status()
            if (choice.provider or "").strip():
                self._append_notice_block(
                    f"已连接 {choice.provider}",
                    "切换模型: /model  ·  列表: /model list",
                )
            else:
                self._append_notice_block("已清除该服务商配置", "Ctrl+M 重新连接模型")
            self._focus_prompt()
            return
        self._apply_model(
            choice.provider,
            choice.model,
            source="setup",
            skip_validate=True,  # just configured; catalog may lag one tick
        )
        self._focus_prompt()

    def _apply_model(
        self,
        provider: str,
        model: str,
        *,
        source: str = "cmd",
        skip_validate: bool = False,
    ) -> bool:
        """Persist model; return False if rejected by catalog validation."""
        from room_tui.config import save_config
        from room_tui.pi_catalog import (
            catalog_models,
            check_model_status,
            resolve_against_catalog,
        )

        app: "RoomApp" = self.app  # type: ignore[assignment]
        prov = (provider or "").strip()
        mid = (model or "").strip()
        if not mid:
            self._turn_system(
                "用法: /model 打开选择器  ·  /model <provider/model>  ·  Ctrl+M 连接模型",
                error=True,
            )
            return False

        catalog = catalog_models(pi_bin=app.cfg.pi_bin)
        if catalog and not skip_validate:
            hit = resolve_against_catalog(prov, mid, catalog)
            if hit is None:
                self._turn_system(
                    f"当前 Room 不支持「{prov + '/' if prov else ''}{mid}」",
                    error=True,
                )
                self._turn_system("/model 从列表选择  ·  /model list 查看  ·  Ctrl+M 配置")
                return False
            prov, mid = hit.provider, hit.model

        app.cfg.provider = prov
        app.cfg.model = mid
        app.orch.state.provider = prov
        app.orch.state.model = mid
        try:
            path = save_config(app.cfg)
            where = f"  ·  写入 {path}"
        except Exception as e:
            where = f"  ·  未写入配置: {e}"
        st = check_model_status(prov, mid, pi_bin=app.cfg.pi_bin, catalog=catalog)
        self._model_ok = st.ok
        self._model_issue = st.reason
        src = (
            "选择器"
            if source == "picker"
            else ("连接" if source == "setup" else "命令")
        )
        self._turn_system(f"已切换模型 → {st.label or self._current_llm_label()}  ({src}){where}")
        self._paint_footer()
        return True

    @on(events.Click, "#status-right")
    def _on_status_right_click(self, event: events.Click) -> None:
        """Click footer model chip → switch model (/model)."""
        event.stop()
        self._open_model_picker()

    def _cmd_model(self, arg: str) -> None:
        """ /model | /model pick | /model list [q] | /model <provider/model> """
        from room_tui.pi_catalog import catalog_models, list_models, parse_model_spec

        app: "RoomApp" = self.app  # type: ignore[assignment]
        a = (arg or "").strip()
        cur = self._current_llm_label()

        # Bare `/model` → 选择模型 (switch). Configure providers with Ctrl+M.
        if not a:
            self._open_model_picker()
            return

        low = a.lower()
        if low in ("setup", "auth", "login", "key", "keys", "connect"):
            self._open_model_setup()
            return
        if low in ("pick", "ui", "menu", "select", "status", "show"):
            if low in ("status", "show"):
                think = getattr(app.cfg, "agent_thinking", "") or "—"
                self._refresh_model_status(announce=False)
                if self._model_ok:
                    self._turn_system(f"当前模型  {cur}  ·  agent_thinking={think}")
                else:
                    self._turn_system(
                        f"当前模型  {cur}  ·  ⚠ {self._model_issue or '未就绪'}",
                        error=True,
                    )
                self._turn_system(
                    "切换: /model  ·  配置: Ctrl+M  ·  列表: /model list"
                )
                return
            self._open_model_picker()
            return

        if low in ("list", "ls") or low.startswith("list ") or low.startswith("ls "):
            q = ""
            if " " in a:
                q = a.split(" ", 1)[1].strip()
            # Source of truth: what this pi binary actually supports
            models = catalog_models(q, pi_bin=app.cfg.pi_bin)
            if not models:
                models = list_models(q, pi_bin=app.cfg.pi_bin, prefer_enabled=False)
            if not models:
                self._turn_system(
                    "未找到可用模型（检查鉴权 · Ctrl+M 连接模型）",
                    error=True,
                )
                return
            lines = [f"Room 可用模型（{len(models)}）  当前: {cur}"]
            for m in models[:50]:
                mark = "●" if m.spec == cur or m.model == app.cfg.model else " "
                lines.append(f"  {mark}  {m.spec}")
            if len(models) > 50:
                lines.append(f"  … +{len(models) - 50}  ·  /model list <关键词>")
            lines.append("")
            lines.append("切换: /model  ·  指定: /model <provider/model>  ·  配置: Ctrl+M")
            self._turn_system("\n".join(lines))
            return

        # set model
        prov, model = parse_model_spec(a)
        if not model and not prov:
            self._turn_system("用法: /model <provider/model>", error=True)
            return
        if not model:
            model = prov
            prov = ""
        # Resolve bare model against catalog
        if not prov:
            for m in catalog_models(model, pi_bin=app.cfg.pi_bin):
                if (
                    m.model == model
                    or m.model.endswith("/" + model)
                    or m.spec.endswith(model)
                ):
                    prov, model = m.provider, m.model
                    break
        self._apply_model(prov, model, source="cmd")

    def _template_help_text(self) -> str:
        return (
            "【模板 /template】主入口\n"
            "\n"
            "  /template                 列表（无模板时显示帮助）\n"
            "  /template list            已注册模板\n"
            "  /template show <id>       模板详情\n"
            "  /template register <样例> [名称]\n"
            "      完整注册（推荐质量）: 引擎构造 prompt → Room Agent 分析 → 写入模板库\n"
            "  /template register --fast <样例> [名称]\n"
            "      快速注册: 结构扫描 + 三次小 LLM（register-auto，适合有标题/编号的样例）\n"
            "  /template delete <id>     删除模板\n"
            "  /template help            本说明\n"
            "\n"
            "注册完成后用 /new：① 选模板 → ② 勾资料 → ③ Enter 开始（主界面内联，无跳屏）。\n"
            "快速模式可选 ROOM_API_BASE；失败可改完整注册。"
        )

    def _cmd_template(self, arg: str) -> None:
        """ /template hub: list | register [--fast] | show | help """
        a = (arg or "").strip()
        low = a.lower()

        if low in ("help", "h", "?"):
            self._append_notice_block(
                *self._template_help_text().splitlines(), persist=False
            )
            return

        # Bare /template → list (or help when empty)
        if not a or low in ("list", "ls") or low.startswith("list ") or low.startswith("ls "):
            self.run_worker(self._template_list_async(), exclusive=False)
            return

        if low.startswith("show ") or low.startswith("info "):
            tid = a.split(None, 1)[1].strip()
            self.run_worker(self._template_show_async(tid), exclusive=False)
            return

        if low.startswith("delete ") or low.startswith("del ") or low.startswith("rm "):
            tid = a.split(None, 1)[1].strip()
            self.run_worker(self._template_delete_async(tid), exclusive=False)
            return

        # /template register-fast …  or  /template fast …
        if low.startswith("register-fast") or low == "fast" or low.startswith("fast "):
            if low.startswith("register-fast"):
                rest = a[len("register-fast") :].strip()
            else:
                rest = a[4:].strip() if low.startswith("fast") else a
            self._template_start_register(rest, fast=True)
            return

        if low == "register" or low.startswith("register "):
            rest = a[len("register") :].strip()
            self._template_start_register(rest, fast=None)  # auto or flag inside
            return

        self._turn_system("未知子命令  ·  /template help", error=True)

    def _template_start_register(self, rest: str, *, fast: bool | None) -> None:
        """Parse register args and kick off full or fast registration.

        *fast*:
          - True  → force register-auto
          - False → force full pi path
          - None  → honor --fast/--full flags; default full
        """
        rest = (rest or "").strip()
        if not rest:
            self._turn_system(
                "用法:\n"
                "  /template register <样例> [名称]           完整（Room Agent）\n"
                "  /template register --fast <样例> [名称]    快速\n"
                "  /template register-fast <样例> [名称]      同上",
                error=True,
            )
            return

        tokens = rest.split()
        flags: set[str] = set()
        path_toks: list[str] = []
        name_parts: list[str] = []
        name_from_flag = ""
        j = 0
        while j < len(tokens):
            t = tokens[j]
            tl = t.lower()
            if tl in ("--fast", "-f", "--quick"):
                flags.add("fast")
                j += 1
                continue
            if tl in ("--full", "--quality"):
                flags.add("full")
                j += 1
                continue
            if tl in ("-n", "--name") and j + 1 < len(tokens):
                name_from_flag = tokens[j + 1].strip().strip("\"'")
                j += 2
                continue
            if not path_toks:
                path_toks.append(t)
            else:
                name_parts.append(t)
            j += 1

        sample_s = (path_toks[0] if path_toks else "").strip().strip("\"'")
        name = name_from_flag or (
            " ".join(name_parts).strip().strip("\"'") if name_parts else ""
        )
        if not sample_s:
            self._turn_system("缺少样例文件路径", error=True)
            return
        sample = self._resolve_template_sample(sample_s)
        if sample is None:
            return
        if not name:
            name = sample.stem

        # .doc on Windows is handled by paper-derived (LibreOffice / Word COM).
        # No hard block — engine returns actionable error if no converter available.

        if fast is True or "fast" in flags:
            use_fast = True
        elif fast is False or "full" in flags:
            use_fast = False
        else:
            use_fast = False
            if sample.suffix.lower() in (".md", ".markdown", ".txt"):
                self._turn_system(
                    "提示: md/txt 可用 /template register --fast … 更快；"
                    "本次使用完整注册。"
                )

        self.run_worker(
            self._template_register_async(sample.resolve(), name, fast=use_fast),
            exclusive=False,
        )

    def _resolve_template_sample(self, sample_s: str) -> Path | None:
        sample = Path(sample_s).expanduser()
        if sample.is_file():
            return sample
        app: "RoomApp" = self.app  # type: ignore[assignment]
        alt = (Path(app.cfg.workspace or Path.cwd()) / sample_s).resolve()
        if alt.is_file():
            return alt
        self._turn_system(f"找不到样例文件: {sample_s}", error=True)
        return None

    async def _template_list_async(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        try:
            rows = await app.load_templates()
        except Exception as e:
            self._turn_system(f"模板列表失败: {e}", error=True)
            return
        if not rows:
            self._append_notice_block(
                "暂无已注册模板（引擎库 ~/.paper-derived/templates 为空）",
                "样例 .doc 在工作区 ≠ 已注册；历史 tpl-xxx 会话也不算库存",
                "注册: /template register <样例文件> [名称]",
                "快速: /template register <样例> [名] --fast  ·  注册后 /new 生成",
            )
            return
        lines = [f"已注册模板（{len(rows)}）:"]
        for t in rows[:40]:
            tid = str(t.get("id") or "?")
            name = str(t.get("name") or tid)
            nsec = t.get("section_count", "?")
            lines.append(f"  {name}  [{tid}]  ·  {nsec} 节")
        if len(rows) > 40:
            lines.append(f"  … +{len(rows) - 40}")
        lines.append("注册: /template register <样例> [名]  ·  快速: --fast  ·  生成: /new")
        # Pale-yellow rail + soft fill (same as incomplete-task entrance tip).
        self._append_notice_block(*lines)

    async def _template_show_async(self, template_id: str) -> None:
        import asyncio

        app: "RoomApp" = self.app  # type: ignore[assignment]
        try:
            data = await asyncio.to_thread(app.engine.template_show, template_id)
        except Exception as e:
            self._turn_system(f"template show 失败: {e}", error=True)
            return
        if not data:
            self._turn_system(f"无数据: {template_id}", error=True)
            return
        tid = str(data.get("id") or template_id)
        name = str(data.get("name") or tid)
        desc = str(data.get("description") or "")
        secs = data.get("sections") or data.get("section_ids") or []
        n = data.get("section_count")
        if n is None and isinstance(secs, list):
            n = len(secs)
        lines = [
            f"模板  {name}",
            f"  id: {tid}",
        ]
        if desc:
            lines.append(f"  描述: {desc[:200]}")
        lines.append(f"  章节数: {n if n is not None else '?'}")
        if isinstance(secs, list) and secs:
            preview = ", ".join(str(x) for x in secs[:12])
            if len(secs) > 12:
                preview += f" … +{len(secs) - 12}"
            lines.append(f"  sections: {preview}")
        lines.append("开始生成: /new  （①选模板 ②勾资料 ③确认）")
        self._append_notice_block(*lines)

    async def _template_delete_async(self, template_id: str) -> None:
        import asyncio

        tid = (template_id or "").strip()
        if not tid:
            self._turn_system("用法: /template delete <template_id>", error=True)
            return
        app: "RoomApp" = self.app  # type: ignore[assignment]
        try:
            await asyncio.to_thread(app.engine.template_delete, tid)
        except Exception as e:
            self._turn_system(f"删除失败: {e}", error=True)
            return
        self._append_notice_block(
            f"已删除模板  [{tid}]",
            "列表: /template list  ·  新建: /new",
        )

    def _worker_event_to_ui(self, event: dict[str, Any]) -> None:
        """Forward Room Agent worker events into the live step timeline.

        Strips chat-kind so phase suffixes (模型生成 / 解析写入) paint like
        document generation, not free-form chat.
        """
        ev = dict(event)
        if str(ev.get("kind") or "") == "chat":
            ev["kind"] = "worker"
        self._on_orch_event(ev)

    async def _template_register_async(
        self, sample: Path, name: str, *, fast: bool = False
    ) -> None:
        """Register template with the same live-step chrome as /new · /continue."""
        import asyncio
        import time as _time

        if self._chat_busy:
            self._turn_system("请等待当前任务结束再注册模板。", error=True)
            return

        mode = "快速" if fast else "完整"
        self._chat_busy = True
        self._chat_cancel = asyncio.Event()
        t0 = _time.monotonic()
        # Sidebar: template-register steps (not document /new pipeline)
        self._pipe.begin_template_register()
        self._render_steps()
        # Header notice (static) + live step rows below (progress).
        self._append_notice_block(
            f"注册模板（{mode}）  「{name}」",
            f"样例  {sample.name}",
        )
        self._set_activity("注册模板", phase="thinking", reset_timer=True)

        try:
            if fast:
                await self._template_register_fast(sample, name, t0=t0)
            else:
                await self._template_register_full(sample, name, t0=t0)
        except Exception as e:
            from room_tui.engine.errors import humanize_engine_error, register_error_hints

            raw = str(e)
            # Full human line (not truncated) for the notice; brief for the step row
            detail = humanize_engine_error(
                raw,
                sample_suffix=sample.suffix,
            )
            # EngineError may carry richer stderr
            stderr = getattr(e, "stderr", "") or ""
            stdout = getattr(e, "stdout", "") or ""
            if stderr or stdout:
                detail = humanize_engine_error(
                    raw,
                    stderr=stderr,
                    stdout=stdout,
                    sample_suffix=sample.suffix,
                )
            brief = self._short_error(
                detail, max_cells=72, sample_suffix=sample.suffix
            )
            if self._live_step_active:
                self._turn_step(
                    f"失败  注册模板  ·  {brief}",
                    ok=False,
                    key=self._live_step_key or "tpl-reg",
                )
            else:
                self._turn_system(f"模板注册失败: {brief}", error=True)
            # Actionable follow-ups (id already exists is the common case)
            if "已存在" in raw or "template_id_exists" in raw or "已存在" in detail:
                self._append_notice_block(
                    "该模板 id 已在库中，无需重复注册",
                    "查看: /template list",
                    "删除后重注册: /template delete <id>",
                    "或换名称: /template register <样例> <新名称>",
                    "直接生成: /new",
                )
            else:
                lines = [f"原因: {detail}"]
                lines.extend(
                    register_error_hints(detail, sample_path=str(sample))
                )
                if fast:
                    lines.append(
                        f"可改完整注册: /template register {sample.name} {name}"
                    )
                self._append_notice_block(*lines)
        finally:
            self._chat_busy = False
            self._chat_cancel = None
            self._clear_activity()
            # Release pin from /template register so the list can free-scroll.
            self._end_user_pin()
            self._paint_footer()
            self._focus_prompt()
            try:
                self.call_later(self._pump_message_queue)
            except Exception:
                pass

    async def _template_register_full(
        self, sample: Path, name: str, *, t0: float
    ) -> None:
        import asyncio

        from room_tui.config import PiTierConfig
        from room_tui.llm.pi_runner import WorkerRequest
        from room_tui.workspace import Workspace

        self._refresh_model_status(announce=False)
        if not self._model_ok:
            raise RuntimeError(
                f"{self._model_issue or '模型未配置'} — Ctrl+M 连接 或 /model 选择后再试"
            )

        app: "RoomApp" = self.app  # type: ignore[assignment]
        ws = Workspace(Path(app.cfg.workspace or Path.cwd()))
        root = ws.root
        prompt_path = root / ".pd" / "tui" / "tmp" / f"reg-tpl-{int(t0)}.md"
        resp_path = root / ".pd" / "responses" / f"reg-tpl-{int(t0)}.json"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        resp_path.parent.mkdir(parents=True, exist_ok=True)

        # ① build prompt
        self._pipe.mark_register_step("build")
        self._render_steps()
        self._turn_step("注册模板  构造 prompt", key="tpl-reg:build")
        self._set_activity("构造 prompt", phase="thinking", reset_timer=True)
        handle = await asyncio.to_thread(
            app.engine.template_register_build,
            sample,
            name,
            prompt_path,
            description=f"Registered via Room from {sample.name}",
        )
        tok = handle.prompt_tokens or "?"
        self._pipe.mark_register_step("build", done=True)
        self._render_steps()
        self._turn_step(
            f"完成  构造 prompt  ·  tokens≈{tok}",
            ok=True,
            key="tpl-reg:build",
        )

        # ② LLM analysis (live phase via worker events)
        self._pipe.mark_register_step("analyze")
        self._render_steps()
        self._turn_step("注册模板  分析样例", key="tpl-reg:llm")
        self._set_activity("分析样例", phase="thinking", reset_timer=True)
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
                key=f"tpl-reg-{int(t0)}",
                prompt_file=handle.path,
                response_file=resp_path,
                tier=tier,
                timeout_s=float(getattr(app.cfg, "worker_timeout_s", 600) or 600),
            ),
            on_event=self._worker_event_to_ui,
            cancel=self._chat_cancel,
        )
        if not result.ok:
            raise RuntimeError(result.error or "Room Agent 分析失败")
        self._pipe.mark_register_step("analyze", done=True)
        self._render_steps()
        self._turn_step("完成  分析样例", ok=True, key="tpl-reg:llm")

        # ③ parse into library
        self._pipe.mark_register_step("write")
        self._render_steps()
        self._turn_step("注册模板  写入库", key="tpl-reg:write")
        self._set_activity("写入库", phase="parsing", reset_timer=True)
        parsed = await asyncio.to_thread(
            app.engine.template_register_parse,
            sample,
            name,
            resp_path,
        )
        self._pipe.mark_register_step("write", done=True)
        self._render_steps()
        self._turn_step("完成  写入库", ok=True, key="tpl-reg:write")
        self._template_register_done(name, parsed, t0=t0, mode="完整")

    async def _template_register_fast(
        self, sample: Path, name: str, *, t0: float
    ) -> None:
        import asyncio
        import os

        app: "RoomApp" = self.app  # type: ignore[assignment]
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
        model = (app.cfg.model or "").strip()
        # Fast path: treat as build→analyze→write in one scan
        self._pipe.mark_register_step("build")
        self._pipe.mark_register_step("build", done=True)
        self._pipe.mark_register_step("analyze")
        self._render_steps()
        self._turn_step("注册模板  快速扫描", key="tpl-reg:fast")
        self._set_activity("快速注册", phase="thinking", reset_timer=True)
        parsed = await asyncio.to_thread(
            app.engine.template_register_auto,
            sample,
            name,
            description=f"Fast register via Room from {sample.name}",
            model=model,
            api_base=api_base,
            api_key=api_key,
            compact=True,
            timeout_s=float(getattr(app.cfg, "worker_timeout_s", 600) or 600),
        )
        parsed.pop("_progress", None)
        self._pipe.mark_register_step("analyze", done=True)
        self._pipe.mark_register_step("write", done=True)
        self._render_steps()
        self._turn_step("完成  快速扫描", ok=True, key="tpl-reg:fast")
        self._template_register_done(name, parsed, t0=t0, mode="快速")

    def _template_register_done(
        self, name: str, parsed: dict, *, t0: float, mode: str
    ) -> None:
        import time as _time

        tid = str(parsed.get("template_id") or parsed.get("id") or "")
        nsec = parsed.get("sections") or parsed.get("section_count") or "?"
        if isinstance(parsed.get("section_ids"), list):
            nsec = len(parsed["section_ids"])
        elapsed = int((_time.monotonic() - t0) * 1000)
        self._pipe.mark_register_step("done", done=True)
        self._pipe.complete_all()
        self._render_steps()
        self._append_notice_block(
            f"✓ 模板已注册（{mode}）  {name}"
            + (f"  [{tid}]" if tid else "")
            + f"  ·  {nsec} 节  ·  {elapsed}ms",
            "下一步: /new  → ①选模板 ②勾资料 ③Enter 开始",
        )
        self._show_footer_hint("模板已注册 · /new 开始", seconds=2.0)

    def _cmd_skills_list(self) -> None:
        from room_tui import __version__
        from room_tui.pi_catalog import REQUIRED_SKILLS, list_skills
        from room_tui.pi_env import apply_room_pi_isolation, room_pi_skills_dir

        # Always isolate + seed before list (never send users to system ~/.pi)
        try:
            apply_room_pi_isolation(seed_skills=True)
        except Exception:
            pass

        app: "RoomApp" = self.app  # type: ignore[assignment]
        skills = list_skills()
        active = (getattr(app.cfg, "active_skill", "") or "").strip()
        room_skills = room_pi_skills_dir()
        if not skills:
            self._append_notice_block(
                f"未发现 Skills  ·  room {__version__}",
                f"扫描目录: {room_skills}",
                f"必装: {', '.join(REQUIRED_SKILLS)}",
                "修复: room skills-seed  ·  或重装 suite / room doctor",
            )
            return
        lines = [
            f"Skills（{len(skills)}）  room {__version__}  当前: {active or '（无固定启用）'}",
            f"目录: {room_skills}",
        ]
        for s in skills[:50]:
            mark = "●" if active and s.name.lower() == active.lower() else " "
            ver = f"  v{s.version}" if s.version else ""
            desc = (s.description or "")[:72]
            lines.append(f"  {mark}  {s.name}{ver}")
            if desc:
                lines.append(f"       {desc}")
        lines.append("")
        lines.append("启用: /skill <name>  ·  调用: /skill <name> <问题>  ·  清除: /skill clear")
        self._turn_assistant("\n".join(lines))

    def _cmd_skill(self, arg: str) -> None:
        """ /skill clear | /skill <name> | /skill <name> <prompt> """
        from room_tui.config import save_config
        from room_tui.pi_catalog import find_skill, list_skills

        app: "RoomApp" = self.app  # type: ignore[assignment]
        a = (arg or "").strip()
        if not a or a.lower() in ("list", "ls"):
            self._cmd_skills_list()
            return
        if a.lower() in ("clear", "off", "none", "disable"):
            app.cfg.active_skill = ""
            try:
                save_config(app.cfg)
            except Exception:
                pass
            self._turn_system("已清除固定 Skill（仍可自动发现）")
            return

        name, _, prompt = a.partition(" ")
        name = name.strip()
        prompt = prompt.strip()
        info = find_skill(name)
        if not info:
            names = ", ".join(s.name for s in list_skills()[:12])
            self._turn_system(
                f"未找到 skill「{name}」  ·  可用: {names or '（空）'}",
                error=True,
            )
            return

        app.cfg.active_skill = info.name
        try:
            save_config(app.cfg)
        except Exception:
            pass
        ver = f" v{info.version}" if info.version else ""
        self._turn_system(
            f"已启用 Skill  {info.name}{ver}  ·  {info.path.parent}"
        )
        if info.description:
            self._turn_system(info.description[:200])
        if prompt:
            # Immediate agent turn with skill forced (queue if busy).
            # Caller slash path already painted the user command row.
            self._start_chat_turn(prompt, skill_name=info.name, paint_user=False)
        else:
            self._turn_system("下一条消息将携带该 Skill；或 /skill <name> <问题> 立即提问")

    def _cmd_status(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
        ws = Workspace(Path(root))
        m = ws.load_manifest()
        st = app.orch.state
        lines = [
            f"项目    {ws.root}",
            f"运行    {'是' if st.running else '否'}",
            f"阶段    {st.phase or '—'}",
            f"进度    {st.progress or '—'}",
        ]
        if m:
            lines += [
                f"Session {m.session_id or '—'}",
                f"模板    {m.template_id or '—'}",
                f"状态    {m.status} / {m.phase}",
                f"输出    {m.output or '—'}",
            ]
        else:
            lines.append("本项目尚无任务清单")
        # Notice rail (system) — not assistant prose — so restore path matches
        # other slash results and is never mistaken for free-form chat.
        self._append_notice_block(*lines)

    def _chat_workspace_context(self, ws: Path) -> str:
        """Brief workspace facts for the agent (tools can read full files)."""
        lines: list[str] = [
            f"工作区(CWD): {ws}",
            "你是 Room Agent：可用工具与已安装 skills 完成工作区任务。",
        ]
        try:
            m = Workspace(ws).load_manifest()
        except Exception:
            m = None
        if m:
            lines.append(
                f"Room 文档任务 session={m.session_id} template={m.template_id} "
                f"status={m.status} phase={m.phase} progress={m.progress}"
            )
            if m.output:
                lines.append(f"最近输出路径: {m.output}")
        else:
            lines.append("本项目尚无 Room 文档生成任务（可用 /new 创建）。")
        tpl = ws / "template"
        if tpl.is_dir():
            names = [p.name for p in sorted(tpl.iterdir()) if p.is_file()][:12]
            if names:
                lines.append("template/ 下文件: " + ", ".join(names))
        for name in ("output.md", "软件需求规格.md"):
            p = ws / name
            if p.is_file():
                lines.append(f"存在产物文件: {p} ({p.stat().st_size} bytes)")
        return "\n".join(lines)

    async def _chat_turn(
        self,
        user_text: str,
        *,
        skill_name: str | None = None,
        from_queue: bool = False,
    ) -> None:
        """Full Pi Agent turn in the left pane (tools + session + skills)."""
        if self._chat_busy:
            # Race: another turn started; re-queue instead of dropping (Grok).
            self._enqueue_chat_message(
                user_text,
                skill_name=skill_name,
                paint_user=not from_queue,
            )
            return
        app: "RoomApp" = self.app  # type: ignore[assignment]
        if not self._bootstrap_done:
            self._turn_system("正在初始化，请稍候…", persist=False)
            return

        # Show life immediately (Grok "Waiting for response… · Ns"): the
        # preflight below spawns subprocesses (engine version, pi
        # --list-models) that take seconds on Windows — without this the UI
        # sat dead between the pinned prompt and the first Thinking… row.
        self._set_activity(
            "Waiting for response...", phase="waiting-response", reset_timer=True
        )

        # Re-probe every send so TUI matches a green `room doctor` (no stale _ready)
        try:
            ready, env_err = await app.probe_environment()
        except Exception as e:
            ready, env_err = False, str(e)
        self._ready = bool(ready)
        self._set_bottom_bar(
            self._ws_root or Path(app.cfg.workspace or Path.cwd()),
            app.cfg.model,
            ok=self._ready,
            err=env_err if not self._ready else "",
        )
        if not self._ready:
            self._clear_activity()
            reason = (env_err or self._env_err or "环境未就绪").strip()
            self._append_notice_block(
                f"无法对话 · {reason}",
                "终端: room doctor  （须 engine OK + room agent ok）",
                "引擎分支: paper-derived claude0  ·  paper-derived version",
                "若 room --version 仍是 0.1.2：PATH 指到旧 room.exe，请重装并 where room",
            )
            return

        # Gate: model must be set and known to active pi (avoids Unknown provider).
        # Off-loop: check_model_status may spawn ``pi --list-models`` (seconds
        # on Windows) — inline it froze the whole UI right after submit.
        await self._refresh_model_status_async()
        if not self._model_ok:
            self._clear_activity()
            self._turn_system(
                f"无法发送：{self._model_issue or '模型未配置'}",
                error=True,
            )
            self._turn_system(
                "切换: /model  ·  配置: Ctrl+M  ·  列表: /model list"
            )
            return

        import asyncio
        import time as _time

        from room_tui.config import PiTierConfig
        from room_tui.pi_catalog import find_skill

        self._chat_busy = True
        self._chat_cancel = asyncio.Event()
        self._agent_tool_seq = 0
        self._agent_live_tool = ""
        self._agent_live_args = None
        self._agent_streamed_tools = []
        self._agent_last_thought_s = None
        self._agent_think_t0 = _time.monotonic()
        self._agent_thinking_buf = ""
        self._agent_thinking_dirty = False
        self._agent_thinking_last_paint = 0.0
        self._agent_answer_buf = ""
        self._agent_answer_dirty = False
        self._agent_answer_last_paint = 0.0
        self._thinking_gap_above = False
        if not self._footer_hint:
            self._paint_footer()
        # Keep "Waiting for response… · Ns" until the model actually replies —
        # the first thinking/answer/tool event flips the phase and opens its
        # own live row (``_agent_on_thinking_delta`` → ``_open_thinking_live_row``).
        # Opening a Thinking… card here would claim thinking before any token
        # arrived (and flash-remove it for answers with no thinking).
        try:
            from room_tui.llm.sanitize import looks_like_tool_dump, sanitize_model_text

            ws = Path(app.cfg.workspace or Path.cwd()).resolve()
            ctx = self._chat_workspace_context(ws)
            ctx += f"\n文档流水线运行中: {app.orch.state.running}\n"
            ctx += f"模型: {self._current_llm_label()}\n"

            skill_paths: list[Path] = []
            active = (skill_name or getattr(app.cfg, "active_skill", "") or "").strip()
            if active:
                info = find_skill(active)
                if info:
                    sp = info.path.parent if info.path.is_file() else info.path
                    skill_paths.append(sp)
                    ctx += f"固定 Skill: {info.name}  ({sp})\n"
                    ctx += (
                        "请优先按该 Skill 的 SKILL.md 指令执行；"
                        "需要时用工具读取 skill 目录内文件。\n"
                    )

            def _on_chat_ev(ev: dict[str, Any]) -> None:
                try:
                    self.call_later(self._handle_event, ev)
                except Exception:
                    try:
                        self.app.call_from_thread(self._handle_event, ev)
                    except Exception:
                        pass

            timeout = float(getattr(app.cfg, "agent_timeout_s", 900.0) or 900.0)
            agent_tier = PiTierConfig(
                provider=app.cfg.provider,
                model=app.cfg.model,
                thinking=getattr(app.cfg, "agent_thinking", None) or "high",
            )
            result = await app.orch.runner.chat(
                user_text,
                system=CHAT_SYSTEM + "\n\n## Room 上下文\n" + ctx,
                tier=agent_tier,
                work_dir=ws,
                timeout_s=timeout,
                on_event=_on_chat_ev,
                cancel=self._chat_cancel,
                full_agent=True,
                skill_paths=skill_paths or None,
            )
            # Mark idle BEFORE handling result so late call_later events
            # (worker_done / progress) cannot reopen the Working bar.
            self._chat_busy = False
            self._chat_cancel = None
            self._clear_activity()
            # NB: the top-pin stays active through the final polished render
            # below — releasing it first made those writes auto-scroll to the
            # bottom and drop the pad, yanking the viewport off the pinned
            # prompt the moment a short turn finished. The ``finally`` block
            # releases the pin (keeping the pad) after rendering.
            if result.ok:
                if result.provider:
                    app.orch.state.provider = result.provider
                if result.model:
                    app.orch.state.model = result.model
                # Close Thinking… / open tool, then answer body (no "Agent 完成" doc row).
                if self._live_step_active and self._is_agent_thinking_live():
                    self._agent_finish_thinking()
                if getattr(result, "agent_turn", None) and result.agent_turn.blocks:
                    self._turn_agent(result, skip_streamed_tools=True)
                else:
                    raw = result.response_file.read_text(encoding="utf-8").strip()
                    if looks_like_tool_dump(raw):
                        text = sanitize_model_text(raw)
                        if not text:
                            self._agent_discard_live_answer()
                            if self._live_step_active:
                                self._freeze_live_step()
                            self._turn_system(
                                "模型输出了未执行的工具协议文本（异常）。"
                                "请重试；若持续出现，用 /model 切换模型或 Ctrl+M 重新连接。",
                                error=True,
                            )
                        else:
                            if self._live_step_active and self._is_agent_thinking_live():
                                self._agent_finish_thinking()
                            self._agent_discard_live_answer()
                            if self._live_step_active:
                                self._freeze_live_step()
                            self._turn_assistant(text)
                    else:
                        if self._live_step_active and self._is_agent_thinking_live():
                            self._agent_finish_thinking()
                        self._agent_discard_live_answer()
                        if self._live_step_active:
                            self._freeze_live_step()
                        self._turn_assistant(raw)
            else:
                err = result.error or "Agent 失败"
                self._agent_discard_live_answer()
                if self._live_step_active and self._is_agent_tool_live():
                    self._agent_finish_tool_row(
                        self._agent_live_tool or "tool",
                        is_error=True,
                        args=self._agent_live_args,
                    )
                elif self._live_step_active and self._is_agent_thinking_live():
                    self._agent_finish_thinking()
                if "cancel" in err.lower():
                    self._write(
                        f"[{COLOR_ERR}]✗[/{COLOR_ERR}]  "
                        f"[{COLOR_ERR}]Cancelled[/{COLOR_ERR}]"
                    )
                    self._write("")
                else:
                    self._write(
                        f"[{COLOR_ERR}]✗[/{COLOR_ERR}]  "
                        f"[{COLOR_ERR}]{self._short_error(err, max_cells=60)}[/{COLOR_ERR}]"
                    )
                    self._write("")
        except Exception as e:
            self._chat_busy = False
            self._chat_cancel = None
            self._clear_activity()
            self._write(
                f"[{COLOR_ERR}]✗[/{COLOR_ERR}]  "
                f"[{COLOR_ERR}]{str(e)[:80]}[/{COLOR_ERR}]"
            )
            self._write("")
        finally:
            self._chat_busy = False
            self._chat_cancel = None
            self._agent_live_tool = ""
            self._agent_live_args = None
            self._agent_answer_buf = ""
            self._agent_answer_dirty = False
            self._clear_activity()
            # Always release top-pin (idempotent if already released on success).
            self._end_user_pin()
            self._force_stop_spin_timer()
            self._focus_prompt()
            if not self._footer_hint:
                self._paint_footer()
            # Grok FIFO: start next queued user message when this turn ends.
            try:
                self.call_later(self._pump_message_queue)
            except Exception:
                try:
                    self._pump_message_queue()
                except Exception:
                    pass

    # ── orchestrator → scrollback as step rows ──────────────

    def _on_orch_event(self, event: dict[str, Any]) -> None:
        try:
            self.call_later(self._handle_event, event)
        except Exception:
            try:
                self.app.call_from_thread(self._handle_event, event)
            except Exception:
                pass

    def _handle_event(self, event: dict[str, Any]) -> None:
        t = str(event.get("type") or "")
        map_event_to_pipeline(event, self._pipe)
        self._render_steps()

        if t == "run_start":
            # Ensure document pipeline is shown (not leftover register steps)
            if self._pipe.mode != "run":
                self._pipe.reset_run()
                self._pipe.done_keys.add("template")
                self._pipe.current_key = "register"
                self._render_steps()
            tpl = event.get("template") or ""
            # Keep live row open (same chrome as continue); humanize later events.
            if not self._live_step_active:
                self._turn_step(
                    "新建任务" + (f"  ·  {tpl}" if tpl else ""),
                    key="run",
                )
            self._set_activity("创建会话", phase="thinking", reset_timer=True)
        elif t == "session_init":
            sid = event.get("session_id", "")
            total = event.get("total_sections", "")
            self._turn_step(
                f"会话就绪  {sid}" + (f"  ·  {total} 节" if total else ""),
                ok=True,
                key="run",
            )
            self._set_activity("注册资料", phase="waiting", reset_timer=True)
        elif t == "resume":
            # /continue already opened the live row — only retarget text.
            sid = event.get("session_id", "")
            self._turn_step(f"继续任务  {sid}", key="run")
            self._set_activity("继续生成", phase="thinking", reset_timer=True)
        elif t == "step_ok":
            # Ensure sidebar advances even if map_event missed a field
            kind = str(event.get("kind") or "")
            if kind == "input_register":
                self._pipe.mode = "run"
                if event.get("partial"):
                    # Mid-file chunk finished — stay on 注册资料
                    self._pipe.done_keys.add("template")
                    self._pipe.current_key = "register"
                else:
                    self._pipe.done_keys.update({"template", "register"})
                    self._pipe.current_key = "feed"
                self._render_steps()
            elif kind == "session_feed":
                self._pipe.mode = "run"
                self._pipe.done_keys.update({"template", "register", "feed"})
                self._pipe.current_key = "generate"
                self._render_steps()
            elif kind in ("session_prompt", "summarize"):
                self._pipe.mode = "run"
                self._pipe.done_keys.update({"template", "register", "feed"})
                self._pipe.current_key = "generate"
                self._render_steps()
            elif kind == "assemble":
                self._pipe.mode = "run"
                self._pipe.done_keys.update(
                    {"template", "register", "feed", "generate", "assemble"}
                )
                self._pipe.current_key = "complete"
                self._render_steps()
        elif t == "step_start":
            kind = event.get("kind", "")
            section = event.get("section", "")
            key = event.get("key", "")
            if kind == "input_register":
                self._pipe.mode = "run"
                self._pipe.done_keys.add("template")
                self._pipe.current_key = "register"
                self._render_steps()
                sk = f"reg:{key}"
                label = f"注册资料  {self._humanize_input_step_key(str(key), event)}"
                self._turn_step(label, key=sk)
                self._set_activity("注册资料", phase="thinking", reset_timer=True)
            elif kind == "session_feed":
                self._pipe.mode = "run"
                self._pipe.done_keys.update({"template", "register"})
                self._pipe.current_key = "feed"
                self._render_steps()
                sk = f"feed:{key}"
                label = f"喂入上下文  {self._humanize_input_step_key(str(key), event)}"
                self._turn_step(label, key=sk)
                self._set_activity("喂入上下文", phase="thinking", reset_timer=True)
            elif kind == "session_prompt":
                attempt = event.get("attempt", 1)
                sid = str(section or key)
                name = self._section_display_name(sid)
                sk = f"sec:{sid}"
                label = f"生成  {name}" + (
                    f"  #{attempt}" if attempt and int(attempt) != 1 else ""
                )
                self._turn_step(label, key=sk)
                self._set_activity(label, phase="thinking", reset_timer=True)
            elif kind == "summarize":
                # Keep the same live row; only refresh label + activity.
                sid = str(section or "")
                if not sid and str(key).startswith("sum-"):
                    sid = str(key)[4:]
                name = self._section_display_name(sid) if sid else ""
                label = f"摘要  {name}" if name else "摘要"
                sk = f"sec:{sid}" if sid else (self._live_step_key or "summarize")
                self._turn_step(label, key=sk)
                self._set_activity(label, phase="thinking", reset_timer=True)
            elif kind == "assemble":
                out = Path(str(event.get("output") or "")).name
                label = f"组装文档" + (f"  →  {out}" if out else "")
                self._turn_step(label, key="assemble")
                self._set_activity("组装文档", phase="parsing", reset_timer=True)
            else:
                sk = f"step:{kind or key or 'x'}"
                label = str(kind or key or "步骤")
                self._turn_step(label, key=sk)
                self._set_activity(label, phase="thinking", reset_timer=True)
        elif t == "worker_start":
            kind = event.get("kind", "")
            if kind == "chat":
                self._set_activity("对话", phase="thinking", reset_timer=True)
            else:
                # Keep current step label (do not wipe to Working...).
                self._set_activity(phase="thinking")
        elif t == "step_phase":
            # Silent engine / LLM phases — keep pulse alive with readable cue.
            phase_key = str(event.get("phase") or "")
            label = self._PHASE_LABEL.get(phase_key, "")
            detail = str(event.get("detail") or "").strip()
            if label and detail:
                self._live_step_phase = f"{label} {detail}"
            elif label:
                self._live_step_phase = label
            self._live_step_elapsed_i = -1  # force live text refresh
            act_phase = {
                "build_prompt": "thinking",
                "llm": "writing",
                "parse": "parsing",
                "summarize_build": "thinking",
                "summarize_llm": "writing",
                "summarize_parse": "parsing",
            }.get(phase_key, "thinking")
            # Activity strip stays Grok "Working..."; phase only on message live row.
            self._set_activity(phase=act_phase)
            if self._live_step_active:
                self._paint_live_step()
        elif t == "agent_tool_start":
            # Ignore late events after chat already finished.
            if not self._chat_busy:
                return
            tool = str(event.get("tool") or "tool")
            # Each tool = new message row (freeze prior tool/header as history).
            self._agent_open_tool_row(tool, event.get("args"))
        elif t == "agent_tool_end":
            if not self._chat_busy:
                return
            tool = str(event.get("tool") or "tool")
            self._agent_finish_tool_row(
                tool,
                is_error=bool(event.get("is_error")),
                args=event.get("args"),
                result=event.get("result"),
            )
            self._set_activity(phase="writing")
        elif t == "agent_thinking_delta":
            if not self._chat_busy:
                return
            self._agent_on_thinking_delta(str(event.get("delta") or ""))
        elif t == "agent_text_delta":
            if not self._chat_busy:
                return
            self._agent_on_text_delta(str(event.get("delta") or ""))
        elif t == "worker_progress":
            # Chat progress after idle must not reopen status bar.
            if str(event.get("kind") or "") == "chat" and not self._chat_busy:
                return
            if str(event.get("kind") or "") == "chat":
                # Agent: keep Thinking… / tool / answer stream; pulse activity.
                phase_hint = str(event.get("phase") or "")
                if phase_hint == "writing" and self._is_agent_thinking_live():
                    # Model started answer text — collapse Thinking first.
                    self._agent_finish_thinking()
                if (
                    self._is_agent_thinking_live()
                    and phase_hint != "thinking"
                    and self._live_step_text.startswith("Thinking")
                ):
                    # Still in thinking block until first tool — leave label
                    pass
                self._set_activity(phase="thinking" if phase_hint == "thinking" else "writing")
                # Answer live row: do not re-pulse as diamond step chrome.
                if self._is_agent_answer_live():
                    if self._agent_answer_dirty:
                        self._paint_live_answer()
                    return
                if self._live_step_active:
                    self._paint_live_step()
                return
            # Doc-gen: rewrite phase on the same live section row.
            phase_hint = str(event.get("phase") or "")
            if phase_hint == "thinking":
                self._live_step_phase = "思考中"
            elif not self._live_step_phase or self._live_step_phase.startswith("准备"):
                self._live_step_phase = "模型生成"
            self._live_step_elapsed_i = -1
            if self._activity_phase != "writing":
                self._set_activity(phase="writing")
            else:
                self._paint_activity()
            if self._live_step_active:
                self._paint_live_step()
        elif t == "worker_heartbeat":
            if str(event.get("kind") or "") == "chat" and not self._chat_busy:
                return
            if str(event.get("kind") or "") == "chat":
                self._set_activity(phase="thinking")
                if self._live_step_active:
                    self._paint_live_step()
                return
            # Doc-gen long silent LLM
            if self._live_step_phase in ("", "准备提示"):
                self._live_step_phase = "模型生成"
                self._live_step_elapsed_i = -1
            ms = event.get("elapsed_ms")
            if isinstance(ms, (int, float)) and ms >= 30_000:
                if "仍在" not in self._live_step_phase:
                    self._live_step_phase = "模型生成 · 仍在输出"
                    self._live_step_elapsed_i = -1
            self._set_activity(phase="writing")
            if self._live_step_active:
                self._paint_live_step()
        elif t == "worker_done":
            # Chat completion UI is owned by _chat_turn — never reopen Working.
            if str(event.get("kind") or "") == "chat":
                return
            self._live_step_phase = "解析写入"
            self._live_step_elapsed_i = -1
            self._set_activity(phase="parsing")
        elif t == "worker_error":
            # Chat owns its own completion UI in _chat_turn.
            if str(event.get("kind") or "") == "chat":
                return
            err = self._short_error(str(event.get("error") or "worker error"))
            # Cancel is reported once via run_cancelled — skip duplicate ✗ here
            if "cancel" not in err.lower():
                name = ""
                if self._live_step_text:
                    # strip leading 生成/摘要
                    name = (
                        self._live_step_text.removeprefix("生成  ")
                        .removeprefix("摘要  ")
                        .strip()
                    )
                self._turn_step(
                    f"失败  {name}  {err}".strip() if name else f"失败  {err}",
                    ok=False,
                    key=self._live_step_key or "err",
                )
                self._clear_activity()
        elif t == "step_ok":
            kind = event.get("kind", "")
            section = event.get("section", "")
            app: "RoomApp" = self.app  # type: ignore[assignment]
            summarize_on = bool(getattr(app.cfg, "summarize", True))
            if kind == "summarize":
                # Finalize the section live row after summarize.
                sid = str(section or "")
                if not sid and str(event.get("key") or "").startswith("sum-"):
                    sid = str(event.get("key"))[4:]
                if not sid and self._live_step_key.startswith("sec:"):
                    sid = self._live_step_key[4:]
                name = self._section_display_name(sid) if sid else (
                    self._live_step_text.removeprefix("摘要  ")
                    .removeprefix("生成  ")
                    .strip()
                    or "章节"
                )
                sk = f"sec:{sid}" if sid else (self._live_step_key or "summarize")
                self._turn_step(f"完成  {name}", ok=True, key=sk)
                # Not "完成 …" as activity while the run continues — next step_start
                # will set the real label; heal via snapshot if it is missed.
                self._set_activity("生成中…", phase="waiting")
            elif kind == "session_prompt" and section:
                name = self._section_display_name(str(section))
                sk = f"sec:{section}"
                if summarize_on:
                    # Stay live through summarize so the row keeps text + pulse.
                    self._turn_step(f"生成  {name}", key=sk)
                    self._set_activity(f"生成  {name}", phase="waiting")
                else:
                    self._turn_step(f"完成  {name}", ok=True, key=sk)
                    self._set_activity("生成中…", phase="waiting")
            elif kind == "input_register":
                k = str(event.get("key") or "")
                human = self._humanize_input_step_key(k, event)
                label = f"完成  注册资料  {human}".rstrip()
                self._turn_step(label, ok=True, key=f"reg:{k}")
                self._set_activity("生成中…", phase="waiting")
            elif kind == "session_feed":
                k = str(event.get("key") or "")
                human = self._humanize_input_step_key(k, event)
                label = f"完成  喂入上下文  {human}".rstrip()
                self._turn_step(label, ok=True, key=f"feed:{k}")
                self._set_activity("生成中…", phase="waiting")
            elif kind == "assemble":
                out = Path(str(event.get("output") or "")).name
                label = "完成  组装文档" + (f"  →  {out}" if out else "")
                self._turn_step(label, ok=True, key="assemble")
                self._set_activity("即将完成", phase="waiting")
            elif kind:
                label = f"完成  {kind}"
                self._turn_step(label, ok=True, key=self._live_step_key or kind)
                self._set_activity("生成中…", phase="waiting")
            else:
                self._turn_step("步骤完成", ok=True)
                self._set_activity("生成中…", phase="waiting")
        elif t == "step_error":
            sec = event.get("section") or event.get("key") or ""
            name = self._section_display_name(str(sec)) if sec else ""
            err = self._short_error(str(event.get("error") or ""))
            self._turn_step(
                f"失败  {name or sec}  {err}".strip(),
                ok=False,
                key=self._live_step_key or f"err:{sec}",
            )
            # Keep activity if the run will retry / continue; orch may re-open.
            self._set_activity("重试中…", phase="waiting")
        elif t == "step_warn":
            # Summarize is best-effort — still finalize open section row if any.
            if self._live_step_active and self._live_step_key.startswith("sec:"):
                sid = self._live_step_key[4:]
                name = self._section_display_name(sid)
                self._turn_step(f"完成  {name}", ok=True, key=self._live_step_key)
                self._set_activity("生成中…", phase="waiting")
            self._turn_system(f"警告  {self._short_error(str(event.get('error') or ''))}")
        elif t == "session_next":
            action = event.get("action")
            if action == "assemble":
                self._pipe.current_key = "assemble"
                self._pipe.done_keys.add("generate")
                self._render_steps()
                self._turn_step("组装文档", key="assemble")
                self._set_activity("组装文档", phase="writing", reset_timer=True)
            elif action == "feed_more":
                self._turn_step("需要补充资料", ok=False, key="feed_more")
                self._clear_activity()
            elif action == "generate":
                batch = event.get("parallel_batch") or []
                if batch:
                    name = self._section_display_name(str(batch[0]))
                    extra = f" 等 {len(batch)} 节" if len(batch) > 1 else ""
                    # Always surface upcoming work (activity + live if idle).
                    label = f"排队  {name}{extra}"
                    self._set_activity(label, phase="waiting")
                    if not self._live_step_active:
                        self._turn_step(f"生成  {name}", key=f"sec:{batch[0]}")
                        self._set_activity(f"生成  {name}", phase="waiting")
            elif action == "wait":
                n = len(event.get("in_progress") or [])
                label = f"等待中  {n} 节卡住，尝试恢复…" if n else "等待中…"
                self._turn_step(label, key=self._live_step_key or "run")
                self._set_activity(label, phase="waiting")
        elif t == "session_wait":
            n = len(event.get("in_progress") or [])
            label = f"等待中  {n} 节卡住，尝试恢复…" if n else "等待中…"
            self._turn_step(label, key=self._live_step_key or "run")
            self._set_activity(label, phase="waiting")
        elif t == "session_reclaim":
            secs = event.get("sections") or []
            label = f"已重置  {len(secs)} 节 → 继续生成"
            self._turn_step(label, key=self._live_step_key or "run")
            self._set_activity(label, phase="thinking", reset_timer=True)
        elif t == "snapshot":
            app: "RoomApp" = self.app  # type: ignore[assignment]
            snap = app.orch.state.snapshot
            if snap:
                self._render_chapters(
                    snap.sections, focus=app.orch.state.focus_section
                )
                # Prefer orch terminal phase over lagging engine snap.phase
                phase = app.orch.state.phase or snap.phase
                self._set_title(
                    mode=mode_label(
                        app.orch.state.running, phase, snap.progress or app.orch.state.progress
                    ),
                    template=snap.template_id,
                )
            # Heal: run still active but list stopped updating after last ✓.
            if app.orch.state.running and not self._live_step_active:
                self._ensure_live_for_focus()
        elif t == "run_complete":
            self._clear_activity()
            self._force_stop_spin_timer()
            self._turn_step(f"全部完成 → {event.get('output', '')}", ok=True)
            self._turn_assistant(f"生成完成。输出：{event.get('output', '')}")
            # Release /continue · /new pin so the finished turn can sit freely.
            self._end_user_pin()
            app = self.app  # type: ignore[assignment]
            prog = app.orch.state.progress or (
                app.orch.state.snapshot.progress if app.orch.state.snapshot else ""
            )
            self._set_title(mode=mode_label(False, "complete", prog))
            if app.orch.state.snapshot:
                self._render_chapters(app.orch.state.snapshot.sections)
            self._flush_pending_reflow()
            # Doc pipeline done → drain free-form queue (if any).
            try:
                self.call_later(self._pump_message_queue)
            except Exception:
                pass
        elif t == "run_cancelled":
            self._clear_activity()
            # Rewrite the open live row instead of stacking cancel notices.
            self._turn_step("任务已取消", ok=False, key=self._live_step_key or "run")
            self._set_title(mode="已取消")
            self._end_user_pin()
            try:
                self.call_later(self._pump_message_queue)
            except Exception:
                pass
        elif t == "run_failed":
            err = str(event.get("error") or "")
            self._clear_activity()
            if "cancel" in err.lower():
                self._turn_step("任务已取消", ok=False, key=self._live_step_key or "run")
                self._set_title(mode="已取消")
            else:
                self._turn_step(
                    f"任务失败  {err}",
                    ok=False,
                    key=self._live_step_key or "run",
                )
                self._set_title(mode="失败")
            self._end_user_pin()
            try:
                self.call_later(self._pump_message_queue)
            except Exception:
                pass
        elif t == "paused":
            self._clear_activity()
            self._turn_system("已请求暂停（当前步骤结束后停止）")
            # Keep pin while paused so /continue context stays top-aligned;
            # user can still scroll up (unfollow) via wheel.

        app: "RoomApp" = self.app  # type: ignore[assignment]
        st = app.orch.state
        if t in ("run_start", "session_init"):
            # New session: outline empty until a snapshot for *this* sid arrives.
            if t == "run_start" or not st.snapshot:
                self._render_chapters([])
            elif t == "session_init" and st.snapshot:
                # Prefer fresh snap after init if already loaded.
                snap_sid = str(getattr(st.snapshot, "session_id", "") or "")
                cur_sid = str(st.session_id or "")
                if snap_sid and cur_sid and snap_sid == cur_sid:
                    self._render_chapters(
                        st.snapshot.sections, focus=st.focus_section
                    )
                else:
                    self._render_chapters([])
        if st.running and t not in ("run_complete", "run_failed", "run_cancelled"):
            self._set_title(
                mode=mode_label(True, st.phase, st.progress),
                template=(st.snapshot.template_id if st.snapshot else ""),
            )
        if st.snapshot and t in (
            "step_ok",
            "step_start",
            "snapshot",
            "session_next",
        ):
            # Never paint a previous session's sections onto a new /new run.
            snap_sid = str(getattr(st.snapshot, "session_id", "") or "")
            cur_sid = str(st.session_id or "")
            if not cur_sid or not snap_sid or snap_sid == cur_sid:
                self._render_chapters(
                    st.snapshot.sections, focus=st.focus_section
                )

        if self._ws_root is not None:
            self._set_bottom_bar(self._ws_root, app.cfg.model, ok=self._ready)
        elif not self._footer_hint:
            self._paint_footer()

    # ── actions ─────────────────────────────────────────────

    def action_clear_scrollback(self) -> None:
        self._clear_live_step()
        self._expandables.clear()
        self._clear_user_sections()
        self._end_user_pin(keep_pad=False)
        self._scroll_pad_count = 0
        self.query_one("#msg-log", SmoothRichLog).clear()
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
            Workspace(root).clear_chat_history()
        except Exception:
            pass
        # Ephemeral notice only — do not re-seed history with "cleared".
        prev = self._restoring_history
        self._restoring_history = True
        try:
            self._turn_system("消息区已清空")
        finally:
            self._restoring_history = prev

    # ── Inline /new flow (template → inputs → confirm) ─────────

    def _close_new_flow(self, *, cancelled: bool = False) -> None:
        was = self._new_open
        self._new_open = False
        self._new_step = ""
        self._new_templates = []
        self._new_cursor = 0
        self._new_template_id = ""
        self._new_template_name = ""
        self._new_inputs = []
        self._new_suggestions = []
        self._new_output = "output.md"
        try:
            sug = self.query_one("#new-suggest", Static)
            sug.update("")
            sug.remove_class("active")
            sug.styles.display = "none"
            sug.styles.height = 0
            sug.styles.min_height = 0
            sug.styles.max_height = 0
        except Exception:
            pass
        if was and cancelled:
            # Durable so quit/re-entry still shows what happened after bare /new
            self._turn_system("已取消新建")
            self._show_footer_hint("已取消新建", seconds=0.9)

    def _new_panel_width(self) -> int:
        try:
            return max(40, int(self.size.width or 80) - 4)
        except Exception:
            return 72

    def _paint_new_flow(self) -> None:
        from room_tui.new_run_flow import (
            format_new_confirm_dropdown,
            format_new_inputs_dropdown,
            format_new_template_dropdown,
        )

        if not self._new_open:
            self._close_new_flow()
            return
        w = self._new_panel_width()
        app: "RoomApp" = self.app  # type: ignore[assignment]
        if self._new_step == "template":
            text, rows = format_new_template_dropdown(
                self._new_templates, selected=self._new_cursor, width=w
            )
        elif self._new_step == "inputs":
            text, rows = format_new_inputs_dropdown(
                selected_paths=self._new_inputs,
                candidates=self._new_suggestions,
                cursor=self._new_cursor,
                ws=self._ws_root or Path(app.cfg.workspace or Path.cwd()),
                width=w,
            )
        else:
            text, rows = format_new_confirm_dropdown(
                template_name=self._new_template_name,
                template_id=self._new_template_id,
                inputs=self._new_inputs,
                output=self._new_output,
                model=app.cfg.model or "",
                width=w,
            )
        sug = self.query_one("#new-suggest", Static)
        sug.update(text)
        sug.add_class("active")
        sug.styles.display = "block"
        sug.styles.height = "auto"
        sug.styles.min_height = rows
        sug.styles.max_height = max(rows, 16)
        try:
            self.refresh(layout=True)
        except Exception:
            pass

    def _new_move_selection(self, direction: int) -> None:
        if not self._new_open:
            return
        from room_tui.new_run_flow import inputs_row_count

        if self._new_step == "template":
            n = max(1, len(self._new_templates))
        elif self._new_step == "inputs":
            n = max(1, inputs_row_count(self._new_inputs, self._new_suggestions))
        else:
            n = 1
        self._new_cursor = (self._new_cursor + direction) % n
        self._paint_new_flow()

    def _open_new_flow(self) -> None:
        """Start inline /new: load templates then show step ①."""
        if self._chat_busy:
            self._show_footer_hint("Agent 运行中，稍后再 /new", seconds=1.4)
            return
        app: "RoomApp" = self.app  # type: ignore[assignment]
        if app.orch.state.running:
            self._show_footer_hint("任务运行中，请先结束或取消", seconds=1.4)
            return
        self._close_slash_dropdown()
        self._close_rewind_picker()
        self._close_new_flow()
        self._new_open = True
        self._new_step = "template"
        self._new_cursor = 0
        self._clear_composer()
        self.run_worker(self._new_boot_async(), exclusive=True)

    async def _new_boot_async(self) -> None:
        from room_tui.new_run_flow import safe_filename_stem, scan_input_suggestions

        app: "RoomApp" = self.app  # type: ignore[assignment]
        root = self._ws_root or Path(app.cfg.workspace or Path.cwd()).resolve()
        self._new_suggestions = scan_input_suggestions(root)
        try:
            rows = await app.load_templates()
        except Exception as e:
            self._close_new_flow()
            self._turn_system(f"无法加载模板: {e}", error=True)
            return
        if not self._new_open:
            return
        self._new_templates = list(rows or [])
        if not self._new_templates:
            self._close_new_flow()
            self._append_notice_block(
                "暂无已注册模板，无法新建",
                "先注册: /template register <样例文件> [名称]",
                "快速: 加 --fast  ·  帮助: /template help",
            )
            return
        # Prefer first template; seed default output name
        t0 = self._new_templates[0]
        self._new_template_id = str(t0.get("id") or "")
        self._new_template_name = str(t0.get("name") or self._new_template_id)
        self._new_output = f"{safe_filename_stem(self._new_template_name)}.md"
        self._new_cursor = 0
        self._paint_new_flow()
        # Message-list row (dropdown itself is ephemeral chrome above composer).
        # Without this, quit/re-entry leaves bare "/new" with no response.
        self._append_notice_block(
            "新建文档  ·  ①选模板  ②勾资料  ③确认开始",
            f"已加载 {len(self._new_templates)} 个模板  ·  Esc 取消",
        )
        try:
            self.query_one("#cmd-input", PromptField).focus()
        except Exception:
            pass
        self._show_footer_hint("新建 ① 选模板 · Enter · Esc 取消", seconds=2.0)

    def _new_on_enter(self) -> None:
        """Enter handler for the active /new step."""
        if not self._new_open:
            return
        if self._new_step == "template":
            self._new_confirm_template()
        elif self._new_step == "inputs":
            self._new_confirm_inputs_enter()
        else:
            self._new_confirm_start()

    def _new_confirm_template(self) -> None:
        from room_tui.new_run_flow import safe_filename_stem

        if not self._new_templates:
            self._close_new_flow()
            return
        idx = max(0, min(self._new_cursor, len(self._new_templates) - 1))
        t = self._new_templates[idx]
        self._new_template_id = str(t.get("id") or "")
        self._new_template_name = str(t.get("name") or self._new_template_id)
        if not self._new_template_id:
            self._show_footer_hint("该模板无 id，请换一个", seconds=1.5)
            return
        self._new_output = f"{safe_filename_stem(self._new_template_name)}.md"
        self._new_step = "inputs"
        self._new_cursor = 0
        self._clear_composer()
        self._paint_new_flow()
        self._show_footer_hint(
            "新建 ② 勾选资料 · Enter 勾选 · 选「下一步」· 或粘贴路径 Enter",
            seconds=2.5,
        )

    def _new_confirm_inputs_enter(self) -> None:
        from room_tui.new_run_flow import (
            inputs_path_at,
            inputs_row_is_next,
        )

        raw = self._composer_text().strip()
        if raw:
            path = self._resolve_new_path(raw)
            if path is not None and path.is_file():
                self._new_add_input(path)
                self._clear_composer()
                self._paint_new_flow()
                return
            # Absolute/relative path that doesn't exist
            doc_ext = (".doc", ".docx", ".pdf", ".md", ".txt", ".xlsx", ".xls", ".csv", ".pptx", ".html")
            if any(raw.lower().endswith(e) for e in doc_ext) or "/" in raw or raw.startswith("~"):
                self._show_footer_hint(f"文件不存在: {raw[:48]}", seconds=1.6)
                return
            if raw.startswith("/"):
                self._show_footer_hint("新建进行中 · Esc 取消后再用命令", seconds=1.2)
                return

        if inputs_row_is_next(self._new_cursor, self._new_inputs, self._new_suggestions):
            if not self._new_inputs:
                self._show_footer_hint("请先勾选至少一份资料", seconds=1.4)
                return
            self._new_step = "confirm"
            self._new_cursor = 0
            self._set_composer_text(self._new_output)
            self._paint_new_flow()
            self._show_footer_hint("新建 ③ Enter 开始 · 可改输出文件名", seconds=2.0)
            return

        p = inputs_path_at(self._new_cursor, self._new_inputs, self._new_suggestions)
        if p is None:
            return
        # Toggle
        rp = p.resolve()
        existing = {x.resolve() for x in self._new_inputs}
        if rp in existing:
            self._new_inputs = [x for x in self._new_inputs if x.resolve() != rp]
        else:
            self._new_inputs.append(rp)
        self._paint_new_flow()

    def _new_add_input(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._show_footer_hint(f"文件不存在: {path.name}", seconds=1.5)
            return
        rp = path.resolve()
        if rp in {p.resolve() for p in self._new_inputs}:
            self._show_footer_hint("已在列表中", seconds=1.0)
            return
        self._new_inputs.append(rp)

    def _resolve_new_path(self, raw: str) -> Path | None:
        s = (raw or "").strip().strip("'\"")
        if not s:
            return None
        app: "RoomApp" = self.app  # type: ignore[assignment]
        root = self._ws_root or Path(app.cfg.workspace or Path.cwd()).resolve()
        path = Path(s).expanduser()
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()
        return path

    def _new_confirm_start(self) -> None:
        from room_tui.new_run_flow import default_budget

        app: "RoomApp" = self.app  # type: ignore[assignment]
        out_name = (self._composer_text().strip() or self._new_output or "output.md")
        if not out_name.endswith((".md", ".docx", ".doc", ".txt", ".html")):
            # allow bare stem
            if "." not in Path(out_name).name:
                out_name = out_name + ".md"
        self._new_output = out_name
        if not self._new_template_id:
            self._show_footer_hint("未选模板", seconds=1.2)
            return
        if not self._new_inputs:
            self._show_footer_hint("未选资料", seconds=1.2)
            return
        root = self._ws_root or Path(app.cfg.workspace or Path.cwd()).resolve()
        output = (root / out_name).resolve()
        tid = self._new_template_id
        inputs = list(self._new_inputs)
        tname = self._new_template_name or tid
        self._close_new_flow()
        self._clear_composer()
        # Notice = summary; live step timeline carries detailed progress
        # (same chrome as /continue: 注册资料 → 喂入 → 逐节生成 → 组装).
        self._append_notice_block(
            f"开始生成  ·  {tname}",
            f"资料 {len(inputs)} 份  →  {output.name}",
        )
        app.start_run(
            template_id=tid,
            inputs=inputs,
            output=output,
            budget=default_budget(),
            template_name=tname,
        )

    def action_new_run(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        ready, env_err = app.probe_environment_sync()
        self._ready = bool(ready)
        self._set_bottom_bar(
            self._ws_root or Path(app.cfg.workspace or Path.cwd()),
            app.cfg.model,
            ok=self._ready,
            err=env_err if not self._ready else "",
        )
        if not self._ready:
            reason = (env_err or self._env_err or "环境未就绪").strip()
            self._turn_system(f"无法新建 · {reason}", error=True)
            self._turn_system("终端: room doctor", error=False)
            return
        if not self._model_ok:
            self._turn_system(
                f"模型未就绪，无法新建。{(' · ' + self._model_issue) if self._model_issue else ''}  ·  Ctrl+M 连接 或 /model",
                error=True,
            )
            return
        self._open_new_flow()

    def action_continue_run(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        root = self._ws_root or Path(app.cfg.workspace or Path.cwd())
        ws = Workspace(Path(root))
        m = ws.load_manifest()
        if not m or not m.session_id:
            self._append_notice_block(
                "没有可继续的任务",
                "请先 /new 开始文档生成  ·  ①选模板 ②勾资料 ③Enter",
            )
            return
        if app.orch.state.running:
            self._turn_system("任务正在运行中。")
            return
        # Preflight (template_exists) can take 1–2s via paper-derived CLI —
        # never run it on the UI thread or the user prompt stays invisible.
        sid = m.session_id
        tid = (m.template_id or "").strip()
        self._turn_step(f"继续  {sid}  ·  进行中…", key="run")
        self._set_activity("resume", phase="thinking", reset_timer=True)
        self.run_worker(
            self._continue_run_async(sid, tid),
            exclusive=False,
        )

    async def _continue_run_async(self, session_id: str, template_id: str) -> None:
        """Background preflight + resume so Enter feels instant."""
        import asyncio

        app: "RoomApp" = self.app  # type: ignore[assignment]
        tid = (template_id or "").strip()
        if tid:
            try:
                exists = await asyncio.to_thread(app.engine.template_exists, tid)
            except Exception:
                exists = True
            if not exists:
                self._append_notice_block(
                    "没有可继续的进行中任务",
                    "上次文档生成已无法恢复（任务状态失效）。",
                    "请先 /new 开始新的文档生成  ·  ①选模板 ②勾资料 ③Enter",
                )
                self._show_footer_hint("请 /new 开始文档生成", seconds=2.5)
                self._clear_activity()
                if self._live_step_active:
                    try:
                        self._turn_step(
                            "无法继续 · 模板已失效",
                            ok=False,
                            key=self._live_step_key or "run",
                        )
                    except Exception:
                        pass
                return
        app.resume_session(session_id)

    def action_pause(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        app.orch.request_pause()
        self._turn_system("暂停请求已发送")

    def action_cancel_run(self) -> None:
        app: "RoomApp" = self.app  # type: ignore[assignment]
        cancelled_any = False
        if app.orch.state.running:
            app.orch.request_cancel()
            cancelled_any = True
            self._set_activity("cancel", phase="cancel", reset_timer=True)
            self._turn_step("正在取消…", key=self._live_step_key or "run")
        if self._chat_busy:
            # 1) signal the chat coroutine
            if self._chat_cancel is not None:
                try:
                    self._chat_cancel.set()
                except Exception:
                    pass
            # 2) kill pi process tree immediately (don't wait for poll loop)
            try:
                app.orch.runner.kill_active_chat()
            except Exception:
                pass
            cancelled_any = True
            self._set_activity("cancel", phase="cancel", reset_timer=True)
            self._turn_step("正在取消 Agent…", key="chat")
        if not cancelled_any:
            self._turn_system("当前没有运行中的任务或 Agent")

    def action_refresh(self) -> None:
        self.run_worker(self._bootstrap(), exclusive=True)

    def notify_run_started(
        self, template_id: str, *, template_name: str = ""
    ) -> None:
        """Open the same live step timeline used by /continue."""
        # Drop previous-run orch snapshot so step events cannot re-paint old 大纲.
        try:
            app: "RoomApp" = self.app  # type: ignore[assignment]
            if hasattr(app.orch, "_reset_ui_session_state"):
                app.orch._reset_ui_session_state()
            else:
                st = app.orch.state
                st.snapshot = None
                st.progress = ""
                st.focus_section = ""
                st.session_id = ""
                st.phase = "init"
        except Exception:
            pass
        # Document generation pipeline (never leave template-register steps stuck)
        self._pipe = PipelineState()
        self._pipe.reset_run()
        self._pipe.done_keys.add("template")
        self._pipe.current_key = "register"
        self._render_steps()
        # Explicit empty outline — not last run's all-green sections.
        self._render_chapters([])
        label = (template_name or template_id or "").strip()
        # No stale 43/43 in the title while register is still running.
        self._set_title(mode="注册资料", template=label or template_id)
        # Pin last user band (/new) so generation steps grow in view (Grok).
        self._pin_latest_user_section()
        # Live row (not system text) — subsequent orch events retarget/finish it.
        self._turn_step(
            f"新建任务  ·  {label}" if label else "新建任务",
            key="run",
        )
        self._set_activity("准备会话", phase="thinking", reset_timer=True)
        self._focus_prompt()
