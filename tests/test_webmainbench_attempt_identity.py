import pytest

from harness.web_fetch.webmainbench_evidence import require_unique_attempt_ids


def test_cohort_join_rejects_duplicate_physical_attempt_identity():
    physical = [
        {"attempt_id": "moli__case-one__p01"},
        {"attempt_id": "moli-fixed__case-one__p01"},
        {"attempt_id": "moli-fixed__case-one__p01"},
    ]

    with pytest.raises(ValueError, match="Physical attempt identity is not unique"):
        require_unique_attempt_ids(physical, "Physical")


def test_cohort_join_requires_an_attempt_identity():
    with pytest.raises(ValueError, match="Physical attempt identity is not unique"):
        require_unique_attempt_ids([{"provider": "moli-fixed"}], "Physical")
