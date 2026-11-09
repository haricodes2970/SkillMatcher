"""Shared pytest setup for the skill-matcher tool tests."""

import sys
from pathlib import Path

# The scripts are standalone modules, not a package. Put their directory on the import path
# so tests can import scan_skills and match_skills directly.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
