"""Shared physical-call binding checks for WebMainBench evidence."""
from __future__ import annotations


def retry_chain_matches(row: dict, attempts: list[dict]) -> bool:
    return bool(attempts) and (
        len(attempts) == row["physical_attempt_count"]
        and [item["physical_attempt"] for item in attempts] == list(range(1, len(attempts) + 1))
        and attempts[-1]["output_sha256"] == row["output_sha256"]
        and abs(attempts[-1]["elapsed_ms"] - row["elapsed_ms"]) <= 1e-12
        and abs(sum(item["elapsed_ms"] for item in attempts) - row["total_elapsed_ms"]) <= 1e-12
    )
