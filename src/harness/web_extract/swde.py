"""SWDE input and page-hit scoring adapter for the shared extraction runner."""

from __future__ import annotations

import hashlib
import html
import re
import subprocess
import tarfile
import tempfile
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

DOMAINS = ("auto", "book", "camera", "job", "movie", "nbaplayer", "restaurant", "university")
CARD = Path(__file__).resolve().parents[1] / "benchmarks/cards/swde.json"
SEED = "swde-native-api-first-round-20261104"


def generic_methods():
    """Use the shared registry as the capability authority for schema-based tasks."""
    from harness.web_extract.pipeline import available_methods

    return tuple(name for name, spec in available_methods().items() if spec.get("custom_schema"))


def prepare(home: Path, source: Path):
    """Compile the predeclared site-balanced scope from an immutable source archive."""
    from harness.web_extract.pipeline import (
        digest,
        read_json,
        require_external_output,
        validate_dataset,
    )
    from harness.web_fetch.safety import reject_evidence_symlinks

    require_external_output(home)
    require_external_output(home / "dataset")
    reject_evidence_symlinks(home / "source")
    card = read_json(CARD)
    if digest(source.read_bytes()) != card["source"]["archive_sha256"]:
        raise ValueError("SWDE source archive differs from the pinned release")
    if (home / "dataset/manifest.json").exists():
        return validate_dataset(home, CARD)
    target = home / "dataset"
    if target.exists():
        raise ValueError("Incomplete dataset exists; retain it and use a new destination")
    home.mkdir(parents=True, exist_ok=True)
    scorer = home / "source/markuplm-eval.py"
    if scorer.exists():
        scorer_bytes = scorer.read_bytes()
    else:
        from harness.web_fetch.providers.http_client import request

        response = request(card["source"]["scorer_download_url"], timeout=60, max_bytes=5_000_000)
        if response.status != 200:
            raise ValueError("Cannot download the pinned original SWDE evaluator")
        scorer_bytes = response.body
    if digest(scorer_bytes) != card["source"]["scorer_sha256"]:
        raise ValueError("Original SWDE evaluator changed")
    if not scorer.exists():
        scorer.parent.mkdir(parents=True, exist_ok=True)
        scorer.write_bytes(scorer_bytes)
    with tempfile.TemporaryDirectory(prefix=".swde-dataset-", dir=home) as temporary:
        root = Path(temporary) / "dataset"
        root.mkdir()
        manifest = _compile(home, source, card, root, scorer_bytes)
        validate_dataset(root.parent, CARD)
        root.rename(target)
        return manifest


