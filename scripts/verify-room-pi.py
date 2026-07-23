#!/usr/bin/env python3
"""Hard gate: Room packaging must ship Room-branded pi only.

Checks (fail = exit 1):
  --source DIR   third_party/pi tree has piConfig brand; path not code.research/pi
  --stamp FILE   pi.ROOM.txt has brand=room and allowed source
  --dist DIR     dist/bin has pi[.exe] + valid pi.ROOM.txt
  --suite DIR    suite stage/bin has pi + valid pi.ROOM.txt (if pi present)
  --binary FILE  given pi binary is Room-branded (sibling or dist stamp)

Used by build-room-pi / package-suite / build-windows-suite so humans do not
need a checklist.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


FORBIDDEN_SOURCE_SUFFIXES = (
    "/code.research/pi",
    "/code.research/pi/",
)


def _norm(p: str | Path) -> str:
    return str(p).replace("\\", "/").rstrip("/")


def die(msg: str) -> None:
    print(f"X Room pi check failed: {msg}", file=sys.stderr)
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"OK room-pi: {msg}")


def assert_allowed_source_path(path: Path, *, role: str) -> None:
    try:
        n = _norm(path.resolve())
    except OSError:
        n = _norm(path)
    # Forbidden: the shared research monorepo (not Room's third_party/pi)
    if n.rstrip("/").endswith("code.research/pi"):
        die(f"{role} path is forbidden shared tree: {path}")
    if "/code.research/pi/" in n + "/":
        # allow only if also under something else? no - any path through that tree
        die(f"{role} path is under forbidden code.research/pi: {path}")


def parse_stamp(path: Path) -> dict[str, str]:
    if not path.is_file():
        die(f"missing stamp {path}")
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        data[k.strip()] = v.strip()
    return data


def verify_stamp(stamp: Path, *, require_third_party: bool = True) -> dict[str, str]:
    data = parse_stamp(stamp)
    if data.get("brand") != "room":
        die(f"{stamp}: brand={data.get('brand')!r} (want brand=room)")
    if data.get("configDir") != ".config/room-tui":
        die(f"{stamp}: configDir={data.get('configDir')!r} (want .config/room-tui)")
    if data.get("env") != "ROOM_CODING_AGENT_DIR":
        die(f"{stamp}: env={data.get('env')!r} (want ROOM_CODING_AGENT_DIR)")
    src = data.get("source") or ""
    if not src:
        die(f"{stamp}: missing source=")
    assert_allowed_source_path(Path(src), role="stamp source")
    n = _norm(src)
    if require_third_party and "third_party/pi" not in n and "third_party\\pi" not in src:
        # allow override builds that still brand-stamp correctly if env says so
        if (data.get("allow_external") or "").lower() not in ("1", "true", "yes"):
            die(
                f"{stamp}: source is not third_party/pi ({src}). "
                "Rebuild with scripts/build-room-pi.* from the submodule."
            )
    ok(f"stamp {stamp} brand=room source={src}")
    return data


def verify_source(pi_root: Path) -> None:
    assert_allowed_source_path(pi_root, role="source")
    pkg = pi_root / "packages" / "coding-agent" / "package.json"
    if not pkg.is_file():
        die(f"missing {pkg} (git submodule update --init --recursive?)")
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"invalid JSON {pkg}: {e}")
    cfg = data.get("piConfig") or {}
    if cfg.get("name") != "room":
        die(f"{pkg}: piConfig.name={cfg.get('name')!r} (want room). Run apply-room-pi-brand.py")
    if cfg.get("configDir") != ".config/room-tui":
        die(
            f"{pkg}: piConfig.configDir={cfg.get('configDir')!r} "
            "(want .config/room-tui)"
        )
    ok(f"source brand {pi_root}")


def verify_dist(dist_bin: Path) -> None:
    pi = dist_bin / "pi.exe"
    if not pi.is_file():
        pi = dist_bin / "pi"
    if not pi.is_file():
        die(f"no pi binary under {dist_bin}")
    stamp = dist_bin / "pi.ROOM.txt"
    verify_stamp(stamp)
    # stamp binary= should match if present
    data = parse_stamp(stamp)
    ok(f"dist binary {pi} ({pi.stat().st_size} bytes)")


def verify_binary(pi_bin: Path, *, repo_root: Path | None = None) -> None:
    if not pi_bin.is_file():
        die(f"pi binary missing: {pi_bin}")
    assert_allowed_source_path(pi_bin, role="binary")
    n = _norm(pi_bin.resolve())
    if n.rstrip("/").endswith("code.research/pi") or "/code.research/pi/" in n:
        die(f"binary under forbidden tree: {pi_bin}")

    candidates = [
        pi_bin.parent / "pi.ROOM.txt",
        pi_bin.resolve().parent / "pi.ROOM.txt",
    ]
    if repo_root is not None:
        candidates.append(repo_root / "dist" / "bin" / "pi.ROOM.txt")

    stamp = next((c for c in candidates if c.is_file()), None)
    if stamp is None:
        die(
            f"no pi.ROOM.txt next to {pi_bin} (or dist/bin). "
            "This is not a Room-branded build. Run: scripts/build-room-pi.*"
        )
    verify_stamp(stamp)
    ok(f"binary {pi_bin} is Room-branded")


def verify_suite(suite_dir: Path) -> None:
    bin_dir = suite_dir / "bin"
    if not bin_dir.is_dir():
        die(f"suite has no bin/: {suite_dir}")
    pi = bin_dir / "pi.exe"
    if not pi.is_file():
        pi = bin_dir / "pi"
    if not pi.is_file():
        die(f"suite missing pi binary under {bin_dir}")
    stamp = bin_dir / "pi.ROOM.txt"
    if not stamp.is_file():
        # also accept suite root
        alt = suite_dir / "pi.ROOM.txt"
        if alt.is_file():
            stamp = alt
        else:
            die(
                f"suite missing bin/pi.ROOM.txt — package must ship Room brand stamp "
                f"with pi. Re-run build-room-pi then package-suite."
            )
    verify_stamp(stamp)
    theme = bin_dir / "theme" / "dark.json"
    if not theme.is_file():
        die(f"suite pi present but theme/dark.json missing: {theme}")
    ok(f"suite {suite_dir.name} Room-branded pi + theme")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, help="pi monorepo root to check")
    ap.add_argument("--stamp", type=Path, help="pi.ROOM.txt path")
    ap.add_argument("--dist", type=Path, help="dist/bin directory")
    ap.add_argument("--suite", type=Path, help="suite stage directory")
    ap.add_argument("--binary", type=Path, help="path to pi[.exe]")
    ap.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="room-tui root (for dist stamp lookup)",
    )
    args = ap.parse_args()
    if not any([args.source, args.stamp, args.dist, args.suite, args.binary]):
        ap.error("specify at least one of --source --stamp --dist --suite --binary")

    repo = args.repo
    if repo is None:
        repo = Path(__file__).resolve().parents[1]

    if args.source:
        verify_source(args.source.resolve())
    if args.stamp:
        verify_stamp(args.stamp.resolve())
    if args.dist:
        verify_dist(args.dist.resolve())
    if args.binary:
        verify_binary(args.binary.resolve(), repo_root=repo.resolve())
    if args.suite:
        verify_suite(args.suite.resolve())

    print("OK room-pi: all requested checks passed")


if __name__ == "__main__":
    main()
