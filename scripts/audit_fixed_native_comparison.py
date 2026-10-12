#!/usr/bin/env python3
"""Verify one complete two-build native Web Fetch cohort and summarize differences."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.web_fetch.comparison_audit import audit_fixed_native_comparison


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-dataset", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--run-receipt", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--stable-provider", required=True)
    parser.add_argument("--candidate-provider", required=True)
    args = parser.parse_args(argv)
    audit_fixed_native_comparison(
        args.evidence,
        source_dataset=args.source_dataset,
        provider_config=args.provider_config,
        run_receipt=args.run_receipt,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
        stable_provider=args.stable_provider,
        candidate_provider=args.candidate_provider,
    )


if __name__ == "__main__":
    main()
