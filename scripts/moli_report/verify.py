from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import pathlib
import re
import shutil
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from plot_moli_version_resources import combine_comparisons, render_chart  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument(
    "--site",
    type=pathlib.Path,
    default=pathlib.Path.home() / ".browser-eval/reports/moli-1.1.8-failures-site",
)
parser.add_argument(
    "--release", action="store_true", help="Require the complete bound method matrix"
)
args = parser.parse_args()
SITE = args.site.resolve()


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def near(a, b):
    return math.isclose(float(a), float(b), rel_tol=1e-8, abs_tol=1e-6)


def fail(message):
    raise ValueError(message)


def validate(model, site, check_files=True):
    if model.get("schema") != "moli_layout_report/2":
        fail("schema")
    count = model.get("task_count")
    calls_per_mode = model.get("attempts_per_mode")
    if not isinstance(count, int) or count != 372 or calls_per_mode != count * 3:
        fail("denominator")
    protocol = model.get("protocol") or {}
    if (
        protocol.get("k_runs") != 3
        or protocol.get("jobs") != 1
        or not protocol.get("seed")
        or protocol.get("score_eligible") is not False
    ):
        fail("diagnostic protocol")
    if not all(
        re.fullmatch(r"[0-9a-f]{64}", str(protocol.get(k, "")))
        for k in ("runner_sha256", "fixtures_sha256")
    ):
        fail("source identity")
    inv = model.get("cases") or []
    if len(inv) != count or len({c["id"] for c in inv}) != count:
        fail("inventory")
    if len(model.get("strata") or []) != 15:
        fail("strata")
    outcome = model.get("case_outcomes") or {}
    expected_pair = dict.fromkeys(
        ("both_pass", "both_fail", "off_fail_on_pass", "off_pass_on_fail"), 0
    )
    accepted = {"off": 0, "on": 0}
    for item in inv:
        case = json.loads((site / "data/cases" / f"{item['id']}.json").read_text())
        flags = {
            mode: len(case["modes"][mode]["attempts"]) == protocol["k_runs"]
            and all(a["status"] == "pass" for a in case["modes"][mode]["attempts"])
            for mode in ("off", "on")
        }
        a, b = flags["off"], flags["on"]
        for mode in flags:
            accepted[mode] += flags[mode]
        expected_pair[
            "both_pass"
            if a and b
            else "off_pass_on_fail"
            if a
            else "off_fail_on_pass"
            if b
            else "both_fail"
        ] += 1
    if outcome != {
        "rule": "all_recorded_attempts_pass",
        "repeats": protocol["k_runs"],
        "total": count,
        **accepted,
        "paired": expected_pair,
    }:
        fail("case outcome aggregation")
    comparison = model.get("version_comparison") or {}
    version_rows = comparison.get("configurations") or []
    expected_version_ids = ["114_off", "118_off", "118_on"]
    if (
        comparison.get("baseline") != "114_off"
        or [row.get("id") for row in version_rows] != expected_version_ids
    ):
        fail("version comparison registry")
    functional_population = comparison.get("functional_population") or {}
    if any(
        functional_population.get(k) != v
        for k, v in {
            "cases": 372,
            "attempts_per_case": 3,
            "rule": "all_three_attempts_pass",
            "version_118_direct_cases": 372,
            "version_114_candidate_cases": 1,
            "version_114_inherited_failures": 371,
            "version_114_candidate": "v4_cdp_emulation_setdefaultbackgroundcoloroverride",
            "version_114_method": "differential_candidate_check",
        }.items()
    ):
        fail("version functional population")
    resource_population = comparison.get("resource_population", {})
    if (
        resource_population.get("experiment_cases") != 557
        or resource_population.get("attempts_per_case") != 5
        or resource_population.get("total_tasks") != 557
        or resource_population.get("calls_per_task") != 5
        or resource_population.get("chart_calls") != 1045
    ):
        fail("version resource population")
    baseline = version_rows[0]
    for row in version_rows:
        if row.get("total_cases") != 372 or not 0 <= row.get("passed_cases", -1) <= 372:
            fail("version pass denominator")
        if not near(row["success_rate_pct"], row["passed_cases"] / 372 * 100):
            fail("version success rate")
        if not near(
            row["success_change_pp"], row["success_rate_pct"] - baseline["success_rate_pct"]
        ):
            fail("version success change")
        if not near(row["pss_change_pct"], (row["pss_mib"] / baseline["pss_mib"] - 1) * 100):
            fail("version PSS change")
        if not near(row["cpu_change_pct"], (row["cpu_ms"] / baseline["cpu_ms"] - 1) * 100):
            fail("version CPU change")
    functional_methods = model.get("functional_methods") or []
    combined = model.get("combined_comparison")
    if combined != combine_comparisons(
        functional_methods, comparison, model.get("macos_comparison")
    ):
        fail("combined chart registry, baseline or measurement mismatch")
    if json.loads((site / "data/combined-comparison.json").read_text()) != combined:
        fail("combined chart data target")
    with tempfile.TemporaryDirectory() as directory:
        for language in ("zh", "en"):
            expected = pathlib.Path(directory) / f"{language}.svg"
            render_chart(combined, expected, ("memory", "cpu"), language)
            actual = site / f"assets/moli-all-success-resources-{language}.svg"
            if not actual.is_file() or actual.read_bytes() != expected.read_bytes():
                fail("combined chart does not match measured registry")
    candidates = model.get("fixed_candidates") or {}
    expected_ids = ["114_off", "118_off", "118_on"] + list(candidates)
    if [row.get("id") for row in functional_methods] != expected_ids:
        fail("functional method registry")
    if [
        (row.get("final_passed_cases"), row.get("direct_cases"), row.get("inherited_cases"))
        for row in functional_methods[:3]
    ] != [(0, 1, 371), (1, 372, 0), (255, 372, 0)]:
        fail("fixed functional methods")
    for method_id, candidate in candidates.items():
        if method_id not in {"119_off", "119_on", "119_official_on"}:
            fail("unknown fixed method")
        info = candidate.get("evidence") or {}
        path = site / info.get("url", "")
        if not path.resolve().is_relative_to(site.resolve()):
            fail("unsafe fixed summary target")
        if (
            not path.is_file()
            or path.is_symlink()
            or digest(path) != info.get("sha256")
            or path.stat().st_size != info.get("bytes")
        ):
            fail("fixed summary integrity")
        summary = json.loads(path.read_text())
        if summary.get("schema") != "lexbench_moli_fixed_layout_summary/1":
            fail("fixed summary schema")
        layout = "off" if method_id == "119_off" else "on"
        if summary.get("layout") != layout:
            fail("fixed layout identity")
        for field in ("population", "outcomes", "binary", "sources"):
            if summary.get(field) != candidate.get(field):
                fail("fixed summary binding")
        population = summary.get("population") or {}
        if (
            population.get("cases") != 372
            or population.get("attempts_per_case") != 3
            or population.get("calls") != 1116
        ):
            fail("fixed summary population")
        cases = summary.get("cases") or []
        if len(cases) != 372 or {c["task_id"] for c in cases} != {c["id"] for c in inv}:
            fail("fixed summary cases")
        passed = 0
        for case in cases:
            attempts = case.get("attempts") or []
            if len(attempts) != 3 or {a["attempt"] for a in attempts} != {1, 2, 3}:
                fail("fixed summary attempts")
            passes = sum(a["status"] == "pass" for a in attempts)
            if case.get("passes") != passes:
                fail("fixed summary case verdict")
            passed += passes == 3
        if summary["outcomes"].get("passed_cases") != passed or not near(
            summary["outcomes"]["success_rate_pct"], passed / 372 * 100
        ):
            fail("fixed summary result")
        row = next(x for x in functional_methods if x["id"] == method_id)
        if (
            row.get("final_passed_cases") != passed
            or row.get("physical_calls") != 1116
            or row.get("policy") != f"fixed_{layout}"
        ):
            fail("fixed method row")
        if row.get("moli_sha256") != summary["binary"]["moli_sha256"]:
            fail("fixed method binary")
    if candidates:
        compact = model.get("fixed_case_outcomes") or []
        if len(compact) != 372 or {c["id"] for c in compact} != {c["id"] for c in inv}:
            fail("fixed outcome inventory")
        for item in compact:
            path = site / "data/fixed-cases" / f"{item['id']}.json"
            if not path.is_file() or path.is_symlink():
                fail("fixed case evidence target")
            detail = json.loads(path.read_text())
            if set(detail["methods"]) != set(candidates) or set(item["passes"]) != set(candidates):
                fail("fixed case registry")
            for method_id, result in detail["methods"].items():
                if result["passes"] != item["passes"][method_id] or result["passes"] != sum(
                    a["status"] == "pass" for a in result["attempts"]
                ):
                    fail("fixed case count")
                if len(result["attempts"]) != 3:
                    fail("fixed detail attempts")
                for attempt in result["attempts"]:
                    if attempt["status"] != "pass" and not attempt.get("failure", {}).get("detail"):
                        fail("fixed failure locator")
                    if not attempt.get("evidence"):
                        fail("fixed attempt lacks evidence")
                    for evidence in attempt["evidence"].values():
                        target = site / evidence["url"]
                        if (
                            not target.resolve().is_relative_to(site.resolve())
                            or not target.is_file()
                            or target.is_symlink()
                        ):
                            fail("fixed log target")
                        if check_files and (
                            digest(target) != evidence["sha256"]
                            or target.stat().st_size != evidence["bytes"]
                        ):
                            fail("fixed log integrity")
    for resource_key in ("macos_resource", "official_resource"):
        resource = model.get(resource_key)
        if not resource:
            continue
        official = resource_key == "official_resource"
        info = resource.get("evidence") or {}
        path = site / info.get("url", "")
        if not path.resolve().is_relative_to(site.resolve()):
            fail("unsafe resource target")
        if check_files and (
            not path.is_file()
            or path.is_symlink()
            or digest(path) != info.get("sha256")
            or path.stat().st_size != info.get("bytes")
        ):
            fail("resource integrity")
        summary = json.loads(path.read_text()) if check_files else resource.get("summary")
        if (
            summary != resource.get("summary")
            or summary.get("schema") != "lexbench_moli_macos_fixed_resource_summary/1"
        ):
            fail("resource identity")
        population = summary.get("population") or {}
        if population != {
            "tasks": 557,
            "attempts_per_task": 5,
            "calls_per_configuration": 2785,
            "frozen_comparison_calls": 1045,
            "frozen_comparison_tasks": 209,
        }:
            fail("resource population")
        quality = summary.get("quality") or {}
        if (
            quality.get("memory_metric") != "process_tree_rss"
            or quality.get("pss_available") is not False
            or quality.get("publishable") is not True
        ):
            fail("resource quality")
        layouts = ["on"] if official else ["off", "on"]
        method = summary.get("method") or {}
        if (
            method.get("configurations") != [f"layout_{x}" for x in layouts]
            or method.get("resource_runs") != len(layouts)
            or method.get("layout_retry") is not False
            or method.get("attempts_per_case") != 5
            or method.get("resource_sample_interval_ms") != 250
        ):
            fail("resource method")
        configs = summary.get("configurations") or {}
        if set(configs) != set(layouts):
            fail("resource configurations")
        for layout, config in configs.items():
            method_id = "119_official_on" if official else f"119_{layout}"
            if (
                summary["candidate"]["binary_sha256"]
                != candidates[method_id]["binary"]["moli_sha256"]
            ):
                fail("resource binary")
            if (
                config.get("layout") != layout
                or config.get("quality", {}).get("publishable") is not True
            ):
                fail("configuration quality")
            for name, n in (
                ("all_predeclared_calls", 2785),
                ("frozen_historical_common_pass_calls", 1045),
            ):
                for metric in ("rss_peak_mib", "cpu_time_ms"):
                    stats = config.get("metrics", {}).get(name, {}).get(metric, {})
                    if stats.get("n") != n:
                        fail("metric denominator")
                    if any(
                        not isinstance(stats.get(key), (float, int))
                        or not math.isfinite(stats[key])
                        for key in ("mean", "p50", "p95", "sum")
                    ):
                        fail("metric finite values")
                    if not near(stats["sum"], stats["mean"] * n):
                        fail("metric mean/sum agreement")
        if not official:
            for name in ("all_predeclared_calls", "frozen_historical_common_pass_calls"):
                change_values = summary["comparisons"][name]
                for metric, stat, key in [
                    ("rss_peak_mib", "p50", "layout_on_vs_off_rss_p50_change_pct"),
                    ("cpu_time_ms", "mean", "layout_on_vs_off_cpu_mean_change_pct"),
                ]:
                    expected = (
                        configs["on"]["metrics"][name][metric][stat]
                        / configs["off"]["metrics"][name][metric][stat]
                        - 1
                    ) * 100
                    if not near(change_values[key], expected):
                        fail("resource percentage change")
    chart = model.get("macos_comparison")
    if model.get("macos_resource"):
        if not chart or {r["id"] for r in chart["configurations"]} != set(candidates):
            fail("macOS chart registry")
        baseline = next(r for r in chart["configurations"] if r["id"] == chart["baseline"])
        for row in chart["configurations"]:
            resource = (
                model["official_resource"]
                if row["id"] == "119_official_on"
                else model["macos_resource"]
            )
            layout = "off" if row["id"] == "119_off" else "on"
            metrics = resource["summary"]["configurations"][layout]["metrics"][
                "frozen_historical_common_pass_calls"
            ]
            if row["passed_cases"] != candidates[row["id"]]["outcomes"]["passed_cases"] or not near(
                row["success_rate_pct"], row["passed_cases"] / 372 * 100
            ):
                fail("macOS chart success")
            for value, change, expected in [
                ("rss_mib", "rss_change_pct", metrics["rss_peak_mib"]["p50"]),
                ("cpu_ms", "cpu_change_pct", metrics["cpu_time_ms"]["mean"]),
            ]:
                if not near(row[value], expected) or not near(
                    row[change], (row[value] / baseline[value] - 1) * 100
                ):
                    fail("macOS chart metric or change")
        for lang in ("zh", "en"):
            path = site / f"assets/moli-macos-success-resources-{lang}.svg"
            if (
                not path.is_file()
                or "RSS" not in path.read_text()
                or "CPU" not in path.read_text()
                or "PSS" in path.read_text()
            ):
                fail("macOS chart axes")
    correction = model.get("functional_contract_correction") or {}
    if (
        correction.get("task_count") != 2
        or correction.get("configuration_count") != 3
        or correction.get("calls") != 18
        or correction.get("success_rates_changed") is not False
    ):
        fail("functional correction summary")
    correction_info = correction.get("evidence") or {}
    correction_path = site / correction_info.get("url", "")
    if not correction_path.resolve().is_relative_to(site.resolve()):
        fail("unsafe functional correction target")
    if check_files and (
        not correction_path.is_file()
        or correction_path.is_symlink()
        or digest(correction_path) != correction_info.get("sha256")
        or correction_path.stat().st_size != correction_info.get("bytes")
    ):
        fail("functional correction evidence integrity")
    if check_files:
        receipt = json.loads(correction_path.read_text())
        if receipt.get("schema") != "moli_functional_contract_correction/1" or receipt.get(
            "harness_commit"
        ) != correction.get("harness_commit"):
            fail("functional correction identity")
        if [row.get("id") for row in receipt.get("configurations", [])] != [
            "114_off",
            "118_off",
            "118_on",
        ]:
            fail("functional correction configurations")
        if any(
            status != "fail"
            for row in receipt["configurations"]
            for task in row["outcomes"].values()
            for status in task["attempts"]
        ):
            fail("functional correction outcomes")
    costs = model.get("task_cost") or []
    if len(costs) != count or {x["id"] for x in costs} != {x["id"] for x in inv}:
        fail("task cost coverage")
    cost_by_id = {x["id"]: x for x in costs}
    if len(cost_by_id) != count:
        fail("duplicate task cost")
    full = []
    for item in inv:
        p = site / "data/cases" / f"{item['id']}.json"
        if check_files and not p.is_file():
            fail("broken case target")
        c = json.loads(p.read_text()) if check_files else item.get("_case")
        if not c:
            fail("missing case")
        if c["id"] != item["id"] or c["stratum"] != item["stratum"]:
            fail("case identity")
        cost = cost_by_id[c["id"]]
        for mode in ("off", "on"):
            attempts = c["modes"][mode]["attempts"]
            if len(attempts) != 3 or [x["ordinal"] for x in attempts] != [1, 2, 3]:
                fail("attempt coverage")
            passes = sum(x["status"] == "pass" for x in attempts)
            if passes != c["modes"][mode]["passes"] or passes != item[mode + "_passes"]:
                fail("pass summary")
            if passes != cost[mode + "_passes"]:
                fail("task cost pass count")
            if not near(cost[mode + "_duration_ms"], sum(a["duration_ms"] for a in attempts)):
                fail("task cost duration")
            if not near(
                cost[mode + "_cpu_ms"], sum(a["resource"]["cpu_total_ms"] for a in attempts)
            ):
                fail("task cost CPU")
            details = sorted({x["failure"]["detail"] for x in attempts if x["status"] != "pass"})
            if details != item.get(mode + "_fail_details"):
                fail("failure summary")
            for a in attempts:
                if a["status"] != "pass" and not (
                    a["failure"]["detail"] or a["failure"]["class"] == "unavailable"
                ):
                    fail("failure without exact error or unavailable state")
                if not isinstance(a["resource"].get("cpu_total_ms"), (int, float)):
                    fail("missing CPU")
                if not isinstance(a["resource"].get("pss_end_bytes"), (int, float)):
                    fail("missing PSS")
                if a["resource"].get("fixture_tx_bytes") is None:
                    fail("missing fixture traffic")
                if not a.get("evidence", {}).get("baseline", {}).get("grader.json"):
                    fail("missing grader target")
                if a["status"] != "pass" and not (
                    a["evidence"]["baseline"].get("stderr.log")
                    or a["evidence"]["baseline"].get("cdp.jsonl")
                ):
                    fail("failure without log target")
                for files in a["evidence"].values():
                    for _name, info in files.items():
                        if not info.get("url") or not info.get("sha256"):
                            fail("broken evidence reference")
                        target = site / info["url"]
                        if not target.resolve().is_relative_to(site.resolve()):
                            fail("unsafe evidence target")
                        if check_files and (
                            not target.is_file()
                            or target.is_symlink()
                            or digest(target) != info["sha256"]
                            or target.stat().st_size != info["bytes"]
                        ):
                            fail("bad evidence hash or target")
            full.extend((mode, a) for a in attempts)
    for mode in ("off", "on"):
        calls = [a for m, a in full if m == mode]
        if len(calls) != calls_per_mode:
            fail("call matrix")
        b = model["metrics"][mode]["baseline"]
        p = model["metrics"][mode]["profile"]
        if b["passes"] != sum(a["status"] == "pass" for a in calls):
            fail("headline pass count")
        if not near(b["duration_ms_all"]["sum"], sum(a["duration_ms"] for a in calls)):
            fail("headline duration")
        if not near(p["cpu_ms_all"]["sum"], sum(a["resource"]["cpu_total_ms"] for a in calls)):
            fail("headline CPU")
        if p["fixture_tx_bytes_all"]["sum"] != sum(
            a["resource"]["fixture_tx_bytes"] for a in calls
        ):
            fail("headline traffic")
    counts = {key: 0 for key in ("both_pass", "both_fail", "off_fail_on_pass", "off_pass_on_fail")}
    for item in inv:
        c = json.loads((site / "data/cases" / f"{item['id']}.json").read_text())
        for off, on in zip(
            c["modes"]["off"]["attempts"], c["modes"]["on"]["attempts"], strict=True
        ):
            a = off["status"] == "pass"
            b = on["status"] == "pass"
            counts[
                "both_pass"
                if a and b
                else "off_pass_on_fail"
                if a
                else "off_fail_on_pass"
                if b
                else "both_fail"
            ] += 1
    if model["paired"]["baseline"] != counts:
        fail("paired relation counts")
    if not near(
        sum(x["off_duration_ms"] - x["on_duration_ms"] for x in costs),
        model["metrics"]["off"]["baseline"]["duration_ms_all"]["sum"]
        - model["metrics"]["on"]["baseline"]["duration_ms_all"]["sum"],
    ):
        fail("task cost total")
    common = [
        (c["modes"]["off"]["attempts"][i], c["modes"]["on"]["attempts"][i])
        for c in (
            json.loads((site / "data/cases" / f"{item['id']}.json").read_text()) for item in inv
        )
        for i in range(3)
        if all(
            c["modes"][m]["attempts"][i][key] == "pass"
            for m in ("off", "on")
            for key in ("status", "profile_status")
        )
    ]
    if (
        len(common) != model["common_pass"]["count"]
        or len(common) > model["paired"]["baseline"]["both_pass"]
    ):
        fail("common pass denominator")
    for mode, index in [("off", 0), ("on", 1)]:
        c = model["common_pass"]
        if not near(
            c["cpu_total_ms"][mode], sum(x[index]["resource"]["cpu_total_ms"] for x in common)
        ):
            fail("common pass CPU")
        if not near(
            c["pss_end_mean_bytes"][mode],
            statistics.mean(x[index]["resource"]["pss_end_bytes"] for x in common),
        ):
            fail("common pass PSS")
        if not near(
            c["fixture_tx_total_bytes"][mode],
            sum(x[index]["resource"]["fixture_tx_bytes"] for x in common),
        ):
            fail("common pass traffic")
        q = model["quality"][mode]
        if q["resource_missing"]:
            fail("missing resource evidence")
        calibration = q.get("calibration", {})
        if not calibration.get("acceptable"):
            fail("resource timing calibration failed")
        if (
            calibration.get("matched_attempts") != calls_per_mode
            or not calibration.get("complete_pairing")
            or calibration.get("provenance_mismatches")
        ):
            fail("invalid calibration pairing")
        if (
            model["protocol"].get("timing_primary") != "unprofiled_driver_and_grade"
            or model["protocol"].get("resource_scope") != "observed_profiled_process_tree"
        ):
            fail("measurement scope conflated")
        mismatch = sum(a["status"] != a["profile_status"] for m, a in full if m == mode)
        if q["status_mismatch_count"] != mismatch:
            fail("profile mismatch disclosure")
        if not isinstance(model.get("host_context", {}).get("shared_macos_host"), bool):
            fail("missing host scope")
    for correction in model.get("resource_window_corrections", []):
        info = correction["evidence"]
        path = site / info["url"]
        if (
            not path.resolve().is_relative_to(site.resolve())
            or not path.is_file()
            or digest(path) != info["sha256"]
        ):
            fail("correction evidence integrity")
        rows = json.loads(path.read_text())
        if len(rows) != calls_per_mode or correction["calls"] != calls_per_mode:
            fail("correction scope")
        if not near(sum(x["cpu_total_ms"] for x in rows), correction["old_cpu_ms"]):
            fail("correction CPU")
    for path, language in [(site / "index.html", "zh-CN"), (site / "en/index.html", "en")]:
        if not path.is_file() or f'lang="{language}"' not in path.read_text():
            fail("locale shell")
    for asset in ("app.js", "style.css"):
        if not (site / asset).is_file():
            fail("missing shared asset")
    if not (site / "README.md").is_file():
        fail("missing offline bundle instructions")
    if (
        not (site / "data/version-comparison.json").is_file()
        or json.loads((site / "data/version-comparison.json").read_text()) != comparison
    ):
        fail("version comparison data target")
    charts = {
        "zh": (site / "assets/moli-version-success-resources-zh.svg", "372 题成功率与资源消耗"),
        "en": (
            site / "assets/moli-version-success-resources-en.svg",
            "Success on 372 cases vs. resource use",
        ),
    }
    for chart_name, (chart, title) in charts.items():
        if (
            not chart.is_file()
            or '<title id="title">' not in chart.read_text()
            or title not in chart.read_text()
        ):
            fail("summary chart target " + chart_name)
        chart_text = chart.read_text()
        for row in version_rows:
            for key in ("pss_change_pct", "cpu_change_pct"):
                if f"{row[key]:+.2f}%" not in chart_text:
                    fail(
                        "summary chart missing baseline change "
                        + chart_name
                        + " "
                        + row["id"]
                        + " "
                        + key
                    )
    if not (site / "reproduction/plot_version_summary.py").is_file():
        fail("missing chart reproduction script")
    js = (site / "app.js").read_text()
    css = (site / "style.css").read_text()
    if (
        "data-report-detail" not in js
        or "data-evidence" not in js
        or "showModal()" not in js
        or "trigger?.focus()" not in js
    ):
        fail("evidence interaction contract")
    if "data-metric-direction" not in js or "winner" not in css:
        fail("highlight contract")
    if check_files:
        bad = re.compile(
            r"/(?:Users|home|private/tmp|tmp|results|work)/|127\.0\.0\.1:\d+|ARTIFACT_SITE_TOKEN"
        )
        for p in site.rglob("*"):
            if (
                p.is_file()
                and p.suffix in (".json", ".jsonl", ".log", ".html", ".js", ".css")
                and bad.search(p.read_text(errors="replace"))
            ):
                fail("private path or endpoint leaked")
    return True


