"""Track-independent semantic checks for comparison cells and result evidence UI."""
import math
from collections import defaultdict
from html.parser import HTMLParser

from bs4 import BeautifulSoup


def check_presentation(html):
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    for table in soup.select('table'):
        groups = defaultdict(list)
        for cell in table.select('td[data-metric-direction]'):
            direction = cell['data-metric-direction']
            if direction not in ('min', 'max', 'neutral') or not cell.get('data-metric'):
                raise ValueError('Numeric comparison lacks metric identity or direction')
            if direction == 'neutral':
                if cell.select_one('.comparison-winner'):
                    raise ValueError('Out-of-cohort value cannot be highlighted')
                continue
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


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "pre", "code"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "pre", "code"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def visible_text(html: str) -> str:
    """Extract authored prose, excluding scripts, styles and verbatim evidence blocks."""
    parser = _VisibleTextParser()
    parser.feed(html)
    return " ".join(parser.parts)
