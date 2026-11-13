"""The maintained report entry builds and checks isolated output-level pages."""

import json

import pytest
from bs4 import BeautifulSoup

from harness.web_search.example import example
from harness.web_search.report import check


def test_public_report_roundtrip_preserves_native_crlf_text(tmp_path, monkeypatch):
    from harness.web_search import example as owner

    write = owner.write_json

    def native_multiline(path, value):
        if path.parent.name == 'calls' and value.get('results'):
            value['results'][0]['snippet'] = 'Fixture only\r\nExample: /Users/fixture/tool.py'
        return write(path, value)

    monkeypatch.setattr(owner, 'write_json', native_multiline)
    owner.example(tmp_path, levels=True)
    assert check(tmp_path / 'report')
    native = next((tmp_path / 'fixture/calls').glob('*.json')).read_bytes()
    assert b'/Users/fixture/tool.py' in native
    detail = next((tmp_path / 'report/details').glob('*.html')).read_bytes().decode()
    assert '/Users/' not in detail and '[source path]' in detail


def test_public_example_builds_level_tables_findings_and_bound_assessments(tmp_path):
    example(tmp_path, levels=True)
    root = tmp_path / "report"
    assert check(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert manifest["level_assessments"]["path"] == "level-assessments/level-assessments.json"
    assert {item["path"] for item in manifest["bundle"]} >= {
        "level-assessments/level-assessments.json",
        "level-assessments/level-l1.json",
    }
    for prefix in ["", "en/"]:
        home = BeautifulSoup((root / (prefix + "index.html")).read_text(encoding="utf-8"), "html.parser")
        assert [s["data-output-level"] for s in home.select("[data-output-level]")] == ["l1"]
        assert {s["data-level-benchmark"] for s in home.select("[data-level-benchmark]")} == {
            "scenario",
            "factual",
        }
        assert not home.select("[data-executive], [data-discovery]")
        dataset = BeautifulSoup((root / (prefix + 'dataset.html')).read_bytes(), 'html.parser')
        assert len(dataset.select('[data-dataset-context] tbody tr')) == 2
        assert {o['value'] for o in dataset.select('[data-cohort-filter] option')} == {'all', 'scenario', 'factual'}
        assert {row['data-cohort'] for row in dataset.select('[data-row]')} == {'scenario', 'factual'}
        results = BeautifulSoup((root / (prefix + 'results.html')).read_bytes(), 'html.parser')
        comparison = json.loads(results.select_one('[data-comparison-data]').string)
        assert {p['id'] for p in comparison['populations']} == {'scenario', 'factual'}
        assert {o['value'] for o in results.select('[data-output-benchmark] option')} == {'all', 'scenario', 'factual'}
        quality = BeautifulSoup((root / (prefix + "quality.html")).read_text(encoding="utf-8"), "html.parser")
        assert {
            s["data-findings-benchmark"] for s in quality.select("[data-findings-benchmark]")
        } == {"scenario", "factual"}
        assert len(quality.select("[data-primary-chart]")) == 2
        assert not quality.select("[data-factual]")
        assert (
            len(
                BeautifulSoup(
                    (root / (prefix + "capabilities.html")).read_text(encoding="utf-8"), "html.parser"
                ).select('[data-capability-level="l1"]')
            )
            == 1
        )


@pytest.mark.parametrize(
    "damage",
    ["missing-level", "missing-chart", "wrong-value", "wrong-participant", "wrong-dataset"],
)
def test_public_check_rejects_missing_or_incorrect_level_presentation(tmp_path, damage):
    example(tmp_path, levels=True)
    root = tmp_path / "report"
    path = root / ("quality.html" if damage == "missing-chart" else 'dataset.html' if damage == 'wrong-dataset' else "index.html")
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    if damage == "missing-level":
        soup.select_one("[data-output-level]").decompose()
    elif damage == "missing-chart":
        soup.select_one("[data-primary-chart]").decompose()
    elif damage == "wrong-value":
        soup.select_one("[data-output-level] td[data-metric]")["data-sort"] = "999"
    elif damage == 'wrong-dataset':
        soup.select_one('[data-dataset-context] tbody tr td:nth-of-type(2)').string = '999'
    else:
        soup.select_one("[data-output-level] tbody tr td").string = "An unsupported provider"
    path.write_text(str(soup), encoding="utf-8")
    with pytest.raises(ValueError):
        check(root)


def test_level_results_and_details_keep_text_and_grade_sources_separate():
    from harness.web_search.comparison import render_level_results, render_level_detail
    from harness.web_search.output_levels import assessed_levels
    from tests.web_search.test_level_scores import complete_levels

    profile, rows, admissions, artifacts, evidence = complete_levels()
    views = assessed_levels(
        profile, rows, relevance=evidence, admissions=admissions, artifacts=artifacts
    )
    for view in views.values():
        for method in view['profile']['methods']:
            method['label'] = 'Fixture ' + method['id']
    for method in profile['methods']:
        method['label'] = 'Fixture ' + method['id']
    row = rows[0]
    native = dict(
        row,
        answer="The generated answer [引用:1]",
        results=[dict(row["results"][0], citation_id=1)],
    )
    answer_score = dict(
        case_id=row["case_id"],
        method=row["method"],
        answer_grade=2,
        citation_count=1,
        supported_citations=1,
    )
    from copy import deepcopy

    answer_profile = deepcopy(profile)
    answer_profile["protocol"]["relevance_level"] = "l3"
    raw_grade = json.dumps(dict(grade=2, reason='Useful but incomplete'))
    call = dict(response={'choices': [{'message': {'content': raw_grade}}]})
    views["l3"] = dict(profile=answer_profile, rows=[native], answer_scores=[answer_score],
                       answers={'calls': {row['method'] + '/' + row['case_id'] + '/scenario': call}})
    soup = BeautifulSoup(render_level_results(views), "html.parser")
    assert [s["data-level-results"] for s in soup.select("[data-level-results]")] == [
        "l1",
        "l2",
        "l3",
    ]
    data = [json.loads(node.string) for node in soup.select("[data-comparison-data]")]
    assert data[0]["cases"][0]["outputs"]["web"]["items"][0]["snippet"] == "Primary preview"
    assert data[1]["cases"][0]["outputs"]["web"]["items"][0]["snippet"].startswith(
        "An independently published paragraph"
    )
    assert not soup.select_one('[data-level-results="l2"] option[value="source"]')
    assert soup.select_one("[data-answer-result] td:nth-of-type(4)").get_text() == "2 / 3"
    assert not soup.select_one('[data-level-results="l3"] .comparison-winner')
    detail = BeautifulSoup(render_level_detail(views, native), "html.parser")
    assert len(detail.select("[data-detail-level]")) == 3
    assert (
        detail.select_one('[data-detail-level="l1"] .native-answer').get_text() == "Primary preview"
    )
    assert (
        detail.select_one('[data-detail-level="l3"] .native-answer').get_text() == native["answer"]
    )
    assert "1 / 1" in detail.select_one('[data-detail-level="l3"]').get_text()

    from harness.web_search.report import _resolve_owned_links

    for prefix in ('', 'en/'):
        for site in ('', 'reports'):
            rendered = render_level_results(views, detail_link=lambda r, site=site, prefix=prefix: '/'.join(
                part for part in (site, prefix.rstrip('/'), 'details', r['id'] + '.html') if part))
            doc = BeautifulSoup(rendered, 'html.parser')
            _resolve_owned_links(doc, prefix + 'results.html', site, set())
            expected = '/'.join(part for part in (site, prefix.rstrip('/'), 'details',
                                                 native['id'] + '.html#l3') if part)
            assert doc.select_one('[data-answer-result] a')['href'] == expected


@pytest.mark.parametrize("damage", ["missing-results-level", "mixed-preview", "wrong-detail"])
def test_public_check_rejects_mixed_or_missing_level_evidence_views(tmp_path, damage):
    example(tmp_path, levels=True)
    root = tmp_path / "report"
    path = (
        next((root / "details").glob("*.html"))
        if damage == "wrong-detail"
        else root / "results.html"
    )
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    if damage == "missing-results-level":
        soup.select_one("[data-level-results]").decompose()
    elif damage == "mixed-preview":
        node = soup.select_one("[data-comparison-data]")
        value = json.loads(node.string)
        value["cases"][0]["outputs"]["lexmount"]["items"][0]["snippet"] = (
            "A body from another output level"
        )
        node.string = json.dumps(value)
    else:
        soup.select_one("[data-detail-level] .native-answer").string = "Wrong scored text"
    path.write_text(str(soup), encoding="utf-8")
    with pytest.raises(ValueError):
        check(root)


def test_level_report_keeps_authored_quality_definitions_before_findings(tmp_path, monkeypatch):
    from harness.web_search import example as example_module

    original = example_module.build

    def build(output, **kwargs):
        path = kwargs['content']
        content = json.loads(path.read_bytes())
        content['sections'] = [dict(page=page, title=dict(en=title, zh=title),
                                   paragraphs=[dict(en=text, zh=text)]) for page, title, text in [
            ('quality-lead', 'Reading guide', 'Start with the primary metric'),
            ('quality-definitions', 'Grade definitions', 'Three means directly useful'),
            ('quality', 'Findings', 'Compare the two benchmark populations')]]
        path.write_text(json.dumps(content), encoding='utf-8')
        return original(output, **kwargs)

    monkeypatch.setattr(example_module, 'build', build)
    example_module.example(tmp_path, levels=True)
    root = tmp_path / 'report'
    check(root)
    for prefix in ('', 'en/'):
        text = (root / (prefix + 'quality.html')).read_text(encoding='utf-8')
        assert text.index('Reading guide') < text.index('Grade definitions') < text.index('Findings')
        assert 'Three means directly useful' in text


@pytest.mark.parametrize('attribute,value', [('hidden', ''), ('id', 'wrong-level')])
def test_public_check_binds_detail_visibility_and_fragment(tmp_path, attribute, value):
    example(tmp_path, levels=True)
    root = tmp_path / 'report'
    path = next((root / 'details').glob('*.html'))
    doc = BeautifulSoup(path.read_bytes(), 'html.parser')
    doc.select_one('[data-detail-level]')[attribute] = value
    path.write_text(str(doc), encoding='utf-8')
    with pytest.raises(ValueError, match='Per-level detail'):
        check(root)


def test_english_primary_level_report_keeps_edition_local_detail_links(tmp_path, monkeypatch):
    from harness.web_search import example as module
    original = module.build
    monkeypatch.setattr(module, 'build', lambda output, **kwargs: original(output, **dict(kwargs, language='en')))
    module.example(tmp_path, levels=True)
    check(tmp_path / 'report')
