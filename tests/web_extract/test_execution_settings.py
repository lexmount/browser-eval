"""Reports must expose the load under which call latency was measured."""

import pytest
from bs4 import BeautifulSoup

from harness.web_extract.example import create
from harness.web_extract.report import _check_rendered_report, execution_settings


def test_multi_method_report_uses_every_assigned_shared_limit():
    methods = ["first", "second"]
    policy = {
        "purpose": "quality", "max_in_flight": 2,
        "resources": {"first": {"capacity": 2}, "second": {"capacity": 2},
                      "account": {"capacity": 1, "reason": "Shared account"}},
        "assignments": {method: [method, "account"] for method in methods},
    }
    contract = {"methods": methods, "concurrency": 2, "execution_policy": policy}
    html = execution_settings({method: [contract] for method in methods}, methods, {}, "en")
    rows = BeautifulSoup(html, "html.parser").select("tbody tr")
    assert len(rows) == 2
    assert [row.select("td")[1].get_text(strip=True) for row in rows] == ["1", "1"]


@pytest.mark.parametrize(
    "mode,language",
    [
        ("compatibility", "en"),
        ("full-cohort-progress", "en"),
        ("field-extraction-cohort", "zh"),
    ],
)
@pytest.mark.parametrize("damage", ["missing", "wrong_count", "duplicate"])
def test_execution_settings_follow_measured_contracts(tmp_path, mode, language, damage):
    output = tmp_path / "report"
    create(output, mode=mode, language=language)
    _check_rendered_report(output)
    path = output / language / "protocol.html"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    section = soup.select_one("[data-execution-settings]")
    assert section is not None
    assert section.select_one("table[data-method-column]") is not None
    if damage == "missing":
        section.decompose()
    elif damage == "duplicate":
        section.insert_after(BeautifulSoup(str(section), "html.parser"))
    else:
        section.select("tbody tr")[0].select("td")[1].string = "999"
    path.write_text(str(soup), encoding="utf-8")
    with pytest.raises(ValueError, match="execution settings"):
        _check_rendered_report(output)
