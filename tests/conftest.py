"""Puts scripts/ on the import path so the pipeline modules can be imported.

The scrapers are standalone scripts run as `python scripts/<name>.py` rather
than an installed package, so there is no package root to import from.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
