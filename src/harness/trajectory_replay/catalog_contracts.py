"""Shared contracts for WebArena trajectory catalogs and replay evidence."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from webarena_verified.core.utils.checksum import compute_evaluator_checksum
from webarena_verified.types.task import WebArenaSite

from harness.environments.webarena.topology import TRUSTED_RECORDED_HOSTS
from harness.record_io import load_jsonl
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
DATASET_SCHEMA = "lex_webarena_replay_dataset/1"
DATASET_SCHEMAS = frozenset({DATASET_SCHEMA, "lex_webarena_verified_full_dataset/1"})
SCOPE_SCHEMA = "lex_webarena_replay_scope/1"
SCOPE_SCHEMAS = frozenset({SCOPE_SCHEMA, "lex_webarena_verified_full_scope/1"})
ENVIRONMENT_SCHEMA = "lex_webarena_replay_environment/1"
ENVIRONMENT_SCHEMAS = frozenset({ENVIRONMENT_SCHEMA, "lex_webarena_verified_full_environment/1"})
REPORT_PROFILE_SCHEMA = "lex_webarena_replay_report_profile/1"
REPORT_PROFILE_SCHEMAS = frozenset(
    {REPORT_PROFILE_SCHEMA, "lex_webarena_verified_full_report_profile/1"}
)
COMPARISON_RELEASE_SCHEMA = "lex_webarena_replay_comparison_release/1"
RUN_SCHEMA = "lex_trajectory_replay_catalog_run/2"
LEGACY_RUN_SCHEMAS = frozenset(
    {
        "lex_trajectory_replay_catalog_run/1",
        "lex_webarena_full_replay_run/1",
        "lex_webarena_full_replay_run/2",
    }
)
RUN_SCHEMAS = frozenset({RUN_SCHEMA, *LEGACY_RUN_SCHEMAS})
EXECUTION_CONTRACT_RUN_SCHEMAS = frozenset(
    {RUN_SCHEMA, "lex_trajectory_replay_catalog_run/1", "lex_webarena_full_replay_run/2"}
)
ACTION_OVERRIDES_SCHEMA = "lex_webarena_replay_action_overrides/1"
_ACTION_OVERRIDE_FIELDS = frozenset(
    {
        "force",
        "script",
        "script_mode",
        "selector",
        "wait_state",
        "wait_for_enabled",
        "wait_for_selector_hidden",
    }
)
_MAX_OVERRIDE_SCRIPT_BYTES = 64 * 1024
ACCEPTANCE_SCHEMA = "lex_trajectory_replay_catalog_acceptance/1"
LEGACY_ACCEPTANCE_SCHEMAS = frozenset({"lex_webarena_full_acceptance_ledger/1"})
ACCEPTANCE_SCHEMAS = frozenset({ACCEPTANCE_SCHEMA, *LEGACY_ACCEPTANCE_SCHEMAS})
LEGACY_CATALOG_REPORT_SCHEMA = "lex_webarena_full_acceptance_report/3"
LEGACY_CATALOG_CONTRACTS_SCHEMA = "lex_webarena_full_contracts/1"
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


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _validate_action_amendments(amendments: object) -> list[dict]:
    if not isinstance(amendments, list):
        raise ValueError("action override amendments must be a list")
    validated: list[dict] = []
    identities: set[tuple[int, int]] = set()
    for amendment in amendments:
        if not isinstance(amendment, dict) or set(amendment) != {
            "task_id",
            "action_index",
            "precondition_action_sha256",
            "set",
        }:
            raise ValueError("action override amendment fields are invalid")
        task_id = amendment["task_id"]
        action_index = amendment["action_index"]
        precondition = amendment["precondition_action_sha256"]
        updates = amendment["set"]
        if (
            type(task_id) is not int
            or type(action_index) is not int
            or action_index < 1
            or not _is_sha256(precondition)
            or not isinstance(updates, dict)
            or not updates
            or not set(updates) <= _ACTION_OVERRIDE_FIELDS
        ):
            raise ValueError("action override amendment is invalid")
        if "script_mode" in updates and updates["script_mode"] != "function_body":
            raise ValueError("action override script_mode is invalid")
        script = updates.get("script")
        if script is not None and (
            not isinstance(script, str)
            or not script.strip()
            or len(script.encode("utf-8")) > _MAX_OVERRIDE_SCRIPT_BYTES
        ):
            raise ValueError("action override script is invalid")
        hidden = updates.get("wait_for_selector_hidden")
        if hidden is not None and (not isinstance(hidden, str) or not hidden):
            raise ValueError("action override hidden selector is invalid")
        selector = updates.get("selector")
        if selector is not None and (not isinstance(selector, str) or not selector.strip()):
            raise ValueError("action override selector is invalid")
        wait_state = updates.get("wait_state")
        if wait_state is not None and wait_state not in {
            "attached",
            "detached",
            "visible",
            "hidden",
        }:
            raise ValueError("action override wait state is invalid")
        for flag in ("force", "wait_for_enabled"):
            if flag in updates and type(updates[flag]) is not bool:
                raise ValueError(f"action override {flag} flag is invalid")
        identity = (task_id, action_index)
        if identity in identities:
            raise ValueError("action override amendments contain a duplicate action")
        identities.add(identity)
        validated.append(amendment)
    return validated


def load_action_overrides(path: Path) -> tuple[dict[int, list[dict]], str]:
    """Load strict, versioned amendments without consulting a replay result."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("action overrides must be a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema", "amendments"}:
        raise ValueError("action overrides contract fields are invalid")
    if payload["schema"] != ACTION_OVERRIDES_SCHEMA:
        raise ValueError("action overrides schema is invalid")
    amendments = _validate_action_amendments(payload["amendments"])
    if not amendments:
        raise ValueError("action overrides must contain at least one amendment")
    grouped: dict[int, list[dict]] = {}
    for amendment in amendments:
        grouped.setdefault(amendment["task_id"], []).append(amendment)
    return grouped, file_sha256(path)


