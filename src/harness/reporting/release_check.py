"""The outer flow report release checks repeat; each track keeps its own rules."""

from pathlib import Path

from bs4 import BeautifulSoup

from harness.record_io import file_sha256
from harness.report_languages import generated_languages
from harness.reporting.presentation import check_presentation
from harness.reporting.templates import check_template_identity
from harness.reporting.versions import check_build_version
from harness.web_fetch.safety import reject_evidence_symlinks


def open_report(root) -> Path:
    """Refuse a linked report tree before any rule reads from it."""
    root = Path(root)
    reject_evidence_symlinks(root)
    return root.resolve()


def read_page(path) -> BeautifulSoup:
    return BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")


def checked_page(path) -> BeautifulSoup:
    """Read one page through the shared comparison and evidence presentation rules."""
    return check_presentation(read_page(path))


def language_editions(root: Path) -> tuple[tuple[str, str], ...]:
    """Name each generated edition and its route prefix; Chinese stays at the root."""
    return tuple((language, "en/" if language == "en" else "")
                 for language in generated_languages(root))


def check_report_identity(root: Path, manifest: dict, track: str, profile_path: Path | None = None,
                          *, allow_unbound_profile: bool = False) -> None:
    """Bind the build, the maintained template and, when selected, the instance profile.

    ``allow_unbound_profile`` keeps reports from before profile binding readable.
    """
    check_build_version(root, manifest)
    check_template_identity(manifest, track)
    if profile_path is None:
        return
    bound = manifest.get("profile_sha256")
    if allow_unbound_profile and not bound:
        return
    if profile_path.is_symlink() or bound != file_sha256(profile_path):
        raise ValueError("report was built from a different instance profile")
