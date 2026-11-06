"""Reported process counters retain units, missing data and their limited scope."""

import json

import pytest
from bs4 import BeautifulSoup

from harness.web_extract.example import create
from harness.web_extract.pipeline import digest, read_json, save_json
from harness.web_extract.report import build, check, worker_resources


def measured_example(tmp_path, mode):
    home = create(tmp_path / 'initial', mode=mode,
                  language='en' if mode == 'full-cohort-progress' else 'zh')
    for run in (home / 'runs').iterdir():
        journal = run / 'calls.journal.jsonl'
        if not journal.exists():
            continue
        entries = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
        for index, entry in enumerate(entries):
            path = run / entry['path']
            row = read_json(path)
            if index < 2:
                row['response']['usage'] = {
                    'process_cpu_seconds': 4 - 2 * index if len(entries) > 1 else 1,
                    'peak_rss_native_units': 1048576 if index == 1 else (
                        1024 if len(entries) > 1 else 512),
                    'rss_unit': 'bytes' if index == 1 else 'KiB',
                }
            save_json(path, row)
            entry['sha256'] = digest(path.read_bytes())
        journal.write_text(''.join(json.dumps(entry) + '\n' for entry in entries), encoding='utf-8')
    output = tmp_path / 'measured'
    build(output, home / 'content.json', home=home, profile=home / 'profile.json',
          language='en' if mode == 'full-cohort-progress' else 'zh')
    return output


@pytest.mark.parametrize('mode', ['full-cohort-progress', 'field-extraction-cohort'])
def test_resource_tables_share_scope_units_counts_and_leaders_on_both_pages(tmp_path, mode):
    output = measured_example(tmp_path, mode)
    manifest = read_json(output / 'report-manifest.json')
    for language in manifest['languages']:
        sections = []
        for route in ['index', 'interpretation']:
            soup = BeautifulSoup((output / language / (route + '.html')).read_text(
                encoding='utf-8'), 'html.parser')
            section = soup.select_one('[data-worker-resources]')
            assert section is not None
            sections.append(section)
            rows = section.select('tbody tr')
            cells = [[cell.get_text(strip=True) for cell in row.select('td')] for row in rows]
            assert cells[0][1:] == ['2 / 1', '3.000', '2 / 1', '1.000']
            assert cells[1][1:] == ['1 / 0', '1.000', '1 / 0', '0.500']
            assert len(rows[1].select('.comparison-winner')) == 2
            assert not rows[0].select('.comparison-winner')
            assert cells[1][0] in section.select_one('[data-resource-leaders]').get_text()
            assert 'Firecrawl' in section.get_text() and 'MiB' in section.get_text()
        assert sections[0] == sections[1]
    assert check(output)


@pytest.mark.parametrize('mode', ['full-cohort-progress', 'field-extraction-cohort'])
@pytest.mark.parametrize('route', ['index', 'interpretation'])
@pytest.mark.parametrize('damage', ['missing', 'wrong-value', 'wrong-highlight'])
def test_release_boundary_rejects_resource_errors_even_with_updated_file_hash(
    tmp_path, mode, route, damage
):
    output = measured_example(tmp_path, mode)
    path = output / 'zh' / (route + '.html')
    soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    section = soup.select_one('[data-worker-resources]')
    if damage == 'missing':
        section.decompose()
    elif damage == 'wrong-value':
        section.select_one('tbody td:nth-child(3)').string = '0.000'
    else:
        section.select_one('.comparison-winner').unwrap()
    path.write_text(str(soup), encoding='utf-8')
    manifest = read_json(output / 'report-manifest.json')
    manifest['files']['zh/' + path.name] = digest(path.read_bytes())
    save_json(output / 'report-manifest.json', manifest)
    with pytest.raises(ValueError, match='resource|extremum|highlight'):
        check(output)


def test_invalid_or_absent_process_counters_do_not_become_zero_measurements():
    labels = {'a': 'A', 'b': 'B'}
    rows = [
        {'method': 'a', 'response': {'usage': {'process_cpu_seconds': True}}},
        {'method': 'a', 'response': {'usage': {'process_cpu_seconds': -1,
                                              'peak_rss_native_units': 100,
                                              'rss_unit': 'unknown'}}},
        {'method': 'b', 'response': {'usage': {'process_cpu_seconds': 2}}},
    ]
    section = BeautifulSoup(worker_resources(['a', 'b'], rows, labels, 'en'), 'html.parser')
    first = section.select_one('tbody tr')
    assert [cell.get_text() for cell in first.select('td')][1:] == ['0 / 2', '—', '0 / 2', '—']
    assert not first.select('.comparison-winner')
    assert worker_resources(['a'], rows[:2], labels, 'en') == ''
