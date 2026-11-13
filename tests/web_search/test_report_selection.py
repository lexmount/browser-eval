"""Reader-selected API versions retain complete query populations and evidence."""
import copy
import json
import pytest
from bs4 import BeautifulSoup

from harness.web_search.example import example
from harness.web_search.report import build, check, load_run, metric_populations, project_comparison


@pytest.mark.parametrize('independent', [False, True])
def test_added_configuration_does_not_inherit_historical_relevance_even_on_same_urls(tmp_path, independent):
    from lexbench_websearch.relevance import candidate_packet
    from harness.web_search.comparison import comparison_data
    from harness.web_search.report import score_relevance

    example(tmp_path)
    profile, rows, _ = load_run(tmp_path / "fixture")
    scope = [profile["methods"][0]["id"]]
    old_profile = dict(profile, methods=profile["methods"][:1])
    old_rows = [r for r in rows if r["method"] in scope]
    packet, rankings = candidate_packet(old_profile, old_rows)
    evidence = dict(packet, provider_rankings=rankings,
                    judge={"kind": "human", "name": "Synthetic annotator"},
                    labels={c["case_id"]: {x["candidate_id"]: 3 for x in c["candidates"]}
                            for c in packet["blind_candidate_packets"]})
    content = tmp_path / "scoped-content.json"
    authored = {"relevance_methods": scope, "presentation": "comparison-report"}
    if independent:
        authored.update(independent_benchmarks=True, metric_populations=[{
            'id': 'custom', 'metric_kind': 'relevance', 'benchmark_root': True,
            'case_ids': [c['id'] for c in profile['cases']],
            'label': {'en': 'Synthetic scenarios', 'zh': 'Synthetic scenarios'}}])
    content.write_text(json.dumps(authored), encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(evidence), encoding="utf-8")
    output = tmp_path / "scoped-report"
    scoped_profile = dict(profile, relevance_methods=scope)
    scored = score_relevance(scoped_profile, rows, evidence["plan"], evidence["labels"])
    assert {r["method"] for r in scored} == set(scope)
    compared = comparison_data(scoped_profile, rows, relevance=evidence)
    unjudged = compared["cases"][0]["outputs"][profile["methods"][1]["id"]]
    assert unjudged["score"] is None and unjudged["quality"] is None
    assert unjudged['primary'] is None
    assert unjudged['source'] is True and unjudged['availability'] is True
    assert all(item["grade"] is None and item["grade_source"] == "unjudged" for item in unjudged["items"])
    with pytest.raises(ValueError, match='every method and query'):
        build(output, source=tmp_path / "fixture", content=content, relevance=labels, include_english=True)


def test_projection_retains_selected_method_order_and_all_questions(tmp_path):
    example(tmp_path)
    profile, rows, _ = load_run(tmp_path / 'fixture')
    original = copy.deepcopy((profile, rows))
    ids = [m['id'] for m in profile['methods']][::-1]
    projected, selected = project_comparison(profile, rows, {'comparison_methods': ids})
    assert [m['id'] for m in projected['methods']] == ids
    assert len(selected) == len(rows)
    assert (profile, rows) == original
    with pytest.raises(ValueError, match='Incomplete'):
        project_comparison(profile, rows[1:], {'comparison_methods': ids})
    with pytest.raises(ValueError, match='Invalid'):
        project_comparison(profile, rows, {'comparison_methods': ['nonexistent']})


def test_population_membership_rejects_invented_or_partial_denominators():
    profile = {'cases': [{'id': 'a', 'reference_answer': 'A'}, {'id': 'b'}]}
    labels = {'en': 'Queries', 'zh': '题目'}
    all_group = {'id': 'overall', 'label': labels, 'case_ids': ['a', 'b']}
    factual = {'id': 'factual', 'label': labels, 'case_ids': ['a']}
    scenario = {'id': 'scenario', 'label': labels, 'case_ids': ['b']}
    assert len(metric_populations(profile, {'metric_populations': [all_group, factual, scenario]})) == 3
    for invalid in [dict(all_group, case_ids=['a']), dict(factual, case_ids=['b']), dict(scenario, case_ids=['missing'])]:
        groups = [invalid] if invalid['id'] == 'overall' else [all_group, invalid]
        with pytest.raises(ValueError):
            metric_populations(profile, {'metric_populations': groups})


