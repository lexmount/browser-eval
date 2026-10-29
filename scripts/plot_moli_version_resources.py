"""Command entry for the shared Lexbench charts."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from harness.lexbench.charts import main

if __name__ == "__main__":
    main()
