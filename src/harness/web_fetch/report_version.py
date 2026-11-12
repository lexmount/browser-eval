"""Carry an explicitly selected benchmark edition through existing reports."""

import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.versions import verify_release

from harness.reporting.versions import check_build_version

FILENAME = "benchmark-release.json"

SCOPE_TEXT = {
    "main": ("report_scope_main", "Scoring: main content"),
    "full": ("report_scope_full", "Scoring: full content"),
}
RETRY_TEXT = {
    "main": (
        "report_retries_main",
        "Retries followed main-content requirements. This report scores the saved output.",
    ),
    "full": (
        "report_retries_full",
        "Retries followed full-content requirements. This report scores the saved output.",
    ),
    "original": (
        "report_retries_original",
        "Retries followed each task's original requirements. This report scores the saved output.",
    ),
}


def content_scope_presentation(dataset, records, manifests):
    """Keep the selected score contract separate from the acquisition policy."""
    if not any(case.get("content_scopes") for case in dataset.get("cases", [])):
        return None
    scopes = {
        row["metrics"].get("evaluation_content_scope")
        for row in records
        if row["metrics"].get("evaluation_content_scope")
    }
    scope = dataset.get("content_scope")
    scope = scope if scope in SCOPE_TEXT else next(iter(scopes)) if len(scopes) == 1 else "full"
    if scopes - {scope}:
        raise ValueError("Report mixes acceptance scopes")
    retry_scopes = set()
    for manifest in manifests:
        acquisition = manifest.get("acquisition") or {}
        budget = acquisition.get(
            "max_failure_retries", manifest.get("parameters", {}).get("max_failure_retries", 0)
        )
        if not budget:
            continue
        values = set((acquisition.get("task_content_scopes") or {}).values())
        if not values:
            values = {acquisition.get("content_scope")}
        retry_scopes.update(value if value in SCOPE_TEXT else "original" for value in values)
    return {"scope": scope, "retry_scopes": sorted(retry_scopes)}


def scope_retry_text(state):
    retries = state.get("retry_scopes", [])
    if not retries or retries == [state["scope"]]:
        return None
    return RETRY_TEXT[retries[0] if len(retries) == 1 else "original"]


def check_content_scope_presentation(root, state):
    """Validate visible scope and retry provenance on report-owned pages."""
    if state is None:
        return
    from harness.web_fetch.report_i18n import MESSAGES
    from harness.reporting.locales import translate

    owned = {
        "index.html",
        "analysis",
        "datasets",
        "methodology",
        "review",
        "guide",
        "adoption",
        "reproduce",
        "changes",
        "accounts",
        "attempts",
        "providers",
    }
    for page in root.rglob("*.html"):
        parts = page.relative_to(root).parts
        if parts[0] in {"en", "zh"}:
            parts = parts[1:]
        if not parts or parts[0] not in owned:
            continue
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        if not soup.select_one('meta[name="webstone-report"]'):
            continue
        english = soup.html.get("lang", "").startswith("en")

        def text(pair, english=english):
            return pair[1] if english else translate(MESSAGES, pair[0], "zh")

        nodes = soup.select("[data-report-content-scope]")
        if (
            len(nodes) != 1
            or nodes[0].get("data-report-content-scope") != state["scope"]
            or not nodes[0].strong
            or nodes[0].strong.get_text() != text(SCOPE_TEXT[state["scope"]])
        ):
            raise ValueError(
                "Report is missing its visible scoring scope: " + str(page.relative_to(root))
            )
        note = scope_retry_text(state)
        retry_nodes = nodes[0].select("[data-retry-acceptance-scopes]")
        if note:
            if (
                len(retry_nodes) != 1
                or retry_nodes[0].get("data-retry-acceptance-scopes")
                != ",".join(state["retry_scopes"])
                or retry_nodes[0].get_text() != text(note)
            ):
                raise ValueError("Report omits or changes the acquisition retry policy")
        elif retry_nodes:
            raise ValueError("Report invents a different acquisition retry policy")


