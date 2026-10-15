"""Current report prose follows saved evidence and the supported public CLI."""
import hashlib
import json
import runpy
import shlex
import sys
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from bs4 import BeautifulSoup

from harness.web_fetch import report
from harness.web_fetch.report_i18n import localize_html
from tests.test_report_body_scope import case, record


def test_current_review_does_not_repeat_predecessor_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_RELIABILITY_BANNER", "")
    old, added = case('retained'), case('added')
    records = [record(old), record(added, passed=False)]
    raw = json.dumps({'provider_calls_added': 1, 'inherited_results': 1}).encode()
    (tmp_path/'replacement.json').write_bytes(raw)
    dataset = {'cases': [old, added], 'comparison': {
        'kind': 'uniform_native_local_measurement', 'scoring_policy_version': 2,
        'presentation': {'ranking': 'saved_task_pass_rate'},
        'task_replacement': {'path': 'replacement.json', 'sha256': hashlib.sha256(raw).hexdigest()},
        'call_chain_audit': {'findings': [{'text': 'STALE SCORE AND EXCLUSION'}]}}}
    audit = {'kind': 'local_independent_review', 'review_rounds': [],
             'attempts': [], 'reviewed_attempts': 1, 'expected_attempts': 2,
             'unreviewed_attempt_ids': [records[1]['attempt_id']],
             'accepted_unreviewed_passes': 0, 'unresolved': 0,
             'adjudication_revision': {'adjudicated_attempt_ids': ['retained']}}
    (tmp_path/'dataset.json').write_text(json.dumps(dataset))
    (tmp_path/'result-review.json').write_text(json.dumps(audit))
    report.write_review_page(tmp_path, records)
    page = (tmp_path/'review/index.html').read_text()
    text = BeautifulSoup(page, 'html.parser').get_text(' ', strip=True)
    assert '当前通过 1 条，可比较 2 条，排除 0 条' in text
    assert '补充裁定 1 条' in text
    assert '另有 1 条未独立复核' in text
    assert 'STALE SCORE AND EXCLUSION' not in text
    assert not BeautifulSoup(page, 'html.parser').select('.callout')
    english = localize_html(page, PurePosixPath('review/index.html'))
    assert 'Currently 1 pass among 2 eligible results; 0 are excluded.' in english
    assert 'There are 1 supplemental adjudications' in english


def test_reproduction_command_runs_through_the_current_public_cli(tmp_path):
    c = case('fixture')
    r = record(c)
    (tmp_path/'dataset.json').write_text(json.dumps({
        'cases': [c], 'comparison': {'kind': 'uniform_native_local_measurement'}}))
    (tmp_path/'presentation-binding.json').write_text('{}')
    with patch('harness.web_fetch.report_methods.render_methods', return_value='methods'):
        report.write_support(tmp_path, [r], [], authored_interpretation=False)
    soup = BeautifulSoup((tmp_path/'reproduce/index.html').read_text(), 'html.parser')
    commands = next(p.get_text() for p in soup.select('pre') if 'scripts/build_report.py' in p.get_text())
    assert '--hide-local-status-banners' not in commands
    command = shlex.split(commands.splitlines()[0])
    assert '--presentation-binding' in command
    script = Path(__file__).resolve().parents[1]/'scripts/build_report.py'
    with patch.object(sys, 'argv', [str(script), *command[command.index('scripts/build_report.py')+1:]]), \
         patch('harness.web_fetch.storage.require_evidence', return_value=tmp_path), \
         patch('harness.web_fetch.report_build.build_report', return_value=tmp_path/'index.html') as build:
        try:
            runpy.run_path(str(script), run_name='__main__')
        except SystemExit as result:
            assert result.code == 0
    build.assert_called_once()
    assert build.call_args.kwargs['local_audit'] is True
    assert build.call_args.kwargs['presentation_binding'] is not None
