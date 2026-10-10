"""Publication scenarios independent of a historical benchmark size or dataset ID."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from harness.web_fetch import report_package


def report_tree(root, *, sampled=False):
    source = root / "report"
    (source / "assets").mkdir(parents=True)
    (source / "assets/style.css").write_text("body { color: black; }")
    (source / "assets/report.js").write_text("window.addEventListener('report-ready',initReport);")
    (source / "index.html").write_text(
        '<html lang="zh-CN"><body><a href="detail.html">详情</a></body></html>'
    )
    (source / "detail.html").write_text('<html><body><a href="index.html">返回</a></body></html>')
    dataset = {"id": "new-browser-cohort", "cases": [{"id": "alpha"}, {"id": "beta"}]}
    records = []
    if sampled:
        dataset.update(
            comparison={"kind": "webmainbench_frozen_html_to_markdown"},
            coverage_plan={"static_html": ["first", "second"]},
            sample_manifest_sha256="frozen-sample",
        )
        for provider in ("first", "second"):
            for case in ("alpha", "beta"):
                records.append(
                    {
                        "attempt_id": provider + "-" + case,
                        "provider": provider,
                        "case_id": case,
                        "artifact_path": provider + "-" + case + ".txt",
                    }
                )
                (source / records[-1]["artifact_path"]).write_text("measured output")
        review = {
            "status": "passed",
            "failures": [],
            "sample_case_count": 2,
            "providers": ["first", "second"],
            "sample_manifest_sha256": "frozen-sample",
        }
        (source / "result-review.json").write_text(json.dumps(review))
    else:
        (source / "reliability.json").write_text('{"complete":true}')
    (source / "dataset.json").write_text(json.dumps(dataset))
    (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    return source, root / "published.zip"


def test_declared_small_matrix_packages_through_public_command(tmp_path):
    source, archive = report_tree(tmp_path, sampled=True)
    entry = Path(__file__).resolve().parents[1] / "scripts/package_artifact_hub.py"
    subprocess.run(
        [sys.executable, str(entry), str(source), str(archive)], check=True, capture_output=True
    )
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["architecture"]["logical_routes"] == 2
        assert (
            bundle.read("loader.js")
            == Path(report_package.__file__)
            .with_name("assets")
            .joinpath("report-loader.js")
            .read_bytes()
        )


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "extra",
        "wrong-provider",
        "review-count",
        "review-providers",
        "review-binding",
        "unreviewed",
    ],
)
def test_incomplete_or_misbound_matrix_preserves_existing_archive(tmp_path, damage):
    source, archive = report_tree(tmp_path, sampled=True)
    rows = [json.loads(line) for line in (source / "results.jsonl").read_text().splitlines()]
    review_path = source / "result-review.json"
    review = json.loads(review_path.read_text())
    if damage == "missing":
        rows.pop()
    elif damage == "duplicate":
        rows[-1] = rows[0]
    elif damage == "extra":
        rows.append(rows[0])
    elif damage == "wrong-provider":
        rows[-1]["provider"] = "undeclared"
    elif damage == "review-count":
        review["sample_case_count"] = 200
    elif damage == "review-providers":
        review["providers"] = ["first"]
    elif damage == "review-binding":
        review["sample_manifest_sha256"] = "different-sample"
    else:
        review["status"] = "pending"
    (source / "results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    review_path.write_text(json.dumps(review))
    archive.write_bytes(b"previous verified release")
    with pytest.raises(ValueError):
        report_package.build(source, archive)
    assert archive.read_bytes() == b"previous verified release"
    assert not list(tmp_path.glob(".webfetch-package-*"))


def test_optimized_python_still_rejects_broken_links(tmp_path):
    source, archive = report_tree(tmp_path)
    (source / "index.html").write_text('<a href="absent.html">Broken target</a>')
    archive.write_bytes(b"previous verified release")
    entry = Path(__file__).resolve().parents[1] / "scripts/package_artifact_hub.py"
    rejected = subprocess.run(
        [sys.executable, "-O", str(entry), str(source), str(archive)],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0 and "absent.html" in rejected.stderr
    assert archive.read_bytes() == b"previous verified release"


@pytest.mark.parametrize("changed", ["source", "destination"])
def test_mutation_during_packaging_is_not_installed(tmp_path, monkeypatch, changed):
    source, archive = report_tree(tmp_path)
    archive.write_bytes(b"previous verified release")
    project = report_package.project_file
    changed_once = False

    def mutate(name, data):
        nonlocal changed_once
        if not changed_once:
            changed_once = True
            if changed == "source":
                (source / "new-evidence.txt").write_text("new evidence")
            else:
                archive.write_bytes(b"independently installed release")
        return project(name, data)

    monkeypatch.setattr(report_package, "project_file", mutate)
    with pytest.raises(ValueError, match="changed during packaging"):
        report_package.build(source, archive)
    assert archive.read_bytes() == (
        b"previous verified release" if changed == "source" else b"independently installed release"
    )
    assert not list(tmp_path.glob(".webfetch-package-*"))


@pytest.mark.parametrize("linked", ["file", "directory", "source", "destination"])
def test_symlinks_cannot_enter_publication(tmp_path, linked):
    source, archive = report_tree(tmp_path)
    if linked == "file":
        (source / "linked.txt").symlink_to(source / "index.html")
    elif linked == "directory":
        (source / "linked").symlink_to(source / "assets", target_is_directory=True)
    elif linked == "source":
        alias = tmp_path / "alias"
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    else:
        original = tmp_path / "original.zip"
        original.write_bytes(b"original")
        archive.symlink_to(original)
    with pytest.raises(ValueError, match="[Ss]ymlink|regular file"):
        report_package.build(source, archive)


def test_importing_command_does_not_parse_arguments_or_create_files(tmp_path):
    entry = Path(__file__).resolve().parents[1] / "scripts/package_artifact_hub.py"
    code = 'import runpy,sys; sys.argv=["package", "--unknown"]; runpy.run_path(sys.argv[1])'
    # Supply the file independently so command arguments stay deliberately invalid.
    code = code.replace("sys.argv[1]", repr(str(entry)))
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True, capture_output=True)
    assert not list(tmp_path.iterdir())


def test_default_paths_follow_explicit_command_root_from_another_directory(tmp_path, monkeypatch):
    source, _ = report_tree(tmp_path)
    target = tmp_path / "reports/webfetch"
    target.parent.mkdir()
    source.rename(target)
    other = tmp_path / "another-working-directory"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setattr(sys, "argv", ["package"])
    report_package.main(root=tmp_path)
    assert (tmp_path / "publish/webfetch-sites.zip").is_file()
    assert not (other / "publish").exists()
