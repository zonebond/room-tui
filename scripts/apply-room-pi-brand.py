#!/usr/bin/env python3
"""Ensure third_party/pi coding-agent is Room-branded (scheme B).

Patches packages/coding-agent/package.json:
  piConfig.name = "room"
  piConfig.configDir = ".config/room-tui"

Idempotent. Does not touch code.research/pi.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def apply_brand(pi_root: Path) -> bool:
    pkg_path = pi_root / "packages" / "coding-agent" / "package.json"
    if not pkg_path.is_file():
        raise SystemExit(f"missing {pkg_path}")
    data = json.loads(pkg_path.read_text(encoding="utf-8"))
    cfg = dict(data.get("piConfig") or {})
    before = dict(cfg)
    cfg["name"] = "room"
    cfg["configDir"] = ".config/room-tui"
    data["piConfig"] = cfg
    if before == cfg:
        return False
    pkg_path.write_text(
        json.dumps(data, indent="\t", ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "pi_root",
        nargs="?",
        default=None,
        help="pi monorepo root (default: <room-tui>/third_party/pi)",
    )
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    pi_root = Path(args.pi_root) if args.pi_root else root / "third_party" / "pi"
    pi_root = pi_root.resolve()
    # Refuse shared research tree
    s = str(pi_root).replace("\\", "/")
    if s.rstrip("/").endswith("/code.research/pi"):
        print(f"X refusing {pi_root} (other products)", file=sys.stderr)
        raise SystemExit(1)
    changed = apply_brand(pi_root)
    print(f"{'updated' if changed else 'ok already'}: {pi_root / 'packages/coding-agent/package.json'}")
    print("  piConfig.name=room  configDir=.config/room-tui")
    print("  default agent: ~/.config/room-tui/agent")


if __name__ == "__main__":
    main()
