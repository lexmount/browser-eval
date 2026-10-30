"""Explicit synthetic label contracts for parser controls; not runtime defaults."""
from copy import deepcopy

COMMENT_LABELS = {'ignored_labels': ['add a comment', 'leave a reply', 'like', 'likes', 'load more', 'log in', 'login', 'read more', 'register', 'replies', 'reply', 'report', 'share', 'show more', 'sign in', '加载更多', '发表评论', '回复', '展开', '展开回复', '查看更多', '注册', '添加评论', '点赞', '登录', '登录后评论'], 'unavailable_notices': ['[deleted]', '[removed]', '[内容不可见]', '[已删除]', 'this comment has been hidden', 'this comment has been hidden (marked as spam)']}
POST_LABELS = {'ignored_labels': ['bookmark', 'bookmarks', 'like', 'likes', 'load more', 'log in', 'permalink', 'read more', 'replies', 'reply', 'report', 'repost', 'reposts', 'share', 'show more', 'sign in', 'today', 'view', 'views', 'yesterday', '今天', '分享', '加载更多', '回复', '展开', '收藏', '昨天', '查看', '查看更多', '注册', '点赞', '登录', '转发'], 'unavailable_notices': [], 'non_author_labels': ['today', 'yesterday', 'date', 'time', 'permalink', 'view post', 'post', '今天', '昨天', '详情', '查看帖子']}


def comment_labels():
    return deepcopy(COMMENT_LABELS)


def post_labels():
    return deepcopy(POST_LABELS)
