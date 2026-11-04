"""Shared visual styling for the current report and standalone editorial export."""
from pathlib import Path

STYLE = (Path(__file__).parent / "assets" / "report-style.css").read_text(encoding="utf-8")