def test_independent_benchmark_roots_partition_without_an_overall_score():
    profile = {'cases': [{'id': 'a'}, {'id': 'b'}, {'id': 'c', 'reference_answer': 'C'}]}
    groups = [
        {'id': 'custom', 'benchmark_root': True, 'label': {'en': 'Custom', 'zh': '自建'}, 'case_ids': ['a', 'b']},
        {'id': 'factual', 'benchmark_root': True, 'label': {'en': 'Public', 'zh': '公开'}, 'case_ids': ['c']},
        {'id': 'site', 'metric_kind': 'site', 'label': {'en': 'Site', 'zh': '站点'}, 'case_ids': ['b']},
    ]
    assert metric_populations(profile, {'independent_benchmarks': True, 'metric_populations': groups}) == groups
    broken = copy.deepcopy(groups)
    broken[1]['case_ids'] = ['b', 'c']
    with pytest.raises(ValueError):
        metric_populations(profile, {'independent_benchmarks': True, 'metric_populations': broken})


def test_site_population_counts_empty_response_as_constraint_miss():
    from lexbench_websearch.scoring import evaluate
    from harness.web_search.report import population_summary

    case = {'id': 'site-empty', 'query': 'site:example.com report', 'domains': ['example.com']}
    profile = {
        'cases': [case],
        'methods': [{'id': 'a', 'label': 'A', 'currency': 'CNY'}],
        'protocol': {'top_k': 5},
    }
    rows = [{
        'case_id': case['id'], 'method': 'a', 'http_status': 200, 'results': [],
        'error': '', 'elapsed_ms': 10, 'cost': 0, 'metrics': evaluate(case, []),
    }]
    content = {'metric_populations': [{
        'id': 'overall', 'metric_kind': 'site',
        'label': {'en': 'Site', 'zh': '站点'}, 'case_ids': [case['id']],
    }]}
    soup = BeautifulSoup(population_summary(profile, rows, content, True), 'html.parser')
    assert '0/1' in soup.get_text()


def test_latency_population_keeps_failures_and_reports_actual_retry_usage():
    from lexbench_websearch.scoring import evaluate
    from harness.web_search.core import aggregate
    from harness.web_search.report import population_summary, benchmark_operations

    case = {'id': 'query', 'query': 'Find a document', 'domains': ['example.com']}
    profile = {'cases': [case], 'methods': [{'id': 'a', 'label': 'A', 'currency': 'USD'}],
               'protocol': {'top_k': 5, 'retry': True}}
    rows = []
    for elapsed, results, error in [(100, [{'url': 'https://example.com/'}], ''),
                                    (200, [], ''), (300, [], 'Timed out')]:
        rows.append({'case_id': f'query-{elapsed}', 'method': 'a', 'http_status': 200 if not error else 0,
                     'results': results, 'error': error, 'elapsed_ms': elapsed, 'cost': 0,
                     'metrics': evaluate(case, results), 'attempts': [{'elapsed_ms': elapsed}]})
    stats = aggregate(rows)['a']
    assert stats['all_latency']['n'] == 3
    assert stats['all_latency']['mean'] == 200
    assert stats['success_latency']['n'] == 1
    assert stats['success_latency']['mean'] == 100
    profile['cases'] = [dict(case, id=row['case_id']) for row in rows]
    content = {'catalog_pricing': {'a': {'amount': 1, 'currency': 'USD', 'unit': '1000-requests',
                                       'source': 'https://prices.test/', 'checked_at': '2026-11-13'}},
               'metric_populations': [{'id': 'overall', 'metric_kind': 'site',
                                       'label': {'en': 'Site', 'zh': '站点'},
                                       'case_ids': [row['case_id'] for row in rows]}]}
    for zh, expected in [(False, 'this collection used no retries'), (True, '本批未发生重试')]:
        page = BeautifulSoup(population_summary(profile, rows, content, zh), 'html.parser')
        assert expected in page.select_one('.population-note').get_text()
        performance = BeautifulSoup(benchmark_operations(profile, rows, content, zh), 'html.parser')
        assert expected in performance.select_one('[data-benchmark-operations] p').get_text()
    rows[-1]['attempts'].append({'elapsed_ms': 50})
    page = BeautifulSoup(population_summary(profile, rows, content, False), 'html.parser')
    assert 'every call and retry wait' in page.select_one('.population-note').get_text()
    assert 'used no retries' not in page.get_text()
    performance = BeautifulSoup(benchmark_operations(profile, rows, content, False), 'html.parser')
    assert 'Timing includes retries' in performance.select_one('[data-benchmark-operations] p').get_text()
    assert 'used no retries' not in performance.get_text()


