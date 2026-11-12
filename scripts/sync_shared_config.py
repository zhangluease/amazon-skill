#!/usr/bin/env python3
"""Bundle the shared configuration reader so each skill can be installed alone."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (ROOT / "skills/amazon-jp-keywords/scripts/amazon_config.py",
           ROOT / "skills/amazon-jp-listing/scripts/amazon_config.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    source = (ROOT / "shared/amazon_config.py").read_bytes()
    stale = [path for path in TARGETS if not path.is_file() or path.read_bytes() != source]
    if args.check:
        if stale:
            print("Shared configuration bundles need syncing: " + ", ".join(str(p.relative_to(ROOT)) for p in stale))
            return 1
    else:
        for path in stale:
            path.write_bytes(source)
    print("Shared configuration bundles are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
