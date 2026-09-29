"""Fail closed on a Moli diagnostic site's evidence and reader-facing totals."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup


SOURCE_STATUSES = {"source_met", "source_unmet", "source_unavailable"}
HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


def _evidence_file(site: Path, relative: str, expected_hash: str) -> None:
    if (not isinstance(relative, str) or not relative.startswith("evidence/")
            or Path(relative).is_absolute() or ".." in Path(relative).parts):
        raise ValueError("Unsafe or missing evidence file path")
    if not isinstance(expected_hash, str) or not HEX_64.fullmatch(expected_hash):
        raise ValueError("Missing evidence SHA-256")
    path = site / relative
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing evidence file: {relative}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError(f"Evidence hash mismatch: {relative}")


def check_report(site: Path, *, expected_cases: int = 200) -> dict:
    document = json.loads((site / "moli-111-audit.json").read_text(encoding="utf-8"))
    html = (site / "index.html").read_text(encoding="utf-8")
    rows = document.get("rows")
    if (document.get("schema") != "moli_webfetch_111_diagnostic/2"
            or not isinstance(rows, list) or len(rows) != expected_cases
            or len({row.get("id") for row in rows}) != expected_cases):
        raise ValueError("Diagnostic inventory is incomplete or duplicated")
    if re.search(r"__[A-Z_]+__", html) or "根因待查" in html:
        raise ValueError("Unrendered or unresolved report content")
    counts = Counter(f"{row['family']}:{row['stage']}" for row in rows)
    if dict(counts) != document.get("counts"):
        raise ValueError("Result-stage counts do not match case inventory")
    first_pass = sum(row.get("initial_pass") is True for row in rows)
    if document.get("calls", {}).get("uniform_first_pass") != expected_cases:
        raise ValueError("Initial-call denominator is not bound to inventory")
    remaining = [row for row in rows if row.get("stage") == "未通过"]
    if any(row.get("diagnostic_pass") is not False for row in remaining):
        raise ValueError("Failed case has a passing diagnostic verdict")
    source_counts = Counter(row.get("source_status") for row in remaining)
    if (set(source_counts) - SOURCE_STATUSES
            or dict(source_counts) != document.get("source_review_counts")):
        raise ValueError("Failure source classification is missing or inconsistent")
    for row in rows:
        _evidence_file(site, row.get("answer_file"), row.get("answer_sha256"))
        _evidence_file(site, row.get("first_answer_file"), row.get("first_answer_sha256"))
        if row.get("stage") == "未通过":
            if (not row.get("failed_checks") and not row.get("process_error")
                    and not row.get("parse_error") and not row.get("score_error")):
                raise ValueError(f"Failure lacks exact error evidence: {row['id']}")
            if (not isinstance(row.get("source_evidence_sha256"), str)
                    or not HEX_64.fullmatch(row["source_evidence_sha256"])
                    or not row.get("source_checked_at")):
                raise ValueError(f"Failure lacks independent source evidence: {row['id']}")
    soup = BeautifulSoup(html, "html.parser")
    headline = soup.select_one("[data-first-pass][data-case-count]")
    if (headline is None or headline.get("data-first-pass") != str(first_pass)
            or headline.get("data-case-count") != str(expected_cases)
            or headline.select_one("strong") is None
            or headline.select_one("strong").get_text(strip=True) != f"{first_pass} / {expected_cases}"):
        raise ValueError("Reader-facing initial result does not match evidence")
    remaining_cell = soup.select_one("[data-remaining-failures]")
    if (remaining_cell is None or remaining_cell.get("data-remaining-failures") != str(len(remaining))
            or remaining_cell.get_text(strip=True) != str(len(remaining))):
        raise ValueError("Reader-facing failure count does not match evidence")
    visible_sources = {cell.get("data-source-status"): cell
                       for cell in soup.select("td[data-source-status][data-count]")}
    if set(visible_sources) != SOURCE_STATUSES:
        raise ValueError("Reader-facing source breakdown is incomplete")
    for status in SOURCE_STATUSES:
        cell = visible_sources[status]
        count = source_counts[status]
        if cell.get("data-count") != str(count) or cell.get_text(strip=True) != str(count):
            raise ValueError(f"Reader-facing source count differs: {status}")
    listed = [button.get("data-open") for button in soup.select(".result-table [data-open]")]
    if len(listed) != expected_cases or set(listed) != {row["id"] for row in rows}:
        raise ValueError("Reader-facing case inventory is incomplete")
    for selector in ("#case-dialog", "#dialog-facts", "#dialog-excerpt",
                     "#dialog-complete-output", "#dialog-first-output",
                     'a[href="moli-111-audit.json"]'):
        if soup.select_one(selector) is None:
            raise ValueError(f"Missing case evidence control: {selector}")
    return {"cases": expected_cases, "first_pass": first_pass,
            "remaining_failures": len(remaining), "source_counts": dict(source_counts)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    parser.add_argument("--expected-cases", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(check_report(args.site, expected_cases=args.expected_cases), ensure_ascii=False))


if __name__ == "__main__":
    main()