def test_comparison_report_has_owned_chapters_and_bound_populations(tmp_path):
    example(tmp_path)
    profile, _, _ = load_run(tmp_path / 'fixture')
    ids = [m['id'] for m in profile['methods']]
    case_ids = [c['id'] for c in profile['cases']]
    content = {
        'presentation': 'comparison-report', 'reading_model': True,
        'comparison_methods': ids,
        'metric_populations': [
            {'id': 'overall', 'label': {'en': 'Overall', 'zh': '总体'}, 'case_ids': case_ids},
            {'id': 'scenario', 'label': {'en': 'Scenario subset', 'zh': '场景子集'}, 'case_ids': case_ids[:1]},
        ],
        'sections': [{'page': 'capabilities', 'title': {'en': 'Fields', 'zh': '字段'}, 'paragraphs': [{'en': 'Chapter-specific text.', 'zh': '章节专属说明。'}]}],
    }
    source = tmp_path / 'content.json'; source.write_text(json.dumps(content), encoding='utf-8')
    output = tmp_path / 'comparison'
    build(output, source=tmp_path / 'fixture', content=source)
    home = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    assert len(home.select('[data-executive]')) == 1
    assert len(home.select('[data-discovery] tbody tr')) == 1
    assert [node['data-population'] for node in home.select('[data-population]')] == ['overall', 'scenario']
    assert not home.select('[data-modes]')
    assert 'Chapter-specific text.' not in home.get_text()
    assert '章节专属说明。' in (output / 'capabilities.html').read_text(encoding='utf-8')
    assert check(output)
    home.select_one('[data-executive] td').string = 'invented method'
    (output / 'index.html').write_text(str(home), encoding='utf-8')
    with pytest.raises(ValueError, match='Population summary'):
        check(output)


@pytest.mark.parametrize('damage', ['order', 'primary', 'guide'])
def test_primary_metric_order_and_explanation_are_release_checked(tmp_path, damage):
    example(tmp_path, comparison=True)
    output = tmp_path / 'report'
    home = BeautifulSoup((output / 'index.html').read_text(encoding='utf-8'), 'html.parser')
    tables = home.select('[data-executive] table')
    assert [len(t.select('th[data-primary-metric]')) for t in tables] == [1, 1]
    assert [t.select('th').index(t.select_one('[data-primary-metric]')) for t in tables] == [1, 3]
    for table in tables:
        index = table.select('th').index(table.select_one('[data-primary-metric]'))
        values = [float(row.select('td')[index]['data-value']) for row in table.select('tbody tr')]
        assert values == sorted(values, reverse=True)
        assert table.select_one('[data-primary-metric]')['aria-sort'] == 'descending'
    assert '评测概览' in home.get_text()
    if damage == 'order':
        rows = tables[0].select('tbody tr')
        tables[0].tbody.append(rows[0].extract())
    elif damage == 'primary':
        del tables[0].select_one('[data-primary-metric]')['data-primary-metric']
    else:
        home.select_one('[data-primary-guide]').decompose()
    (output / 'index.html').write_text(str(home), encoding='utf-8')
    with pytest.raises(ValueError, match='Population summary'):
        check(output)


def test_nested_comparison_keeps_primary_evidence_links_in_both_editions(tmp_path):
    example(tmp_path, comparison=True)
    site = tmp_path / 'report'
    output = site / 'social'
    build(output, source=tmp_path / 'fixture', content=tmp_path / 'content.json',
          relevance=tmp_path / 'labels.json', include_english=True, site_prefix='social')
    assert check(output)
    for prefix in ('', 'en/'):
        page = BeautifulSoup((output / prefix / 'index.html').read_bytes(), 'html.parser')
        link = page.select_one('[data-executive] a[href*="relevance-scores"]')
        assert link['href'] == 'social/' + prefix + 'quality.html#relevance-scores'


def test_three_selected_versions_drop_two_optional_experiments(tmp_path):
    example(tmp_path)
    profile, rows, _ = load_run(tmp_path / 'fixture')
    template = profile['methods'][0]
    originals = [r for r in rows if r['method'] == template['id']]
    ids = ['lexmount', 'deepseek', 'bocha', 'deepseek-content', 'bocha-summary']
    profile['methods'] = [dict(template, id=mid, label=mid) for mid in ids]
    rows = [dict(row, method=mid, id=row['id'] + '-' + mid) for mid in ids for row in originals]
    chosen = ['lexmount', 'deepseek-content', 'bocha-summary']
    projected, projected_rows = project_comparison(profile, rows, {'comparison_methods': chosen})
    assert [m['id'] for m in projected['methods']] == chosen
    assert len(projected_rows) == 3 * len(profile['cases'])
    assert {r['method'] for r in projected_rows} == set(chosen)
    assert len(rows) == 5 * len(profile['cases'])


