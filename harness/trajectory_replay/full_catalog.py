"""Shared contracts for the full WebArena trajectory catalog and run evidence."""

from __future__ import annotations

import json
import tomllib
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from webarena_verified.core.utils.checksum import compute_evaluator_checksum
from webarena_verified.types.task import WebArenaSite

from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
)
from harness.trajectory_replay.retrieval_acceptance import validate_contract
from harness.trajectory_replay.webarena_corrections import apply_correction
from harness.trajectory_replay.webarena_replay import _json_sha256

CATALOG_SCHEMA = "lex_webarena_replay_catalog/1"
CATALOG_SCHEMAS = frozenset({CATALOG_SCHEMA, "lex_webarena_full_replay_catalog/1"})
RUN_SCHEMAS = frozenset({"lex_webarena_full_replay_run/1", "lex_webarena_full_replay_run/2"})
TRUSTED_RECORDED_HOSTS = frozenset(
    {
        "onestopmarket.example.com",
        "gitlab.example.com",
        "openstreetmap.example.com",
        "wikipedia.example.com",
        "ec2-3-133-227-75.us-east-2.compute.amazonaws.com",
        "ec2-3-135-39-80.us-east-2.compute.amazonaws.com",
        "host.docker.internal",
        "192.168.5.2",
        "127.0.0.1",
    }
)
WEBARENA_FORK_REPOSITORY = "https://github.com/lexmount/webarena-verified.git"
WEBARENA_FORK_BRANCH = "lex-main"
ENVIRONMENT_PREPARATION_METHODS = frozenset(
    {
        "new_rainbond_application_from_pinned_images",
        "component_restarts_from_pinned_images",
    }
)

_EVIDENCE_FILENAMES = {
    "network_har": "network.har",
    "acceptance": "acceptance.json",
    "actual_response": "actual_response.json",
    "evaluation": "evaluation.json",
    "failure_screenshot": "failure.png",
    "trace": "trace.json",
    "readiness": "readiness.json",
    "site_reset": "site-reset.json",
    "outputs": "outputs.json",
}
_LEGACY_EVIDENCE_FILENAMES = frozenset(
    {
        "actions.json",
        "evaluation.json",
        "failure.png",
        "final.png",
        "network.har",
        "outputs.json",
        "response.json",
        "trace.json",
    }
)


def source_sites(catalog: dict) -> dict[WebArenaSite, str]:
    raw = catalog.get("source_site_urls") or {}
    if not isinstance(raw, dict) or not set(raw) <= {site.value for site in WebArenaSite}:
        raise ValueError("catalog source site URLs are invalid")
    if any(not isinstance(value, str) or not urlsplit(value).netloc for value in raw.values()):
        raise ValueError("catalog source site URLs are invalid")
    return {WebArenaSite(key): value for key, value in raw.items()}


def row_source_sites(catalog: dict, row: dict) -> dict[WebArenaSite, str]:
    override = row.get("source_site_urls")
    if override is not None:
        return source_sites({"source_site_urls": override})
    return source_sites(catalog)


