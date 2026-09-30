"""Build the autonomous browser-use report."""

from harness.browser_use.report import main
from harness.reporting.templates import resolve_template

if __name__ == "__main__":
    resolve_template("browser-use")
    main()
