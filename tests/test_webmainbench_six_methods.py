import pytest

from scripts.assemble_webmainbench_six_methods import require_unique_attempt_ids


def test_six_method_join_rejects_duplicate_physical_attempt_identity():
    physical = [
        {"attempt_id": "moli__case-one__p01"},
        {"attempt_id": "moli-fixed__case-one__p01"},
        {"attempt_id": "moli-fixed__case-one__p01"},
    ]

    with pytest.raises(ValueError, match="Six-version physical attempt identity is not unique"):
        require_unique_attempt_ids(physical, "Six-version physical")


def test_six_method_join_requires_an_attempt_identity():
    with pytest.raises(ValueError, match="Six-version physical attempt identity is not unique"):
        require_unique_attempt_ids([{"provider": "moli-fixed"}], "Six-version physical")
