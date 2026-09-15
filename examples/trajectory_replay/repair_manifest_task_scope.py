#!/usr/bin/env python3
"""Repair the pre-fix manifest denominator without changing measured attempts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.manifest_repair import repair_task_scope

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    print(json.dumps(repair_task_scope(parser.parse_args().artifacts), ensure_ascii=False))