def load_catalog_inputs(
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path,
    *,
    accepted_ledger_path: Path | None = None,
) -> tuple[dict, dict[WebArenaSite, str], dict[int, object]]:
    """Load and fully validate one frozen full-catalog input set."""

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    sites = load_remote_sites(site_map_path)
    suite = load_suite(suite_path)
    tasks = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    rows = catalog.get("rows") or []
    ids = [row.get("task_id") for row in rows]
    if (
        catalog.get("schema") not in CATALOG_SCHEMAS
        or not ids
        or len(set(ids)) != len(ids)
        or catalog.get("task_ids") != ids
        or catalog.get("site_map_sha256") != file_sha256(site_map_path)
        or catalog.get("portable_suite_sha256") != file_sha256(suite_path)
    ):
        raise ValueError("full replay catalog binding is invalid")
    if accepted_ledger_path is not None and catalog.get("accepted_ledger_sha256") != file_sha256(
        accepted_ledger_path
    ):
        raise ValueError("full replay catalog is not bound to the accepted ledger")
    portable_ids = {row["task_id"] for row in rows if row.get("mode") == "portable"}
    if portable_ids != set(tasks):
        raise ValueError("portable suite differs from the catalog")
    for row in rows:
        task_id = row["task_id"]
        mode = row.get("mode")
        if mode == "portable":
            actions = list(tasks[task_id].tool_calls)
        elif mode == "direct":
            actions = row.get("source_actions")
            if not isinstance(actions, list) or not actions:
                raise ValueError(f"task {task_id}: direct source actions are missing")
            contract = row.get("retrieval_contract")
            if contract is not None:
                validate_contract(contract, actions)
        else:
            raise ValueError(f"task {task_id}: unknown catalog mode")
        bound = rebind_remote_calls(
            actions, sites, set(TRUSTED_RECORDED_HOSTS), row_source_sites(catalog, row)
        )
        if _json_sha256(bound) != row.get("bound_calls_sha256"):
            raise ValueError(f"task {task_id}: bound actions changed")
        if "recorded_response" in row:
            raise ValueError("recorded answers cannot enter a replay catalog")
    return catalog, sites, tasks


def completed_task_ids(progress_path: Path, execution_order: list[int]) -> set[int]:
    """Return completed IDs only when progress is a strict order prefix."""

    if not progress_path.exists():
        return set()
    rows = [json.loads(line) for line in progress_path.read_text(encoding="utf-8").splitlines()]
    observed = [row.get("task_id") for row in rows]
    if observed != execution_order[: len(observed)]:
        raise ValueError("progress is not a strict prefix of the declared execution order")
    return set(observed)


def verify_physical_evidence(directory: Path, row: dict) -> Path | None:
    """Hash-check every declared artifact for one completed physical attempt."""

    task_id = row["task_id"]
    evidence = row.get("evidence_sha256")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError(f"task {task_id} has no declared physical evidence")
    task_dir = directory / f"task-{task_id}"
    if task_dir.is_symlink() or not task_dir.is_dir():
        raise ValueError(f"task {task_id} evidence directory is missing")
    if "trace.json" in evidence:
        if not set(evidence) <= _LEGACY_EVIDENCE_FILENAMES:
            raise ValueError(f"task {task_id} has unknown legacy evidence files")
        names = {name: name for name in evidence}
        required = {"evaluation.json", "network.har", "response.json", "trace.json"}
        evaluation_path = task_dir / "evaluation.json"
    else:
        unknown = set(evidence) - set(_EVIDENCE_FILENAMES)
        if unknown:
            raise ValueError(f"task {task_id} has unknown evidence kinds: {sorted(unknown)}")
        names = {name: _EVIDENCE_FILENAMES[name] for name in evidence}
        required = {"evaluation", "network_har", "actual_response", "trace"}
        evaluation_path = task_dir / "evaluation.json"
    for name, filename in names.items():
        path = task_dir / filename
        if path.is_symlink() or not path.is_file() or file_sha256(path) != evidence[name]:
            raise ValueError(f"task {task_id} evidence hash changed: {filename}")
    if row.get("status") == "pass" and not required <= set(evidence):
        raise ValueError(f"task {task_id} pass lacks official evaluation")
    return evaluation_path if evaluation_path.is_file() else None


