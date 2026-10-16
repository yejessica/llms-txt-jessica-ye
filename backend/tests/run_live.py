"""Compatibility entry point for the coverage benchmark (run from backend)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.benchmark import main

if __name__ == "__main__":
    raise SystemExit(main())
