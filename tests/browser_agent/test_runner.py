from webarena_verified.types.task import WebArenaSite

from harness.browser_agent.runner import SITE_AUTH_HEADERS, _configure_benchmark, _user_prompt


def test_official_container_urls_and_header_auth() -> None:
    benchmark = _configure_benchmark()

    assert (
        benchmark.config.get_environment(WebArenaSite.SHOPPING_ADMIN).active_url
        == "http://host.docker.internal:7780/admin"
    )
    assert SITE_AUTH_HEADERS == {
        WebArenaSite.SHOPPING: "X-M2-Customer-Auto-Login",
        WebArenaSite.SHOPPING_ADMIN: "X-M2-Admin-Auto-Login",
        WebArenaSite.REDDIT: "X-Postmill-Auto-Login",
    }


def test_user_prompt_exposes_schema_but_not_reference_answer() -> None:
    benchmark = _configure_benchmark()
    task = benchmark.get_task(41)
    prompt = _user_prompt(task, ["http://host.docker.internal:7780/admin"])

    assert "task_type: RETRIEVE" in prompt
    assert '"type":"array"' in prompt
    assert "hollister" not in prompt
