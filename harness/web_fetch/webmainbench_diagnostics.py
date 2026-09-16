"""Shared investigation signals; these do not change official scores."""
import re

ERROR_PAGE = re.compile(
    r"(?:^#\s*Error response\b|^Error code:\s*404\b|ERR[_\\]ACCESS[_\\]DENIED\b|"
    r"^#\s*无法访问此网站\b|^This site can.t be reached\b)", re.I | re.M,
)
