"""Engine errors and cross-platform humanization of CLI failure dumps."""

from __future__ import annotations

import re
import sys


class EngineError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        cmd: list[str] | None = None,
        returncode: int | None = None,
        stderr: str = "",
        stdout: str = "",
    ):
        super().__init__(message)
        self.cmd = cmd or []
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


# Exception lines Python prints at the end of a traceback
_EXC_LINE = re.compile(
    r"^(?:[A-Za-z_][\w.]*\.)*"
    r"(?:RuntimeError|ValueError|FileNotFoundError|OSError|IOError|"
    r"PermissionError|TimeoutError|KeyError|TypeError|ImportError|"
    r"ModuleNotFoundError|EngineError|ClickException|SystemExit)"
    r":\s*(.+)$"
)


def humanize_engine_error(
    message: str = "",
    *,
    stderr: str = "",
    stdout: str = "",
    sample_suffix: str = "",
) -> str:
    """Turn engine/pi dump text into a short, actionable Chinese/English line.

    Prefer real root cause over generic 「引擎调用失败」.
    """
    parts = [message or "", stderr or "", stdout or ""]
    blob = "\n".join(p for p in parts if p).strip()
    if not blob:
        return "未知错误"

    # ── known product patterns (order matters) ─────────────────
    low = blob.lower()
    suf = (sample_suffix or "").lower()

    if "无法读取旧版 .doc" in blob or "无法读取 .doc" in blob or (
        "无法读取" in blob and ".doc" in blob
    ):
        # Keep engine-side Word/COM detail (e.g. "Word: RPC … · 请先手动打开 Word")
        word_detail = ""
        m_word = re.search(r"Word:\s*(.+?)(?:\s*LibreOffice:|$)", blob, re.S)
        if m_word:
            word_detail = " ".join(m_word.group(1).split())[:140]
        if sys.platform == "win32":
            base = (
                "无法读取旧版 .doc · 请安装 LibreOffice 或桌面版 Word/WPS 后重试，"
                "或另存为 .docx / .md"
            )
            if word_detail:
                return f"{base} · {word_detail}"
            return base
        return (
            "无法读取 .doc · 请安装 LibreOffice / textutil / antiword，"
            "或另存为 .docx / .md"
        )

    if "LibreOffice" in blob and (".doc" in low or suf == ".doc"):
        return (
            "无法读取 .doc · 安装 LibreOffice 或 Word 后重试，"
            "或另存为 .docx / .md"
        )

    if "python-docx" in low or "读取 .docx 需要" in blob:
        return "读取 .docx 需要 python-docx · 请检查 paper-derived 运行环境"

    if "template_id_exists" in blob or ("模板 id" in blob and "已存在" in blob):
        m = re.search(r"\{[\s\S]*\}", blob)
        if m:
            try:
                import json

                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    msg = str(data.get("message") or "").strip()
                    eid = str(data.get("existing_id") or "").strip()
                    if msg:
                        if eid and "delete" not in msg.lower() and "删除" not in msg:
                            return f"{msg} · /template delete {eid}"
                        return msg
            except Exception:
                pass

    if "textutil" in low and ("超时" in blob or "timeout" in low or "不可用" in blob):
        return "macOS textutil 转换 .doc 失败 · 请另存为 .docx 后注册"

    if "antiword" in low or "catdoc" in low:
        return "无法用 antiword/catdoc 读取 .doc · 请转为 .docx/.md"

    if "no such command" in low and "version" in low:
        return (
            "引擎缺少 version 命令 · 请用完整 paper-derived.exe 重装套件"
            "（Room 0.1.x 需要 paper-derived version JSON）"
        )

    if "no such option" in low and ("--out" in low or "out'" in low or 'out"' in low):
        return (
            "引擎不支持 --out（需升级 paper-derived，或 Room 应使用 --prompt-file）· "
            "请重装套件中的 paper-derived.exe"
        )

    if "no such option" in low:
        # Click usage dump — keep first Error line
        for line in blob.splitlines():
            if "Error:" in line or "error:" in line:
                return line.strip()[:180]
        return "引擎参数不兼容 · 请升级 paper-derived 与 Room 到同一套件版本"

    if "paper-derived not found" in low:
        return "找不到 paper-derived 引擎 · 请重新安装套件或检查 PATH"

    if "engine timeout" in low or "超时" in blob and "engine" in low:
        return "引擎超时 · 样例可能过大，可先转 .md 再注册"

    if "stdout is not json" in low:
        # Keep a slice of the non-JSON body
        tail = blob.split(":", 1)[-1].strip()[:120]
        return f"引擎输出异常: {tail}" if tail else "引擎输出不是 JSON"

    # ── peel traceback: last Exception: message ────────────────
    for line in reversed(blob.splitlines()):
        line = line.strip()
        if not line:
            continue
        m = _EXC_LINE.match(line)
        if m:
            detail = m.group(1).strip()
            if detail:
                return humanize_engine_error(
                    detail, sample_suffix=sample_suffix
                ) if _needs_remap(detail) else detail

    # Prefix before Traceback (sometimes has a one-liner)
    for sep in ("Traceback (most recent call last)", "Traceback"):
        i = blob.find(sep)
        if i > 0:
            head = blob[:i].strip().rstrip(":")
            head = re.sub(
                r"^engine failed\s*\(\d+\)\s*:\s*",
                "",
                head,
                flags=re.I,
            ).strip()
            if head and "traceback" not in head.lower():
                return head[:200]

    # engine failed (1): <body>
    if low.startswith("engine failed"):
        body = blob.split(":", 1)[-1].strip() if ":" in blob else ""
        if body and not body.lower().startswith("traceback"):
            # try again on body only
            again = humanize_engine_error(body, sample_suffix=sample_suffix)
            if again and again not in ("引擎调用失败", "未知错误"):
                return again
            return body[:160]
        return "引擎调用失败（详见终端或 room 日志）"

    # Collapse whitespace for single-line UI
    one = " ".join(blob.replace("\n", " ").split())
    if len(one) > 200:
        return one[:197] + "…"
    return one


