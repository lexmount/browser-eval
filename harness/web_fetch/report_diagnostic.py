"""Validate a diagnostic report model without upgrading it to certification."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .safety import contained_path, safe_identifier, validate_http_url


def load_diagnostic(source):
    source = Path(source).resolve()
    model = json.loads(contained_path(source, "report.json").read_text())
    if model.get("schema") != "webfetch_cohort_diagnostic/1" or model.get("certified") is not False:
        raise ValueError("Expected an explicitly diagnostic WebFetch model")
    groups = {g["id"]: g for g in model["cohorts"]}
    if len(groups) != len(model["cohorts"]):
        raise ValueError("Duplicate cohort")
    rows = model["rows"]
    ids = [safe_identifier(r["id"]) for r in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError("Empty or duplicate result inventory")
    actual = Counter(r["cohort"] for r in rows)
    if actual != Counter({key: group["case_count"] for key, group in groups.items()}):
        raise ValueError("Result population differs from declared cohorts")
    metrics = {key: [sum(r["initial_pass"] for r in rows if r["cohort"] == key), count]
               for key, count in actual.items()}
    metrics["overall"] = [sum(r["initial_pass"] for r in rows), len(rows)]
    if metrics != model["metrics"]:
        raise ValueError("Summary differs from individual results")
    files = {}
    for row in rows:
        if type(row["initial_pass"]) is not bool:
            raise ValueError("Result must have an explicit Boolean verdict")
        validate_http_url(row["url"])
        safe_identifier(row["run_id"])
        for field in ("first_answer", "answer", "reference", "source", "log"):
            binding = row.get(field)
            if binding is None:
                if field in ("first_answer", "answer", "reference", "log"):
                    raise ValueError("Missing result evidence: " + field)
                continue
            if not isinstance(binding, dict):
                raise ValueError("Invalid evidence binding: " + field)
            if not isinstance(binding.get('path'), str) or not binding['path'].strip():
                raise ValueError("Invalid evidence path: " + field)
            digest = binding.get('sha256')
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError("Invalid evidence sha256: " + field)
            if field == 'reference' or 'scope' in binding:
                scope = binding.get('scope')
                if not isinstance(scope, str) or not scope.strip():
                    raise ValueError("Invalid " + field + " evidence scope: expected nonempty text")
            relative = binding["path"]
            original = source / relative
            if any(p.is_symlink() for p in (original, *original.parents)
                   if p == source or p.is_relative_to(source)):
                raise ValueError("Symlink evidence is not allowed")
            path = contained_path(source, relative)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != binding["sha256"]:
                raise ValueError("Evidence hash mismatch: " + relative)
            if relative in files and files[relative] != raw:
                raise ValueError("Conflicting evidence binding")
            files[relative] = raw
        if row["cause"] not in model["cause_labels"]:
            raise ValueError("Unknown result cause")
    audited = [r for r in rows if r.get("success_audit")]
    states = Counter(r["success_audit"]["source_state"] for r in audited)
    if len(audited) != model["success_audit"]["audited_passes"] or dict(states) != model["success_audit"]["source_state_counts"]:
        raise ValueError("Success review summary differs from result evidence")
    return model, files


def check_diagnostic_report(output):
    """Enforce the shared reader structure and evidence dialogs at release."""
    from bs4 import BeautifulSoup
    from scripts.report_presentation_contract import check_presentation

    output = Path(output)
    model, files = load_diagnostic(output)
    manifest = json.loads((output / "report-manifest.json").read_text())
    if manifest["template"]["track"] != "webfetch" or manifest["template"]["renderer"] != "harness/web_fetch/report.py":
        raise ValueError("Diagnostic report bypassed its registered template")
    if manifest["model_sha256"] != hashlib.sha256((output / "report.json").read_bytes()).hexdigest():
        raise ValueError("Report model binding mismatch")
    if manifest.get("certified") is not False or manifest.get("provider_calls_added") != 0:
        raise ValueError("Diagnostic report cannot upgrade evidence or add calls")
    if manifest.get("artifact_hashes") != {p: hashlib.sha256(raw).hexdigest() for p, raw in files.items()}:
        raise ValueError("Report artifact inventory mismatch")
    core = ["index.html", "datasets/index.html", "methodology/index.html", "analysis/index.html",
            "review/index.html", "guide/index.html", "reproduce/index.html", "en/index.html"]
    for route in core:
        page = (output / route).read_text()
        check_presentation(page)
    for row in model["rows"]:
        route = "attempts/" + row["id"] + ".html"
        soup = check_presentation((output / route).read_text())
        if soup.select_one('[data-task-id]')["data-task-id"] != row["id"]:
            raise ValueError("Result identity mismatch")
        detail = soup.select_one('[data-report-detail]')
        if detail["data-report-detail"] != ('success' if row['initial_pass'] else 'failed'):
            raise ValueError("Rendered verdict differs from measured result")
        for field in ("reference", "first_answer", "source", "log"):
            if not row.get(field):
                continue
            panel = soup.select_one(f'[data-evidence-kind="{field}"]')
            if not panel or not panel.select_one('pre'):
                raise ValueError("Missing result evidence panel: " + field)
            pre = panel.select_one('pre')
            binding = row[field]
            if pre.get('data-evidence-file'):
                if pre['data-evidence-file'] != binding['path']:
                    raise ValueError("Lazy evidence target mismatch")
            elif pre.get_text() != files[binding['path']].decode('utf-8'):
                raise ValueError("Rendered evidence differs from saved output")
    for route in output.rglob("*.html"):
        soup = BeautifulSoup(route.read_text(), "html.parser")
        for node in soup.select("[href], [src]"):
            value = node.get("href") or node.get("src")
            if value.startswith(("http:", "https:", "#", "data:")):
                continue
            # Artifact Site injects a site-root base on every route.
            target = contained_path(output, value.split("#")[0].split("?")[0])
            if not target.is_file():
                raise ValueError("Broken report target: " + value)
    return {"cases": len(model["rows"]), "core_routes": len(core), "status": "passed"}
