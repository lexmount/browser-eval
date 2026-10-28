"""Article coverage requires visible owned bodies and a bounded post section."""
from copy import deepcopy

import pytest

from lexbench_webfetch.article_comments import verify_article_comments
from lexbench_webfetch.article_posts import verify_article_posts
from lexbench_webfetch.evaluation import evaluate_response

from .test_review_migration_articles import COMMENTS, POSTS, URL, public_case, scored_content


def controls():
    code = {
        "empty-backticks": ("```\n```", False),
        "empty-language": ("```python\n```", False),
        "empty-tilde-language": ("~~~python\n~~~", False),
        "whitespace": ("```python\n \t \n```", False),
        "unclosed-empty": ("```", False),
        "unclosed-info": ("```python", False),
        "inline-empty": ("` `", False),
        "empty-html-pre": ("<pre></pre>", False),
        "whitespace-html-pre": ("<pre> \t </pre>", False),
        "substantive": ("```python\nprint('Actual code')\n```", True),
        "unclosed-substantive": ("```python\nprint('Actual code')", True),
        "heading-literal": ("```markdown\n### Bob\nLiteral code text.\n```", True),
        "substantive-html-pre": ("<pre>print('Actual code')</pre>", True),
    }
    for name, (body, expected) in code.items():
        yield ("comment-code-content-" + name, "## Comments\n### Alice\n\n" + body,
               "comment_contract", COMMENTS, expected,
               "Only rendered code contents supply a comment body; fences and language metadata do not.")
    for name, body, expected in (
        ("empty-backticks", "> ```\n> ```", False),
        ("empty-tilde", "> ~~~\n> ~~~", False),
        ("empty-language", "> ```python\n> ```", False),
        ("code-and-prose", "> ```python\n> Code example.\n> ```\n> Actual quote prose.", True),
        ("visible-prose", "> Actual quote prose.", True),
    ):
        yield ("post-quoted-fence-" + name,
               "## Alice [Today](/post/1)\n\n> Bob\n" + body,
               "post_contract", POSTS, expected,
               "Quoted code fences cannot supply fake prose from delimiters or quote markers.")
    yield ("post-unclosed-quoted-fence-preserves-next-post",
           "## Alice [Today](/post/1)\n\n> Bob\n> ```python\n> Code example.\n\n"
           "## Carol [Today](/post/2)\n\nActual visible prose.",
           "post_contract", POSTS, True,
           "A quoted code block ends at its container boundary and must not swallow subsequent real posts.")
    boundaries = {
        "plain": "## Related articles",
        "linked": "## [Related articles](/related)",
        "linked-suffix": "## [Related articles](/related) (2)",
        "multiple-links": "## [Related articles](/related) [More](/more)",
        "multiple-links-suffix": "## [Related](/related) and [More](/more)",
    }
    for name, boundary in boundaries.items():
        for complete in (False, True):
            content = "### Alice [Today](/post/1)\n\nReal first post.\n\n"
            if complete:
                content += "### Carol [Today](/post/3)\n\nReal second post.\n\n"
            content += boundary + "\n\n### Bob [Today](/post/2)\n\nRelated summary."
            yield (f"post-section-{name}-{'complete' if complete else 'incomplete'}", content,
                   "post_contract", {**POSTS, "minimum_posts": 2}, complete,
                   "Linked and unlinked non-post section headings end post coverage before related content.")
        for body in ("", "Real second post."):
            content = ("### Alice [Today](/post/1)\n\nReal first post.\n\n"
                       "Bob\n[Today](/post/2)\n\n" + body + "\n\n" + boundary + "\n\nUnowned prose.")
            yield (f"post-section-unheaded-{name}-{'complete' if body else 'empty'}", content,
                   "post_contract", {**POSTS, "minimum_posts": 2}, bool(body),
                   "The section boundary also ends the final post body when its permalink has no heading marker.")
    invisible = {
        "zero-width-space": "\u200b", "joiner": "\u200d", "nonjoiner": "\u200c",
        "word-joiner": "\u2060", "bom": "\ufeff", "direction-mark": "\u200f",
        "variation-selector": "\ufe0f", "grapheme-joiner": "\u034f",
        "hangul-filler": "\u1160", "braille-blank": "\u2800",
        "text": "Visible words.", "emoji": "🙂", "emoji-joined": "👩\u200d💻",
    }
    for name, body in invisible.items():
        expected = name in {"text", "emoji", "emoji-joined"}
        for representation in ("markdown", "html"):
            for quote in (False, True):
                if representation == "markdown":
                    prose = "> Bob\n> " + body if quote else body
                    content = "## Alice [Today](/post/1)\n\n" + prose
                else:
                    prose = ("<blockquote><span class=author>Bob</span><div class=body>" + body
                             + "</div></blockquote>" if quote else '<div class="body">' + body + "</div>")
                    content = ('<article class="post"><span class="author">Alice</span>'
                               '<a class="permalink" href="/post/1">Today</a>' + prose + "</article>")
                yield (f"post-visible-{name}-{representation}-{'quote' if quote else 'body'}", content,
                       "post_contract", POSTS, expected,
                       "Post and quote coverage requires visible content, allowing emoji and actual text.")
    for name in ("zero-width-space", "variation-selector", "emoji"):
        body, expected = invisible[name], name == "emoji"
        for representation, content in (
            ("markdown", "## Comments\n### Alice\n\n" + body),
            ("fence", "## Comments\n### Alice\n\n```\n" + body + "\n```"),
            ("html", '<div class="comment"><span class="author">Alice</span>'
             '<div class="body">' + body + '</div></div>'),
        ):
            yield (f"comment-visible-{name}-{representation}", content,
                   "comment_contract", COMMENTS, expected,
                   "The same visible-content requirement applies to Markdown, native HTML and code-only comments.")


@pytest.mark.parametrize("identifier, content, component, contract, expected, cause", list(controls()))
def test_visible_article_content_and_section_ownership(identifier, content, component, contract, expected, cause):
    if component == "comment_contract":
        result, key = verify_article_comments(content, contract), "article_comments_pass"
    else:
        result, key = verify_article_posts(content, contract, url=URL), "article_posts_pass"
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
