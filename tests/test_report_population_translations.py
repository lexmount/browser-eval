"""Population-dependent report prose follows the rendered matrix."""
from pathlib import PurePosixPath

from bs4 import BeautifulSoup
import pytest

from harness.web_fetch.report import overall_ranking, overview_all_call_latency
from harness.web_fetch.report_i18n import localize_html, translate_text


@pytest.mark.parametrize("task_count,method_count", [(430, 2), (17, 3)])
def test_ranking_and_latency_use_the_actual_population(task_count, method_count):
    cases = {
        f"case-{index}": {"id": f"case-{index}", "track": "web", "task_kind": "body"}
        for index in range(task_count)
    }
    methods = [f"method-{index}" for index in range(method_count)]
    records = [
        {"provider": method, "case_id": case_id, "ok": True,
         "elapsed_ms": 100, "metrics": {"content_pass": True}}
        for method in methods for case_id in cases
    ]
    chinese = overall_ranking(records, cases) + overview_all_call_latency(records, cases)
    english = localize_html(chinese, PurePosixPath("index.html"))
    assert f"{task_count} 个计划任务中的通过数 / 可判定数" in chinese
    assert (
        f"Passed / decidable results among {task_count} planned tasks; each task "
        "has equal weight, and undecidable results are excluded from the denominator."
    ) in english
    assert f"Each method uses the same {task_count} tasks" in english
    assert "个计划任务中的通过数" not in english
    for page in (chinese, english):
        soup = BeautifulSoup(page, "html.parser")
        for identifier in ("ranking-overall", "all-call-latency-table"):
            rows = soup.select(f"#{identifier} tbody tr")
            assert len(rows) == method_count
            assert {row.select_one("a")["href"] for row in rows} == {
                f"providers/{method}.html" for method in methods
            }
    if task_count != 430:
        assert "430" not in english


@pytest.mark.parametrize("task_count", [430, 17])
def test_plain_latency_translation_preserves_wording_for_any_task_count(task_count):
    chinese = (
        f"每种方法均使用同一组 {task_count} 个任务，包含通过、未通过、错误和超时。"
        "耗时是单次调用从输入到返回的时间，不包含调用之间的统一冷却间隔。"
        "每种方法内部按案例依次执行；方法之间的并发安排和限制见调用链检查。"
        "耗时仍受本地与远程环境、网络和产品内部限制影响。"
    )
    assert translate_text(chinese) == (
        f"Each method uses the same {task_count} tasks, including passes, failures, errors, "
        "and timeouts. Latency runs from input to return and excludes the common cooldown "
        "between calls. Cases execute sequentially within each method; inter-method "
        "scheduling and limits are documented in the call-chain audit. Local and remote "
        "environments, networks, and product limits still affect latency."
    )
