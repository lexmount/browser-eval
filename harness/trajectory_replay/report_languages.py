"""Languages materialized in a report, independent of available translations."""

from pathlib import Path


def report_languages(root: Path) -> tuple[str, ...]:
    from harness.report_languages import check_primary_report_language, generated_languages

    check_primary_report_language(root)
    return generated_languages(root)


def language_roots(root: Path) -> tuple[Path, ...]:
    return tuple(root if lang == "zh" else root / "en" for lang in report_languages(root))
