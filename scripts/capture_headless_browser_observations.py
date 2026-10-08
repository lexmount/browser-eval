"""Command entry for independent browser observations."""

from pathlib import Path

from harness.web_fetch.browser_observations import main

if __name__ == "__main__":
    main(root=Path(__file__).resolve().parents[1])
