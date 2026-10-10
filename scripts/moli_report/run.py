"""Reproduce one frozen Lexbench layout cohort."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harness.lexbench.reproduction import main  # noqa: E402

if __name__ == "__main__":
    main(default_directory=Path(__file__).parent)
