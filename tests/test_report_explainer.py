"""Unbound reports describe their inputs; authored history needs explicit assets."""
from pathlib import PurePosixPath
import pytest
from harness.web_fetch.report_explainer import render_explainer
from harness.web_fetch.report_i18n import localize_html


@pytest.mark.parametrize('providers', [('custom-browser',), ('moli', 'chromium', 'lightpanda', 'obscura', 'kitesurf'), ('cowagent-built-in', 'moli-webfetch')])
@pytest.mark.parametrize('authored', [True, False])
def test_unbound_report_uses_its_actual_population(tmp_path, providers, authored):
    cases = {f'task-{i}': {'id': f'task-{i}', 'track': 'web', 'task_kind': 'body'} for i in range(3)}
    rows = [{'provider': name, 'provider_type': 'headless_markdown', 'case_id': case_id,
             'ok': True, 'metrics': {'content_pass': True}} for name in providers for case_id in cases]
    page = render_explainer(tmp_path, cases, rows, authored=authored)
    english = localize_html(page, PurePosixPath('guide/index.html'))
    assert f'{len(rows)}/{len(rows)}' in page
    assert f'3 tasks and {len(rows)} logical results' in english
    assert 'Moli 1.1.1' not in page
    assert '150' not in page
    assert not (tmp_path / 'guide/article.html').exists()
    assert not (tmp_path / 'guide/interpretation-evidence.json').exists()


def test_unbound_report_does_not_read_legacy_speed_or_rendering_assets(tmp_path):
    (tmp_path / 'speed-cohort').mkdir()
    (tmp_path / 'speed-cohort/speed-comparison.json').write_text('not JSON')
    page = render_explainer(tmp_path, {}, [])
    assert '0/0' in page
