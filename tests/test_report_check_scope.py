"""Actual input changes select validation work without weakening release boundaries."""

import json
from collections import Counter

import pytest

from harness.record_io import file_sha256
from harness.reporting import release_check as shared
from harness.reporting.links import check as check_links
from harness.web_fetch.publication_privacy import publication_risk

CALLS = Counter()


@shared.validation_step
def score(reference, output):
    CALLS['score'] += 1
    if reference != output:
        raise ValueError('incorrect extraction')


@shared.validation_step
def page_values(text, expected):
    CALLS['page'] += 1
    if f'data-value="{expected}"' not in text:
        raise ValueError('incorrect displayed field')


@shared.scoped_check('example')
def owner(root):
    CALLS['mandatory'] += 1
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    for name, digest in manifest['files'].items():
        if file_sha256(root / name) != digest:
            raise ValueError('binding mismatch')
        if publication_risk((root / name).read_bytes()):
            raise ValueError('private content')
    check_links(root)
    data = json.loads((root / 'results.json').read_text(encoding='utf-8'))
    for name, row in data.items():
        score(row['expected'], row['actual'])
        page_values((root / name).read_text(encoding='utf-8'), row['expected'])


def rebind(root, **metadata):
    manifest = {'files': {p.name: file_sha256(p) for p in root.iterdir()
                          if p.is_file() and p.name != 'manifest.json'}}
    manifest.update(metadata)
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setenv('BROWSER_EVAL_DATA_HOME', str(tmp_path))
    monkeypatch.delenv('BROWSER_EVAL_FULL_REPORT_CHECK', raising=False)
    implementation = ['original-code']
    monkeypatch.setattr(shared, '_implementation', lambda: implementation[0])
    CALLS.clear()
    root = tmp_path / 'report'
    root.mkdir()
    data = {}
    for name, value in [('index.html', 'price'), ('detail.html', 'stock')]:
        (root / name).write_text(f'<p id="fields" data-value="{value}">Prose</p>', encoding='utf-8')
        data[name] = {'expected': value, 'actual': value}
    (root / 'results.json').write_text(json.dumps(data), encoding='utf-8')
    (root / 'report.css').write_text('p {color: black}', encoding='utf-8')
    rebind(root)
    return root, implementation


def test_unchanged_units_are_reused_but_mandatory_checks_run_again(report, capsys):
    root, _ = report
    owner(root)
    assert CALLS == {'score': 2, 'page': 2, 'mandatory': 1}
    owner(root)
    assert CALLS == {'score': 2, 'page': 2, 'mandatory': 2}
    assert '4 unchanged units reused' in capsys.readouterr().err


@pytest.mark.parametrize('change', ['prose', 'css'])
def test_presentation_edits_only_repeat_the_changed_page(report, change):
    root, _ = report
    owner(root)
    path = root / ('index.html' if change == 'prose' else 'report.css')
    path.write_text(path.read_text(encoding='utf-8').replace('Prose', 'Edited prose') + ' ', encoding='utf-8')
    rebind(root, authored={'note': 'Changed presentation'}, content_sha256='updated')
    owner(root)
    assert CALLS['score'] == 2
    assert CALLS['page'] == (3 if change == 'prose' else 2)
    assert CALLS['mandatory'] == 2


def test_implementation_changes_invalidate_every_unit(report):
    root, implementation = report
    owner(root)
    implementation[0] = 'changed-checker'
    owner(root)
    assert CALLS == {'score': 4, 'page': 4, 'mandatory': 2}


def test_python_optimization_changes_invalidate_routine_passes(report, monkeypatch):
    from types import SimpleNamespace

    root, _ = report
    owner(root)
    runtime = shared.sys
    monkeypatch.setattr(shared, 'sys', SimpleNamespace(
        version=runtime.version, stderr=runtime.stderr,
        flags=SimpleNamespace(optimize=runtime.flags.optimize + 1)))
    owner(root)
    assert CALLS == {'score': 4, 'page': 4, 'mandatory': 2}