model = json.loads((SITE / "data/model.json").read_text())
if args.release:
    profile_path = (
        pathlib.Path(__file__).resolve().parents[2] / "evaluation/moli/current-report.json"
    )
    profile = json.loads(profile_path.read_text())
    if [row["id"] for row in model["functional_methods"]] != profile["method_ids"]:
        fail("release requires the complete bound functional method matrix")
    if (
        not model.get("macos_comparison")
        or [row["id"] for row in model["macos_comparison"]["configurations"]]
        != profile["macos_method_ids"]
    ):
        fail("release requires all bound macOS resource configurations")
validate(model, SITE)
# Semantic negative controls must fail through the same validator.
negative_controls = 0


def rejected(change):
    global negative_controls
    copy_model = copy.deepcopy(model)
    change(copy_model)
    try:
        validate(copy_model, SITE)
    except ValueError:
        negative_controls += 1
        return
    fail("negative control passed unexpectedly")


rejected(
    lambda m: m["metrics"]["on"]["baseline"].update(
        passes=m["metrics"]["on"]["baseline"]["passes"] + 1
    )
)
rejected(lambda m: m["case_outcomes"].update(on=m["case_outcomes"]["on"] + 1))
rejected(
    lambda m: m["case_outcomes"]["paired"].update(
        off_fail_on_pass=m["case_outcomes"]["paired"]["off_fail_on_pass"] + 1
    )
)
rejected(lambda m: m["common_pass"]["cpu_total_ms"].update(off=0))
rejected(lambda m: m["cases"].pop())
rejected(lambda m: m["protocol"].update(score_eligible=True))
rejected(lambda m: m["cases"][0].update(off_fail_details=["wrong error"]))
rejected(lambda m: m["task_cost"][0].update(off_duration_ms=0))
rejected(lambda m: m["version_comparison"]["configurations"][1].update(pss_change_pct=99))
rejected(lambda m: m["version_comparison"]["configurations"][2].update(success_rate_pct=0))
rejected(lambda m: m["combined_comparison"].update(baseline="119_official_on"))
rejected(lambda m: m["combined_comparison"]["configurations"].reverse())
rejected(lambda m: m["combined_comparison"]["configurations"][-1].update(cpu_change_pct=0))
rejected(lambda m: m["functional_contract_correction"].update(calls=17))
rejected(lambda m: m["functional_contract_correction"].update(success_rates_changed=True))
if model.get("fixed_candidates"):
    rejected(
        lambda m: m["functional_methods"][-1].update(
            final_passed_cases=m["functional_methods"][-1]["final_passed_cases"] + 1
        )
    )
    rejected(lambda m: next(iter(m["fixed_candidates"].values()))["population"].update(calls=1115))
