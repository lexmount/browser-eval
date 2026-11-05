"""Multi-domain native extraction, frozen references and unchanged product behavior."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harness.web_extract import certification, dataset, pipeline, swde
from tests.web_extract.snapshot_inputs import bind_inputs, serve_input


def plan_for_inputs(home, methods, output):
    return certification.create_plan(
        home, methods, output, snapshots=bind_inputs(home, output.with_suffix(".snapshots.json"))
    )


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    home = tmp_path / "data"
    root = home / "dataset"
    (root / "html").mkdir(parents=True)
    (root / "dataset").mkdir()
    truth = {
        "book-a-0001": {
            "domain": "book",
            "site": "a",
            "url": "https://book.example/one",
            "values": {"author": ["GOLD_DO_NOT_SEND"]},
        },
        "job-b-0002": {
            "domain": "job",
            "site": "b",
            "url": "https://job.example/two",
            "values": {"title": ["Engineer"]},
        },
    }
    schemas = {
        domain: {
            "type": "object",
            "properties": {field: {"type": "array", "items": {"type": "string"}}},
            "required": [field],
            "additionalProperties": False,
        }
        for domain, field in [("book", "author"), ("job", "title")]
    }
    for case in truth:
        (root / "html" / (case + ".html")).write_text("<p>Ada Engineer</p>", encoding="utf-8")
    pipeline.save_json(root / "dataset/ground-truth.json", truth)
    pipeline.save_json(root / "schemas.json", schemas)
    (root / "evaluate.py").write_text("# pinned original page-hit evaluator\n", encoding="utf-8")
    files = {
        p.relative_to(root).as_posix(): {
            "sha256": pipeline.digest(p.read_bytes()),
            "bytes": p.stat().st_size,
        }
        for p in root.rglob("*")
        if p.is_file()
    }
    pipeline.save_json(
        root / "manifest.json",
        {"benchmark": "swde", "commit": "b" * 40, "cases": 2, "files": files},
    )
    card = tmp_path / "swde.json"
    pipeline.save_json(
        card,
        {
            "dataset": {
                "commit": "b" * 40,
                "cases": 2,
                "inventory_sha256": pipeline.digest(
                    json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
                ),
                "files": {"evaluate.py": files["evaluate.py"]["sha256"]},
            }
        },
    )
    monkeypatch.setattr(swde, "CARD", card)
    monkeypatch.setitem(
        dataset.BENCHMARKS, "swde", {"profile": str(card), "module": "harness.web_extract.swde"}
    )
    monkeypatch.setattr(pipeline, "load_runtime_env", lambda: None)
    monkeypatch.setattr("harness.web_extract.repair.load_runtime_env", lambda: None)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token-only")
    monkeypatch.setattr(pipeline, "qualify_fixture", serve_input)
    monkeypatch.setattr("harness.web_extract.repair.qualify_fixture", serve_input)
    return home, schemas


@pytest.mark.parametrize("static_controls", [False, True])
def test_certified_multi_domain_run_uses_task_schema_and_never_sends_labels(
    inputs, tmp_path, monkeypatch, static_controls
):
    home, schemas = inputs
    calls = []

    def request(url, *, json_body, **kwargs):
        calls.append(json_body)
        field = json_body["response_format"]["json_schema"]["required"][0]
        native = {"success": True, "result": {field: ["Ada" if field == "author" else "Engineer"]}}
        return SimpleNamespace(status=200, headers={}, text=lambda: json.dumps(native))

    monkeypatch.setattr(pipeline, "request", request)
    plan = tmp_path / "plan.json"
    frozen = plan_for_inputs(home, ["cloudflare-json"], plan)
    if static_controls:
        frozen["participants"]["cloudflare-json"]["native_options"] = {
            "input_route": "url",
            "setJavaScriptEnabled": False,
            "rejectRequestPattern": [".*"],
        }
        pipeline.save_json(plan, frozen)
    run = tmp_path / "run"
    result = certification.run(home, plan, "cloudflare-json", run)
    assert result["cases"] == 2
    _, rows = pipeline.load_run(run)
    assert len(calls) == 2

    if static_controls:
        assert all(
            c["setJavaScriptEnabled"] is False and c["rejectRequestPattern"] == [".*"]
            for c in calls
        )
        assert (
            pipeline.read_json(run / "contract.json")["native_options"]
            == frozen["participants"]["cloudflare-json"]["native_options"]
        )
    assert [c["response_format"]["json_schema"] for c in calls] == [
        pipeline.instruction_schema(schemas[domain], pipeline.RECORD_INSTRUCTIONS)
        for domain in ["book", "job"]
    ]
    assert "GOLD_DO_NOT_SEND" not in json.dumps(calls)
    assert all(r["schema_valid"] and r["elapsed_seconds"] >= 0 for r in rows)
    assert rows[0]["normalized_fields"] == {"author": ["Ada"]}
    assert result["scores"]["cloudflare-json"]["page_hit_macro"]["cases"] == 2
    assert (run / "certificate.json").is_file()
    certification.run(home, plan, "cloudflare-json", run)
    assert len(calls) == 2  # Completed results are never rerun.
    original_scores = (run / "scores.json").read_bytes()
    reprojected = pipeline.score_run(
        home, run, reproject=True, output=tmp_path / "reprojected.json"
    )
    assert reprojected["scores"] == result["scores"]
    assert reprojected["projection_revision"]["changed_cases"] == []
    assert set(reprojected["projection_revision"]["regression_ids"]) == set(
        pipeline.RECORD_PROJECTION_REGRESSIONS
    )
    assert (run / "scores.json").read_bytes() == original_scores
    assert len(calls) == 2


@pytest.fixture
def source_revision_run(inputs, tmp_path, monkeypatch, request):
    home, _ = inputs
    unannotated = getattr(request, "param", False)
    root = home / "dataset"
    truth = pipeline.read_json(root / "dataset/ground-truth.json")
    truth["book-a-0001"]["values"]["author"] = [] if unannotated else ["Ada"]
    truth["job-b-0002"]["values"]["title"] = ["Engineer &amp; Co"]
    pipeline.save_json(root / "dataset/ground-truth.json", truth)
    pages = {
        "book-a-0001": (
            b'<p>Author: <span id="author">Ada Lovelace</span></p><p id="other">Grace Hopper</p>'
        ),
        "job-b-0002": b"<h1>Engineer &amp; Co</h1>",
    }
    for cid, raw in pages.items():
        (root / "html" / (cid + ".html")).write_bytes(raw)
    manifest = pipeline.read_json(root / "manifest.json")
    for name in manifest["files"]:
        raw = (root / name).read_bytes()
        manifest["files"][name] = {"sha256": pipeline.digest(raw), "bytes": len(raw)}
    pipeline.save_json(root / "manifest.json", manifest)
    profile = pipeline.read_json(swde.CARD)
    profile["dataset"]["inventory_sha256"] = pipeline.digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    pipeline.save_json(swde.CARD, profile)
    calls = []

    def request(url, *, json_body, **kwargs):
        calls.append(json_body)
        field = json_body["response_format"]["json_schema"]["required"][0]
        fields = {field: ["Ada Lovelace" if field == "author" else "Engineer & Co"]}
        return SimpleNamespace(
            status=200, headers={}, text=lambda: json.dumps({"success": True, "result": fields})
        )

    monkeypatch.setattr(pipeline, "request", request)
    plan, run = tmp_path / "original-plan.json", tmp_path / "original-run"
    plan_for_inputs(home, ["cloudflare-json"], plan)
    certification.run(home, plan, "cloudflare-json", run)
    qualified = swde.qualify_reference_text(truth, pages)
    references = qualified["references"]
    references["book-a-0001"]["values"]["author"] = ["Ada Lovelace"]
    revision = {
        "schema": "record-source-reference-candidate/1",
        "original_reference_sha256": pipeline.digest(
            (root / "dataset/ground-truth.json").read_bytes()
        ),
        "source_hashes": qualified["source_hashes"],
        "dom_text_changes": qualified["changes"],
        "references": references,
        "partial_label_decisions": [
            {
                "case_id": "book-a-0001",
                "field": "author",
                "original": ["Ada"],
                "accepted": ["Ada Lovelace"],
                "reason": "The annotation truncated the author text node",
                "source_sha256": pipeline.digest(pages["book-a-0001"]),
                "evidence": [
                    {
                        "value": "Ada Lovelace",
                        "source_quote": "Ada Lovelace",
                        "xpath": "//*[@id='author']",
                        "field_context": "Author: Ada Lovelace",
                    }
                ],
            }
        ],
        "unavailable_reference_groups": [],
        "regression_ids": swde.check_reference_controls(),
        "text_matching": "whitespace",
        "provider_calls_added": 0,
    }
    authority = tmp_path / "reference-revision.json"
    if unannotated:
        import io
        import tarfile

        revision["references"]["book-a-0001"]["values"]["author"] = []
        revision["partial_label_decisions"] = []
        revision["unavailable_reference_groups"] = [["book", "a", "author"]]
        annotations = b"book\ta\tauthor\n2\t0\t0\t0\n0001\t0\t<NULL>\n0002\t0\t<NULL>\n"
        archive = home / "source/archives/groundtruth.7z"
        archive.parent.mkdir(parents=True)
        with tarfile.open(archive, "w") as bundle:
            entry = tarfile.TarInfo("groundtruth/book/book-a-author.txt")
            entry.size = len(annotations)
            bundle.addfile(entry, io.BytesIO(annotations))
        card = pipeline.read_json(swde.CARD)
        card.setdefault("source", {})["annotation_archive_sha256"] = pipeline.digest(
            archive.read_bytes()
        )
        pipeline.save_json(swde.CARD, card)
        coverage = {
            "original_annotation_archive_sha256": pipeline.digest(archive.read_bytes()),
            "all_empty_selected_groups": [
                {
                    "domain": "book",
                    "site": "a",
                    "field": "author",
                    "selected_pages": 1,
                    "full_site_pages": 2,
                    "full_site_nonempty": 0,
                    "original_annotation_sha256": pipeline.digest(annotations),
                }
            ],
        }
        proof = tmp_path / "coverage.json"
        pipeline.save_json(proof, coverage)
        revision["coverage_authority"] = {
            "path": str(proof),
            "sha256": pipeline.digest(proof.read_bytes()),
        }

        def original_annotations(command, **options):
            assert command == ["bsdtar", "-xOf", str(archive), "groundtruth/book/book-a-author.txt"]
            return SimpleNamespace(stdout=annotations)

        monkeypatch.setattr(swde.subprocess, "run", original_annotations)
    pipeline.save_json(authority, revision)
    return home, run, authority, calls


def test_reference_correction_scores_native_answers_without_calls_or_evidence_mutation(
    source_revision_run, tmp_path
):
    from harness.web_extract.cli import main

    home, run, authority, calls = source_revision_run
    originals = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    output = tmp_path / "source-qualified-scores.json"
    main(
        [
            "--home",
            str(home),
            "score",
            str(run),
            "--reference-revision",
            str(authority),
            "--output",
            str(output),
        ]
    )
    result = pipeline.read_json(output)
    assert result["scores"]["cloudflare-json"]["value_micro"]["f1"] == 1
    assert (
        pipeline.read_json(run / "scores.json")["scores"]["cloudflare-json"]["value_micro"]["f1"]
        == 0
    )
    assert result["reference_revision"]["authority_sha256"] == pipeline.digest(
        authority.read_bytes()
    )
    assert result["reference_revision"]["provider_calls_added"] == 0
    assert result["reference_revision"]["regression_ids"] == swde.check_reference_controls()
    assert result["reference_revision"]["root_causes"]
    assert len(calls) == 2
    assert all(p.read_bytes() == data for p, data in originals.items())
    pipeline.score_run(home, run, reference_revision=authority, output=output)
    assert len(calls) == 2


@pytest.fixture
def reviewed_record_report(record_audit_material, tmp_path, monkeypatch):
    from harness.web_extract import report, review

    home, content, works, adjudication, authority = record_audit_material
    audit_path = tmp_path / "complete-report-audit.json"
    review.assemble_record(
        home,
        content,
        works,
        audit_path,
        profile=swde.CARD,
        adjudications=[adjudication],
        reference_revision=authority,
    )
    from harness.web_extract import snapshots

    version, cases = snapshots.inventory(home)
    snapshot = {
        "mode": "dataset_manifest",
        "manifest_url": "https://example.org/snapshots.json",
        "manifest_sha256": "a" * 64,
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "fixture_base": "https://example.org/frozen",
    }
    preview = tmp_path / "preview-receipt.json"
    pipeline.save_json(preview, snapshot)
    monkeypatch.setattr(snapshots, "check_manifest", lambda *a: snapshot)

    model = pipeline.read_json(content)
    model["preview_snapshot"] = {
        "path": str(preview),
        "sha256": pipeline.digest(preview.read_bytes()),
    }
    methods = list(model["participants"])
    model.update(
        mode="field-extraction-cohort",
        title="Source-qualified record example",
        dataset_manifest=str(home / "dataset/manifest.json"),
        snapshot_base="https://example.org/frozen",
        audit={"path": str(audit_path), "sha256": pipeline.digest(audit_path.read_bytes())},
        reference_revision={
            "path": str(authority),
            "sha256": pipeline.digest(authority.read_bytes()),
        },
        prices={
            m: {
                "usd_per_1000": "$0 synthetic",
                "terms": {"zh": "合成示例"},
                "verified_at": "2026-01-01",
                "sources": ["https://example.org/pricing"],
            }
            for m in methods
        },
    )
    for method in methods:
        model["participants"][method]["label"] = method
    for selection, name in zip(
        model["evidence_runs"], ["original-plan.json", "second-plan.json"], strict=True
    ):
        plan = tmp_path / name
        selection.update(plan=str(plan), plan_sha256=pipeline.digest(plan.read_bytes()))
    pipeline.save_json(content, model)
    profile = pipeline.read_json(swde.CARD)
    profile.update(
        independent_result_review_scope="all",
        research_report={"content": {"sha256": pipeline.digest(content.read_bytes())}},
    )
    profile_path = tmp_path / "report-profile.json"
    pipeline.save_json(profile_path, profile)
    output = tmp_path / "reviewed-report"
    report.build(output, content, home=home, profile=profile_path, language="zh")
    return home, content, profile_path, output, audit_path


@pytest.fixture
def source_field_revision(inputs, tmp_path):
    home, _ = inputs
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    truth["book-a-0001"]["values"]["author"] = ["Ada"]
    truth["job-b-0002"]["values"]["title"] = ["Engineer", "No data"]
    pages = {
        "book-a-0001": b"<p>Ada</p>",
        "job-b-0002": (
            b'<main><h1 id="title">Engineer</h1><p id="alt">Senior Engineer</p>'
            b'<p id="placeholder">No data</p><p id="mixed"><b>Other</b> Job</p>'
            b'<a id="self" href="https://job.example/two">Engineer</a>'
            b'<a id="other" href="https://job.example/other">Engineer</a></main>'
        ),
    }
    pipeline.save_json(home / "dataset/dataset/ground-truth.json", truth)
    for cid, raw in pages.items():
        (home / "dataset/html" / (cid + ".html")).write_bytes(raw)
    qualified = swde.qualify_reference_text(truth, pages)
    references = qualified["references"]
    references["job-b-0002"]["values"]["title"] = ["Engineer", "Senior Engineer"]
    decision = {
        "case_id": "job-b-0002",
        "field": "title",
        "original": ["Engineer", "No data"],
        "accepted": ["Engineer", "Senior Engineer"],
        "rule": "SWDE section 5(b)",
        "reason": "Accept complete title nodes and exclude the missing-value placeholder",
        "source_sha256": pipeline.digest(pages["job-b-0002"]),
        "evidence": [
            {
                "value": value,
                "source_quote": value,
                "role": role,
                "xpath": f"//*[@id='{identity}']/text()",
                "field_context": value,
            }
            for identity, value, role in [
                ("title", "Engineer", "accepted"),
                ("alt", "Senior Engineer", "accepted"),
                ("placeholder", "No data", "excluded"),
            ]
        ],
    }
    authority = tmp_path / "field-revision.json"
    revision = {
        "schema": "record-source-reference-candidate/1",
        "original_reference_sha256": pipeline.digest(
            (home / "dataset/dataset/ground-truth.json").read_bytes()
        ),
        "source_hashes": qualified["source_hashes"],
        "dom_text_changes": qualified["changes"],
        "references": references,
        "field_decisions": [decision],
        "regression_ids": swde.check_reference_controls(),
        "text_matching": "whitespace",
        "provider_calls_added": 0,
    }
    pipeline.save_json(authority, revision)
    return home, authority


def test_source_field_decision_accepts_exact_alternatives_and_removes_only_proven_placeholders(
    source_field_revision,
):
    home, authority = source_field_revision
    references, policy = swde.load_reference_revision(home, authority)
    for value, hits in [("Engineer", 1), ("Senior Engineer", 1), ("Other Job", 0), ("No data", 0)]:
        result = swde.score(
            references, {"book-a-0001": {}, "job-b-0002": {"title": [value]}}, **policy
        )
        assert (
            next(row for row in result["site_fields"] if row["field"] == "title")["hit_pages"]
            == hits
        )


@pytest.mark.parametrize(
    "damage",
    [
        "no-excluded-evidence",
        "concatenated-node",
        "attribute",
        "missing-rule",
        "source-hash",
        "duplicate",
    ],
)
def test_source_field_revision_rejects_unproven_or_nontext_changes(source_field_revision, damage):
    home, authority = source_field_revision
    revision = pipeline.read_json(authority)
    decision = revision["field_decisions"][0]
    if damage == "no-excluded-evidence":
        decision["evidence"].pop()
    elif damage == "concatenated-node":
        decision["accepted"][1] = "Other Job"
        decision["evidence"][1].update(
            value="Other Job",
            source_quote="Other Job",
            field_context="Other Job",
            xpath="//*[@id='mixed']",
        )
    elif damage == "attribute":
        decision["evidence"][0]["xpath"] = "//*[@id='title']/@id"
    elif damage == "missing-rule":
        decision.pop("rule")
    elif damage == "source-hash":
        decision["source_sha256"] = "0" * 64
    else:
        revision["field_decisions"].append(decision.copy())
    pipeline.save_json(authority, revision)
    with pytest.raises(ValueError, match="Reference|reference"):
        swde.load_reference_revision(home, authority)


@pytest.mark.parametrize("target", ["same", "wrong-url", "wrong-link", "not-a-link"])
def test_source_self_link_must_identify_the_frozen_main_record(source_field_revision, target):
    home, authority = source_field_revision
    revision = pipeline.read_json(authority)
    evidence = revision["field_decisions"][0]["evidence"][0]
    evidence.update(xpath="//*[@id='self']", record_url="https://job.example/two")
    if target == "wrong-url":
        evidence["record_url"] = "https://job.example/other"
    elif target == "wrong-link":
        evidence["xpath"] = "//*[@id='other']"
    elif target == "not-a-link":
        evidence["xpath"] = "//*[@id='title']"
    pipeline.save_json(authority, revision)
    if target == "same":
        references, _ = swde.load_reference_revision(home, authority)
        assert references["job-b-0002"]["values"]["title"] == ["Engineer", "Senior Engineer"]
    else:
        with pytest.raises(ValueError, match="same source record"):
            swde.load_reference_revision(home, authority)


@pytest.mark.parametrize("source_revision_run", [False, True], indirect=True)
def test_record_report_reproduces_complete_review_and_preserves_original_evidence(
    reviewed_record_report,
):
    from bs4 import BeautifulSoup

    from harness.web_extract import report

    home, content, profile, output, audit = reviewed_record_report
    report.check(output)
    manifest = pipeline.read_json(output / "report-manifest.json")
    assert manifest["required_review_scope"] == "all"
    assert manifest["audit"]["source_sha256"] == pipeline.digest(audit.read_bytes())
    assert pipeline.read_json(
        output / "public-evidence/original-reference.json"
    ) == pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    assert (
        len(
            BeautifulSoup(
                (output / "zh/review.html").read_text(encoding="utf-8"), "html.parser"
            ).select("[data-record-review]")
        )
        == 1
    )
    metrics = pipeline.read_json(output / "data/metrics.json")
    original = pipeline.read_json(output / "data/original-metrics.json")
    for method in metrics:
        assert metrics[method]["latency"] == original[method]["latency"]
        assert metrics[method]["physical_calls"] == original[method]["physical_calls"]
        assert metrics[method]["scores"]["value_micro"]["f1"] == 1


@pytest.mark.parametrize(
    "damage", ["missing-audit", "unresolved", "native-identity", "aggregate", "source-hash"]
)
def test_record_report_rejects_incomplete_or_stale_review(reviewed_record_report, tmp_path, damage):
    from harness.web_extract import report

    home, content, profile_path, _, audit_path = reviewed_record_report
    model = pipeline.read_json(content)
    audit = pipeline.read_json(audit_path)
    if damage == "missing-audit":
        model.pop("audit")
    elif damage == "unresolved":
        audit["unresolved"] = 1
    elif damage == "native-identity":
        next(iter(audit["results"].values()))["execution_sha256"] = "0" * 64
    elif damage == "aggregate":
        next(iter(audit["audited_scores"].values()))["value_micro"]["f1"] = 0
    else:
        audit["source_hashes"]["book-a-0001"] = "0" * 64
    pipeline.save_json(audit_path, audit)
    if "audit" in model:
        model["audit"]["sha256"] = pipeline.digest(audit_path.read_bytes())
    pipeline.save_json(content, model)
    profile = pipeline.read_json(profile_path)
    profile["research_report"]["content"]["sha256"] = pipeline.digest(content.read_bytes())
    pipeline.save_json(profile_path, profile)
    with pytest.raises(ValueError, match="review|audit"):
        report.build(
            tmp_path / "invalid-report", content, home=home, profile=profile_path, language="zh"
        )


@pytest.mark.parametrize("damage", ["missing-summary", "wrong-summary", "wrong-fields"])
def test_record_release_checks_review_content_beyond_file_hashes(reviewed_record_report, damage):
    from bs4 import BeautifulSoup

    from harness.web_extract import report

    _, _, _, output, _ = reviewed_record_report
    route = "zh/review.html" if damage != "wrong-fields" else "zh/results.html"
    path = output / route
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    if damage == "missing-summary":
        soup.select_one("[data-record-review]").decompose()
    elif damage == "wrong-summary":
        soup.select_one("[data-record-review] tbody td").string = "Wrong method"
    else:
        # A completed review must not hide the result inventory or its evidence links.
        soup.select_one("tr[data-status]").decompose()
    path.write_text(str(soup), encoding="utf-8")
    manifest_path = output / "report-manifest.json"
    manifest = pipeline.read_json(manifest_path)
    manifest["files"][route] = pipeline.digest(path.read_bytes())
    pipeline.save_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="review summary|inventory"):
        report.check(output)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-output",
        "overwrite",
        "source-hash",
        "original-reference",
        "missing-regressions",
        "undeclared-value",
        "undeclared-metadata",
        "missing-decision",
        "wrong-original",
        "wrong-locator",
        "missing-context",
        "different-accepted-value",
        "broad-prefix",
        "missing-coverage",
        "old-score",
    ],
)
def test_invalid_reference_correction_is_rejected_before_writing(
    source_revision_run, tmp_path, mutation
):
    home, run, authority, calls = source_revision_run
    revision = pipeline.read_json(authority)
    output = tmp_path / "rejected.json"
    if mutation == "missing-output":
        output = None
    elif mutation == "overwrite":
        output = run / "scores.json"
    elif mutation == "source-hash":
        revision["source_hashes"]["book-a-0001"] = "0" * 64
    elif mutation == "original-reference":
        revision["original_reference_sha256"] = "0" * 64
    elif mutation == "missing-regressions":
        revision["regression_ids"] = []
    elif mutation == "undeclared-value":
        revision["references"]["job-b-0002"]["values"]["title"] = ["Other"]
    elif mutation == "undeclared-metadata":
        revision["references"]["job-b-0002"]["url"] = "https://unrelated.example"
    elif mutation == "missing-decision":
        revision["partial_label_decisions"] = []
    elif mutation == "wrong-original":
        revision["partial_label_decisions"][0]["original"] = ["Different"]
    elif mutation == "wrong-locator":
        revision["partial_label_decisions"][0]["evidence"][0]["xpath"] = "//*[@id='other']"
    elif mutation == "missing-context":
        revision["partial_label_decisions"][0]["evidence"][0]["field_context"] = "Invented role"
    elif mutation == "different-accepted-value":
        revision["partial_label_decisions"][0]["accepted"] = ["Grace Hopper"]
    elif mutation == "broad-prefix":
        revision["partial_label_decisions"][0]["evidence"][0]["source_quote"] = "Ada"
    elif mutation == "missing-coverage":
        revision["unavailable_reference_groups"] = [["book", "a", "author"]]
    elif mutation == "old-score":
        old = pipeline.read_json(run / "scores.json")
        old["scores"] = {}
        pipeline.save_json(run / "scores.json", old)
    pipeline.save_json(authority, revision)
    before = (run / "scores.json").read_bytes()
    with pytest.raises(ValueError):
        pipeline.score_run(home, run, reference_revision=authority, output=output)
    if mutation not in ("missing-output", "overwrite"):
        assert not output.exists()
    assert (run / "scores.json").read_bytes() == before
    assert len(calls) == 2


@pytest.fixture
def record_audit_material(source_revision_run, tmp_path, monkeypatch):
    from copy import deepcopy

    from harness.web_extract import review

    home, first, authority, calls = source_revision_run
    monkeypatch.setenv("SGAI_API_KEY", "test-sgai-key-only")
    second_calls = []

    def request(url, *, json_body, **kwargs):
        second_calls.append(json_body)
        field = json_body["schema"]["required"][0]
        fields = {field: ["Ada Lovelace" if field == "author" else "Engineer & Co"]}
        return SimpleNamespace(status=200, headers={}, text=lambda: json.dumps({"json": fields}))

    monkeypatch.setattr(pipeline, "request", request)
    second = tmp_path / "second-provider"
    second_plan = tmp_path / "second-plan.json"
    plan_for_inputs(home, ["scrapegraph-extract"], second_plan)
    certification.run(home, second_plan, "scrapegraph-extract", second)
    selections = [{"run": str(first)}, {"run": str(second)}]
    content = tmp_path / "audit-content.json"
    model = {
        "participants": {m: {} for m in ["cloudflare-json", "scrapegraph-extract"]},
        "evidence_runs": selections,
    }
    pipeline.save_json(content, model)
    works = []
    for index, selection in enumerate(selections):
        selected = tmp_path / f"selected-{index}.json"
        pipeline.save_json(selected, {"evidence_runs": [selection]})
        work = tmp_path / f"review-{index}"
        manifest = review.prepare(home, selected, work)
        for entry in manifest["packets"]:
            packet = review.read_packet(Path(entry["path"]))
            cases = []
            for case in packet["cases"]:
                field = next(iter(case["reference"]))
                value = "Ada Lovelace" if field == "author" else "Engineer & Co"
                finding = {
                    "status": "supported",
                    "values": [value],
                    "source_quote": value,
                    "source_locator": "main record text node",
                    "reason": "Frozen source value",
                }
                cases.append(
                    {
                        "case_id": case["case_id"],
                        "main_record": "Frozen example",
                        "source_fields": {field: deepcopy(finding)},
                        "reference": {field: {**finding, "status": "incorrect"}},
                        "outputs": [
                            {
                                "method": output["method"],
                                "fields": {field: deepcopy(finding)},
                                "projection_issue": "Native field arrays are unchanged",
                            }
                            for output in case["outputs"]
                        ],
                    }
                )
            opinion = {"cases": cases}
            review.validate(opinion, packet)
            pipeline.save_json(work / "reviews" / Path(entry["path"]).name, opinion)
        pipeline.save_json(
            work / "reviewer.json", {"model": "synthetic-reviewer", "reasoning": "high"}
        )
        works.append(work)
    rules = tmp_path / "rules.txt"
    rules.write_text(
        "Original SWDE labels target source text nodes, including mixed attributes.",
        encoding="utf-8",
    )
    adjudication = tmp_path / "adjudication"
    manifest = review.prepare_adjudication(works[0], adjudication, rules)
    originals = {
        c["case_id"]: c for c in pipeline.read_json(works[0] / "reviews/000.json")["cases"]
    }
    for entry in manifest["packets"]:
        packet = review.read_packet(Path(entry["path"]))
        opinion = {
            "cases": [{**deepcopy(originals[c["case_id"]]), "outputs": []} for c in packet["cases"]]
        }
        review.validate(opinion, packet)
        pipeline.save_json(adjudication / "reviews" / Path(entry["path"]).name, opinion)
    pipeline.save_json(adjudication / "reviewer.json", {"model": "synthetic-adjudicator"})
    assert len(calls) + len(second_calls) == 4
    return home, content, works, adjudication, authority


@pytest.mark.parametrize("source_revision_run", [False, True], indirect=True)
def test_record_audit_assembly_joins_all_providers_and_source_qualified_references(
    record_audit_material, tmp_path
):
    from harness.web_extract.cli import main

    home, content, works, adjudication, authority = record_audit_material
    output = tmp_path / "complete-audit.json"
    main(
        [
            "--home",
            str(home),
            "audit-assemble",
            "--content",
            str(content),
            "--profile",
            str(swde.CARD),
            "--initial",
            str(works[0]),
            "--additional-review",
            str(works[1]),
            "--adjudication",
            str(adjudication),
            "--reference-revision",
            str(authority),
            "--output",
            str(output),
        ]
    )
    audit = pipeline.read_json(output)
    assert audit["schema"] == "record-complete-audit/1"
    assert audit["case_count"] == 2 and audit["result_count"] == 4
    assert audit["unresolved"] == audit["accepted_unreviewed_passes"] == 0
    assert audit["reference_revision_sha256"] == pipeline.digest(authority.read_bytes())
    assert set(audit["audited_scores"]) == {"cloudflare-json", "scrapegraph-extract"}
    assert all(s["value_micro"]["f1"] == 1 for s in audit["audited_scores"].values())
    assert len(audit["review_receipts"]) == 3
    assert audit["provider_calls_added"] == 0
    assert audit["results"]["cloudflare-json:job-b-0002"]["audited_fields"]["title"]["actual"] == [
        "Engineer & Co"
    ]


def test_record_audit_retains_model_disagreement_and_source_bound_final_decision(
    record_audit_material, tmp_path
):
    from harness.web_extract import review

    home, content, works, adjudication, authority = record_audit_material
    revision = pipeline.read_json(authority)
    original = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    decision = {
        "case_id": "job-b-0002",
        "field": "title",
        "original": original["job-b-0002"]["values"]["title"],
        "accepted": ["Engineer & Co"],
        "rule": "SWDE section 5(d)",
        "reason": "The complete source title is retained with literal entity decoding",
        "source_sha256": revision["source_hashes"]["job-b-0002"],
        "evidence": [
            {
                "value": "Engineer & Co",
                "source_quote": "Engineer & Co",
                "xpath": "//h1/text()",
                "field_context": "Engineer & Co",
                "role": "accepted",
            }
        ],
    }
    revision["field_decisions"] = [decision]
    pipeline.save_json(authority, revision)
    path = adjudication / "reviews/000.json"
    opinion = pipeline.read_json(path)
    title = next(c for c in opinion["cases"] if c["case_id"] == "job-b-0002")
    title["reference"]["title"]["status"] = "ambiguous"
    pipeline.save_json(path, opinion)
    output = tmp_path / "source-final-audit.json"
    review.assemble_record(
        home,
        content,
        works,
        output,
        profile=swde.CARD,
        adjudications=[adjudication],
        reference_revision=authority,
    )
    audit = pipeline.read_json(output)
    assert audit["unresolved"] == 0
    source = audit["sources"]["job-b-0002"]
    assert source["reference_review"]["title"]["status"] == "ambiguous"
    assert source["source_decisions"]["title"] == decision


@pytest.mark.parametrize(
    "scope",
    [
        "current",
        "missing",
        "unchanged-extra",
        "stale-identity",
        "later-dispute",
        "later-value-dispute",
        "same-proposal-new-wording",
        "covered-later-dispute",
    ],
)
def test_record_audit_supplemental_review_covers_only_changed_repair_identities(
    record_audit_material, tmp_path, monkeypatch, scope
):

    from harness.web_extract import repair, review

    home, content, works, adjudication, authority = record_audit_material
    original_loader = repair.load_selection

    def corrected_loader(home, selection):
        source, rows = original_loader(home, selection)
        return source, [
            {**row, "repair_run_id": "retained-repair", "retry_missing_timers": 1}
            if (row["method"], row["case_id"]) == ("cloudflare-json", "book-a-0001")
            else row
            for row in rows
        ]

    monkeypatch.setattr(repair, "load_selection", corrected_loader)
    work = tmp_path / "supplemental-current-identity"
    review.prepare(home, content, work, prior=works[0])
    packet = review.read_packet(work / "packets/000.json")
    assert len(packet["cases"]) == len(packet["cases"][0]["outputs"]) == 1
    opinion = pipeline.read_json(works[0] / "reviews/000.json")
    opinion["cases"] = [c for c in opinion["cases"] if c["case_id"] == "book-a-0001"]
    if scope in {"later-dispute", "covered-later-dispute"}:
        opinion["cases"][0]["reference"]["author"].update(
            status="ambiguous", reason="Later source-scope dispute"
        )
    elif scope == "later-value-dispute":
        opinion["cases"][0]["reference"]["author"].update(
            values=[], reason="Later proposed absence"
        )
    elif scope == "same-proposal-new-wording":
        opinion["cases"][0]["reference"]["author"]["reason"] = (
            "Same proposed reference correction, rephrased"
        )
    pipeline.save_json(work / "reviews/000.json", opinion)
    pipeline.save_json(work / "reviewer.json", {"model": "synthetic-current-execution-review"})
    if scope == "covered-later-dispute":
        adjudication = tmp_path / "current-dispute-adjudication"
        selected = review.prepare_adjudication(
            works[0], adjudication, tmp_path / "rules.txt", additional_priors=[work]
        )
        originals = {
            c["case_id"]: c for c in pipeline.read_json(works[0] / "reviews/000.json")["cases"]
        }
        for entry in selected["packets"]:
            packet = review.read_packet(Path(entry["path"]))
            pipeline.save_json(
                adjudication / "reviews" / Path(entry["path"]).name,
                {"cases": [{**originals[c["case_id"]], "outputs": []} for c in packet["cases"]]},
            )
        pipeline.save_json(
            adjudication / "reviewer.json", {"model": "synthetic-current-adjudicator"}
        )
    if scope == "unchanged-extra":
        # A second complete review is not a valid supplement for one changed identity.
        work = works[1]
    elif scope == "stale-identity":
        original_packet = review.read_packet(works[0] / "packets/000.json")
        packet["cases"][0]["outputs"][0]["execution_identity"] = next(
            c["outputs"][0]["execution_identity"]
            for c in original_packet["cases"]
            if c["case_id"] == "book-a-0001"
        )
        # Rebind the packet so rejection must come from current execution verification.
        manifest = review.read_manifest(work)
        pipeline.save_json(work / "packets/000.json", packet)
        manifest["packets"][0]["sha256"] = pipeline.digest((work / "packets/000.json").read_bytes())
        stored = pipeline.read_json(work / "manifest.json")
        stored["packets"][0]["sha256"] = manifest["packets"][0]["sha256"]
        pipeline.save_json(work / "manifest.json", stored)
    output = tmp_path / "supplemental-audit.json"
    kwargs = dict(
        profile=swde.CARD,
        adjudications=[adjudication],
        reference_revision=authority,
        supplemental=None if scope == "missing" else work,
    )
    if scope not in {"current", "covered-later-dispute", "same-proposal-new-wording"}:
        with pytest.raises(
            ValueError, match="supplemental|current native execution|different predecessor reviews"
        ):
            review.assemble_record(home, content, works, output, **kwargs)
    else:
        review.assemble_record(home, content, works, output, **kwargs)
        audit = pipeline.read_json(output)
        assert audit["result_count"] == audit["original_results_reviewed"] == 4
        assert audit["supplemental_results_reviewed"] == 1
        assert audit["unresolved"] == audit["provider_calls_added"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "native",
        "execution",
        "schema",
        "reference",
        "projection",
        "adjudication",
        "profile",
        "runtime",
        "source-url",
    ],
)
def test_record_audit_rejects_incomplete_or_changed_review_authorities(
    record_audit_material, tmp_path, monkeypatch, mutation
):
    from harness.web_extract import review

    home, content, works, adjudication, authority = record_audit_material
    selected = list(works)
    adjudications = [adjudication]
    profile = swde.CARD
    if mutation == "missing":
        selected.pop()
    elif mutation == "duplicate":
        selected.append(works[0])
    elif mutation == "adjudication":
        adjudications.append(adjudication)
    elif mutation == "projection":
        monkeypatch.setattr(review, "normalize", lambda *a, **k: {"author": ["Changed"]})
    elif mutation == "profile":
        profile = tmp_path / "wrong-profile.json"
        value = pipeline.read_json(swde.CARD)
        value["dataset"]["commit"] = "0" * 40
        pipeline.save_json(profile, value)
    else:
        packet = works[0] / "packets/000.json"
        value = pipeline.read_json(packet)
        case = value["cases"][0]
        if mutation == "native":
            case["outputs"][0]["native_response"] = {"result": {"author": ["Other"]}}
        elif mutation == "execution":
            case["outputs"][0]["execution_identity"] = "0" * 64
        elif mutation == "runtime":
            case["outputs"][0]["runtime_error"] = "Different runtime error"
        elif mutation == "source-url":
            case["source_url"] = "https://unrelated.example"
        elif mutation == "schema":
            case["field_schema"]["additionalProperties"] = True
        elif mutation == "reference":
            case["reference"][next(iter(case["reference"]))] = []
        pipeline.save_json(packet, value)
        manifest = pipeline.read_json(works[0] / "manifest.json")
        manifest["packets"][0]["sha256"] = pipeline.digest(packet.read_bytes())
        pipeline.save_json(works[0] / "manifest.json", manifest)
    output = tmp_path / "not-assembled.json"
    with pytest.raises(ValueError):
        review.assemble_record(
            home,
            content,
            selected,
            output,
            profile=profile,
            adjudications=adjudications,
            reference_revision=authority,
        )
    assert not output.exists()


def test_complete_record_review_does_not_hide_unresolved_reference_findings(
    record_audit_material, tmp_path
):
    from harness.web_extract import review

    home, content, works, _, authority = record_audit_material
    value = review.assemble_record(
        home,
        content,
        works,
        tmp_path / "draft.json",
        profile=swde.CARD,
        reference_revision=authority,
    )
    assert value["result_count"] == 4
    assert value["unresolved"] == 2
    assert len(value["unresolved_fields"]) == 2


@pytest.mark.parametrize("source_revision_run", [True], indirect=True)
@pytest.mark.parametrize(
    "mutation", ["none", "outside-sample", "selected-size", "full-size", "archive", "archive-link"]
)
def test_unannotated_scope_requires_complete_original_annotation_evidence(
    source_revision_run, tmp_path, monkeypatch, mutation
):
    home, run, authority, calls = source_revision_run
    revision = pipeline.read_json(authority)
    proof = tmp_path / "coverage.json"
    coverage = pipeline.read_json(proof)
    group = coverage["all_empty_selected_groups"][0]
    if mutation == "outside-sample":
        annotations = b"book\ta\tauthor\n2\t1\t1\t1\n0001\t0\t<NULL>\n0002\t1\tActual author\n"
        group["original_annotation_sha256"] = pipeline.digest(annotations)
        monkeypatch.setattr(
            swde.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=annotations)
        )
    elif mutation == "selected-size":
        group["selected_pages"] = 0
    elif mutation == "full-size":
        group["full_site_pages"] = 1
    elif mutation == "archive":
        (home / "source/archives/groundtruth.7z").write_bytes(b"Changed archive")
    elif mutation == "archive-link":
        archive = home / "source/archives/groundtruth.7z"
        preserved = tmp_path / "original-annotations.7z"
        archive.rename(preserved)
        archive.symlink_to(preserved)
    pipeline.save_json(proof, coverage)
    revision["coverage_authority"]["sha256"] = pipeline.digest(proof.read_bytes())
    pipeline.save_json(authority, revision)
    output = tmp_path / "unannotated-correction.json"
    if mutation == "none":
        result = pipeline.score_run(home, run, reference_revision=authority, output=output)
        scores = result["scores"]["cloudflare-json"]
        assert scores["value_micro"]["f1"] == 1
        assert scores["reference_coverage"]["unscored_page_fields"] == 1
        assert scores["page_hit_macro"]["site_field_groups"] == 1
    else:
        with pytest.raises(ValueError, match="annotation|archive|Unsafe output"):
            pipeline.score_run(home, run, reference_revision=authority, output=output)
        assert not output.exists()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "missing",
    ["schema", "task_adapter_sha256", "dataset_profile_sha256", "dataset_registry_sha256"],
)
def test_record_certification_requires_every_frozen_binding_before_writing(
    inputs, tmp_path, monkeypatch, missing
):
    home, _ = inputs
    path = tmp_path / "plan.json"
    plan = plan_for_inputs(home, ["cloudflare-json"], path)
    plan.pop(missing)
    pipeline.save_json(path, plan)
    dispatch = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(certification, "probe", dispatch)
    target = tmp_path / "execution"
    with pytest.raises(ValueError, match="Frozen.*(adapter|schema)"):
        certification.run(home, path, "cloudflare-json", target)
    dispatch.assert_not_called()
    assert not target.exists()


@pytest.mark.parametrize(
    "base",
    [
        "https://user:password@fixtures.example",
        "https://fixtures.example?api_key=private",
        "https://fixtures.example?X-Amz-Signature=private",
        "https://fixtures.example#token=private",
    ],
)
def test_fixture_credentials_never_reach_precall_receipts(inputs, tmp_path, monkeypatch, base):
    home, _ = inputs
    path = tmp_path / "plan.json"
    plan_for_inputs(home, ["browserbase-json"], path)
    dispatch = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(certification, "probe", dispatch)
    target = tmp_path / "execution"
    with pytest.raises(ValueError, match="credentials, query or fragment"):
        certification.run(home, path, "browserbase-json", target, fixture_base=base)
    assert not target.exists()
    dispatch.assert_not_called()


@pytest.mark.parametrize("full", [False, True])
def test_record_compatibility_and_full_runs_budget_the_complete_method_matrix(
    inputs, tmp_path, monkeypatch, full
):
    home, _ = inputs
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "fixture")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fixture")
    monkeypatch.setenv("ZYTE_API_KEY", "fixture")
    calls = []

    def respond(method, *args, **kwargs):
        calls.append((method, kwargs["schema"]))
        value = {field: ["value"] for field in kwargs["schema"]["properties"]}
        payload = (
            {"result": value}
            if method == "cloudflare-json"
            else {"customAttributes": {"values": value}}
        )
        return {"http_status": 200, "response": payload}

    monkeypatch.setattr(pipeline, "provider_call", respond)
    target = pipeline.probe(
        home, ["cloudflare-json", "zyte-custom"], full=full, run_dir=tmp_path / "run"
    )
    contract, rows = pipeline.load_run(target)
    assert len(calls) == len(rows) == 4
    assert contract["maximum_physical_calls"] == 24
    assert set(contract["case_ids"]) == {"book-a-0001", "job-b-0002"}
    assert {tuple(schema["properties"]) for _, schema in calls} == {("author",), ("title",)}
    with pytest.raises(ValueError, match="Physical call budget"):
        pipeline.probe(
            home,
            ["cloudflare-json", "zyte-custom"],
            full=full,
            run_dir=tmp_path / "too-small",
            maximum_physical_calls=3,
        )
    assert len(calls) == 4


def test_changed_reference_stops_before_any_provider_call(inputs, tmp_path, monkeypatch):
    home, _ = inputs
    plan = tmp_path / "plan.json"
    plan_for_inputs(home, ["cloudflare-json"], plan)
    (home / "dataset/html/book-a-0001.html").write_text("changed", encoding="utf-8")
    dispatch = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(pipeline, "provider_call", dispatch)
    with pytest.raises(ValueError, match="Dataset bytes changed"):
        certification.run(home, plan, "cloudflare-json", tmp_path / "run")
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "method", ["zyte-product", "diffbot-product", "scrapfly-product", "extruct"]
)
def test_product_only_interfaces_cannot_claim_swde_schema_support(inputs, tmp_path, method):
    home, _ = inputs
    with pytest.raises(ValueError, match="arbitrary-schema|Retired"):
        certification.create_plan(home, [method], tmp_path / "plan.json")
    assert not (tmp_path / "plan.json").exists()


@pytest.mark.parametrize(
    "prediction,hit,exact",
    [
        ({"author": ["Ada"]}, True, False),
        ({"author": ["Ada", "Bob"]}, True, True),
        ({"author": ["Eve"]}, False, False),
        ({"author": []}, False, False),
        ({"author": "Ada"}, False, False),
    ],
)
def test_multi_value_page_hit_does_not_imply_complete_extraction(prediction, hit, exact):
    truth = {"one": {"domain": "book", "site": "a", "values": {"author": ["Ada", "Bob"]}}}
    score = swde.score(truth, {"one": prediction})
    assert bool(score["site_fields"][0]["hit_pages"]) is hit
    assert bool(score["exact_field_sets"]["correct"]) is exact
    if prediction == {"author": ["Ada"]}:
        assert score["value_micro"]["recall"] == 0.5


def test_empty_gold_false_positive_stays_in_the_frozen_population():
    truth = {"one": {"domain": "job", "site": "a", "values": {"title": []}}}
    result = swde.score(truth, {"one": {"title": ["invented"]}})
    assert result["site_fields"][0]["predicted_pages"] == 1
    assert result["site_fields"][0]["hit_pages"] == 0
    assert result["value_micro"]["false_positive"] == 1
    assert result["page_hit_macro"]["site_field_groups"] == 1


@pytest.mark.parametrize("unannotated_output", [[], ["V6"], "wrong type"])
def test_explicitly_unannotated_groups_are_unavailable_for_every_method(unannotated_output):
    truth = {
        "one": {"domain": "auto", "site": "a", "values": {"model": ["Car"], "engine": []}},
        "two": {"domain": "auto", "site": "a", "values": {"model": [], "engine": []}},
    }
    predictions = {
        "one": {"model": ["Car"], "engine": unannotated_output},
        "two": {"model": ["invented"], "engine": unannotated_output},
    }
    result = swde.score(truth, predictions, unavailable_reference_groups=[("auto", "a", "engine")])
    assert result["page_hit_macro"]["cases"] == 2
    assert result["page_hit_macro"]["site_field_groups"] == 1
    assert result["site_fields"][0]["predicted_pages"] == 2
    assert result["value_micro"]["false_positive"] == 1
    assert result["exact_field_sets"]["total"] == 2
    assert result["reference_coverage"]["unscored_page_fields"] == 2
    with pytest.raises(ValueError, match="populated reference"):
        swde.score(truth, predictions, unavailable_reference_groups=[("auto", "a", "model")])
    with pytest.raises(ValueError, match="outside the declared"):
        swde.score(truth, predictions, unavailable_reference_groups=[("auto", "other", "model")])


def test_wholly_unavailable_reference_returns_no_quality_score():
    truth = {"one": {"domain": "auto", "site": "a", "values": {"engine": []}}}
    result = swde.score(
        truth, {"one": {"engine": ["V6"]}}, unavailable_reference_groups=[("auto", "a", "engine")]
    )
    assert result["page_hit_macro"]["f1"] is None
    assert result["domains"]["auto"]["f1"] is None
    assert result["value_micro"]["f1"] is None
    assert result["exact_field_sets"]["rate"] is None


@pytest.mark.parametrize(
    "source,label,expected,changed",
    [
        ("<p>AT&amp;T</p>", "AT&amp;T", "AT&T", True),
        ("<p><b>Label:</b>AT&amp;T<i>End</i>AT&amp;T</p>", "AT&amp;T", "AT&T", True),
        ("<p>AT&amp;amp;T</p>", "AT&amp;T", "AT&amp;T", False),
        ("<p>AT&amp;amp;T</p><footer>AT&amp;T</footer>", "AT&amp;T", "AT&amp;T", False),
        ("<p>Bold title</p>", "<b>Bold</b> title", "Bold title", True),
        ("<p>Other</p>", "AT&amp;T", "AT&amp;T", False),
        ("<script>AT&T</script><p>Other</p>", "AT&amp;T", "AT&amp;T", False),
        ("<p>Démon</p>", "Démon", "Démon", False),
    ],
)
def test_reference_text_changes_require_exact_source_nodes(source, label, expected, changed):
    from copy import deepcopy

    truth = {"one": {"domain": "book", "site": "a", "values": {"title": [label]}}}
    original = deepcopy(truth)
    result = swde.qualify_reference_text(truth, {"one": source.encode()})
    assert result["references"]["one"]["values"]["title"] == [expected]
    assert bool(result["changes"]) is changed
    assert truth == original
    if changed:
        from lxml import html

        tree = html.fromstring(source)
        for change in result["changes"]:
            for node in change["nodes"]:
                assert list(map(str, tree.xpath(node["xpath"]))) == [node["text"]]
        assert result["changes"][0]["source_sha256"] == pipeline.digest(source.encode())
        assert result["changes"][0]["nodes"]
    with pytest.raises(ValueError, match="exact frozen HTML"):
        swde.qualify_reference_text(truth, {})


@pytest.mark.parametrize(
    "actual,hit",
    [
        (" Démon  € 9.99 ", True),
        ("Dmon € 9.99", False),
        ("Démon 9.99", False),
        ("démon € 9.99", False),
    ],
)
def test_whitespace_matching_preserves_accents_units_and_case(actual, hit):
    truth = {"one": {"domain": "book", "site": "a", "values": {"title": ["Démon\u00a0€ 9.99"]}}}
    prediction = {"one": {"title": [actual]}}
    result = swde.score(truth, prediction, text_matching="whitespace")
    assert bool(result["site_fields"][0]["hit_pages"]) is hit
    assert prediction["one"]["title"] == [actual]


def test_partial_labels_need_field_role_adjudication_not_suffix_matching():
    truth = {"one": {"domain": "movie", "site": "amctv", "values": {"director": ["Reitherman"]}}}
    page = b"<p>Director: <a>Wolfgang Reitherman</a></p><p>Cast: <a>Bruce Reitherman</a></p>"
    result = swde.qualify_reference_text(truth, {"one": page})
    assert result["unmatched"] == [
        {"case_id": "one", "field": "director", "original": "Reitherman"}
    ]
    assert result["changes"] == []
    adjudicated = {
        "one": {"domain": "movie", "site": "amctv", "values": {"director": ["Wolfgang Reitherman"]}}
    }
    for name, hit in [("Wolfgang Reitherman", True), ("Bruce Reitherman", False)]:
        scored = swde.score(adjudicated, {"one": {"director": [name]}})
        assert bool(scored["site_fields"][0]["hit_pages"]) is hit


@pytest.mark.parametrize("method", swde.generic_methods())
def test_custom_schema_projection_retains_fields_and_invalid_types(method):
    value = {"author": ["Ada"], "year": "invalid scalar", "extra": 123}
    payload = {
        "crawl4ai-json": {"data": value},
        "scrapegraph-oss-json": {"data": value},
        "stagehand-local-json": {"data": value},
        "cloudflare-json": {"result": value},
        "firecrawl-json": {"data": {"json": value}},
        "firecrawl-parse-json": {"data": {"json": value}},
        "browserbase-json": {"statusCode": 200, "content": value},
        "oxylabs-json": {"results": [{"json": value}]},
        "scrapegraph-extract": {"json": value},
        "zyte-custom": {"customAttributes": {"values": value}},
    }[method]
    assert pipeline.normalize(method, payload, schema={"type": "object"}) == value


def test_zyte_custom_uses_original_html_and_custom_schema_without_fetching(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "test-zyte-key-only")
    schema = {
        "type": "object",
        "properties": {"author": {"type": "array", "items": {"type": "string"}}},
        "required": ["author"],
        "additionalProperties": False,
    }
    metadata = {
        "inputTokens": 150,
        "outputTokens": 12,
        "textInputTokensBeforeTruncation": 140,
        "textInputTokens": 130,
        "maxInputTokens": 16384,
    }
    requests = []

    def send(url, *, json_body, **kwargs):
        requests.append(json_body)
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps(
                {"customAttributes": {"values": {"author": ["Ada"]}, "metadata": metadata}}
            ),
        )

    monkeypatch.setattr(pipeline, "request", send)
    row = pipeline.provider_call(
        "zyte-custom", "<p>Ada</p>", "https://source.example/book", schema=schema
    )
    assert requests == [
        {
            "url": "https://source.example/book",
            "pageContent": True,
            "pageContentOptions": {"extractFrom": "userHtml", "userHtml": "<p>Ada</p>"},
            "customAttributes": schema["properties"],
        }
    ]
    assert row["request"]["input"] == "html"
    assert row["request"]["schema"] == schema
    assert row["provider_usage"]["custom_attributes"] == metadata
    assert row["elapsed_seconds"] >= 0
    assert pipeline.normalize("zyte-custom", row["response"], schema=schema) == {"author": ["Ada"]}


def test_zyte_custom_native_error_is_not_a_successful_empty_extraction():
    response = {
        "customAttributes": {"values": {}, "metadata": {"error": "extraction/schema-size-exceeded"}}
    }
    assert pipeline.normalize("zyte-custom", response, schema={"type": "object"}) is None


def test_zyte_custom_cannot_run_without_an_explicit_schema(monkeypatch):
    monkeypatch.setenv("ZYTE_API_KEY", "test-zyte-key-only")
    request = Mock(side_effect=AssertionError("must not dispatch"))
    monkeypatch.setattr(pipeline, "request", request)
    with pytest.raises(ValueError, match="object schema"):
        pipeline.provider_call("zyte-custom", "<p>Ada</p>", "https://source.example/book")
    request.assert_not_called()


@pytest.mark.parametrize("payload", [[], ["unexpected"], "not an object", None])
def test_zyte_custom_keeps_malformed_native_envelopes(monkeypatch, payload):
    monkeypatch.setenv("ZYTE_API_KEY", "test-zyte-key-only")
    monkeypatch.setattr(
        pipeline,
        "request",
        lambda *a, **kw: SimpleNamespace(status=200, headers={}, text=lambda: json.dumps(payload)),
    )
    row = pipeline.provider_call(
        "zyte-custom",
        "<p>Ada</p>",
        "https://source.example/book",
        schema={"type": "object", "properties": {}},
    )
    assert row["response"] == payload
    assert pipeline.normalize("zyte-custom", payload, schema={"type": "object"}) is None


def test_firecrawl_parse_upload_preserves_html_and_requests_native_json(monkeypatch):
    from email.parser import BytesParser
    from email.policy import default

    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-firecrawl-key-only")
    html = "<html><p>作者：Ada</p></html>"
    schema = {
        "type": "object",
        "properties": {"author": {"type": "array", "items": {"type": "string"}}},
        "required": ["author"],
    }
    captured = []

    def request(url, *, body, headers, **kwargs):
        assert "json_body" not in kwargs
        assert url == "https://api.firecrawl.dev/v2/parse"
        message = BytesParser(policy=default).parsebytes(
            ("Content-Type: " + headers["Content-Type"] + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
            + body
        )
        parts = {p.get_param("name", header="content-disposition"): p for p in message.iter_parts()}
        assert parts["file"].get_filename() == "snapshot.html"
        assert parts["file"].get_payload(decode=True) == html.encode("utf-8")
        captured.append(json.loads(parts["options"].get_payload(decode=True)))
        return SimpleNamespace(
            status=200,
            headers={},
            text=lambda: json.dumps(
                {
                    "success": True,
                    "data": {"json": {"author": ["Ada"]}, "metadata": {"creditsUsed": 5}},
                }
            ),
        )

    monkeypatch.setattr(pipeline, "request", request)
    row = pipeline.provider_call(
        "firecrawl-parse-json", html, "https://source.example/book", schema=schema
    )
    assert captured == [
        {
            "formats": [{"type": "json", "schema": schema}],
            "onlyMainContent": False,
            "timeout": 120000,
        }
    ]
    assert row["request"]["input"] == "html"
    assert row["provider_usage"]["parse_metadata"]["creditsUsed"] == 5
    assert pipeline.normalize("firecrawl-parse-json", row["response"], schema=schema) == {
        "author": ["Ada"]
    }


@pytest.mark.parametrize("method", ["zyte-custom", "firecrawl-parse-json"])
def test_schema_only_interfaces_are_rejected_before_any_dispatch(tmp_path, monkeypatch, method):
    from harness.web_extract.example import create

    home = create(tmp_path / "example")
    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    with pytest.raises(ValueError, match="dataset-provided custom schema"):
        certification.create_plan(home, [method], tmp_path / "plan.json")
    with pytest.raises(ValueError, match="dataset-provided custom schema"):
        pipeline.probe(home, [method], full=True, run_dir=tmp_path / "calls")
    call.assert_not_called()


@pytest.mark.parametrize(
    "method", ["extruct", "zyte-product", "diffbot-product", "scrapfly-product"]
)
def test_product_only_probe_rejects_record_schema_before_dispatch(
    inputs, tmp_path, monkeypatch, method
):
    home, _ = inputs
    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    run = tmp_path / "probe"
    with pytest.raises(ValueError, match="require methods supporting a custom schema"):
        pipeline.probe(home, [method], full=True, run_dir=run)
    call.assert_not_called()
    assert not (run / "contract.json").exists()


def test_missing_schema_dependency_fails_before_paid_dispatch(inputs, monkeypatch, tmp_path):
    import builtins

    home, _ = inputs
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "jsonschema":
            raise ModuleNotFoundError("jsonschema unavailable")
        return original(name, *args, **kwargs)

    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModuleNotFoundError, match="jsonschema unavailable"):
        pipeline.probe(home, ["cloudflare-json"], full=True, run_dir=tmp_path / "execution")
    call.assert_not_called()
    assert not (tmp_path / "execution/inflight.json").exists()


def test_registered_adapter_dispatch_does_not_depend_on_dataset_name(inputs, monkeypatch, tmp_path):
    home, _ = inputs
    manifest = pipeline.read_json(home / "dataset/manifest.json")
    manifest["benchmark"] = "another-schema-cohort"
    pipeline.save_json(home / "dataset/manifest.json", manifest)
    monkeypatch.setitem(
        dataset.BENCHMARKS, "another-schema-cohort", dict(dataset.BENCHMARKS["swde"])
    )
    plan = plan_for_inputs(home, ["cloudflare-json"], tmp_path / "registered.json")
    assert plan["case_count"] == 2
    assert plan["dataset_registry_sha256"] == dataset.registry_sha256()
    scores = pipeline.score_rows(
        home,
        pipeline.read_json(home / "dataset/dataset/ground-truth.json"),
        [
            {"method": "cloudflare-json", "case_id": cid, "http_status": 422}
            for cid in pipeline.read_json(home / "dataset/dataset/ground-truth.json")
        ],
        ["cloudflare-json"],
    )
    assert "page_hit_macro" in scores["cloudflare-json"]


def test_swde_prepare_cannot_reuse_a_valid_other_benchmark(tmp_path, monkeypatch):
    from harness.web_extract.example import create

    home = create(tmp_path / "product")
    monkeypatch.setitem(
        dataset.BENCHMARKS,
        "zyte",
        {
            **dataset.BENCHMARKS["zyte"],
            "profile": str(home / "profile.json"),
        },
    )
    archive = tmp_path / "archive.bin"
    archive.write_bytes(b"pinned archive")
    card = pipeline.read_json(swde.CARD)
    card["source"]["archive_sha256"] = pipeline.digest(archive.read_bytes())
    card_path = tmp_path / "swde-card.json"
    pipeline.save_json(card_path, card)
    monkeypatch.setattr(swde, "CARD", card_path)
    before = (home / "dataset/manifest.json").read_bytes()
    with pytest.raises(ValueError, match="selected pinned cohort|Pinned"):
        swde.prepare(home, archive)
    assert (home / "dataset/manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    "unsafe", ["source", "source/archives", "source/archives/auto.7z", "source/markuplm-eval.py"]
)
def test_swde_prepare_rejects_linked_storage_before_download_or_write(
    tmp_path, monkeypatch, unsafe
):
    import io
    import tarfile

    from harness.web_fetch.providers.http_client import HttpResponse

    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (outside / "auto.7z").write_bytes(b"outside sentinel")
    linked = home / unsafe
    linked.parent.mkdir(parents=True, exist_ok=True)
    directory = unsafe in ("source", "source/archives")
    linked.symlink_to(outside if directory else outside / "auto.7z", target_is_directory=directory)
    archive = tmp_path / "archive.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        item = tarfile.TarInfo("source/auto.7z")
        item.size = 3
        bundle.addfile(item, io.BytesIO(b"new"))
    scorer = b"pinned scorer"
    card = pipeline.read_json(swde.CARD)
    card["source"].update(
        archive_sha256=pipeline.digest(archive.read_bytes()), scorer_sha256=pipeline.digest(scorer)
    )
    card_path = tmp_path / "card.json"
    pipeline.save_json(card_path, card)
    monkeypatch.setattr(swde, "CARD", card_path)
    download = Mock(return_value=HttpResponse(200, "https://source.example", {}, scorer))
    monkeypatch.setattr("harness.web_fetch.providers.http_client.request", download)
    with pytest.raises(ValueError, match="symlink"):
        swde.prepare(home, archive)
    assert (outside / "auto.7z").read_bytes() == b"outside sentinel"
    assert not (home / "dataset").exists()
    download.assert_not_called()


@pytest.mark.parametrize("wrong_pin", [False, True])
def test_clean_prepare_downloads_pinned_scorer_and_recovers_after_partial_extraction(
    tmp_path, monkeypatch, wrong_pin
):
    import io
    import tarfile

    from harness.web_fetch.providers.http_client import HttpResponse

    home = tmp_path / "clean"
    archive = tmp_path / "source.tar.gz"
    scorer = b"# synthetic original evaluator\n"
    with tarfile.open(archive, "w:gz") as bundle:
        for name in [
            *[d + ".7z" for d in swde.DOMAINS],
            "groundtruth.7z",
            "readme.txt",
            "license.json",
        ]:
            raw = b"synthetic"
            item = tarfile.TarInfo("source/" + name)
            item.size = len(raw)
            bundle.addfile(item, io.BytesIO(raw))
    card = tmp_path / "card.json"
    pipeline.save_json(
        card,
        {
            "source": {
                "archive_sha256": pipeline.digest(archive.read_bytes()),
                "scorer_sha256": pipeline.digest(scorer),
                "scorer_download_url": "https://source.example/pinned-evaluator",
            },
            "dataset": {
                "commit": "a" * 40,
                "cases": 80,
                "files": {"evaluate.py": "0" * 64 if wrong_pin else pipeline.digest(scorer)},
            },
            "selection": {"pages_per_site": 1},
        },
    )
    monkeypatch.setattr(swde, "CARD", card)
    monkeypatch.setitem(
        dataset.BENCHMARKS, "swde", {"profile": str(card), "module": "harness.web_extract.swde"}
    )
    download = Mock(
        return_value=HttpResponse(200, "https://source.example/pinned-evaluator", {}, scorer)
    )
    monkeypatch.setattr("harness.web_fetch.providers.http_client.request", download)
    fail = [True]

    def unpack(source, target):
        if source.stem == "groundtruth":
            for domain in swde.DOMAINS:
                for i in range(10):
                    path = target / "groundtruth" / f"{domain}-site{i}-value.txt"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("header\nstats\n0001\t1\tA value\n", encoding="utf-8")
        else:
            if source.stem == "book" and fail[0]:
                fail[0] = False
                raise RuntimeError("Synthetic interrupted archive extraction")
            for i in range(10):
                path = target / f"{source.stem}-site{i}(1)" / "0001.htm"
                path.parent.mkdir(parents=True)
                path.write_text(
                    f'<base href="https://{source.stem}.example/{i}"><p>A value</p>',
                    encoding="utf-8",
                )

    monkeypatch.setattr(swde, "_extract", unpack)
    with pytest.raises(RuntimeError, match="interrupted archive extraction"):
        swde.prepare(home, archive)
    assert not (home / "dataset").exists()
    if wrong_pin:
        with pytest.raises(ValueError, match="Pinned source binding"):
            swde.prepare(home, archive)
        assert not (home / "dataset").exists()
        return
    result = swde.prepare(home, archive)
    assert result["cases"] == 80
    assert len(list((home / "dataset/html").glob("*.html"))) == 80
    assert (home / "dataset/evaluate.py").read_bytes() == scorer
    assert swde.prepare(home, archive)["cases"] == 80
    assert download.call_count == 1


def test_clean_prepare_rejects_wrong_scorer_before_extracting(tmp_path, monkeypatch):
    home = tmp_path / "clean"
    source = tmp_path / "source.tar.gz"
    source.write_bytes(b"archive")
    scorer = home / "source/markuplm-eval.py"
    scorer.parent.mkdir(parents=True)
    scorer.write_bytes(b"wrong evaluator")
    card = tmp_path / "card.json"
    pipeline.save_json(
        card,
        {
            "source": {
                "archive_sha256": pipeline.digest(source.read_bytes()),
                "scorer_sha256": pipeline.digest(b"expected"),
            }
        },
    )
    monkeypatch.setattr(swde, "CARD", card)
    extract = Mock()
    monkeypatch.setattr(swde, "_extract", extract)
    with pytest.raises(ValueError, match="evaluator changed"):
        swde.prepare(home, source)
    extract.assert_not_called()
    assert not (home / "dataset").exists()


def test_product_interfaces_reject_other_field_families_before_reading_artifacts(inputs, tmp_path):
    from harness.web_extract import report

    home, _ = inputs
    target = tmp_path / "not-created"
    missing = tmp_path / "absent-input"
    with pytest.raises(ValueError, match="requires product references"):
        report.build(target, missing, home=home)
    assert not target.exists()


def test_record_review_missing_content_fails_before_creating_work(inputs, tmp_path):
    from harness.web_extract import review

    home, _ = inputs
    target = tmp_path / "not-created"
    with pytest.raises(FileNotFoundError):
        review.prepare(home, tmp_path / "absent-input", target)
    assert not target.exists()


@pytest.mark.parametrize("change", ["none", "missing", "before", "during", "certificate"])
def test_record_repair_binds_adapter_before_resume_after_calls_and_on_read(
    inputs, tmp_path, monkeypatch, change
):
    from harness.web_extract import repair

    home, _ = inputs
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "fixture")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fixture")
    monkeypatch.setattr(
        pipeline,
        "provider_call",
        Mock(
            side_effect=[
                {"http_status": 500, "response": {"error": "temporary origin error"}},
                {"http_status": 200, "response": {"result": {"title": ["Engineer"]}}},
            ]
        ),
    )
    plan = tmp_path / "parent-plan.json"
    plan_for_inputs(home, ["cloudflare-json"], plan)
    parent = tmp_path / "parent"
    certification.run(home, plan, "cloudflare-json", parent)
    content = tmp_path / "repair-content.json"
    pipeline.save_json(content, {"evidence_runs": [{"run": str(parent)}]})
    path = tmp_path / "repair-plan.json"
    frozen = repair.create_plan(home, content, path, {}, None)
    bindings = dataset.task_bindings(home)
    changed = {**bindings, "task_adapter_sha256": "f" * 64}
    if change == "missing":
        frozen.pop("task_adapter_sha256")
        pipeline.save_json(path, frozen)
    if change == "before":
        monkeypatch.setattr(repair, "task_bindings", lambda _: changed)

    def respond(*args, **kwargs):
        if change == "during":
            monkeypatch.setattr(repair, "task_bindings", lambda _: changed)
        return {"http_status": 200, "response": {"result": {"author": ["Ada"]}}}

    dispatch = Mock(side_effect=respond)
    monkeypatch.setattr(pipeline, "provider_call", dispatch)
    monkeypatch.setattr(repair, "load_runtime_env", lambda: None)
    target = tmp_path / "repair"
    if change in ("missing", "before", "during"):
        with pytest.raises(ValueError, match="repair task adapter|Repair task adapter"):
            repair.run(home, path, "cloudflare-json", target)
        assert dispatch.call_count == (1 if change == "during" else 0)
        assert not (target / "certificate.json").exists()
        return
    repair.run(home, path, "cloudflare-json", target)
    repair.run(home, path, "cloudflare-json", target)
    assert dispatch.call_count == 1
    contract = pipeline.read_json(target / "contract.json")
    certificate = pipeline.read_json(target / "certificate.json")
    assert contract["schema"] == "failed-extraction-repair-run/2"
    for key, value in bindings.items():
        assert contract[key] == certificate[key] == value
    if change == "certificate":
        certificate["task_adapter_sha256"] = "f" * 64
        pipeline.save_json(target / "certificate.json", certificate)
    revision = {
        "path": str(target),
        "sha256": pipeline.digest((target / "certificate.json").read_bytes()),
    }
    if change == "certificate":
        with pytest.raises(ValueError, match="task adapter or dataset binding"):
            repair.verify_repair(home, parent, revision)
    else:
        _, rows = repair.load_selection(home, {"run": str(parent), "repair": [revision]})
        by_case = {row["case_id"]: row for row in rows}
        assert by_case["book-a-0001"]["normalized_fields"] == {"author": ["Ada"]}
        assert by_case["job-b-0002"]["normalized_fields"] == {"title": ["Engineer"]}


def test_invalid_schema_is_rejected_before_any_paid_dispatch(inputs, monkeypatch, tmp_path):
    from jsonschema.exceptions import SchemaError

    home, schemas = inputs
    schemas["book"]["properties"]["author"]["type"] = "invalid-type"
    path = home / "dataset/schemas.json"
    pipeline.save_json(path, schemas)
    manifest = pipeline.read_json(home / "dataset/manifest.json")
    manifest["files"]["schemas.json"] = {
        "sha256": pipeline.digest(path.read_bytes()),
        "bytes": path.stat().st_size,
    }
    pipeline.save_json(home / "dataset/manifest.json", manifest)
    card = pipeline.read_json(swde.CARD)
    card["dataset"]["inventory_sha256"] = pipeline.digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    pipeline.save_json(swde.CARD, card)
    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    with pytest.raises(SchemaError):
        pipeline.probe(home, ["cloudflare-json"], full=True, run_dir=tmp_path / "execution")
    call.assert_not_called()


def test_completed_cohort_rejects_changed_fixture_origin_without_calls(
    inputs, tmp_path, monkeypatch
):
    home, _ = inputs
    monkeypatch.setattr(
        pipeline,
        "provider_call",
        lambda *a, **k: {
            "http_status": 200,
            "state": "response_received",
            "response": {"result": {}},
        },
    )
    monkeypatch.setattr(
        pipeline,
        "qualify_fixture",
        lambda home, base, cid, **kw: {
            "case_id": cid,
            "url": base + "/" + cid,
            "sha256": pipeline.digest((home / "dataset/html" / (cid + ".html")).read_bytes()),
            "http_status": 200,
        },
    )
    plan, run = tmp_path / "plan.json", tmp_path / "run"
    plan_for_inputs(home, ["cloudflare-json"], plan)
    certification.run(home, plan, "cloudflare-json", run, fixture_base="https://snapshot-a.example")
    monkeypatch.setattr(
        pipeline, "provider_call", lambda *a, **k: pytest.fail("Completed calls repeated")
    )
    certification.run(home, plan, "cloudflare-json", run, fixture_base="https://snapshot-a.example")
    with pytest.raises(ValueError, match="fixture_base|frozen public snapshot"):
        certification.run(
            home, plan, "cloudflare-json", run, fixture_base="https://snapshot-b.example"
        )


@pytest.mark.parametrize("source_revision_run", [True], indirect=True)
def test_annotation_archive_cannot_be_rebound_with_external_coverage(source_revision_run, tmp_path):
    home, run, authority, _ = source_revision_run
    archive = home / "source/archives/groundtruth.7z"
    archive.write_bytes(archive.read_bytes() + b"\0" * 512)
    revision = pipeline.read_json(authority)
    proof = Path(revision["coverage_authority"]["path"])
    coverage = pipeline.read_json(proof)
    coverage["original_annotation_archive_sha256"] = pipeline.digest(archive.read_bytes())
    pipeline.save_json(proof, coverage)
    revision["coverage_authority"]["sha256"] = pipeline.digest(proof.read_bytes())
    pipeline.save_json(authority, revision)
    with pytest.raises(ValueError, match="pinned source"):
        pipeline.score_run(home, run, reference_revision=authority, output=tmp_path / "scores.json")


def test_record_assessment_preserves_dataset_schema_in_report_input(source_revision_run, tmp_path):
    from harness.web_extract.report import cohort_source

    home, run, _, _ = source_revision_run
    assessment = tmp_path / "assessment.json"
    pipeline.score_run(home, run, reproject=True, output=assessment)
    plan = tmp_path / "original-plan.json"
    truth = pipeline.read_json(home / "dataset/dataset/ground-truth.json")
    _, rows, _ = cohort_source(
        run,
        {
            "plan": str(plan),
            "plan_sha256": pipeline.digest(plan.read_bytes()),
            "assessment": {
                "path": str(assessment),
                "sha256": pipeline.digest(assessment.read_bytes()),
            },
        },
        home,
        truth,
    )
    assert rows[0]["normalized_fields"] == {"author": ["Ada Lovelace"]}
    assert rows[1]["normalized_fields"] == {"title": ["Engineer & Co"]}


def test_historical_projection_reproduces_without_rewriting_creation_identity(
    source_revision_run, tmp_path
):
    home, run, _, _ = source_revision_run
    assessment = tmp_path / "historical-assessment.json"
    saved = pipeline.score_run(home, run, reproject=True, output=assessment)
    saved["projection_revision"]["pipeline_sha256"] = "a" * 64
    pipeline.save_json(assessment, saved)
    frozen = assessment.read_bytes()
    actual = pipeline.score_run(home, run, reproject=True, output=assessment, verify_existing=True)
    assert actual == saved
    assert assessment.read_bytes() == frozen
    saved["scores"] = {}
    pipeline.save_json(assessment, saved)
    with pytest.raises(ValueError, match="Existing score differs"):
        pipeline.score_run(home, run, reproject=True, output=assessment, verify_existing=True)


def test_record_adjudication_rejects_another_review_of_same_sources(
    record_audit_material, tmp_path
):
    import shutil

    from harness.web_extract import review

    home, content, works, _, authority = record_audit_material
    other = tmp_path / "other-review"
    shutil.copytree(works[0], other)
    path = other / "reviews/000.json"
    payload = pipeline.read_json(path)
    for row in payload["cases"]:
        for finding in row["reference"].values():
            finding["reason"] = "Another independently valid claim about these source bytes"
    pipeline.save_json(path, payload)
    adjudication = tmp_path / "other-adjudication"
    manifest = review.prepare_adjudication(other, adjudication, tmp_path / "rules.txt")
    indexed = {row["case_id"]: row for row in payload["cases"]}
    for entry in manifest["packets"]:
        packet = review.read_packet(Path(entry["path"]))
        pipeline.save_json(
            adjudication / "reviews" / Path(entry["path"]).name,
            {"cases": [{**indexed[c["case_id"]], "outputs": []} for c in packet["cases"]]},
        )
    pipeline.save_json(adjudication / "reviewer.json", {"model": "synthetic"})
    with pytest.raises(ValueError, match="different predecessor reviews"):
        review.assemble_record(
            home,
            content,
            works,
            tmp_path / "wrong.json",
            profile=swde.CARD,
            adjudications=[adjudication],
            reference_revision=authority,
        )


@pytest.mark.parametrize("key", ["reason", "source_locator", "source_quote"])
def test_record_review_rejects_whitespace_evidence(record_audit_material, key):
    from harness.web_extract import review

    _, _, works, _, _ = record_audit_material
    packet = review.read_packet(works[0] / "packets/000.json")
    payload = pipeline.read_json(works[0] / "reviews/000.json")
    finding = next(iter(payload["cases"][0]["reference"].values()))
    finding[key] = " \n\t"
    with pytest.raises(ValueError, match="source-grounded|nonblank|Nonblank|schema"):
        review.validate(payload, packet)


def test_nonempty_reference_absence_is_adjudicated_before_record_assembly(
    record_audit_material, tmp_path
):
    from harness.web_extract import review

    home, content, works, original_adjudication, authority = record_audit_material
    review_path = works[0] / "reviews/000.json"
    payload = pipeline.read_json(review_path)
    payload["cases"][0]["reference"]["author"]["status"] = "absent"
    pipeline.save_json(review_path, payload)
    adjudication = tmp_path / "absence-adjudication"
    manifest = review.prepare_adjudication(
        works[0], adjudication, tmp_path / "rules.txt", additional_priors=[works[1]]
    )
    assert manifest["case_count"] == 2
    resolutions = {
        c["case_id"]: c
        for c in pipeline.read_json(original_adjudication / "reviews/000.json")["cases"]
    }
    for entry in manifest["packets"]:
        packet = review.read_packet(Path(entry["path"]))
        pipeline.save_json(
            adjudication / "reviews" / Path(entry["path"]).name,
            {"cases": [resolutions[c["case_id"]] for c in packet["cases"]]},
        )
    pipeline.save_json(adjudication / "reviewer.json", {"model": "synthetic"})
    result = review.assemble_record(
        home,
        content,
        works,
        tmp_path / "complete-absence.json",
        profile=swde.CARD,
        adjudications=[adjudication],
        reference_revision=authority,
    )
    assert result["unresolved"] == 0


def test_record_preview_binds_host_output_to_exact_local_inventory(inputs, tmp_path, monkeypatch):
    from harness.web_extract import report, snapshots

    home, _ = inputs
    version, cases = snapshots.inventory(home)
    base = "https://snapshot.example/frozen"
    receipt = {
        "mode": "dataset_manifest",
        "manifest_url": "https://snapshot.example/snapshots.json",
        "manifest_sha256": "a" * 64,
        "huggingface": {"repository": "owner/data", "revision": "b" * 40},
        "html_inventory_sha256": version,
        "case_count": len(cases),
        "fixture_base": base,
    }
    saved = tmp_path / "host-output.json"
    pipeline.save_json(
        saved, {"snapshot_manifest": receipt, "cloudflare": {"account_id": "private-account"}}
    )
    model = {
        "snapshot_base": base,
        "preview_snapshot": {"path": str(saved), "sha256": pipeline.digest(saved.read_bytes())},
    }
    monkeypatch.setattr(snapshots, "check_manifest", lambda *a: receipt)
    assert report.record_preview(home, model, local_example=False) == receipt
    model["snapshot_base"] = "https://another-dataset.example/frozen"
    with pytest.raises(ValueError, match="changed"):
        report.record_preview(home, model, local_example=False)
    model["snapshot_base"] = base
    receipt["html_inventory_sha256"] = "f" * 64
    pipeline.save_json(saved, {"snapshot_manifest": receipt})
    model["preview_snapshot"]["sha256"] = pipeline.digest(saved.read_bytes())
    with pytest.raises(ValueError, match="differs from selected inputs"):
        report.record_preview(home, model, local_example=False)


def test_record_adjudication_accepts_hash_bound_readonly_review_projection(
    record_audit_material, tmp_path
):
    import shutil

    from harness.web_extract import review

    home, content, works, _, authority = record_audit_material
    projected = tmp_path / "readonly-projection"
    shutil.copytree(works[0], projected)
    manifest = pipeline.read_json(projected / "manifest.json")
    for entry in manifest["packets"]:
        original_review = works[0] / "reviews" / Path(entry["path"]).name
        # The projection explicitly retains the selected original review identity.
        import os

        root = review.manifest_root(projected, manifest)
        manifest["bindings"][os.path.relpath(original_review, root)] = pipeline.digest(
            original_review.read_bytes()
        )
    pipeline.save_json(projected / "manifest.json", manifest)
    adjudication = tmp_path / "projected-adjudication"
    selected = review.prepare_adjudication(projected, adjudication, tmp_path / "rules.txt")
    originals = {
        c["case_id"]: c for c in pipeline.read_json(works[0] / "reviews/000.json")["cases"]
    }
    for entry in selected["packets"]:
        packet = review.read_packet(Path(entry["path"]))
        pipeline.save_json(
            adjudication / "reviews" / Path(entry["path"]).name,
            {"cases": [{**originals[c["case_id"]], "outputs": []} for c in packet["cases"]]},
        )
    pipeline.save_json(adjudication / "reviewer.json", {"model": "synthetic"})
    result = review.assemble_record(
        home,
        content,
        works,
        tmp_path / "projected-complete.json",
        profile=swde.CARD,
        adjudications=[adjudication],
        reference_revision=authority,
    )
    assert result["unresolved"] == 0


@pytest.mark.parametrize(
    "method",
    [
        "cloudflare-json",
        "zyte-custom",
        "browserbase-json",
        "oxylabs-json",
        "scrapegraph-extract",
        "firecrawl-parse-json",
        "firecrawl-json",
    ],
)
def test_shared_instructions_reach_every_native_request_and_its_receipt(monkeypatch, method):
    from copy import deepcopy
    from email.parser import BytesParser
    from email.policy import default

    schema = {
        "type": "object",
        "properties": {
            "price": {"type": "array", "items": {"type": "string"}, "description": "Current price"}
        },
        "required": ["price"],
    }
    original = deepcopy(schema)
    for key in pipeline.available_methods()[method]["keys"]:
        monkeypatch.setenv(key, "synthetic")
    captured = []

    def send(url, **kwargs):
        if "body" in kwargs:
            envelope = BytesParser(policy=default).parsebytes(
                ("Content-Type: " + kwargs["headers"]["Content-Type"] + "\r\n\r\n").encode()
                + kwargs["body"]
            )
            options = next(
                part
                for part in envelope.iter_parts()
                if part.get_param("name", header="content-disposition") == "options"
            )
            body = json.loads(options.get_payload(decode=True))
            fields = body["formats"][0]["schema"]["properties"]
        else:
            body = kwargs["json_body"]
            if method == "zyte-custom":
                fields = body["customAttributes"]
            elif method == "cloudflare-json":
                fields = body["response_format"]["json_schema"]["properties"]
            elif method == "oxylabs-json":
                fields = body["json"]["schema"]["properties"]
            elif method == "firecrawl-json":
                fields = body["formats"][0]["schema"]["properties"]
            else:
                fields = body["schema"]["properties"]
        captured.append(fields)
        return SimpleNamespace(status=200, headers={}, text=lambda: "{}")

    monkeypatch.setattr(pipeline, "request", send)
    row = pipeline.provider_call(
        method,
        "<p>12</p>",
        "https://source.example",
        "https://frozen.example/case",
        schema=schema,
        prompt=pipeline.RECORD_INSTRUCTIONS,
    )
    assert captured[0]["price"]["description"] == "Current price " + pipeline.RECORD_INSTRUCTIONS
    assert row["request"]["schema"]["properties"] == captured[0]
    assert row["request"]["instructions"]["text"] == pipeline.RECORD_INSTRUCTIONS
    assert row["request"]["standalone_prompt"] == (
        "Extract the fields defined by the schema." if method == "scrapegraph-extract" else None
    )
    assert schema == original


def test_upload_only_record_method_can_be_planned_through_the_public_cli(inputs, tmp_path):
    from harness.web_extract.cli import main

    home, _ = inputs
    output = tmp_path / "upload-plan.json"
    main(
        ["--home", str(home), "plan", "--methods", "firecrawl-parse-json", "--output", str(output)]
    )
    assert "public_snapshots" not in pipeline.read_json(output)
    assert pipeline.read_json(output)["participants"]["firecrawl-parse-json"]["input"] == "html"


@pytest.mark.parametrize("invalid", [None, False, 0, "", {}])
def test_explicit_falsey_native_values_are_attempts_not_missing_fields(invalid):
    truth = {
        cid: {"domain": "book", "site": "a", "values": {"title": ["Example"]}}
        for cid in ["one", "two"]
    }
    baseline = swde.score(truth, {"one": {"title": ["Example"]}, "two": {}})
    explicit = swde.score(truth, {"one": {"title": ["Example"]}, "two": {"title": invalid}})
    empty = swde.score(truth, {"one": {"title": ["Example"]}, "two": {"title": []}})
    assert baseline == empty
    assert explicit["site_fields"][0]["predicted_pages"] == 2
    assert explicit["value_micro"]["false_positive"] == 1
    assert explicit["value_micro"]["precision"] == 0.5


@pytest.mark.parametrize("method", ["cloudflare-json", "browserbase-json", "zyte-custom"])
def test_url_capable_interfaces_cannot_plan_an_implicit_upload_cohort(inputs, tmp_path, method):
    home, _ = inputs
    with pytest.raises(ValueError, match="require --snapshots"):
        certification.create_plan(home, [method], tmp_path / "invalid-plan.json")
    assert not (tmp_path / "invalid-plan.json").exists()


def test_description_limit_fails_at_planning_before_calls(inputs, tmp_path):
    home, schemas = inputs
    schemas["book"]["properties"]["author"]["description"] = "x" * 301
    pipeline.save_json(home / "dataset/schemas.json", schemas)
    # Keep the synthetic inventory valid after extending its schema.
    manifest = pipeline.read_json(home / "dataset/manifest.json")
    raw = (home / "dataset/schemas.json").read_bytes()
    manifest["files"]["schemas.json"] = {"sha256": pipeline.digest(raw), "bytes": len(raw)}
    pipeline.save_json(home / "dataset/manifest.json", manifest)
    card = pipeline.read_json(swde.CARD)
    card["dataset"]["inventory_sha256"] = pipeline.digest(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    )
    pipeline.save_json(swde.CARD, card)
    with pytest.raises(ValueError, match="300-character"):
        plan_for_inputs(home, ["zyte-custom"], tmp_path / "too-long.json")
    assert not (tmp_path / "too-long.json").exists()


def test_record_repair_sends_the_parents_custom_frozen_instructions(inputs, tmp_path, monkeypatch):
    from harness.web_extract import repair

    home, _ = inputs
    plan = tmp_path / "parent-plan.json"
    plan_for_inputs(home, ["cloudflare-json"], plan)
    parent = tmp_path / "parent"
    monkeypatch.setattr(
        pipeline,
        "provider_call",
        Mock(
            side_effect=[
                {"http_status": 500, "response": {"error": "temporary failure"}},
                {"http_status": 200, "response": {"result": {"title": ["Engineer"]}}},
            ]
        ),
    )
    certification.run(home, plan, "cloudflare-json", parent)
    contract = pipeline.read_json(parent / "contract.json")
    contract["extraction_instructions"] = "Return the parent policy verbatim."
    pipeline.save_json(parent / "contract.json", contract)
    manifest = pipeline.read_json(parent / "manifest.json")
    manifest["contract_sha256"] = pipeline.digest((parent / "contract.json").read_bytes())
    pipeline.save_json(parent / "manifest.json", manifest)
    certificate = pipeline.read_json(parent / "certificate.json")
    certificate["manifest_sha256"] = pipeline.digest((parent / "manifest.json").read_bytes())
    pipeline.save_json(parent / "certificate.json", certificate)
    content = tmp_path / "selection.json"
    pipeline.save_json(content, {"evidence_runs": [{"run": str(parent)}]})
    profile = tmp_path / "repair-plan.json"
    repair.create_plan(home, content, profile, {}, None)
    dispatch = Mock(return_value={"http_status": 200, "response": {"result": {"author": ["Ada"]}}})
    monkeypatch.setattr(pipeline, "provider_call", dispatch)
    output = tmp_path / "repaired"
    repair.run(home, profile, "cloudflare-json", output)
    assert dispatch.call_count == 1
    assert dispatch.call_args.kwargs["prompt"] == contract["extraction_instructions"]
    assert (
        pipeline.read_json(output / "contract.json")["extraction_instructions"]
        == contract["extraction_instructions"]
    )


@pytest.mark.parametrize(
    "missing", ["extraction_instructions", "extraction_instruction_delivery", "historical_parent"]
)
def test_old_record_repair_plan_cannot_bypass_current_instruction_gate(
    inputs, tmp_path, monkeypatch, missing
):
    from harness.web_extract import repair

    home, _ = inputs
    profile = tmp_path / "parent-plan.json"
    plan_for_inputs(home, ["cloudflare-json"], profile)
    parent = tmp_path / "parent"
    monkeypatch.setattr(
        pipeline, "provider_call", Mock(return_value={"http_status": 500, "response": {}})
    )
    certification.run(home, profile, "cloudflare-json", parent)
    content = tmp_path / "selection.json"
    pipeline.save_json(content, {"evidence_runs": [{"run": str(parent)}]})
    target = tmp_path / "repair-plan.json"
    plan = repair.create_plan(home, content, target, {}, None)
    participant = plan["participants"]["cloudflare-json"]
    if missing == "historical_parent":
        contract = pipeline.read_json(parent / "contract.json")
        contract.pop("extraction_instruction_delivery")
        pipeline.save_json(parent / "contract.json", contract)
        manifest = pipeline.read_json(parent / "manifest.json")
        manifest["contract_sha256"] = pipeline.digest((parent / "contract.json").read_bytes())
        pipeline.save_json(parent / "manifest.json", manifest)
        certificate = pipeline.read_json(parent / "certificate.json")
        certificate["manifest_sha256"] = pipeline.digest((parent / "manifest.json").read_bytes())
        pipeline.save_json(parent / "certificate.json", certificate)
        participant["parent_manifest_sha256"] = certificate["manifest_sha256"]
        participant["parent_certificate_sha256"] = pipeline.digest(
            (parent / "certificate.json").read_bytes()
        )
        participant.pop("extraction_instruction_delivery")
    else:
        participant.pop(missing)
    pipeline.save_json(target, plan)
    call = Mock()
    monkeypatch.setattr(pipeline, "provider_call", call)
    destination = tmp_path / "must-not-dispatch"
    with pytest.raises(ValueError, match="current instruction delivery"):
        repair.run(home, target, "cloudflare-json", destination)
    call.assert_not_called()
    assert not (destination / "contract.json").exists()


def test_mixed_plan_keeps_parse_uploads_and_url_calls_on_the_same_frozen_html(
    inputs, tmp_path, monkeypatch
):
    home, _ = inputs
    monkeypatch.setenv("FIRECRAWL_API_KEY", "synthetic")
    profile = tmp_path / "mixed-plan.json"
    plan = plan_for_inputs(home, ["cloudflare-json", "firecrawl-parse-json"], profile)
    assert plan["participants"]["cloudflare-json"]["input"] == "url"
    assert plan["participants"]["firecrawl-parse-json"]["input"] == "html"
    dispatch = Mock(
        return_value={
            "http_status": 200,
            "response": {"result": {"author": ["Ada"]}, "data": {"json": {"author": ["Ada"]}}},
        }
    )
    monkeypatch.setattr(pipeline, "provider_call", dispatch)
    for method in plan["participants"]:
        certification.run(home, profile, method, tmp_path / method)
    calls = dispatch.call_args_list
    assert len(calls) == 4
    assert calls[0].args[1] == calls[2].args[1]
    assert calls[0].args[3].startswith("https://")
    assert calls[2].args[3] is None


def test_unselected_historical_parent_does_not_block_a_current_repair(
    inputs, tmp_path, monkeypatch
):
    from harness.web_extract import repair

    home, _ = inputs
    monkeypatch.setenv("FIRECRAWL_API_KEY", "synthetic")
    profile = tmp_path / "parents.json"
    plan_for_inputs(home, ["cloudflare-json", "firecrawl-parse-json"], profile)
    monkeypatch.setattr(
        pipeline, "provider_call", Mock(return_value={"http_status": 500, "response": {}})
    )
    parents = [tmp_path / "historical-parent", tmp_path / "current-parent"]
    for method, parent in zip(["cloudflare-json", "firecrawl-parse-json"], parents, strict=True):
        certification.run(home, profile, method, parent)
    historical = parents[0]
    contract = pipeline.read_json(historical / "contract.json")
    contract.pop("extraction_instruction_delivery")
    pipeline.save_json(historical / "contract.json", contract)
    manifest = pipeline.read_json(historical / "manifest.json")
    manifest["contract_sha256"] = pipeline.digest((historical / "contract.json").read_bytes())
    pipeline.save_json(historical / "manifest.json", manifest)
    certificate = pipeline.read_json(historical / "certificate.json")
    certificate["manifest_sha256"] = pipeline.digest((historical / "manifest.json").read_bytes())
    pipeline.save_json(historical / "certificate.json", certificate)
    content = tmp_path / "selected.json"
    pipeline.save_json(content, {"evidence_runs": [{"run": str(p)} for p in parents]})
    selected = {"firecrawl-parse-json": ["book-a-0001"]}
    plan = repair.create_plan(home, content, tmp_path / "selected-plan.json", {}, None, selected)
    assert list(plan["participants"]) == ["firecrawl-parse-json"]
    assert plan["fixture_base"] is None


def test_frozen_legacy_url_plan_still_uses_run_fixture_base(inputs, tmp_path, monkeypatch):
    home, _ = inputs
    profile = tmp_path / "legacy-plan.json"
    plan = plan_for_inputs(home, ["browserbase-json"], profile)
    plan.pop("public_snapshots")
    pipeline.save_json(profile, plan)
    dispatch = Mock(
        return_value={
            "http_status": 200,
            "response": {"statusCode": 200, "content": {"author": ["Ada"]}},
        }
    )
    monkeypatch.setenv("BROWSERBASE_API_KEY", "synthetic")
    monkeypatch.setattr(pipeline, "provider_call", dispatch)
    with pytest.raises(ValueError, match="requires frozen HTTPS fixtures"):
        certification.run(home, profile, "browserbase-json", tmp_path / "missing-origin")
    dispatch.assert_not_called()
    certification.run(
        home,
        profile,
        "browserbase-json",
        tmp_path / "legacy-run",
        fixture_base="https://legacy.example/inputs",
    )
    assert dispatch.call_count == 2
    assert all(
        call.args[3].startswith("https://legacy.example/inputs/")
        for call in dispatch.call_args_list
    )
