"""Exercise generated WCXB editions and navigation with synthetic scored evidence."""

import runpy
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from harness.report_languages import check_language_contract, localized_report_routes
from harness.web_fetch.report import generate_wcxb
from tests.wcxb_fixture import create_report_inputs


@pytest.mark.parametrize('include_english', [False, True])
def test_wcxb_language_output_and_every_language_link(tmp_path, include_english):
    inputs = create_report_inputs(tmp_path, english_source=include_english)
    root = inputs[0]
    if include_english:
        generate_wcxb(*inputs, include_english=True)
    else:
        generate_wcxb(*inputs)
    check_language_contract(root)
    routes = {path.relative_to(root).as_posix() for path in root.rglob('*.html')
              if path.relative_to(root).parts[0] != 'en'}
    editions = localized_report_routes(root, routes)
    assert set(editions) == ({'zh', 'en'} if include_english else {'zh'})
    assert 'cases/4017.html' in routes and 'analysis/index.html' in routes
    for route in routes:
        soup = BeautifulSoup((root / route).read_text(), 'html.parser')
        english_link = next(link for link in soup.select('header a') if link.get_text() == 'English')
        assert english_link['href'] == ('en/' + route if include_english else 'en/index.html')
        assert (root / english_link['href']).is_file()
    if not include_english:
        assert {path.relative_to(root / 'en').as_posix()
                for path in (root / 'en').rglob('*') if path.is_file()} == {'index.html'}


@pytest.mark.parametrize('include_english', [False, True])
def test_wcxb_command_forwards_requested_language(tmp_path, monkeypatch, include_english):
    from harness.web_fetch import report_build

    captured = []
    monkeypatch.setattr(report_build, 'build_wcxb_report',
                        lambda *args, **kwargs: captured.append(kwargs) or tmp_path / 'index.html')
    root = Path(__file__).resolve().parents[1]
    argv = [str(root / 'scripts/build_report.py'), '--wcxb', '--evidence', str(tmp_path)]
    if include_english:
        argv.append('--include-english')
    monkeypatch.setattr('sys.argv', argv)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(argv[0], run_name='__main__')
    assert result.value.code == 0
    assert captured == [{'include_english': include_english}]
