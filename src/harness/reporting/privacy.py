"""Own every rule that recognizes or removes machine-local paths.

Each public surface keeps its own rule because rebuilt public files are bound by
hash: merging two rules would change published bytes. Add a surface here instead
of writing another expression next to its caller.
"""
import re
from dataclasses import dataclass

_WEB_URL = re.compile(r"https?://[^\s\"']+")
# Public surfaces share this one recognizer; each decides whether to replace a match or stop.
PRIVATE_IPV4 = re.compile(
    r"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|"
    r"172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2})(?![0-9])"
)


@dataclass(frozen=True)
class LocalPathPolicy:
    """Ordered (pattern, replacement) rules for one public surface."""

    rules: tuple[tuple[re.Pattern, str | bytes], ...]
    keep_web_urls: bool = False

    def _apply(self, pattern, replacement, value):
        if not self.keep_web_urls:
            return pattern.subn(lambda _: replacement, value)
        spans = [match.span() for match in _WEB_URL.finditer(value)]
        return pattern.subn(
            lambda match: match.group(0)
            if any(start <= match.start() and match.end() <= end for start, end in spans)
            else replacement,
            value,
        )

    def subn(self, value):
        """Return the redacted value and the number of matched paths."""
        total = 0
        for pattern, replacement in self.rules:
            value, count = self._apply(pattern, replacement, value)
            total += count
        return value, total

    def redact(self, value):
        return self.subn(value)[0]

    def found(self, value) -> bool:
        if self.keep_web_urls:
            return self.redact(value) != value
        return any(pattern.search(value) for pattern, _ in self.rules)


def _policy(*rules, keep_web_urls=False):
    return LocalPathPolicy(
        tuple((re.compile(pattern), replacement) for pattern, replacement in rules), keep_web_urls
    )


# A matching HTTP URL path in Trajectory Replay report text is not a local path.
TRAJECTORY_REPLAY = _policy(
    (r"/(?:Users|home|root|private|tmp|var|opt)/[^\s\"']+", "[local path removed]"),
    keep_web_urls=True,
)
BROWSER_USE = _policy((r"/(?:Users|home|private|tmp|var|opt)/[^\s\"']+", "<LOCAL_PATH>"))
# Only the account segment is replaced; the rest of the path stays readable.
PUBLIC_WEBARENA = _policy((r"/(?:Users|home|data)/[^/\s<>'\"]+", "/[local-user]"))
LEXBENCH = _policy((r"/(?:Users|home|private/tmp|tmp|results|work)/[^\s'\"<>]+", "<local path>"))
WEB_SEARCH_PREVIEW = _policy((r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^\s\"<>]+", "[source path]"))
WEBMAINBENCH = _policy(
    (r"/Users/[^/\s:]+", "/redacted-home"),
    (r"/home/[^/\s:]+", "/redacted-home"),
    (r"/private/tmp/[^\s:]+", "/redacted-temp"),
    (r"/(?:var/)?tmp/[^\s:]+", "/redacted-temp"),
)
WCXB_ERROR = _policy((r"/(?:Users|home|private/var)/[^\s:]+", "[local path]"))
WEB_FETCH_DIAGNOSTIC = _policy(
    (r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var|opt|etc)/[^\s'\"<>]+", "[local-path]"),
    (r"(?i)\b[A-Z]:\\[^\s'\"<>]+", "[local-path]"),
)
# Web Fetch public views project raw bytes. The account form keeps the remaining
# directories; a retained diagnostic replaces the complete path.
_WEB_FETCH_USER = rb"(?<![A-Za-z0-9])(?:/(?:Users|home)/[^/\s\"'<>]+/|/root/)"
WEB_FETCH_USER_SEGMENT = _policy((_WEB_FETCH_USER, b"/[REDACTED_USER]/"))
WEB_FETCH_RETAINED_DIAGNOSTIC = _policy(
    (_WEB_FETCH_USER + rb"[^\s\"'<>\\\x00]*", b"[HOST_PATH_OMITTED]")
)

# Release checks that only reject a path; they replace nothing.
BROWSER_USE_CHECK_SOURCE = BROWSER_USE.rules[0][0].pattern
LEXBENCH_CHECK_SOURCE = r"/(?:Users|home|private/tmp|tmp|results|work)/"
FIX_LOG_LOCAL_ROOT = re.compile(r"/(?:Users|home|root|private|tmp|var|opt)/")
ANOMALY_REVIEW_LOCAL_PATH = re.compile(
    r"(?:^|[\s(])(?:/Users/[^\s:]+|/home/[^\s:]+|/private/(?:tmp|var)/[^\s:]+)",
    re.MULTILINE,
)