def _compile(home, source, card, root, scorer_bytes):
    """Write a complete staged input tree; preparation commits it atomically."""
    from harness.web_extract.pipeline import digest, save_json

    archives = home / "source/archives"
    archives.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source) as bundle:
        for member in bundle:
            name = Path(member.name).name
            if member.isfile() and (
                name in {d + ".7z" for d in DOMAINS}
                or name in ("groundtruth.7z", "readme.txt", "GroundtruthReader.cs", "license.json")
            ):
                (archives / name).write_bytes(bundle.extractfile(member).read())
    truth = {}
    schemas = {}
    provenance = {}
    with tempfile.TemporaryDirectory(prefix="swde-compile-", dir=home) as temporary:
        stage = Path(temporary)
        _extract(archives / "groundtruth.7z", stage)
        groups = defaultdict(dict)
        for path in sorted((stage / "groundtruth").rglob("*.txt")):
            domain, site, field = path.stem.split("-", 2)
            labels = {}
            for line in path.read_text(encoding="utf-8-sig").splitlines()[2:]:
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                count = int(parts[1])
                values = parts[2:]
                labels[parts[0]] = [] if count == 0 else values
                if count and (len(values) != count or "<NULL>" in values):
                    raise ValueError("Malformed original SWDE labels: " + path.name)
            groups[(domain, site)][field] = labels
        if len(groups) != 80:
            raise ValueError("SWDE requires all 80 original websites")
        for domain in DOMAINS:
            directory = stage / domain
            directory.mkdir()
            _extract(archives / (domain + ".7z"), directory)
            pages = list(directory.rglob("*.htm"))
            by_site = defaultdict(list)
            for page in pages:
                match = re.fullmatch(re.escape(domain) + r"-(.+)\(\d+\)", page.parent.name)
                if not match:
                    raise ValueError("Unexpected SWDE page path: " + page.name)
                by_site[match[1]].append(page)
            if len(by_site) != 10:
                raise ValueError("Incomplete SWDE domain: " + domain)
            fields = sorted(next(v for (d, _), v in groups.items() if d == domain))
            schemas[domain] = {
                "type": "object",
                "properties": {
                    field: {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            f"All {field.replace('_', ' ')} values of the main {domain} "
                            "record, copied exactly from the page. Empty array if absent."
                        ),
                    }
                    for field in fields
                },
                "required": fields,
                "additionalProperties": False,
            }
            for site, candidates in sorted(by_site.items()):
                labels = groups[(domain, site)]
                if sorted(labels) != fields:
                    raise ValueError("Inconsistent original domain attributes")

                def key(page, selected_domain=domain):
                    return hashlib.sha256(
                        (
                            SEED + "\0" + selected_domain + "/" + page.parent.name + "/" + page.name
                        ).encode()
                    ).hexdigest()

                selected = sorted(candidates, key=key)[: card["selection"]["pages_per_site"]]
                for page in selected:
                    case_id = domain + "-" + site + "-" + page.stem
                    raw = page.read_bytes()
                    text = raw.decode("utf-8")
                    match = re.search(r'<base\b[^>]*href=["\']([^"\']+)', text, re.I)
                    if not match or not match[1].startswith(("http://", "https://")):
                        raise ValueError("Original source URL is absent: " + case_id)
                    path = root / "html" / (case_id + ".html")
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(raw)
                    truth[case_id] = {
                        "url": html.unescape(match[1]),
                        "domain": domain,
                        "site": site,
                        "values": {f: labels[f][page.stem] for f in fields},
                    }
                    provenance[case_id] = {
                        "original_path": domain + "/" + page.parent.name + "/" + page.name,
                        "original_sha256": digest(raw),
                        "input_sha256": digest(raw),
                    }
            # The immutable .7z remains the source; discard only compilation scratch bytes.
            import shutil

            shutil.rmtree(directory)
    if len(truth) != card["dataset"]["cases"]:
        raise ValueError("SWDE selected population differs from its contract")
    save_json(root / "dataset/ground-truth.json", truth)
    save_json(root / "schemas.json", schemas)
    save_json(root / "provenance.json", provenance)
    (root / "README.txt").write_bytes((archives / "readme.txt").read_bytes())
    (root / "LICENSE.json").write_bytes((archives / "license.json").read_bytes())
    (root / "evaluate.py").write_bytes(scorer_bytes)
    files = {
        p.relative_to(root).as_posix(): {
            "sha256": digest(p.read_bytes()),
            "bytes": p.stat().st_size,
        }
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "benchmark": "swde",
        "commit": card["dataset"]["commit"],
        "cases": len(truth),
        "selection": card["selection"],
        "files": files,
    }
    save_json(root / "manifest.json", manifest)
    return manifest


def _extract(archive, directory):
    """Reject traversal names before extracting a pinned public 7z archive."""
    listing = subprocess.check_output(["bsdtar", "-tf", str(archive)], text=True)
    for name in listing.splitlines():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Unsafe SWDE archive entry")
    subprocess.run(["bsdtar", "-xf", str(archive), "-C", str(directory)], check=True)


def task_schema(home, case):
    from harness.web_extract.pipeline import read_json

    return read_json(home / "dataset/schemas.json")[case["domain"]]


def probe_cases(truth):
    """Exercise every declared record category without inspecting provider outputs."""
    by_domain = {}
    for case_id, case in sorted(truth.items()):
        by_domain.setdefault(case["domain"], case_id)
    if not by_domain:
        raise ValueError("Compatibility sampling requires nonempty record categories")
    return [by_domain[domain] for domain in sorted(by_domain)]


def canonical_text(value):
    """Preserve characters, punctuation and units; normalize whitespace only."""
    return " ".join(value.split())


