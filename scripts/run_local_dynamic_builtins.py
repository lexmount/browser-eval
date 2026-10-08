"""Run only CowAgent/OpenClaw/DeepSeek on a frozen dynamic dataset.

Always starts a clean child environment; never loads dotenv, merges results,
builds reports, installs dependencies, or overwrites an existing run directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.reference_inputs import input_tree
from harness.web_fetch.storage import data_home

NAMES = ("cowagent-built-in", "openclaw-built-in", "deepseek-harness-built-in")


@dataclass(frozen=True)
class RuntimePaths:
    cowagent: Path
    openclaw: Path
    deepseek: Path
    node: Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_environment(output: Path, paths: RuntimePaths, temporary: Path | None = None) -> dict[str, str]:
    return {
        "PATH": f"{paths.node.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8", "LC_CTYPE": "UTF-8",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(temporary or output / "runtime/t"), "TSX_DISABLE_CACHE": "1",
        "OPENCLAW_REPO": str(paths.openclaw),
        "OPENCLAW_HOME": str(output / "runtime/openclaw-home"),
        "OPENCLAW_STATE_DIR": str(output / "runtime/openclaw-state"),
        "OPENCLAW_CONFIG_PATH": str(output / "runtime/openclaw-state/openclaw.json"),
        "COW_DATA_DIR": str(output / "runtime/cowagent-data"),
        "NETRC": str(output / "runtime/no-netrc"),
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def selected_dataset(payload: dict, track: str = "dynamic") -> dict:
    cases = [deepcopy(case) for case in payload["cases"] if case.get("track") == track]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Selected track must contain nonempty unique cases")
    result = deepcopy(payload)
    result.update(id=payload["id"] + "--local-dynamic-builtins", cases=cases,
                  coverage_plan={track: list(NAMES)})
    return result


def resolved_config(payload: dict, output: Path, paths: RuntimePaths) -> dict:
    config = {name: deepcopy(payload["providers"][name]) for name in NAMES}
    config[NAMES[0]].update(repo_path=str(paths.cowagent), workspace=str(output / "runtime/cowagent-workspace"))
    config[NAMES[1]].update(repo_path=str(paths.openclaw),
        command=[str(paths.node), "--import", str(paths.openclaw / "node_modules/tsx/dist/loader.mjs"),
                 str(ROOT / "scripts/oss/openclaw_webfetch_worker.mts")],
        env={"OPENCLAW_REPO": str(paths.openclaw)})
    config[NAMES[2]].update(repo_path=str(paths.deepseek),
        command=[str(paths.deepseek / "node_modules/.bin/tsx"),
                 str(paths.deepseek / "node_modules/.pnpm/.browser-harness-dsh-worker.mts")])
    return {"schema_version": payload["schema_version"], "providers": config}


def publishable_identity(identity: dict, paths: RuntimePaths) -> dict:
    """Keep measured hashes/versions; omit local environment and absolute paths."""
    roots = {"harness": ROOT, "cowagent": paths.cowagent,
             "openclaw": paths.openclaw, "deepseek": paths.deepseek}
    def label(value):
        path = Path(value)
        if path == paths.node:
            return "runtime/node"
        if value == identity.get("python_executable"):
            return "runtime/python"
        for name, root in roots.items():
            if path.is_relative_to(root):
                return name + "/" + path.relative_to(root).as_posix()
        raise ValueError("Unrecognized runtime identity path")
    public = {key: deepcopy(value) for key, value in identity.items()
              if key not in {"environment", "python_executable", "file_sha256", "repositories"}}
    public["file_sha256"] = {label(path): value for path, value in identity["file_sha256"].items()}
    public["repositories"] = {name: deepcopy(identity["repositories"][str(root)])
                              for name, root in roots.items() if str(root) in identity["repositories"]}
    public["environment_policy"] = "Explicit clean environment; no dotenv or inherited credentials; isolated state and NETRC"
    return public


def package_providers(output: Path, records: list[dict], manifest: dict, *, directory: str = "providers") -> None:
    """Copy measurements verbatim into single-provider ingestion bundles."""
    from harness.web_fetch.metrics import summarize
    for name in NAMES:
        target = output / directory / name
        target.mkdir(parents=True, exist_ok=False)
        selected = [record for record in records if record["provider"] == name]
        for filename in ("dataset.json", "providers.json", "runtime-identity.json", "reference-validation.json"):
            shutil.copy2(output / filename, target / filename)
        shutil.copytree(output / "sources", target / "sources")
        shutil.copytree(output / "artifacts" / name, target / "artifacts" / name)
        (target / "results.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n"
                                                     for record in selected), encoding="utf-8")
        batch = deepcopy(manifest)
        batch.update(providers=[provider for provider in manifest["providers"] if provider["name"] == name],
                     attempt_count=len(selected), attempt_ids=[record["attempt_id"] for record in selected],
                     provider_config_source="providers.json",
                     parent_measurement_manifest="../../manifest.json",
                     parent_measurement_manifest_sha256=digest(output / "manifest.json"))
        batch["dataset"]["source"] = "dataset.json"
        batch["runtime_identity_sha256"] = digest(target / "runtime-identity.json")
        batch["runtime_identity_record"] = json.loads((target / "runtime-identity.json").read_text())
        if "environment" in batch:
            batch["environment"].pop("hostname", None)
        write_json(target / "manifest.json", batch)
        write_json(target / "execution.json", {"batches": [batch]})
        write_json(target / "summary.json", summarize(selected))
        print(f"Merge-ready: {target}", flush=True)


def run_child(output: Path, runtime_paths: RuntimePaths, track: str, temporary: Path, dataset_path: Path) -> int:
    # Parent exclusively created the directory and supplies only declared vars.
    # macOS injects this locale hint even into an explicitly empty environment.
    os.environ.pop("__CF_USER_TEXT_ENCODING", None)
    if dict(os.environ) != clean_environment(output, runtime_paths, temporary):
        raise ValueError("Refusing non-isolated environment: " + ",".join(sorted(
            key for key in set(os.environ) | set(clean_environment(output, runtime_paths, temporary))
            if os.environ.get(key) != clean_environment(output, runtime_paths, temporary).get(key))))
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers import build_provider
    from harness.web_fetch.reference_lifecycle import preflight_reference_checks
    from harness.web_fetch.render_evidence import validate_render_reference
    from harness.web_fetch.runner import run_evaluation

    raw = dataset_path.read_bytes()
    dataset = selected_dataset(json.loads(raw), track)
    source_tree = input_tree(dataset_path, 'sources')
    preflight_reference_checks(dataset["cases"], dataset_path)
    case_ids = {case["id"] for case in dataset["cases"]}
    config = resolved_config(json.loads((ROOT / "configs/providers.resources.json").read_text()), output, runtime_paths)
    write_json(output / "dataset.json", dataset)
    write_json(output / "providers.json", config)
    audits = {}
    for case in dataset["cases"]:
        source = source_tree / case["id"]
        shutil.copytree(source, output / "sources" / case["id"])
        saved = output / "sources" / case["id"]
        audit = validate_render_reference(case, (saved / "initial.html.txt").read_bytes(),
                                          (saved / "rendered.txt").read_text())
        if not audit["valid"]:
            raise ValueError(f"Invalid reference {case['id']}: {audit['errors']}")
        if digest(saved / "reference.txt") != audit["reference_sha256"]:
            raise ValueError(f"Reference file mismatch: {case['id']}")
        audits[case["id"]] = audit
    write_json(output / "reference-validation.json", audits)
    paths = [runtime_paths.node, Path(sys.executable), ROOT / "scripts/run_local_dynamic_builtins.py",
             ROOT / "configs/providers.resources.json", ROOT / "harness/web_fetch/runner.py",
             ROOT / "harness/web_fetch/reference_lifecycle.py",
             ROOT / "harness/web_fetch/metrics.py", ROOT / "harness/web_fetch/providers/cowagent.py",
             ROOT / "harness/web_fetch/providers/subprocess_jsonl.py",
             ROOT / "scripts/oss/openclaw_webfetch_worker.mts",
             ROOT / "scripts/oss/dsh_webfetch_worker.mts", ROOT / "scripts/oss/dsh_output.mjs",
             ROOT / "scripts/oss/public_dns.mjs", ROOT / "harness/web_fetch/network_dns.py",
             runtime_paths.deepseek / "node_modules/.pnpm/.browser-harness-dsh-worker.mts",
             runtime_paths.cowagent / "agent/tools/web_fetch/web_fetch.py", runtime_paths.cowagent / "agent/tools/utils/url_safety.py",
             runtime_paths.openclaw / "src/agents/tools/web-fetch.ts", runtime_paths.openclaw / "pnpm-lock.yaml",
             runtime_paths.deepseek / "packages/web/web-fetch-http/src/provider.ts", runtime_paths.deepseek / "pnpm-lock.yaml"]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        write_json(output / "blocked.json", {"missing_dependencies": missing})
        return 1
    if (runtime_paths.deepseek / "node_modules/.pnpm/.browser-harness-dsh-worker.mts").read_bytes() != (
            ROOT / "scripts/oss/dsh_webfetch_worker.mts").read_bytes():
        raise ValueError("Installed DeepSeek worker differs from current harness worker")
    identity = {
        "source_dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "frozen_subset_sha256": digest(output / "dataset.json"),
        "provider_config_sha256": digest(output / "providers.json"),
        "python": sys.version, "python_executable": sys.executable,
        "python_packages": {name: version(name) for name in ("requests", "beautifulsoup4")},
        "node_version": subprocess.check_output([str(runtime_paths.node), "--version"], text=True).strip(),
        "file_sha256": {str(path): digest(path) for path in paths},
        "repositories": {str(repo): {
            "head": subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
            "tracked_status": subprocess.check_output(
                ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"], text=True),
        } for repo in (runtime_paths.cowagent, runtime_paths.openclaw, runtime_paths.deepseek)},
        "environment": clean_environment(output, runtime_paths, temporary), "dotenv_loaded": False,
        "inherited_credentials": False,
        "timeout_note": "Harness 45s; unchanged CowAgent and OpenClaw internal HTTP defaults 30s; subprocess adapter has 5s shutdown allowance.",
        "sources_sha256": {str(path.relative_to(output)): digest(path)
                           for path in (output / "sources").rglob("*") if path.is_file()},
    }
    write_json(output / "runtime-identity.private.json", identity)
    write_json(output / "runtime-identity.json", publishable_identity(identity, runtime_paths))
    preflight = {}
    commands = {name: (entry["command"], entry["repo_path"])
                for name, entry in config["providers"].items() if "command" in entry}
    commands[NAMES[0]] = ([sys.executable, "-B", "-c",
        f"import sys; sys.path.insert(0, {str(runtime_paths.cowagent)!r}); import agent.tools.web_fetch.web_fetch; print('CowAgent import OK')"], str(output))
    for name, (command, cwd) in commands.items():
        try:
            check = subprocess.run(command, cwd=cwd, input="", text=True, capture_output=True, timeout=45)
            preflight[name] = {"returncode": check.returncode, "stdout": check.stdout, "stderr": check.stderr}
        except subprocess.TimeoutExpired:
            preflight[name] = {"returncode": None, "error_type": "startup_timeout"}
        print(f"preflight {name}: {preflight[name]['returncode']}", flush=True)
    write_json(output / "preflight.json", preflight)
    if any(row["returncode"] != 0 for row in preflight.values()):
        write_json(output / "blocked.json", {"dependency_preflight_failed": [
            name for name, row in preflight.items() if row["returncode"] != 0]})
        return 1
    meta, cases = load_dataset(output / "dataset.json")
    providers = [build_provider(name, config["providers"][name]) for name in NAMES]
    records, _ = run_evaluation(output_dir=output, dataset_meta=meta,
        dataset_path=output / "dataset.json", cases=cases, providers=providers,
        provider_config_path=output / "providers.json", runs=1, timeout=45, delay=.5,
        provider_concurrency=3)
    if len(records) != len(NAMES) * len(case_ids) or {(r["provider"], r["case_id"]) for r in records} != {
            (name, identifier) for name in NAMES for identifier in case_ids}:
        raise ValueError("Incomplete attempt matrix")
    for record in records:
        if digest(output / record["artifact_path"]) != record["metrics"]["content_sha256"]:
            raise ValueError("Artifact hash mismatch")
    manifest = json.loads((output / "manifest.json").read_text())
    manifest.update(runtime_identity="runtime-identity.json",
                    runtime_identity_sha256=digest(output / "runtime-identity.json"),
                    dataset_sha256=digest(output / "dataset.json"),
                    reference_validation="reference-validation.json")
    write_json(output / "manifest.json", manifest)
    write_json(output / "execution.json", {"batches": [manifest]})
    package_providers(output, records, manifest)
    print(f"Complete: {len(records)} attempts in {output}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json",
                        help="Frozen dataset JSON; paired sources are resolved beside it")
    parser.add_argument("--track", default="dynamic", help="Dataset track with paired render evidence")
    parser.add_argument("--cowagent-repo", type=Path, required=True)
    parser.add_argument("--openclaw-repo", type=Path, required=True)
    parser.add_argument("--deepseek-repo", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--isolated-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-tmp", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    dataset_path = args.dataset.resolve()
    runtime_paths = RuntimePaths(args.cowagent_repo.resolve(), args.openclaw_repo.resolve(),
                                 args.deepseek_repo.resolve(), args.node.resolve())
    run_root=(data_home()/"runs").resolve()
    if not output.is_relative_to(run_root) or output == run_root:
        raise ValueError(f"Output must be a fresh subdirectory of {run_root}")
    if args.isolated_child:
        if args.runtime_tmp is None:
            raise ValueError("Isolated child requires its allocated temporary directory")
        return run_child(output, runtime_paths, args.track, args.runtime_tmp, dataset_path)
    from harness.web_fetch.reference_lifecycle import preflight_reference_checks
    selected = selected_dataset(json.loads(dataset_path.read_bytes()), args.track)
    preflight_reference_checks(selected["cases"], dataset_path)
    output.mkdir(parents=True, exist_ok=False)
    for folder in ("openclaw-home", "openclaw-state", "cowagent-data", "cowagent-workspace"):
        (output / "runtime" / folder).mkdir(parents=True)
    # macOS Unix-domain socket paths have a small limit; tsx appends its socket
    # name to TMPDIR. Keep this isolated path short regardless of output length.
    with tempfile.TemporaryDirectory(prefix="wfd-", dir="/tmp") as allocated:
        temporary = Path(allocated)
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                 "--output", str(output), "--isolated-child", "--track", args.track,
                                 "--dataset", str(dataset_path),
                                 "--runtime-tmp", str(temporary),
                                 "--cowagent-repo", str(runtime_paths.cowagent),
                                 "--openclaw-repo", str(runtime_paths.openclaw),
                                 "--deepseek-repo", str(runtime_paths.deepseek), "--node", str(runtime_paths.node)],
                                cwd=ROOT, env=clean_environment(output, runtime_paths, temporary))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
