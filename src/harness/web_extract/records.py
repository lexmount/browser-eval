"""Portable, source-qualified field cohorts using the maintained extraction pipeline.

Benchmark definitions and original references remain external inputs. This adapter
does not generate questions or answers and does not infer values from provider output.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC
from pathlib import Path
from urllib.parse import urljoin


class LiveSourceAccessError(ValueError):
    """The checker could not observe the page; task validity remains unresolved."""

    def __init__(self, case_id, attempts):
        self.case_id = case_id
        self.source_attempts = attempts
        self.validity = "unresolved"
        super().__init__(f"Live source unavailable: {case_id}; {attempts[-1]}")


def prepare(home: Path, source: Path):
    from harness.web_extract.pipeline import digest, read_json, require_external_output, save_json

    require_external_output(home)
    definition = read_json(source)
    if definition.get("schema") != "source-qualified-extraction-cohort/1":
        raise ValueError("A source-qualified cohort definition is required")
    mode = definition.get("input_mode")
    if mode not in {"live", "snapshot"}:
        raise ValueError("Declare live URLs or immutable hosted inputs")
    live_policy = definition.get("live_reference_policy")
    if live_policy is not None:
        if (
            mode != "live"
            or set(live_policy) != {"maximum_observation_age_seconds"}
            or type(live_policy["maximum_observation_age_seconds"]) is not int
            or not 0 < live_policy["maximum_observation_age_seconds"] <= 86400
        ):
            raise ValueError("Declare a bounded independent live reference observation window")
    cases = definition["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("The cohort must contain distinct cases")
    if not definition.get("source_versions") or not definition.get("selection"):
        raise ValueError("Source versions and sampling rules must be frozen")
    if "extraction_instructions" in definition and (
        not isinstance(definition["extraction_instructions"], str)
        or not definition["extraction_instructions"].strip()
    ):
        raise ValueError("Extraction instructions must be a nonempty string")
    target = home / "dataset"
    if target.exists():
        raise ValueError("Prepare a new cohort directory")
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".records-", dir=home) as temporary:
        stage = Path(temporary)
        root = stage / "dataset"
        (root / "html").mkdir(parents=True)
        truth, schemas, provenance = {}, {}, {}
        for case in cases:
            cid = case["id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
                raise ValueError("Unsafe case identity")
            raw = _source_bytes(source.parent, case["input"])
            if digest(raw) != case["input_sha256"]:
                raise ValueError("Source HTML differs from qualification")
            references = case["values"]
            schema = case["schema"]
            if not references or set(references) != set(schema.get("properties", {})):
                raise ValueError("Scored fields and requested fields differ")
            if any(
                not isinstance(values, list) or any(not isinstance(v, str) for v in values)
                for values in references.values()
            ):
                raise ValueError("References must be explicit string lists")
            validate_value_counts(case)
            receipt = case.get("qualification")
            if (
                not receipt
                or receipt.get("input_sha256") != digest(raw)
                or receipt.get("reference_sha256")
                != digest(json.dumps(references, sort_keys=True, ensure_ascii=False).encode())
            ):
                raise ValueError("Every input needs its source qualification receipt")
            if live_policy is not None:
                from datetime import datetime

                observed = datetime.fromisoformat(
                    receipt.get("observed_at", "").replace("Z", "+00:00")
                )
                if observed.tzinfo is None or receipt.get("source_url") != case["url"]:
                    raise ValueError("Live observation must identify its time and original URL")
            from lexbench_webfetch.paths import validate_http_url

            validate_http_url(case["url"])
            from lxml import html

            encoding = receipt.get("encoding")
            if encoding is not None and (not isinstance(encoding, str) or not encoding.strip()):
                raise ValueError("Captured HTTP encoding must be a nonempty charset name")
            base = document_base_url(
                html.fromstring(raw, parser=html.HTMLParser(encoding=encoding)),
                case.get("final_url", case["url"]),
            )
            (root / "html" / (cid + ".html")).write_bytes(raw)
            truth[cid] = {
                "url": case["url"],
                "domain": case["domain"],
                "site": case["site"],
                "values": references,
                "schema_id": cid,
                "value_kinds": case.get("value_kinds", {}),
                "source_value_counts": case.get("source_value_counts", {}),
                **({"document_base_url": base} if base != case["url"] else {}),
                **({"query": case["query"]} if case.get("query") else {}),
            }
            schemas[cid] = schema
            provenance[cid] = {
                "qualification": receipt,
                "original_reference": case.get("original_reference", references),
                "unscored_fields": case.get("unscored_fields", {}),
            }
            for asset in case.get("assets", []):
                name = Path(asset["path"])
                if name.is_absolute() or ".." in name.parts or name.parts[:2] != ("assets", cid):
                    raise ValueError("Assets must belong to their case")
                blob = _source_bytes(source.parent, asset["input"])
                if digest(blob) != asset["sha256"]:
                    raise ValueError("Source resource differs from qualification")
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)
        save_json(root / "dataset/ground-truth.json", truth)
        save_json(root / "schemas.json", schemas)
        save_json(root / "provenance.json", provenance)
        (root / "evaluate.py").write_bytes(exported_evaluator())
        (root / "LICENSE").write_text(definition["source_license"], encoding="utf-8")
        save_json(root / "source-versions.json", definition["source_versions"])
        files = {
            path.relative_to(root).as_posix(): {
                "sha256": digest(path.read_bytes()),
                "bytes": path.stat().st_size,
            }
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }
        commit = digest(json.dumps(definition["source_versions"], sort_keys=True).encode())
        manifest = {
            "benchmark": "records",
            "input_mode": mode,
            "commit": commit,
            "cases": len(cases),
            "selection": definition["selection"],
            "files": files,
            **({"live_reference_policy": live_policy} if live_policy is not None else {}),
        }
        profile = {
            "name": definition["name"],
            "source_license": definition["source_license"],
            "dataset": {
                "id": definition["id"],
                "repository": definition["repository"],
                "commit": commit,
                "cases": len(cases),
                "files": {name: entry["sha256"] for name, entry in files.items()},
                "inventory_sha256": digest(
                    json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
                ),
            },
            "scoring": {
                "primary": "value_micro",
                "text_matching": "whitespace",
                "official_reproduction": False,
            },
            "source_versions": definition["source_versions"],
            **(
                {"extraction_instructions": definition["extraction_instructions"]}
                if "extraction_instructions" in definition
                else {}
            ),
            **(
                {"snapshot_distribution": definition["snapshot_distribution"]}
                if definition.get("snapshot_distribution")
                else {}
            ),
        }
        save_json(root / "manifest.json", manifest)
        profile_path = home / "profile.json"
        if profile_path.exists():
            if profile_path.is_symlink() or read_json(profile_path) != profile:
                raise ValueError("Existing profile belongs to a different cohort")
        else:
            save_json(profile_path, profile, exclusive=True)
        root.rename(target)
    from harness.web_extract.pipeline import validate_dataset

    return validate_dataset(home)


def _source_bytes(base, name):
    """Definitions may refer to external approved inputs, never symlink substitutions."""
    from harness.web_extract.pipeline import unsafe_parents

    path = Path(name)
    path = path if path.is_absolute() else base / path
    if path.is_symlink() or unsafe_parents(path) or not path.is_file():
        raise ValueError("Missing or unsafe source input")
    return path.read_bytes()


def task_schema(home, case):
    from harness.web_extract.pipeline import read_json

    return read_json(home / "dataset/schemas.json")[case["schema_id"]]


def probe_cases(truth):
    from harness.web_extract.swde import probe_cases as select

    return select(truth)


def reference_policy(home=None):
    return {"text_matching": "whitespace", "unavailable_reference_groups": []}


def document_base_url(tree, source_url):
    """Match the document's first base href, including relative base URLs."""
    bases = tree.xpath("(//base[@href])[1]/@href")
    try:
        return urljoin(source_url, bases[0]) if bases else source_url
    except ValueError:
        return source_url