def validate_environment_preparation(receipt_path: Path, environment_profile_path: Path) -> dict:
    """Validate the fresh-deployment receipt against the versioned environment profile."""

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    profile = json.loads(environment_profile_path.read_text(encoding="utf-8"))
    deployment = profile.get("deployment") or {}
    images = profile.get("images") or {}
    mutable_sites = ("shopping", "shopping_admin", "reddit", "gitlab")
    if (
        profile.get("schema") != "lex_webarena_verified_full_environment/1"
        or receipt.get("schema") != "lex_webarena_environment_preparation/1"
        or receipt.get("method") not in ENVIRONMENT_PREPARATION_METHODS
        or receipt.get("source_app_id") != deployment.get("source_app_id")
        or receipt.get("fresh_app_id") != deployment.get("acceptance_app_id")
        or receipt.get("source_app_id") == receipt.get("fresh_app_id")
        or receipt.get("site_reset_between_tasks") is not deployment.get("site_reset_between_tasks")
    ):
        raise ValueError("environment preparation is not bound to the declared deployment")
    if receipt["method"] == "component_restarts_from_pinned_images" and set(
        receipt.get("reset_scope") or ()
    ) != set(mutable_sites):
        raise ValueError("environment preparation reset scope is incomplete")
    receipt_images = receipt.get("site_component_images") or {}
    volumes = receipt.get("database_volume_mounts") or {}
    initialization = receipt.get("initialization") or {}
    statuses = receipt.get("public_http_status") or {}
    for site in mutable_sites:
        expected_digest = images.get(site)
        if (
            not isinstance(expected_digest, str)
            or not str(receipt_images.get(site) or "").endswith("@" + expected_digest)
            or volumes.get(site) != 0
            or not initialization.get(site)
            or statuses.get(site) != 200
        ):
            raise ValueError(f"environment preparation is incomplete for {site}")
    if set(receipt.get("shared_site_components") or ()) != {"map", "wikipedia"}:
        raise ValueError("environment preparation has an unexpected shared-site scope")
    return receipt


def repository_root(path: Path) -> Path:
    """Find the browser-eval checkout that owns a versioned input or entry point."""

    start = path.resolve()
    candidates = (start, *start.parents) if start.is_dir() else start.parents
    root = next(
        (
            candidate
            for candidate in candidates
            if (candidate / "pyproject.toml").is_file() and (candidate / "uv.lock").is_file()
        ),
        None,
    )
    if root is None:
        raise ValueError("path is outside a browser-eval repository")
    return root


def validate_passing_evaluation(
    evaluation: dict,
    *,
    task_id: int,
    expected_data_checksum: str,
    benchmark: object,
    corrections: object,
) -> None:
    """Require current official rules and the declared replay correction for a pass."""

    official = benchmark.get_task(task_id)
    correction = corrections.records.get(task_id)
    if correction is None:
        task = official
        expected_correction = (None, None)
    else:
        applied = apply_correction(official, corrections)
        task = applied.task
        expected_correction = (applied.correction_id, applied.corrected_task_sha256)
    actual_correction = (
        evaluation.get("contract_correction_id"),
        evaluation.get("corrected_task_sha256"),
    )
    evaluator_names = tuple(item.evaluator for item in task.eval)
    evaluator_rows = evaluation.get("evaluators_results") or []
    if (
        evaluation.get("task_id") != task_id
        or float(evaluation.get("score") or 0) != 1.0
        or str(evaluation.get("status") or "").lower() not in {"pass", "success"}
        or evaluation.get("webarena_verified_version") != version("webarena-verified")
        or evaluation.get("webarena_verified_evaluator_checksum") != compute_evaluator_checksum()
        or evaluation.get("webarena_verified_data_checksum") != expected_data_checksum
        or actual_correction != expected_correction
        or tuple(row.get("evaluator_name") for row in evaluator_rows) != evaluator_names
        or any(
            float(row.get("score") or 0) != 1.0
            or str(row.get("status") or "").lower() not in {"pass", "success"}
            for row in evaluator_rows
        )
    ):
        raise ValueError(f"task {task_id} pass has stale or incomplete evaluator provenance")


