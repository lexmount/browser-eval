"""Frozen population selection follows input limits, never provider outcomes."""

import csv
import io

import pytest

from lexbench_websearch.simpleqa import eligible_cases, rebuild_dataset


def csv_fixture(questions):
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(
        ["original_index", "problem", "answer", "topic", "answer_type", "urls"]
    )
    for index, question in enumerate(questions):
        writer.writerow(
            [index, question, "Answer", "Topic", "Text", "https://example.org/a)"]
        )
    return text.getvalue().encode()


def test_shared_limit_is_inclusive_unicode_character_count_and_keeps_order():
    cases = eligible_cases(csv_fixture(["A" * 128, "B" * 129, "\u4e2d" * 128, "short"]))
    assert [c["id"] for c in cases] == ["sqv-0", "sqv-2", "sqv-3"]
    assert cases[1]["query"] == "\u4e2d" * 128
    assert cases[0]["reference_urls"] == ["https://example.org/a"]


def test_duplicate_publisher_ids_rejected():
    raw = csv_fixture(["one", "two"]).replace(b"1,two", b"0,two")
    with pytest.raises(ValueError, match="Duplicate"):
        eligible_cases(raw)


def test_builder_rejects_unpinned_csv_before_selection():
    with pytest.raises(ValueError, match="SHA256"):
        rebuild_dataset(csv_fixture(["one"]))


def test_builder_retains_source_artifact_metadata():
    from lexbench_websearch import simpleqa

    raw = csv_fixture(["one"])
    manifest = simpleqa.source_manifest()
    manifest["sha256"] = simpleqa.digest(raw)
    # Correctly hashed but partial publisher inputs must still be rejected.
    with pytest.raises(ValueError, match="Frozen population"):
        simpleqa.rebuild_dataset(raw, manifest=manifest)


def test_mirror_admission_requires_both_matching_title_and_original_lead():
    from lexbench_websearch.simpleqa import mirror_candidates, qualify_mirror

    reference = dict(url='https://en.wikipedia.org/wiki/Example_article', title='Example article',
                     lead='An independently observed encyclopedia article about a particular subject. ' * 10,
                     body_sha256='a'*64, verified_at='2026-11-13T00:00:00Z', status=200)
    candidates = mirror_candidates(reference['url'])
    same = dict(reference, url=candidates[0], body_sha256='b'*64)
    assert qualify_mirror(reference, same)['qualified']
    assert not qualify_mirror(reference, dict(same, title='Another article'))['qualified']
    assert not qualify_mirror(reference, dict(same, lead='Unrelated content about a different person and place. '*20))['qualified']
    for changed in (dict(same, status=403), dict(same, lead='short'),
                    dict(same, body_sha256=''), dict(same, url='https://provider-selected.test/article')):
        with pytest.raises(ValueError):
            qualify_mirror(reference, changed)
    assert mirror_candidates('https://en.wikipedia.org.evil.test/wiki/Example') == []
    assert mirror_candidates('https://example.org/page') == []
    assert mirror_candidates('https://en.wikipedia.org/wiki/Special:Search') == []
    assert mirror_candidates('https://en.wikipedia.org/wiki/Star_Trek:_First_Contact')


def test_article_observations_exclude_navigation_tables_and_later_sections():
    from harness.web_search.revisions import article_observation

    paragraph = 'This independently observed article paragraph identifies the subject.'
    html = ('<nav><p>Navigation text</p></nav><h1>Article</h1>'
            '<div class="mw-parser-output"><table><tr><td>Metadata</td></tr></table>'
            f'<p>{paragraph}</p><h2>History</h2><p>Later section</p></div>').encode()
    markdown = ('Site navigation\n\n# Article\n\n## From Wikipedia, the free encyclopedia\n\n'
                '| Field | Value |\n| --- | --- |\n| Metadata | Other |\n\n'
                + paragraph + '\n\n## History\n\nLater section').encode()
    options = dict(status=200,verified_at='2026-11-13T00:00:00Z')
    a = article_observation(html,'https://en.wikipedia.org/wiki/Article',**options)
    b = article_observation(markdown,'https://wiki2.org/en/Article',media_type='text/markdown',**options)
    assert a['title'] == b['title'] == 'Article'
    assert a['lead'] == b['lead'] == paragraph
    assert a['body_sha256'] != b['body_sha256']
    plain = ('Article\n\n' + paragraph + '\n\n## History\n\nLater section').encode()
    fallback = article_observation(plain,'https://en.wikipedia.org/wiki/Article',
                                  media_type='text/markdown',source_title='Article',**options)
    assert fallback['title'] == 'Article' and fallback['lead'] == paragraph
    wrong_title = article_observation(plain,'https://en.wikipedia.org/wiki/Article',
                                     media_type='text/markdown',source_title='Different',**options)
    assert wrong_title['title'] == '' and wrong_title['lead'] == ''


def test_reference_extension_preserves_publisher_data_and_shares_aliases_by_document():
    from copy import deepcopy
    from lexbench_websearch.simpleqa import extend_reference_urls
    reference = dict(url='https://en.wikipedia.org/wiki/Article',title='Article',
                     lead='A reference paragraph identifying the subject and its independently documented history. '*6,
                     body_sha256='a'*64,status=200,verified_at='2026-11-13T00:00:00Z')
    mirror = dict(reference,url='https://wiki2.org/en/Article',body_sha256='b'*64)
    dataset = dict(cases=[dict(id=str(i),query='Question '+str(i),reference_answer='Answer '+str(i),
                              reference_urls=[reference['url']]) for i in range(2)])
    original = deepcopy(dataset)
    extended = extend_reference_urls(dataset,[reference,mirror])
    from lexbench_websearch.contracts import validate_cases

    validate_cases(extended['cases'])
    assert dataset == original
    for case, previous in zip(extended['cases'], original['cases'], strict=True):
        assert {key:case[key] for key in previous} == previous
        assert case['equivalent_reference_urls'] == [mirror['url']]
        assert case['equivalent_reference_provenance'][0]['reference_url'] == reference['url']
    assert not extend_reference_urls(dataset,[mirror])['cases'][0]['equivalent_reference_urls']
    with pytest.raises(ValueError,match='independently derived'):
        extend_reference_urls(dataset,[dict(mirror,url='https://provider-result.test/document')])
    for key,value in [('equivalent_reference_provenance',[]),('equivalent_reference_urls',[mirror['url'],mirror['url']])]:
        modified=deepcopy(extended['cases'])
        modified[0][key]=value
        with pytest.raises(ValueError,match='provenance'):
            validate_cases(modified)
    modified=deepcopy(extended['cases'])
    modified[0]['equivalent_reference_provenance'][0]['title_matches']=False
    with pytest.raises(ValueError,match='proof'):
        validate_cases(modified)