def validate_value_counts(reference):
    """Optional source multiplicities cannot hide or weaken a scored reference."""
    from collections import Counter

    overrides = reference.get("source_value_counts", {})
    if not isinstance(overrides, dict) or set(overrides) - set(reference["values"]):
        raise ValueError("Invalid source value counts")
    for field, counts in overrides.items():
        expected = Counter(reference["values"][field])
        if (
            not isinstance(counts, dict)
            or set(counts) != set(expected)
            or any(
                type(count) is not int or count < expected[value] for value, count in counts.items()
            )
        ):
            raise ValueError(
                "Source value counts must cover every reference with positive integers"
            )


def scoring_sources():
    from harness.web_extract import swde

    return (Path(swde.__file__),)


def exported_evaluator():
    """Keep a standalone implementation with this adapter's declared default."""
    return (
        scoring_sources()[0].read_bytes()
        + b"\n\n_record_score = score\ndef score(truth, predictions, **options):\n"
        b'    options.setdefault("text_matching", "whitespace")\n'
        b"    return _record_score(truth, predictions, **options)\n"
    )


def score(truth, predictions, **options):
    from harness.web_extract.swde import score as score_fields

    options.setdefault("text_matching", "whitespace")
    return score_fields(truth, predictions, **options)


def qualify_live(home, case_ids, *, timeout=30):
    """Check declared original values on the current source, independently of answers."""
    import time
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor
    from email.message import Message
    from urllib.parse import urlsplit

    from harness.web_extract.pipeline import digest, read_json
    from harness.web_fetch.providers.http_client import request

    truth = read_json(home / "dataset/dataset/ground-truth.json")
    policy = read_json(home / "dataset/manifest.json").get("live_reference_policy")

    def unavailable(cid, attempts):
        if policy is None:
            raise LiveSourceAccessError(cid, attempts)
        from datetime import datetime

        reference = truth[cid]
        receipt = read_json(home / "dataset/provenance.json")[cid]["qualification"]
        observed = datetime.fromisoformat(receipt["observed_at"].replace("Z", "+00:00"))
        age = (datetime.now(UTC) - observed).total_seconds()
        raw = (home / "dataset/html" / (cid + ".html")).read_bytes()
        if (
            not 0 <= age <= policy["maximum_observation_age_seconds"]
            or receipt["source_url"] != reference["url"]
            or digest(raw) != receipt["input_sha256"]
            or reference_support(
                reference,
                raw,
                encoding=receipt.get("encoding"),
                document_base=reference.get("document_base_url"),
            )
        ):
            raise LiveSourceAccessError(cid, attempts)
        return {
            "case_id": cid,
            "url": reference["url"],
            "source_state": "access_unavailable",
            "http_status": next(
                (a["http_status"] for a in reversed(attempts) if "http_status" in a), None
            ),
            "verification_mode": "independent_captured_reference",
            "reference_observed_at": receipt["observed_at"],
            "encoding": receipt.get("encoding"),
            "observed_html_sha256": digest(raw),
            "reference_sha256": digest(json.dumps(reference["values"], sort_keys=True).encode()),
            "reference_values_verified": sum(map(len, reference["values"].values())),
            "source_attempts": attempts,
        }

    def one(cid):
        reference = truth[cid]
        attempts = []
        for index in range(3):
            try:
                response = request(
                    reference["url"],
                    timeout=timeout,
                    max_bytes=8_000_000,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; benchmark-source-check/1.0)"},
                )
            except OSError as error:
                attempts.append({"error": type(error).__name__})
                if index == 2:
                    return unavailable(cid, attempts)
                delay = 2 ** (index + 1)
            else:
                attempts.append({"http_status": response.status})
                if response.status not in {408, 429, 500, 502, 503, 504} or index == 2:
                    break
                retry_after = getattr(response, "headers", {}).get("retry-after", "")
                delay = min(30, int(retry_after)) if retry_after.isdigit() else 2 ** (index + 1)
            time.sleep(delay)
        if response.status != 200:
            return unavailable(cid, attempts)
        content_type = Message()
        content_type["content-type"] = getattr(response, "headers", {}).get("content-type", "")
        missing = reference_support(
            reference,
            response.body,
            source_url=response.url,
            encoding=content_type.get_content_charset(),
        )
        if missing and access_interstitial(response.body):
            attempts[-1]["access_interstitial"] = True
            return unavailable(cid, attempts)
        if missing:
            raise ValueError("Live reference no longer supported: " + cid + "; " + str(missing[:3]))
        return {
            "case_id": cid,
            "url": reference["url"],
            "http_status": response.status,
            "encoding": content_type.get_content_charset(),
            "observed_html_sha256": digest(response.body),
            "reference_sha256": digest(json.dumps(reference["values"], sort_keys=True).encode()),
            "reference_values_verified": sum(map(len, reference["values"].values())),
            "source_attempts": attempts,
        }

    # One request at a time per origin prevents validation itself triggering rate limits.
    origins = defaultdict(list)
    for cid in case_ids:
        origins[urlsplit(truth[cid]["url"]).netloc].append(cid)

    def origin_cases(ids):
        rows = []
        for index, cid in enumerate(ids):
            if index:
                time.sleep(1)
            rows.append(one(cid))
        return rows

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for group in pool.map(origin_cases, origins.values()) for row in group]
    return sorted(rows, key=lambda row: row["case_id"])


