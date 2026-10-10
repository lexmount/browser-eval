"""Report checks are reusable without a checkout or private report inputs."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.lexbench.charts import combine_comparisons, render_chart
from harness.lexbench.verification import validate, validate_release

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/moli_report/verify.py"


def test_importing_command_does_not_parse_arguments_or_read_report(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy, sys; sys.argv = ['caller', '--unrelated']; "
            f"runpy.run_path({str(SCRIPT)!r}, run_name='imported_command')",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_release_profile_can_be_selected_explicitly(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--profile" in result.stdout


def save(site, relative, data):
    path = site / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = data if isinstance(data, bytes) else json.dumps(data).encode()
    path.write_bytes(payload)
    return {"url": relative, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


@pytest.fixture
def report(tmp_path):
    """Complete diagnostic population with partial, full and failed executions."""
    site = tmp_path / "report"
    site.mkdir()
    grader = save(site, "data/evidence/grader.json", {"synthetic": True})
    error = save(site, "data/evidence/stderr.log", b"synthetic protocol rejection\n")
    correction = save(
        site,
        "data/correction.json",
        {
            "schema": "moli_functional_contract_correction/1",
            "harness_commit": "fixture",
            "configurations": [
                {"id": key, "outcomes": {"one": {"attempts": ["fail"] * 3}}}
                for key in ("114_off", "118_off", "118_on")
            ],
        },
    )
    cases, costs, all_calls = [], [], {"off": [], "on": []}
    for index in range(372):
        task = {"id": f"case-{index}", "stratum": f"stratum-{index % 15}"}
        detail = {**task, "modes": {}}
        cost = {"id": task["id"]}
        for mode in ("off", "on"):
            attempts = []
            for ordinal in (1, 2, 3):
                passed = (mode == "on" and index < 255) or (
                    mode == "off" and (index == 0 or (index == 255 and ordinal == 1))
                )
                status = "pass" if passed else "fail"
                attempts.append(
                    {
                        "ordinal": ordinal,
                        "status": status,
                        "profile_status": status,
                        "duration_ms": 1,
                        "failure": {"class": "protocol", "detail": "synthetic rejection"},
                        "resource": {
                            "cpu_total_ms": 2,
                            "pss_end_bytes": 100,
                            "fixture_tx_bytes": 3,
                        },
                        "evidence": {"baseline": {"grader.json": grader, "stderr.log": error}},
                    }
                )
            passes = sum(a["status"] == "pass" for a in attempts)
            task[mode + "_passes"] = passes
            task[mode + "_fail_details"] = ["synthetic rejection"] if passes < 3 else []
            cost.update({mode + "_passes": passes, mode + "_duration_ms": 3, mode + "_cpu_ms": 6})
            detail["modes"][mode] = {"passes": passes, "attempts": attempts}
            all_calls[mode].extend(attempts)
        save(site, f"data/cases/{task['id']}.json", detail)
        cases.append(task)
        costs.append(cost)
    rows, methods = [], []
    for index, key in enumerate(("114_off", "118_off", "118_on")):
        passed = (0, 1, 255)[index]
        rows.append(
            {
                "id": key,
                "label": key,
                "label_en": key,
                "short_label": key,
                "short_label_en": key,
                "total_cases": 372,
                "passed_cases": passed,
                "success_rate_pct": passed / 372 * 100,
                "success_change_pp": passed / 372 * 100,
                "pss_mib": 1,
                "cpu_ms": 2,
                "pss_change_pct": 0,
                "cpu_change_pct": 0,
            }
        )
        methods.append(
            {
                "id": key,
                "label": key,
                "label_en": key,
                "final_passed_cases": passed,
                "policy": "fixed_on" if key.endswith("_on") else "fixed_off",
                "moli_sha256": "c" * 64,
                "direct_cases": 1 if index == 0 else 372,
                "inherited_cases": 371 if index == 0 else 0,
            }
        )
    comparison = {
        "baseline": "114_off",
        "configurations": rows,
        "functional_population": {
            "cases": 372,
            "attempts_per_case": 3,
            "rule": "all_three_attempts_pass",
            "version_118_direct_cases": 372,
            "version_114_candidate_cases": 1,
            "version_114_inherited_failures": 371,
            "version_114_candidate": "v4_cdp_emulation_setdefaultbackgroundcoloroverride",
            "version_114_method": "differential_candidate_check",
        },
        "resource_population": {
            "experiment_cases": 557,
            "attempts_per_case": 5,
            "total_tasks": 557,
            "calls_per_task": 5,
            "chart_tasks": 209,
            "chart_calls": 1045,
        },
    }
    combined = combine_comparisons(methods, comparison, None)
    save(site, "data/combined-comparison.json", combined)
    save(site, "data/version-comparison.json", comparison)
    for lang in ("zh", "en"):
        render_chart(
            combined,
            site / f"assets/moli-all-success-resources-{lang}.svg",
            ("memory", "cpu"),
            lang,
        )
        render_chart(
            comparison,
            site / f"assets/moli-version-success-resources-{lang}.svg",
            ("pss", "cpu"),
            lang,
        )
    save(site, "reproduction/plot_version_summary.py", b"# synthetic chart source\n")
    save(site, "index.html", b'<html lang="zh-CN"></html>')
    save(site, "en/index.html", b'<html lang="en"></html>')
    save(site, "README.md", b"Synthetic offline evidence")
    save(site, "app.js", (ROOT / "scripts/moli_report/app.js").read_bytes())
    save(site, "style.css", (ROOT / "scripts/moli_report/style.css").read_bytes())
    model = {
        "schema": "moli_layout_report/2",
        "task_count": 372,
        "attempts_per_mode": 1116,
        "protocol": {
            "k_runs": 3,
            "jobs": 1,
            "seed": 1,
            "score_eligible": False,
            "runner_sha256": "a" * 64,
            "fixtures_sha256": "b" * 64,
            "timing_primary": "unprofiled_driver_and_grade",
            "resource_scope": "observed_profiled_process_tree",
        },
        "cases": cases,
        "strata": [f"stratum-{i}" for i in range(15)],
        "case_outcomes": {
            "rule": "all_recorded_attempts_pass",
            "repeats": 3,
            "total": 372,
            "off": 1,
            "on": 255,
            "paired": {
                "both_pass": 1,
                "both_fail": 117,
                "off_fail_on_pass": 254,
                "off_pass_on_fail": 0,
            },
        },
        "version_comparison": comparison,
        "combined_comparison": combined,
        "functional_methods": methods,
        "functional_contract_correction": {
            "task_count": 2,
            "configuration_count": 3,
            "calls": 18,
            "success_rates_changed": False,
            "harness_commit": "fixture",
            "evidence": correction,
        },
        "task_cost": costs,
        "metrics": {},
        "paired": {
            "baseline": {
                "both_pass": 3,
                "both_fail": 350,
                "off_fail_on_pass": 762,
                "off_pass_on_fail": 1,
            }
        },
        "common_pass": {
            "count": 3,
            "cpu_total_ms": {"off": 6, "on": 6},
            "pss_end_mean_bytes": {"off": 100, "on": 100},
            "fixture_tx_total_bytes": {"off": 9, "on": 9},
        },
        "quality": {},
        "host_context": {"shared_macos_host": True},
    }
    for mode in ("off", "on"):
        model["metrics"][mode] = {
            "baseline": {
                "passes": sum(a["status"] == "pass" for a in all_calls[mode]),
                "duration_ms_all": {"sum": 1116},
            },
            "profile": {"cpu_ms_all": {"sum": 2232}, "fixture_tx_bytes_all": {"sum": 3348}},
        }
        model["quality"][mode] = {
            "resource_missing": 0,
            "status_mismatch_count": 0,
            "calibration": {
                "acceptable": True,
                "matched_attempts": 1116,
                "complete_pairing": True,
                "provenance_mismatches": [],
            },
        }
    save(site, "data/model.json", model)
    return site, model


def test_complete_synthetic_report_passes_real_evidence_validation(report):
    site, model = report
    assert validate(model, site)


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda m: m["cases"].pop(), "inventory"),
        (lambda m: m["metrics"]["on"]["baseline"].update(passes=0), "headline pass count"),
        (lambda m: m["common_pass"]["cpu_total_ms"].update(off=99), "common pass CPU"),
        (lambda m: m["quality"]["off"]["calibration"].update(acceptable=False), "calibration"),
        (
            lambda m: m["functional_contract_correction"].update(success_rates_changed=True),
            "correction summary",
        ),
    ],
)
def test_corrupted_report_is_rejected_through_real_validator(report, change, error):
    site, model = report
    change(model)
    with pytest.raises(ValueError, match=error):
        validate(model, site)


def test_complete_report_runs_all_evidence_negative_controls(report):
    from harness.lexbench.verification import verify_evidence

    site, _ = report
    summary = verify_evidence(site)
    assert summary == {
        "verified_cases": 372,
        "verified_calls": 4482,
        "verified_evidence_files": 2,
        "negative_controls": 19,
        "result": "pass",
    }


def test_release_binding_uses_selected_profile_registry():
    profile = {
        "resource_builds": {"engine-z": {"sha256": "a" * 64}},
        "method_ids": ["engine-z", "engine-a"],
        "resource_method_ids": ["engine-z"],
    }
    model = {
        "resource_builds": copy.deepcopy(profile["resource_builds"]),
        "functional_methods": [{"id": key} for key in profile["method_ids"]],
        "linux_comparison": {"configurations": [{"id": "engine-z"}]},
        "linux_resource_profiles": {"bound": True},
    }
    validate_release(model, profile)
    for change, error in [
        (lambda m: m["resource_builds"]["engine-z"].update(sha256="b" * 64), "binaries"),
        (lambda m: m["functional_methods"].reverse(), "functional method matrix"),
        (lambda m: m["functional_methods"].pop(), "functional method matrix"),
        (
            lambda m: m["linux_comparison"]["configurations"].append({"id": "extra"}),
            "Linux resource",
        ),
        (lambda m: m.pop("linux_resource_profiles"), "Linux resource"),
    ]:
        altered = copy.deepcopy(model)
        change(altered)
        with pytest.raises(ValueError, match=error):
            validate_release(altered, profile)


def test_profile_option_without_release_is_rejected_before_reading_site(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--site",
            str(tmp_path / "absent"),
            "--profile",
            str(tmp_path / "unread.json"),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--profile requires --release" in result.stderr
    assert "FileNotFoundError" not in result.stderr


def test_library_import_and_help_do_not_touch_private_report_inputs(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "from harness.lexbench.verification import main; main(['--help'])",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("optimized", [False, True])
def test_schema_rejection_remains_active_under_optimization(tmp_path, optimized):
    command = [sys.executable] + (["-O"] if optimized else [])
    result = subprocess.run(
        command
        + [
            "-c",
            f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
            "from harness.lexbench.verification import validate; "
            f"from pathlib import Path; validate({{}}, Path({str(tmp_path)!r}))",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ValueError: schema" in result.stderr


@pytest.mark.parametrize("entry", ["validate", "verify_evidence"])
def test_linked_report_root_is_rejected(report, tmp_path, entry):
    from harness.lexbench import verification

    site, model = report
    linked = tmp_path / "linked-report"
    linked.symlink_to(site, target_is_directory=True)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        if entry == "validate":
            verification.validate(model, linked)
        else:
            verification.verify_evidence(linked)


def test_linked_case_directory_is_rejected(report, tmp_path):
    site, model = report
    original = site / "data/cases"
    external = tmp_path / "external-cases"
    original.rename(external)
    original.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        validate(model, site)


@pytest.mark.parametrize("relative", [False, True])
def test_declared_artifact_paths_must_be_relative_without_parent_segments(report, relative):
    site, model = report
    target = site / model["functional_contract_correction"]["evidence"]["url"]
    model["functional_contract_correction"]["evidence"]["url"] = (
        f"../{site.name}/data/correction.json" if relative else str(target)
    )
    with pytest.raises(ValueError, match="relative"):
        validate(model, site)


def test_invalid_case_identifier_is_rejected_before_reading_cases(report, monkeypatch):
    site, model = report
    model["cases"][0]["id"] = "../outside"
    original = Path.read_text

    def forbidden_case_read(path, *args, **kwargs):
        if "cases" in path.parts:
            raise RuntimeError("Opened a task file before validating its identifier")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbidden_case_read)
    with pytest.raises(ValueError, match="Identifiers"):
        validate(model, site)


def test_three_configuration_report_passes_evidence_and_render_release_checks(report):
    from harness.lexbench.verification import verify

    site, _ = report
    summary = verify(site)
    assert summary["verified_cases"] == 372
    assert summary["verified_calls"] == 4482
    assert summary["negative_controls"] == 19
    assert summary["result"] == "pass"


def chinese_only_report(report):
    from harness.report_languages import write_language_contract

    site, model = report
    (site / "en/index.html").unlink()
    for path in (site / "assets").glob("*-en.svg"):
        path.unlink()
    write_language_contract(site, include_english=False)
    (site / "en.html").write_text(
        (site / "en/index.html").read_text().replace('href="../index.html"', 'href="index.html"')
    )
    return site, model


def test_chinese_only_report_passes_release_checks_without_english_charts(report):
    from harness.lexbench.verification import verify

    site, _ = chinese_only_report(report)
    summary = verify(site)
    assert summary["result"] == "pass"
    assert not list((site / "assets").glob("*-en.svg"))


@pytest.mark.parametrize("stale", ["en/guide.html", "assets/moli-all-success-resources-en.svg"])
def test_chinese_only_report_rejects_stale_english(report, stale):
    site, model = chinese_only_report(report)
    save(site, stale, b"stale English output")
    with pytest.raises(ValueError, match="[Ee]nglish"):
        validate(model, site)


@pytest.mark.parametrize("include_english", [False, True])
def test_requested_editions_are_generated_and_verified_end_to_end(report, include_english):
    import shutil

    from harness.lexbench.report import write_presentation
    from harness.lexbench.verification import verify
    from harness.report_languages import generated_languages

    site, model = report
    shutil.rmtree(site / "en")
    shutil.rmtree(site / "assets")
    options = {"include_english": True} if include_english else {}
    write_presentation(
        ROOT / "scripts/moli_report", site,
        model["combined_comparison"], model["version_comparison"], **options,
    )
    assert generated_languages(site) == (("zh", "en") if include_english else ("zh",))
    assert len(list((site / "assets").glob("*.svg"))) == (4 if include_english else 2)
    assert 'lang="zh-CN"' in (site / "index.html").read_text()
    for entry in ("en.html", "en/index.html"):
        html = (site / entry).read_text()
        assert ('lang="en"' in html) is include_english
        assert ("data-english-not-generated" in html) is not include_english
    assert verify(site)["result"] == "pass"


def test_build_command_requires_explicit_option_for_english():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/moli_report/build.py"), "--help"],
        check=True, capture_output=True, text=True,
    )
    assert "--include-english" in result.stdout
