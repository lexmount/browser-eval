"""A diagnostic report switches acceptance rules while keeping saved calls."""
import hashlib
import json
import shutil
from copy import deepcopy

import pytest

from harness.web_fetch.report_build import build_diagnostic_cohorts
from harness.web_fetch.report_diagnostic import check_diagnostic_report
from lexbench_webfetch.content_scopes import select_content_scope
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_content_scopes import annotated_case
from tests.test_report_templates import diagnostic_example


def paired_input(root):
    model = diagnostic_example(root)
    original = deepcopy(model)
    child = root.parent/'full-input'
    shutil.copytree(root, child)
    for scope, directory in [('main', root), ('full', child)]:
        current = deepcopy(original)
        current.update(content_scope=scope, paired_content_scope=True,
                       scope_bundle={'default_scope': 'main', 'views': {'main': '.', 'full': 'content-scopes/full'}},
                       scope_views=[{'id': 'main', 'label': '主要内容', 'href': 'index.html' if scope == 'main' else '../../index.html'},
                                    {'id': 'full', 'label': '完整内容', 'href': 'content-scopes/full/index.html' if scope == 'main' else 'index.html'}])
        for row in current['rows']:
            case = annotated_case()
            case.update(id=row['id'], url=row['url'])
            for profile in (case['content_scopes'][name] for name in ('main', 'full')):
                profile['requirements']['min_content_chars'] = 1
                profile['requirements']['required_facts'][0]['alternatives'] = ['Required article body']
            metrics = score_attempt(case, (directory/row['answer']['path']).read_bytes(), ok=True)
            selected = metrics['content_scope_metrics'][scope]
            other = 'full' if scope == 'main' else 'main'
            row['initial_pass'] = task_passed(True, selected)
            row['cause'] = 'passed' if row['initial_pass'] else 'challenge'
            row['failure'] = '' if row['initial_pass'] else 'Required content missing'
            log = {'attempt_id': row['id'], 'elapsed_ms': 100, 'ok': True, 'metrics': selected,
                   'uniform_'+other+'_content': {'metrics': metrics['content_scope_metrics'][other],
                                                'passed': task_passed(True, metrics['content_scope_metrics'][other])}}
            for key, value in [('log', log), ('reference', select_content_scope(case, scope)[0])]:
                path = directory/row[key]['path']
                path.write_text(json.dumps(value), encoding='utf-8')
                row[key]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        passed = sum(row['initial_pass'] for row in current['rows'])
        current['metrics'] = {'articles': [passed, 2], 'overall': [passed, 2]}
        (directory/'report.json').write_text(json.dumps(current), encoding='utf-8')
    (root/'content-scopes').mkdir()
    shutil.move(child, root/'content-scopes/full')


def test_diagnostic_report_keeps_both_views_in_one_package(tmp_path):
    source = tmp_path/'input'
    paired_input(source)
    output = tmp_path/'report'
    build_diagnostic_cohorts(source, output)
    assert check_diagnostic_report(output)['status'] == 'passed'
    main = json.loads((output/'report.json').read_text(encoding='utf-8'))
    full = json.loads((output/'content-scopes/full/report.json').read_text(encoding='utf-8'))
    assert main['metrics']['overall'] == [1, 2]
    assert full['metrics']['overall'] == [0, 2]
    for relative in ('index.html', 'analysis/index.html', 'attempts/complete.html'):
        assert 'data-content-scope="full"' in (output/relative).read_text(encoding='utf-8')
        assert 'data-content-scope="main"' in (output/'content-scopes/full'/relative).read_text(encoding='utf-8')
        assert 'hreflang="en"' not in (output/relative).read_text(encoding='utf-8')
        assert 'hreflang="en"' not in (output/'content-scopes/full'/relative).read_text(encoding='utf-8')
    shutil.rmtree(output/'content-scopes/full')
    with pytest.raises((ValueError, FileNotFoundError)):
        check_diagnostic_report(output)


@pytest.mark.parametrize('damage', ['elapsed_ms', 'reference', 'answer'])
def test_scope_bundle_rejects_independently_rebound_inconsistent_evidence(tmp_path, damage):
    source = tmp_path/'input'
    paired_input(source)
    child = source/'content-scopes/full'
    model = json.loads((child/'report.json').read_text(encoding='utf-8'))
    row = model['rows'][0]
    kind = 'log' if damage == 'elapsed_ms' else damage
    path = child/row[kind]['path']
    if damage == 'answer':
        path.write_text('Different response', encoding='utf-8')
        row['first_answer']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        value = json.loads(path.read_text(encoding='utf-8'))
        if damage == 'reference':
            value['task_prompt'] = 'A different requirement'
        else:
            value['elapsed_ms'] += 1
        path.write_text(json.dumps(value), encoding='utf-8')
    row[kind]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (child/'report.json').write_text(json.dumps(model), encoding='utf-8')
    with pytest.raises(ValueError, match='scope changed|saved response bytes'):
        build_diagnostic_cohorts(source, tmp_path/'report')


