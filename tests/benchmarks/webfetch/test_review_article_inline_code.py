"""Literal code and introductory links cannot create article record boundaries."""
from copy import deepcopy

import pytest

from lexbench_webfetch.article_comments import verify_article_comments
from lexbench_webfetch.article_posts import verify_article_posts
from lexbench_webfetch.article_title import verify_article_title
from lexbench_webfetch.evaluation import evaluate_response

from .test_review_migration_articles import COMMENTS, POSTS, URL, public_case, scored_content


def controls():
    literals = {
        "colon": "`Alice: example`",
        "double-tick": "``Alice: `example` ``",
        "multiline": "`Alice:\nexample`",
        "multiline-heading": "`\n### Alice\nExample reply.\n`",
        "bold-author": "`**Alice**`\nExample reply.",
        "linked-author": "`[Alice](https://example.test/reader)`\nExample reply.",
        "pingback": "`Pingback: example`",
    }
    for name, literal in literals.items():
        yield ("comment-code-" + name, "## Comments\n\n" + literal,
               "comment_contract", COMMENTS, False,
               "Inline code cannot create section headings or author boundaries for comment records.")
    yield ("comment-code-false-section", "`## Comments`\n\nAlice: Example reply.",
           "comment_contract", COMMENTS, False,
           "A comment section marker inside inline code must not establish comment ownership.")
    yield ("comment-code-html", "<div><h2>Comments</h2><code>Alice: example</code></div>",
           "comment_contract", COMMENTS, False,
           "Cleaned HTML code must retain literal ownership when converted to Markdown.")
    for index, literal in enumerate(("prefix`Alice: example`", "`Alice: example`", "prefix``Alice: example``")):
        yield (f"comment-code-html-nested-backticks-{index}",
               "<div><h2>Comments</h2><code>" + literal + "</code></div>",
               "comment_contract", COMMENTS, False,
               "HTML code serialization must use a delimiter longer than every literal backtick run.")
    yield ("comment-genuine-html-code-backticks",
           "<div><h2>Comments</h2><h3>Alice</h3><code>prefix`Bob: example`</code></div>",
           "comment_contract", COMMENTS, True,
           "Native inline code with literal backticks remains the existing comment's visible body.")
    for name, text in (
        ("colon", "Alice: Example reply."),
        ("body-code", "Alice: `Bob: example`"),
        ("body-only-code", "**Alice**\n\n`Bob: example`"),
        ("multiline-body-code", "### Alice\n\n`\n### Bob\nLiteral example.\n`"),
    ):
        yield ("comment-genuine-" + name, "## Comments\n\n" + text,
               "comment_contract", COMMENTS, True,
               "Inline code remains visible body text inside a genuinely established comment.")
    yield ("comment-code-does-not-inflate-count", "## Comments\n\nAlice: Genuine reply.\n\n`Bob: example`",
           "comment_contract", {**COMMENTS, "minimum_comments": 2}, False,
           "An inline code example in a real comment must not count as another author record.")
    yield ("comment-empty-inline-code-body", "## Comments\n\nAlice: ` `",
           "comment_contract", COMMENTS, False,
           "Code delimiters without visible text cannot supply an owned comment body.")
    for name, heading in (
        ("about", "## [About](/about)"),
        ("wrong-host", "## [About](https://other.test/post/0)"),
        ("invalid-port", "## [About](https://example.test:bad/about)"),
        ("multiple-links", "## [About](/about) [Contact](/contact)"),
        ("malformed-profile-post", "## [About](/about) [More](/contact)"),
    ):
        content = heading + "\n\n## Feed\n\n### Alice [Today](/post/1)\n\nReal post text."
        yield ("post-leading-navigation-" + name, content, "post_contract", POSTS, True,
               "Only an owning post permalink may establish the post section level.")
    yield ("post-leading-navigation-html",
           '<h2><a href="/about">About</a></h2><h2>Feed</h2>'
           '<h3>Alice <a href="/post/1">Today</a></h3><p>Real post text.</p>',
           "post_contract", POSTS, True,
           "Cleaned HTML introductory navigation must not establish a post section boundary.")
    for name, first in (
        ("author-prefix", "### Alice [Today](/post/1)"),
        ("linked-profile", "### [Alice](/profile/alice) [Today](/post/1)"),
    ):
        yield ("post-preserve-section-stop-" + name,
               first + "\n\nReal first post.\n\n## Related articles\n\n"
               "### Bob [Today](/post/2)\n\nNot in the feed.",
               "post_contract", {**POSTS, "minimum_posts": 2}, False,
               "A higher-level section heading still ends an established post section.")
    for body in ("", "Real first body."):
        yield ("post-unheaded-boundary-" + ("complete" if body else "empty"),
               "Alice\n[Today](/post/1)\n\n" + body
               + "\n\n## Bob [Today](https://other.test/post/2)\n\nUnowned body.",
               "post_contract", POSTS, bool(body),
               "An invalid explicit record still ends the body of an earlier unheaded owning post.")
    for name, header in (
        ("author-prefix", "## Alice [Today](/post/1)"),
        ("linked-profile", "## [Alice](/profile/alice) [Today](/post/1)"),
    ):
        yield ("post-inline-code-fake-" + name, "`\n" + header + "\n\nFake post text.\n`",
               "post_contract", POSTS, False,
               "Multiline inline code cannot create an owning post permalink or author boundary.")
    for name, content in (
        ("body", "## Alice [Today](/post/1)\n\n`Actual visible post text.`"),
        ("author", "## `Alice` [Today](/post/1)\n\nActual visible post text."),
        ("native-author", '<article class="post"><span class="author"><code>Alice</code></span>'
         '<a class="permalink" href="/post/1">Today</a><div class="body">Actual post text.</div></article>'),
    ):
        yield ("post-inline-code-genuine-" + name, content, "post_contract", POSTS, True,
               "Inline code retains visible text inside genuinely owned post author and body fields.")
    title = {"version": 1, "alternatives": ["Observed title"], "html_selector": "h1"}
    for name, content, expected in (
        ("multiline", "`\nObserved title\n`", False),
        ("multiline-heading", "`\n# Observed title\n`", False),
        ("double-tick-multiline", "``\nObserved title\n``", False),
        ("real-heading", "# `Observed title`", True),
        ("mixed-text", "Observed `title`", True),
        ("native-heading", "<h1><code>Observed title</code></h1>", True),
    ):
        yield ("title-inline-code-" + name, content, "title_contract", title, expected,
               "A literal code span cannot manufacture a standalone title, while real title markup may format code.")


@pytest.mark.parametrize("identifier, content, component, contract, expected, cause", list(controls()))
def test_code_and_introductory_heading_boundaries(identifier, content, component, contract, expected, cause):
    if component == "comment_contract":
        result, key = verify_article_comments(content, contract), "article_comments_pass"
    elif component == "post_contract":
        result, key = verify_article_posts(content, contract, url=URL), "article_posts_pass"
    else:
        result, key = verify_article_title(content, contract), "article_title_pass"
    assert result[key] is expected
    public = evaluate_response(public_case(component, deepcopy(contract)), scored_content(content).encode())
    assert public["measured"]
    assert public["passed"] is expected


def scoring_controls():
    for identifier, content, component, contract, expected, cause in controls():
        yield {
            "id": "article-" + identifier,
            "failure_mode": "false-negative" if expected else "false-positive",
            "category": "answer-surface", "root_cause": cause,
            "case": public_case(component, contract), "artifact": scored_content(content),
            "ok": True, "expected_task_pass": expected,
        }
