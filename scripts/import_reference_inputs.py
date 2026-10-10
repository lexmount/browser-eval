"""Import or verify externally stored, hash-bound reference inputs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.web_fetch.reference_inputs import main

if __name__ == '__main__':
    main()