def validate_full_report_profile(profile_path: Path, data_home: Path) -> dict:
    """Validate the repository authority against its frozen local release artifacts."""

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    root = repository_root(profile_path)
    dataset_path = root / str(profile.get("dataset_path") or "")
    scope_path = root / str(profile.get("scope_path") or "")
    if (
        profile.get("schema") != "lex_webarena_verified_full_report_profile/1"
        or not dataset_path.is_file()
        or file_sha256(dataset_path) != profile.get("dataset_sha256")
        or not scope_path.is_file()
        or file_sha256(scope_path) != profile.get("scope_sha256")
    ):
        raise ValueError("full report profile changed its versioned dataset binding")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    evaluator_source = dataset.get("evaluator_source") or {}
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependency_source = (
        project.get("tool", {}).get("uv", {}).get("sources", {}).get("webarena-verified") or {}
    )
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    locked_package = next(
        (
            package
            for package in lock.get("package", [])
            if package.get("name") == "webarena-verified"
        ),
        {},
    )
    locked_git = (locked_package.get("source") or {}).get("git", "")
    if (
        dataset.get("schema") != "lex_webarena_verified_full_dataset/1"
        or dataset.get("task_count") != 812
        or dataset.get("task_id_range") != [0, 811]
        or "browser" in dataset
        or evaluator_source.get("repository") != WEBARENA_FORK_REPOSITORY
        or evaluator_source.get("branch") != WEBARENA_FORK_BRANCH
        or dependency_source.get("git") != WEBARENA_FORK_REPOSITORY
        or dependency_source.get("rev") != evaluator_source.get("revision")
        or locked_git
        != (
            f"{WEBARENA_FORK_REPOSITORY}?rev={evaluator_source.get('revision')}"
            f"#{evaluator_source.get('revision')}"
        )
    ):
        raise ValueError("full WebArena dataset definition is invalid")
    root = data_home / str(profile.get("evidence_root") or "")
    if root.is_symlink() or not root.is_dir() or data_home.resolve() not in root.resolve().parents:
        raise ValueError("full report evidence root is missing or unsafe")
    artifacts = {
        "acceptance_ledger": (
            profile.get("acceptance_ledger_path"),
            profile.get("acceptance_ledger_sha256"),
        ),
        "report_manifest": (
            profile.get("report_manifest_path"),
            profile.get("report_manifest_sha256"),
        ),
        "report_archive": (
            profile.get("report_archive_path"),
            profile.get("report_archive_sha256"),
        ),
        "accepted_catalog": (
            (profile.get("accepted_catalog") or {}).get("path"),
            (profile.get("accepted_catalog") or {}).get("sha256"),
        ),
        "accepted_portable_suite": (
            (profile.get("accepted_portable_suite") or {}).get("path"),
            (profile.get("accepted_portable_suite") or {}).get("sha256"),
        ),
    }
    for name, (relative, expected) in artifacts.items():
        path = Path(str(relative or ""))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{name} path is unsafe")
        target = root / path
        if target.is_symlink() or not target.is_file() or file_sha256(target) != expected:
            raise ValueError(f"{name} artifact changed")
    ledger = json.loads((root / artifacts["acceptance_ledger"][0]).read_text(encoding="utf-8"))
    accepted_counts = profile.get("accepted_counts")
    if (
        not isinstance(accepted_counts, dict)
        or set(accepted_counts)
        != {
            "total",
            "pass",
            "unresolved",
            "physical_attempts",
            "excluded_duplicate_attempts",
        }
        or accepted_counts.get("total") != dataset["task_count"]
        or accepted_counts.get("pass") != dataset["task_count"]
        or accepted_counts.get("unresolved") != 0
        or ledger.get("counts") != accepted_counts
    ):
        raise ValueError("full report acceptance population changed")
    catalog = json.loads((root / artifacts["accepted_catalog"][0]).read_text(encoding="utf-8"))
    if catalog.get("accepted_ledger_sha256") != profile["acceptance_ledger_sha256"]:
        raise ValueError("accepted catalog is not bound to the current ledger")
    publication = profile.get("publication") or {}
    if not all(publication.get(key) for key in ("host", "slug", "version_id", "share_url")):
        raise ValueError("full report publication target is incomplete")
    return profile
