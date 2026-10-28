"""Strict body contracts protect the declared ending without changing legacy ones."""
import copy
import pytest
from lexbench_webfetch.body_coverage import body_requirements, evaluate_body_requirements

INTRO = "Scientists map distant galaxies and record their motion across the sky. " * 12
ENDING = "The closing recommendation requires an independent replication before deployment."


def contract():
    rule = body_requirements(INTRO + ENDING, structure_hint="whole_declared_component")
    rule.update(version=4, ending={"text": ENDING}, minimum_floor=.8, minimum_region_passes=1)
    return rule


def test_ending_rejects_truncation_that_whole_region_tolerates():
    strict = contract()
    legacy = copy.deepcopy(strict)
    legacy["version"] = 3
    del legacy["ending"]
    assert evaluate_body_requirements(INTRO, legacy)["body_coverage_pass"]
    assert not evaluate_body_requirements(INTRO, strict)["body_coverage_pass"]
    for text in [INTRO + ENDING, INTRO + "\n**" + ENDING + "**"]:
        assert evaluate_body_requirements(text, strict)["body_coverage_pass"]


@pytest.mark.parametrize("ending", [None, {}, {"text": ""}, {"text": 3}])
def test_invalid_or_missing_ending_fails_closed(ending):
    rule = contract()
    if ending is None:
        del rule["ending"]
    else:
        rule["ending"] = ending
    with pytest.raises(ValueError):
        evaluate_body_requirements(INTRO + ENDING, rule)


def test_strict_contract_cannot_allow_a_weak_region():
    rule = contract()
    rule["minimum_floor"] = .6
    with pytest.raises(ValueError):
        evaluate_body_requirements(INTRO + ENDING, rule)
