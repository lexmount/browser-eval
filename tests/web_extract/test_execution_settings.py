"""Reports must expose the load under which call latency was measured."""

import pytest
from bs4 import BeautifulSoup

from harness.web_extract.example import create
from harness.web_extract.report import check


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
    check(output)
    path = output / language / "protocol.html"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    section = soup.select_one("[data-execution-settings]")
    assert section is not None
    if damage == "missing":
        section.decompose()
    elif damage == "duplicate":
        section.insert_after(BeautifulSoup(str(section), "html.parser"))
    else:
        section.select("tbody tr")[0].select("td")[1].string = "999"
    path.write_text(str(soup), encoding="utf-8")
    with pytest.raises(ValueError, match="execution settings"):
        check(output)
