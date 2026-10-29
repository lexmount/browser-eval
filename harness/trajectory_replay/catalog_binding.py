"""Bind a frozen WebArena action catalog to another site deployment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.trajectory_replay.catalog_contracts import (
    ACTION_OVERRIDES_SCHEMA,
    TRUSTED_RECORDED_HOSTS,
    apply_action_overrides,
    catalog_action_overrides,
    file_sha256,
    load_action_overrides,
    load_catalog_inputs,
    row_source_sites,
)
from harness.trajectory_replay.public_webarena.suite import load_suite
from harness.trajectory_replay.remote_runtime import load_remote_sites, rebind_remote_calls
from harness.trajectory_replay.webarena_replay import _json_sha256


def rebind_catalog(
    catalog_path: Path,
    suite_path: Path,
    site_map_path: Path,
    *,
    revised_task_ids: set[int] | None = None,
    source_site_map_path: Path | None = None,
    action_overrides_path: Path | None = None,
    revise_action_overrides: bool = False,
) -> dict:
    if revise_action_overrides and action_overrides_path is None:
        raise ValueError("action override revision requires an overrides source")
    source = json.loads(catalog_path.read_text(encoding="utf-8"))
    suite = load_suite(suite_path)
    tasks = {int(task.task_id.removeprefix("webarena-")): task for task in suite.tasks}
    sites = load_remote_sites(site_map_path)
    target = dict(source)
    target["predecessor_catalog_sha256"] = file_sha256(catalog_path)
    target["site_map_sha256"] = file_sha256(site_map_path)
    target["portable_suite_sha256"] = file_sha256(suite_path)
    if action_overrides_path is not None:
        override_map, override_sha256 = load_action_overrides(action_overrides_path)
        existing_sha256 = source.get("action_overrides_sha256")
        has_existing_overrides = "action_overrides_sha256" in source or any(
            row.get("action_overrides") is not None for row in source.get("rows") or []
        )
        if (
            has_existing_overrides
            and existing_sha256 != override_sha256
            and not revise_action_overrides
        ):
            raise ValueError("action overrides differ from the predecessor catalog")
        if has_existing_overrides and existing_sha256 == override_sha256:
            embedded = {
                row["task_id"]: amendments
                for row in source.get("rows") or []
                if (amendments := catalog_action_overrides(source, row))
            }
            if embedded != override_map:
                raise ValueError("action overrides differ from the predecessor catalog rows")
        target["action_overrides_sha256"] = override_sha256
    else:
        if "action_overrides_sha256" in source or any(
            row.get("action_overrides") is not None for row in source.get("rows") or []
        ):
            raise ValueError("catalog rebind requires its external action overrides source")
        override_map = {}
    if source_site_map_path is not None:
        if file_sha256(source_site_map_path) != source["site_map_sha256"]:
            raise ValueError("source site map differs from the predecessor catalog")
        target["source_site_urls"] = {
            site.value: url for site, url in load_remote_sites(source_site_map_path).items()
        }
    target["rows"] = []
    revised = set(revised_task_ids or ())
    if revised and target["site_map_sha256"] != source["site_map_sha256"]:
        raise ValueError("action revisions must keep the site map fixed")
    for original in source["rows"]:
        row = dict(original)
        task_id = row["task_id"]
        actions = (
            list(tasks[task_id].tool_calls) if row["mode"] == "portable" else row["source_actions"]
        )
        amendments = override_map.get(task_id, [])
        actions = apply_action_overrides(actions, amendments, task_id=task_id)
        if amendments:
            row["action_overrides"] = {
                "schema": ACTION_OVERRIDES_SCHEMA,
                "source_sha256": target["action_overrides_sha256"],
                "amendments_sha256": _json_sha256(amendments),
                "amendments": amendments,
            }
        else:
            row.pop("action_overrides", None)
        row["bound_calls_sha256"] = _json_sha256(
            rebind_remote_calls(
                actions, sites, set(TRUSTED_RECORDED_HOSTS), row_source_sites(target, row)
            )
        )
        if task_id in revised:
            if (
                row["mode"] != "portable"
                or row["bound_calls_sha256"] == original["bound_calls_sha256"]
            ):
                raise ValueError(f"task {task_id} has no revised portable actions")
            row["source_kind"] = "revised_portable_suite"
            row["source_suite_sha256"] = file_sha256(suite_path)
            row.pop("source_path", None)
        target["rows"].append(row)
    if revised - {row["task_id"] for row in target["rows"]}:
        raise ValueError("revised task IDs are outside the catalog")
    if set(override_map) - {row["task_id"] for row in target["rows"]}:
        raise ValueError("action overrides contain tasks outside the catalog")
    if revised:
        target["repairs"] = list(source.get("repairs") or []) + [
            {
                "kind": "portable_action_revision",
                "task_ids": sorted(revised),
                "suite_sha256": file_sha256(suite_path),
            }
        ]
    if revise_action_overrides:
        if not has_existing_overrides or existing_sha256 == target["action_overrides_sha256"]:
            raise ValueError("action override revision must replace an existing source")
        target["repairs"] = list(target.get("repairs") or []) + [
            {
                "kind": "action_override_revision",
                "predecessor_action_overrides_sha256": existing_sha256,
                "action_overrides_sha256": target["action_overrides_sha256"],
                "task_ids": sorted(override_map),
            }
        ]
    return target




def current_catalog_inputs(
    site_map_path: Path, binding_path: Path, *, suite_path=None
) -> tuple[Path, Path]:
    """Bind the locked public inputs to a deployment using the existing catalog contract."""
    from harness.trajectory_replay.report_inputs import replay_input_path
    catalog_path = replay_input_path(current='full-catalog')
    suite_path = replay_input_path(suite_path, current='full-portable')
    result = rebind_catalog(catalog_path, suite_path, site_map_path)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if binding_path.is_symlink():
        raise ValueError('catalog input binding cannot be symlinked')
    if binding_path.exists():
        if binding_path.read_text(encoding='utf-8') != payload:
            raise ValueError('existing run uses a different catalog input binding')
    else:
        binding_path.parent.mkdir(parents=True, exist_ok=True)
        binding_path.write_text(payload, encoding='utf-8')
    load_catalog_inputs(binding_path, suite_path, site_map_path)
    return binding_path, suite_path

def public_replay_calls(calls: list[dict], source_sites: dict, public_sites: dict) -> list[dict]:
    """Project deployment origins without changing the compiled action language."""
    from urllib.parse import urlsplit, urlunsplit
    trusted = set(TRUSTED_RECORDED_HOSTS)
    projected = rebind_remote_calls(calls, public_sites, trusted, source_sites, origins_only=True)
    # Exercise both transport schemes independently of the source deployment.
    # Qualification receipts remain bound to their original, unmodified bytes.
    for scheme in ('http', 'https'):
        fixture = {
            site: urlunsplit((scheme, f'{site.value}.replay.invalid', urlsplit(url).path, '', ''))
            for site, url in public_sites.items()
        }
        for origins_only in (True, False):
            original_view = rebind_remote_calls(
                calls, fixture, trusted, source_sites, origins_only=origins_only
            )
            public_view = rebind_remote_calls(
                projected, fixture, trusted, public_sites, origins_only=origins_only
            )
            if original_view != public_view:
                raise ValueError('public origin projection changes the canonical action view')
    return projected


def public_corrected_inputs(
    dataset_path: Path,
    public_sites: dict,
    *,
    dataset_sha256: str,
    source_sites_by_task: dict | None = None,
) -> tuple[dict, dict]:
    """Keep replay inputs and hashed seed provenance, excluding prior response payloads."""
    import copy

    if file_sha256(dataset_path) != dataset_sha256:
        raise ValueError("corrected replay input authority changed")
    source = json.loads(dataset_path.read_text(encoding="utf-8"))
    if source.get("schema") != "lex_webarena_verified_trajectory_dataset/1":
        raise ValueError("unsupported corrected replay input schema")
    target = {
        key: copy.deepcopy(source[key])
        for key in (
            "schema",
            "benchmark",
            "benchmark_version",
            "official_data_checksum",
            "selection_sha256",
            "semantics",
        )
    }
    target["semantics"]["evaluation_input_role"] = (
        "declarative task type only; RETRIEVE responses come from current-run observations"
    )
    target["trajectories"] = []
    receipts = {"source_dataset_sha256": dataset_sha256, "tasks": []}
    for original in source["trajectories"]:
        task_id = original["task_id"]
        task = {
            key: copy.deepcopy(original[key])
            for key in (
                "id",
                "task_id",
                "task",
                "tool_calls",
                "source",
            )
        }
        task_type = original["evaluation_input"]["task_type"]
        if task_type not in {"RETRIEVE", "MUTATE", "NAVIGATE"}:
            raise ValueError(f"task {task_id}: unsupported evaluator task type")
        task["evaluation_input"] = {"task_type": task_type}
        task["tool_calls"] = public_replay_calls(
            task["tool_calls"],
            (source_sites_by_task or {}).get(task_id, {}),
            public_sites,
        )
        target["trajectories"].append(task)
        receipts["tasks"].append(
            {
                "task_id": task_id,
                "source_task_sha256": _json_sha256(original),
                "source_actions_sha256": _json_sha256(original["tool_calls"]),
                "public_actions_sha256": _json_sha256(task["tool_calls"]),
                "historical_evaluation_input_sha256": _json_sha256(original["evaluation_input"]),
                "historical_qualification_sha256": _json_sha256(original.get("verification", {})),
            }
        )
    target["provenance"] = {
        "source_dataset_sha256": dataset_sha256,
        "edition": "historical-corrected-inputs",
        "qualification": (
            "External historical receipts retain their original contracts; "
            "this projection is not a new qualification."
        ),
    }
    return target, receipts


def public_catalog_inputs(
    catalog_path: Path,
    suite_path: Path,
    public_site_map_path: Path,
    *,
    catalog_sha256: str,
    suite_sha256: str,
) -> tuple[dict, dict, dict]:
    """Export reusable inputs from a hash-bound accepted catalog, excluding run evidence."""
    import copy

    if file_sha256(catalog_path) != catalog_sha256 or file_sha256(suite_path) != suite_sha256:
        raise ValueError('accepted replay input authority changed')
    source = json.loads(catalog_path.read_text(encoding='utf-8'))
    raw_suite = json.loads(suite_path.read_text(encoding='utf-8'))
    if source.get('portable_suite_sha256') != suite_sha256:
        raise ValueError('accepted catalog does not bind the source suite')
    sites = load_remote_sites(public_site_map_path)
    source_tasks = {int(task['id'].removeprefix('webarena-')): task for task in raw_suite['tasks']}
    rows = source.get('rows') or []
    ids = [row['task_id'] for row in rows]
    if source.get('task_ids') != ids or len(set(ids)) != len(ids):
        raise ValueError('accepted catalog task identities changed')
    if {row['task_id'] for row in rows if row['mode'] == 'portable'} != set(source_tasks):
        raise ValueError('accepted portable input population changed')
    suite = {'schema': raw_suite['schema'], 'tasks': []}
    target = {'schema': source['schema'], 'task_ids': ids, 'rows': [],
              'source_site_urls': {site.value: url for site, url in sites.items()},
              'site_map_sha256': file_sha256(public_site_map_path)}
    receipts = {
        "schema": "lex_replay_public_input_projection/1",
        "catalog_sha256": catalog_sha256,
        "suite_sha256": suite_sha256,
        "source_accepted_ledger_sha256": source.get("accepted_ledger_sha256"),
        "source_action_overrides_sha256": source.get("action_overrides_sha256"),
        "action_view_check": "explicit-http-and-https-origin-and-runtime-call-rebinding",
        "tasks": [],
    }
    for row in rows:
        task_id = row['task_id']
        source_sites = row_source_sites(source, row)
        if row['mode'] == 'portable':
            task = copy.deepcopy(source_tasks[task_id])
            calls = task['trajectory']['actions']
        elif row['mode'] == 'direct':
            calls = row['source_actions']
        else:
            raise ValueError(f'task {task_id}: unsupported catalog mode')
        amendments = catalog_action_overrides(source, row)
        effective = apply_action_overrides(calls, amendments, task_id=task_id)
        projected = public_replay_calls(effective, source_sites, sites)
        public_row = {
            key: copy.deepcopy(row[key])
            for key in ("task_id", "mode", "source_kind", "readiness", "retrieval_contract")
            if key in row
        }
        if 'retrieval_contract' in public_row:
            from harness.trajectory_replay.retrieval_acceptance import digest, validate_contract
            validate_contract(row['retrieval_contract'], calls)
            contract = public_row['retrieval_contract']
            contract['canonical_calls_sha256'] = digest(projected)
            for observation in contract['sources']:
                observation['action_sha256'] = digest(projected[observation['step'] - 1])
            validate_contract(contract, projected)
        if row['mode'] == 'portable':
            task['trajectory']['actions'] = projected
            environment = task.get('environment') or {}
            if environment.get('start_urls'):
                environment['start_urls'] = [call['url'] for call in public_replay_calls(
                    [{'url': url} for url in environment['start_urls']], source_sites, sites)]
            suite['tasks'].append(task)
            public_row['task_sha256'] = _json_sha256(task)
        else:
            public_row['source_actions'] = projected
        public_row['bound_calls_sha256'] = _json_sha256(rebind_remote_calls(
            projected, sites, set(TRUSTED_RECORDED_HOSTS), sites))
        from urllib.parse import urlsplit

        public_text = json.dumps(public_row) + (
            json.dumps(task) if row["mode"] == "portable" else ""
        )
        canonical_hosts = {urlsplit(url).hostname for url in sites.values()}
        for source_url in source_sites.values():
            hostname = urlsplit(source_url).hostname
            if hostname and hostname not in canonical_hosts and hostname in public_text:
                raise ValueError(f'task {task_id}: source deployment remains in public input')
        target['rows'].append(public_row)
        receipts['tasks'].append({'task_id': task_id, 'mode': row['mode'],
            'source_actions_sha256': _json_sha256(calls),
            'effective_actions_sha256': _json_sha256(effective),
            'public_actions_sha256': _json_sha256(projected),
            'source_bound_calls_sha256': row['bound_calls_sha256'],
            'source_task_sha256': row.get('task_sha256'),
            'source_qualification_sha256': row.get('accepted_progress_sha256'),
            'amendments_sha256': _json_sha256(amendments),
            'source_retrieval_contract_sha256': _json_sha256(row.get('retrieval_contract')),
            'public_retrieval_contract_sha256': _json_sha256(public_row.get('retrieval_contract'))})
    return target, suite, receipts

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--portable-suite", type=Path, required=True)
    parser.add_argument("--site-map", type=Path, required=True)
    parser.add_argument("--source-site-map", type=Path)
    parser.add_argument("--action-overrides", type=Path)
    parser.add_argument("--revise-action-overrides", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revised-task-id", type=int, action="append", default=[])
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = rebind_catalog(
        args.catalog,
        args.portable_suite,
        args.site_map,
        revised_task_ids=set(args.revised_task_id),
        source_site_map_path=args.source_site_map,
        action_overrides_path=args.action_overrides,
        revise_action_overrides=args.revise_action_overrides,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    load_catalog_inputs(
        args.output,
        args.portable_suite,
        args.site_map,
        action_overrides_path=args.action_overrides,
    )
    print(json.dumps({"tasks": len(result["rows"]), "catalog_sha256": file_sha256(args.output)}))


if __name__ == "__main__":
    main()
