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


def _title(content, contract, url, *, representation=None):
    return verify_article_title(content, contract, representation=representation)


def _comments(content, contract, url, *, representation=None):
    return verify_article_comments(content, contract, representation=representation)


def _posts(content, contract, url, *, representation=None):
    # Relative permalinks require the response document URL, unlike title/comment text.
    return verify_article_posts(content, contract, url=url, representation=representation)


COMPONENTS = {
    "title_contract": Component("article_title", _title, "article_title_pass", "article_title_contract_sha256"),
    "comment_contract": Component("article_comments", _comments, "article_comments_pass", "article_comment_contract_sha256"),
    "post_contract": Component("article_posts", _posts, "article_posts_pass", "article_post_contract_sha256"),
}