def access_interstitial(raw):
    """Recognize affirmative gate markup, not articles discussing access challenges."""
    from lxml import html
    from lxml.etree import ParserError

    try:
        tree = html.fromstring(raw)
    except (ParserError, ValueError):
        return False
    title = " ".join(tree.xpath("//title/text()")).strip().lower()
    challenge_title = title in {
        "just a moment...",
        "attention required! | cloudflare",
        "verify you are human",
        "access denied",
    }
    markers = tree.xpath(
        "//*[@id='challenge-form' or @id='cf-challenge-running'] | "
        "//script[contains(@src, '/cdn-cgi/challenge-platform/')]"
    )
    login = title in {"sign in", "log in", "login", "authentication required"} and tree.xpath(
        "//form//input[@type='password']"
    )
    return bool((challenge_title and markers) or login)


def reference_support(reference, raw, *, source_url=None, encoding=None, document_base=None):
    """Ground complete values, not numeric substrings of years or a page shell.

    Repeated source values must have enough occurrences. URL evidence comes from
    native attributes, not incidental URL strings in scripts or page text.
    """
    from collections import Counter

    from lxml import html

    from harness.web_extract.swde import canonical_text, record_value_key

    validate_value_counts(reference)

    def text_key(value):
        return canonical_text(value)

    from lxml.etree import ParserError

    try:
        tree = html.fromstring(raw, parser=html.HTMLParser(encoding=encoding))
    except ParserError:
        return [
            {"field": field, "reason": "empty or invalid webpage"} for field in reference["values"]
        ]
    for node in tree.xpath("//script|//style"):
        node.drop_tree()
    # Ordinary inline markup preserves words. Blocks, controls and neighboring
    # labelled fields delimit values; comments do not contribute text.
    blocks = set(
        (
            "address article aside blockquote br caption dd details dialog div dl dt fieldset "
            "figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre "
            "section summary table tbody td tfoot th thead tr ul"
        ).split()
    )

    def labelled(node):
        return (
            node is not None
            and node.tag == "span"
            and any(
                child.tag in {"strong", "label"} and child.text_content().strip().endswith(":")
                for child in node
            )
        )

    def visible(node):
        if not isinstance(node.tag, str):
            return
        previous = node.getprevious()
        before = (
            node.tag in blocks
            or node.tag == "button"
            or (labelled(node) and labelled(previous) and not (previous.tail or ""))
        )
        after = (
            node.tag in blocks
            or node.tag == "button"
            or (labelled(node) and labelled(node.getnext()) and not (node.tail or ""))
        )
        if before:
            yield " "
        yield node.text or ""
        for child in node:
            yield from visible(child)
            yield child.tail or ""
        if after:
            yield " "

    text = text_key("".join(visible(tree)))
    base = (
        document_base
        if document_base is not None
        else document_base_url(tree, source_url or reference["url"])
    )
    links = []
    for value in tree.xpath("//@href|//@src|//@data-src|//@poster"):
        try:
            links.append(urljoin(base, value))
        except ValueError:
            continue
    url_context = {
        **reference,
        "document_base_url": reference.get("document_base_url", reference["url"]),
    }
    missing = []
    for field, values in reference["values"].items():
        counts = reference.get("source_value_counts", {}).get(field) or dict(Counter(values))
        if reference.get("value_kinds", {}).get(field) == "url":
            actual = Counter(record_value_key(url_context, field, u, "whitespace") for u in links)
            required = Counter()
            for value, count in counts.items():
                required[record_value_key(url_context, field, value, "whitespace")] += count
            for key, count in required.items():
                if actual[key] < count:
                    missing.append({"field": field, "value": key})
        else:
            required = Counter()
            for value, count in counts.items():
                required[text_key(value)] += count
            for key, count in required.items():
                pattern = r"(?<!\w)" + re.escape(key) + r"(?!\w)"
                if re.fullmatch(r"[+-]?\d+(?:[.,]\d+)*", key):
                    pattern = r"(?<![\w.,+\-])" + re.escape(key) + r"(?!\w|[.,+\-]\d)"
                observed = len(re.findall(pattern, text))
                if not key or observed < count:
                    missing.append({"field": field, "value": key})
    return missing