def test_changed_reference_and_output_repeat_only_the_affected_units(report):
    root, _ = report
    owner(root)
    data = root / 'results.json'
    data.write_text(data.read_text(encoding='utf-8').replace('price', 'updated-price'), encoding='utf-8')
    page = root / 'index.html'
    page.write_text(page.read_text(encoding='utf-8').replace('price', 'updated-price'), encoding='utf-8')
    rebind(root)
    owner(root)
    assert CALLS == {'score': 3, 'page': 3, 'mandatory': 2}


@pytest.mark.parametrize('change', ['evidence-formatting', 'unknown-metadata', 'new-file'])
def test_unrelated_input_changes_keep_their_own_checks_without_repeating_pure_units(report, change):
    root, _ = report
    owner(root)
    if change == 'evidence-formatting':
        path = root / 'results.json'
        path.write_text(path.read_text(encoding='utf-8') + '\n', encoding='utf-8')
    elif change == 'new-file':
        (root / 'new-contract.txt').write_text('Unknown dependency', encoding='utf-8')
    rebind(root, **({'new-contract': True} if change == 'unknown-metadata' else {}))
    owner(root)
    assert CALLS == {'score': 2, 'page': 2, 'mandatory': 2}


@pytest.mark.parametrize('damage', ['value', 'privacy', 'link', 'binding'])
def test_a_previous_pass_does_not_hide_new_publication_errors(report, damage):
    root, _ = report
    owner(root)
    path = root / 'index.html'
    text = path.read_text(encoding='utf-8')
    if damage == 'value':
        text = text.replace('data-value="price"', 'data-value="wrong"')
    elif damage == 'privacy':
        text += '/Users/private/internal-file'
    elif damage == 'link':
        text += '<a href="detail.html#missing">Evidence</a>'
    else:
        text += 'unbound'
    path.write_text(text, encoding='utf-8')
    if damage != 'binding':
        rebind(root)
    with pytest.raises(ValueError):
        owner(root)


def test_incoming_links_are_rechecked_when_their_target_changes(report):
    root, _ = report
    page = root / 'index.html'
    page.write_text(page.read_text(encoding='utf-8') + '<a href="detail.html#fields">Evidence</a>', encoding='utf-8')
    rebind(root)
    owner(root)
    target = root / 'detail.html'
    target.write_text(target.read_text(encoding='utf-8').replace('id="fields"', 'id="removed"'), encoding='utf-8')
    rebind(root)
    with pytest.raises(ValueError, match='missing fragment'):
        owner(root)


def test_full_audit_bypasses_reuse_without_changing_the_result(report, monkeypatch):
    root, _ = report
    owner(root)
    monkeypatch.setenv('BROWSER_EVAL_FULL_REPORT_CHECK', '1')
    owner(root)
    assert CALLS == {'score': 4, 'page': 4, 'mandatory': 2}


@pytest.mark.parametrize('release_flag', ['release', 'require_complete'])
def test_formal_checks_never_reuse_or_update_routine_receipts(report, release_flag):
    root, _ = report

    @shared.scoped_check('release-contract')
    def gate(root, *, release=False, require_complete=False):
        owner(root)

    gate(root)
    cache = root.parent / 'report-checks'
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in cache.glob('*.json')}
    gate(root, **{release_flag: True})
    gate(root, **{release_flag: True})
    assert CALLS == {'score': 6, 'page': 6, 'mandatory': 3}
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in cache.glob('*.json')}


def test_release_context_ignores_saved_checks_and_leaves_no_new_receipt(report):
    root, _ = report

    @shared.full_validation()
    def release():
        owner(root)

    release()
    release()
    assert CALLS == {'score': 4, 'page': 4, 'mandatory': 2}
    assert not (root.parent / 'report-checks').exists()


def test_package_boundary_runs_fresh_checks_after_a_routine_pass(report):
    from harness.reporting.publication import package_archive

    root, _ = report
    owner(root)
    cache = root.parent / 'report-checks'
    before = {p.name: p.read_bytes() for p in cache.glob('*.json')}
    package_archive(root, root.parent / 'release.zip', root.parent / 'release.json',
                    inputs=[], validate=lambda: owner(root), attest=lambda archive: {})
    assert CALLS['score'] > 2
    assert CALLS['score'] == CALLS['mandatory'] * 2
    assert before == {p.name: p.read_bytes() for p in cache.glob('*.json')}