for resource_key in ("macos_resource", "official_resource"):
    if not model.get(resource_key):
        continue
    rejected(
        lambda m, resource_key=resource_key: m[resource_key]["summary"]["population"].update(
            calls_per_configuration=2784
        )
    )
    rejected(
        lambda m, resource_key=resource_key: m[resource_key]["summary"]["population"].update(
            frozen_comparison_calls=1044
        )
    )
    rejected(
        lambda m, resource_key=resource_key: m[resource_key]["summary"]["quality"].update(
            pss_available=True
        )
    )
    rejected(
        lambda m, resource_key=resource_key: next(
            iter(m[resource_key]["summary"]["configurations"].values())
        )["metrics"]["all_predeclared_calls"]["cpu_time_ms"].update(sum=0)
    )
# Evidence target and missing-error mutations run on an isolated validation view.

with tempfile.TemporaryDirectory() as td:
    temp = pathlib.Path(td) / "site"
    shutil.copytree(SITE, temp)
    failure_id = next(x["id"] for x in model["cases"] if x["off_passes"] == 0)
    bad_case = temp / "data/cases" / f"{failure_id}.json"
    original = bad_case.read_text()
    item = json.loads(original)
    item["modes"]["off"]["attempts"][0]["failure"] = {"class": "cdp_semantic", "detail": ""}
    bad_case.write_text(json.dumps(item))
    try:
        validate(model, temp)
    except ValueError:
        negative_controls += 1
    else:
        fail("missing-error negative control passed")
    bad_case.write_text(original)
    item = json.loads(original)
    item["modes"]["off"]["attempts"][0]["evidence"]["baseline"]["grader.json"]["url"] = (
        "data/evidence/absent.json"
    )
    bad_case.write_text(json.dumps(item))
    try:
        validate(model, temp)
    except ValueError:
        negative_controls += 1
    else:
        fail("broken-target negative control passed")
    bad_case.write_text(original)
    item = json.loads(original)
    item["modes"]["off"]["attempts"][0]["evidence"]["baseline"].pop("stderr.log", None)
    item["modes"]["off"]["attempts"][0]["evidence"]["baseline"].pop("cdp.jsonl", None)
    bad_case.write_text(json.dumps(item))
    try:
        validate(model, temp)
    except ValueError:
        negative_controls += 1
    else:
        fail("missing-failure-log negative control passed")
    bad_case.write_text(original)
    leak = temp / "data/leak.log"
    leak.write_text("evidence stored at /results/internal/attempt.json")
    try:
        validate(model, temp)
    except ValueError:
        negative_controls += 1
    else:
        fail("private-path negative control passed")
candidate_calls = sum(c["population"]["calls"] for c in model.get("fixed_candidates", {}).values())

subprocess.run(
    [
        "node",
        str(pathlib.Path(__file__).resolve().parents[2] / "tests/moli_report_render_contract.cjs"),
        "--site",
        str(SITE),
    ],
    check=True,
)
print(
    json.dumps(
        {
            "verified_cases": model["task_count"],
            "verified_calls": model["attempts_per_mode"] * 4
            + model["functional_contract_correction"]["calls"]
            + candidate_calls,
            "verified_evidence_files": len(list((SITE / "data/evidence").rglob("*.*"))),
            "negative_controls": negative_controls,
            "result": "pass",
        }
    )
)
