"""English is the replay report source language; `messages.json` holds its translations."""
from pathlib import Path

from harness.reporting.locales import catalog_text

MESSAGES = Path(__file__).with_suffix(".json")


def report_text(language: str):
    """Return `t(id, english, **values)` for one report edition."""
    return catalog_text(MESSAGES, language)
