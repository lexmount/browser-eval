"""Resolve current selections without rewriting immutable collection profiles."""

import copy
import json
from pathlib import Path


from .core import digest, validate_profile
from lexbench_websearch.inputs import resolve_profile

def authority_root(path, root=None):
    if path is None:
        raise ValueError("Select an external Search instance with --profile")
    return Path(root).resolve() if root is not None else Path(path).resolve().parent



def _inside(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Search instance input must be bundle-relative")
    root = root.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("Current search input escapes its authority bundle")
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("Current search input uses a symlink")
        current = current.parent
    return path


def _bound(root, binding):
    path = _inside(root, binding["path"])
    raw = path.read_bytes()
    if digest(raw) != binding["sha256"]:
        raise ValueError("Historical search profile binding changed")
    return resolve_profile(json.loads(raw))


def current_selection(path, *, root=None):
    root = authority_root(path, root)
    selection = json.loads(Path(path).read_bytes())
    if selection.get("schema") != "web-search-current/1":
        raise ValueError("Unsupported current search selection")
    if selection.get("independent_result_review_scope") not in (None, "failures", "all"):
        raise ValueError("Unsupported independent Search review scope")
    profile = _bound(root, selection["profile"])
    ids = selection["methods"]
    available = {m["id"]: m for m in profile["methods"]}
    if not ids or len(ids) != len(set(ids)) or not set(ids).issubset(available):
        raise ValueError("Invalid current search method selection")
    cohorts = selection.get("supplementary_profiles", [])
    if len({c["id"] for c in cohorts}) != len(cohorts) or any(c["id"] == "primary" for c in cohorts):
        raise ValueError("Duplicate current search cohort")
    for cohort in cohorts:
        _bound(root, cohort)
    _inside(root, selection["publication_target"])
    return selection


def load_profile_bytes(path, *, root=None, cohort="primary"):
    """Keep flat historical bytes exact; freeze a new identity for a current run."""
    root = authority_root(path, root)
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    if document.get("schema") != "web-search-current/1":
        if cohort != "primary":
            raise ValueError("A flat profile has no supplementary cohorts")
        expanded = resolve_profile(document)
        return raw if expanded == document else (json.dumps(expanded, ensure_ascii=False, indent=2) + "\n").encode()
    selection = current_selection(path, root=root)
    binding = selection["profile"] if cohort == "primary" else next((c for c in selection.get("supplementary_profiles", []) if c["id"] == cohort), None)
    if binding is None:
        raise ValueError("Unknown supplementary cohort")
    profile = copy.deepcopy(_bound(root, binding))
    by_id = {m["id"]: m for m in profile["methods"]}
    profile["methods"] = [by_id[mid] for mid in selection["methods"]]
    profile["title"] = selection["title"] if cohort == "primary" else profile["title"]
    profile["protocol"]["comparison"] = "Current selected methods; one declared result per query and method."
    profile["publication"] = {"authority": selection["publication_target"]}
    if selection.get("independent_result_review_scope"):
        profile["independent_result_review_scope"] = selection["independent_result_review_scope"]
    profile["current_selection"] = {"sha256": digest(raw), "profile": binding, "methods": selection["methods"], "cohort": cohort}
    for field in ("collection_revision", "cohort_revision"):
        profile.pop(field, None)
    if selection.get("independent_result_review_scope"):
        profile["protocol"]["review"] = "Review is a separate post-collection stage; declared scope: " + selection["independent_result_review_scope"]
    validate_profile(profile)
    return (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode()


def load_profile(path, *, root=None):
    return json.loads(load_profile_bytes(path, root=root))


def publication_authority(manifest, path, *, root=None, planned_target=None, expected_target=None):
    """Bind a formal release to the current participants, inputs and live target."""
    root = authority_root(path, root)
    selection = current_selection(path, root=root)
    if selection.get("independent_result_review_scope") == "all" and (
        manifest["profile"].get("independent_result_review_scope") != "all"
        or not manifest.get("independent_review_sha256")
        or (manifest.get("report_assessment") or {}).get("independent_review_scope") != "all"
    ):
        raise ValueError("Current Search release requires complete independent result review")
    target = json.loads(_inside(root, selection["publication_target"]).read_bytes())
    if planned_target is not None:
        identity = ("base_url", "slug", "versionId", "visibility")
        if expected_target != {k: target[k] for k in identity} or any(planned_target.get(k) != target[k] for k in identity):
            raise ValueError("Publication plan no longer matches the exact live target")
        target = planned_target

    from harness.web_fetch.publication_target import validate_service_base
    validate_service_base(target['base_url'])
    if not target.get("slug") or not target.get("versionId") or target.get("url") != target["base_url"].rstrip("/") + "/s/" + target["slug"]:
        raise ValueError("Incomplete live search publication target")
    methods = [m["id"] for m in manifest["profile"]["methods"]]
    if methods != selection["methods"] or target.get("methods") != methods:
        raise ValueError("Search release participants differ from the current selection")
    expected = {"primary": selection["profile"], **{c["id"]: c for c in selection.get("supplementary_profiles", [])}}
    receipts = manifest["receipt"].get("separate_collections")
    if not receipts:
        receipts = [dict(manifest["receipt"], cohort="primary")]
    if len(receipts) != len(expected) or {r["cohort"] for r in receipts} != set(expected):
        raise ValueError("Search release cohorts differ from the current selection")
    bindings = target["bindings"]
    for receipt in receipts:
        cohort = receipt["cohort"]
        prefix = "primary" if cohort == "primary" else cohort
        expected_profile = expected[cohort]
        if bindings[prefix + "_profile"] != {k: expected_profile[k] for k in ("path", "sha256")}:
            raise ValueError("Live search target profile differs from the current selection")
        accepted_profiles = {digest(load_profile_bytes(_inside(root, expected_profile["path"]), root=root))}
        accepted_profiles.add(digest(load_profile_bytes(path, root=root, cohort=cohort)))
        if receipt["profile_sha256"] not in accepted_profiles:
            raise ValueError("Search release uses a different collection profile")
        if receipt.get("receipt_sha256") != bindings[prefix + "_receipt"]["sha256"]:
            raise ValueError("Search release uses a different collection receipt")
    return target


def publication_metadata(manifest, review, relevance):
    """Derive release populations from the same evidence used by the renderer."""
    from collections import Counter
    profile = manifest['profile']
    factual = {c['id'] for c in profile['cases'] if 'reference_answer' in c}
    excluded = factual & set(profile.get('factual_quality_exclusions', []))
    counts = {key: review[key] for key in ('case_total', 'case_reviewed', 'output_total', 'output_reviewed', 'candidate_total', 'candidate_reviewed', 'accepted_unreviewed_passes')}
    counts['finding_dispositions'] = dict(Counter(d['resolution'] for d in review['adjudications'].values()))
    counts['reference_dispositions'] = dict(Counter(d['resolution'] for key, d in review['adjudications'].items() if key.endswith(':dataset') and key.removesuffix(':dataset') in factual))
    return {
        'populations': {'custom': len(profile['cases']) - len(factual), 'factual': len(factual), 'factual_quality': len(factual - excluded), 'factual_quarantined': len(excluded)},
        'independent_review': counts,
        'quality_annotation': {'queries': len(relevance['plan']['case_ids']), 'candidates': sum(len(v) for v in relevance['labels'].values()), 'scope': relevance['plan']['scope']},
    }


def verify_publication_inputs(manifest, target, *, root=None, data_home=None):
    """Verify pinned source and previous-release files before a replacement upload."""
    from lexbench_webfetch.paths import contained_path
    import os
    root = Path(root) if root is not None else None
    data_home = Path(data_home) if data_home is not None else Path(os.environ.get("BROWSER_EVAL_DATA_HOME", Path.home() / ".browser-eval")).expanduser()
    bound_paths = {}
    for name, binding in target["bindings"].items():
        base = root if root is not None and (root / binding["path"]).is_file() else data_home
        source = contained_path(base, binding["path"], reject_symlinks=True)
        if not source.is_file() or digest(source.read_bytes()) != binding["sha256"]:
            raise ValueError("Search publication binding changed: " + name)
        bound_paths[name] = source
    current_fields = [("relevance", "relevance_sha256"), ("independent_review", "independent_review_sha256")]
    if "content" in target["bindings"]:
        current_fields.append(("content", "content_sha256"))
    for key, field in current_fields:
        if manifest.get(field) != target["bindings"][key]["sha256"]:
            raise ValueError("Search publication source differs: " + key)
    if target.get("logical_results") != len(manifest["rows"]):
        raise ValueError("Search publication logical result population differs")
    if target.get("cases") != len(manifest["profile"]["cases"]):
        raise ValueError("Search publication query population differs")
    physical = sum(len(row.get("attempts", [row])) for row in manifest["rows"])
    if target.get("physical_calls") != physical:
        raise ValueError("Search publication physical call population differs")
    fields = ('populations', 'independent_review', 'quality_annotation')
    if any(field in target for field in fields):
        expected = publication_metadata(manifest, json.loads(bound_paths['independent_review'].read_bytes()), json.loads(bound_paths['relevance'].read_bytes()))
        if any(target.get(field) != expected[field] for field in fields):
            raise ValueError('Search publication assessment counts differ from evidence')
    return True
