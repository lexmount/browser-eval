"""Shared visual styling for the current report and standalone editorial export."""
from pathlib import Path


def asset_text(name: str) -> str:
    return (Path(__file__).parent / "assets" / name).read_text(encoding="utf-8")


STYLE = asset_text("report-style.css")
