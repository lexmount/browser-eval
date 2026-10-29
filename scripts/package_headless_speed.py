"""Build a bound same-window latency comparison from a complete headless subcohort."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.speed_cohort import main

if __name__ == "__main__":
    main()
