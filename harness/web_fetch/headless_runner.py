"""Run browser Markdown methods against the unchanged canonical Web Fetch cases."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from harness.web_fetch.config import (
    load_dataset,
    load_provider_settings,
    load_runtime_env,
)
from harness.web_fetch.providers import build_provider
from harness.web_fetch.providers.headless import HEADLESS_CONVERSION
from harness.web_fetch.providers.runtime_identity import (
    require_expected_provider_versions,
    version_number,
)
from harness.web_fetch.reference_inputs import input_tree
from harness.web_fetch.runner import run_evaluation
from harness.web_fetch.safety import safe_identifier

ROOT = Path(__file__).resolve().parents[2]


def selection(value, label):
    names = [name.strip() for name in value.split(",")]
    if not names or len(names) != len(set(names)) or any(not name for name in names):
        raise ValueError(label + " selection must be nonempty and unique")
    for name in names:
        safe_identifier(name)
    return names


def select_cohort(settings, *, resource=False, cohort=None, providers=None):
    """Select a declared cohort without inferring behavior from provider labels."""
    registry = settings["providers"]
    key = (
        cohort
        if cohort is not None
        else settings.get("resource_cohort" if resource else "default_cohort")
    )
    if key is not None:
        safe_identifier(key)
    declared = settings.get("cohorts", {})
    if not isinstance(declared, dict):
        raise ValueError("Cohorts must be a registry")
    if key is not None and key not in declared:
        raise ValueError("Unknown cohort: " + str(key))
    selected = declared.get(key, {})
    if not isinstance(selected, dict):
        raise ValueError("Cohort must define settings")
    names = (
        selection(providers, "Provider")
        if providers is not None
        else selected.get("providers", list(registry))
    )
    if not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names):
        raise ValueError("Provider selection must be nonempty strings")
    if len(names) != len(set(names)):
        raise ValueError("Provider selection must be nonempty and unique")
    for name in names:
        safe_identifier(name)
        if name not in registry:
            raise ValueError("Unknown provider: " + name)
    overrides = selected.get("provider_overrides", {})
    if not isinstance(overrides, dict) or set(overrides) - set(registry):
        raise ValueError("Cohort overrides must name registered providers")
    if any(not isinstance(value, dict) for value in overrides.values()):
        raise ValueError("Provider overrides must define settings")
    configs = {name: {**registry[name], **overrides.get(name, {})} for name in names}
    return key, selected, configs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/providers.headless.json")
    parser.add_argument(
        "--providers", help="Comma-separated registered methods; defaults to the selected cohort"
    )
    parser.add_argument("--cohort", help="Cohort declared in the provider configuration")
    parser.add_argument("--case-ids")
    parser.add_argument("--tracks", help="Comma-separated task families; default all")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument(
        "--max-failure-retries",
        type=int,
        choices=(0, 1),
        default=0,
        help="Retry a failed task at most once; the final call is authoritative",
    )
    parser.add_argument(
        "--resource-sample-interval-ms",
        type=int,
        help="Sample each disposable Linux container cgroup; use a separate cohort",
    )
    parser.add_argument(
        "--resource-container-image",
        help="Prebuilt immutable-input image used only by the resource cohort",
    )
    parser.add_argument("--resource-cpus", type=float, default=2.0)
    parser.add_argument("--resource-memory", default="2g")
    parser.add_argument(
        "--env-file", type=Path, help="Explicit dotenv file; defaults to repository .env"
    )
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("Output exists; measured evidence must not be overwritten")
    if args.resource_sample_interval_ms is not None:
        if args.resource_sample_interval_ms < 10:
            parser.error("--resource-sample-interval-ms must be at least 10")
        if args.workers != 1:
            parser.error("Resource measurement requires --workers 1 to avoid local CPU contention")
    load_runtime_env(args.env_file)
    dataset, cases = load_dataset(args.dataset)
    input_trees = {
        folder: input_tree(args.dataset, folder) for folder in ("sources", "list-references")
    }
    if args.tracks is not None:
        tracks = set(selection(args.tracks, "Track"))
        if tracks - {case.track for case in cases}:
            parser.error("Unknown track")
        cases = [c for c in cases if c.track in tracks]
    if args.case_ids is not None:
        wanted = set(selection(args.case_ids, "Case"))
        cases = [c for c in cases if c.id in wanted]
        if len(cases) != len(wanted):
            parser.error("Unknown case ID")
    if not cases:
        parser.error("Selected task scope is empty")
    settings = load_provider_settings(args.config)
    cohort, cohort_config, config = select_cohort(
        settings,
        resource=args.resource_sample_interval_ms is not None,
        cohort=args.cohort,
        providers=args.providers,
    )
    names = list(config)
    if args.resource_sample_interval_ms is not None:
        if args.resource_container_image is None:
            args.resource_container_image = cohort_config.get("resource_container_image")
        if not args.resource_container_image:
            parser.error(
                "Resource cohort requires --resource-container-image or a configured image"
            )
        config = {
            name: {
                **value,
                "resource_sample_interval_ms": args.resource_sample_interval_ms,
                "resource_container_image": args.resource_container_image,
                "resource_cpus": args.resource_cpus,
                "resource_memory": args.resource_memory,
            }
            for name, value in config.items()
        }
    providers = [build_provider(name, config[name]) for name in names]
    for provider in providers:
        if missing := provider.validate():
            parser.error(provider.name + ": " + ", ".join(missing))
    preflight_identities = {
        provider.name: provider.implementation_identity() for provider in providers
    }
    require_expected_provider_versions(preflight_identities, config)
    resource_profile = args.resource_sample_interval_ms is not None
    dataset = {
        **dataset,
        "id": cohort_config.get("id", dataset.get("id", "web-fetch") + "--headless"),
        "name": cohort_config.get("name", "浏览器 Web Fetch 对比"),
        "cases": [c for c in dataset["cases"] if c["id"] in {x.id for x in cases}],
        "coverage_plan": {c.track: names for c in cases},
        "comparison": {
            "kind": (
                "headless_client_resource_profile" if resource_profile else "headless_markdown"
            ),
            "timeout_seconds": args.timeout,
            "source_dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
            "adapter_input_sha256": hashlib.sha256(
                (Path(__file__).parent / "providers/headless_worker.py").read_bytes()
            ).hexdigest(),
            "conversion": HEADLESS_CONVERSION,
            "cohort": cohort,
            "concurrency": args.workers,
            "runs_per_case": 1,
            "resource_sample_interval_ms": args.resource_sample_interval_ms,
            "resource_scope": (
                "disposable Linux client container cgroup and dedicated "
                "network namespace; controller excluded; remote Kitesurf "
                "service CPU, memory and upstream traffic excluded"
                if resource_profile
                else None
            ),
            "resource_backend": "docker_cgroup_v2" if resource_profile else None,
            "resource_container_image": args.resource_container_image if resource_profile else None,
            "resource_cpus": args.resource_cpus if resource_profile else None,
            "resource_memory": args.resource_memory if resource_profile else None,
            "max_failure_retries": args.max_failure_retries,
            "retry_selection": "final_physical_attempt_is_authoritative",
        },
    }
    records, _ = run_evaluation(
        output_dir=args.output,
        dataset_meta=dataset,
        dataset_path=args.dataset,
        cases=cases,
        providers=providers,
        provider_config_path=args.config,
        runs=1,
        timeout=args.timeout,
        delay=0.5,
        provider_concurrency=args.workers,
        max_failure_retries=args.max_failure_retries,
        resource_sample_interval_ms=args.resource_sample_interval_ms,
    )
    if len(records) != len(cases) * len(providers):
        raise ValueError("Incomplete benchmark call count")
    postflight_identities = {
        provider.name: provider.implementation_identity(refresh=True) for provider in providers
    }
    if postflight_identities != preflight_identities:
        changed = sorted(
            name for name in names if postflight_identities[name] != preflight_identities[name]
        )
        raise ValueError("Implementation identity changed during execution: " + ", ".join(changed))
    # A mutable path, package name or nightly label is not an implementation
    # identity. Bind call metadata to the preflight identity and fail if an
    # executable changes inside one batch.
    implementation_identities = {}
    for name in names:
        metadata = [r.get("provider_metadata", {}) for r in records if r["provider"] == name]
        hashes = sorted({m["binary_sha256"] for m in metadata if m.get("binary_sha256")})
        versions = sorted({m["browser_version"] for m in metadata if m.get("browser_version")})
        normalized_versions = {version_number(value) for value in versions}
        deployments = sorted({m["deployment"] for m in metadata if m.get("deployment")})
        extractors = sorted({m["extractor"] for m in metadata if m.get("extractor")})
        if len(hashes) > 1 or len(normalized_versions) > 1:
            raise ValueError(name + ": implementation changed during one execution batch")
        preflight = preflight_identities[name]
        if hashes and hashes != [preflight["binary_sha256"]]:
            raise ValueError(name + ": measured executable differs from preflight identity")
        if (
            versions
            and preflight["reported_version"]
            and {version_number(value) for value in versions}
            != {version_number(preflight["reported_version"])}
        ):
            raise ValueError(name + ": measured version differs from preflight identity")
        implementation_identities[name] = {
            **preflight,
            "identity_source": "preflight and postflight executable identity; copied into call metadata outside timing",
            "call_metadata_coverage": {
                "binary_sha256": sum(bool(m.get("binary_sha256")) for m in metadata),
                "reported_version": sum(bool(m.get("browser_version")) for m in metadata),
                "calls": len(metadata),
            },
            "observed_deployments": deployments,
            "observed_extractors": extractors,
        }
    for folder in ("sources", "list-references"):
        if input_trees[folder].exists():
            shutil.copytree(input_trees[folder], args.output / folder)
    manifest = json.loads((args.output / "manifest.json").read_text())
    manifest["dataset"]["source"] = args.dataset.name
    manifest["provider_config_source"] = args.config.name
    manifest["environment"].pop("hostname", None)
    for provider in manifest["providers"]:
        provider["implementation_identity"] = implementation_identities[provider["name"]]
    (args.output / "execution.json").write_text(
        json.dumps({"batches": [manifest]}, ensure_ascii=False, indent=2) + "\n"
    )
    print("Completed:", len(records), "calls. Evidence:", args.output)


if __name__ == "__main__":
    main()
