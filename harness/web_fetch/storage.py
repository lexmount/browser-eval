"""Persistent locations for measured evidence that must not live in Git."""
from __future__ import annotations

import os
from pathlib import Path


def data_home() -> Path:
    configured = os.environ.get("BROWSER_EVAL_DATA_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home()/".browser-eval"


def current_evidence_dir() -> Path:
    configured = os.environ.get("WEB_FETCH_EVIDENCE_DIR")
    return Path(configured).expanduser().resolve() if configured else data_home()/"evidence"/"current"


def research_evidence_dir() -> Path:
    configured = os.environ.get("WEB_FETCH_RESEARCH_EVIDENCE_DIR")
    return (Path(configured).expanduser().resolve() if configured
            else current_evidence_dir().parent/"research")


def require_evidence(path: Path | None = None) -> Path:
    result = (path or current_evidence_dir()).resolve()
    required = ["dataset.json", "results.jsonl", "execution.json", "artifacts"]
    missing = [name for name in required if not (result/name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Persistent benchmark evidence is incomplete at {result}: missing {', '.join(missing)}. "
            "Set WEB_FETCH_EVIDENCE_DIR to an explicit evidence bundle if needed."
        )
    require_selection_history(result)
    return result


def require_selection_history(evidence: Path) -> None:
    """A rerun selection is invalid without the baseline it compares against."""
    if not (evidence / "file-rerun-selection.json").is_file():
        return
    baseline = evidence / "history/file-baseline"
    required = [baseline / "results.jsonl", baseline / "execution.json", baseline / "artifacts"]
    missing = [path.relative_to(evidence).as_posix() for path in required if not path.exists()]
    if missing:
        raise ValueError(
            "File rerun selection requires complete historical baseline evidence: "
            + ", ".join(missing)
        )


def require_canonical_inputs(evidence: Path, canonical: Path) -> None:
    """Reject a report build when its frozen inputs differ from Git."""
    for relative in (Path("dataset.json"), Path("sources"), Path("list-references")):
        expected = canonical / relative
        actual = evidence / relative
        if expected.is_file():
            if not actual.is_file() or actual.read_bytes() != expected.read_bytes():
                raise ValueError(f"Persistent evidence differs from canonical input: {relative}")
            continue
        expected_files = {
            path.relative_to(expected): path.read_bytes()
            for path in expected.rglob("*")
            if path.is_file()
        }
        actual_files = {
            path.relative_to(actual): path.read_bytes()
            for path in actual.rglob("*")
            if path.is_file()
        } if actual.is_dir() else {}
        if actual_files != expected_files:
            raise ValueError(f"Persistent evidence differs from canonical input tree: {relative}")