def test_closure_inputs_are_never_reused(report):
    root, _ = report
    expected = ['price']

    @shared.validation_step
    def validate(value):
        if value != expected[0]:
            raise ValueError('changed captured value')

    @shared.scoped_check('captured-values')
    def check(root):
        validate('price')

    check(root)
    expected[0] = 'stock'
    with pytest.raises(ValueError, match='changed captured'):
        check(root)


def test_damaged_receipt_falls_back_to_a_full_check(report):
    root, _ = report
    owner(root)
    receipt = next((root.parent / 'report-checks').glob('*.json'))
    receipt.write_text('{truncated', encoding='utf-8')
    owner(root)
    assert CALLS['score'] == 4


def test_failed_checks_cannot_install_a_pass_receipt(report):
    root, _ = report
    path = root / 'detail.html'
    original = path.read_text(encoding='utf-8')
    path.write_text(original.replace('data-value="stock"', 'data-value="wrong"'), encoding='utf-8')
    rebind(root)
    with pytest.raises(ValueError, match='incorrect displayed'):
        owner(root)
    assert not list((root.parent / 'report-checks').glob('*.json'))


def test_mutable_or_unknown_types_keep_the_original_execution(report):
    root, _ = report
    observed = []

    @shared.validation_step
    def io_validator(path):
        observed.append(path.read_text(encoding='utf-8'))
        return ('preserve', 'tuple')

    @shared.scoped_check('unknown-input')
    def check(root):
        assert io_validator(root / 'report.css') == ('preserve', 'tuple')

    check(root)
    check(root)
    assert len(observed) == 2


def test_non_json_return_types_are_preserved_and_never_reused(report):
    root, _ = report
    observed = []

    @shared.validation_step
    def count():
        observed.append(True)
        return Counter(items=1)

    @shared.scoped_check('return-types')
    def check(root):
        assert type(count()) is Counter

    check(root)
    check(root)
    assert len(observed) == 2


@pytest.mark.parametrize('change', ['report', 'checker'])
def test_validation_cannot_certify_inputs_that_change_during_the_check(report, change):
    root, implementation = report

    @shared.scoped_check('input-race')
    def check(root):
        score('price', 'price')
        if change == 'report':
            (root / 'report.css').write_text('Changed during validation', encoding='utf-8')
        else:
            implementation[0] = 'changed-during-validation'

    with pytest.raises(ValueError, match='changed during validation'):
        check(root)
    assert not list((root.parent / 'report-checks').glob('*.json'))


def test_order_sensitive_results_do_not_share_an_input_receipt(report):
    root, _ = report

    @shared.validation_step
    def fields(value):
        return list(value)

    @shared.scoped_check('ordered-result')
    def check(root, value):
        return fields(value)

    assert check(root, {'price': 1, 'stock': 2}) == ['price', 'stock']
    assert check(root, {'stock': 2, 'price': 1}) == ['stock', 'price']


@pytest.mark.parametrize('forced', [False, True], ids=['automatic', 'forced-full'])
def test_native_field_order_is_rechecked_after_a_previous_pass(report, monkeypatch, forced):
    from harness.web_extract.report import _check_record_detail, record_field_rows

    root, _ = report
    row = {'case_id': 'one', 'status': 'exact', 'field_results': {
        field: {'expected': [field], 'actual': [field], 'status': 'correct',
                'hit': True, 'exact': True}
        for field in ['price', 'stock']
    }}
    cells = record_field_rows(row, 'zh')
    text = ('<details id="method"><table data-record-fields><tbody>'
            + ''.join('<tr>' + ''.join('<td>' + cell + '</td>' for cell in values) + '</tr>'
                      for values in cells)
            + '</tbody></table><button data-call-log-open data-key="one" '
            'data-src="public-evidence/method.json"></button></details>')

    @shared.scoped_check('ordered-native-fields')
    def check(root, result):
        _check_record_detail(text, result, 'method')

    check(root, row)
    reordered = {**row, 'field_results': dict(reversed(list(row['field_results'].items())))}
    monkeypatch.setenv('BROWSER_EVAL_FULL_REPORT_CHECK', '1' if forced else '0')
    with pytest.raises(ValueError, match='displayed fields differ'):
        check(root, reordered)


