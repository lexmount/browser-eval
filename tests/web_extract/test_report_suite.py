"""Independent benchmark results share navigation, never a quality denominator."""

import pytest
from bs4 import BeautifulSoup

from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import check


@pytest.mark.parametrize("damage", [None, "quality", "navigation", "child-binding"])
def test_suite_uses_each_cohorts_results_and_bound_details(tmp_path, damage):
    output = tmp_path / "report"
    create(output, mode="field-extraction-suite")
    manifest = check(output)
    assert [c["id"] for c in manifest["cohorts"]] == ["first", "second"]
    path = output / "zh/index.html"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    assert [section["data-cohort"] for section in soup.select("[data-cohort]")] == [
        "first",
        "second",
    ]
    assert len(soup.select('[data-cohort="first"] tbody tr')) == 4
    assert not soup.select("main > table")
    assert soup.select_one("header .report-toolbar nav")
    assert soup.select_one(".report-tools #theme")
    if damage is None:
        with pytest.raises(ValueError, match="visual|Synthetic"):
            check(output, release=True)
        return
    if damage == "quality":
        soup.select_one('[data-cohort="first"] [data-metric="micro-f1"]').string = "0.999"
    elif damage == "navigation":
        soup.select_one("nav a")["href"] = "missing.html"
    else:
        path = output / "first/report-manifest.json"
        child = read_json(path)
        child["case_count"] += 1
        save_json(path, child)
    if damage != "child-binding":
        path.write_text(str(soup), encoding="utf-8")
    manifest["files"][path.relative_to(output).as_posix()] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="comparison|navigation|cohort manifest"):
        check(output)


def test_suite_example_rejects_repository_output_before_creating_anything():
    from pathlib import Path

    from harness.web_extract.example import create_suite

    output = Path(__file__).resolve().parents[2] / "rejected-suite-example"
    with pytest.raises(ValueError):
        create_suite(output)
    assert not output.exists()
    assert not output.with_name(output.name + "-suite-inputs").exists()


@pytest.mark.parametrize("reserved", ["zh", "en", "public-evidence", "assets"])
def test_suite_rejects_language_directory_collisions_before_writing(tmp_path, reserved):
    from harness.web_extract import report_suite

    content = tmp_path / "content.json"
    profile = tmp_path / "profile.json"
    save_json(content, {"cohorts": [{"id": reserved}]})
    save_json(profile, {"research_report": {"content": {"sha256": digest(content.read_bytes())}}})
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="distinct safe"):
        report_suite.build(output, content, profile=profile)
    assert not output.exists()


def test_quality_exclusion_preserves_operational_comparison(tmp_path):
    from pathlib import Path

    from harness.web_extract import report_suite

    source = create(tmp_path / "seed", mode="field-extraction-suite")
    suite = read_json(source / "content.json")
    entry = suite["cohorts"][0]
    content = Path(entry["content"])
    model = read_json(content)
    method = next(iter(model["participants"]))
    model["participants"][method].update(
        include_in_comparison=False, exclusion_reason="Synthetic account quota"
    )
    save_json(content, model)
    profile = Path(entry["profile"])
    authority = read_json(profile)
    authority["research_report"]["content"]["sha256"] = digest(content.read_bytes())
    save_json(profile, authority)
    entry["content_sha256"] = digest(content.read_bytes())
    entry["profile_sha256"] = digest(profile.read_bytes())
    save_json(source / "content.json", suite)
    authority = read_json(source / "profile.json")
    authority["research_report"]["content"]["sha256"] = digest(
        (source / "content.json").read_bytes()
    )
    authority["research_report"]["cohorts"] = report_suite.cohort_registry(suite["cohorts"])
    save_json(source / "profile.json", authority)
    output = tmp_path / "report"
    report_suite.build(output, source / "content.json", profile=source / "profile.json")
    soup = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    tables = soup.select('[data-cohort="first"] table')
    quality, operation = [table.select("tbody tr") for table in tables]
    assert len(quality) == len(operation) == 2
    assert "Synthetic account quota" in quality[0].get_text()
    assert [td.get_text() for td in quality[0].select("td")[2:5]] == ["—"] * 3
    assert model["participants"][method]["label"] in operation[0].get_text()
    assert operation[0].select('[data-metric="mean"]')
    assert "$" in operation[0].get_text()


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("prefix", [None, "wrong"])
def test_suite_validates_every_prefix_before_creating_output(tmp_path, position, prefix):
    from harness.web_extract import report_suite

    content = tmp_path / "content.json"
    entries = []
    for name in ["first", "second"]:
        child = tmp_path / (name + ".json")
        save_json(child, {"site_prefix": name})
        child_profile = tmp_path / (name + "-profile.json")
        save_json(child_profile, {})
        entries.append({"id": name, "content": str(child), "profile": str(child_profile),
                        "content_sha256": digest(child.read_bytes()),
                        "profile_sha256": digest(child_profile.read_bytes())})
    bad = read_json(tmp_path / (["first", "second"][position] + ".json"))
    if prefix is None:
        bad.pop("site_prefix")
    else:
        bad["site_prefix"] = prefix
    save_json(tmp_path / (["first", "second"][position] + ".json"), bad)
    save_json(content, {"cohorts": entries})
    profile = tmp_path / "profile.json"
    save_json(profile, {"research_report": {"content": {"sha256": digest(content.read_bytes())}}})
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="suite directory"):
        report_suite.build(output, content, profile=profile)
    assert not output.exists()


