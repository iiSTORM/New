#!/usr/bin/env python3
"""Entry point: `python scripts/propedge_cli.py --help`.

A thin wrapper so the package can be run without installing it, matching how
every other script in scripts/ is run.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from propedge.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