def record_value_key(reference, field, value, text_matching="literal"):
    """Apply only the field's declared equivalence, shared by scores and details."""
    if reference.get("value_kinds", {}).get(field) == "url":
        from urllib.parse import urljoin, urlsplit

        if not value.strip():
            return ""
        try:
            parts = urlsplit(
                urljoin(
                    reference.get("document_base_url", reference["url"]),
                    value.strip().replace(" ", "%20"),
                )
            )
        except ValueError:
            return value
        userinfo, separator, host = parts.netloc.rpartition("@")
        authority = userinfo + separator + host.lower() if separator else parts.netloc.lower()
        return parts._replace(scheme=parts.scheme.lower(), netloc=authority).geturl()
    return canonical_text(value) if text_matching == "whitespace" else value


def qualify_reference_text(truth, html_pages):
    """Resolve encoded labels only when exact frozen text nodes prove the result."""
    from lxml import etree
    from lxml import html as lxml_html

    from harness.web_extract.pipeline import digest

    if set(truth) != set(html_pages):
        raise ValueError("Reference qualification requires the exact frozen HTML population")
    references = deepcopy(truth)
    changes, unmatched, source_hashes = [], [], {}
    for cid, case in sorted(truth.items()):
        raw = html_pages[cid]
        source_hashes[cid] = digest(raw)
        tree = lxml_html.fromstring(
            raw.decode("utf-8"), parser=lxml_html.HTMLParser(no_network=True)
        )
        nodes = defaultdict(list)
        for node in tree.xpath("//text()[not(ancestor::script) and not(ancestor::style)]"):
            value = canonical_text(str(node))
            if value:
                nodes[value].append(
                    {
                        "xpath": tree.getroottree().getpath(node.getparent())
                        + ("/following-sibling::text()[1]" if node.is_tail else "/text()[1]"),
                        "text": str(node),
                    }
                )
        for field, values in case["values"].items():
            accepted = []
            for value in values:
                try:
                    decoded = canonical_text(
                        lxml_html.fragment_fromstring(value, create_parent="div").text_content()
                    )
                except (etree.ParserError, ValueError):
                    decoded = value
                selected = canonical_text(value)
                if selected not in nodes and decoded in nodes:
                    selected = decoded
                if selected not in nodes:
                    unmatched.append({"case_id": cid, "field": field, "original": value})
                    selected = value
                elif selected != value:
                    changes.append(
                        {
                            "case_id": cid,
                            "field": field,
                            "original": value,
                            "accepted": selected,
                            "source_sha256": source_hashes[cid],
                            "nodes": nodes[selected],
                        }
                    )
                accepted.append(selected)
            references[cid]["values"][field] = list(dict.fromkeys(accepted))
    return {
        "references": references,
        "changes": changes,
        "unmatched": unmatched,
        "source_hashes": source_hashes,
    }


def _reference_evidence(tree, evidence, *, source_url):
    """Resolve one complete source text node and its nearby field context."""
    from lxml import html as lxml_html

    nodes = tree.xpath(evidence["xpath"])
    if len(nodes) != 1:
        raise ValueError("Reference decision locator is not one source text node")
    node = nodes[0]
    if isinstance(node, lxml_html.HtmlElement):
        value = canonical_text(node.text_content())
        if not any(canonical_text(text) == value for text in node.xpath(".//text()")):
            raise ValueError("Reference decision concatenates distinct source text nodes")
        context_node = node
    elif isinstance(node, str) and (
        getattr(node, "is_text", False) or getattr(node, "is_tail", False)
    ):
        value = canonical_text(str(node))
        context_node = node.getparent()
        if node.is_tail:
            context_node = context_node.getparent()
    else:
        raise ValueError("Reference decision locator is not source text")
    if not value or value != evidence.get("value") or value != evidence.get("source_quote"):
        raise ValueError("Reference decision does not copy its source text node")
    if "record_url" in evidence:
        from urllib.parse import urljoin, urlsplit

        target = evidence["record_url"]
        if (
            target != source_url
            or context_node.tag != "a"
            or not context_node.get("href")
            or urlsplit(urljoin(source_url, context_node.get("href")))._replace(fragment="")
            != urlsplit(target)._replace(fragment="")
        ):
            raise ValueError("Reference self-link does not identify the same source record")
    context = canonical_text(evidence.get("field_context", ""))
    ancestors = [context_node, *list(context_node.iterancestors())[:6]]
    if not context or not any(context in canonical_text(n.text_content()) for n in ancestors):
        raise ValueError("Reference decision field context is absent from its source")
    return value


