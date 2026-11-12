"""Track-independent HTML escaping and semantic checks for comparisons and evidence UI."""
import html as html_text
import math
from collections import defaultdict
from html.parser import HTMLParser

from bs4 import BeautifulSoup


def esc(value, *, none=""):
    """Escape one value for HTML text or a quoted attribute, rendering ``None`` as ``none``."""
    return html_text.escape(str(none if value is None else value), quote=True)


def check_presentation(html):
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    for table in soup.select('table'):
        if table.get('data-sort-controls') == 'buttons':
            headers = table.select('thead th')
            for index, header in enumerate(headers):
                control = header.select_one('button[type="button"][data-sort-column]')
                if (control is None or control.get('data-sort-column') != str(index)
                        or not control.get_text(strip=True) or header.get('aria-sort') not in
                        ('none', 'ascending', 'descending')):
                    raise ValueError('Sortable comparison needs an accessible control for every column')
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
                if marked != (
                    (unique or table.get("data-highlight-ties") == "all") and value == extreme
                ):
                    raise ValueError('Column extremum highlight missing, incorrect, or tied')
    for inventory in soup.select('[data-dataset-inventory]'):
        if not soup.select_one('form[data-dataset-controls] input[type="search"]') or not inventory.has_attr('data-sortable'):
            raise ValueError('Dataset inventory needs independent search and sorting controls')
    for detail in soup.select('[data-report-detail]'):
        if len(detail.select('[data-result-summary]')) != 1:
            raise ValueError('Result detail requires exactly one summary')
        if any(
            pre.find_parent('dialog') is None and pre.find_parent('details') is None
            for pre in detail.select('pre')
        ):
            raise ValueError('Raw evidence must not be flattened into the result summary')
        ids = [node['id'] for node in detail.select('[id]')]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate evidence target')
        for button in detail.select('[data-call-log-open]'):
            dialog = detail.find('dialog', id=button['data-call-log-open'])
            if (dialog is None or not dialog.select_one('[data-call-log-close]')
                    or not dialog.get('aria-labelledby')):
                raise ValueError('Evidence trigger lacks an accessible closable dialog')
        if detail['data-report-detail'] == 'failed':
            error_target = (
                '#result-logs pre[data-kind="error"], #result-logs [data-log-unavailable], '
                '[data-result-logs] pre[data-kind="error"], '
                '[data-result-logs] [data-log-unavailable]'
            )
            if (detail.select_one('[data-failure-diagnosis]') is None
                    or detail.select_one(error_target) is None):
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


def metric_guide(definitions, *, title, headings):
    """One reusable reader guide, driven by the same metric identities as comparisons."""
    required = ("id", "label", "meaning", "unit", "population", "direction")
    if not definitions or len({d.get("id") for d in definitions}) != len(definitions):
        raise ValueError("Metric definitions must be nonempty and unique")
    rows = []
    for definition in definitions:
        if any(
            not isinstance(definition.get(k), str) or not definition[k].strip() for k in required
        ):
            raise ValueError("Every metric needs its meaning, unit, population and direction")
        rows.append(
            '<tr id="metric-'
            + esc(definition["id"])
            + '">'
            + "".join(
                "<td>" + esc(definition[k]) + "</td>"
                for k in ("label", "meaning", "unit", "population", "direction")
            )
            + "</tr>"
        )
    return (
        "<details data-metric-guide><summary>"
        + esc(title)
        + '</summary><div class="table-wrap"><table><thead><tr>'
        + "".join("<th>" + esc(h) + "</th>" for h in headings)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div></details>"
    )


def check_metric_guide(document, definitions, *, title, headings):
    soup = BeautifulSoup(document, "html.parser") if isinstance(document, str) else document
    actual = soup.select("[data-metric-guide]")
    expected = BeautifulSoup(
        metric_guide(definitions, title=title, headings=headings), "html.parser"
    )
    if len(actual) != 1 or actual[0].get_text(" ", strip=True) != expected.get_text(
        " ", strip=True
    ):
        raise ValueError("Metric guide is missing or differs from the metric definitions")

    def cells(guide):
        return [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
            for row in guide.select("thead tr, tbody tr")
        ]

    if cells(actual[0]) != cells(expected):
        raise ValueError("Metric guide columns differ from the metric definitions")
    if [r.get("id") for r in actual[0].select("tbody tr")] != [
        "metric-" + d["id"] for d in definitions
    ]:
        raise ValueError("Metric explanation targets differ from their definitions")
