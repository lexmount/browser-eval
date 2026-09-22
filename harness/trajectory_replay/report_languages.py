"""Languages materialized in a report, independent of available translations."""

import json
from pathlib import Path


def report_languages(root: Path) -> tuple[str, ...]:
    path = root / "report-manifest.json"
    manifest = json.loads(path.read_text()) if path.is_file() else {}
    languages = manifest.get("languages", ["zh", "en"])
    if languages not in (["zh"], ["zh", "en"]):
        raise ValueError("unsupported report languages")
    return tuple(languages)


def language_roots(root: Path) -> tuple[Path, ...]:
    return tuple(root if lang == "zh" else root / "en" for lang in report_languages(root))
