"""Every track gate opens its report through the shared flow, and the flow fails closed.

Gate-level controls below reach the shared page and language rules wherever a synthetic
report can get that far. No fixture here renders the complete 200-case report whose home
and guide the sample WebMainBench gate re-reads last, or a complete certified WCXB report.
"""

import hashlib
import json

import pytest

from harness.browser_use import report_check as browser_use
from harness.record_io import file_sha256
from harness.report_languages import write_language_contract
from harness.reporting.release_check import (
    check_report_identity,
    checked_page,
    language_editions,
    open_report,
    read_page,
)
from harness.reporting.templates import template_identity
from harness.reporting.versions import FILENAME
from harness.trajectory_replay import webarena_report_check as trajectory
from harness.web_fetch import (
    published_snapshot,
    report_check,
    report_diagnostic,
    wcxb_report_check,
    webmainbench_report_check,
)
from harness.web_fetch.report_build import build_diagnostic_cohorts
from tests.browser_use.test_report import build_browser_use_fixture
from tests.browser_use.test_report_check import SELECTION, authority
from tests.test_published_snapshot_report import example as snapshot_example
from tests.test_report_templates import diagnostic_example
from tests.test_webmainbench_full_report import render_full_report
from tests.trajectory_replay.test_webarena_report import build_replay_fixture

GATES = {
    "webfetch": lambda root: report_check.check(root, authority_root=root),
    "webfetch-revision": lambda root: report_check.check_revision(root, authority_root=root),
    "webfetch-diagnostic": report_diagnostic.check_diagnostic_report,
    "webfetch-snapshot": published_snapshot.check_snapshot_audit,
    "wcxb": lambda root: wcxb_report_check.check(root, authority_root=root),
    "webmainbench": lambda root: webmainbench_report_check.check(root, authority_root=root),
    "webmainbench-sample": lambda root: webmainbench_report_check.check_sample_report(
        root, authority_root=root
    ),
    "trajectory-replay": trajectory.check,
    "browser-use": lambda root: browser_use.check(
        root, root, None, require_complete=False, authority_root=root
    ),
}
# Directional highlight on the smaller of two values, so only the shared rule can reject it.
WRONG_HIGHLIGHT = """<table><tr>
<td data-metric="pass" data-metric-direction="max" data-sort="1"><span class="comparison-winner">1</span></td>
<td data-metric="pass" data-metric-direction="max" data-sort="2">2</td></tr></table>"""


@pytest.mark.parametrize("gate", GATES.values(), ids=GATES)
def test_every_gate_refuses_a_linked_report_before_reading_it(tmp_path, gate):
    (tmp_path / "linked").symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="symlink"):
        gate(tmp_path)


@pytest.mark.parametrize("gate", GATES.values(), ids=GATES)
def test_every_gate_goes_on_to_its_own_rules_without_a_link(tmp_path, gate):
    with pytest.raises((OSError, ValueError, KeyError)) as failure:
        gate(tmp_path)
    assert "symlink" not in str(failure.value)


def test_open_report_returns_the_resolved_root(tmp_path, monkeypatch):
    (tmp_path / "report").mkdir()
    monkeypatch.chdir(tmp_path)
    assert open_report("report") == (tmp_path / "report").resolve()


