from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import statistics

ROOT = pathlib.Path(os.environ.get("BROWSER_EVAL_DATA_HOME", pathlib.Path.home() / ".browser-eval"))
from harness.record_io import load_jsonl
from harness.report_languages import english_notice, generated_languages, write_language_contract
from harness.resource_calibration import duration_calibration
from lexbench_webfetch.paths import contained_path, safe_identifier

from . import charts, reproduction
from .charts import combine_comparisons, render_chart
from .resource_evidence import load_matrix
from .resource_summary import summarize as summarize_linux_profiles
from .resource_summary import validate_summary as validate_linux_summary

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]


def read_comparison_task(roots, relative, *, expected_sha256):
    """Read exact measured task bytes from the declared source roots."""
    for root in roots:
        path = contained_path(root, relative, reject_symlinks=True)
        if path.is_file():
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == expected_sha256:
                return data
    raise ValueError("Measured task bytes are missing from declared sources: " + str(relative))


def write_presentation(source, output, combined, version, macos=None, *, include_english=False):
    """Materialize only requested editions; translation sources remain reusable."""
    write_language_contract(output, include_english=include_english)
    languages = generated_languages(output)
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    comparisons = [
        ("all", combined, ("memory", "cpu")),
        ("version", version, ("pss", "cpu")),
    ]
    if macos:
        comparisons.append(("macos", macos, ("rss", "cpu")))
    for name, comparison, metrics in comparisons:
        for language in languages:
            render_chart(
                comparison, assets / f"comparison-{name}-success-resources-{language}.svg",
                metrics, language,
            )
    entries = [("zh-CN", "", "index.html")]
    if include_english:
        entries.extend([("en", "", "en.html"), ("en", "../", "en/index.html")])
    else:
        # Retain the published flat entry as a notice for existing external links.
        (output / "en.html").write_text(english_notice(nested=False))
    template = (source / "index.html").read_text()
    for language, base, entry in entries:
        target = output / entry
        target.parent.mkdir(exist_ok=True)
        shell = template.replace("{{lang}}", language).replace("{{base}}", base)
        for name in ("style.css", "app.js"):
            digest = hashlib.sha256((source / name).read_bytes()).hexdigest()[:12]
            shell = shell.replace(name, f"{name}?v={digest}")
        target.write_text(shell)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        type=pathlib.Path,
        required=True,
    )
    parser.add_argument("--cohort", type=pathlib.Path)
    parser.add_argument("--summary", type=pathlib.Path)
    parser.add_argument("--source", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument(
        "--include-english", action="store_true", help="Also generate English pages and charts"
    )
    parser.add_argument(
        "--vendor-dir", type=pathlib.Path
    )
    parser.add_argument(
        "--functional-correction", type=pathlib.Path
    )
    parser.add_argument(
        "--fixed-summary",
        action="append",
        nargs=2,
        metavar=("METHOD_ID", "SUMMARY_PATH"),
        default=[],
    )
    parser.add_argument("--macos-resource-summary", type=pathlib.Path)
    parser.add_argument("--official-resource-summary", type=pathlib.Path)
    parser.add_argument("--resource-matrix", type=pathlib.Path)
    parser.add_argument("--linux-resource-profiles", type=pathlib.Path)
    args = parser.parse_args(argv)
    report_profile = json.loads(args.profile.read_bytes())
    paths = report_profile.get("paths", {})

    def configured_path(name, override=None):
        value = override if override is not None else paths.get(name)
        if not value:
            parser.error(f"Report profile requires paths.{name} or an explicit CLI path")
        value = pathlib.Path(value).expanduser()
        return value if value.is_absolute() else (REPOSITORY_ROOT if name == "source" else ROOT) / value

    for name in ("cohort", "source", "output", "vendor_dir", "functional_correction"):
        setattr(args, name, configured_path(name, getattr(args, name)))
    BENCH_ROOT = configured_path("runner", os.environ.get("LEXBENCH_REPORT_RUNNER_ROOT"))
    VERSION_RUNS = configured_path("version_runs")
    VERSION_CANDIDATE = configured_path("version_candidate")
    LAYOUT_POLICY_ROOT = configured_path("layout_policy_runner")
    RUNS = args.cohort.parent
    if args.summary is None:
        args.summary = RUNS / "report-runs.json"
    resource_builds = report_profile.get("resource_builds", {})
    # The report profile names the experiment; the chart schema names plotted calls.
    # Convert once at this boundary instead of passing the profile to render_chart.
    resource_scope = report_profile["resource_population"]
    chart_population = {
        "total_tasks": resource_scope["tasks"],
        "calls_per_task": resource_scope["attempts_per_task"],
        "chart_tasks": resource_scope["chart_tasks"],
        "chart_calls": resource_scope["chart_calls"],
    }
    SRC = args.source.resolve()
    OUT = args.output.resolve()
    FUNCTIONAL_CORRECTION = args.functional_correction.absolute()
    cohort = json.loads(
        contained_path(args.cohort.parent, args.cohort.name, reject_symlinks=True).read_text()
    )
    summary = json.loads(
        contained_path(args.summary.parent, args.summary.name, reject_symlinks=True).read_text()
    )
    selected = {
        x["task_id"]: (group, x["sha256"])
        for group, items in cohort["strata"].items()
        for x in items
    }
    for task_id in selected:
        safe_identifier(task_id)
    protocol_file = contained_path(RUNS, "protocol.json", reject_symlinks=True)
    contract = json.loads(protocol_file.read_text())
    certificate = json.loads(
        contained_path(RUNS, "certification.json", reject_symlinks=True).read_text()
    )
    if not certificate["protocol_sha256"] == hashlib.sha256(protocol_file.read_bytes()).hexdigest():
        raise ValueError("Protocol hash mismatch")
    for rel, digest in certificate["artifact_sha256"].items():
        path = contained_path(RUNS, rel, reject_symlinks=True)
        if not (
            path.is_file()
            and (not path.is_symlink())
            and (hashlib.sha256(path.read_bytes()).hexdigest() == digest)
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: path.is_file() and (not path.is_symlink()) and (hashlib.sha256(path.read_bytes()).hexdigest() == digest)"
            )
    if not (set(selected) == set(contract["task_ids"]) and (not contract["try_layout"])):
        raise ValueError(
            "Invalid Lexbench report evidence: set(selected) == set(contract['task_ids']) and (not contract['try_layout'])"
        )
    if not {t: h for t, (_, h) in selected.items()} == {
        t: v["sha256"] for t, v in contract["tasks"].items()
    }:
        raise ValueError(
            "Invalid Lexbench report evidence: {t: h for t, (_, h) in selected.items()} == {t: v['sha256'] for t, v in contract['tasks'].items()}"
        )
    task_count = len(selected)
    k_runs = cohort["k_runs"]
    call_count = task_count * k_runs
    if not (
        task_count == report_profile["functional_population"]["cases"]
        and k_runs == report_profile["functional_population"]["attempts_per_case"]
    ):
        raise ValueError("Functional population differs from the report profile")
    if not (task_count > 0 and k_runs > 0):
        raise ValueError("Invalid Lexbench report evidence: task_count > 0 and k_runs > 0")
    label = summary.get("runs") or {
        "off_baseline": "m117_layout_off_baseline_init_0918",
        "on_baseline": "m117_layout_on_baseline_init_0918",
        "off_engine": "m117_layout_off_engine_init_0918",
        "on_engine": "m117_layout_on_engine_init_0918",
    }
    path_re = re.compile(r'/(?:Users|home|private/tmp|tmp|results|work)/[^\s\'"<>]+')
    endpoint_re = re.compile(r"(?:(?:https?|wss?)://)?127\.0\.0\.1:\d+")

    def sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def clean(s: str) -> str:
        return path_re.sub("<local path>", endpoint_re.sub("<local fixture>", s))

    def read_run(name: str):
        safe_identifier(name)
        p = contained_path(RUNS, label[name], reject_symlinks=True)
        if not (p.is_dir() and (not p.is_symlink())):
            raise ValueError(
                "Invalid Lexbench report evidence: p.is_dir() and (not p.is_symlink())"
            )
        if not all(
            (
                not (p / f).is_symlink()
                for f in ("run_manifest.json", "results.jsonl", "host_summary.json")
            )
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: all((not (p / f).is_symlink() for f in ('run_manifest.json', 'results.jsonl', 'host_summary.json')))"
            )
        m = json.loads((p / "run_manifest.json").read_text())
        rows = load_jsonl(p / "results.jsonl")
        if not m["run_id"] == label[name]:
            raise ValueError("Invalid Lexbench report evidence: m['run_id'] == label[name]")
        if not (
            len(rows) == call_count
            and m["completion_status"] == "completed"
            and (m["expected_result_rows"] == call_count)
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: len(rows) == call_count and m['completion_status'] == 'completed' and (m['expected_result_rows'] == call_count)"
            )
        if not m["engines"]["moli"]["sha256"] == cohort["moli_sha256"]:
            raise ValueError(
                "Invalid Lexbench report evidence: m['engines']['moli']['sha256'] == cohort['moli_sha256']"
            )
        if not m["bench_manifest"]["sha256"] == cohort["manifest_sha256"]:
            raise ValueError(
                "Invalid Lexbench report evidence: m['bench_manifest']['sha256'] == cohort['manifest_sha256']"
            )
        if not {x["task_id"]: x["sha256"] for x in m["resolved_tasks"]} == {
            t: h for t, (_, h) in selected.items()
        }:
            raise ValueError(
                "Invalid Lexbench report evidence: {x['task_id']: x['sha256'] for x in m['resolved_tasks']} == {t: h for t, (_, h) in selected.items()}"
            )
        mode = name.split("_")[0]
        prof = name.split("_")[1]
        if not m["resource_profile"]["mode"] == prof:
            raise ValueError(
                "Invalid Lexbench report evidence: m['resource_profile']['mode'] == prof"
            )
        if not all((r["engine_provenance"]["layout_enabled"] == (mode == "on") for r in rows)):
            raise ValueError(
                "Invalid Lexbench report evidence: all((r['engine_provenance']['layout_enabled'] == (mode == 'on') for r in rows))"
            )
        by = {(r["task_id"], r["attempt"]): r for r in rows}
        if not set(by) == {
            (task, attempt) for task in selected for attempt in range(1, k_runs + 1)
        }:
            raise ValueError(
                "Invalid Lexbench report evidence: set(by) == {(task, attempt) for task in selected for attempt in range(1, k_runs + 1)}"
            )
        return p, m, by

    runs = {name: read_run(name) for name in label}
    first_manifest = runs["off_baseline"][1]
    protocol = {
        "runner_sha256": first_manifest["runner"]["source"]["tree_sha256"],
        "fixtures_sha256": first_manifest["runner"]["fixtures"]["tree_sha256"],
        "seed": first_manifest["seed"],
        "k_runs": first_manifest["k_runs"],
        "timing_primary": "unprofiled_driver_and_grade",
        "resource_scope": "observed_profiled_process_tree",
        "timing_calibration_scope": "paired profiler-off/on rounds; one worker per round",
        "jobs": first_manifest["runner"]["jobs"],
        "browser_reuse": first_manifest["runner"]["browser_reuse"],
        "score_eligible": first_manifest["score_eligible"],
        "try_layout": False,
        "historical_version": "0.1.1",
        "historical_failed_cases": 372,
    }
    for run_path, manifest, _ in runs.values():
        if not not (run_path / "layout_retry_results.jsonl").exists():
            raise ValueError(
                "Invalid Lexbench report evidence: not (run_path / 'layout_retry_results.jsonl').exists()"
            )
        if not (manifest["k_runs"] == 3 and manifest["runner"]["jobs"] == 1):
            raise ValueError(
                "Invalid Lexbench report evidence: manifest['k_runs'] == 3 and manifest['runner']['jobs'] == 1"
            )
        if not manifest["runner"]["source"]["tree_sha256"] == protocol["runner_sha256"]:
            raise ValueError(
                "Invalid Lexbench report evidence: manifest['runner']['source']['tree_sha256'] == protocol['runner_sha256']"
            )
        if not manifest["runner"]["fixtures"]["tree_sha256"] == protocol["fixtures_sha256"]:
            raise ValueError(
                "Invalid Lexbench report evidence: manifest['runner']['fixtures']['tree_sha256'] == protocol['fixtures_sha256']"
            )
        if not (manifest["seed"] == protocol["seed"] and manifest["k_runs"] == protocol["k_runs"]):
            raise ValueError(
                "Invalid Lexbench report evidence: manifest['seed'] == protocol['seed'] and manifest['k_runs'] == protocol['k_runs']"
            )
        if not (
            manifest["runner"]["jobs"] == protocol["jobs"]
            and manifest["runner"]["browser_reuse"] == protocol["browser_reuse"]
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: manifest['runner']['jobs'] == protocol['jobs'] and manifest['runner']['browser_reuse'] == protocol['browser_reuse']"
            )
        if not (manifest["score_eligible"] is False and protocol["score_eligible"] is False):
            raise ValueError(
                "Invalid Lexbench report evidence: manifest['score_eligible'] is False and protocol['score_eligible'] is False"
            )
    for key, row in runs["off_baseline"][2].items():
        if not all((by[key]["seed"] == row["seed"] for _, _, by in runs.values())):
            raise ValueError(
                "Invalid Lexbench report evidence: all((by[key]['seed'] == row['seed'] for _, _, by in runs.values()))"
            )

    def percentile(values, p):
        values = sorted(values)
        position = (len(values) - 1) * p
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)

    def stats(values):
        values = list(values)
        return {
            "n": len(values),
            "sum": sum(values),
            "mean": statistics.mean(values) if values else None,
            "p50": percentile(values, 0.5) if values else None,
            "p95": percentile(values, 0.95) if values else None,
        }

    metrics = {}
    quality = {}
    paired = {}
    for mode in ("off", "on"):
        metrics[mode] = {}
        for phase in ("baseline", "engine"):
            rows = list(runs[mode + "_" + phase][2].values())
            passes = [r for r in rows if r["status"] == "pass"]
            data = {
                "calls": len(rows),
                "passes": len(passes),
                "status_counts": dict(collections.Counter(r["status"] for r in rows)),
                "duration_ms_all": stats(r["duration_ms"] for r in rows),
                "duration_ms_pass": stats(r["duration_ms"] for r in passes),
            }
            if phase == "engine":
                for population, items in [("all", rows), ("pass", passes)]:
                    for name, key, scale in [
                        ("cpu_ms", "cpu_total_ms", 1),
                        ("pss_peak_mib", "pss_peak_bytes", 1048576),
                        ("pss_delta_mib", "pss_peak_delta_bytes", 1048576),
                    ]:
                        data[name + "_" + population] = stats(
                            r["resource"][key] / scale for r in items
                        )
                    data["fixture_tx_bytes_" + population] = stats(
                        r["resource"]["fixture_traffic"]["fixture_app_tx_body_bytes"] for r in items
                    )
            metrics[mode]["profile" if phase == "engine" else phase] = data
        b = runs[mode + "_baseline"][2]
        e = runs[mode + "_engine"][2]
        mismatch = [list(key) for key in b if b[key]["status"] != e[key]["status"]]
        quality[mode] = {
            "status_mismatch_count": len(mismatch),
            "status_mismatch_keys": mismatch,
            "baseline_host": json.loads(
                (runs[mode + "_baseline"][0] / "host_summary.json").read_text()
            ),
            "engine_host": json.loads(
                (runs[mode + "_engine"][0] / "host_summary.json").read_text()
            ),
            "resource_missing": sum(not r.get("resource") for r in e.values()),
            "resource_quality_flags": dict(
                collections.Counter(
                    flag
                    for r in e.values()
                    for flag in r.get("resource", {}).get("quality_flags", [])
                )
            ),
            "resource_unavailable": dict(
                collections.Counter(
                    key for r in e.values() for key in r.get("resource", {}).get("unavailable", {})
                )
            ),
        }
    for mode in ("off", "on"):
        quality[mode]["calibration"] = duration_calibration(
            list(runs[mode + "_engine"][2].values()),
            list(runs[mode + "_baseline"][2].values()),
            10,
            profiled_manifest=runs[mode + "_engine"][1],
            baseline_manifest=runs[mode + "_baseline"][1],
        )
    for phase in ("baseline", "engine"):
        counts = collections.Counter()
        for key, off in runs["off_" + phase][2].items():
            on = runs["on_" + phase][2][key]
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
        paired[phase] = {
            key: counts[key]
            for key in ("off_fail_on_pass", "off_pass_on_fail", "both_pass", "both_fail")
        }

    if OUT.exists():
        raise ValueError("Output already exists; choose a new build directory")
    (OUT / "data/cases").mkdir(parents=True)
    (OUT / "data/evidence").mkdir(parents=True)

    def evidence_for(run_name: str, row: dict, run_root=None):
        p = run_root if run_root is not None else runs[run_name][0]
        folder = contained_path(p, row["artifact_dir"], reject_symlinks=True)
        result = {}
        for filename in (
            "grader.json",
            "stdout.log",
            "stderr.log",
            "cdp.jsonl",
            "resource.jsonl",
            "run.json",
        ):
            source = contained_path(folder, filename, reject_symlinks=True)
            if not source.exists():
                continue
            if not (
                source.is_file() and (not source.is_symlink()) and (source.stat().st_size < 8000000)
            ):
                raise ValueError(
                    "Invalid Lexbench report evidence: source.is_file() and (not source.is_symlink()) and (source.stat().st_size < 8000000)"
                )
            source_hash = sha(source.read_bytes())
            raw = source.read_text(errors="replace")
            data = clean(raw).encode()
            target = (
                OUT
                / "data/evidence"
                / safe_identifier(run_name)
                / safe_identifier(row["task_id"])
                / safe_identifier(str(row["attempt"]))
                / filename
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            result[filename] = {
                "url": str(target.relative_to(OUT)),
                "sha256": sha(data),
                "source_sha256": source_hash,
                "bytes": len(data),
            }
        return result

    def safe_failure(run_name, r, run_root=None):
        f = r.get("failure") or {}
        original = str(f.get("detail") or "")
        detail = original
        generic = {
            "script-reported checks failed",
            "inline assertions failed",
            "answer did not match server-side expectation",
        }
        if original in generic:
            root = run_root if run_root is not None else runs[run_name][0]
            folder = contained_path(root, r["artifact_dir"], reject_symlinks=True)
            for filename in ("stdout.log", "grader.json"):
                path = contained_path(folder, filename, reject_symlinks=True)
                if not path.is_file():
                    continue
                if not not path.is_symlink():
                    raise ValueError("Invalid Lexbench report evidence: not path.is_symlink()")
                raw = path.read_text(errors="replace")
                payload = None
                for text in [raw, *reversed(raw.splitlines())]:
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(payload, dict):
                        break
                if not isinstance(payload, dict):
                    continue
                checks = (
                    (payload.get("observations") or {}).get("checks") or payload.get("checks") or []
                )
                failed = next(
                    (c for c in checks if c.get("status") == "fail" and c.get("evidence")), None
                )
                if failed:
                    detail = str(failed["name"]) + ": " + str(failed["evidence"])
                    break
        return {
            "class": str(f.get("class") or ""),
            "detail": clean(detail),
            "original_detail": clean(original),
        }

    def resource_scalar(r):
        x = r.get("resource") or {}
        t = x.get("fixture_traffic") or {}
        return {
            k: x.get(k)
            for k in (
                "cpu_total_ms",
                "cpu_user_ms",
                "cpu_system_ms",
                "pss_baseline_bytes",
                "pss_end_bytes",
                "pss_peak_bytes",
                "pss_peak_delta_bytes",
                "samples_seen",
                "quality_flags",
                "measurement_backend",
                "unavailable",
            )
        } | {
            "fixture_rx_bytes": t.get("fixture_app_rx_body_bytes"),
            "fixture_tx_bytes": t.get("fixture_app_tx_body_bytes"),
        }

    cases = []
    for task_id, (stratum, digest) in selected.items():
        definition_path = contained_path(
            BENCH_ROOT,
            next(
                x["path"]
                for x in runs["off_baseline"][1]["resolved_tasks"]
                if x["task_id"] == task_id
            ),
            reject_symlinks=True,
        )
        raw = definition_path.read_bytes()
        if not sha(raw) == digest:
            raise ValueError("Invalid Lexbench report evidence: sha(raw) == digest")
        definition = json.loads(raw)
        item = {
            "id": task_id,
            "stratum": stratum,
            "title": definition.get("title", task_id),
            "description": definition.get("description", ""),
            "subset": definition["subset_id"],
            "features": definition.get("features", []),
            "scene": definition.get("scene", {}),
            "driver": definition.get("driver", {}),
            "grader": definition.get("grader", {}),
            "task_sha256": digest,
            "modes": {},
        }
        for mode in ("off", "on"):
            attempts = []
            for attempt in range(1, k_runs + 1):
                b = runs[mode + "_baseline"][2][(task_id, attempt)]
                e = runs[mode + "_engine"][2][(task_id, attempt)]
                attempts.append(
                    {
                        "ordinal": attempt,
                        "status": b["status"],
                        "duration_ms": b["duration_ms"],
                        "profile_status": e["status"],
                        "profile_duration_ms": e["duration_ms"],
                        "answer": clean(str(b.get("answer") or "")),
                        "failure": safe_failure(mode + "_baseline", b),
                        "resource": resource_scalar(e),
                        "evidence": {
                            "baseline": evidence_for(mode + "_baseline", b),
                            "profile": evidence_for(mode + "_engine", e),
                        },
                    }
                )
            item["modes"][mode] = {
                "passes": sum(a["status"] == "pass" for a in attempts),
                "attempts": attempts,
            }
        cases.append(item)
        (OUT / "data/cases" / f"{task_id}.json").write_text(
            json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
    cases.sort(key=lambda c: (list(cohort["strata"]).index(c["stratum"]), c["id"]))
    network_rx = {}
    cold_start = {}
    for mode in ("off", "on"):
        rx = [
            a["resource"]["fixture_rx_bytes"] for c in cases for a in c["modes"][mode]["attempts"]
        ]
        network_rx[mode] = {
            "total_bytes": sum(rx),
            "p50_bytes": percentile(rx, 0.5),
            "p95_bytes": percentile(rx, 0.95),
        }
        starts = []
        for i in range(1, 6):
            directory = contained_path(RUNS, f"cold_{i:02d}_{mode}", reject_symlinks=True)
            manifest = json.loads(
                contained_path(directory, "run_manifest.json", reject_symlinks=True).read_text()
            )
            if not manifest["engines"]["moli"]["sha256"] == cohort["moli_sha256"]:
                raise ValueError(
                    "Invalid Lexbench report evidence: manifest['engines']['moli']['sha256'] == cohort['moli_sha256']"
                )
            values = load_jsonl(contained_path(directory, "cold_start.jsonl", reject_symlinks=True))
            if not (len(values) == 1 and values[0]["status"] == "ready"):
                raise ValueError(
                    "Invalid Lexbench report evidence: len(values) == 1 and values[0]['status'] == 'ready'"
                )
            starts.extend(values)
        cold_start[mode] = {
            "n": 5,
            "ready_ms": {
                "p50": percentile([x["ready_ms"] for x in starts], 0.5),
                "p95": percentile([x["ready_ms"] for x in starts], 0.95),
            },
            "launch_cpu_ms": {"p50": percentile([x["launch_cpu_ms"] for x in starts], 0.5)},
            "launch_pss_bytes": {
                "p50": percentile([x["launch_peak_pss_bytes"] for x in starts], 0.5)
            },
        }

    shared = [
        (c["modes"]["off"]["attempts"][i], c["modes"]["on"]["attempts"][i])
        for c in cases
        for i in range(k_runs)
        if all(
            c["modes"][m]["attempts"][i][key] == "pass"
            for m in ("off", "on")
            for key in ("status", "profile_status")
        )
    ]
    if not len(shared) <= paired["baseline"]["both_pass"]:
        raise ValueError(
            "Invalid Lexbench report evidence: len(shared) <= paired['baseline']['both_pass']"
        )
    common_pass = {
        "count": len(shared),
        "baseline_count": paired["baseline"]["both_pass"],
        "scope": "Same task-attempt pairs that passed all four baseline and profiled rounds",
        "cpu_total_ms": {
            m: sum(pair[0 if m == "off" else 1]["resource"]["cpu_total_ms"] for pair in shared)
            for m in ("off", "on")
        },
        "pss_end_mean_bytes": {
            m: sum(pair[0 if m == "off" else 1]["resource"]["pss_end_bytes"] for pair in shared)
            / len(shared)
            if shared
            else None
            for m in ("off", "on")
        },
        "fixture_tx_total_bytes": {
            m: sum(pair[0 if m == "off" else 1]["resource"]["fixture_tx_bytes"] for pair in shared)
            for m in ("off", "on")
        },
    }
    task_cost = []
    for c in cases:
        off = c["modes"]["off"]["attempts"]
        on = c["modes"]["on"]["attempts"]
        task_cost.append(
            {
                "id": c["id"],
                "off_duration_ms": sum(a["duration_ms"] for a in off),
                "on_duration_ms": sum(a["duration_ms"] for a in on),
                "off_cpu_ms": sum(a["resource"]["cpu_total_ms"] for a in off),
                "on_cpu_ms": sum(a["resource"]["cpu_total_ms"] for a in on),
                "off_passes": c["modes"]["off"]["passes"],
                "on_passes": c["modes"]["on"]["passes"],
            }
        )
    task_cost.sort(key=lambda x: x["off_duration_ms"] - x["on_duration_ms"], reverse=True)
    if (
        not sum((x["off_duration_ms"] - x["on_duration_ms"] for x in task_cost))
        == metrics["off"]["baseline"]["duration_ms_all"]["sum"]
        - metrics["on"]["baseline"]["duration_ms_all"]["sum"]
    ):
        raise ValueError(
            "Invalid Lexbench report evidence: sum((x['off_duration_ms'] - x['on_duration_ms'] for x in task_cost)) == metrics['off']['baseline']['duration_ms_all']['sum'] - metrics['on']['baseline']['duration_ms_all']['sum']"
        )
    launch_receipts = {
        name: json.loads(
            contained_path(RUNS, run_id + "_launch.json", reject_symlinks=True).read_text()
        )
        for name, run_id in label.items()
        if (RUNS / (run_id + "_launch.json")).exists()
    }
    host_context = {
        "shared_macos_host": True,
        "concurrent_test_observed_at_start": any(
            r["host_busy_at_start"] for r in launch_receipts.values()
        )
        if launch_receipts
        else None,
        "launch_receipts": launch_receipts,
        "scope": "Docker VM telemetry does not establish macOS host isolation.",
    }
    observation_file = contained_path(RUNS, "macos-observations.jsonl", reject_symlinks=True)
    if observation_file.exists():
        observations = load_jsonl(observation_file)

        def timestamp(value):
            return datetime.datetime.fromisoformat(value).timestamp()

        host_context["observation_sha256"] = sha(observation_file.read_bytes())
        host_context["rounds"] = {}
        for name, (_, manifest, _) in runs.items():
            start = timestamp(manifest["started_at"])
            end = timestamp(manifest["completed_at"])
            samples = [row for row in observations if start <= timestamp(row["utc"]) <= end]
            host_context["rounds"][name] = {
                "samples": len(samples),
                "samples_with_other_moli_tests": sum(
                    bool(row["other_moli_test_processes"]) for row in samples
                ),
                "first_sample": samples[0]["utc"] if samples else None,
                "last_sample": samples[-1]["utc"] if samples else None,
                "run_started_at": manifest["started_at"],
                "run_completed_at": manifest["completed_at"],
                "macos_load1_median": statistics.median(row["load"][0] for row in samples)
                if samples
                else None,
                "macos_load1_max": max((row["load"][0] for row in samples), default=None),
                "broad_process_observer_samples": sum(
                    row.get("observer_version") == 2 for row in samples
                ),
            }
    corrections = []
    case_outcomes = {
        "rule": "all_recorded_attempts_pass",
        "repeats": k_runs,
        "total": task_count,
        "off": 0,
        "on": 0,
        "paired": dict.fromkeys(
            ("both_pass", "both_fail", "off_fail_on_pass", "off_pass_on_fail"), 0
        ),
    }
    for c in cases:
        a = all(x["status"] == "pass" for x in c["modes"]["off"]["attempts"])
        b = all(x["status"] == "pass" for x in c["modes"]["on"]["attempts"])
        case_outcomes["off"] += a
        case_outcomes["on"] += b
        case_outcomes["paired"][
            "both_pass"
            if a and b
            else "off_pass_on_fail"
            if a
            else "off_fail_on_pass"
            if b
            else "both_fail"
        ] += 1
    comparison_file = contained_path(VERSION_RUNS, "resource-comparison.json", reject_symlinks=True)
    version_protocol_file = contained_path(VERSION_RUNS, "protocol.json", reject_symlinks=True)
    resource_comparison = json.loads(comparison_file.read_text())
    resource_certificate = json.loads(
        contained_path(
            VERSION_RUNS, "resource-certification.json", reject_symlinks=True
        ).read_text()
    )
    if not sha(comparison_file.read_bytes()) == resource_certificate["comparison_sha256"]:
        raise ValueError(
            "Invalid Lexbench report evidence: sha((VERSION_RUNS / 'resource-comparison.json').read_bytes()) == resource_certificate['comparison_sha256']"
        )
    if not sha(version_protocol_file.read_bytes()) == resource_certificate["protocol_sha256"]:
        raise ValueError(
            "Invalid Lexbench report evidence: sha((VERSION_RUNS / 'protocol.json').read_bytes()) == resource_certificate['protocol_sha256']"
        )
    for rel, digest in resource_certificate["artifact_sha256"].items():
        path = contained_path(VERSION_RUNS, rel, reject_symlinks=True)
        if not (path.is_file() and (not path.is_symlink()) and (sha(path.read_bytes()) == digest)):
            raise ValueError(
                "Invalid Lexbench report evidence: path.is_file() and (not path.is_symlink()) and (sha(path.read_bytes()) == digest)"
            )
    version_protocol = json.loads(version_protocol_file.read_text())
    if not (
        set(version_protocol["functional_tasks"]) == set(selected)
        and len(version_protocol["functional_tasks"]) == len(selected)
    ):
        raise ValueError(
            "Invalid Lexbench report evidence: set(version_protocol['functional_tasks']) == set(selected) and len(version_protocol['functional_tasks']) == len(selected)"
        )
    functional_114 = contained_path(
        VERSION_CANDIDATE.parent, VERSION_CANDIDATE.name, reject_symlinks=True
    )
    functional_manifest = json.loads(
        contained_path(functional_114, "run_manifest.json", reject_symlinks=True).read_text()
    )
    functional_rows = load_jsonl(
        contained_path(functional_114, "results.jsonl", reject_symlinks=True)
    )
    candidate_114 = "v4_cdp_emulation_setdefaultbackgroundcoloroverride"
    if not (
        functional_manifest["completion_status"] == "completed"
        and functional_manifest["expected_result_rows"] == 3
    ):
        raise ValueError(
            "Invalid Lexbench report evidence: functional_manifest['completion_status'] == 'completed' and functional_manifest['expected_result_rows'] == 3"
        )
    if not len(functional_rows) == 3:
        raise ValueError("Invalid Lexbench report evidence: len(functional_rows) == 3")
    if not {(row["task_id"], row["attempt"]) for row in functional_rows} == {
        (candidate_114, attempt) for attempt in range(1, 4)
    }:
        raise ValueError(
            "Invalid Lexbench report evidence: {(row['task_id'], row['attempt']) for row in functional_rows} == {(candidate_114, attempt) for attempt in range(1, 4)}"
        )
    if not all((not row["engine_provenance"]["layout_enabled"] for row in functional_rows)):
        raise ValueError(
            "Invalid Lexbench report evidence: all((not row['engine_provenance']['layout_enabled'] for row in functional_rows))"
        )
    if not all(
        (
            row["engine_provenance"]["binary_sha256"]
            == version_protocol["configs"]["114_off"]["sha256"]
            for row in functional_rows
        )
    ):
        raise ValueError(
            "Invalid Lexbench report evidence: all((row['engine_provenance']['binary_sha256'] == version_protocol['configs']['114_off']['sha256'] for row in functional_rows))"
        )
    if not case_outcomes["off"] == 1:
        raise ValueError("Invalid Lexbench report evidence: case_outcomes['off'] == 1")
    if not [
        c["id"]
        for c in cases
        if all((row["status"] == "pass" for row in c["modes"]["off"]["attempts"]))
    ] == [candidate_114]:
        raise ValueError(
            "Invalid Lexbench report evidence: [c['id'] for c in cases if all((row['status'] == 'pass' for row in c['modes']['off']['attempts']))] == [candidate_114]"
        )
    if not all((row["status"] != "pass" for row in functional_rows)):
        raise ValueError(
            "Invalid Lexbench report evidence: all((row['status'] != 'pass' for row in functional_rows))"
        )
    passed_114 = 0
    population = "frozen_historical_common_pass_calls"
    version_rows = []
    for config, label_text, label_en, short_label, short_label_en, passed in (
        (
            "114_off",
            "Moli 1.1.4 · 关闭 layout",
            "Moli 1.1.4 · layout off",
            "1.1.4 关闭",
            "1.1.4 off",
            passed_114,
        ),
        (
            "118_off",
            "Moli 1.1.8 · 关闭 layout",
            "Moli 1.1.8 · layout off",
            "1.1.8 关闭",
            "1.1.8 off",
            case_outcomes["off"],
        ),
        (
            "118_on",
            "Moli 1.1.8 · 开启 layout",
            "Moli 1.1.8 · layout on",
            "1.1.8 开启",
            "1.1.8 on",
            case_outcomes["on"],
        ),
    ):
        resource = resource_comparison["memory"][config][population]
        version_rows.append(
            {
                "id": config,
                "label": label_text,
                "label_en": label_en,
                "short_label": short_label,
                "short_label_en": short_label_en,
                "passed_cases": passed,
                "total_cases": 372,
                "success_rate_pct": passed / 372 * 100,
                "pss_mib": resource["pss_peak_mib"]["median"],
                "cpu_ms": resource["cpu_time_ms"]["mean"],
                "cpu_p50_ms": resource["cpu_time_ms"]["median"],
                "cpu_p95_ms": resource["cpu_time_ms"]["p95"],
            }
        )
    baseline = version_rows[0]
    for row in version_rows:
        row["success_change_pp"] = row["success_rate_pct"] - baseline["success_rate_pct"]
        row["pss_change_pct"] = (row["pss_mib"] / baseline["pss_mib"] - 1) * 100
        row["cpu_change_pct"] = (row["cpu_ms"] / baseline["cpu_ms"] - 1) * 100
    version_comparison = {
        "baseline": "114_off",
        "configurations": version_rows,
        "functional_population": {
            "cases": 372,
            "attempts_per_case": 3,
            "rule": "all_three_attempts_pass",
            "version_118_direct_cases": 372,
            "version_114_candidate_cases": 1,
            "version_114_inherited_failures": 371,
            "version_114_candidate": candidate_114,
            "version_114_method": "differential_candidate_check",
        },
        "resource_population": {
            "experiment_cases": 557,
            "attempts_per_case": 5,
            **chart_population,
            "selection": population,
            "sample_interval_ms": 250,
        },
        "bindings": {
            "resource_comparison_sha256": resource_certificate["comparison_sha256"],
            "resource_protocol_sha256": resource_certificate["protocol_sha256"],
            "functional_114_results_sha256": sha((functional_114 / "results.jsonl").read_bytes()),
            "functional_114_manifest_sha256": sha(
                (functional_114 / "run_manifest.json").read_bytes()
            ),
        },
        "published_71_mib": {
            "value": 71,
            "status": resource_comparison["original_image_memory_status"],
        },
    }
    functional_methods = [
        {
            "id": "114_off",
            "label": "Moli 1.1.4 · 关闭 layout",
            "label_en": "Moli 1.1.4 · layout off",
            "policy": "fixed_off",
            "direct_cases": 1,
            "inherited_cases": 371,
            "initial_passed_cases": 0,
            "recovered_cases": 0,
            "final_passed_cases": 0,
            "retried_cases": 0,
            "extra_physical_calls": 0,
        },
        {
            "id": "118_off",
            "label": "Moli 1.1.8 · 关闭 layout",
            "label_en": "Moli 1.1.8 · layout off",
            "policy": "fixed_off",
            "direct_cases": 372,
            "inherited_cases": 0,
            "initial_passed_cases": case_outcomes["off"],
            "recovered_cases": 0,
            "final_passed_cases": case_outcomes["off"],
            "retried_cases": 0,
            "extra_physical_calls": 0,
        },
        {
            "id": "118_on",
            "label": "Moli 1.1.8 · 开启 layout",
            "label_en": "Moli 1.1.8 · layout on",
            "policy": "fixed_on",
            "direct_cases": 372,
            "inherited_cases": 0,
            "initial_passed_cases": case_outcomes["on"],
            "recovered_cases": 0,
            "final_passed_cases": case_outcomes["on"],
            "retried_cases": 0,
            "extra_physical_calls": 0,
        },
    ]
    for method in functional_methods:
        method["moli_sha256"] = version_protocol["configs"][method["id"]]["sha256"]
        method["physical_calls"] = 3 if method["id"] == "114_off" else 1116
    fixed_candidates = {}
    fixed_case_data = {c["id"]: {"id": c["id"], "title": c["title"], "methods": {}} for c in cases}
    public_candidate_files = {}
    labels = {
        "119_official_off": ("Moli 1.1.9 · 关闭 layout", "Moli 1.1.9 · layout off", "off"),
        "119_official_on": ("Moli 1.1.9 · 开启 layout", "Moli 1.1.9 · layout on", "on"),
        "119_off": ("Moli 1.1.9 修复版 · 关闭 layout", "Moli 1.1.9 patched · layout off", "off"),
        "119_on": ("Moli 1.1.9 修复版 · 开启 layout", "Moli 1.1.9 patched · layout on", "on"),
    }
    functional_task_ids_sha256 = sha(
        "".join(task_id + "\n" for task_id in sorted(selected)).encode()
    )
    if not len(dict(args.fixed_summary)) == len(args.fixed_summary):
        raise ValueError(
            "Invalid Lexbench report evidence: len(dict(args.fixed_summary)) == len(args.fixed_summary)"
        )
    if not set(dict(args.fixed_summary)).issubset(labels):
        raise ValueError(
            "Invalid Lexbench report evidence: set(dict(args.fixed_summary)).issubset(labels)"
        )
    for method_id, summary_path in sorted(
        args.fixed_summary, key=lambda item: list(labels).index(item[0])
    ):
        if not (method_id in labels and method_id not in fixed_candidates):
            raise ValueError(
                "Invalid Lexbench report evidence: method_id in labels and method_id not in fixed_candidates"
            )
        source_bytes = pathlib.Path(summary_path).read_bytes()
        candidate = json.loads(source_bytes)
        if not candidate["schema"] == "lexbench_moli_fixed_layout_summary/1":
            raise ValueError(
                "Invalid Lexbench report evidence: candidate['schema'] == 'lexbench_moli_fixed_layout_summary/1'"
            )
        label_zh, label_en, layout = labels[method_id]
        if not candidate["layout"] == layout:
            raise ValueError("Invalid Lexbench report evidence: candidate['layout'] == layout")
        if not candidate["population"] == {
            "cases": 372,
            "attempts_per_case": 3,
            "calls": 1116,
            "task_ids_sha256": functional_task_ids_sha256,
        }:
            raise ValueError(
                "Invalid Lexbench report evidence: candidate['population'] == {'cases': 372, 'attempts_per_case': 3, 'calls': 1116, 'task_ids_sha256': functional_task_ids_sha256}"
            )
        if not (
            {c["task_id"] for c in candidate["cases"]} == set(selected)
            and len(candidate["cases"]) == 372
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: {c['task_id'] for c in candidate['cases']} == set(selected) and len(candidate['cases']) == 372"
            )
        for case in candidate["cases"]:
            if not (
                len(case["attempts"]) == 3 and {a["attempt"] for a in case["attempts"]} == {1, 2, 3}
            ):
                raise ValueError(
                    "Invalid Lexbench report evidence: len(case['attempts']) == 3 and {a['attempt'] for a in case['attempts']} == {1, 2, 3}"
                )
            if not case["passes"] == sum((a["status"] == "pass" for a in case["attempts"])):
                raise ValueError(
                    "Invalid Lexbench report evidence: case['passes'] == sum((a['status'] == 'pass' for a in case['attempts']))"
                )
        source_runs = {}
        source_tasks = {}
        for binding in candidate["sources"]:
            run_id = binding["run_id"]
            if not re.fullmatch("[A-Za-z0-9_-]+", run_id):
                raise ValueError(
                    "Invalid Lexbench report evidence: re.fullmatch('[A-Za-z0-9_-]+', run_id)"
                )
            run_root = contained_path(
                LAYOUT_POLICY_ROOT,
                pathlib.Path("runs") / run_id,
                reject_symlinks=True,
            )
            manifest_path = contained_path(run_root, "run_manifest.json", reject_symlinks=True)
            if not (
                not run_root.is_symlink()
                and sha(manifest_path.read_bytes()) == binding["manifest_sha256"]
            ):
                raise ValueError(
                    "Invalid Lexbench report evidence: not run_root.is_symlink() and sha((run_root / 'run_manifest.json').read_bytes()) == binding['manifest_sha256']"
                )
            source_result = contained_path(run_root, binding["results_file"], reject_symlinks=True)
            if not (
                source_result.resolve().parent == run_root.resolve()
                and (not source_result.is_symlink())
            ):
                raise ValueError(
                    "Invalid Lexbench report evidence: source_result.resolve().parent == run_root.resolve() and (not source_result.is_symlink())"
                )
            if not sha(source_result.read_bytes()) == binding["results_sha256"]:
                raise ValueError(
                    "Invalid Lexbench report evidence: sha(source_result.read_bytes()) == binding['results_sha256']"
                )
            source_runs[run_id] = run_root
            for task in json.loads(manifest_path.read_text())["resolved_tasks"]:
                if task["task_id"] in source_tasks:
                    if not source_tasks[task["task_id"]]["sha256"] == task["sha256"]:
                        raise ValueError(
                            "Invalid Lexbench report evidence: source_tasks[task['task_id']]['sha256'] == task['sha256']"
                        )
                source_tasks[task["task_id"]] = task
        for case in candidate["cases"]:
            task = source_tasks[case["task_id"]]
            if task["sha256"] != case["task_sha256"]:
                raise ValueError("Candidate summary differs from its task manifest")
            task_bytes = read_comparison_task(
                [BENCH_ROOT, LAYOUT_POLICY_ROOT], task["path"],
                expected_sha256=task["sha256"],
            )
            case_detail = fixed_case_data[case["task_id"]]
            if "task_sha256" in case_detail:
                if not case_detail["task_sha256"] == task["sha256"]:
                    raise ValueError(
                        "Invalid Lexbench report evidence: case_detail['task_sha256'] == task['sha256']"
                    )
            case_detail["task_sha256"] = task["sha256"]
            case_detail["task_definition"] = json.loads(clean(task_bytes.decode()))
            detail = []
            for attempt in case["attempts"]:
                run_root = source_runs[attempt["source_run_id"]]
                raw = {**attempt, "task_id": case["task_id"]}
                evidence = evidence_for(method_id, raw, run_root)
                detail.append(
                    {
                        "ordinal": attempt["attempt"],
                        "status": attempt["status"],
                        "answer": clean(str(attempt.get("answer") or "")),
                        "failure": safe_failure(method_id, raw, run_root),
                        "evidence": evidence,
                    }
                )
            fixed_case_data[case["task_id"]]["methods"][method_id] = {
                "passes": case["passes"],
                "attempts": detail,
            }
        passed = sum(c["passes"] == 3 for c in candidate["cases"])
        if not (
            candidate["outcomes"]["passed_cases"] == passed
            and abs(candidate["outcomes"]["success_rate_pct"] - passed / 372 * 100) < 1e-09
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: candidate['outcomes']['passed_cases'] == passed and abs(candidate['outcomes']['success_rate_pct'] - passed / 372 * 100) < 1e-09"
            )
        if not sum((source["calls"] for source in candidate["sources"])) == 1116:
            raise ValueError(
                "Invalid Lexbench report evidence: sum((source['calls'] for source in candidate['sources'])) == 1116"
            )
        public_bytes = (clean(json.dumps(candidate, ensure_ascii=False, indent=2)) + "\n").encode()
        url = f"data/candidates/{method_id}-fixed-summary.json"
        public_candidate_files[url] = public_bytes
        evidence = {"url": url, "sha256": sha(public_bytes), "bytes": len(public_bytes)}
        fixed_candidates[method_id] = {
            "population": candidate["population"],
            "outcomes": candidate["outcomes"],
            "binary": candidate["binary"],
            "sources": candidate["sources"],
            "evidence": evidence,
            "source_summary_sha256": sha(source_bytes),
        }
        functional_methods.append(
            {
                "id": method_id,
                "label": label_zh,
                "label_en": label_en,
                "policy": f"fixed_{layout}",
                "direct_cases": 372,
                "inherited_cases": 0,
                "final_passed_cases": passed,
                "physical_calls": 1116,
                "moli_sha256": candidate["binary"]["moli_sha256"],
                "evidence": evidence,
            }
        )
    macos_resource = None
    official_resource = None
    for resource_path, is_official in [
        (args.macos_resource_summary, False),
        (args.official_resource_summary, True),
    ]:
        if resource_path is None:
            continue
        resource = json.loads(resource_path.read_text())
        if not resource["schema"] == "lexbench_moli_macos_fixed_resource_summary/1":
            raise ValueError(
                "Invalid Lexbench report evidence: resource['schema'] == 'lexbench_moli_macos_fixed_resource_summary/1'"
            )
        expected_layouts = [
            f"layout_{layout}"
            for layout in ("off", "on")
            if (f"119_official_{layout}" if is_official else f"119_{layout}") in fixed_candidates
        ]
        if not (
            resource["method"]["configurations"] == expected_layouts
            and resource["method"]["layout_retry"] is False
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: resource['method']['configurations'] == expected_layouts and resource['method']['layout_retry'] is False"
            )
        if not resource["method"]["resource_runs"] == len(expected_layouts):
            raise ValueError(
                "Invalid Lexbench report evidence: resource['method']['resource_runs'] == len(expected_layouts)"
            )
        if not resource["population"] == {
            "tasks": 557,
            "attempts_per_task": 5,
            "calls_per_configuration": 2785,
            "frozen_comparison_calls": 1045,
            "frozen_comparison_tasks": 209,
        }:
            raise ValueError(
                "Invalid Lexbench report evidence: resource['population'] == {'tasks': 557, 'attempts_per_task': 5, 'calls_per_configuration': 2785, 'frozen_comparison_calls': 1045, 'frozen_comparison_tasks': 209}"
            )
        if not (
            resource["quality"]["memory_metric"] == "process_tree_rss"
            and resource["quality"]["pss_available"] is False
            and (resource["quality"]["publishable"] is True)
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: resource['quality']['memory_metric'] == 'process_tree_rss' and resource['quality']['pss_available'] is False and (resource['quality']['publishable'] is True)"
            )
        for layout, config in resource["configurations"].items():
            method_id = f"119_official_{layout}" if is_official else f"119_{layout}"
            if not resource["candidate"]["binary_sha256"] == resource_builds[method_id]["sha256"]:
                raise ValueError(
                    "Invalid Lexbench report evidence: resource['candidate']['binary_sha256'] == resource_builds[method_id]['sha256']"
                )
            for population_name, n in [
                ("all_predeclared_calls", 2785),
                ("frozen_historical_common_pass_calls", 1045),
            ]:
                for metric in ("rss_peak_mib", "cpu_time_ms"):
                    stat = config["metrics"][population_name][metric]
                    if not (
                        stat["n"] == n
                        and abs(stat["sum"] - stat["mean"] * n)
                        < max(1e-06, abs(stat["sum"]) * 1e-10)
                    ):
                        raise ValueError(
                            "Invalid Lexbench report evidence: stat['n'] == n and abs(stat['sum'] - stat['mean'] * n) < max(1e-06, abs(stat['sum']) * 1e-10)"
                        )
        public_bytes = (clean(json.dumps(resource, ensure_ascii=False, indent=2)) + "\n").encode()
        url = (
            "data/candidates/"
            + ("official" if is_official else "patched")
            + "-119-macos-resource-summary.json"
        )
        public_candidate_files[url] = public_bytes
        entry = {
            "summary": json.loads(public_bytes),
            "evidence": {"url": url, "sha256": sha(public_bytes), "bytes": len(public_bytes)},
        }
        if is_official:
            official_resource = entry
        else:
            macos_resource = entry
    resource_matrix = None
    if macos_resource or official_resource:
        if not args.resource_matrix is not None:
            raise ValueError("a complete matched resource receipt is required")
        bundle = load_matrix(
            args.resource_matrix,
            resource_builds,
            {
                "patched": macos_resource["summary"],
                "official": official_resource["summary"],
            },
        )
        public_bytes = (clean(json.dumps(bundle, ensure_ascii=False, indent=2)) + "\n").encode()
        url = "data/candidates/resource-matrix.json"
        public_candidate_files[url] = public_bytes
        resource_matrix = {
            "evidence": {"url": url, "sha256": sha(public_bytes), "bytes": len(public_bytes)},
            "receipt": bundle["receipt"],
        }
        public_candidate_files["reproduction/run_resource_matrix.py"] = (
            args.resource_matrix / "orchestrator.py"
        ).read_bytes()
    functional_correction = json.loads(
        contained_path(FUNCTIONAL_CORRECTION, "receipt.json", reject_symlinks=True).read_text()
    )
    if not functional_correction["schema"] == "moli_functional_contract_correction/1":
        raise ValueError(
            "Invalid Lexbench report evidence: functional_correction['schema'] == 'moli_functional_contract_correction/1'"
        )
    if not functional_correction["effect"]["report_success_rates_changed"] is False:
        raise ValueError(
            "Invalid Lexbench report evidence: functional_correction['effect']['report_success_rates_changed'] is False"
        )
    if not [row["id"] for row in functional_correction["configurations"]] == [
        "114_off",
        "118_off",
        "118_on",
    ]:
        raise ValueError(
            "Invalid Lexbench report evidence: [row['id'] for row in functional_correction['configurations']] == ['114_off', '118_off', '118_on']"
        )
    for item in functional_correction["configurations"]:
        correction_run = contained_path(FUNCTIONAL_CORRECTION, item["run_id"], reject_symlinks=True)
        if (
            not sha(
                contained_path(correction_run, "results.jsonl", reject_symlinks=True).read_bytes()
            )
            == item["results_sha256"]
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: sha((correction_run / 'results.jsonl').read_bytes()) == item['results_sha256']"
            )
        if (
            not sha(
                contained_path(
                    correction_run, "run_manifest.json", reject_symlinks=True
                ).read_bytes()
            )
            == item["manifest_sha256"]
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: sha((correction_run / 'run_manifest.json').read_bytes()) == item['manifest_sha256']"
            )
        if not item["rows"] == 6:
            raise ValueError("Invalid Lexbench report evidence: item['rows'] == 6")
        if not all(
            (status == "fail" for task in item["outcomes"].values() for status in task["attempts"])
        ):
            raise ValueError(
                "Invalid Lexbench report evidence: all((status == 'fail' for task in item['outcomes'].values() for status in task['attempts']))"
            )
    correction_bytes = (
        json.dumps(functional_correction, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    functional_correction_public = {
        "task_count": len(functional_correction["tasks"]),
        "configuration_count": len(functional_correction["configurations"]),
        "calls": sum(row["rows"] for row in functional_correction["configurations"]),
        "harness_commit": functional_correction["harness_commit"],
        "success_rates_changed": False,
        "evidence": {
            "url": "data/corrections/chromium-contract-retest.json",
            "sha256": sha(correction_bytes),
            "bytes": len(correction_bytes),
        },
    }
    macos_comparison = None
    if macos_resource:
        mac_rows = []
        for method_id, resource, layout in [
            ("119_official_off", official_resource, "off"),
            ("119_official_on", official_resource, "on"),
            ("119_off", macos_resource, "off"),
            ("119_on", macos_resource, "on"),
        ]:
            if resource is None or method_id not in fixed_candidates:
                continue
            metric = resource["summary"]["configurations"][layout]["metrics"][
                "frozen_historical_common_pass_calls"
            ]
            method = next(row for row in functional_methods if row["id"] == method_id)
            mac_rows.append(
                {
                    "id": method_id,
                    "label": method["label"],
                    "label_en": method["label_en"],
                    "short_label": method["label"].replace("Moli ", ""),
                    "short_label_en": method["label_en"].replace("Moli ", ""),
                    "functional_binary_sha256": method["moli_sha256"],
                    "resource_binary_sha256": resource["summary"]["candidate"]["binary_sha256"],
                    "passed_cases": method["final_passed_cases"],
                    "total_cases": 372,
                    "success_rate_pct": method["final_passed_cases"] / 372 * 100,
                    "rss_mib": metric["rss_peak_mib"]["p50"],
                    "cpu_ms": metric["cpu_time_ms"]["mean"],
                }
            )
        mac_baseline = next(
            (row for row in mac_rows if row["id"] == "119_official_on"), mac_rows[0]
        )
        for row in mac_rows:
            row["rss_change_pct"] = (row["rss_mib"] / mac_baseline["rss_mib"] - 1) * 100
            row["cpu_change_pct"] = (row["cpu_ms"] / mac_baseline["cpu_ms"] - 1) * 100
        macos_comparison = {
            "baseline": mac_baseline["id"],
            "configurations": mac_rows,
            "resource_population": dict(chart_population),
        }
    linux_resource_profiles = None
    linux_comparison = None
    resource_metrics = None
    resource_metrics_source = None
    if args.linux_resource_profiles:
        if not (not macos_resource and (not official_resource)):
            raise ValueError("use one current resource cohort")
        linux_summary = summarize_linux_profiles(args.linux_resource_profiles)
        validate_linux_summary(linux_summary, resource_builds)
        public_bytes = (
            clean(json.dumps(linux_summary, ensure_ascii=False, indent=2)) + "\n"
        ).encode()
        url = "data/candidates/linux-resource-profiles.json"
        public_candidate_files[url] = public_bytes
        linux_public_summary = json.loads(public_bytes)
        linux_resource_profiles = {
            "configurations": linux_public_summary["configurations"],
            "receipt": linux_public_summary["receipt"],
            "evidence": {"url": url, "sha256": sha(public_bytes), "bytes": len(public_bytes)},
        }
        public_candidate_files["reproduction/run_linux_resource_profiles.py"] = (
            args.linux_resource_profiles / "orchestrator.py"
        ).read_bytes()
        original_bytes = (clean(json.dumps(resource_comparison, indent=2)) + "\n").encode()
        original_url = "data/candidates/version-resource-measurements.json"
        public_candidate_files[original_url] = original_bytes
        resource_metrics_source = {
            "url": original_url,
            "sha256": sha(original_bytes),
            "bytes": len(original_bytes),
        }
        resource_metrics = {}
        for method in functional_methods:
            method_id = method["id"]
            if method_id in linux_summary["configurations"]:
                resource_metrics[method_id] = linux_summary["configurations"][method_id]["metrics"]
            else:
                resource_metrics[method_id] = {
                    scope: {
                        metric: {**stat, "p50": stat["median"], "sum": stat["mean"] * stat["n"]}
                        for metric, stat in resource_comparison["memory"][method_id][scope].items()
                        if metric in {"pss_peak_mib", "cpu_time_ms"}
                    }
                    for scope in ("all_predeclared_calls", "frozen_historical_common_pass_calls")
                }
        linux_rows = []
        for method in functional_methods:
            method_id = method["id"]
            if method_id not in linux_summary["configurations"]:
                continue
            config = linux_summary["configurations"][method_id]
            metric = config["metrics"]["frozen_historical_common_pass_calls"]
            linux_rows.append(
                {
                    "id": method_id,
                    "label": method["label"],
                    "label_en": method["label_en"],
                    "short_label": method["label"].removeprefix("Moli "),
                    "short_label_en": method["label_en"].removeprefix("Moli "),
                    "functional_binary_sha256": method["moli_sha256"],
                    "resource_binary_sha256": config["build"]["sha256"],
                    "passed_cases": method["final_passed_cases"],
                    "total_cases": 372,
                    "success_rate_pct": method["final_passed_cases"] / 372 * 100,
                    "pss_mib": metric["pss_peak_mib"]["p50"],
                    "cpu_ms": metric["cpu_time_ms"]["mean"],
                    "platform": "Linux",
                    "memory_kind": "PSS",
                }
            )
        linux_comparison = {"configurations": linux_rows}
    combined_comparison = combine_comparisons(
        functional_methods, version_comparison, linux_comparison or macos_comparison
    )
    public = {
        "linux_resource_profiles": linux_resource_profiles,
        "linux_comparison": linux_comparison,
        "resource_metrics": resource_metrics,
        "resource_metrics_source": resource_metrics_source,
        "combined_comparison": combined_comparison,
        "fixed_case_outcomes": [
            {
                "id": c["id"],
                "passes": {method: value["passes"] for method, value in c["methods"].items()},
            }
            for c in fixed_case_data.values()
        ],
        "macos_comparison": macos_comparison,
        "version_comparison": version_comparison,
        "input_definition_variants": {
            cid: {"baseline_sha256": selected[cid][1], "comparison_sha256": item["task_sha256"]}
            for cid, item in fixed_case_data.items()
            if item.get("task_sha256") and item["task_sha256"] != selected[cid][1]
        },
        "functional_methods": functional_methods,
        "fixed_candidates": fixed_candidates,
        "macos_resource": macos_resource,
        "official_resource": official_resource,
        "resource_builds": resource_builds,
        "resource_matrix": resource_matrix,
        "case_outcomes": case_outcomes,
        "schema": "moli_layout_report/2",
        "functional_contract_correction": functional_correction_public,
        "resource_window_corrections": corrections,
        "environment": json.loads(
            contained_path(RUNS, "environment.json", reject_symlinks=True).read_text()
        ),
        "title": "Moli 配置对比",
        "task_count": task_count,
        "attempts_per_mode": call_count,
        "binary_sha256": cohort["moli_sha256"],
        "manifest_sha256": cohort["manifest_sha256"],
        "host_context": host_context,
        "selection": cohort["selection"],
        "protocol": protocol,
        "strata": list(cohort["strata"]),
        "metrics": metrics,
        "network_rx": network_rx,
        "cold_start": cold_start,
        "common_pass": common_pass,
        "task_cost": task_cost,
        "paired": paired,
        "quality": quality,
        "review_coverage": {
            "reviewed_calls": 0,
            "total_calls": call_count * 4,
            "unreviewed_program_passes": sum(
                metrics[mode][phase]["passes"]
                for mode in ("off", "on")
                for phase in ("baseline", "profile")
            ),
        },
        "cases": [
            {
                "id": c["id"],
                "title": c["title"],
                "stratum": c["stratum"],
                "subset": c["subset"],
                "off_passes": c["modes"]["off"]["passes"],
                "on_passes": c["modes"]["on"]["passes"],
                "off_fail_classes": sorted(
                    set(
                        a["failure"]["class"]
                        for a in c["modes"]["off"]["attempts"]
                        if a["status"] != "pass"
                    )
                ),
                "on_fail_classes": sorted(
                    set(
                        a["failure"]["class"]
                        for a in c["modes"]["on"]["attempts"]
                        if a["status"] != "pass"
                    )
                ),
                "off_fail_details": sorted(
                    set(
                        a["failure"]["detail"]
                        for a in c["modes"]["off"]["attempts"]
                        if a["status"] != "pass"
                    )
                ),
                "on_fail_details": sorted(
                    set(
                        a["failure"]["detail"]
                        for a in c["modes"]["on"]["attempts"]
                        if a["status"] != "pass"
                    )
                ),
            }
            for c in cases
        ],
        "runs": {
            name: {
                "run_id": label[name],
                "manifest_sha256": sha((RUNS / label[name] / "run_manifest.json").read_bytes()),
                "rows": call_count,
            }
            for name in label
        },
    }
    (OUT / "data/model.json").write_text(
        json.dumps(public, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    (OUT / "data/corrections").mkdir()
    (OUT / "data/corrections/chromium-contract-retest.json").write_bytes(correction_bytes)
    if fixed_candidates:
        (OUT / "data/fixed-cases").mkdir()
        for task_id, case in fixed_case_data.items():
            (OUT / "data/fixed-cases" / f"{task_id}.json").write_text(
                json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    for relative, data in public_candidate_files.items():
        target = contained_path(OUT, relative, reject_symlinks=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (OUT / "data/version-comparison.json").write_text(
        json.dumps(version_comparison, ensure_ascii=False, indent=2) + "\n"
    )
    (OUT / "data/combined-comparison.json").write_text(
        json.dumps(combined_comparison, ensure_ascii=False, indent=2) + "\n"
    )
    if macos_comparison:
        (OUT / "data/macos-comparison.json").write_text(
            json.dumps(macos_comparison, ensure_ascii=False, indent=2) + "\n"
        )
    write_presentation(
        SRC, OUT, combined_comparison, version_comparison, macos_comparison,
        include_english=args.include_english,
    )
    for name in ("app.js", "style.css", "README.md"):
        shutil.copy2(SRC / name, OUT / name)
    for name, digest in {
        "mermaid.min.js": "07e37dfa97b337ccc85365d57eddf99b9706f09db3b59b260d0333b23b343c4b",
        "mermaid-LICENSE.txt": "ec9fb67dcb25eccc416ed56e1aab819222c805a2a4bfe4cb19e7556bf2ffde80",
    }.items():
        asset = contained_path(args.vendor_dir, name, reject_symlinks=True)
        if not (not asset.is_symlink() and sha(asset.read_bytes()) == digest):
            raise ValueError(
                "Invalid Lexbench report evidence: not asset.is_symlink() and sha(asset.read_bytes()) == digest"
            )
        shutil.copy2(asset, OUT / name)
    (OUT / "reproduction").mkdir(exist_ok=True)
    (OUT / "reproduction/cohort.json").write_text(clean(args.cohort.read_text()))
    shutil.copy2(pathlib.Path(reproduction.__file__), OUT / "reproduction/run.py")
    shutil.copy2(
        pathlib.Path(charts.__file__),
        OUT / "reproduction/plot_version_summary.py",
    )
    (OUT / "reproduction/protocol.json").write_text(json.dumps(contract, indent=2) + "\n")
    print(
        json.dumps(
            {
                "site": str(OUT),
                "cases": len(cases),
                "evidence_files": len(list((OUT / "data/evidence").rglob("*.*"))),
                "bytes": sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
