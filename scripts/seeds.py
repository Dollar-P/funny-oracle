"""Demo seed predictions (data/seeds.json).

    python scripts/seeds.py load                      # into data/oracle.db (skips ids that already exist)
    python scripts/seeds.py export --ids 4,9,10-17    # from data/oracle.db (maintainers)

The web API also loads the seeds automatically on startup when the database has no predictions yet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from core import seeds  # noqa: E402


def parse_ids(s: str) -> list[int]:
    out: list[int] = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += range(int(a), int(b) + 1)
        elif part.strip():
            out.append(int(part))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("load")
    ex = sub.add_parser("export")
    ex.add_argument("--ids", default="4,9,10-17")
    a = ap.parse_args()
    if a.cmd == "export":
        print(seeds.export(parse_ids(a.ids)))
    else:
        r = seeds.load()
        print(f"loaded {len(r['loaded'])} seed prediction(s) {r['loaded']}; skipped (id already in use) {r['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