def apply_action_overrides(
    actions: list[dict], amendments: list[dict], *, task_id: int
) -> list[dict]:
    """Apply hash-guarded generic action fields to one immutable source trajectory."""

    result = [dict(action) for action in actions]
    for amendment in _validate_action_amendments(amendments):
        if amendment["task_id"] != task_id:
            raise ValueError(f"task {task_id}: action override belongs to another task")
        index = amendment["action_index"] - 1
        if index >= len(result):
            raise ValueError(f"task {task_id}: action override index is outside the trajectory")
        original = actions[index]
        if _json_sha256(original) != amendment["precondition_action_sha256"]:
            raise ValueError(f"task {task_id}: action override precondition changed")
        if {"script", "script_mode"} & set(amendment["set"]) and original.get(
            "action"
        ) != "evaluate":
            raise ValueError(f"task {task_id}: script fields require an evaluate action")
        if {"force", "wait_for_enabled"} & set(amendment["set"]) and original.get("action") not in {
            "click",
            "fill",
            "select",
        }:
            raise ValueError(f"task {task_id}: actionability fields require click, fill, or select")
        if "selector" in amendment["set"] and original.get("action") not in {
            "click",
            "fill",
            "select",
            "wait",
        }:
            raise ValueError(
                f"task {task_id}: selector override requires click, fill, select, or wait"
            )
        if "wait_state" in amendment["set"] and original.get("action") != "wait":
            raise ValueError(f"task {task_id}: wait_state requires a wait action")
        result[index].update(amendment["set"])
    return result


def catalog_action_overrides(catalog: dict, row: dict) -> list[dict]:
    """Read and validate the self-contained amendment provenance on a catalog row."""

    provenance = row.get("action_overrides")
    source_sha256 = catalog.get("action_overrides_sha256")
    if provenance is None:
        return []
    if (
        not isinstance(provenance, dict)
        or set(provenance) != {"schema", "source_sha256", "amendments_sha256", "amendments"}
        or provenance.get("schema") != ACTION_OVERRIDES_SCHEMA
        or not _is_sha256(source_sha256)
        or provenance.get("source_sha256") != source_sha256
        or not _is_sha256(provenance.get("amendments_sha256"))
    ):
        raise ValueError(f"task {row.get('task_id')}: action override provenance is invalid")
    amendments = _validate_action_amendments(provenance["amendments"])
    if not amendments or _json_sha256(amendments) != provenance["amendments_sha256"]:
        raise ValueError(f"task {row.get('task_id')}: action override provenance changed")
    return amendments


