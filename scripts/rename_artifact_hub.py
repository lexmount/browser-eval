"""Update one report's display title while preserving its share and versions."""
from pathlib import Path
from harness.web_fetch.report_publish import rename_main


if __name__ == "__main__":
    rename_main(default_root=Path(__file__).resolve().parents[1])