@pytest.mark.parametrize("flag", [False, None, 0])
def test_suite_example_identity_cannot_be_removed_or_relabelled(tmp_path, flag):
    output = tmp_path / "report"
    create(output, mode="field-extraction-suite")
    path = output / "report-manifest.json"
    manifest = read_json(path)
    if flag is None:
        manifest.pop("local_example")
    else:
        manifest["local_example"] = flag
    save_json(path, manifest)
    with pytest.raises(ValueError, match="example flag"):
        check(output)


def test_suite_explicit_unsupported_language_fails_before_children_or_output(tmp_path):
    from harness.web_extract import report_suite

    with pytest.raises(ValueError, match="supports only zh"):
        report_suite.build(tmp_path / "output", tmp_path / "unused-content.json",
                           profile=tmp_path / "unused-profile.json", language="en")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("href,expected", [
    ("https://example.org/source", "https://example.org/source"),
    ("first/zh/dataset.html", "first/zh/dataset.html"),
    ("#first", "zh/index.html#first"),
    ("?source=first#first", "zh/index.html?source=first#first"),
])
def test_suite_summary_links_survive_owner_normalization_and_are_checked(tmp_path, href, expected):
    from harness.web_extract import report_suite

    source = create(tmp_path / "seed", mode="field-extraction-suite")
    content = source / "content.json"
    model = read_json(content)
    model["cohorts"][0]["summary_html"] = f'<p><a href="{href}">Source</a></p>'
    save_json(content, model)
    profile = source / "profile.json"
    authority = read_json(profile)
    authority["research_report"]["content"]["sha256"] = digest(content.read_bytes())
    authority["research_report"]["cohorts"] = report_suite.cohort_registry(model["cohorts"])
    save_json(profile, authority)
    output = tmp_path / "report"
    report_suite.build(output, content, profile=profile, language="zh")
    path = output / "zh/index.html"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    link = soup.select_one('[data-cohort="first"] > p a')
    assert link["href"] == expected
    if href.startswith("https:"):
        assert link["target"] == "_blank"
        assert set(link["rel"]) == {"noopener", "noreferrer"}
    link["href"] = "https://example.org/another-source"
    path.write_text(str(soup), encoding="utf-8")
    manifest = read_json(output / "report-manifest.json")
    manifest["files"]["zh/index.html"] = digest(path.read_bytes())
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="Suite comparison"):
        check(output)


@pytest.mark.parametrize("damage", [
    "remove", "reorder", "label", "child-content", "child-profile",
    "content-authority", "rewrite-authority",
])
def test_suite_scope_is_bound_to_approved_content_after_rehashing(tmp_path, damage):
    import shutil

    output = tmp_path / "report"
    create(output, mode="field-extraction-suite")
    manifest = read_json(output / "report-manifest.json")
    suite = read_json(output / "suite.json")
    path = output / "zh/index.html"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    if damage in {"remove", "rewrite-authority"}:
        suite["cohorts"].pop()
        shutil.rmtree(output / "second")
        soup.select_one('[data-cohort="second"]').decompose()
        soup.select_one('nav a[href="second/zh/index.html"]').decompose()
        if damage == "rewrite-authority":
            authority_path = output / manifest["input_profile"]["path"]
            authority = read_json(authority_path)
            authority["research_report"]["cohorts"].pop()
            save_json(authority_path, authority)
            manifest["input_profile"]["published_sha256"] = digest(authority_path.read_bytes())
    elif damage == "reorder":
        suite["cohorts"].reverse()
    elif damage == "label":
        suite["cohorts"][0]["label"] = "Different benchmark"
    elif damage == "content-authority":
        manifest["content_sha256"] = "0" * 64
    else:
        child_path = output / "first/report-manifest.json"
        child = read_json(child_path)
        child[damage.removeprefix("child-") + "_sha256"] = "0" * 64
        save_json(child_path, child)
        suite["cohorts"][0]["manifest_sha256"] = digest(child_path.read_bytes())
    path.write_text(str(soup), encoding="utf-8")
    save_json(output / "suite.json", suite)
    manifest["cohorts"] = suite["cohorts"]
    manifest["files"] = {
        p.relative_to(output).as_posix(): digest(p.read_bytes())
        for p in output.rglob("*")
        if p.is_file() and p != output / "report-manifest.json"
    }
    save_json(output / "report-manifest.json", manifest)
    with pytest.raises(ValueError, match="profile-authorized|child input|bound input profile"):
        check(output)


@pytest.mark.parametrize("mode,suffix", [
    ("field-extraction-suite", "-suite-inputs"),
    ("field-extraction-cohort", "-synthetic-inputs"),
])
def test_unsupported_example_language_leaves_output_reusable(tmp_path, mode, suffix):
    output = tmp_path / "report"
    with pytest.raises(ValueError, match="Chinese only|supports only zh"):
        create(output, mode=mode, language="en")
    assert not output.exists()
    assert not output.with_name(output.name + suffix).exists()
    create(output, mode=mode, language="zh")
    check(output)
