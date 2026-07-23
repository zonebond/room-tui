#!/usr/bin/env python3
"""Cross-platform script encoding gate (macOS + Windows).

Policy
------
PowerShell (*.ps1)
  - UTF-8 **with BOM** (Windows PowerShell 5.x default code page is not UTF-8)
  - Body must be **ASCII-only** (no em-dash, ellipsis, arrows, CJK in source)

Batch (*.bat, *.cmd)
  - ASCII-only body (BOM optional)

Bash (*.sh under scripts/ and installs/)
  - UTF-8 **without BOM** (BOM breaks some bash/shebang paths)
  - Prefer ASCII punctuation; fail on "smart" punctuation that also breaks PS
    when scripts are copy-pasted. CJK is allowed in .sh (macOS/Linux UTF-8).

Usage
-----
  python3 scripts/check-script-encoding.py
  python3 scripts/check-script-encoding.py --fix   # rewrite to policy

Exit 0 if ok, 1 if violations (or fix applied with remaining issues).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Characters that frequently break CP936 PowerShell or confuse editors
SMART = {
    "\u2014": "-",  # em dash
    "\u2013": "-",  # en dash
    "\u2026": "...",  # ellipsis
    "\u2192": "->",
    "\u2190": "<-",
    "\u2260": "!=",
    "\u2500": "-",
    "\u2550": "=",  # ═
    "\u2713": "OK",
    "\u2714": "OK",
    "\u2717": "X",
    "\u2718": "X",
    "\u26a0": "!",  # ⚠
    "\u00a0": " ",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
}

BOM = b"\xef\xbb\xbf"


def iter_scripts() -> list[Path]:
    out: list[Path] = []
    for pat in (
        "scripts/**/*.ps1",
        "scripts/**/*.sh",
        "installs/**/*.ps1",
        "installs/**/*.sh",
        "installs/**/*.bat",
        "installs/**/*.cmd",
        "packaging/**/*.ps1",
    ):
        out.extend(ROOT.glob(pat))
    # de-dupe, skip junk
    seen: set[Path] = set()
    files: list[Path] = []
    for p in sorted(out):
        if any(x in p.parts for x in (".venv", "dist", "build", "node_modules", ".git")):
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        files.append(p)
    return files


def strip_smart(text: str) -> str:
    for a, b in SMART.items():
        text = text.replace(a, b)
    return text


def is_ascii(s: str) -> bool:
    return all(ord(c) < 128 for c in s)


def check_ps1(path: Path, *, fix: bool) -> list[str]:
    raw = path.read_bytes()
    has_bom = raw.startswith(BOM)
    body = raw[3:] if has_bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"{path}: not valid UTF-8 ({e})"]
    fixed = strip_smart(text)
    errs: list[str] = []
    if not is_ascii(fixed):
        bad = sorted({c for c in fixed if ord(c) >= 128})
        errs.append(
            f"{path}: .ps1 must be ASCII-only after smart-char strip; "
            f"leftover: {[f'U+{ord(c):04X}' for c in bad[:12]]}"
        )
    if not has_bom or fixed != text:
        if fix and is_ascii(fixed):
            path.write_bytes(BOM + fixed.encode("utf-8"))
            print(f"fixed {path.relative_to(ROOT)} (UTF-8 BOM + ASCII)")
        else:
            if not has_bom:
                errs.append(f"{path}: .ps1 must start with UTF-8 BOM")
            if fixed != text:
                errs.append(f"{path}: contains smart Unicode punctuation (use --fix)")
    return errs


def check_bat(path: Path, *, fix: bool) -> list[str]:
    raw = path.read_bytes()
    body = raw[3:] if raw.startswith(BOM) else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"{path}: not valid UTF-8 ({e})"]
    fixed = strip_smart(text)
    if not is_ascii(fixed):
        bad = sorted({c for c in fixed if ord(c) >= 128})
        return [
            f"{path}: .bat/.cmd must be ASCII-only; leftover "
            f"{[f'U+{ord(c):04X}' for c in bad[:12]]}"
        ]
    if fixed != text:
        if fix:
            path.write_bytes(fixed.encode("ascii"))
            print(f"fixed {path.relative_to(ROOT)} (ASCII)")
            return []
        return [f"{path}: smart Unicode punctuation (use --fix)"]
    return []


def check_sh(path: Path, *, fix: bool) -> list[str]:
    raw = path.read_bytes()
    if raw.startswith(BOM):
        if fix:
            path.write_bytes(raw[3:])
            raw = raw[3:]
            print(f"fixed {path.relative_to(ROOT)} (removed BOM)")
        else:
            return [f"{path}: .sh must not have UTF-8 BOM (breaks shebang on some systems)"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"{path}: not valid UTF-8 ({e})"]
    fixed = strip_smart(text)
    if fixed != text:
        if fix:
            path.write_bytes(fixed.encode("utf-8"))
            print(f"fixed {path.relative_to(ROOT)} (smart punctuation -> ASCII)")
            return []
        return [f"{path}: smart Unicode punctuation (use --fix)"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true", help="rewrite files to match policy")
    args = ap.parse_args()
    errors: list[str] = []
    for path in iter_scripts():
        suf = path.suffix.lower()
        if suf == ".ps1":
            errors.extend(check_ps1(path, fix=args.fix))
        elif suf in (".bat", ".cmd"):
            errors.extend(check_bat(path, fix=args.fix))
        elif suf == ".sh":
            errors.extend(check_sh(path, fix=args.fix))
    if errors:
        print("Script encoding policy violations:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nFix: python3 scripts/check-script-encoding.py --fix",
            file=sys.stderr,
        )
        return 1
    print(f"OK: {len(iter_scripts())} scripts match macOS+Windows encoding policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
