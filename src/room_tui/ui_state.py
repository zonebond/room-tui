"""UI state helpers: pipeline steps + chapter glyphs (no Agent branding)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from rich.cells import cell_len, set_cell_size

# Pipeline steps shown in right sidebar upper half — document generation (/new)
PIPELINE_STEPS: list[tuple[str, str]] = [
    ("template", "选择模板"),
    ("register", "注册资料"),
    ("feed", "喂入上下文"),
    ("generate", "生成章节"),
    ("assemble", "组装输出"),
    ("complete", "完成"),
]

# Template registration (/template register) — different job, different progress
TEMPLATE_REG_STEPS: list[tuple[str, str]] = [
    ("build", "构造 prompt"),
    ("analyze", "分析样例"),
    ("write", "写入模板库"),
    ("done", "完成"),
]

StepStatus = Literal["done", "active", "pending"]
SectionStatus = Literal["done", "active", "pending", "failed", "placeholder"]
PipelineMode = Literal["run", "register"]

GLYPH = {
    "done": "✓",
    "active": "●",
    "pending": "○",
    "failed": "!",
    "placeholder": "◇",
}

# Shared soft palette (Room chrome / roles / pipeline).
COLOR_BRAND = "#57A5E2"
# High-contrast brand on primary chrome (title bar / footer).
COLOR_BRAND_ON_BAR = "#FFFFFF"
COLOR_OK = "#8FBF9F"
COLOR_ERR = "#D17E92"
COLOR_WARN = "#FFC473"
# Message-list chrome — GrokNight (xai-grok-pager-render theme/groknight.rs)
# Live accent wave blends bg_base → thinking.accent (gray_dim) via sin².
COLOR_MSG_BG = "#141414"  # bg_storm — blend base for wave
COLOR_MSG_DIM = "#414141"  # FG_GUTTER trough
COLOR_MSG_MID = "#585858"  # gray_dim — ThinkingConfig.accent default
COLOR_MSG_HI = "#787878"  # DARK5 / gray_bright peak readability
COLOR_MSG_LABEL = "#6c6c6c"  # COMMENT — muted header while running
COLOR_MSG_USER = "#c8c8c8"  # FG_DARK — user prompt body
COLOR_MSG_TEXT = "#c8c8c8"  # md_text / text_secondary

# Markdown / code blocks (GrokNight md_* + tool accents)
COLOR_MD_CODE = "#3A95AB"  # md_code / BLUE1 — inline code
COLOR_MD_CODE_BG = "#1c1c1c"  # md_code_bg — fenced block band (≠ monokai olive)
COLOR_CMD = "#e0af68"  # command / YELLOW — shell $ argv
COLOR_PATH = "#ff9e64"  # path / ORANGE
COLOR_DIFF_DEL_FG = "#f7768e"  # diff_delete_fg
COLOR_DIFF_DEL_BG = "#420e14"  # diff_delete_bg
COLOR_DIFF_INS_FG = "#9ece6a"  # diff_insert_fg
COLOR_DIFF_INS_BG = "#063806"  # diff_insert_bg
COLOR_TOOL_BULLET = "#787878"  # accent_tool / DARK5 — ◆

# Fallback sidebar content cells when live layout width is unknown.
# SIDEBAR 34 − border 1 − sidebar L pad 1 − scroll L pad 1 − slack 1 ≈ 30.
# Keep lines ≤ this so Static does not wrap (CJK = 2 cells; wrap looks like a blank row).
CHAPTER_LINE_CELLS = 30
CHAPTER_MAX_LEVEL = 4

# Engine / internal phase → UI Chinese label.
PHASE_CN: dict[str, str] = {
    "init": "初始化",
    "idle": "空闲",
    "created": "已创建",
    "generating": "生成中",
    "registering": "注册资料",
    "feeding": "喂入中",
    "assembling": "组装中",
    "running": "生成中",
    "paused": "已暂停",
    "pause": "已暂停",
    "complete": "已完成",
    "done": "已完成",
    "failed": "失败",
    "error": "失败",
    "cancelled": "已取消",
    "canceled": "已取消",
}


@dataclass
class PipelineState:
    """Which pipeline step is current (run = /new, register = /template register)."""

    mode: PipelineMode = "run"
    current_key: str = "template"
    # keys that are done
    done_keys: set[str] = field(default_factory=set)

    def steps(self) -> list[tuple[str, str]]:
        if self.mode == "register":
            return list(TEMPLATE_REG_STEPS)
        return list(PIPELINE_STEPS)

    def reset_run(self) -> None:
        self.mode = "run"
        self.current_key = "template"
        self.done_keys = set()

    def begin_template_register(self) -> None:
        """Switch sidebar to template-registration progress."""
        self.mode = "register"
        self.current_key = "build"
        self.done_keys = set()

    def mark_register_step(self, key: str, *, done: bool = False) -> None:
        """Advance template-register pipeline (build → analyze → write → done)."""
        if self.mode != "register":
            self.begin_template_register()
        keys = [k for k, _ in TEMPLATE_REG_STEPS]
        if key not in keys:
            return
        idx = keys.index(key)
        for k in keys[:idx]:
            self.done_keys.add(k)
        if done:
            self.done_keys.add(key)
            if idx + 1 < len(keys):
                self.current_key = keys[idx + 1]
            else:
                self.current_key = "done"
        else:
            self.current_key = key

    def mark_done_up_to(self, key: str) -> None:
        if self.mode != "run":
            self.mode = "run"
        keys = [k for k, _ in PIPELINE_STEPS]
        if key not in keys:
            return
        idx = keys.index(key)
        for k in keys[:idx]:
            self.done_keys.add(k)
        self.current_key = key

    def complete_all(self) -> None:
        steps = self.steps()
        self.done_keys = {k for k, _ in steps}
        self.current_key = steps[-1][0] if steps else "complete"

    def render_lines(self) -> list[str]:
        """Rich markup lines: active solid, done soft, pending quiet."""
        lines: list[str] = []
        steps = self.steps()
        keys = [k for k, _ in steps]
        cur_i = keys.index(self.current_key) if self.current_key in keys else 0
        for i, (key, label) in enumerate(steps):
            if key in self.done_keys and key != self.current_key:
                status = "done"
            elif key == self.current_key and key not in self.done_keys:
                status = "active"
            elif key == self.current_key and key in ("complete", "done"):
                status = "done"
            elif i < cur_i:
                status = "done"
            elif i == cur_i:
                status = "active"
            else:
                status = "pending"
            g = GLYPH[status]
            if status == "active":
                lines.append(f" [bold {COLOR_BRAND}]{g}[/bold {COLOR_BRAND}]  {label}")
            elif status == "done":
                lines.append(f" [{COLOR_OK}]{g}[/{COLOR_OK}]  [dim]{label}[/dim]")
            else:
                lines.append(f" [dim]{g}  {label}[/dim]")
        return lines


def map_event_to_pipeline(event: dict[str, Any], pipe: PipelineState) -> None:
    """Advance document-generation pipeline from orchestrator events.

    Template-register mode uses ``pipe.mark_register_step`` from the shell and
    ignores gen-run step events so the sidebar stays on register steps.
    """
    t = str(event.get("type") or "")
    kind = str(event.get("kind") or "")

    # Switch back to document pipeline when a real doc run starts
    if t in ("run_start", "session_init", "resume"):
        pipe.mode = "run"
    elif pipe.mode == "register":
        return

    if t == "session_init":
        pipe.mark_done_up_to("register")
        pipe.done_keys.add("template")
        pipe.current_key = "register"
    elif t == "step_start" and kind == "input_register":
        # Stay on 注册资料 for multi-chunk / multi-file register
        pipe.mark_done_up_to("register")
        pipe.current_key = "register"
        # partial chunk ok must not advance sidebar
    elif t == "step_ok" and kind == "input_register":
        if event.get("partial"):
            pipe.current_key = "register"
        else:
            pipe.done_keys.add("register")
            pipe.current_key = "feed"
    elif t == "step_start" and kind == "session_feed":
        pipe.current_key = "feed"
    elif t == "step_ok" and kind == "session_feed":
        pipe.done_keys.add("feed")
        pipe.current_key = "generate"
    elif t in ("step_start", "step_ok") and kind in ("session_prompt", "summarize"):
        pipe.done_keys.update({"template", "register", "feed"})
        # Stay on 生成章节 until assemble (multi-section)
        pipe.current_key = "generate"
    elif t == "step_start" and kind == "assemble":
        pipe.done_keys.update({"template", "register", "feed", "generate"})
        pipe.current_key = "assemble"
    elif t == "step_ok" and kind == "assemble":
        pipe.done_keys.add("assemble")
        pipe.current_key = "complete"
    elif t == "run_complete":
        pipe.mode = "run"
        pipe.complete_all()
    elif t == "resume":
        pipe.mode = "run"
        pipe.done_keys.update({"template", "register", "feed"})
        pipe.current_key = "generate"
    elif t == "run_start":
        pipe.mode = "run"
        pipe.done_keys = set()
        if event.get("template"):
            pipe.done_keys.add("template")
            pipe.current_key = "register"
        else:
            pipe.current_key = "template"
    elif t == "session_next":
        # Engine says next action — keep sidebar aligned
        action = str(event.get("action") or "").lower()
        if action in ("generate", "prompt", "section"):
            pipe.done_keys.update({"template", "register", "feed"})
            pipe.current_key = "generate"
        elif action == "assemble":
            pipe.done_keys.update({"template", "register", "feed", "generate"})
            pipe.current_key = "assemble"
        elif action in ("complete", "done"):
            pipe.complete_all()


def section_glyph(status: str) -> str:
    s = (status or "pending").lower()
    if s in ("done", "generated", "complete"):
        return GLYPH["done"]
    if s in ("generating", "running", "active"):
        return GLYPH["active"]
    if s in ("failed", "error"):
        return GLYPH["failed"]
    if s in ("placeholder",):
        return GLYPH["placeholder"]
    # ready / pending / empty / …
    return GLYPH["pending"]


def _section_status_kind(status: str) -> str:
    s = (status or "pending").lower()
    if s in ("done", "generated", "complete"):
        return "done"
    if s in ("generating", "running", "active"):
        return "active"
    if s in ("failed", "error"):
        return "failed"
    if s in ("placeholder",):
        return "placeholder"
    return "pending"


def _truncate_cells(text: str, max_cells: int) -> str:
    """Truncate by terminal display cells (CJK = 2), append … if needed."""
    if max_cells <= 0:
        return ""
    s = text or ""
    if cell_len(s) <= max_cells:
        return s
    if max_cells == 1:
        return "…"
    return set_cell_size(s, max_cells - 1).rstrip() + "…"


def render_chapter_lines(
    sections: list[Any],
    *,
    focus: str = "",
    max_lines: int = 200,
    width_cells: int = CHAPTER_LINE_CELLS,
) -> list[str]:
    """One line per section; CJK-safe truncate so the sidebar never wraps.

    Tree indent is 2 spaces per level (capped). Status colors:
    active bright · done soft green · failed rose · pending dim.
    """
    lines: list[str] = []
    for sec in sections[:max_lines]:
        sid = getattr(sec, "section_id", None) or (
            sec.get("section_id") if isinstance(sec, dict) else ""
        )
        title = getattr(sec, "title", None) or (
            sec.get("title") if isinstance(sec, dict) else sid
        )
        status = getattr(sec, "status", None) or (
            sec.get("status") if isinstance(sec, dict) else "pending"
        )
        level = getattr(sec, "level", None) or (
            sec.get("level") if isinstance(sec, dict) else 1
        )
        try:
            level_i = max(1, min(CHAPTER_MAX_LEVEL, int(level)))
        except (TypeError, ValueError):
            level_i = 1

        kind = _section_status_kind(str(status))
        # Exact id match only — never `sid in focus` (substring: "1" in "3.1").
        focused = bool(focus and sid and str(sid) == str(focus))
        if focused and kind == "pending":
            kind = "active"

        # indent + glyph + space + title  (all within width_cells)
        indent = "  " * (level_i - 1)
        g = GLYPH["active"] if kind == "active" else section_glyph(str(status))
        # prefix display width (spaces are 1 cell; glyphs are 1)
        prefix = f"{indent}{g} "
        name_budget = max(2, width_cells - cell_len(prefix))
        name = _truncate_cells(str(title or sid or "—"), name_budget)

        if kind == "active":
            lines.append(
                f"{indent}[bold {COLOR_BRAND}]{g}[/bold {COLOR_BRAND}] [bold]{name}[/bold]"
            )
        elif kind == "done":
            lines.append(f"{indent}[{COLOR_OK}]{g}[/{COLOR_OK}] [dim]{name}[/dim]")
        elif kind == "failed":
            lines.append(
                f"{indent}[bold {COLOR_ERR}]{g}[/bold {COLOR_ERR}] "
                f"[{COLOR_ERR}]{name}[/{COLOR_ERR}]"
            )
        elif level_i <= 1:
            # L1 pending: slightly clearer than nested dim rows.
            lines.append(f"{indent}{g} [dim]{name}[/dim]")
        else:
            lines.append(f"{indent}[dim]{g} {name}[/dim]")
    return lines


def progress_all_done(progress: str) -> bool:
    """True when progress looks like ``46/46`` (all sections finished)."""
    import re

    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", (progress or "").strip())
    if not m:
        return False
    done, total = int(m.group(1)), int(m.group(2))
    return total > 0 and done >= total


def mode_label(running: bool, phase: str, progress: str) -> str:
    """Human Chinese mode for title bar — never raw engine English (init, …)."""
    prog = (progress or "").strip()
    p = (phase or "").strip().lower()

    # Idle complete: engine often still reports assembling/generating after
    # assemble; trust complete phase or full progress over a stale mid-phase.
    if not running:
        if p in ("complete", "done") or progress_all_done(prog):
            return f"已完成  {prog}" if prog else "已完成"
        if not p or p == "idle":
            return "空闲"
        # Interrupted mid-run (not actively running anymore)
        if p in (
            "generating",
            "running",
            "assembling",
            "feeding",
            "registering",
            "resume",
        ):
            return f"已暂停  {prog}" if prog else "已暂停"
        label = PHASE_CN.get(p, p)
        if prog and label in (
            "生成中",
            "喂入中",
            "注册资料",
            "组装中",
            "已完成",
            "已暂停",
        ):
            return f"{label}  {prog}"
        return label

    # Actively running: prefer precise phase when known
    if p in ("assembling", "assemble"):
        return f"组装中  {prog}" if prog else "组装中"
    if p in ("registering", "register"):
        return f"注册资料  {prog}" if prog else "注册资料"
    if p in ("feeding", "feed"):
        return f"喂入中  {prog}" if prog else "喂入中"
    return f"生成中  {prog}" if prog else "生成中"


def normalize_mode_display(mode: str) -> str:
    """Map any leftover English phase token to Chinese for the title bar."""
    mode_s = (mode or "").strip() or "空闲"
    for cn in PHASE_CN.values():
        if mode_s == cn or mode_s.startswith(cn + " ") or mode_s.startswith(cn + "\t"):
            return mode_s
    parts = mode_s.split(None, 1)
    head = parts[0].lower()
    if head in PHASE_CN:
        tail = parts[1] if len(parts) > 1 else ""
        label = PHASE_CN[head]
        return f"{label}  {tail}" if tail else label
    return mode_s