def load_reference_revision(home, path):
    """Verify source-only reference changes before scoring retained native answers."""
    from lxml import html as lxml_html

    from harness.web_extract.pipeline import digest, read_json, require_external_output

    require_external_output(path)
    revision = read_json(path)
    original = home / "dataset/dataset/ground-truth.json"
    if (
        revision.get("schema") != "record-source-reference-candidate/1"
        or revision.get("original_reference_sha256") != digest(original.read_bytes())
        or revision.get("regression_ids") != check_reference_controls()
        or revision.get("text_matching") != "whitespace"
        or revision.get("provider_calls_added") != 0
    ):
        raise ValueError("Reference revision has missing source or regression authority")
    truth = read_json(original)
    pages = {cid: (home / "dataset/html" / (cid + ".html")).read_bytes() for cid in truth}
    qualified = qualify_reference_text(truth, pages)
    if (
        revision.get("source_hashes") != qualified["source_hashes"]
        or revision.get("dom_text_changes") != qualified["changes"]
    ):
        raise ValueError("Reference revision differs from frozen source text")
    references = qualified["references"]
    seen = set()
    unmatched = {(r["case_id"], r["field"]) for r in qualified["unmatched"]}
    for decision in revision.get("partial_label_decisions", []):
        cid, field = decision["case_id"], decision["field"]
        key = (cid, field)
        if (
            key in seen
            or key not in unmatched
            or decision.get("original") != truth[cid]["values"][field]
            or decision.get("source_sha256") != qualified["source_hashes"][cid]
            or not decision.get("reason")
            or not decision.get("accepted")
            or len(set(decision["accepted"])) != len(decision["accepted"])
        ):
            raise ValueError("Reference decision is outside its declared source defect")
        seen.add(key)
        tree = lxml_html.fromstring(pages[cid].decode("utf-8"))
        evidenced = []
        for evidence in decision.get("evidence", []):
            evidenced.append(_reference_evidence(tree, evidence, source_url=truth[cid]["url"]))
        if evidenced != decision["accepted"]:
            raise ValueError("Every accepted reference value requires exact source evidence")
        references[cid]["values"][field] = list(evidenced)
    if seen != unmatched:
        raise ValueError("Every unmatched original reference requires source adjudication")
    for decision in revision.get("field_decisions", []):
        cid, field = decision.get("case_id"), decision.get("field")
        key = cid, field
        accepted = decision.get("accepted")
        if (
            cid not in truth
            or field not in truth[cid]["values"]
            or key in seen
            or decision.get("original") != truth[cid]["values"][field]
            or decision.get("source_sha256") != qualified["source_hashes"][cid]
            or not decision.get("reason")
            or not decision.get("rule")
            or not isinstance(accepted, list)
            or any(not isinstance(v, str) for v in accepted)
            or len(set(accepted)) != len(accepted)
        ):
            raise ValueError("Reference field decision is outside its declared source authority")
        seen.add(key)
        tree = lxml_html.fromstring(pages[cid].decode("utf-8"))
        evidence_values = {"accepted": [], "excluded": []}
        for evidence in decision.get("evidence", []):
            role = evidence.get("role")
            if role not in evidence_values:
                raise ValueError("Reference field evidence needs an accepted or excluded role")
            evidence_values[role].append(
                _reference_evidence(tree, evidence, source_url=truth[cid]["url"])
            )
        removed = set(references[cid]["values"][field]) - set(accepted)
        if (
            evidence_values["accepted"] != accepted
            or not removed.issubset(evidence_values["excluded"])
            or not decision.get("evidence")
        ):
            raise ValueError("Every accepted and removed reference value needs source evidence")
        references[cid]["values"][field] = list(accepted)
    if revision.get("references") != references:
        raise ValueError("Reference revision contains undeclared answer or metadata changes")
    groups = revision.get("unavailable_reference_groups", [])
    if len({tuple(group) for group in groups}) != len(groups):
        raise ValueError("Duplicate unavailable reference group")
    if groups:
        _check_reference_coverage(home, truth, revision, groups)
    # The production scorer owns validation of known, entirely empty groups.
    score(
        references,
        {cid: {} for cid in references},
        unavailable_reference_groups=groups,
        text_matching="whitespace",
    )
    return references, {
        "unavailable_reference_groups": groups,
        "text_matching": "whitespace",
    }


