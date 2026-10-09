"""Validate internal page, image and evidence targets in a virtual report tree."""
from html.parser import HTMLParser
import posixpath
from urllib.parse import unquote, urlsplit


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.links = []
        self.evidence = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "img" and values.get("src"):
            self.images.append(values["src"])
        if values.get("href"):
            self.links.append(values["href"])
        if values.get("data-file"):
            self.evidence.append(values["data-file"])


def validate_virtual_links(files, read):
    """Check the complete inventory after privacy projection or an overlay."""
    for name, file in files.items():
        if file["mime"] != "text/html":
            continue
        content = read(name)
        parsed = PageLinks()
        parsed.feed(content.decode("utf-8") if isinstance(content, bytes) else content)
        for image, urls in ((False, parsed.links), (True, parsed.images)):
            for url in urls:
                parts = urlsplit(url)
                if parts.scheme or parts.netloc:
                    continue
                target = (posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parts.path)))
                          if parts.path else name)
                if target not in files or (image and not files[target]["mime"].startswith("image/")):
                    raise ValueError(f"Invalid internal {'image' if image else 'link'}: {name}: {url}")
        for target in parsed.evidence:
            if target not in files:
                raise ValueError(f"Invalid internal evidence: {name}: {target}")
