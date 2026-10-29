"""Execute one isolated browser call through the shared worker."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.web_fetch.providers.headless_worker import main

if __name__ == '__main__':
    main()