def _needs_remap(detail: str) -> bool:
    """Whether detail still looks like a raw engine phrase worth remapping."""
    d = detail.lower()
    return (
        "无法读取" in detail
        or ".doc" in d
        or "python-docx" in d
        or "textutil" in d
        or "antiword" in d
        or "template_id" in d
    )


def register_error_hints(message: str, *, sample_path: str = "") -> list[str]:
    """Extra notice lines after a failed /template register."""
    m = message or ""
    low = m.lower()
    hints: list[str] = []
    suf = Path_suffix(sample_path)

    if "已存在" in m or "template_id_exists" in low:
        return []  # caller has a dedicated block

    # CLI mismatch is not a .doc problem — don't push Word/LibreOffice noise
    if "no such option" in low or "--out" in low or "参数不兼容" in m or "Usage:" in m:
        hints.append("请用同一套件重装 room + paper-derived（版本需匹配）")
        hints.append("终端自检: paper-derived template register --help 应含 --prompt-file 或 --out")
        return hints

    if ".doc" in low or suf == ".doc" or "旧版 .doc" in m:
        if sys.platform == "win32":
            hints.append(
                "Windows 读 .doc: 套件 tools/libreoffice、本机 LibreOffice，或桌面版 Word/WPS"
            )
            hints.append(
                "完整套件: room doctor 应显示 doc converter: LibreOffice（无则重装 Full 包）"
            )
            hints.append(
                "虚拟机: 无 LO 时先手动打开 Word 完成激活后重试，或另存为 .docx"
            )
            hints.append("或: Word/WPS 另存为 .docx / 导出 .md 后再 /template register")
        else:
            hints.append("建议: 另存为 .docx / .md，或安装 LibreOffice")
            if sys.platform == "darwin":
                hints.append("macOS: textutil -convert docx 样例.doc -output 样例.docx")

    if "模型" in m or "agent" in low or "timeout" in low or "超时" in m:
        hints.append("检查: Ctrl+M 连接密钥  ·  /model 切换模型")

    if "python-docx" in low:
        hints.append("开发态: 在 paper-derived 环境安装 python-docx")

    if not hints:
        hints.append("可在终端复现: paper-derived template register <样例> -n <名> --out /tmp/reg.md")
    return hints


def Path_suffix(path: str) -> str:
    if not path:
        return ""
    p = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in p:
        return ""
    return "." + p.rsplit(".", 1)[-1].lower()
