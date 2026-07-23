"""Inline /new flow helpers (template → inputs → confirm).

Kept out of shell.py so the step UI stays easy to tweak. Rendering is
Grok-style: a dropdown above the composer, not a full-screen wizard.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from room_tui.ui_state import COLOR_BRAND

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


def default_budget() -> int:
    return _DEFAULT_BUDGET


def safe_filename_stem(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", s)
    s = re.sub(r"_+", "_", s).strip("._")
    return s or "output"


def scan_input_suggestions(root: Path, *, limit: int = 80) -> list[Path]:
    """Workspace document candidates for the inputs step."""
    root = root.resolve()
    found: list[Path] = []
    seen: set[Path] = set()
    for pat in _DOC_GLOBS:
        for p in sorted(root.glob(pat)):
            if p.is_file():
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    found.append(rp)
    try:
        for sub in sorted(root.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            for pat in _DOC_GLOBS:
                for p in sorted(sub.glob(pat)):
                    if p.is_file():
                        rp = p.resolve()
                        if rp not in seen:
                            seen.add(rp)
                            found.append(rp)
    except OSError:
        pass
    return found[:limit]


def _rel_label(p: Path, ws: Path) -> str:
    try:
        return str(p.relative_to(ws))
    except ValueError:
        return p.name


def format_new_template_dropdown(
    templates: list[dict[str, Any]],
    *,
    selected: int,
    width: int = 72,
) -> tuple[str, int]:
    """① pick template."""
    w = max(40, int(width))
    lines: list[str] = [
        f"[bold {COLOR_BRAND}]新建 ①/3[/bold {COLOR_BRAND}]  "
        f"选择模板  ·  ↑↓  ·  Enter 确认  ·  Esc 取消"
    ]
    if not templates:
        lines.append("[dim]  （无已注册模板 · 先 /template register <样例> [名称]）[/dim]")
        return "\n".join(lines), len(lines)

    sel = max(0, min(int(selected), len(templates) - 1))
    # Window around selection
    max_rows = 10
    start = 0
    if len(templates) > max_rows:
        start = max(0, min(sel - max_rows // 2, len(templates) - max_rows))
    end = min(len(templates), start + max_rows)

    for i in range(start, end):
        t = templates[i]
        name = str(t.get("name") or t.get("id") or "?")
        nsec = t.get("section_count", "?")
        tid = str(t.get("id") or "")
        mark = "›" if i == sel else " "
        row = f" {mark} {name}  ·  {nsec} 节"
        if tid and tid != name:
            row += f"  [dim]{tid}[/dim]"
        if len(row) > w - 2:
            row = row[: w - 5] + "…"
        if i == sel:
            lines.append(f"[bold {COLOR_BRAND}]{row}[/bold {COLOR_BRAND}]")
        else:
            lines.append(f"[dim]{row}[/dim]")
    if start > 0 or end < len(templates):
        lines.append(f"[dim]  … {len(templates)} 个模板[/dim]")
    return "\n".join(lines), len(lines)


def format_new_inputs_dropdown(
    *,
    selected_paths: list[Path],
    candidates: list[Path],
    cursor: int,
    ws: Path,
    width: int = 72,
) -> tuple[str, int]:
    """② multi-select inputs. Last row is always 「下一步」."""
    w = max(40, int(width))
    n = len(selected_paths)
    lines: list[str] = [
        f"[bold {COLOR_BRAND}]新建 ②/3[/bold {COLOR_BRAND}]  "
        f"勾选资料  ·  Enter 勾选/取消  ·  底部「下一步」  ·  Esc 取消"
    ]
    # Rows = selected + remaining candidates + next-action
    rows: list[tuple[str, str]] = []  # (kind, label) kind=path|next
    sel_set = {p.resolve() for p in selected_paths}
    for p in selected_paths:
        rows.append(("path", f"✓ {_rel_label(p, ws)}"))
    for p in candidates:
        if p.resolve() not in sel_set:
            rows.append(("path", f"○ {_rel_label(p, ws)}"))
    next_label = f"→ 下一步（已选 {n} 份）" if n else "→ 下一步（请先勾选至少 1 份）"
    rows.append(("next", next_label))

    if len(rows) == 1 and n == 0:
        lines.append("[dim]  （工作区未扫描到文档 · 在输入框粘贴路径后 Enter 添加）[/dim]")

    sel = max(0, min(int(cursor), len(rows) - 1))
    max_rows = 12
    start = 0
    if len(rows) > max_rows:
        start = max(0, min(sel - max_rows // 2, len(rows) - max_rows))
    end = min(len(rows), start + max_rows)

    for i in range(start, end):
        kind, label = rows[i]
        mark = "›" if i == sel else " "
        row = f" {mark} {label}"
        if len(row) > w - 2:
            row = row[: w - 5] + "…"
        if i == sel:
            style = COLOR_BRAND if kind == "next" and n > 0 else COLOR_BRAND
            lines.append(f"[bold {style}]{row}[/bold {style}]")
        elif kind == "next":
            lines.append(f"[{'#8FBF9F' if n else 'dim'}]{row}[/{'#8FBF9F' if n else 'dim'}]")
        else:
            lines.append(f"[dim]{row}[/dim]" if label.startswith("○") else row)

    return "\n".join(lines), len(lines)


def inputs_row_count(selected_paths: list[Path], candidates: list[Path]) -> int:
    sel_set = {p.resolve() for p in selected_paths}
    rest = sum(1 for p in candidates if p.resolve() not in sel_set)
    return len(selected_paths) + rest + 1  # + next row


def inputs_row_is_next(cursor: int, selected_paths: list[Path], candidates: list[Path]) -> bool:
    return cursor >= inputs_row_count(selected_paths, candidates) - 1


def inputs_path_at(
    cursor: int, selected_paths: list[Path], candidates: list[Path]
) -> Path | None:
    """Path under cursor, or None if on next-row / OOB."""
    if cursor < 0:
        return None
    if cursor < len(selected_paths):
        return selected_paths[cursor]
    sel_set = {p.resolve() for p in selected_paths}
    rest = [p for p in candidates if p.resolve() not in sel_set]
    i = cursor - len(selected_paths)
    if 0 <= i < len(rest):
        return rest[i]
    return None


def format_new_confirm_dropdown(
    *,
    template_name: str,
    template_id: str,
    inputs: list[Path],
    output: str,
    model: str,
    width: int = 72,
) -> tuple[str, int]:
    """③ confirm + start."""
    files = ", ".join(p.name for p in inputs[:3])
    if len(inputs) > 3:
        files += f" 等{len(inputs)}个"
    tlabel = template_name or template_id or "—"
    lines = [
        f"[bold {COLOR_BRAND}]新建 ③/3[/bold {COLOR_BRAND}]  "
        f"确认开始  ·  Enter 生成  ·  Esc 取消",
        f"  模板  {tlabel}",
        f"  资料  {files or '—'}",
        f"  输出  {output or 'output.md'}",
        f"  模型  {model or '—'}",
        f"[bold {COLOR_BRAND}] ›  开始生成[/bold {COLOR_BRAND}]",
        "[dim]  （可在输入框改输出文件名后 Enter）[/dim]",
    ]
    return "\n".join(lines), len(lines)
