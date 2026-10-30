"""Article ownership excludes image metadata and invisible markup."""
from copy import deepcopy

import pytest

from lexbench_webfetch.article_comments import verify_article_comments
from lexbench_webfetch.article_posts import verify_article_posts
from lexbench_webfetch.article_qualification import qualify_article_posts
from lexbench_webfetch.evaluation import evaluate_response

from .article_contract_fixtures import comment_labels, post_labels


URL = "https://example.test/feed"
POSTS = {
    **post_labels(), "version": 2, "minimum_posts": 1,
    "hosts": ["example.test"], "entry_path": r"/post/\d+",
    "html_record_selector": "article.post", "html_permalink_selector": "a.permalink",
    "html_author_selector": ".author", "html_body_selector": ".body",
    "allow_empty_outer_with_quotes": True,
}
COMMENTS = {
    **comment_labels(), "version": 2, "minimum_comments": 1,
    "record_selector": ".comment", "author_selector": ".author",
    "body_selector": ".body", "section_markers": ["Comments"],
}


def public_case(component, contract):
    return {
        "id": "article-ownership", "name": "Benchmark page", "url": URL,
        "category": "article", "task_kind": "body", "expected_any": [],
        "acceptance_contract": {
            "version": 1, "schema": "body_text", "minimum_records": 0,
            "page_patterns": ["Benchmark page"], "stable_text_role": "required",
            "stable_text_anchors": [],
        },
        "source_benchmark": {component: contract},
    }


def scored_content(content):
    prefix = "<p>Benchmark page</p>" if content.startswith("<") else "Benchmark page\n\n"
    return prefix + content


def assert_public_verdict(content, component, contract, expected):
    """The same ownership control must govern the complete task predicate."""
    result = evaluate_response(public_case(component, contract), scored_content(content).encode())
    assert result["measured"]
    assert result["passed"] is expected


IMAGE_CASES = [
    ("image-only-post", "![Some description](image.png)", False),
    ("linked-image-only-post", "[![Some description](image.png)](https://example.test/photo)", False),
    ("image-and-post-prose", "![Some description](image.png)\n\nVisible post prose.", True),
    ("image-only-quote", "> Bob\n> ![Some description](image.png)", False),
    ("image-and-quote-prose", "> Bob\n> ![Some description](image.png)\n> Visible quoted prose.", True),
    ("linked-post-prose", "[Visible post prose](https://example.test/article)", True),
]


@pytest.mark.parametrize("identifier, body, expected", IMAGE_CASES)
def test_post_and_quote_images_do_not_supply_owned_prose(identifier, body, expected):
    content = "## [Alice](https://example.test/post/1)\n\n" + body
    assert verify_article_posts(content, POSTS, url=URL)["article_posts_pass"] is expected
    assert_public_verdict(content, "post_contract", POSTS, expected)


def scoring_controls():
    """Paired, reusable controls for the current executable regression registry."""
    for identifier, body, expected in IMAGE_CASES:
        content = "## [Alice](https://example.test/post/1)\n\n" + body
        yield {
            "id": "article-" + identifier,
            "failure_mode": "false-negative" if expected else "false-positive",
            "category": "answer-surface",
            "root_cause": "Markdown image alt labels supplied post or quote prose despite being image metadata.",
            "case": public_case("post_contract", POSTS), "artifact": scored_content(content),
            "ok": True, "expected_task_pass": expected,
        }
    for kind, contract, text in (
        ("post", POSTS, "\n## [Alice](https://example.test/post/1)\n\nVisible body.\n"),
        ("comment", COMMENTS, "\n## Comments\n### Alice\nVisible reply.\n"),
    ):
        for visible in (False, True):
            content = "<div>" + (text if visible else "<!--" + text + "-->") + "</div>"
            yield {
                "id": f"article-{'visible' if visible else 'html-comment-hidden'}-{kind}",
                "failure_mode": "false-negative" if visible else "false-positive",
                "category": "answer-surface",
                "root_cause": "HTML comments were rendered as visible strings and created fallback article records.",
                "case": public_case(f"{kind}_contract", contract), "artifact": scored_content(content),
                "ok": True, "expected_task_pass": visible,
            }


@pytest.mark.parametrize("kind", ["posts", "comments"])
@pytest.mark.parametrize("visible", [False, True])
def test_html_comments_do_not_create_fallback_records(kind, visible):
    text = ("\n## [Alice](https://example.test/post/1)\n\nVisible body.\n"
            if kind == "posts" else "\n## Comments\n### Alice\nVisible reply.\n")
    content = "<div>" + (text if visible else "<!--" + text + "-->") + "</div>"
    result = (verify_article_posts(content, POSTS, url=URL) if kind == "posts"
              else verify_article_comments(content, COMMENTS))
    assert result[f"article_{kind}_pass"] is visible
    assert_public_verdict(content, "post_contract" if kind == "posts" else "comment_contract",
                          POSTS if kind == "posts" else COMMENTS, visible)


@pytest.mark.parametrize("author, expected", [
    ("Alice", True), ("🙂", True), ("Today", False), ("2026-10-20", False),
    ("\u200b", False), ("", False),
])
@pytest.mark.parametrize("quote", [False, True])
def test_historical_post_and_quote_authors_follow_runtime_policy(author, expected, quote):
    parts = [{"kind": "post", "text": "Complete visible post prose.",
              "complete_initial_text": True}]
    if quote:
        parts[0].update(text="", empty_outer_text=True)
        parts.append({"kind": "quote", "author": author,
                      "text": "Complete visible attributed quote prose.",
                      "complete_initial_text": True})
    reference = {
        "version": 1, "case_id": "feed", "url": URL,
        "scope": "initial_load_only", "context_id": "historical",
        "observed_at": "2026-10-20T00:00:00Z", "source_observation_counts": [1, 1],
        "records": [{"url": "https://example.test/post/1",
                     "author": "Alice" if quote else author, "body_parts": deepcopy(parts)}],
    }
    status = qualify_article_posts(POSTS, reference, case_id="feed", url=URL)["status"]
    assert (status == "qualified") is expected
    body = (f"> {author}\n> Complete visible attributed quote prose." if quote
            else "Complete visible post prose.")
    content = f"## [{'Alice' if quote else author}](https://example.test/post/1)\n\n{body}"
    assert verify_article_posts(content, POSTS, url=URL)["article_posts_pass"] is expected
    assert_public_verdict(content, "post_contract", POSTS, expected)
