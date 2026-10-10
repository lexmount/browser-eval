"""Independent source capture; never reads candidate/provider outputs.

Capture is evidence, NOT automatic approval of ground truth. Review selectors,
text and failures before freezing a dataset. Bounded threads isolate requests.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import ipaddress
import json
import re
import socket
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from harness.web_fetch.document_text import extract_document_text
from harness.web_fetch.metrics import normalize_for_match
from harness.web_fetch.reference_lifecycle import scope_identity
from harness.web_fetch.reference_quality import audit_reference, clean_reference_text
from harness.web_fetch.safety import contained_path, safe_identifier, validate_http_url


def extract_html(raw: bytes, url: str):
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    resources = []
    for node in soup.select(
        "img[src], video[src], video source[src], iframe[src], embed[src], object[data]"
    ):
        resources.append(
            {
                "tag": node.name,
                "url": urljoin(url, node.get("src") or node.get("data")),
                "alt": node.get("alt", ""),
            }
        )
    selectors = [
        "article.markdown-body",
        "article#docs-content",
        "#mw-content-text .mw-parser-output",
        '[itemprop="articleBody"]',
        ".heraAdit-renderContainer",
        "#mainContent",
        "#main-content",
        "main",
        "[role=main]",
        ".document .body",
        "article",
        "body",
    ]
    host = urlsplit(url).hostname or ""
    if host == "www.feishu.cn" or host == "feishu.cn":
        # Parse the site's explicit article payload as JSON, never execute script.
        # This keeps help-centre navigation outside the scoring reference.
        for script in soup.find_all("script"):
            source = script.string or ""
            marker = "window._templateValue = "
            if marker not in source:
                continue
            try:
                payload, _ = json.JSONDecoder().raw_decode(source.split(marker, 1)[1])
            except ValueError:
                continue
            parts = []
            questions = []

            def visit(value):
                if isinstance(value, dict):
                    html = value.get("html")
                    if isinstance(html, dict) and html.get("html"):
                        article = BeautifulSoup(html["html"], "html.parser")
                        # The renderer replaces these internal icon placeholders;
                        # they are not visible prose or an expected answer token.
                        for node in article.find_all(
                            string=lambda t: (
                                t and re.fullmatch(r"此处是\{\{icon_[a-z_]+\}\}icon", t)
                            )
                        ):
                            node.extract()
                        parts.append(article.get_text("\n", strip=True))
                        return
                    if isinstance(value.get("question"), str):
                        questions.append(value["question"])
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)

            visit(payload)
            if parts:
                return (
                    title,
                    "\n".join([title] + parts + questions).strip(),
                    "embedded article HTML + collapsed FAQ questions",
                    resources,
                )
    if host == "books.toscrape.com":
        selectors = [".col-sm-8.col-md-9", ".page_inner", "body"]
    elif (
        host.endswith("rfc-editor.org")
        or host.endswith("w3.org")
        or host == "fetch.spec.whatwg.org"
    ):
        selectors = ["body"]
    elif host.endswith("w3schools.com"):
        selectors = ["#main", "body"]
    selector, root = next(
        ((s, soup.select_one(s)) for s in selectors if soup.select_one(s)),
        ("body", soup),
    )
    if host.endswith("wikipedia.org") and "oldid=" in (urlsplit(url).query or ""):
        # A permanent article revision can still render live ``Excerpt``
        # transclusions from another page.  They are not part of the immutable
        # article revision and have changed under an unchanged oldid in real
        # captures, so exclude the declared transclusion container while
        # retaining ordinary article prose and fixed templates.
        for node in root.select('.excerpt-block[typeof~="mw:Transclusion"]'):
            node.decompose()
    if host == "browser.lexmount.com":
        # Fumadocs keeps the page breadcrumb and adjacent-page cards inside
        # ``article``. They are navigation, not the selected document body.
        for node in root.find_all("div", recursive=False):
            classes = set(node.get("class", []))
            if {"text-sm", "text-fd-muted-foreground"} <= classes or {
                "grid",
                "grid-cols-2",
            } <= classes:
                node.decompose()
    for node in root.select(
        "script,style,noscript,nav,footer,[aria-hidden=true],.mw-editsection,"
        ".vector-toc,.sidebar,.sphinxsidebar,.ws-share,.navbox,.navbox-styles,"
        ".article-footer__last-modified,"
        '[role="navigation"]'
    ):
        node.decompose()
    text = "\n".join(
        line.strip() for line in root.get_text("\n").splitlines() if line.strip()
    )
    return title, text, selector, resources


def capture(case, out, resolved_hosts=None):
    import requests

    safe_identifier(case["id"])
    validate_http_url(case["url"])
    dest = contained_path(out, case["id"])
    dest.mkdir(parents=True, exist_ok=True)
    host = urlsplit(case["url"]).hostname or ""
    override = (resolved_hosts or {}).get(host)
    began = monotonic()
    completion_timeout_ms = 120000
    result = {
        "case_id": case["id"],
        "url": case["url"],
        "source": "independent HTTP GET (no provider output)",
        "attempts": [],
        "independent": True,
        "scope_identity": scope_identity(case),
    }
    if override:
        result["dns_override"] = {
            "host": host,
            "ip": override,
            "scope": "explicit resolver override in this capture process",
        }
    try:
        for attempt in range(2):
            try:
                r = requests.get(
                    case["url"],
                    timeout=(12, 35),
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; WebFetchEvaluation/1.0)"
                    },
                )
                result["attempts"].append({"status": r.status_code, "url": r.url})
                if r.status_code < 500:
                    break
            except requests.RequestException as e:
                result["attempts"].append({"error": str(e)[:400]})
                if attempt:
                    raise
        raw = r.content
        mime = r.headers.get("Content-Type", "")
        result.update(
            status=r.status_code,
            final_url=r.url,
            mime=mime,
            bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            response_headers={
                k: r.headers[k]
                for k in ["X-Frame-Options", "Content-Security-Policy", "Content-Type"]
                if k in r.headers
            },
        )
        suffix = (
            ".html"
            if "html" in mime
            else ".pdf"
            if raw.startswith(b"%PDF")
            else ".docx"
            if raw.startswith(b"PK")
            else ".bin"
        )
        (dest / ("source" + suffix)).write_bytes(raw)
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
            result.update(
                title=case["name"],
                selector="PDF all pages",
                page_count=len(reader.pages),
                resources=[],
            )
        elif suffix == ".docx":
            text, _ = extract_document_text(raw, preserve_whitespace=True)
            result.update(
                title=case["name"],
                selector="DOCX XML: document paragraphs including nested tables/textboxes, footnotes/endnotes, headers/footers",
                resources=[],
            )
        elif "html" in mime:
            title, text, selector, resources = extract_html(raw, r.url)
            result.update(title=title, selector=selector, resources=resources)
        elif mime.startswith("text/"):
            text = r.content.decode("utf-8", errors="replace")
            result.update(title=case["name"], selector="entire text file", resources=[])
        else:
            text = ""
            result.update(title=case["name"], selector="binary file", resources=[])
        (dest / "extracted.txt").write_text(text, encoding="utf-8")
        result["extracted_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        text = clean_reference_text(text)
        result["reference_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        (dest / "reference.txt").write_text(text, encoding="utf-8")
        result["text_chars"] = len(text)
        result["excerpt"] = text[:800]
        result["missing_facts"] = [
            f["id"]
            for f in case.get("required_facts", [])
            if not any(
                normalize_for_match(a)
                and normalize_for_match(a) in normalize_for_match(text)
                for a in f["alternatives"]
            )
        ]
        result["quality_issues"] = (
            audit_reference(
                text, case.get("required_facts", []), normalize=normalize_for_match
            )
            if suffix in {".html", ".pdf", ".docx"} or mime.startswith("text/")
            else []
        )
        if result.get("selector") == "body":
            result["quality_issues"].append("whole_body_requires_scope_review")
        result["state"] = (
            "captured"
            if r.status_code == 200 and not result["quality_issues"]
            else "needs_review"
        )
    except Exception as e:
        result.update(state="needs_review", error=f"{type(e).__name__}: {e}")
    elapsed_ms = (monotonic() - began) * 1000
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    result["completion"] = {
        "satisfied": (result.get("state") == "captured"
                      and not result.get("missing_facts")
                      and elapsed_ms <= completion_timeout_ms),
        "elapsed_ms": elapsed_ms,
        "timeout_ms": completion_timeout_ms,
    }
    (dest / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        case["id"],
        result.get("status"),
        result.get("text_chars"),
        result.get("missing_facts"),
        flush=True,
    )
    return result


@contextmanager
def resolver_override(resolved_hosts):
    """Scope an explicitly declared DNS override to one capture batch."""
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        return original(resolved_hosts.get(host, host), port, *args, **kwargs)

    try:
        if resolved_hosts:
            socket.getaddrinfo = resolve
        yield
    finally:
        if resolved_hosts:
            socket.getaddrinfo = original


def collect(
    dataset: Path,
    output: Path,
    *,
    workers: int = 8,
    only: list[str] | None = None,
    resolved_hosts: dict[str, str] | None = None,
):
    """Capture a complete selected scope into a new, immutable directory."""
    if workers < 1:
        raise ValueError("workers must be positive")
    if output.exists() or output.is_symlink():
        raise ValueError("Capture output must be a new directory")
    from .reference_inputs import read_dataset_bytes
    cases = json.loads(read_dataset_bytes(dataset))["cases"]
    ids = [safe_identifier(case["id"]) for case in cases]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Dataset case IDs must be nonempty and unique")
    if only is not None:
        if not only or len(only) != len(set(only)) or set(only) - set(ids):
            raise ValueError(
                "Selected case IDs must be nonempty, unique and present in the dataset"
            )
        cases = [case for case in cases if case["id"] in only]
    for case in cases:
        validate_http_url(case["url"])
    resolved_hosts = resolved_hosts or {}
    for host, address in resolved_hosts.items():
        if not isinstance(host, str) or not host:
            raise ValueError("DNS override host is empty")
        ipaddress.ip_address(address)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".source-capture-", dir=output.parent
    ) as work:
        staging = Path(work) / "captures"
        staging.mkdir()
        with resolver_override(resolved_hosts):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(
                    pool.map(lambda case: capture(case, staging, resolved_hosts), cases)
                )
        (staging / "audit.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(output)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--only", default=None)
    parser.add_argument(
        "--resolve",
        action="append",
        default=[],
        metavar="HOST=IP",
        help="Audited process-local DNS override; repeat for multiple hosts",
    )
    args = parser.parse_args(argv)
    resolved_hosts = {}
    for value in args.resolve:
        host, separator, address = value.rpartition("=")
        if not separator or not host or host in resolved_hosts:
            parser.error("Each --resolve requires a unique nonempty HOST=IP")
        try:
            ipaddress.ip_address(address)
        except ValueError:
            parser.error("--resolve requires a literal IPv4/IPv6 address")
        resolved_hosts[host] = address
    try:
        collect(
            args.dataset,
            args.output,
            workers=args.workers,
            only=args.only.split(",") if args.only is not None else None,
            resolved_hosts=resolved_hosts,
        )
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()


def resource_links_main(argv=None) -> None:
    import argparse
    import json
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone
    from pathlib import Path
    from urllib.parse import urljoin
    from harness.web_fetch.reference_inputs import read_dataset_bytes
    from harness.web_fetch.safety import unlinked_path, write_new_file

    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/dataset.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args(argv)
    output = unlinked_path(args.output)
    if output.exists():
        raise ValueError("output already exists")
    dataset_bytes = read_dataset_bytes(args.dataset)
    dataset = json.loads(dataset_bytes)
    expected: dict[str, list[str]] = {}
    for case in dataset["cases"]:
        for resource in case.get("expected_resources", []):
            mime_types = [str(item) for item in resource.get("mime_types", [])]
            for value in resource.get("urls", []):
                url = urljoin(case["url"], value)
                if url in expected and set(expected[url]) != set(mime_types):
                    raise ValueError("conflicting expected MIME types for resource URL")
                expected[url] = mime_types
    checks = []
    for url, expected_mime in sorted(expected.items()):
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "browser-harness-eval/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                checks.append({
                    "url": url,
                    "status": response.status,
                    "final_url": response.geturl(),
                    "content_type": response.headers.get_content_type(),
                    "expected_mime_types": expected_mime,
                    "pass": response.status < 400 and response.headers.get_content_type() in expected_mime,
                })
        except (urllib.error.URLError, TimeoutError) as error:
            checks.append({"url": url, "status": None, "final_url": None, "content_type": None, "expected_mime_types": expected_mime, "pass": False, "error": str(error)})
    payload = {
        "schema_version": 1,
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "method": "HTTP HEAD；记录最终 URL、状态与 MIME",
        "checks": checks,
    }
    if read_dataset_bytes(args.dataset) != dataset_bytes:
        raise ValueError("resource reference inputs changed")
    write_new_file(output, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode())
    print(f"checked {len(checks)} resource URLs; passed {sum(item['pass'] for item in checks)}")
