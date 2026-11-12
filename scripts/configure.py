#!/usr/bin/env python3
"""Configure the shared services used by the Amazon skills in this project."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
from amazon_config import main

if __name__ == "__main__":
    raise SystemExit(main())
