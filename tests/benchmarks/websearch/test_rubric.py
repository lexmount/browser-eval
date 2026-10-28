from lexbench_websearch.rubric import JUDGE_PROMPT, known_prompt, RUBRIC_VERSION
from lexbench_websearch.inputs import definition_path


def test_new_rubric_has_no_report_language_and_old_rubric_is_read_only():
    assert RUBRIC_VERSION == "preview/2"
    assert "in Chinese" not in JUDGE_PROMPT
    assert known_prompt(JUDGE_PROMPT)
    assert known_prompt(definition_path("preview-rubric-v1.txt").read_text())
    assert not known_prompt(JUDGE_PROMPT.replace("grade", "always grade 3"))