def load_catalog_inputs(
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path,
    *,
    accepted_ledger_path: Path | None = None,
    action_overrides_path: Path | None = None,
) -> tuple[dict, dict[WebArenaSite, str], dict[int, object]]:
    """Load and fully validate one frozen catalog input set."""

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
        raise ValueError("replay catalog binding is invalid")
    if accepted_ledger_path is not None and catalog.get("accepted_ledger_sha256") != file_sha256(
        accepted_ledger_path
    ):
        raise ValueError("replay catalog is not bound to the accepted ledger")
    external_overrides: dict[int, list[dict]] | None = None
    has_override_binding = "action_overrides_sha256" in catalog or any(
        row.get("action_overrides") is not None for row in rows
    )
    if has_override_binding and action_overrides_path is None:
        raise ValueError("replay catalog requires its external action overrides source")
    if action_overrides_path is not None:
        external_overrides, override_sha256 = load_action_overrides(action_overrides_path)
        if catalog.get("action_overrides_sha256") != override_sha256:
            raise ValueError("replay catalog is not bound to the action overrides")
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
        amendments = catalog_action_overrides(catalog, row)
        if external_overrides is not None and amendments != external_overrides.get(task_id, []):
            raise ValueError(f"task {task_id}: catalog action overrides differ from their source")
        actions = apply_action_overrides(actions, amendments, task_id=task_id)
        bound = rebind_remote_calls(
            actions, sites, set(TRUSTED_RECORDED_HOSTS), row_source_sites(catalog, row)
        )
        if _json_sha256(bound) != row.get("bound_calls_sha256"):
            raise ValueError(f"task {task_id}: bound actions changed")
        if "recorded_response" in row:
            raise ValueError("recorded answers cannot enter a replay catalog")
    if external_overrides is not None and set(external_overrides) - set(ids):
        raise ValueError("action overrides contain tasks outside the catalog")
    return catalog, sites, tasks


def completed_task_ids(progress_path: Path, execution_order: list[int]) -> set[int]:
    """Return completed IDs only when progress is a strict order prefix."""

    if not progress_path.exists():
        return set()
    rows = load_jsonl(progress_path)
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
        profile.get("schema") not in ENVIRONMENT_SCHEMAS
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
    expected_evaluator_checksum: str | None = None,
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
        or evaluation.get("webarena_verified_evaluator_checksum")
        != (expected_evaluator_checksum or compute_evaluator_checksum())
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


def validate_catalog_report_profile(profile_path: Path, data_home: Path) -> dict:
    """Validate the repository authority against its frozen local release artifacts."""

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    root = repository_root(profile_path)
    dataset_path = root / str(profile.get("dataset_path") or "")
    scope_path = root / str(profile.get("scope_path") or "")
    if (
        profile.get("schema") not in REPORT_PROFILE_SCHEMAS
        or not dataset_path.is_file()
        or file_sha256(dataset_path) != profile.get("dataset_sha256")
        or not scope_path.is_file()
        or file_sha256(scope_path) != profile.get("scope_sha256")
    ):
        raise ValueError("catalog report profile changed its versioned dataset binding")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    task_ids = scope.get("task_ids")
    evaluator_source = dataset.get("evaluator_source") or {}
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = (
        project.get("project", {}).get("optional-dependencies", {}).get("browser-use-eval", [])
    )
    sources = [
        value.split(" @ ", 1)[1]
        for value in requirements
        if value.startswith("webarena-verified @ ")
    ]
    dependency_source = {}
    if len(sources) == 1:
        repository, separator, revision = sources[0].rpartition("@")
        if separator and repository.startswith("git+"):
            dependency_source = {"git": repository.removeprefix("git+"), "rev": revision}
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
        dataset.get("schema") not in DATASET_SCHEMAS
        or scope.get("schema") not in SCOPE_SCHEMAS
        or not isinstance(task_ids, list)
        or not task_ids
        or any(not isinstance(task_id, int) for task_id in task_ids)
        or len(set(task_ids)) != len(task_ids)
        or scope.get("task_count") != len(task_ids)
        or dataset.get("task_count") != len(task_ids)
        or (
            dataset.get("task_id_range") is not None
            and dataset.get("task_id_range") != [min(task_ids), max(task_ids)]
        )
        or scope.get("official_dataset_sha256") != dataset.get("official_dataset_sha256")
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
        raise ValueError("WebArena catalog dataset definition is invalid")
    root = data_home / str(profile.get("evidence_root") or "")
    if root.is_symlink() or not root.is_dir() or data_home.resolve() not in root.resolve().parents:
        raise ValueError("catalog report evidence root is missing or unsafe")
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
        or accepted_counts.get("total") != len(task_ids)
        or accepted_counts.get("pass") != len(task_ids)
        or accepted_counts.get("unresolved") != 0
        or ledger.get("schema") not in ACCEPTANCE_SCHEMAS
        or ledger.get("scope_sha256") != profile.get("scope_sha256")
        or ledger.get("counts") != accepted_counts
    ):
        raise ValueError("catalog report acceptance population changed")
    catalog = json.loads((root / artifacts["accepted_catalog"][0]).read_text(encoding="utf-8"))
    if (
        catalog.get("schema") not in CATALOG_SCHEMAS
        or catalog.get("task_ids") != task_ids
        or catalog.get("accepted_ledger_sha256") != profile["acceptance_ledger_sha256"]
    ):
        raise ValueError("accepted catalog is not bound to the current ledger")
    publication = profile.get("publication") or {}
    if not all(publication.get(key) for key in ("host", "slug", "version_id", "share_url")):
        raise ValueError("catalog report publication target is incomplete")
    return profile


