"""Update the current evaluator and its frozen definition bindings."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.dependency import main

if __name__ == "__main__":
    main()
