"""Track-independent semantic checks for comparison cells and result evidence UI."""
import math
from collections import defaultdict
from bs4 import BeautifulSoup


def check_presentation(html):
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    for table in soup.select('table'):
        groups = defaultdict(list)
        for cell in table.select('td[data-metric-direction]'):
            direction = cell['data-metric-direction']
            if direction not in ('min', 'max') or not cell.get('data-metric'):
                raise ValueError('Numeric comparison lacks metric identity or direction')
            value = cell.get('data-sort')
            if value is None:
                if cell.select_one('.comparison-winner'):
                    raise ValueError('Unavailable value cannot be highlighted')
                continue
            number = float(value)
            if not math.isfinite(number):
                raise ValueError('Comparison contains a non-finite value')
            groups[(cell['data-metric'], direction)].append((number, cell))
        for (_, direction), values in groups.items():
            extreme = (min if direction == 'min' else max)(v for v, _ in values)
            unique = sum(v == extreme for v, _ in values) == 1
            for value, cell in values:
                marked = cell.select_one('.comparison-winner') is not None
                if marked != (unique and value == extreme):
                    raise ValueError('Column extremum highlight missing, incorrect, or tied')
    for detail in soup.select('[data-report-detail]'):
        if len(detail.select('[data-result-summary]')) != 1:
            raise ValueError('Result detail requires exactly one summary')
        if any(pre.find_parent('dialog') is None and pre.find_parent('details') is None for pre in detail.select('pre')):
            raise ValueError('Raw evidence must not be flattened into the result summary')
        ids = [node['id'] for node in detail.select('[id]')]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate evidence target')
        for button in detail.select('[data-call-log-open]'):
            dialog = detail.find('dialog', id=button['data-call-log-open'])
            if dialog is None or not dialog.select_one('[data-call-log-close]') or not dialog.get('aria-labelledby'):
                raise ValueError('Evidence trigger lacks an accessible closable dialog')
        if detail['data-report-detail'] == 'failed':
            if detail.select_one('[data-failure-diagnosis]') is None or detail.select_one('#result-logs pre[data-kind="error"], #result-logs [data-log-unavailable]') is None:
                raise ValueError('Failed result lacks diagnosis or exact error evidence')
    return soup
