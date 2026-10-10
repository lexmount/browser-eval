"""Release checks exercise real reports under ordinary and optimized Python."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.web_fetch.report_check import check
from tests.report_check_fixture import create_report, write_json

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_report.py"


def run_check(root, authority, optimized=False, *extra):
    return subprocess.run(
        [
            sys.executable,
            *(["-O"] if optimized else []),
            str(SCRIPT),
            str(root),
            "--authority-root",
            str(authority),
            *extra,
        ],
        cwd=root.parent,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize("english", [False, True])
def test_valid_report_runs_from_outside_checkout(tmp_path, optimized, english):
    root, authority = create_report(tmp_path, english=english)
    result = run_check(root, authority, optimized)
    assert result.returncode == 0, result.stderr
    assert "1 tasks / 1 records / 1 providers" in result.stdout


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize(
    "defect",
    [
        "artifact",
        "duplicate_case",
        "duplicate_result",
        "reference",
        "execution",
        "workflow",
        "table",
        "duplicate_summary",
        "reliability",
        "notice",
    ],
)
def test_invalid_report_cannot_bypass_checks_with_optimization(tmp_path, optimized, defect):
    root, authority = create_report(tmp_path)
    if defect == "artifact":
        (root / "artifacts/one.txt").write_text("changed")
    elif defect == "duplicate_case":
        dataset = json.loads((root / "dataset.json").read_bytes())
        dataset["cases"] *= 2
        write_json(root / "dataset.json", dataset)
    elif defect == "duplicate_result":
        path = root / "results.jsonl"
        path.write_bytes(path.read_bytes() * 2)
    elif defect == "reference":
        (root / "references/one.txt").write_text("changed")
    elif defect == "execution":
        write_json(root / "execution.json", {"batches": []})
    elif defect == "workflow":
        with (root / "index.html").open("a") as stream:
            stream.write("<p>需用户辅助</p>")
    elif defect == "table":
        with (root / "index.html").open("a") as stream:
            stream.write("<table><tr><th>Wrong first header</th></tr></table>")
    elif defect == "duplicate_summary":
        with (root / "attempts/alpha__one.html").open("a") as stream:
            stream.write("<h3>实际返回</h3>")
    elif defect == "reliability":
        write_json(root / "reliability.json", {})
    else:
        (root / "en/index.html").write_text("missing notice")
    result = run_check(root, authority, optimized)
    assert result.returncode != 0, result.stdout
    assert "PASS:" not in result.stdout


@pytest.mark.parametrize("defect", ["number", "link", "translation"])
def test_bilingual_mismatches_rejected(tmp_path, defect):
    root, authority = create_report(tmp_path, english=True)
    path = root / "en/index.html"
    text = path.read_text()
    if defect == "number":
        text = text.replace("1 task", "2 tasks")
    elif defect == "link":
        text += '<a href="missing.html">Evidence</a>'
    else:
        text += "<p>未翻译的说明</p>"
    path.write_text(text)
    with pytest.raises(ValueError, match="parity|untranslated"):
        check(root, authority_root=authority)


def test_chinese_only_report_does_not_require_english_editorial_assets(tmp_path):
    root, authority = create_report(tmp_path)
    (root / "assets/editorial").mkdir(parents=True)
    (root / "assets/editorial/zh.png").write_bytes(b"Chinese image")
    write_json(
        authority / "research/editorial-figures.json",
        {
            "figures": [
                {
                    "slot": "diagram",
                    "assets": {
                        "zh": {"file": "zh.png", "sha256": "unused"},
                        "en": {"file": "missing-en.png", "sha256": "unused"},
                    },
                }
            ]
        },
    )
    assert check(root, authority_root=authority).startswith("PASS:")


def test_explicit_headless_profile_is_not_replaced_by_global_profile(tmp_path):
    root, authority = create_report(tmp_path, headless=True)
    result = run_check(root, authority, True, "--profile", "evaluation/custom/current-report.json")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("kind", ["escape", "symlink"])
def test_output_artifact_must_stay_in_report(tmp_path, kind):
    root, authority = create_report(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"fixture")
    if kind == "escape":
        path = root / "results.jsonl"
        row = json.loads(path.read_bytes())
        row["artifact_path"] = "../outside.txt"
        path.write_text(json.dumps(row) + "\n")
    else:
        path = root / "artifacts/one.txt"
        path.unlink()
        path.symlink_to(outside)
    with pytest.raises(ValueError, match="Paths must|[Ss]ymlink"):
        check(root, authority_root=authority)


def test_task_default_track_matches_shared_dataset_contract(tmp_path):
    root, authority = create_report(tmp_path)
    path = root / "dataset.json"
    dataset = json.loads(path.read_bytes())
    dataset["cases"][0].pop("track")
    write_json(path, dataset)
    assert check(root, authority_root=authority).startswith("PASS:")


@pytest.mark.parametrize("source", ["inventory", "coverage"])
def test_duplicate_provider_declarations_are_rejected(tmp_path, source):
    root, authority = create_report(tmp_path)
    if source == "inventory":
        path = authority / "configs/evaluation-inventory.json"
        document = json.loads(path.read_bytes())
        document["web"]["providers"] *= 2
    else:
        path = root / "dataset.json"
        document = json.loads(path.read_bytes())
        document["coverage_plan"]["web"] *= 2
    write_json(path, document)
    with pytest.raises(ValueError, match="providers must be nonempty and unique"):
        check(root, authority_root=authority)


def test_explicit_wcxb_profile_dispatches_to_wcxb_gate(tmp_path):
    from harness.web_fetch.wcxb_publication import write_inventory
    (tmp_path / "wcxb-score-summary.json").write_text("{}")
    write_json(tmp_path / "sample-manifest.json", {"cases": []})
    (tmp_path / "index.html").write_text('<h1>Synthetic WCXB report</h1>')
    write_inventory(tmp_path, json.loads((SCRIPT.parents[1] / "evaluation/wcxb/current-report.json").read_bytes()))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path),
            "--profile",
            "evaluation/wcxb/current-report.json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "guide/index.html" in result.stderr
    assert "legacy current-report profile validator" not in result.stderr


@pytest.mark.parametrize("same_target", [False, True])
def test_bilingual_query_only_evidence_links_must_match(tmp_path, same_target):
    root, authority = create_report(tmp_path, english=True)
    for path, value in (
        (root / "index.html", "one"),
        (root / "en/index.html", "one" if same_target else "two"),
    ):
        path.write_text(path.read_text() + f'<a href="?case={value}">Evidence</a>')
    if same_target:
        assert check(root, authority_root=authority).startswith("PASS:")
    else:
        with pytest.raises(ValueError, match="navigation links differ"):
            check(root, authority_root=authority)


@pytest.mark.parametrize("optimized", [False, True])
@pytest.mark.parametrize(
    "defect",
    [
        None,
        "table_width",
        "filter",
        "adoption_count",
        "overview",
        "sort",
        "interface",
        "obsolete_metric",
    ],
)
def test_presentation_checks_remain_active_for_chinese_only_reports(tmp_path, optimized, defect):
    from tests.report_check_fixture import create_revision_report

    root, authority = create_revision_report(tmp_path)
    if defect == "table_width":
        path = root / "analysis/index.html"
        path.write_text(path.read_text().replace("<td>请求失败</td>", ""))
    elif defect == "filter":
        path = root / "analysis/index.html"
        path.write_text(path.read_text().replace('value="fail" selected', 'value="fail"'))
    elif defect == "adoption_count":
        path = root / "adoption/counts.json"
        data = json.loads(path.read_bytes())
        data["methods"][0]["repository_count"] = 2
        write_json(path, data)
    elif defect == "overview":
        path = root / "index.html"
        path.write_text(path.read_text().replace('class="home-overview"', 'class="missing"'))
    elif defect == "sort":
        path = root / "index.html"
        path.write_text(path.read_text().replace('data-col="1"', 'data-col="0"'))
    elif defect == "interface":
        path = root / "adoption/index.html"
        path.write_text(path.read_text().replace("<pre>Response</pre>", ""))
    elif defect == "obsolete_metric":
        path = root / "index.html"
        path.write_text(path.read_text() + "<p>条件通过率</p>")
    result = subprocess.run(
        [
            sys.executable,
            *(["-O"] if optimized else []),
            str(SCRIPT.with_name("check_report_revision.py")),
            str(root),
            "--authority-root",
            str(authority),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    if defect is None:
        assert result.returncode == 0, result.stderr
        assert "PASS:" in result.stdout
    else:
        assert result.returncode != 0
        assert "PASS:" not in result.stdout


@pytest.mark.parametrize("defect", ["duplicate_provider", "duplicate_interface"])
def test_presentation_inventories_require_unique_registered_entries(tmp_path, defect):
    from harness.web_fetch.report_check import check_revision
    from tests.report_check_fixture import create_revision_report

    root, authority = create_revision_report(tmp_path)
    if defect == "duplicate_provider":
        path = root / "adoption/counts.json"
        data = json.loads(path.read_bytes())
        data["methods"].append(data["methods"][0])
        write_json(path, data)
    else:
        from harness.web_fetch.report_interfaces import SPECS

        first, second, *_ = SPECS
        path = root / "adoption/index.html"
        path.write_text(
            path.read_text().replace(
                'id="interface-' + second + '"', 'id="interface-' + first + '"'
            )
        )
    with pytest.raises(ValueError, match="Duplicate adoption|Interface inventory"):
        check_revision(root, authority_root=authority)


@pytest.mark.parametrize('defect', [None, 'missing', 'duplicate', 'unregistered'])
def test_registered_browser_build_has_its_own_interface_panel(tmp_path, defect):
    from harness.web_fetch.report_check import check_revision
    from tests.report_check_fixture import create_revision_report
    root, authority = create_revision_report(tmp_path)
    row=json.loads((root/'results.jsonl').read_text())
    row['provider']='moli-pr919'
    (root/'results.jsonl').write_text(json.dumps(row)+'\n')
    counts=json.loads((root/'adoption/counts.json').read_text())
    for item in counts['methods']:
        if item['provider']=='alpha':item['provider']='moli-pr919'
    write_json(root/'adoption/counts.json',counts)
    panel='<details class="interface-panel" id="interface-moli-pr919"><pre>Request</pre><pre>Response</pre></details>'
    path=root/'adoption/index.html'
    additions='' if defect=='missing' else panel
    if defect=='duplicate':additions+=panel
    if defect=='unregistered':additions+=panel.replace('moli-pr919','unknown-build')
    path.write_text(path.read_text().replace('</body>',additions+'</body>'))
    if defect:
        with pytest.raises(ValueError):check_revision(root,authority_root=authority)
    else:
        assert check_revision(root,authority_root=authority).startswith('PASS:')
