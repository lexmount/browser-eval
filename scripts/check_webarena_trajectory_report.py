"""Check reader-visible structure and comparison semantics in the trajectory report."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from harness.browser_use.report import METRICS, PAGES, REPORT_STEM


def check_report_bundle(formal_root: Path) -> dict[str, int]:
    reports = formal_root / "reports"
    pages_checked = 0
    for index, (_slug, label, filename) in enumerate(PAGES, 1):
        path = reports / filename
        if not path.is_file():
            raise ValueError(f"missing report page: {filename}")
        text = path.read_text(encoding="utf-8")
        nav = re.search(r'<nav aria-label="报告分页">(.*?)</nav>', text, re.DOTALL)
        if not nav:
            raise ValueError(f"missing report navigation: {filename}")
        for expected_index, (_, expected_label, expected_filename) in enumerate(PAGES, 1):
            expected = f'href="{expected_filename}"'
            label_markup = f">{expected_index}. {expected_label}</a>"
            if expected not in nav.group(1) or label_markup not in nav.group(1):
                raise ValueError(f"incomplete report navigation: {filename}")
        active = re.findall(r'<a [^>]*aria-current=page[^>]*>(.*?)</a>', nav.group(1))
        if active != [f"{index}. {label}"]:
            raise ValueError(f"wrong active report navigation: {filename}")
        pages_checked += 1

    homepage = (reports / f"{REPORT_STEM}.html").read_text(encoding="utf-8")
    specs = [spec for _, metrics in METRICS for spec in metrics]
    if homepage.count('data-metric="') != len(specs):
        raise ValueError("resource table metric inventory does not match the declared contract")
    for spec in specs:
        pattern = (
            rf'<tr data-metric="{re.escape(spec.key)}" '
            rf'data-preference="{re.escape(spec.preference)}">(.*?)</tr>'
        )
        rows = re.findall(pattern, homepage, re.DOTALL)
        if len(rows) != 1:
            raise ValueError(f"missing or duplicated resource metric row: {spec.key}")
        row = rows[0]
        if row.count('class="metric-policy ') != 1:
            raise ValueError(f"missing metric interpretation badge: {spec.key}")
        if spec.preference == "neutral" and (
            'class="better"' in row or 'class="worse"' in row
        ):
            raise ValueError(f"neutral metric must not declare a winner: {spec.key}")

    summary = json.loads((formal_root / "run/summary.json").read_text(encoding="utf-8"))
    comparison = summary.get("resource_comparison") or {}
    relative = comparison.get("relative_ratios") or {}
    comparison_engines = relative.get("by_engine") or {}
    if not comparison_engines:
        paired = comparison.get("paired_ratios") or {}
        if paired.get("numerator_engine") and paired.get("denominator_engine"):
            comparison_engines = {str(paired["numerator_engine"]): paired.get("metrics") or {}}
    ratio_modules = homepage.count(
        'class="ratio-module" data-formula="per-task-ratio"'
    )
    expected_ratio_modules = len(METRICS) * len(comparison_engines)
    if ratio_modules != expected_ratio_modules:
        raise ValueError("ratio modules are incomplete across metric groups")
    if expected_ratio_modules and "每个共同通过任务先相除，再汇总" not in homepage:
        raise ValueError("per-task ratio aggregation is not explained")

    return {
        "pages": pages_checked,
        "metrics": len(specs),
        "ratio_modules": ratio_modules,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formal-root", type=Path, required=True)
    args = parser.parse_args()
    result = check_report_bundle(args.formal_root.resolve())
    print(
        "REPORT SEMANTICS PASS: "
        f"{result['pages']} pages, {result['metrics']} metrics, "
        f"{result['ratio_modules']} ratio modules"
    )


if __name__ == "__main__":
    main()
