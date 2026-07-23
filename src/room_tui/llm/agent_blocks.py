"""Classify and hold Pi Agent content blocks for rich TUI rendering."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentBlock:
    """One display unit from a Pi agent turn."""

    kind: str  # text | thinking | thought | tool | code | diff | json | error | plain
    text: str = ""
    tool_name: str = ""
    tool_args: Any = None
    tool_result: Any = None
    is_error: bool = False
    language: str = ""  # for code fences
    # Grok collapsed thinking header: "Thought for Xs"
    elapsed_s: float | None = None

    def to_dict(self, *, max_result: int = 6000) -> dict[str, Any]:
        """Serialize for chat-history / blocks.json (truncate heavy tool output)."""
        result = self.tool_result
        if result is not None and max_result > 0:
            if isinstance(result, str) and len(result) > max_result:
                result = result[: max_result - 1] + "…"
            else:
                try:
                    raw = json.dumps(result, ensure_ascii=False, default=str)
                    if len(raw) > max_result:
                        result = raw[: max_result - 1] + "…"
                except Exception:
                    s = str(result)
                    result = s if len(s) <= max_result else s[: max_result - 1] + "…"
        d: dict[str, Any] = {"kind": self.kind}
        if self.text:
            d["text"] = self.text
        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.tool_args is not None:
            d["tool_args"] = self.tool_args
        if result is not None:
            d["tool_result"] = result
        if self.is_error:
            d["is_error"] = True
        if self.language:
            d["language"] = self.language
        if self.elapsed_s is not None:
            d["elapsed_s"] = float(self.elapsed_s)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentBlock":
        return cls(
            kind=str(data.get("kind") or "text"),
            text=str(data.get("text") or ""),
            tool_name=str(data.get("tool_name") or ""),
            tool_args=data.get("tool_args"),
            tool_result=data.get("tool_result"),
            is_error=bool(data.get("is_error")),
            language=str(data.get("language") or ""),
            elapsed_s=(
                float(data["elapsed_s"])
                if data.get("elapsed_s") is not None
                else None
            ),
        )


@dataclass
class AgentTurn:
    """Structured result of one Pi agent chat turn."""

    blocks: list[AgentBlock] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    raw_text: str = ""  # flat fallback

    def assistant_text(self) -> str:
        parts = [b.text for b in self.blocks if b.kind in ("text", "plain", "markdown") and b.text]
        return "\n\n".join(parts).strip()

    def to_history_blocks(
        self,
        *,
        thought_elapsed_s: float | None = None,
        collapse_thinking: bool = True,
    ) -> list[dict[str, Any]]:
        """Grok-like durable entries: Thought for Xs + tools + final text.

        When ``collapse_thinking``, raw thinking bodies become a single
        collapsed ``thought`` row (process stays visible after the turn).
        """
        out: list[dict[str, Any]] = []
        thinking_seen = False
        for b in self.blocks:
            if b.kind == "thinking":
                if collapse_thinking:
                    if not thinking_seen:
                        elapsed = thought_elapsed_s
                        if elapsed is None:
                            elapsed = b.elapsed_s
                        out.append(
                            AgentBlock(
                                kind="thought",
                                elapsed_s=elapsed if elapsed and elapsed > 0 else None,
                                text=(b.text or "")[:2000],
                            ).to_dict()
                        )
                        thinking_seen = True
                    continue
                out.append(b.to_dict())
            elif b.kind in ("text", "plain", "markdown", "code", "diff", "json", "error", "tool"):
                out.append(b.to_dict())
            else:
                out.append(b.to_dict())
        # Thinking-only / empty answer turns still record the thought header.
        if (
            collapse_thinking
            and not thinking_seen
            and thought_elapsed_s is not None
            and thought_elapsed_s > 0.05
        ):
            out.insert(
                0,
                AgentBlock(kind="thought", elapsed_s=thought_elapsed_s).to_dict(),
            )
        # Drop pure-empty text rows
        cleaned: list[dict[str, Any]] = []
        for d in out:
            if d.get("kind") in ("text", "plain", "markdown") and not str(
                d.get("text") or ""
            ).strip():
                continue
            cleaned.append(d)
        return cleaned


def _short_json(obj: Any, limit: int = 240) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        s = str(obj)
    s = s.strip()
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def blocks_from_pi_message(message: dict[str, Any]) -> list[AgentBlock]:
    """Extract blocks from a Pi JSON ``message`` object (role assistant/user)."""
    out: list[AgentBlock] = []
    content = message.get("content")
    if isinstance(content, str):
        if content.strip():
            out.append(AgentBlock(kind="text", text=content))
        return out
    if not isinstance(content, list):
        return out
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type") or "")
        if ptype == "text":
            t = str(part.get("text") or "")
            if t.strip():
                out.append(AgentBlock(kind="text", text=t))
        elif ptype == "thinking":
            t = str(part.get("thinking") or part.get("text") or "")
            if t.strip():
                out.append(AgentBlock(kind="thinking", text=t))
        elif ptype in ("toolCall", "tool_call", "toolUse", "tool_use"):
            name = str(part.get("name") or part.get("toolName") or "tool")
            args = part.get("arguments") or part.get("args") or part.get("input")
            out.append(
                AgentBlock(
                    kind="tool",
                    tool_name=name,
                    tool_args=args,
                    text=f"{name}({_short_json(args, 120)})",
                )
            )
        elif ptype in ("image", "image_url"):
            out.append(AgentBlock(kind="plain", text="[image]"))
        else:
            # Unknown structured part — show compact JSON
            out.append(AgentBlock(kind="json", text=_short_json(part, 400)))
    return out


def apply_tool_execution(
    blocks: list[AgentBlock],
    *,
    tool_name: str,
    args: Any,
    result: Any,
    is_error: bool,
) -> None:
    """Merge tool_execution_end into an existing tool block or append one."""
    for b in reversed(blocks):
        if b.kind == "tool" and b.tool_name == tool_name and b.tool_result is None:
            b.tool_args = args if args is not None else b.tool_args
            b.tool_result = result
            b.is_error = is_error
            return
    blocks.append(
        AgentBlock(
            kind="tool",
            tool_name=tool_name or "tool",
            tool_args=args,
            tool_result=result,
            is_error=is_error,
            text=tool_name or "tool",
        )
    )


# ── text-mode fallback classifiers ─────────────────────────

_DIFF_RE = re.compile(r"(?m)^(?:diff --git |--- |\+\+\+ |@@ )")
_JSON_OBJ_RE = re.compile(r"^\s*[\{\[]")


def classify_plain_text(text: str) -> list[AgentBlock]:
    """Split unstructured agent text into renderable blocks (no JSON stream)."""
    body = (text or "").strip()
    if not body:
        return []

    # Pure JSON document
    if _JSON_OBJ_RE.match(body):
        try:
            parsed = json.loads(body)
            return [AgentBlock(kind="json", text=json.dumps(parsed, ensure_ascii=False, indent=2))]
        except json.JSONDecodeError:
            pass

    # Unified diff (whole body)
    if _DIFF_RE.search(body) and body.count("\n") >= 2:
        # if mostly diff lines
        lines = body.splitlines()
        diffish = sum(
            1
            for ln in lines
            if ln.startswith(("diff ", "---", "+++", "@@", "+", "-", " "))
        )
        if diffish >= max(3, len(lines) * 0.5):
            return [AgentBlock(kind="diff", text=body)]

    # Split fenced code blocks from surrounding markdown.
    # Allow optional space after lang; treat unclosed ```…EOF as a code block
    # (models often leak fences — still paint as Grok code band).
    blocks: list[AgentBlock] = []
    fence = re.compile(
        r"```([^\n`]*)[ \t]*\r?\n(.*?)(?:```|$)",
        re.DOTALL,
    )
    pos = 0
    for m in fence.finditer(body):
        pre = body[pos : m.start()].strip()
        if pre:
            blocks.append(AgentBlock(kind="text", text=pre))
        lang = (m.group(1) or "").strip().lower()
        # Strip accidental brace noise from lang tag
        lang = re.sub(r"[^\w+-].*$", "", lang)
        code = m.group(2).rstrip("\n")
        # Drop trailing lone fence line if partially matched
        if code.endswith("```"):
            code = code[: -3].rstrip("\n")
        if lang in ("diff", "udiff", "patch") or _DIFF_RE.search(code):
            blocks.append(AgentBlock(kind="diff", text=code, language=lang or "diff"))
        elif lang in ("json",) or (
            not lang and _JSON_OBJ_RE.match(code.strip())
        ):
            try:
                parsed = json.loads(code)
                blocks.append(
                    AgentBlock(
                        kind="json",
                        text=json.dumps(parsed, ensure_ascii=False, indent=2),
                        language="json",
                    )
                )
            except json.JSONDecodeError:
                blocks.append(AgentBlock(kind="code", text=code, language=lang or "json"))
        else:
            blocks.append(AgentBlock(kind="code", text=code, language=lang or "text"))
        pos = m.end()
    tail = body[pos:].strip()
    if tail:
        # Unfenced leftover that still looks like a fence open — paint as code.
        if tail.startswith("```"):
            m2 = re.match(r"```([^\n`]*)[ \t]*\r?\n?(.*)$", tail, re.DOTALL)
            if m2:
                lang = (m2.group(1) or "").strip().lower()
                lang = re.sub(r"[^\w+-].*$", "", lang)
                code = (m2.group(2) or "").rstrip("\n")
                blocks.append(
                    AgentBlock(kind="code", text=code, language=lang or "text")
                )
            else:
                blocks.append(AgentBlock(kind="text", text=tail))
        else:
            blocks.append(AgentBlock(kind="text", text=tail))
    if not blocks:
        blocks.append(AgentBlock(kind="text", text=body))
    return blocks