def test_nested_language_comparison_links_keep_language():
    from harness.web_search.report import render_result_comparison
    profile = {'methods': [{'id': 'a', 'label': 'A'}], 'cases': [{'id': 'q', 'query': 'Q'}]}
    row = {'id': 'q-a-r0', 'case_id': 'q', 'method': 'a', 'results': [], 'metrics': {}, 'error': None}
    soup = BeautifulSoup(render_result_comparison(profile, [row], False, prefix='en/'), 'html.parser')
    data = json.loads(soup.select_one('[data-comparison-data]').string)
    assert data['cases'][0]['outputs']['a']['detail'] == 'en/details/q-a-r0.html'


def test_projection_only_publishes_metadata_for_materialized_calls(tmp_path, monkeypatch):
    from harness.web_search import report
    example(tmp_path)
    actual = report.public_calls
    def with_audit_records(*args):
        rows, files, redactions, corrections = actual(*args)
        for row in rows:
            path = row['evidence']['path']
            redactions.append({'path': path, 'original_sha256': row['evidence']['sha256'], 'public_sha256': row['evidence']['sha256']})
            corrections.append({'path': path, 'original_cost': 0, 'cost': row['cost']})
        return rows, files, redactions, corrections
    monkeypatch.setattr(report, 'public_calls', with_audit_records)
    content = tmp_path / 'selection.json'
    content.write_text(json.dumps({'comparison_methods': ['lexmount']}), encoding='utf-8')
    output = tmp_path / 'selected'
    build(output, source=tmp_path / 'fixture', content=content)
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    retained = {r['evidence']['path'] for r in manifest['rows']}
    assert {r['path'] for r in manifest['evidence_redactions']} == retained
    assert {r['path'] for r in manifest['billing_corrections']} == retained
    assert check(output)


def test_additional_reference_exposes_source_date_excerpt_without_precall_claim():
    from harness.web_search.report import annotation_detail
    annotation = {'sources': [{'url': 'https://example.org/page', 'title': 'Independent page', 'excerpt': 'Relevant source statement.', 'verified_at': '2026-10-21'}]}
    html = annotation_detail(annotation, False)
    assert 'after collection' in html
    assert '2026-10-21' in html
    assert 'Relevant source statement.' in html
    assert 'Before/after HTTP' not in html