def validate_catalog_comparison_release(binding_path: Path, data_home: Path) -> dict:
    """Validate the current multi-browser release without storing measured evidence in Git."""

    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    evidence_root = Path(str(binding.get("evidence_root") or ""))
    if (
        binding.get("schema") != COMPARISON_RELEASE_SCHEMA
        or evidence_root.is_absolute()
        or ".." in evidence_root.parts
        or not evidence_root.parts
    ):
        raise ValueError("comparison release binding is invalid")
    root = data_home / evidence_root
    if root.is_symlink() or not root.is_dir() or data_home.resolve() not in root.resolve().parents:
        raise ValueError("comparison release evidence root is missing or unsafe")

    required_artifacts = {
        "profile",
        "dataset",
        "results",
        "failure_review",
        "execution_metadata_revision",
        "report_manifest",
        "publication_archive",
        "publication_attestation",
        "publication_receipt",
    }
    artifacts = binding.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != required_artifacts:
        raise ValueError("comparison release artifact inventory is incomplete")
    resolved: dict[str, Path] = {}
    for name, artifact in artifacts.items():
        relative = Path(str((artifact or {}).get("path") or ""))
        expected = (artifact or {}).get("sha256")
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or not _is_sha256(expected)
        ):
            raise ValueError(f"comparison release {name} binding is invalid")
        target = root / relative
        components = [
            root.joinpath(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1)
        ]
        if (
            any(component.is_symlink() for component in components)
            or not target.is_file()
            or file_sha256(target) != expected
        ):
            raise ValueError(f"comparison release {name} artifact changed")
        resolved[name] = target
    if len({path.resolve() for path in resolved.values()}) != len(resolved):
        raise ValueError("comparison release artifacts must be distinct files")

    profile = json.loads(resolved["profile"].read_text(encoding="utf-8"))
    dataset = json.loads(resolved["dataset"].read_text(encoding="utf-8"))
    results = json.loads(resolved["results"].read_text(encoding="utf-8"))
    metadata_revision = json.loads(
        resolved["execution_metadata_revision"].read_text(encoding="utf-8")
    )
    manifest = json.loads(resolved["report_manifest"].read_text(encoding="utf-8"))
    receipt = json.loads(resolved["publication_receipt"].read_text(encoding="utf-8"))
    attestation = json.loads(resolved["publication_attestation"].read_text(encoding="utf-8"))
    attempts = results.get("attempts")
    participants = binding.get("participants")
    if (
        profile.get("schema") != "lex_webarena_trajectory_replay_profile/1"
        or results.get("schema") != "lex_webarena_catalog_comparison_results/1"
        or not isinstance(attempts, list)
        or not isinstance(participants, list)
        or not participants
    ):
        raise ValueError("comparison release inputs are invalid")
    source_inputs = metadata_revision.get("source_inputs")
    verification = metadata_revision.get("verification") or {}
    if (
        metadata_revision.get("schema") != "lex_trajectory_replay_metadata_revision/1"
        or metadata_revision.get("provider_calls") != 0
        or metadata_revision.get("results_sha256") != artifacts["results"]["sha256"]
        or not isinstance(source_inputs, list)
        or not source_inputs
        or any(
            not isinstance(row, dict)
            or row.get("backend") != "playwright"
            or not _is_sha256(row.get("sha256"))
            or not row.get("path")
            for row in source_inputs
        )
        or verification.get("source_input_count") != len(source_inputs)
        or verification.get("non_playwright_source_inputs") != 0
        or profile.get("execution_backend") != "playwright"
        or (profile.get("resources") or {}).get("collection_status") != "not_recorded"
    ):
        raise ValueError("comparison release execution metadata revision changed")
    methods = profile.get("methods") or []
    method_registry = {method.get("id"): method for method in methods if isinstance(method, dict)}
    declared = {row.get("id"): row for row in participants if isinstance(row, dict)}
    if (
        len(method_registry) != len(methods)
        or len(declared) != len(participants)
        or set(declared) != set(method_registry)
        or any(
            set(row) != {"id", "engine", "label", "pass", "fail"}
            or row.get("engine") != method_registry[participant_id].get("engine")
            or row.get("label") != method_registry[participant_id].get("label")
            for participant_id, row in declared.items()
        )
    ):
        raise ValueError("comparison release participant registry changed")
    task_count = binding.get("task_count")
    trajectories = dataset.get("trajectories") or []
    dataset_task_ids = {row.get("task_id") for row in trajectories if isinstance(row, dict)}
    task_ids: set[int] | None = None
    for participant_id, declared_row in declared.items():
        rows = [row for row in attempts if row.get("participant_id") == participant_id]
        row_task_ids = {row.get("task_id") for row in rows}
        passed = sum(row.get("status") == "pass" for row in rows)
        if (
            len(rows) != task_count
            or len(row_task_ids) != task_count
            or row_task_ids != dataset_task_ids
            or (task_ids is not None and row_task_ids != task_ids)
            or declared_row.get("pass") != passed
            or declared_row.get("fail") != len(rows) - passed
        ):
            raise ValueError("comparison release participant counts changed")
        task_ids = row_task_ids
    logical_results = len(attempts)
    physical_executions = results.get("physical_executions")
    failed_results = sum(row.get("fail", 0) for row in participants)
    if (
        type(task_count) is not int
        or task_count <= 0
        or dataset.get("schema") != "lex_webarena_catalog_comparison_dataset/1"
        or len(trajectories) != task_count
        or len(dataset_task_ids) != task_count
        or any(type(task_id) is not int for task_id in dataset_task_ids)
        or (dataset.get("counts") or {}).get("verified_tasks") != task_count
        or (dataset.get("counts") or {}).get("verified_trajectories") != task_count
        or results.get("dataset_sha256") != artifacts["dataset"]["sha256"]
        or binding.get("logical_results") != logical_results
        or logical_results != task_count * len(declared)
        or any(
            row.get("participant_id") not in declared
            or row.get("engine") != method_registry[row.get("participant_id")].get("engine")
            for row in attempts
        )
        or not isinstance(physical_executions, list)
        or binding.get("physical_executions") != len(physical_executions)
        or len(physical_executions) < logical_results
        or any(
            row.get("participant_id") not in declared
            or row.get("engine") != method_registry[row.get("participant_id")].get("engine")
            or row.get("task_id") not in dataset_task_ids
            for row in physical_executions
        )
        or manifest.get("task_count") != task_count
        or manifest.get("logical_results") != logical_results
        or manifest.get("failed_results") != failed_results
        or manifest.get("reviewed_failures") != failed_results
        or manifest.get("participants") != list(declared)
        or manifest.get("results_sha256") != artifacts["results"]["sha256"]
        or manifest.get("dataset_sha256") != artifacts["dataset"]["sha256"]
        or manifest.get("review_sha256") != artifacts["failure_review"]["sha256"]
    ):
        raise ValueError("comparison release population or report binding changed")
    publication = binding.get("publication") or {}
    profile_publication = profile.get("publication") or {}
    if (
        receipt.get("verified") is not True
        or receipt.get("archive_sha256") != artifacts["publication_archive"]["sha256"]
        or attestation.get("archive_sha256") != artifacts["publication_archive"]["sha256"]
        or attestation.get("profile_sha256") != artifacts["profile"]["sha256"]
        or attestation.get("dataset_sha256") != artifacts["dataset"]["sha256"]
        or attestation.get("review_sha256") != artifacts["failure_review"]["sha256"]
        or attestation.get("evidence_sha256") != artifacts["results"]["sha256"]
        or publication.get("slug") != receipt.get("slug")
        or publication.get("share_url") != receipt.get("share_url")
        or publication.get("version_id") != receipt.get("version")
        or publication.get("slug") != profile_publication.get("site_slug")
        or publication.get("share_url") != profile_publication.get("share_url")
        or publication.get("version_id") != profile_publication.get("expected_version")
    ):
        raise ValueError("comparison release publication binding changed")
    return binding