def _check_reference_coverage(home, truth, revision, groups):
    from harness.web_extract.pipeline import digest, read_json, require_external_output

    binding = revision.get("coverage_authority", {})
    path = Path(binding.get("path", ""))
    require_external_output(path)
    if not path.is_file() or digest(path.read_bytes()) != binding.get("sha256"):
        raise ValueError("Unavailable fields need a hash-bound full-annotation authority")
    authority = read_json(path)
    archive = home / "source/archives/groundtruth.7z"
    require_external_output(archive)
    archive_sha = digest(archive.read_bytes())
    trusted_sha = read_json(CARD).get("source", {}).get("annotation_archive_sha256")
    if not trusted_sha or archive_sha != trusted_sha:
        raise ValueError("Original annotation archive differs from the pinned source")
    if archive_sha != authority.get("original_annotation_archive_sha256"):
        raise ValueError("Original annotation archive changed")
    entries = {
        (entry["domain"], entry["site"], entry["field"]): entry
        for entry in authority["all_empty_selected_groups"]
    }
    for domain, site, field in groups:
        entry = entries.get((domain, site, field))
        if entry is None:
            raise ValueError("Unavailable group lacks full-annotation coverage evidence")
        member = f"groundtruth/{domain}/{domain}-{site}-{field}.txt"
        raw = subprocess.run(
            ["bsdtar", "-xOf", str(archive), member], check=True, capture_output=True
        ).stdout
        lines = raw.decode("utf-8-sig").splitlines()[2:]
        selected = [
            case for case in truth.values() if (case["domain"], case["site"]) == (domain, site)
        ]
        if (
            digest(raw) != entry.get("original_annotation_sha256")
            or not lines
            or len(lines) != entry.get("full_site_pages")
            or len(selected) != entry.get("selected_pages")
            or entry.get("full_site_nonempty") != 0
            or any(int(line.split("\t")[1]) != 0 for line in lines)
        ):
            raise ValueError("Unavailable field has incomplete or populated original annotations")


