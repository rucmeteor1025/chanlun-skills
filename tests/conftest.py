# -*- coding: utf-8 -*-
"""Ensure the engine package is importable regardless of CWD."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