@pytest.mark.parametrize('track', [
    'web-extract-products', 'web-extract-records', 'web-search',
    'webfetch-diagnostic', 'trajectory-replay',
])
def test_maintained_track_checks_reuse_successful_units(tmp_path, monkeypatch, capsys, track):
    """Exercise real build/check commands, without private evidence or provider calls."""
    import re

    monkeypatch.setenv('BROWSER_EVAL_DATA_HOME', str(tmp_path))
    monkeypatch.delenv('BROWSER_EVAL_FULL_REPORT_CHECK', raising=False)
    if track.startswith('web-extract-'):
        from harness.web_extract.example import create
        from harness.web_extract.report import check
        output = tmp_path / 'report'
        create(output, mode=('field-extraction-cohort' if track.endswith('records')
                             else 'full-cohort-progress'))
        run = lambda: check(output)
    elif track == 'web-search':
        from harness.web_search.example import example
        from harness.web_search.report import check
        example(tmp_path)
        run = lambda: check(tmp_path / 'report')
    elif track == 'webfetch-diagnostic':
        from harness.web_fetch.report_build import build_diagnostic_cohorts
        from harness.web_fetch.report_diagnostic import check_diagnostic_report
        from tests.test_report_templates import diagnostic_example
        diagnostic_example(tmp_path / 'source')
        output = tmp_path / 'report'
        build_diagnostic_cohorts(tmp_path / 'source', output)
        run = lambda: check_diagnostic_report(output)
    else:
        from harness.trajectory_replay.webarena_report_check import check
        from tests.trajectory_replay.test_webarena_report import build_replay_fixture
        output, _, _, _, _, _, files = build_replay_fixture(tmp_path, monkeypatch)
        run = lambda: check(output, profile_path=files['profile.json'])
    run()
    capsys.readouterr()
    result = run()
    warm = capsys.readouterr().err
    assert 'Report validation: incremental;' in warm
    assert re.search(r'[1-9]\d* unchanged units reused', warm)
    monkeypatch.setenv('BROWSER_EVAL_FULL_REPORT_CHECK', '1')
    assert run() == result
    full = capsys.readouterr().err
    assert 'Report validation: full;' in full
    assert '0 unchanged units reused' in full


def test_prose_rebuild_reuses_measurement_checks_after_profile_rebinding(tmp_path, monkeypatch, capsys):
    from harness.web_extract.example import create
    from harness.web_extract.pipeline import digest, read_json, save_json
    from harness.web_extract.report import build

    monkeypatch.setenv('BROWSER_EVAL_DATA_HOME', str(tmp_path))
    monkeypatch.delenv('BROWSER_EVAL_FULL_REPORT_CHECK', raising=False)
    initial = tmp_path / 'initial'
    home = create(initial, mode='field-extraction-cohort')
    old = read_json(initial / 'report-manifest.json')
    content = home / 'content.json'
    model = read_json(content)
    model['title'] = 'Updated synthetic report title'
    save_json(content, model)
    profile = home / 'profile.json'
    authority = read_json(profile)
    authority['research_report']['content']['sha256'] = digest(content.read_bytes())
    save_json(profile, authority)
    capsys.readouterr()
    updated = tmp_path / 'updated'
    build(updated, content, home=home, profile=profile, language='zh')
    new = read_json(updated / 'report-manifest.json')
    assert old['profile_sha256'] != new['profile_sha256']
    assert (initial / 'data/metrics.json').read_bytes() == (updated / 'data/metrics.json').read_bytes()
    assert 'Report validation: incremental;' in capsys.readouterr().err