def score(truth, predictions, *, unavailable_reference_groups=(), text_matching="literal"):
    """SWDE page-hit P/R/F1; multi-valued fields hit on a nonempty intersection.

    This follows MarkupLM's page_hits_level_metric without its training or
    site-voting stages. Exact strings preserve the source's text-node contract.
    An explicit source-qualified revision can declare wholly unannotated groups
    unavailable. Historical calls retain the default frozen population. The caller
    binds annotation-coverage authority; output contents never choose the scope.
    """
    if set(truth) != set(predictions):
        raise ValueError("Predictions must cover the complete frozen SWDE population")
    if text_matching not in ("literal", "whitespace"):
        raise ValueError("Unknown record text matching policy")
    unavailable = {tuple(group) for group in unavailable_reference_groups}
    declared = {
        (reference["domain"], reference["site"], field)
        for reference in truth.values()
        for field in reference["values"]
    }
    if unavailable - declared:
        raise ValueError("Unavailable reference group is outside the declared population")
    if any(
        values
        for reference in truth.values()
        for field, values in reference["values"].items()
        if (reference["domain"], reference["site"], field) in unavailable
    ):
        raise ValueError("A populated reference group cannot be declared unavailable")
    groups = defaultdict(lambda: {"truth_pages": 0, "predicted_pages": 0, "hit_pages": 0})
    exact = {"correct": 0, "total": 0}
    value_counts = {"true_positive": 0, "false_positive": 0, "false_negative": 0}
    unscored = 0
    for case_id, reference in truth.items():
        prediction = predictions[case_id]
        for field, gold in reference["values"].items():
            if (reference["domain"], reference["site"], field) in unavailable:
                unscored += 1
                continue
            values = prediction.get(field, [])
            gold = [record_value_key(reference, field, value, text_matching) for value in gold]
            if isinstance(values, list):
                values = [
                    record_value_key(reference, field, value, text_matching)
                    if isinstance(value, str)
                    else value
                    for value in values
                ]
            # Invalid field types receive no hit but still count as an attempted prediction.
            valid = {v for v in values if isinstance(v, str)} if isinstance(values, list) else set()
            counts = groups[(reference["domain"], reference["site"], field)]
            counts["truth_pages"] += bool(gold)
            counts["predicted_pages"] += (
                bool(values) if isinstance(values, list) else field in prediction
            )
            counts["hit_pages"] += bool(set(gold) & valid)
            exact["total"] += 1
            exact["correct"] += (
                isinstance(values, list) and set(gold) == valid and len(valid) == len(values)
            )
            value_counts["true_positive"] += len(set(gold) & valid)
            value_counts["false_positive"] += len(valid - set(gold)) + (
                field in prediction
                if not isinstance(values, list)
                else sum(not isinstance(v, str) for v in values)
            )
            value_counts["false_negative"] += len(set(gold) - valid)
    rows = []
    for (domain, site, field), counts in sorted(groups.items()):
        p = counts["hit_pages"] / (counts["predicted_pages"] + 0.000001)
        r = counts["hit_pages"] / (counts["truth_pages"] + 0.000001)
        f1 = 2 * p * r / (p + r + 0.000001)
        rows.append(
            {
                "domain": domain,
                "site": site,
                "field": field,
                **counts,
                "precision": p,
                "recall": r,
                "f1": f1,
            }
        )

    def average(selected):
        return {
            metric: sum(r[metric] for r in selected) / len(selected) if selected else None
            for metric in ("precision", "recall", "f1")
        }

    vp = value_counts["true_positive"] / max(
        1, value_counts["true_positive"] + value_counts["false_positive"]
    )
    vr = value_counts["true_positive"] / max(
        1, value_counts["true_positive"] + value_counts["false_negative"]
    )
    result = {
        "exact_field_sets": {
            **exact,
            "rate": exact["correct"] / exact["total"] if exact["total"] else None,
        },
        "value_micro": {
            **value_counts,
            "precision": vp,
            "recall": vr,
            "f1": 2 * vp * vr / (vp + vr) if vp + vr else 0,
        },
        "page_hit_macro": {**average(rows), "cases": len(truth), "site_field_groups": len(rows)},
        "domains": {
            domain: average([r for r in rows if r["domain"] == domain])
            for domain in sorted({reference["domain"] for reference in truth.values()})
        },
        "site_fields": rows,
    }
    if unavailable:
        result["reference_coverage"] = {
            "unavailable_groups": [
                dict(zip(("domain", "site", "field"), group, strict=True))
                for group in sorted(unavailable)
            ],
            "unscored_page_fields": unscored,
        }
        if not exact["total"]:
            result["value_micro"].update(precision=None, recall=None, f1=None)
    if text_matching != "literal":
        result["text_matching"] = text_matching
    return result