def test_headline_subgroups_show_actual_quality_counts_and_ranks():
    from lexbench_websearch.scoring import evaluate
    from harness.web_search.report import population_summary
    cases = [
        {'id': 'f', 'reference_answer': '42', 'reference_urls': ['https://source.org/answer']},
        {'id': 's', 'anchors': ['https://source.org/target']},
    ]
    profile = {'cases': cases, 'methods': [{'id': 'a', 'label': 'A', 'currency': 'CNY'}, {'id': 'b', 'label': 'B', 'currency': 'CNY'}], 'protocol': {'top_k': 5, 'retry': True}}
    rows = []
    for method in ('a', 'b'):
        for case in cases:
            urls = (['https://source.org/answer'] if case['id'] == 'f' else ['https://other.org/x', 'https://source.org/target']) if method == 'a' else ['https://other.org/x']
            results = [{'url': url, 'title': 'Result', 'snippet': '42' if method == 'a' else 'unrelated', 'summary': '', 'content': ''} for url in urls]
            rows.append({'method': method, 'case_id': case['id'], 'http_status': 200, 'error': None, 'elapsed_ms': 100, 'cost': 0.01, 'results': results, 'metrics': evaluate(case, results)})
    groups = [{'id': key, 'label': {'en': key, 'zh': key}, 'case_ids': ids} for key, ids in [('overall', ['f', 's']), ('factual', ['f']), ('scenario', ['s'])]]
    soup = BeautifulSoup(population_summary(profile, rows, {'metric_populations': groups}, False), 'html.parser')
    factual = soup.select('[data-population="factual"] tbody tr')
    assert [x.get_text().replace('★ ', '') for x in factual[0].select('td')][1:3] == ['1/1 · 100.0%', '1/1 · 100.0%']
    assert [x.get_text() for x in factual[1].select('td')][1:3] == ['0/1 · 0.0%', '0/1 · 0.0%']
    scenario = soup.select('[data-population="scenario"] tbody tr')
    assert [x.get_text().replace('★ ', '') for x in scenario[0].select('td')][1:3] == ['1/1 · 100.0%', '0.500']
    assert len(soup.select('[data-population="overall"] th')) == 6
    assert len(soup.select('[data-population="factual"] th')) == 7
    independent = {"independent_benchmarks": True, "metric_populations": [
        dict(groups[2], id="custom", benchmark_root=True),
        dict(groups[1], benchmark_root=True), groups[2]]}
    home = BeautifulSoup(population_summary(profile, rows, independent, False), "html.parser")
    assert [node["data-population"] for node in home.select("[data-population]")] == ["custom", "factual"]
    from harness.web_search.report import executive_table
    grades = [
        {"method": "a", "case_id": "s", "ndcg": 1.0, "precision": 0.4, "success": True},
        {"method": "b", "case_id": "s", "ndcg": 0.0, "precision": 0.0, "success": False},
    ]
    landing = BeautifulSoup(executive_table(profile, rows, independent, grades, True), "html.parser")
    assert [node["data-population"] for node in landing.select("[data-population]")] == ["custom", "factual"]
    custom = landing.select_one('[data-population="custom"]')
    assert not custom.find_parent("details")
    assert [x.get_text().replace("★ ", "") for x in custom.select("tbody tr")[0].select("td")][1:4] == ["100.0", "40.0%", "100.0%"]
    assert [x.get_text() for x in custom.select("tbody tr")[1].select("td")][1:4] == ["0.0", "0.0%", "0.0%"]
    assert landing.select_one('[data-population="factual"]').select_one("tbody td:nth-child(2)").get_text().replace("★ ", "") == "1/1 · 100.0%"
    assert landing.select_one('a[href="quality.html#relevance-scores"]')
    from harness.web_search.report import interpretation_summary_table
    interpretation = BeautifulSoup(interpretation_summary_table(profile, rows, False, independent), "html.parser")
    assert [node["data-population"] for node in interpretation.select("[data-population]")] == ["custom", "factual"]
    assert "2/2" not in interpretation.get_text()
    detail = BeautifulSoup(population_summary(profile, rows, independent, False, diagnostics=True), "html.parser")
    assert [node["data-population"] for node in detail.select("[data-population]")] == ["scenario"]


def test_reference_quarantine_is_common_and_never_removes_latency_or_cost():
    from lexbench_websearch.scoring import evaluate
    from harness.web_search.report import population_summary
    cases = [{"id": key, "query": key, "reference_answer": "42", "reference_urls": ["https://source.org/answer"]} for key in ("sound", "wrong")]
    profile = {"cases": cases, "methods": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "protocol": {"top_k": 5}, "factual_quality_exclusions": ["wrong"]}
    rows = []
    for method in ("a", "b"):
        for case in cases:
            results = [{"url": "https://source.org/answer", "title": "Source", "snippet": "42", "summary": "", "content": ""}]
            rows.append({"method": method, "case_id": case["id"], "http_status": 200, "error": None, "elapsed_ms": 100 if case["id"] == "sound" else 900, "cost": .01, "results": results, "metrics": evaluate(case, results)})
    content = {"independent_benchmarks": True, "metric_populations": [{"id": "factual", "benchmark_root": True, "label": {"en": "Factual", "zh": "事实题"}, "case_ids": ["sound", "wrong"]}]}
    soup = BeautifulSoup(population_summary(profile, rows, content, True), "html.parser")
    for row in soup.select("tbody tr"):
        text = [cell.get_text().replace("★ ", "") for cell in row.select("td")]
        assert text[1:3] == ["1/1 · 100.0%", "1/1 · 100.0%"]
        assert text[3] == "500.00"
        assert text[-1] == "0.02 元"
    assert soup.select_one("[data-quality-exclusions]")


