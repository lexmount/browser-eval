#!/usr/bin/env python3
"""Prepare the process-local native DNS bridge outside Git."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.web_fetch.network_dns import build_native_dns_library

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    path, manifest = build_native_dns_library(arguments.destination)
    print(f"{path} {manifest['library_sha256']}")
