"""Explicit optional acceptance components shared by scoring and task export."""
from typing import Callable, NamedTuple
from .article_title import verify_article_title
from .article_comments import verify_article_comments
from .article_posts import verify_article_posts


class Component(NamedTuple):
    name: str
    verify: Callable
    pass_metric: str
    digest_metric: str


def _title(content, contract, url, *, representation=None, structure=None, information=None):
    if structure is not None and not isinstance(structure, dict):
        raise ValueError('page_structure must be an object')
    from .response_input import page_information_texts
    page_information_texts(information)
    information = information or {}
    headings = (structure or {}).get('headings', [])
    extra_headings = information.get('headings') or []
    for values in (headings, extra_headings):
        if not isinstance(values, (list, tuple)) or any(not isinstance(x, str) for x in values):
            raise ValueError('returned headings must be a list of text values')
    headings = [*headings, *extra_headings]
    return verify_article_title(content, contract, representation=representation,
                                headings=headings, extracted_title=information.get('title'))


def _comments(content, contract, url, *, representation=None, structure=None, information=None):
    return verify_article_comments(content, contract, representation=representation)


def _posts(content, contract, url, *, representation=None, structure=None, information=None):
    # Relative permalinks require the response document URL, unlike title/comment text.
    return verify_article_posts(content, contract, url=url, representation=representation)


COMPONENTS = {
    "title_contract": Component("article_title", _title, "article_title_pass", "article_title_contract_sha256"),
    "comment_contract": Component("article_comments", _comments, "article_comments_pass", "article_comment_contract_sha256"),
    "post_contract": Component("article_posts", _posts, "article_posts_pass", "article_post_contract_sha256"),
}
