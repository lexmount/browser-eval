#!/usr/bin/env python3
"""Export a minimal, host-scrubbed browser report bundle for site assembly."""

from __future__ import annotations

import argparse
from pathlib import Path

from harness.trajectory_replay.publication import export_public_report_bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    print(export_public_report_bundle(**vars(parser.parse_args())))