def _input_release(root):
    execution = root / "execution.json"
    binding = (json.loads(execution.read_text(encoding="utf-8")).get("aggregate", {}).get("benchmark_release")
               if execution.is_file() else None)
    manifest = root / "report-manifest.json"
    if manifest.is_file():
        declared = (json.loads(manifest.read_text(encoding="utf-8")).get("benchmark_release") or {}).get("input_release")
        if declared is not None and declared != binding:
            raise ValueError("Report lost its original benchmark input release binding")
    if binding is not None:
        path = contained_path(root, binding["path"], reject_symlinks=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != binding.get("sha256"):
            raise ValueError("Measured benchmark input release changed")
    return binding


def _profile_binding(profile):
    if profile is None:
        return None
    return json.loads(Path(profile).read_text(encoding="utf-8")).get("benchmark_release")


def _verify_scores(release, dataset, records):
    verify_release(release, dataset, exact_dataset=False)
    if not records or any(
        (
            row.get("metrics", {}).get("scoring_source_sha256") != release["scoring_source_sha256"]
            or row.get("scoring", {}).get("source_sha256", release["scoring_source_sha256"])
            != release["scoring_source_sha256"]
        )
        for row in records
    ):
        raise ValueError("Benchmark release differs from the saved scoring sources; rescore first")


def load_release(source, dataset, records, *, profile=None):
    """Validate the profile's edition, otherwise the measured input's edition."""
    binding = _profile_binding(profile)
    if binding is not None:
        selected = contained_path(Path(profile).parent, binding["path"], reject_symlinks=True)
    else:
        selected = contained_path(source, FILENAME, reject_symlinks=True)
        if not selected.exists():
            measured = _input_release(source)
            if measured is None:
                return None
            selected = contained_path(source, measured["path"], reject_symlinks=True)
    raw = selected.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if binding is not None and digest != binding.get("sha256"):
        raise ValueError("Profile benchmark release changed")
    release = json.loads(raw)
    _verify_scores(release, dataset, records)
    return raw, release


def bind_release(source, output, dataset, records, *, profile=None):
    """Carry the validated edition into the generated report."""
    selected = load_release(source, dataset, records, profile=profile)
    if selected is None:
        return None
    raw, release = selected
    digest = hashlib.sha256(raw).hexdigest()
    target = output / FILENAME
    target.write_bytes(raw)
    measured = _input_release(source)
    if measured is not None:
        original = contained_path(source, measured["path"], reject_symlinks=True)
        archived = contained_path(output, measured["path"], reject_symlinks=True)
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_bytes(original.read_bytes())
        execution_path = output / "execution.json"
        execution = json.loads(execution_path.read_text(encoding="utf-8")) if execution_path.exists() else {}
        aggregate = execution.setdefault("aggregate", {})
        if aggregate.get("benchmark_release") != measured:
            aggregate["benchmark_release"] = measured
            execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding = {
        "path": FILENAME,
        "sha256": digest,
        "benchmark": release["benchmark"],
        "version": release["version"],
    }
    if measured is not None:
        binding["input_release"] = measured
    return binding


def check_release(root, dataset, records, *, profile=None):
    """Check the edition, rendered label and scoring identity without rescoring."""

    manifest_path = root / "report-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    check_build_version(root, manifest)
    execution = root / "execution.json"
    manifests = (json.loads(execution.read_text(encoding="utf-8")).get("batches", [])
                 if execution.is_file() else [])
    scope_state = content_scope_presentation(dataset, records, manifests)
    if scope_state is not None and (
        manifest.get("content_scope") != scope_state["scope"]
        or manifest.get("retry_acceptance_scopes") != scope_state["retry_scopes"]
    ):
        raise ValueError("Report scoring scope or acquisition retry binding changed")
    check_content_scope_presentation(root, scope_state)
    binding = manifest.get("benchmark_release")
    expected = _profile_binding(profile)
    measured = _input_release(root)
    release_path = contained_path(root, FILENAME, reject_symlinks=True)
    if binding is None:
        if expected is not None or release_path.exists() or measured is not None:
            raise ValueError("Report lost its benchmark release binding")
        return
    if binding.get("path") != FILENAME or not release_path.is_file():
        raise ValueError("Report benchmark release is missing")
    raw = release_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != binding.get("sha256") or (
        expected is not None and digest != expected.get("sha256")
    ):
        raise ValueError("Report benchmark release changed")
    release = json.loads(raw)
    _verify_scores(release, dataset, records)
    if any(binding.get(key) != release[key] for key in ("benchmark", "version")):
        raise ValueError("Report benchmark version label differs from its release")
    for route in ("index.html", "en/index.html"):
        path = root / route
        if not path.is_file():
            continue
        page = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
        if page.select_one("[data-english-not-generated]"):
            continue
        label = page.select_one("[data-benchmark-release]")
        if (
            label is None
            or label.get("href") != ("../" if route.startswith("en/") else "") + FILENAME
            or label.get("data-benchmark-release") != release["version"]
            or label.get_text(" ", strip=True) not in (
                {"bench v" + release["version"]} if manifest.get("webstone") else
                {"bench v" + release["version"], release["benchmark"] + " v" + release["version"]}
            )
        ):
            raise ValueError("Report lost its benchmark version label: " + route)
