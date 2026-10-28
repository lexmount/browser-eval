"""Article record identity and visibility remain owned by the declared field."""
from copy import deepcopy

import pytest

from lexbench_webfetch.article_comments import verify_article_comments
from lexbench_webfetch.article_posts import verify_article_posts
from lexbench_webfetch.article_title import verify_article_title
from lexbench_webfetch.evaluation import evaluate_response

from .test_review_migration_articles import COMMENTS, POSTS, URL, public_case, scored_content


def controls():
    for attribute in ("class", "data-comment-id"):
        contract = {**COMMENTS, "minimum_comments": 2, "id_attribute": attribute}
        for distinct in (False, True):
            def record(value):
                return (f'<div class="comment {value}" data-comment-id="{value}">'
                        '<span class="author">Alice</span><div class="body">Real reply.</div></div>')
            content = record("first") + record("second" if distinct else "first")
            yield (f"comment-{attribute}-{'distinct' if distinct else 'repeated'}", content,
                   "comment_contract", contract, distinct,
                   "Multi-valued HTML ID attributes must form hashable identities without losing deduplication.")
    contract = {**POSTS, "minimum_posts": 2}
    for first_body in ("", "Real first body."):
        content = f"Alice\n[Today](/post/1)\n\n{first_body}\n\nBob\n[Today](/post/2)\n\nReal second body."
        yield ("post-author-boundary-" + ("complete" if first_body else "missing-body"), content,
               "post_contract", contract, bool(first_body),
               "A following post's preceding author line must not supply the previous post's missing body.")
    title = {"version": 1, "alternatives": ["Observed title"], "html_selector": "h1"}
    for component, contract, text in (
        ("post_contract", POSTS, "\n## [Alice](/post/1)\n\nVisible body.\n"),
        ("title_contract", title, "\nObserved title\n"),
    ):
        for hidden in (False, True):
            content = "<noscript>" + text + "</noscript>" if hidden else text
            yield (f"{component}-{'noscript' if hidden else 'visible'}", content,
                   component, contract, not hidden,
                   "Article fallback extraction must exclude noscript text in both posts and titles.")


@pytest.mark.parametrize("identifier, content, component, contract, expected, cause", list(controls()))
def test_record_boundaries_through_component_and_public_scoring(
    identifier, content, component, contract, expected, cause,
):
    verify = {
        "comment_contract": lambda: verify_article_comments(content, contract)["article_comments_pass"],
        "post_contract": lambda: verify_article_posts(content, contract, url=URL)["article_posts_pass"],
        "title_contract": lambda: verify_article_title(content, contract)["article_title_pass"],
    }
    assert verify[component]() is expected
    result = evaluate_response(public_case(component, deepcopy(contract)), scored_content(content).encode())
    assert result["measured"]
    assert result["passed"] is expected


def scoring_controls():
    for identifier, content, component, contract, expected, cause in controls():
        yield {
            "id": "article-" + identifier,
            "failure_mode": "false-negative" if expected else "false-positive",
            "category": "answer-surface", "root_cause": cause,
            "case": public_case(component, contract), "artifact": scored_content(content),
            "ok": True, "expected_task_pass": expected,
        }
