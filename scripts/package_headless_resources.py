"""Build a bound client-side CPU, memory and network comparison from a reviewed cohort."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.resource_cohort import main

if __name__ == '__main__':
    main()