def test_collection_description_separates_retries_from_logical_results(tmp_path):
    from harness.web_search.report import check, cohort_summary, fairness_table
    example(tmp_path)
    profile, rows = check(tmp_path / "report", return_data=True)
    profile["report_cohorts"] = [{"id": "batch", "label": {"zh": "测试批次", "en": "Test batch"}, "case_ids": [c["id"] for c in profile["cases"]]}]
    for row in rows:
        row["cohort"] = "batch"
    rows[0]["attempts"] = [{"http_status": 503}, {"http_status": 200}]
    page = BeautifulSoup(cohort_summary(profile, rows, True), "html.parser")
    assert "4 份最终结果" in page.get_text()
    assert "实际请求为 5 次" in page.get_text()
    assert "最终结果数" in page.select_one("thead").get_text()
    budget = BeautifulSoup(fairness_table(profile, rows, True), "html.parser")
    assert "请求上限（条 × 任务数）" in budget.get_text()
    assert "调用次数" not in budget.get_text()


def test_concurrent_three_method_collection_renders_two_selected_methods(tmp_path):
    from harness.record_io import write_json, bytes_sha256
    from harness.web_search.report import load_report_runs, execution_settings

    example(tmp_path)
    source = tmp_path / 'fixture'
    profile, rows, _ = load_run(source)
    third = dict(profile['methods'][0], id='third', key_env='THIRD_KEY', label='Fixture C')
    profile['methods'].append(third)
    profile['protocol'].update(concurrency=3, order='bounded calls')
    profile['execution_policy'] = {
        'purpose': 'quality', 'max_in_flight': 3,
        'resources': {m['id']: {'capacity': 1, 'reason': 'One provider lane'}
                      for m in profile['methods']},
        'assignments': {m['id']: [m['id']] for m in profile['methods']}}
    originals = [row for row in rows if row['method'] == profile['methods'][0]['id']]
    for row in originals:
        identity = row['id'].replace(row['method'], 'third')
        relative = 'calls/' + identity + '.json'
        (source / relative).write_bytes((source / row['evidence']['path']).read_bytes())
        rows.append(dict(row, method='third', id=identity,
                         evidence=dict(row['evidence'], path=relative)))
    write_json(source / 'profile.json', profile)
    (source / 'results.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
    receipt = json.loads((source / 'receipt.json').read_text(encoding='utf-8'))
    receipt.update(profile_sha256=bytes_sha256((source / 'profile.json').read_bytes()),
                   results_sha256=bytes_sha256((source / 'results.jsonl').read_bytes()), call_count=len(rows))
    write_json(source / 'receipt.json', receipt)
    chosen = [profile['methods'][0]['id'], 'third']
    projected, selected, _, _ = load_report_runs(source, [], {}, method_ids=chosen)
    assert [m['id'] for m in projected['methods']] == chosen
    assert len(projected['collection_methods']) == 3
    assert len(selected) == 4
    assert '3' in execution_settings(projected, False)
    content = tmp_path / 'content.json'
    write_json(content, {'comparison_methods': chosen})
    output = tmp_path / 'selected'
    build(output, source=source, content=content)
    assert check(output)


def test_uncertain_primary_scores_show_intervals_without_a_unique_winner(tmp_path):
    from harness.web_search.report import population_summary

    example(tmp_path)
    profile, rows, _ = load_run(tmp_path / 'fixture')
    content = dict(uncertainty='paired-query-bootstrap/1', metric_populations=[dict(
        id='overall', metric_kind='relevance', label={'en':'Scenarios','zh':'场景'},
        case_ids=[case['id'] for case in profile['cases']])])
    scores = [dict(case_id=case['id'],method=m['id'],
                   ndcg=((1.0,0.0),(0.0,0.8))[ci][mi],precision=0.5,success=0.5)
              for ci,case in enumerate(profile['cases']) for mi,m in enumerate(profile['methods'])]
    page = BeautifulSoup(population_summary(profile,rows,content,False,relevance_scores=scores),'html.parser')
    cells = page.select('td[data-metric="population-1"]')
    assert cells and all('95% CI [' in cell.get_text() for cell in cells)
    from harness.reporting.presentation import check_presentation
    check_presentation(page)
    assert not any(cell.select('.comparison-winner') for cell in cells)


def test_uncertainty_report_passes_shared_release_presentation_checks(tmp_path):
    example(tmp_path,comparison=True)
    content = json.loads((tmp_path/'content.json').read_bytes())
    content['uncertainty'] = 'paired-query-bootstrap/1'
    (tmp_path/'content.json').write_text(json.dumps(content),encoding='utf-8')
    output = tmp_path/'uncertainty-report'
    build(output,source=tmp_path/'fixture',content=tmp_path/'content.json',
          relevance=tmp_path/'labels.json',include_english=True)
    assert check(output)
