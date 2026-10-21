"""Resolve current selections without rewriting immutable collection profiles."""

import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from harness.web_fetch.config import ROOT

from .core import digest, validate_profile

CURRENT_PROFILE = ROOT / "evaluation/web-search/current.json"


def _inside(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Current search input must be repository-relative")
    root = root.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError("Current search input escapes its repository")
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
    return json.loads(raw)


def current_selection(path=CURRENT_PROFILE, *, root=ROOT):
    selection = json.loads(Path(path).read_bytes())
    if selection.get("schema") != "web-search-current/1":
        raise ValueError("Unsupported current search selection")
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


def load_profile_bytes(path=CURRENT_PROFILE, *, root=ROOT):
    """Keep flat historical bytes exact; freeze a new identity for a current run."""
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    if document.get("schema") != "web-search-current/1":
        return raw
    selection = current_selection(path, root=root)
    profile = copy.deepcopy(_bound(root, selection["profile"]))
    by_id = {m["id"]: m for m in profile["methods"]}
    profile["methods"] = [by_id[mid] for mid in selection["methods"]]
    profile["title"] = selection["title"]
    profile["protocol"]["comparison"] = "Current selected methods; one declared result per query and method."
    profile["publication"] = {"authority": selection["publication_target"]}
    profile["current_selection"] = {"sha256": digest(raw), "profile": selection["profile"], "methods": selection["methods"]}
    validate_profile(profile)
    return (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode()


def load_profile(path=CURRENT_PROFILE, *, root=ROOT):
    return json.loads(load_profile_bytes(path, root=root))


def publication_authority(manifest, path=CURRENT_PROFILE, *, root=ROOT):
    """Bind a formal release to the current participants, inputs and live target."""
    selection = current_selection(path, root=root)
    target = json.loads(_inside(root, selection["publication_target"]).read_bytes())
    host = urlsplit(target["base_url"])
    if host.scheme != "https" or not host.netloc or host.username or host.password or host.path not in ("", "/"):
        raise ValueError("Invalid search publication host")
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
        accepted_profiles = {expected_profile["sha256"]}
        if cohort == "primary":
            accepted_profiles.add(digest(load_profile_bytes(path, root=root)))
        if receipt["profile_sha256"] not in accepted_profiles:
            raise ValueError("Search release uses a different collection profile")
        if receipt.get("receipt_sha256") != bindings[prefix + "_receipt"]["sha256"]:
            raise ValueError("Search release uses a different collection receipt")
    return target
