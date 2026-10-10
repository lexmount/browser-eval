"""Execute the frozen Linux resource profile cohort."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harness.lexbench.resource_profiles import main  # noqa: E402

if __name__ == "__main__":
    main()
