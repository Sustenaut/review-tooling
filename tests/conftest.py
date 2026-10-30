"""The tools read corpus/ where they run, so these tests point them at made-up data."""
import os
from pathlib import Path

os.environ["SOURCES_ROOT"] = str(Path(__file__).resolve().parent / "fixtures" / "root")