def test_checked_page_applies_the_shared_presentation_rules(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(WRONG_HIGHLIGHT, encoding="utf-8")
    assert read_page(page).select_one(".comparison-winner")
    with pytest.raises(ValueError, match="highlight"):
        checked_page(page)
    page.write_text(WRONG_HIGHLIGHT.replace('data-sort="2"', 'data-sort="0"'), encoding="utf-8")
    assert checked_page(page).select_one(".comparison-winner")


@pytest.mark.parametrize(
    ("english", "expected"),
    [(False, (("zh", ""),)), (True, (("zh", ""), ("en", "en/")))],
)
def test_language_editions_follow_the_language_contract(tmp_path, english, expected):
    write_language_contract(tmp_path, include_english=english)
    assert language_editions(tmp_path) == expected


def _bound_manifest(profile):
    profile.write_text("{}", encoding="utf-8")
    return {"profile_sha256": file_sha256(profile), "template": template_identity("browser-use")}


def test_report_identity_accepts_the_bound_profile(tmp_path):
    profile = tmp_path / "profile.json"
    check_report_identity(tmp_path, _bound_manifest(profile), "browser-use", profile)


@pytest.mark.parametrize("defect", ["changed", "unbound", "linked"])
def test_report_identity_rejects_a_different_profile(tmp_path, defect):
    profile = tmp_path / "profile.json"
    manifest = _bound_manifest(profile)
    if defect == "changed":
        profile.write_text('{"changed": true}', encoding="utf-8")
    elif defect == "unbound":
        del manifest["profile_sha256"]
    else:
        target = tmp_path / "target.json"
        profile.rename(target)
        profile.symlink_to(target)
    with pytest.raises(ValueError, match="different instance profile"):
        check_report_identity(tmp_path, manifest, "browser-use", profile)


def test_only_a_declared_legacy_track_accepts_an_unbound_report(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text("{}", encoding="utf-8")
    check_report_identity(tmp_path, {}, "browser-use", profile, allow_unbound_profile=True)
    with pytest.raises(ValueError, match="different instance profile"):
        check_report_identity(tmp_path, {"profile_sha256": "0" * 64, "template": template_identity("browser-use")},
                              "browser-use", profile, allow_unbound_profile=True)


def test_report_identity_still_binds_the_template_and_the_build(tmp_path):
    profile = tmp_path / "profile.json"
    bypassed = {**_bound_manifest(profile), "template": {"track": "other", "renderer": "other.py"}}
    with pytest.raises(ValueError, match="bypasses the maintained"):
        check_report_identity(tmp_path, bypassed, "browser-use")
    (tmp_path / FILENAME).write_text(json.dumps({"role": "report_builder"}), encoding="utf-8")
    with pytest.raises(ValueError, match="version binding"):
        check_report_identity(tmp_path, {}, "browser-use")


def _inject_wrong_highlight(page):
    original = page.read_text(encoding="utf-8")
    assert "</main>" in original
    page.write_text(original.replace("</main>", WRONG_HIGHLIGHT + "</main>", 1), encoding="utf-8")


@pytest.mark.parametrize("route", ["index.html", "guide/index.html", "analysis/index.html", "audit/index.html"])
def test_snapshot_gate_applies_presentation_rules_to_its_core_routes(tmp_path, route):
    source, profile = snapshot_example(tmp_path)
    report = tmp_path / "report"
    published_snapshot.build_snapshot_audit(source, report, profile=profile)
    published_snapshot.check_snapshot_audit(report)
    _inject_wrong_highlight(report / route)
    with pytest.raises(ValueError, match="highlight"):
        published_snapshot.check_snapshot_audit(report)


@pytest.fixture
def diagnostic_report(tmp_path):
    source = tmp_path / "source"
    model = diagnostic_example(source)
    report = tmp_path / "report"
    build_diagnostic_cohorts(source, report)
    report_diagnostic.check_diagnostic_report(report)
    return report, model


@pytest.mark.parametrize("route", [
    "index.html", "datasets/index.html", "methodology/index.html", "analysis/index.html",
    "review/index.html", "guide/index.html", "reproduce/index.html", "en/index.html",
])
def test_diagnostic_gate_applies_presentation_rules_to_its_core_routes(diagnostic_report, route):
    report, _ = diagnostic_report
    _inject_wrong_highlight(report / route)
    with pytest.raises(ValueError, match="highlight"):
        report_diagnostic.check_diagnostic_report(report)


def test_diagnostic_gate_applies_presentation_rules_to_each_result(diagnostic_report):
    report, model = diagnostic_report
    _inject_wrong_highlight(report / "attempts" / (model["rows"][0]["id"] + ".html"))
    with pytest.raises(ValueError, match="highlight"):
        report_diagnostic.check_diagnostic_report(report)


def test_full_webmainbench_table_gate_applies_presentation_rules(tmp_path):
    providers, _ = render_full_report(tmp_path, "method-binding.json")
    webmainbench_report_check.check_table_interaction(tmp_path, provider_count=len(providers), case_count=1)
    _inject_wrong_highlight(tmp_path / "index.html")
    with pytest.raises(ValueError, match="highlight"):
        webmainbench_report_check.check_table_interaction(tmp_path, provider_count=len(providers), case_count=1)


FULL_SCORING_RECEIPT = {"scorer_commit": "b" * 40, "regression_registry_sha256": "c" * 64,
                        "input_results_sha256": "d" * 64, "output_results_sha256": "e" * 64,
                        "provider_calls": 0}


def _full_webmainbench_authority(root, providers, rows):
    """Bind one rendered synthetic report the way a certified authority bundle does."""
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    repo = root / "authority"
    full = repo / "evaluation/webmainbench-full"
    variants = [{"provider": name, "profile": name + "-report.json"} for name in providers[5:]]
    variant_hashes = {item["provider"]: digest(save(full / item["profile"], {})) for item in variants}
    profile = save(full / "current-report.json", {"providers": providers[:5], "case_count": 1})
    registry = save(full / "moli-variants.json", {"variants": variants})
    save(full / "publication-target.json", {
        "profile_sha256": digest(profile), "variant_profile_sha256": variant_hashes,
        "expected_remote_version": "ver_fixture", "host": "https://sites.invalid",
        "share_url": "https://sites.invalid/v/fixture", "site_slug": "fixture",
    })
    scorer = FULL_SCORING_RECEIPT
    receipt = root / "report/scoring-correction.json"
    save(root / "report/method-binding.json", {
        "schema_version": 2, "kind": "webmainbench_method_cohort_binding",
        "logical_results": len(providers), "registry_sha256": digest(registry),
        "results_sha256": scorer["input_results_sha256"],
        "variants": [{"provider": name, "profile_sha256": value} for name, value in variant_hashes.items()],
    })
    save(full / "scoring-correction-binding.json", {
        "kind": "webmainbench_shared_population_scoring_correction_binding",
        "receipt_sha256": digest(receipt), "scorer_commit": scorer["scorer_commit"],
        "regression_registry_sha256": scorer["regression_registry_sha256"],
        "results_sha256": scorer["output_results_sha256"],
    })
    report = root / "report"
    rendered_history = read_page(report / "history/index.html").select("tr[data-release-id]")
    save(repo / "research/report-changes.json", {"releases": [
        {"id": row["data-release-id"], "title": "", "summary": "",
         "comparison_kinds": ["webmainbench_frozen_html_to_markdown"]}
        for row in rendered_history
    ]})
    save(full / "content-binding.json", {"sha256": digest(report / "report-content.json")})
    save(full / "failure-analysis-binding.json", {"sha256": digest(report / "failure-analysis.json")})
    save(report / "report-manifest.json", {
        "schema_version": 1, "report": "webmainbench-545-full", "languages": ["zh"],
        "case_count": 1, "provider_count": len(providers), "result_count": len(providers),
    })
    save(report / "dataset.json", {"coverage_plan": {"static_html": providers}, "cases": [{"id": "case-1"}]})
    (report / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return repo


def _full_webmainbench_populations(report, repo, providers):
    """Give the one-case fixture the fixed populations the gate requires before its result template."""
    denominators = {"text_edit": 545, "code_edit": 110, "formula_edit": 257, "table_edit": 179, "table_TEDS": 179}
    (report / "score-summaries.json").write_text(json.dumps({
        provider: {"official_aggregate": {"denominators": denominators, "scores": {"overall": 0.5}}}
        for provider in providers
    }), encoding="utf-8")
    picture = report / "assets/overview.png"
    picture.write_bytes(b"fixture image")
    (repo / "evaluation/webmainbench-full/illustrations.json").write_text(json.dumps({"assets": [{
        "name": picture.name, "sha256": hashlib.sha256(picture.read_bytes()).hexdigest(), "role": "overview",
        "claims": {"scores_percent": dict.fromkeys(providers, "50.00")},
    }]}), encoding="utf-8")
    home, guide = report / "index.html", report / "guide/index.html"
    home.write_text(home.read_text(encoding="utf-8").replace("</main>", "<p>545</p></main>", 1), encoding="utf-8")
    page = guide.read_text(encoding="utf-8").replace("</main>", '<img src="assets/overview.png"></main>', 1)
    guide.write_text(page.replace('<section id="content-audit">',
                                  '<section id="content-audit"><p>545/545 604 修复前候选输出</p>', 1),
                     encoding="utf-8")


@pytest.mark.parametrize("route", ["index.html", "guide/index.html", "results/index.html"])
def test_full_webmainbench_gate_applies_presentation_rules_to_its_pages(tmp_path, route):
    report = tmp_path / "report"
    report.mkdir()
    (report / "scoring-correction.json").write_text(json.dumps(FULL_SCORING_RECEIPT), encoding="utf-8")
    providers, rows = render_full_report(report, "method-binding.json")
    repo = _full_webmainbench_authority(tmp_path, providers, rows)
    if route == "results/index.html":
        _full_webmainbench_populations(report, repo, providers)
    _inject_wrong_highlight(report / route)
    with pytest.raises(ValueError, match="highlight"):
        webmainbench_report_check.check(report, authority_root=repo)


def test_public_evidence_gate_requires_every_generated_edition(tmp_path, monkeypatch):
    attempt = "chromium__case__r001"
    views = {"scored_answer": {"original_sha256": "a" * 64, "published_sha256": "a" * 64}}
    monkeypatch.setattr(report_check, "public_render_views", lambda *_: {attempt: views})
    page = tmp_path / "attempts" / (attempt + ".html")
    page.parent.mkdir()
    page.write_text("<main>Result</main>", encoding="utf-8")
    records = [{"attempt_id": attempt}]
    write_language_contract(tmp_path, include_english=False)
    report_check.check_public_evidence_pages(tmp_path, records)
    write_language_contract(tmp_path, include_english=True)
    with pytest.raises(ValueError, match="result page is missing"):
        report_check.check_public_evidence_pages(tmp_path, records)


def _sample_webmainbench_with_history(root):
    """Stop the sample gate at its per-edition release pages, the first read of every edition."""
    repo, report = root / "authority", root / "report"
    (repo / "evaluation").mkdir(parents=True)
    (repo / "research").mkdir()
    (repo / "evaluation/current-report.json").write_text("{}", encoding="utf-8")
    release = {
        "id": "fixture", "date": "2026-01-01", "title": "合成变更", "summary": "合成说明", "changes": [],
        "comparison_kinds": ["webmainbench_frozen_html_to_markdown"],
        "i18n": {"en": {"title": "Fixture", "summary": "Fixture", "changes": []}},
    }
    history = json.dumps({"releases": [release]})
    (repo / "research/report-changes.json").write_text(history, encoding="utf-8")
    page = report / "changes/index.html"
    page.parent.mkdir(parents=True)
    page.write_text(
        '<div class="hero">合成说明</div><section id="current-release">'
        '<div data-release-id="fixture">合成变更</div></section>', encoding="utf-8")
    (report / "report-changes.json").write_text(history, encoding="utf-8")
    (report / "dataset.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
    (report / "results.jsonl").write_text("", encoding="utf-8")
    return report, repo


def test_sample_webmainbench_gate_reads_every_generated_edition(tmp_path):
    report, repo = _sample_webmainbench_with_history(tmp_path)
    write_language_contract(report, include_english=False)
    with pytest.raises(ValueError, match="Not the current WebMainBench"):
        webmainbench_report_check.check_sample_report(report, authority_root=repo)
    write_language_contract(report, include_english=True)
    with pytest.raises(FileNotFoundError, match="en/changes/index.html"):
        webmainbench_report_check.check_sample_report(report, authority_root=repo)


def test_sample_webmainbench_gate_applies_presentation_rules_to_result_details(tmp_path):
    report, repo = _sample_webmainbench_with_history(tmp_path)
    write_language_contract(report, include_english=False)
    providers = sorted(webmainbench_report_check.SAMPLE_PROVIDERS)
    case_ids = [f"case-{number}" for number in range(200)]
    artifact = report / "artifacts/output.md"
    artifact.parent.mkdir()
    artifact.write_text("output", encoding="utf-8")
    records = [{"provider": provider, "case_id": case_id, "attempt_id": f"{provider}__{case_id}",
                "artifact_path": "artifacts/output.md", "output_sha256": file_sha256(artifact)}
               for provider in providers for case_id in case_ids]
    (report / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    sample = report / "sample-manifest.json"
    sample.write_text(json.dumps({
        "cases": [{"track_id": case_id} for case_id in case_ids], "sampling": {"size": 200},
    }), encoding="utf-8")
    summaries = report / "score-summaries.json"
    summaries.write_text(json.dumps(dict.fromkeys(providers, {})), encoding="utf-8")
    dataset = report / "dataset.json"
    dataset.write_text(json.dumps({
        "id": "webmainbench-545-preselected-200-six-methods",
        "sample_manifest_sha256": file_sha256(sample),
        "cases": [{"id": case_id} for case_id in case_ids],
    }), encoding="utf-8")
    results_hash = file_sha256(report / "results.jsonl")
    (report / "result-review.json").write_text(json.dumps({
        "status": "passed", "providers": providers, "sample_manifest_sha256": file_sha256(sample),
        "sample_case_count": 200, "bindings": {"results_jsonl_sha256": results_hash},
    }), encoding="utf-8")
    (report / "webmainbench-anomaly-review.json").write_text(json.dumps({
        "kind": "webmainbench_anomaly_root_cause_review",
        "scope": {"signal_triggered_results": 36, "distinct_cases": 6, "unresolved_triggered_results": 0},
        "triggered_attempt_ids": [f"attempt-{number}" for number in range(36)],
        "bindings": {"dataset_json_sha256": file_sha256(dataset), "results_jsonl_sha256": results_hash,
                     "score_summaries_json_sha256": file_sha256(summaries)},
    }), encoding="utf-8")
    detail = report / "attempts" / (records[0]["attempt_id"] + ".html")
    detail.parent.mkdir()
    detail.write_text("<main></main>", encoding="utf-8")
    _inject_wrong_highlight(detail)
    with pytest.raises(ValueError, match="highlight"):
        webmainbench_report_check.check_sample_report(report, authority_root=repo)


def test_trajectory_gate_accepts_the_bound_profile(tmp_path, monkeypatch):
    output, _, _, _, _, _, files = build_replay_fixture(tmp_path, monkeypatch)
    assert trajectory.check(output, profile_path=files["profile.json"])["html_pages"]


@pytest.mark.parametrize("defect", ["changed", "unbound", "linked"])
def test_trajectory_gate_rejects_a_different_profile(tmp_path, monkeypatch, defect):
    output, _, _, _, _, _, files = build_replay_fixture(tmp_path, monkeypatch)
    profile = files["profile.json"]
    if defect == "changed":
        profile.write_text(profile.read_text(encoding="utf-8") + " ", encoding="utf-8")
    elif defect == "unbound":
        path = output / "report-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        del manifest["profile_sha256"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        target = tmp_path / "profile-target.json"
        profile.rename(target)
        profile.symlink_to(target)
    with pytest.raises(ValueError, match="different instance profile"):
        trajectory.check(output, profile_path=profile)


def _browser_use_report(tmp_path, monkeypatch):
    import harness.browser_use.report as renderer

    # The current renderer stores the selected registry at module scope.
    for name in ("ENGINE_LABELS", "MODEL_LABELS", "BASELINE_ENGINE"):
        monkeypatch.setattr(renderer, name, getattr(renderer, name))
    report, evidence, _ = build_browser_use_fixture(tmp_path, monkeypatch)
    profile = authority(tmp_path / "authority", engines=["chromium", "moli"],
                        baseline_engine="chromium",
                        engine_labels={"chromium": "Chromium", "moli": "Moli"},
                        models=["gpt-5.6-sol"], model_labels={"gpt-5.6-sol": "GPT-5.6 Sol"})
    return report, evidence, profile


@pytest.mark.parametrize("bound", [False, True], ids=["legacy-unbound", "other-profile"])
def test_browser_use_gate_reads_only_an_unbound_legacy_report(tmp_path, monkeypatch, bound):
    report, evidence, profile = _browser_use_report(tmp_path, monkeypatch)
    path = report / "report-data.json"
    audit = json.loads(path.read_text(encoding="utf-8"))
    audit.pop("profile_sha256", None)
    if bound:
        audit["profile_sha256"] = "0" * 64
    # A stale selection hash fails right after the identity check, so reaching it proves a pass.
    audit["selection_sha256"] = "stale"
    path.write_text(json.dumps(audit), encoding="utf-8")
    expected = "different instance profile" if bound else "current pre-registered selection"
    with pytest.raises(ValueError, match=expected):
        browser_use.check(report, evidence, SELECTION, require_complete=False, profile_path=profile)