def check_reference_controls():
    """Execute paired source-reference controls through the public record scorer."""
    truth = {
        "one": {"domain": "auto", "site": "a", "values": {"model": ["Car"], "engine": []}},
        "two": {"domain": "auto", "site": "a", "values": {"model": [], "engine": []}},
    }
    unavailable = [("auto", "a", "engine")]
    predictions = {"one": {"model": ["Car"]}, "two": {"model": []}}
    qualified = score(truth, predictions, unavailable_reference_groups=unavailable)
    legacy = score(truth, predictions)
    if qualified["page_hit_macro"]["f1"] <= legacy["page_hit_macro"]["f1"]:
        raise ValueError("Unannotated group was scored as quality zero")
    predictions["one"]["engine"] = ["V6"]
    if score(truth, predictions, unavailable_reference_groups=unavailable) != qualified:
        raise ValueError("Unannotated output changed qualified quality")
    predictions["two"]["model"] = ["invented"]
    if (
        score(truth, predictions, unavailable_reference_groups=unavailable)["value_micro"][
            "false_positive"
        ]
        != 1
    ):
        raise ValueError("A covered field's false positive was discarded")
    for invalid in [("auto", "a", "model"), ("auto", "unknown", "engine")]:
        try:
            score(truth, predictions, unavailable_reference_groups=[invalid])
        except ValueError:
            pass
        else:
            raise ValueError("Unavailable-reference scope was not validated")
    truth = {"one": {"domain": "book", "site": "a", "values": {"title": ["AT&amp;T"]}}}
    qualified = qualify_reference_text(truth, {"one": b"<p>AT&amp;T</p>"})["references"]
    for prediction, hit in [("AT&T", True), ("AT&amp;T", False)]:
        result = score(qualified, {"one": {"title": [prediction]}})
        if bool(result["site_fields"][0]["hit_pages"]) != hit:
            raise ValueError("Encoded annotation does not match source DOM text")
    for source in (
        b"<p>AT&amp;amp;T</p>",
        b"<p>AT&amp;amp;T</p><footer>AT&amp;T</footer>",
    ):
        if qualify_reference_text(truth, {"one": source})["references"] != truth:
            raise ValueError("Literal entity text was decoded without source authority")
    truth = {"one": {"domain": "book", "site": "a", "values": {"title": ["Démon\u00a0€ 9.99"]}}}
    for prediction, hit in [
        (" Démon € 9.99 ", True),
        ("Dmon € 9.99", False),
        ("Démon 9.99", False),
    ]:
        result = score(truth, {"one": {"title": [prediction]}}, text_matching="whitespace")
        if bool(result["site_fields"][0]["hit_pages"]) != hit:
            raise ValueError("Whitespace matching changed meaningful characters or units")
    for field, accepted, wrong in [
        ("director", "Wolfgang Reitherman", "Bruce Reitherman"),
        ("mpaa_rating", "PG", "PG-13"),
    ]:
        truth = {"one": {"domain": "movie", "site": "a", "values": {field: [accepted]}}}
        for prediction, hit in [(accepted, True), (wrong, False)]:
            result = score(truth, {"one": {field: [prediction]}}, text_matching="whitespace")
            if bool(result["site_fields"][0]["hit_pages"]) != hit:
                raise ValueError(
                    "Reference correction broadened matching to an adjacent wrong value"
                )
    truth = {
        "one": {
            "domain": "auto",
            "site": "a",
            "values": {"engine": ["3.6L V6", "3.6 liters V 6"], "fuel_economy": []},
        }
    }
    for value, hit in [("3.6L V6", True), ("3.6 liters V 6", True), ("3.6L V8", False)]:
        result = score(truth, {"one": {"engine": [value]}})
        engine = next(row for row in result["site_fields"] if row["field"] == "engine")
        if bool(engine["hit_pages"]) != hit:
            raise ValueError("Source alternatives broadened unrelated field matching")
    for values, false_positives in [([], 0), (["No data"], 1), (["25 MPG"], 1)]:
        result = score(truth, {"one": {"engine": ["3.6L V6"], "fuel_economy": values}})
        if result["value_micro"]["false_positive"] != false_positives:
            raise ValueError("Placeholder removal discarded covered negative-page errors")
    truth = {"one": {"domain": "profile", "site": "a", "url": "https://a.com/p/",
                     "values": {"photo": ["https://a.com/x.jpg"]},
                     "value_kinds": {"photo": "url"}}}
    for value, hit in [(" \thttps://a.com/x.jpg\n", True), (" /x.jpg ", True),
                       (" https://a.com/y.jpg ", False), (" /x .jpg ", False)]:
        result = score(truth, {"one": {"photo": [value]}})
        if bool(result["value_micro"]["true_positive"]) != hit:
            raise ValueError("URL boundary whitespace changed field identity")
    return [
        "unannotated-group-is-unavailable-not-zero",
        "unannotated-prediction-does-not-select-quality-scope",
        "covered-empty-page-still-counts-false-positives",
        "populated-and-unknown-reference-groups-cannot-be-excluded",
        "encoded-reference-resolves-only-to-exact-source-nodes",
        "literal-entity-text-is-not-decoded-twice",
        "whitespace-equivalence-preserves-accents-and-units",
        "full-director-name-excludes-another-role-with-same-surname",
        "complete-rating-does-not-enable-prefix-matching",
        "source-alternatives-do-not-broaden-unrelated-value-matching",
        "placeholder-removal-retains-covered-negative-false-positives",
        "url-boundary-whitespace-preserves-absolute-and-relative-targets",
        "url-whitespace-does-not-collapse-distinct-paths",
    ]
