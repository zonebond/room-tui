"""Slash command registry + Tab completion (Grok-like: builtins + skills).

Typing ``/`` matches both built-in Room commands and discovered Skills, so
``/paper-derived`` works the same way as in Grok Build (skills as first-class
slash targets). Builtins win on exact name collision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from room_tui.ui_state import COLOR_BRAND

SlashKind = Literal["cmd", "skill", "model", "hint"]

# Slash label color — Room primary blue (title bar / accents).
_SLASH_FG = COLOR_BRAND


@dataclass(frozen=True)
class SlashItem:
    """One slash target: builtin command or skill."""

    name: str  # without leading /
    description: str
    kind: SlashKind = "cmd"
    aliases: tuple[str, ...] = ()
    # skill-only
    skill_path: str = ""
    skill_version: str = ""

    @property
    def forms(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    @property
    def badge(self) -> str:
        return "skill" if self.kind == "skill" else "cmd"


# Backward-compat alias used by older imports
SlashCommand = SlashItem


_BUILTIN: list[SlashItem] = [
    SlashItem("new", "新建生成  ·  ①模板 ②资料 ③确认（主界面内联）", "cmd", ("n",)),
    SlashItem("continue", "继续本项目未完成任务", "cmd", ("c", "cont", "resume")),
    SlashItem("pause", "暂停当前生成", "cmd", ("p",)),
    SlashItem("cancel", "取消当前生成/Agent", "cmd", ("x", "stop")),
    SlashItem("status", "本项目任务状态", "cmd", ("s",)),
    SlashItem(
        "model",
        "切换模型  ·  /model list  ·  /model setup 连接  ·  /model <id>",
        "cmd",
        ("m", "models"),
    ),
    SlashItem(
        "setup",
        "首次配置 Room 模型 API Key（写入 agent/auth.json）",
        "cmd",
        ("auth", "login"),
    ),
    SlashItem(
        "template",
        "模板  ·  list / register [--fast] / show / delete  ·  生成用 /new",
        "cmd",
        ("tpl", "templates"),
    ),
    SlashItem("skills", "列出可用 Skills", "cmd", ("skill-list",)),
    SlashItem(
        "skill",
        "Skill 管理  ·  /skill <name> [提问]  ·  /skill clear",
        "cmd",
        ("sk",),
    ),
    SlashItem("refresh", "刷新界面", "cmd", ("r",)),
    SlashItem("clear", "清空消息区", "cmd", ("cls",)),
    SlashItem(
        "rewind",
        "回退到某条用户消息之前  ·  Esc Esc",
        "cmd",
        ("rw", "undo"),
    ),
    SlashItem("help", "帮助", "cmd", ("h", "?")),
    SlashItem("quit", "退出", "cmd", ("exit", "q")),
]

# Public list used by help (builtins only; skills are dynamic)
SLASH_COMMANDS: list[SlashItem] = list(_BUILTIN)

# Cache of skill items (refreshed on demand)
_skill_cache: list[SlashItem] = []
_skill_cache_loaded: bool = False


def invalidate_skill_cache() -> None:
    global _skill_cache, _skill_cache_loaded
    _skill_cache = []
    _skill_cache_loaded = False


def _load_skill_items(*, force: bool = False) -> list[SlashItem]:
    global _skill_cache, _skill_cache_loaded
    if _skill_cache_loaded and not force:
        return _skill_cache
    try:
        from room_tui.pi_catalog import list_skills

        skills = list_skills()
    except Exception:
        skills = []
    builtin_names = {b.name.lower() for b in _BUILTIN}
    for a in _BUILTIN:
        builtin_names.update(x.lower() for x in a.aliases)
    items: list[SlashItem] = []
    for s in skills:
        name = s.name.strip()
        if not name:
            continue
        # On collision with builtin, qualify: local:name (Grok-style re-home)
        slash_name = name
        if name.lower() in builtin_names:
            slash_name = f"skill:{name}"
        desc = (s.description or f"Skill · {s.path}").strip()
        if len(desc) > 80:
            desc = desc[:79] + "…"
        if s.version:
            desc = f"{desc}  v{s.version}"
        path = str(s.path.parent if s.path.is_file() else s.path)
        items.append(
            SlashItem(
                name=slash_name,
                description=desc,
                kind="skill",
                skill_path=path,
                skill_version=s.version or "",
            )
        )
    _skill_cache = items
    _skill_cache_loaded = True
    return _skill_cache


def all_slash_items(*, refresh_skills: bool = False) -> list[SlashItem]:
    """Builtins first, then skills (Grok registry order: local cmds + dynamic)."""
    skills = _load_skill_items(force=refresh_skills)
    return list(_BUILTIN) + skills


def match_slash(prefix: str, *, include_skills: bool = True) -> list[SlashItem]:
    """Match commands/skills whose name/alias starts with token (or fuzzy contains)."""
    p = prefix.strip()
    if p.startswith("/"):
        p = p[1:]
    token = p.split(" ", 1)[0].lower()
    items = all_slash_items() if include_skills else list(_BUILTIN)
    if token == "" and prefix.strip() in ("", "/"):
        return items

    prefix_hits: list[SlashItem] = []
    contain_hits: list[SlashItem] = []
    for item in items:
        forms = [f.lower() for f in item.forms]
        if any(f.startswith(token) for f in forms):
            prefix_hits.append(item)
        elif any(token in f for f in forms) or token in item.description.lower():
            contain_hits.append(item)
    # Prefix matches first (stable), then weaker contains matches
    return prefix_hits or contain_hits


def complete_slash(value: str) -> tuple[str | None, list[SlashItem]]:
    """
    Tab-complete current slash input (commands + skills).
    Returns (new_value_or_None, matches).
    """
    raw = value
    if not raw.startswith("/"):
        return None, []
    rest = raw[1:]
    # Argument completion for /model and /skill
    if " " in rest:
        cmd, _, arg = rest.partition(" ")
        return _complete_slash_args(cmd.strip().lower(), arg, raw)

    token = rest.lower()
    matches = match_slash("/" + token)
    if not matches:
        return None, []
    if len(matches) == 1:
        # Skills leave a trailing space so user can add a prompt
        return f"/{matches[0].name} ", matches
    names = [m.name for m in matches]
    lcp = _longest_common_prefix(names)
    if lcp and len(lcp) > len(token):
        return f"/{lcp}", matches
    return f"/{matches[0].name} ", matches


def _complete_slash_args(
    cmd: str, arg: str, raw: str
) -> tuple[str | None, list[SlashItem]]:
    """Tab-complete arguments for /model and /skill."""
    # resolve alias
    for b in _BUILTIN:
        if cmd in b.forms:
            cmd = b.name
            break

    if cmd in ("model", "models", "m"):
        return _complete_model_arg(arg)
    if cmd in ("skill", "sk"):
        return _complete_skill_arg(arg)
    return None, []


def _model_brand_label(provider_id: str) -> str:
    """Short product brand for dropdown: DeepSeek / LM Studio / …"""
    try:
        from room_tui.auth_setup import find_preset

        p = find_preset(provider_id)
        if p:
            # Compact: drop parenthetical noise in the list
            return (
                p.label.replace(" (self-hosting)", "")
                .replace(" (CN)", "")
                .strip()
            )
    except Exception:
        pass
    return (provider_id or "").strip() or "?"


def _empty_model_hints() -> list[SlashItem]:
    """Rows shown when no models are configured / catalog is empty."""
    return [
        SlashItem(
            name="__setup__",
            description="尚未配置任何模型",
            kind="hint",
        ),
        SlashItem(
            name="__setup__",
            description="Ctrl+M 连接服务商与密钥",
            kind="hint",
        ),
        SlashItem(
            name="__setup__",
            description="或输入 /model setup",
            kind="hint",
        ),
    ]


def _complete_model_arg(arg: str) -> tuple[str | None, list[SlashItem]]:
    from room_tui.pi_catalog import list_models

    q = (arg or "").strip()
    # Don't complete subcommands further
    if q.lower() in ("list", "ls", "setup", "auth", "login") or q.lower().startswith(
        ("list ", "ls ", "setup ")
    ):
        return None, []
    models = list_models(q, prefer_enabled=True)
    if not models and q:
        models = list_models(q, prefer_enabled=False)
    if not models:
        models = list_models("", prefer_enabled=True)
    items = [
        SlashItem(
            name=m.spec,  # completion token stays provider/model
            description=_model_brand_label(m.provider),  # brand for display
            kind="model",
        )
        for m in models[:40]
    ]
    if not items:
        # New install / no keys — never show a blank dropdown
        return None, _empty_model_hints()
    # Filter by arg prefix on full spec or brand
    token = q.lower()
    if token:
        filtered = [
            i
            for i in items
            if i.name.lower().startswith(token)
            or token in i.name.lower()
            or token in (i.description or "").lower()
        ]
        items = filtered or items
    if not items:
        return None, [
            SlashItem(
                name="__empty__",
                description=f"无匹配「{q}」· 清空再试 或 Ctrl+M",
                kind="hint",
            )
        ]
    if len(items) == 1:
        return f"/model {items[0].name} ", items
    # common prefix of specs
    lcp = _longest_common_prefix([i.name for i in items])
    if lcp and len(lcp) > len(q):
        return f"/model {lcp}", items
    return f"/model {items[0].name} ", items


def _complete_skill_arg(arg: str) -> tuple[str | None, list[SlashItem]]:
    skills = _load_skill_items()
    # arg may be "name" or "name rest of prompt"
    token = (arg or "").split(" ", 1)[0].lower()
    if not token:
        return f"/skill {skills[0].name} " if skills else None, skills[:20]
    hits = [
        s
        for s in skills
        if s.name.lower().startswith(token)
        or token in s.name.lower()
        or s.name.lower().removeprefix("skill:").startswith(token)
    ]
    if not hits:
        return None, []
    # bare skill name without skill: prefix for completion under /skill
    bare = []
    for s in hits:
        n = s.name.removeprefix("skill:")
        bare.append(
            SlashItem(
                name=n,
                description=s.description,
                kind="skill",
                skill_path=s.skill_path,
                skill_version=s.skill_version,
            )
        )
    if len(bare) == 1:
        # Keep trailing space only if no more prompt yet
        rest = arg[len(token) :] if arg.lower().startswith(token) else ""
        if rest.startswith(" "):
            return None, bare  # already past skill name
        return f"/skill {bare[0].name} ", bare
    lcp = _longest_common_prefix([b.name for b in bare])
    if lcp and len(lcp) > len(token):
        return f"/skill {lcp}", bare
    return f"/skill {bare[0].name} ", bare


def _longest_common_prefix(strs: list[str]) -> str:
    if not strs:
        return ""
    s1 = min(strs)
    s2 = max(strs)
    for i, ch in enumerate(s1):
        if i >= len(s2) or s2[i] != ch:
            return s1[:i]
    return s1


def format_suggestions(matches: list[SlashItem], *, max_n: int = 10) -> str:
    """One-line suggestion strip with skill badge (compact fallback)."""
    if not matches:
        return ""
    parts: list[str] = []
    for m in matches[:max_n]:
        if m.kind == "skill":
            parts.append(f"[{_SLASH_FG}]/{m.name}[/{_SLASH_FG}][dim]·sk[/dim]")
        else:
            parts.append(f"[{_SLASH_FG}]/{m.name}[/{_SLASH_FG}]")
    extra = f"  [dim]+{len(matches) - max_n}[/dim]" if len(matches) > max_n else ""
    return "  ".join(parts) + extra


# Max rows shown in the Grok-style multi-line slash dropdown.
MAX_DROPDOWN_ROWS = 8
# Label column pad (cells of "/name"); longer names still render fully then pad.
_LABEL_PAD = 22


def _markup_literal(text: str) -> str:
    """Escape *text* so Textual/Rich treat it as plain content, not markup.

    ``rich.markup.escape`` / ``textual.markup.escape`` only escape tags that
    look like ``[a-z#/@...]`` (lowercase).  Brand labels such as
    ``[DeepSeek]`` start with an uppercase letter, so they are *not* escaped —
    then Textual's Content.from_markup **drops** them as unknown styles,
    which is why the /model list showed only bare model ids.

    Always backslash-escape ``[`` (and existing backslashes) for dynamic text.
    """
    return (text or "").replace("\\", "\\\\").replace("[", "\\[")


def format_dropdown(
    matches: list[SlashItem],
    *,
    selected: int = 0,
    max_n: int = MAX_DROPDOWN_ROWS,
) -> tuple[str, int]:
    """Grok-style multi-line dropdown.

    Returns ``(markup, visible_row_count)``. Each row:

        ❯ /name····  cmd|skill  description
          /other···  skill      …
    """
    if not matches:
        return "[dim]无匹配命令或 Skill[/dim]", 1

    sel = max(0, min(selected, len(matches) - 1))
    # Window so selected stays visible when list is long
    if len(matches) <= max_n:
        start, end = 0, len(matches)
    else:
        half = max_n // 2
        start = max(0, min(sel - half, len(matches) - max_n))
        end = start + max_n

    # Align label column on the visible window
    label_w = min(
        max(len(f"/{m.name}") for m in matches[start:end]),
        36,
    )
    label_w = max(label_w, 12)

    lines: list[str] = []
    for i in range(start, end):
        m = matches[i]
        is_sel = i == sel
        prefix = "❯ " if is_sel else "  "
        label = f"/{m.name}"
        pad = " " * max(1, label_w - len(label) + 2)
        kind = "skill" if m.kind == "skill" else "cmd  "
        desc = (m.description or "").replace("\n", " ").strip()
        if len(desc) > 52:
            desc = desc[:51] + "…"
        if is_sel:
            # Selected: brand-blue name; description bright white for readability
            lines.append(
                f"[bold {_SLASH_FG}]{prefix}{label}[/bold {_SLASH_FG}]{pad}"
                f"[bold]{kind}[/bold]  [bold white]{desc}[/bold white]"
            )
        else:
            # Unselected: white label; muted kind + description
            lines.append(
                f"[white]{prefix}{label}[/white]{pad}"
                f"[dim]{kind}[/dim]  [dim]{desc}[/dim]"
            )

    # Footer count when truncated
    if len(matches) > max_n:
        lines.append(
            f"[dim]  {sel + 1}/{len(matches)}  ·  ↑↓ 选择  ·  Tab 补全  ·  Enter 执行[/dim]"
        )
    else:
        lines.append(
            f"[dim]  ↑↓ 选择  ·  Tab 补全  ·  Enter 执行  ·  {len(matches)} 项[/dim]"
        )
    return "\n".join(lines), len(lines)


def _model_display_parts(item: SlashItem) -> tuple[str, str]:
    """Return ``(brand, model_id)`` for a model SlashItem (name = full spec)."""
    from room_tui.pi_catalog import parse_model_spec

    brand = (item.description or "").strip()
    prov, mid = parse_model_spec(item.name)
    if not brand:
        brand = _model_brand_label(prov)
    model_id = (mid or item.name or "").strip()
    # Avoid "deepseek/deepseek-v4" double brand in model column when model embeds path
    if prov and model_id.startswith(prov + "/"):
        model_id = model_id[len(prov) + 1 :]
    return brand, model_id


def format_arg_dropdown(
    matches: list[SlashItem],
    *,
    selected: int = 0,
    kind_label: str = "arg",
    max_n: int = MAX_DROPDOWN_ROWS,
    current_spec: str = "",
) -> tuple[str, int]:
    """Dropdown for /model and /skill argument completion.

    Model rows render as::

        ❯ [DeepSeek]   deepseek-v4-flash     ●
          [LM Studio]  qwen/qwen3.6-35b-a3b
    """
    if not matches:
        if kind_label == "model":
            return _format_model_empty_dropdown()
        return f"[dim]无匹配 {kind_label}[/dim]", 1
    if matches and matches[0].kind == "hint":
        return _format_hint_dropdown(matches, selected=selected)
    if kind_label == "model" or (matches and matches[0].kind == "model"):
        return _format_model_dropdown(
            matches,
            selected=selected,
            max_n=max_n,
            current_spec=current_spec,
        )

    sel = max(0, min(selected, len(matches) - 1))
    if len(matches) <= max_n:
        start, end = 0, len(matches)
    else:
        half = max_n // 2
        start = max(0, min(sel - half, len(matches) - max_n))
        end = start + max_n
    label_w = min(max(len(m.name) for m in matches[start:end]), 40)
    label_w = max(label_w, 10)
    lines: list[str] = []
    for i in range(start, end):
        m = matches[i]
        is_sel = i == sel
        prefix = "❯ " if is_sel else "  "
        pad = " " * max(1, label_w - len(m.name) + 2)
        desc = (m.description or "")[:48]
        if is_sel:
            lines.append(
                f"[bold {_SLASH_FG}]{prefix}{m.name}[/bold {_SLASH_FG}]{pad}"
                f"[bold white]{desc}[/bold white]"
            )
        else:
            lines.append(
                f"[white]{prefix}{m.name}[/white]{pad}[dim]{desc}[/dim]"
            )
    lines.append(
        f"[dim]  {kind_label}  ·  ↑↓  ·  Tab  ·  Enter  ·  {len(matches)}[/dim]"
    )
    return "\n".join(lines), len(lines)


def _format_model_empty_dropdown() -> tuple[str, int]:
    """High-contrast empty state (must stay readable on surface-lighten bg)."""
    lines = [
        f"[bold {_SLASH_FG}]选择模型[/bold {_SLASH_FG}]  [bold]尚未配置[/bold]",
        f"[{_SLASH_FG}]────────────────────────────────────────[/{_SLASH_FG}]",
        "[bold white]还没有可用模型[/bold white]",
        "[white]新装 Room 需要先连接 LLM 服务商[/white]",
        "",
        f"[bold {_SLASH_FG}]Ctrl+M[/bold {_SLASH_FG}]  [bold white]连接模型（推荐）[/bold white]",
        "[white]或输入[/white]  [bold]/model setup[/bold]",
        "",
        "[bold]Enter[/bold] 打开连接  ·  [bold]Esc[/bold] 关闭",
    ]
    return "\n".join(lines), len(lines)


def _format_hint_dropdown(
    matches: list[SlashItem],
    *,
    selected: int = 0,
) -> tuple[str, int]:
    """Empty-catalog guidance rows (not selectable model ids)."""
    lines = [
        f"[bold {_SLASH_FG}]  选择模型[/bold {_SLASH_FG}]  [dim]引导[/dim]",
        "[dim]  ────────────────────────────────────[/dim]",
    ]
    sel = max(0, min(selected, len(matches) - 1))
    for i, m in enumerate(matches):
        is_sel = i == sel
        prefix = "❯ " if is_sel else "  "
        desc = _markup_literal((m.description or "").strip())
        if is_sel:
            lines.append(f"[bold {_SLASH_FG}]{prefix}{desc}[/bold {_SLASH_FG}]")
        else:
            lines.append(f"[white]{prefix}{desc}[/white]")
    lines.append(
        f"[dim]  Enter / Ctrl+M 打开连接  ·  Esc 关闭[/dim]"
    )
    return "\n".join(lines), len(lines)


def _format_model_dropdown(
    matches: list[SlashItem],
    *,
    selected: int = 0,
    max_n: int = MAX_DROPDOWN_ROWS,
    current_spec: str = "",
) -> tuple[str, int]:
    """Polished /model list: aligned ``[品牌]`` + model id (+ ● current)."""
    sel = max(0, min(selected, len(matches) - 1))
    if len(matches) <= max_n:
        start, end = 0, len(matches)
    else:
        half = max_n // 2
        start = max(0, min(sel - half, len(matches) - max_n))
        end = start + max_n

    rows: list[tuple[str, str, str]] = []  # brand, model_id, full_spec
    for m in matches[start:end]:
        brand, mid = _model_display_parts(m)
        rows.append((brand, mid, m.name))

    # Fixed brand column width (display cells ≈ char len for CJK-safe ASCII brands)
    brand_inner_w = max((len(b) for b, _, _ in rows), default=8)
    brand_inner_w = min(max(brand_inner_w, 8), 14)
    model_w = max((len(mid) for _, mid, _ in rows), default=12)
    model_w = min(max(model_w, 12), 36)

    cur = (current_spec or "").strip()
    lines: list[str] = [
        f"[bold {_SLASH_FG}]  选择模型[/bold {_SLASH_FG}]"
        + (f"  [dim]当前 {_markup_literal(cur)}[/dim]" if cur else ""),
        "[dim]  ────────────────────────────────────────[/dim]",
    ]
    for idx, (brand, mid, spec) in enumerate(rows):
        i = start + idx
        is_sel = i == sel
        is_cur = bool(cur) and (
            spec == cur
            or mid == cur
            or spec.endswith("/" + cur)
            or cur.endswith("/" + mid)
        )
        prefix = "❯ " if is_sel else "  "
        brand_s = (brand or "?").strip()
        # Pad brand inside brackets for column align: [DeepSeek ] style no —
        # pad after closing bracket instead
        tag = f"[{brand_s}]"
        tag_pad = " " * max(1, brand_inner_w + 2 - len(tag) + 1)
        mid_pad = " " * max(1, model_w - len(mid) + 1)
        cur_mark = " [bold green]●[/bold green]" if is_cur else ""
        tag_m = _markup_literal(tag)
        mid_m = _markup_literal(mid)
        if is_sel:
            lines.append(
                f"{prefix}[bold {_SLASH_FG}]{tag_m}[/bold {_SLASH_FG}]"
                f"{tag_pad}"
                f"[bold white]{mid_m}[/bold white]"
                f"{mid_pad}{cur_mark}"
            )
        else:
            lines.append(
                f"{prefix}[dim]{tag_m}[/dim]"
                f"{tag_pad}"
                f"[white]{mid_m}[/white]"
                f"{mid_pad}{cur_mark}"
            )

    if len(matches) > max_n:
        lines.append(
            f"[dim]  {sel + 1}/{len(matches)}  ·  ↑↓  ·  Tab  ·  Enter 切换  ·  Esc[/dim]"
        )
    else:
        lines.append(
            f"[dim]  ↑↓ 选择  ·  Tab 补全  ·  Enter 切换  ·  {len(matches)} 个模型[/dim]"
        )
    return "\n".join(lines), len(lines)


def resolve_slash_token(token: str) -> SlashItem | None:
    """Resolve a fully typed token to a single item (builtin preferred)."""
    t = token.strip().lower()
    if t.startswith("/"):
        t = t[1:]
    if not t:
        return None
    # exact builtin
    for b in _BUILTIN:
        if t in {f.lower() for f in b.forms}:
            return b
    # exact skill
    for s in _load_skill_items():
        if t == s.name.lower() or t == s.name.lower().removeprefix("skill:"):
            return s
        # path parent name
        if Path(s.skill_path).name.lower() == t:
            return s
    # unique prefix among all
    hits = match_slash("/" + t)
    if len(hits) == 1:
        return hits[0]
    return None


def help_text() -> str:
    lines = [
        "[bold]取消 / 退出（连续两次才生效）[/bold]",
        "  [cyan]Esc×2[/cyan] / [cyan]Ctrl+C×2[/cyan]  运行中 → 取消当前任务",
        "  [cyan]Ctrl+C×2[/cyan]              空闲时 → 退出 Room",
        "  [cyan]Esc[/cyan] 单次               清空输入 / 失焦（不退出）",
        "",
        "[bold]其它退出[/bold]",
        "  [cyan]Ctrl+Q[/cyan] / [cyan]/quit[/cyan]   立即正常退出",
        "",
        "[bold]编辑[/bold]",
        "  [cyan]Tab[/cyan]        / 命令与 Skill 补全；否则切换 输入框↔消息区",
        "  [cyan]Enter[/cyan]      发送",
        "  [cyan]Ctrl+M[/cyan]     连接模型（服务商 / 密钥 / 本机）",
        "  [cyan]/model[/cyan]      选择/切换模型",
        "  [cyan]/model list[/cyan]  列出可用模型",
        "  [cyan]/template[/cyan]   模板列表/注册  ·  --fast 快速注册",
        "",
        "[bold]/SLASH 命令[/bold]  ·  Tab 补全  ·  Enter 执行",
        "  输入 [cyan]/[/cyan] 同时匹配 [bold]内置指令[/bold] 与 [bold]Skills[/bold]（与 Grok Build 相同）",
        "",
    ]
    for m in _BUILTIN:
        alias = (
            f"  [dim]({', '.join('/' + a for a in m.aliases)})[/dim]"
            if m.aliases
            else ""
        )
        lines.append(f"  [{_SLASH_FG}]/{m.name}[/{_SLASH_FG}]{alias}  {m.description}")
    skills = _load_skill_items()
    if skills:
        lines.append("")
        lines.append(f"[bold]Skills[/bold]  ·  {len(skills)} 个  ·  /name 或 /skill name")
        for s in skills[:12]:
            lines.append(
                f"  [{_SLASH_FG}]/{s.name}[/{_SLASH_FG}]  {s.description[:60]}"
            )
        if len(skills) > 12:
            lines.append(f"  [dim]… +{len(skills) - 12}  ·  /skills[/dim]")
    lines.append("")
    lines.append("也支持自然语言；Skill 也可 /skill <name> <问题> 直接调用")
    return "\n".join(lines)
