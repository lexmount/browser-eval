"""Bind a freshly completed external Rainbond reset to one replay method.

The reset itself is performed through the Rainbond control plane. This hook
copies its independently recorded receipt into the immutable method evidence.
"""

from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    validate_remote_cohort_reset_receipt,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method-id", required=True)
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--site-map", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    source_value = os.environ.get("WEBARENA_PREPARED_COHORT_RESET_RECEIPT")
    if not source_value:
        raise ValueError("prepared Rainbond cohort reset receipt is required")
    source = Path(source_value)
    if source.is_symlink() or not source.is_file() or args.receipt.exists():
        raise ValueError("prepared reset source or output is invalid")
    if source.resolve() == args.receipt.resolve():
        raise ValueError("prepared reset receipt cannot be its own output")
    receipt = validate_remote_cohort_reset_receipt(
        source,
        site_map_sha256=file_sha256(args.site_map),
        app_id=args.app_id,
        reset_scope="once_before_method_cohort",
        site_reset_between_tasks=False,
    )
    completed = receipt.get("completed_at_unix")
    if (
        receipt.get("method_id") != args.method_id
        or receipt.get("task_id") is not None
        or type(completed) not in {int, float}
        or not time.time() - 1800 <= completed <= time.time()
    ):
        raise ValueError("prepared reset is stale or belongs to another method")
    shutil.copyfile(source, args.receipt)
    if file_sha256(args.receipt) != file_sha256(source):
        raise ValueError("prepared reset receipt changed during copy")


if __name__ == "__main__":
    main()
