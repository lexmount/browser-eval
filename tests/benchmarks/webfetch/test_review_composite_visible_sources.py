"""Composite APIs discover records and their fields only from visible source content."""

import pytest

from lexbench_webfetch.composite_tasks import evaluate_composite_acceptance
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from tests.benchmarks.webfetch.test_review_owned_fields_exclusions import composite_case


def cases():
    for wildcard in [False, True]:
        group = {
            "id": "items",
            "kind": "named_link",
            "minimum_records": 1,
            "hosts": ["*"] if wildcard else ["example.test"],
            "entry_path": r"/item/\d+",
        }
        for fields in [False, True]:
            configured = dict(group)
            if fields:
                configured["required_fields"] = [{"id": "price", "kind": "price"}]
            for label, wrapper in [
                ("hidden", "<div hidden>\n{}\n</div>"),
                ("styled", '<span style="display:none">\n{}\n</span>'),
                ("closed-details", "<details><summary>More</summary>\n{}\n</details>"),
                ("comment", "<!--\n{}\n-->"),
                ("visible", "{}"),
            ]:
                record = "[Story](https://example.test/item/1)" + ("\nPrice: $12" if fields else "")
                yield (
                    ("wildcard" if wildcard else "ordinary")
                    + ("-fields" if fields else "-names")
                    + "-"
                    + label,
                    composite_case(configured),
                    wrapper.format(record),
                    wrapper == "{}",
                )
        configured = {**group, "required_fields": [{"id": "price", "kind": "price"}]}
        for hidden in [True, False]:
            price = "<div hidden>\nPrice: $12\n</div>" if hidden else "Price: $12"
            yield (
                ("wildcard" if wildcard else "ordinary") + "-field-only-" + str(hidden),
                composite_case(configured),
                "[Story](https://example.test/item/1)\n" + price,
                not hidden,
            )


CASES = list(cases())


@pytest.mark.parametrize("id,case,artifact,expected", CASES, ids=[row[0] for row in CASES])
def test_direct_composite_and_score_attempt_agree_on_visible_records(id, case, artifact, expected):
    direct = evaluate_composite_acceptance(
        artifact, case["composite_acceptance"], base_url=case["url"]
    )
    assert direct["composite_acceptance_pass"] is expected
    metrics = score_attempt(case, artifact.encode(), ok=True)
    assert task_passed(True, metrics, "body") is expected
