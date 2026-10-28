"""Versioned preview relevance rubrics, independent of the report audience."""

from .inputs import definition_path

RUBRIC_VERSION = "preview/2"
JUDGE_PROMPT = definition_path("preview-rubric-v2.txt").read_text()


def known_prompt(prompt):
    return any(
        prompt == definition_path(name).read_text()
        for name in ("preview-rubric-v1.txt", "preview-rubric-v2.txt")
    )