def test_diagnostic_bundle_uses_the_normal_package_publication_gate(tmp_path):
    from harness.web_fetch.report_package import build as package
    from harness.web_fetch.publication_check import check
    source = tmp_path/'input'
    paired_input(source)
    profile = tmp_path/'profile.json'
    profile.write_text(json.dumps({'variant': 'diagnostic-cohorts', 'cohort_plan': [
        {'id': 'articles', 'case_count': 2, 'case_ids_sha256': hashlib.sha256(b'challenge\ncomplete\n').hexdigest()}]}), encoding='utf-8')
    output = tmp_path/'report'
    build_diagnostic_cohorts(source, output, profile=profile)
    archive = tmp_path/'report.zip'
    package(output, archive)
    check(archive, output)
    (output/'content-scopes/full/analysis/index.html').write_text('Changed report', encoding='utf-8')
    with pytest.raises(ValueError):
        check(archive, output)


def test_pair_cannot_bind_two_matching_hashes_to_a_different_response(tmp_path):
    from harness.web_fetch.report_diagnostic import load_diagnostic
    source = tmp_path/'input'
    paired_input(source)
    model = json.loads((source/'report.json').read_text(encoding='utf-8'))
    row = model['rows'][0]
    path = source/row['log']['path']
    log = json.loads(path.read_text(encoding='utf-8'))
    log['metrics']['content_sha256'] = 'a'*64
    log['uniform_full_content']['metrics']['content_sha256'] = 'a'*64
    path.write_text(json.dumps(log), encoding='utf-8')
    row['log']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (source/'report.json').write_text(json.dumps(model), encoding='utf-8')
    with pytest.raises(ValueError, match='saved response bytes'):
        load_diagnostic(source)


def test_scope_switch_keeps_contestant_names_and_configuration_fixed(tmp_path):
    source = tmp_path/'input'
    paired_input(source)
    path = source/'content-scopes/full/report.json'
    model = json.loads(path.read_text(encoding='utf-8'))
    model['cohorts'][0]['label'] += ' (full content)'
    path.write_text(json.dumps(model), encoding='utf-8')
    with pytest.raises(ValueError, match='contestant identities or labels'):
        build_diagnostic_cohorts(source, tmp_path/'report')


@pytest.mark.parametrize('scope', ['main', 'full'])
@pytest.mark.parametrize('navigation', [None, []])
def test_bundled_scores_cannot_remove_the_reader_switch(tmp_path, scope, navigation):
    source = tmp_path/'input'
    paired_input(source)
    path = source/('report.json' if scope == 'main' else 'content-scopes/full/report.json')
    model = json.loads(path.read_text(encoding='utf-8'))
    model['scope_views'] = navigation
    path.write_text(json.dumps(model), encoding='utf-8')
    with pytest.raises(ValueError, match='scope navigation'):
        build_diagnostic_cohorts(source, tmp_path/'report')


@pytest.mark.parametrize('field', ['provider_identity', 'process', 'request', 'call_error'])
def test_adapter_call_fields_cannot_differ_between_scoring_views(tmp_path, field):
    source = tmp_path/'input'
    paired_input(source)
    child = source/'content-scopes/full'
    path = child/'report.json'
    model = json.loads(path.read_text(encoding='utf-8'))
    row = model['rows'][0]
    log = child/row['log']['path']
    value = json.loads(log.read_text(encoding='utf-8'))
    value[field] = {'changed': True}
    log.write_text(json.dumps(value), encoding='utf-8')
    row['log']['sha256'] = hashlib.sha256(log.read_bytes()).hexdigest()
    path.write_text(json.dumps(model), encoding='utf-8')
    with pytest.raises(ValueError, match='saved invocation evidence: '+field):
        build_diagnostic_cohorts(source, tmp_path/'report')


def test_only_declared_alternate_subtrees_are_checked_as_child_reports(tmp_path):
    source = tmp_path/'input'
    paired_input(source)
    output = tmp_path/'report'
    build_diagnostic_cohorts(source, output)
    rogue = output/'content-scopes/unrequested/index.html'
    rogue.parent.mkdir()
    rogue.write_text('<html><a href="missing.html">Broken</a></html>', encoding='utf-8')
    with pytest.raises(ValueError):
        check_diagnostic_report(output)


def test_direct_alternate_check_enforces_the_shared_contestant_identity(tmp_path):
    from harness.web_fetch.report import generate_cohort_diagnostic
    from harness.reporting.versions import bind_build_version
    source = tmp_path/'input'
    paired_input(source)
    output = tmp_path/'report'
    build_diagnostic_cohorts(source, output)
    child = output/'content-scopes/full'
    assert check_diagnostic_report(child)['status'] == 'passed'
    path = child/'report.json'
    model = json.loads(path.read_text(encoding='utf-8'))
    model['cohorts'][0]['label'] = 'A different contestant'
    path.write_text(json.dumps(model), encoding='utf-8')
    generate_cohort_diagnostic(child, model)
    manifest_path = child/'report-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['model_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest['webstone'] = bind_build_version(child)
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='contestant identities'):
        check_diagnostic_report(child)
