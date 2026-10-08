"""Recognize machine-local paths without redacting matching HTTP URL paths."""
import re

LOCAL_PATH_RE = re.compile(r"/(?:Users|home|root|private|tmp|var|opt)/[^\s\"']+")
WEB_URL_RE = re.compile(r"https?://[^\s\"']+")
