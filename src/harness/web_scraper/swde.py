"""SWDE input and page-hit scoring adapter for the shared extraction runner."""

from __future__ import annotations

import hashlib
import html
import re
import subprocess
import tarfile
import tempfile
from collections import defaultdict
from pathlib import Path

DOMAINS = ("auto", "book", "camera", "job", "movie", "nbaplayer", "restaurant", "university")
GENERIC_METHODS = (
    "cloudflare-json",
    "firecrawl-json",
    "browserbase-json",
    "oxylabs-json",
    "scrapegraph-extract",
)
CARD = Path(__file__).resolve().parents[1] / "benchmarks/cards/swde.json"
SEED = "swde-native-api-first-round-20261104"


def prepare(home: Path, source: Path):
    """Compile the predeclared site-balanced scope from an immutable source archive."""
    from harness.web_scraper.pipeline import digest, read_json, require_external_output, save_json

    require_external_output(home)
    card = read_json(CARD)
    if digest(source.read_bytes()) != card["source"]["archive_sha256"]:
        raise ValueError("SWDE source archive differs from the pinned release")
    if (home / "dataset/manifest.json").exists():
        from harness.web_scraper.pipeline import validate_dataset

        return validate_dataset(home)
    root = home / "dataset"
    root.mkdir(parents=True)
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
    # Keep the inspected original page-hit evaluator separate from this API projection.
    scorer = home / "source/markuplm-eval.py"
    if digest(scorer.read_bytes()) != card["source"]["scorer_sha256"]:
        raise ValueError("Original SWDE evaluator changed")
    (root / "evaluate.py").write_bytes(scorer.read_bytes())
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
    from harness.web_scraper.pipeline import read_json

    return read_json(home / "dataset/schemas.json")[case["domain"]]


def score(truth, predictions):
    """SWDE page-hit P/R/F1; multi-valued fields hit on a nonempty intersection.

    This follows MarkupLM's page_hits_level_metric without its training or
    site-voting stages. Exact strings preserve the source's text-node contract.
    """
    if set(truth) != set(predictions):
        raise ValueError("Predictions must cover the complete frozen SWDE population")
    groups = defaultdict(lambda: {"truth_pages": 0, "predicted_pages": 0, "hit_pages": 0})
    exact = {"correct": 0, "total": 0}
    value_counts = {"true_positive": 0, "false_positive": 0, "false_negative": 0}
    for case_id, reference in truth.items():
        prediction = predictions[case_id]
        for field, gold in reference["values"].items():
            values = prediction.get(field, [])
            # Invalid field types receive no hit but still count as an attempted prediction.
            valid = {v for v in values if isinstance(v, str)} if isinstance(values, list) else set()
            counts = groups[(reference["domain"], reference["site"], field)]
            counts["truth_pages"] += bool(gold)
            counts["predicted_pages"] += bool(values)
            counts["hit_pages"] += bool(set(gold) & valid)
            exact["total"] += 1
            exact["correct"] += (
                isinstance(values, list) and set(gold) == valid and len(valid) == len(values)
            )
            value_counts["true_positive"] += len(set(gold) & valid)
            value_counts["false_positive"] += len(valid - set(gold)) + (
                bool(values)
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
            metric: sum(r[metric] for r in selected) / len(selected)
            for metric in ("precision", "recall", "f1")
        }

    vp = value_counts["true_positive"] / max(
        1, value_counts["true_positive"] + value_counts["false_positive"]
    )
    vr = value_counts["true_positive"] / max(
        1, value_counts["true_positive"] + value_counts["false_negative"]
    )
    return {
        "exact_field_sets": {**exact, "rate": exact["correct"] / exact["total"]},
        "value_micro": {
            **value_counts,
            "precision": vp,
            "recall": vr,
            "f1": 2 * vp * vr / (vp + vr) if vp + vr else 0,
        },
        "page_hit_macro": {**average(rows), "cases": len(truth), "site_field_groups": len(rows)},
        "domains": {
            domain: average([r for r in rows if r["domain"] == domain])
            for domain in sorted({r["domain"] for r in rows})
        },
        "site_fields": rows,
    }
