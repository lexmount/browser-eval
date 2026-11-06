"""Portable, source-qualified field cohorts using the maintained extraction pipeline.

Benchmark definitions and original references remain external inputs. This adapter
does not generate questions or answers and does not infer values from provider output.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin


def prepare(home: Path, source: Path):
    from harness.web_extract.pipeline import digest, read_json, require_external_output, save_json

    require_external_output(home)
    definition = read_json(source)
    if definition.get("schema") != "source-qualified-extraction-cohort/1":
        raise ValueError("A source-qualified cohort definition is required")
    mode = definition.get("input_mode")
    if mode not in {"live", "snapshot"}:
        raise ValueError("Declare live URLs or immutable hosted inputs")
    cases = definition["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("The cohort must contain distinct cases")
    if not definition.get("source_versions") or not definition.get("selection"):
        raise ValueError("Source versions and sampling rules must be frozen")
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
            from lexbench_webfetch.paths import validate_http_url

            validate_http_url(case["url"])
            from lxml import html

            base = document_base_url(html.fromstring(raw), case.get("final_url", case["url"]))
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
        (root / "evaluate.py").write_bytes(scoring_sources()[0].read_bytes())
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
                if definition.get("extraction_instructions")
                else {}
            ),
            **(
                {"snapshot_distribution": definition["snapshot_distribution"]}
                if definition.get("snapshot_distribution")
                else {}
            ),
        }
        save_json(root / "manifest.json", manifest)
        save_json(home / "profile.json", profile, exclusive=True)
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


def score(truth, predictions, **options):
    from harness.web_extract.swde import score as score_fields

    options.setdefault("text_matching", "whitespace")
    return score_fields(truth, predictions, **options)


def qualify_live(home, case_ids, *, timeout=30):
    """Check declared original values on the current source, independently of answers."""
    import time
    from collections import defaultdict
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlsplit

    from harness.web_extract.pipeline import digest, read_json
    from harness.web_fetch.providers.http_client import request

    truth = read_json(home / "dataset/dataset/ground-truth.json")

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
                    raise ValueError(f"Live source unavailable: {cid}; {error}") from error
                delay = 2 ** (index + 1)
            else:
                attempts.append({"http_status": response.status})
                if response.status not in {408, 429, 500, 502, 503, 504} or index == 2:
                    break
                retry_after = getattr(response, "headers", {}).get("retry-after", "")
                delay = min(30, int(retry_after)) if retry_after.isdigit() else 2 ** (index + 1)
            time.sleep(delay)
        if response.status != 200:
            raise ValueError(f"Live source unavailable: {cid}; HTTP {response.status}")
        missing = reference_support(reference, response.body, source_url=response.url)
        if missing:
            raise ValueError("Live reference no longer supported: " + cid + "; " + str(missing[:3]))
        return {
            "case_id": cid,
            "url": reference["url"],
            "http_status": response.status,
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


def reference_support(reference, raw, *, source_url=None):
    """Ground complete values, not numeric substrings of years or a page shell.

    Repeated source values must have enough occurrences. URL evidence comes from
    native attributes, not incidental URL strings in scripts or page text.
    """
    import html as html_text
    import unicodedata
    from collections import Counter

    from lxml import html

    from harness.web_extract.swde import record_value_key

    validate_value_counts(reference)

    def text_key(value):
        value = html_text.unescape(html_text.unescape(value))
        return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()

    from lxml.etree import ParserError

    try:
        tree = html.fromstring(raw)
    except ParserError:
        return [
            {"field": field, "reason": "empty or invalid webpage"} for field in reference["values"]
        ]
    for node in tree.xpath("//script|//style"):
        node.drop_tree()
    text = text_key(" ".join(tree.itertext()))
    base = document_base_url(tree, source_url or reference["url"])
    links = []
    for value in tree.xpath("//@href|//@src|//@data-src|//@poster"):
        try:
            links.append(urljoin(base, value))
        except ValueError:
            continue
    url_context = {**reference, "document_base_url": reference.get("document_base_url", base)}
    missing = []
    for field, values in reference["values"].items():
        counts = reference.get("source_value_counts", {}).get(field) or dict(Counter(values))
        if reference.get("value_kinds", {}).get(field) == "url":
            actual = Counter(record_value_key(url_context, field, u, "whitespace") for u in links)
            for value, count in counts.items():
                key = record_value_key(url_context, field, value, "whitespace")
                if actual[key] < count:
                    missing.append({"field": field, "value": value})
        else:
            for value, count in counts.items():
                key = text_key(value)
                pattern = r"(?<!\w)" + re.escape(key) + r"(?!\w)"
                if not key or len(re.findall(pattern, text)) < count:
                    missing.append({"field": field, "value": value})
    return missing
