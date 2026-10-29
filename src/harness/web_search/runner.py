"""Serial matched-query collection with frozen inputs and append-only calls."""

from harness.distribution import source_path

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from harness.record_io import read_completed_records
from harness.web_fetch.config import ROOT, load_runtime_env
from harness.web_search.adapters import adapter_for
from harness.web_search.core import (
    digest,
    normalize_response,
    request_body,
    request_cost,
    validate_profile,
    verify_bundle,
    write_json,
)
from harness.web_search.profiles import load_profile_bytes
from lexbench_websearch import inputs as benchmark_inputs
from lexbench_websearch.scoring import evaluate


def call_budgets(profile):
    declared = profile["protocol"].get("max_calls_per_method")
    if declared is None:
        return {}
    ids = {m["id"] for m in profile["methods"]}
    budgets = {mid: declared for mid in ids} if type(declared) is int else declared
    planned = len(profile["cases"]) * profile["protocol"]["rounds"] * (1 + int(bool(profile["protocol"].get("retry"))))
    if not isinstance(budgets, dict) or set(budgets) != ids or any(type(v) is not int or v < planned for v in budgets.values()):
        raise ValueError("Declared call budget cannot cover the complete matrix and retries")
    return budgets


def physical_call_counts(profile, output):
    """Count exact declared call identities across current and archived evidence."""
    identities = {}
    for round_id, case, method in execution_schedule(profile):
        identity = f"{case['id']}-{method['id']}-r{round_id}"
        if identity in identities:
            raise ValueError("Ambiguous physical call identity: " + identity)
        identities[identity] = method["id"]
    counts = {m["id"]: 0 for m in profile["methods"]}
    contract_path = output / "run-contract.json"
    contract = json.loads(contract_path.read_bytes()) if contract_path.is_file() else {}
    if contract.get("physical_call_accounting") == "write-ahead-reservations/1":
        # Each send has exactly one reservation. Its atomic relocation cannot
        # create a second send, even while the response remains elsewhere.
        calls = set(output.rglob("reservations/*.json"))
    else:
        # Preserve the interpretation of already frozen historical collections.
        calls = {path for path in output.rglob("calls/*.json")}
        calls.update(path.parent.parent / "calls" / path.name for path in output.rglob("reservations/*.json"))
    for path in calls:
        identity, separator, attempt = path.stem.rpartition("-attempt-")
        if not separator or not attempt.isdigit() or int(attempt) < 1 or identity not in identities:
            raise ValueError("Unknown physical call identity: " + path.name)
        counts[identities[identity]] += 1
    return counts


def reserve_attempt(output, relative, payload):
    """Durably record a possibly billable send before entering the transport."""
    path = output / "reservations" / Path(relative).name
    path.parent.mkdir(exist_ok=True)
    reservation = {
        "response_path": relative,
        "request_sha256": digest(json.dumps(payload, sort_keys=True).encode()),
        "reserved_at": datetime.now(UTC).isoformat(),
    }
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(reservation) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Persist the directory entry as well as the file before a network send.
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def archive_reservations(output, archive, kept):
    """Keep interrupted/unknown sends beside displaced response evidence."""
    for path in (output / "reservations").glob("*.json"):
        if "calls/" + path.name not in kept:
            target = archive / "reservations" / path.name
            target.parent.mkdir(exist_ok=True)
            path.rename(target)


def freeze_implementation(output):
    sources = collection_implementation()
    for relative in sources:
        target = output / "implementation" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(implementation_source(relative).read_bytes())
    return sources


def prepare_execution_revision(profile_path, output, revision_path, *, cohort="primary"):
    """Replace a declared method scope while retaining every unaffected paid call."""
    new_bytes = load_profile_bytes(profile_path, cohort=cohort)
    new = json.loads(new_bytes)
    old_bytes = (output / "profile.json").read_bytes()
    old = json.loads(old_bytes)
    revision_bytes = revision_path.read_bytes()
    revision = json.loads(revision_bytes)
    methods = set(revision.get("superseded_methods", []))
    if (output / "receipt.json").exists() or revision.get("schema") != "web-search-execution-revision/1":
        raise ValueError("Execution revision requires an interrupted collection")
    if not revision.get("root_cause") or not methods or not revision.get("evidence"):
        raise ValueError("Execution revision requires a cause, method scope and evidence")
    if revision.get("previous_profile_sha256") != digest(old_bytes) or revision.get("profile_sha256") != digest(new_bytes):
        raise ValueError("Execution revision profile binding mismatch")
    verify_bundle(revision_path.parent, revision["evidence"])
    old_methods = {m["id"]: m for m in old["methods"]}
    new_methods = {m["id"]: m for m in new["methods"]}
    if list(old_methods) != list(new_methods) or not methods.issubset(old_methods):
        raise ValueError("Execution revision cannot change the participant set")
    if {mid for mid in old_methods if old_methods[mid] != new_methods[mid]} != methods:
        raise ValueError("Execution revision must supersede every changed method")
    for mid in methods:
        if {k: v for k, v in old_methods[mid].items() if k not in {"endpoint", "output_options"}} != {k: v for k, v in new_methods[mid].items() if k not in {"endpoint", "output_options"}}:
            raise ValueError("Execution revision can change only native request configuration")
    old_selection, new_selection = old.get("current_selection"), new.get("current_selection")
    if bool(old_selection) != bool(new_selection) or old_selection and any(
        old_selection[k] != new_selection[k] for k in ("methods", "cohort")
    ):
        raise ValueError("Execution revision cannot change selection scope")
    budget_keys = {"max_calls_per_method", "budget"}
    if ({k: v for k, v in old.items() if k not in {"methods", "protocol", "current_selection"}} != {k: v for k, v in new.items() if k not in {"methods", "protocol", "current_selection"}}
            or {k: v for k, v in old["protocol"].items() if k not in budget_keys} != {k: v for k, v in new["protocol"].items() if k not in budget_keys}):
        raise ValueError("Execution revision cannot change tasks or measurement controls")
    contract_bytes = (output / "run-contract.json").read_bytes()
    contract = json.loads(contract_bytes)
    if contract["profile_sha256"] != digest(old_bytes):
        raise ValueError("Original execution contract changed")
    verify_bundle(output / "implementation", [
        {"path": name, "sha256": sha} for name, sha in contract["implementation"].items()
    ])
    selected_datasets = validate_resume_datasets(contract, output)
    raw = (output / "results.jsonl").read_bytes()
    rows = read_completed_records(output / "results.jsonl")
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate interrupted result identity")
    cases = {c["id"]: c for c in old["cases"]}
    for row in rows:
        method = old_methods[row["method"]]
        if row["request"] != request_body(method["adapter"], cases[row["case_id"]], old["protocol"], method.get("output_options")):
            raise ValueError("Interrupted request differs from the frozen contract")
        verify_bundle(output, [a["evidence"] for a in row.get("attempts", [row])])
    retained = [r for r in rows if r["method"] not in methods]
    spent_calls = physical_call_counts(new, output)
    for mid, budget in call_budgets(new).items():
        spent = spent_calls[mid]
        pending = len(new["cases"]) * new["protocol"]["rounds"] - sum(r["method"] == mid for r in retained)
        if spent + pending * (1 + int(bool(new["protocol"].get("retry")))) > budget:
            raise ValueError("Execution revision exceeds the physical call budget for " + mid)
    archive = output / f"resume-{len(list(output.glob('resume-*'))) + 1}"
    archive.mkdir()
    (archive / "previous-profile.json").write_bytes(old_bytes)
    (archive / "previous-run-contract.json").write_bytes(contract_bytes)
    (archive / "previous-results.jsonl").write_bytes(raw)
    (archive / "execution-revision.json").write_bytes(revision_bytes)
    kept = {a["evidence"]["path"] for r in retained for a in r.get("attempts", [r])}
    archive_reservations(output, archive, kept)
    locations = {}
    for call in (output / "calls").glob("*.json"):
        original = call.relative_to(output).as_posix()
        if original not in kept:
            target = archive / "calls" / call.name
            target.parent.mkdir(exist_ok=True)
            shutil.move(call, target)
            locations[original] = target.relative_to(output).as_posix()
        else:
            locations[original] = original
    for item in revision["evidence"]:
        target = archive / "revision-evidence" / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((revision_path.parent / item["path"]).read_bytes())
    shutil.move(output / "implementation", archive / "implementation")
    sources = freeze_implementation(output)
    write_json(archive / "contract.json", {"schema": "web-search-execution-revision-receipt/1", "previous_profile_sha256": digest(old_bytes), "profile_sha256": digest(new_bytes), "previous_results_sha256": digest(raw), "retained_result_ids": [r["id"] for r in retained], "superseded_result_ids": [r["id"] for r in rows if r["method"] in methods], "artifact_locations": locations, "resumed_at": datetime.now(UTC).isoformat()})
    (output / "profile.json").write_bytes(new_bytes)
    (output / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in retained))
    write_json(output / "run-contract.json", {**contract, "profile_sha256": digest(new_bytes), "implementation": sources, "installed_benchmark_datasets": selected_datasets, "resume_unit": "logical", "execution_revision": {"path": (archive / "execution-revision.json").relative_to(output).as_posix(), "sha256": digest(revision_bytes)}})
    return retained


def http_call(url, body=None, header=None, timeout=20):
    """Hard total timeout; credentials enter curl on stdin and never appear in argv."""
    with tempfile.TemporaryDirectory(prefix="search-http-") as temp:
        output = Path(temp) / "response"
        config = "url = " + json.dumps(url) + "\n"
        if header:
            if "\n" in header or "\r" in header:
                raise ValueError("Invalid credential header")
            config += "header = " + json.dumps(header) + "\n"
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--config",
            "-",
            "--max-time",
            str(timeout),
            "--output",
            str(output),
            "--write-out",
            "%{http_code} %{time_starttransfer} %{time_total} "
            "%{time_namelookup} %{time_connect} %{time_appconnect} %{size_download}",
        ]
        if body is not None:
            request = Path(temp) / "request.json"
            write_json(request, body)
            command += [
                "--header",
                "Content-Type: application/json",
                "--data-binary",
                "@" + str(request),
            ]
        else:
            command += ["--location", "--max-redirs", "5"]
        start = time.perf_counter()
        try:
            call = subprocess.run(
                command, input=config, text=True, capture_output=True, timeout=timeout + 5
            )
        except subprocess.TimeoutExpired:
            return {
                "http_status": 0,
                "elapsed_ms": (time.perf_counter() - start) * 1000,
                "ttfb_ms": None,
                "transport_exit": 28,
                "transport_error": "Client subprocess exceeded total timeout",
                "network": None,
            }, output.read_bytes() if output.exists() else b""
        elapsed = (time.perf_counter() - start) * 1000
        fields = call.stdout.split()
        raw = output.read_bytes() if output.exists() else b""
        return dict(
            http_status=int(fields[0]) if fields else 0,
            elapsed_ms=elapsed,
            ttfb_ms=float(fields[1]) * 1000 if len(fields) > 1 else None,
            transport_exit=call.returncode,
            transport_error=call.stderr.strip(),
            network={
                "dns_ms": float(fields[3]) * 1000,
                "connect_ms": float(fields[4]) * 1000,
                "tls_ms": float(fields[5]) * 1000,
                "download_bytes": int(fields[6]),
                "body_ms": (float(fields[2]) - float(fields[1])) * 1000,
            }
            if len(fields) > 6
            else None,
        ), raw


def capture_references(profile, output, phase):
    refs = {}
    urls = dict.fromkeys(u for c in profile["cases"] for u in c.get("anchors", []))
    urls.update(dict.fromkeys(profile.get("reference_artifacts", {})))
    for url in urls:
        check = profile.get("reference_checks", {}).get(url, {})
        receipt, raw = http_call(
            check.get("url", url), timeout=profile["protocol"].get("reference_timeout_s", 60)
        )
        relative = f"references/{phase}-{digest(url.encode())[:16]}.bin"
        path = output / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        refs[url] = {
            **receipt,
            "path": relative,
            "sha256": digest(raw),
            "bytes": len(raw),
            "available": reference_available(profile, url, receipt, raw),
            "identity_check": check or None,
        }
    write_json(output / f"references-{phase}.json", refs)
    return refs


def reference_available(profile, url, receipt, raw):
    check = profile.get("reference_checks", {}).get(url, {})
    return (
        receipt["http_status"] == 200
        and not receipt["transport_exit"]
        and len(raw) > 100
        and (
            url not in profile.get("reference_artifacts", {})
            or digest(raw) == profile["reference_artifacts"][url]["sha256"]
        )
        and all(
            s.casefold() in raw.decode("utf-8", errors="replace").casefold()
            for s in check.get("contains", [])
        )
    )


def collection_implementation():
    """Freeze collection/scoring dependencies, excluding report presentation code."""
    names = (
        "harness/web_search/runner.py", "harness/web_search/core.py",
        "harness/web_search/adapters.py", "harness/web_search/profiles.py",
        "harness/web_fetch/config.py", "harness/record_io.py", "uv.lock", "pyproject.toml",
        "harness/benchmarks/data.py", "harness/distribution.py",
    )
    sources = {name: source_path(name, root=ROOT) for name in names}
    sources.update({"lexbench_websearch/" + name: path for name, path in benchmark_inputs.implementation_sources().items()})
    return {name: digest(source.read_bytes()) for name, source in sources.items()}


def implementation_source(relative):
    for prefix in ("lexbench_websearch/", "dependencies/lexbench_websearch/"):
        if relative.startswith(prefix):
            return benchmark_inputs.implementation_sources()[relative.removeprefix(prefix)]
    return source_path(relative, root=ROOT)


def validate_resume_implementation(output, contract):
    """Reject code drift before reference requests, file mutations or provider calls."""
    verify_bundle(output / "implementation", [
        {"path": name, "sha256": sha} for name, sha in contract["implementation"].items()
    ])
    current = collection_implementation()
    if current != contract["implementation"]:
        raise ValueError("Resume requires the same live collection implementation; start a new cohort")
    validate_resume_datasets(contract, output)


def validate_resume_datasets(contract, output=None):
    """Execution repairs retain the original benchmark inputs and scoring resources."""
    from harness.benchmarks.data import canonical_repository, dependencies, legacy_dependencies
    current = dependencies("websearch")
    if "installed_benchmark_datasets" in contract:
        def normalized(selection):
            return ({name: {**item, "repository": canonical_repository(item.get("repository"))}
                     if isinstance(item, dict) else item for name, item in selection.items()}
                    if isinstance(selection, dict) else selection)
        if normalized(contract["installed_benchmark_datasets"]) != normalized(current):
            raise ValueError("Resume requires the same installed benchmark datasets; "
                             "start a new cohort")
        return current
    required = {"pyproject.toml", "uv.lock", "dependencies/lexbench_websearch/inputs.py",
                "dependencies/lexbench_websearch/scoring.py"}
    implementation = contract.get("implementation", {})
    if output is None or not required.issubset(implementation) or any(
        name.startswith("lexbench_websearch/") for name in implementation
    ):
        raise ValueError("Resume requires the same installed benchmark datasets; "
                         "start a new cohort")
    frozen = Path(output) / "implementation"
    verify_bundle(frozen, [{"path": name, "sha256": sha} for name, sha in implementation.items()])
    try:
        project = tomllib.loads((frozen / "pyproject.toml").read_text())
        dependency_pin = re.compile(
            r"lexbench-websearch\s*@\s*git\+https://github\.com/"
            r"lexmount/lexbench-websearch\.git@([0-9a-f]{40})")
        pins = [match[1] for item in project["project"]["dependencies"]
                if (match := dependency_pin.fullmatch(item))]
        packages = [item for item in tomllib.loads((frozen / "uv.lock").read_text())["package"]
                    if item["name"] == "lexbench-websearch"]
        if len(pins) != 1 or len(packages) != 1 or packages[0]["source"]["git"] != (
            f"https://github.com/lexmount/lexbench-websearch.git?rev={pins[0]}#{pins[0]}"
        ):
            raise ValueError("Frozen benchmark dependency pins disagree")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Legacy benchmark dataset requires consistent "
                         "frozen dependency pins") from error
    return legacy_dependencies("websearch", pins[0])


def prepare_resume(profile_path, output, *, retain_results=False, cohort="primary", validate_only=False):
    """Resume only an unchanged, interrupted matrix; preserve every displaced byte."""
    profile_bytes = load_profile_bytes(profile_path, cohort=cohort)
    profile = json.loads(profile_bytes)
    required = ("profile.json", "run-contract.json", "results.jsonl")
    if any(not (output / name).is_file() for name in required):
        raise ValueError("Resume requires complete frozen inputs and results.jsonl")
    if (output / "profile.json").read_bytes() != profile_bytes:
        raise ValueError("Resume requires the exact frozen profile")
    if (output / "receipt.json").exists():
        raise ValueError("A completed collection cannot be resumed")
    contract = json.loads((output / "run-contract.json").read_text())
    if contract["profile_sha256"] != digest(profile_bytes):
        raise ValueError("Frozen profile binding changed")
    validate_resume_implementation(output, contract)
    raw = (output / "results.jsonl").read_bytes()
    rows = read_completed_records(output / "results.jsonl")
    expected = {(m["id"], r) for m in profile["methods"] for r in range(profile["protocol"]["rounds"])}
    by_case = {c["id"]: [] for c in profile["cases"]}
    for row in rows:
        if row["case_id"] not in by_case:
            raise ValueError("Unknown case in interrupted results")
        by_case[row["case_id"]].append(row)
        for attempt in row.get("attempts", [row]):
            item = attempt["evidence"]
            if digest((output / item["path"]).read_bytes()) != item["sha256"]:
                raise ValueError("Interrupted call evidence changed")
    completed = {
        cid for cid, group in by_case.items()
        if len(group) == len(expected)
        and {(r["method"], r["round"]) for r in group} == expected
        and not any(r["http_status"] in {401, 402} for r in group)
    }
    retained = [row for row in rows if (row["http_status"] not in {401, 402} if retain_results else row["case_id"] in completed)]
    if validate_only:
        return retained, None
    number = len(list(output.glob("resume-*"))) + 1
    archive = output / f"resume-{number}"
    archive.mkdir()
    (archive / "previous-results.jsonl").write_bytes(raw)
    kept_paths = {a["evidence"]["path"] for r in retained for a in r.get("attempts", [r])}
    archive_reservations(output, archive, kept_paths)
    for call in (output / "calls").glob("*.json"):
        if call.relative_to(output).as_posix() not in kept_paths:
            target = archive / "calls" / call.name
            target.parent.mkdir(exist_ok=True)
            shutil.move(call, target)
    source_hashes = {}
    for relative in collection_implementation():
        source = implementation_source(relative)
        target = archive / "implementation" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        source_hashes[str(relative)] = digest(source.read_bytes())
    write_json(archive / "contract.json", {
        "profile_sha256": digest(profile_bytes),
        "previous_results_sha256": digest(raw),
        "retained_case_ids": sorted(completed),
        "retained_result_ids": [r["id"] for r in retained],
        "recollect_case_ids": sorted(set(by_case) - completed),
        "reason": "Interrupted or account-failed cases recollected for every method; complete unaffected cases retained",
        "implementation": source_hashes,
        "resumed_at": datetime.now(UTC).isoformat(),
    })
    (output / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in retained))
    return retained, number


def execution_schedule(profile):
    """Finish each provider's scope; reproduce explicit historical rotation only."""
    methods, cases, protocol = profile["methods"], profile["cases"], profile["protocol"]
    if protocol.get("order", "complete provider batches") == "complete provider batches":
        for method in methods:
            for round_id in range(protocol["rounds"]):
                for case in cases:
                    yield round_id, case, method
    else:
        for round_id in range(protocol["rounds"]):
            for case_index, case in enumerate(cases):
                offset = (case_index + round_id) % len(methods)
                for method in methods[offset:] + methods[:offset]:
                    yield round_id, case, method


def run(profile_path, output, diagnostic=False, resume=False, execution_revision=None, *, certified=False, cohort="primary"):
    diagnostic = diagnostic or not certified
    profile_bytes = load_profile_bytes(profile_path, cohort=cohort)
    profile = json.loads(profile_bytes)
    validate_profile(profile)
    budgets = call_budgets(profile)
    if execution_revision and not resume:
        raise ValueError("Execution revision requires --resume")
    load_runtime_env()
    missing = [m["key_env"] for m in profile["methods"] if not os.getenv(m["key_env"])]
    if missing:
        raise ValueError("Configure repository .env: " + ", ".join(missing))
    output = output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Measured evidence must live outside Git")
    rows = []
    if resume:
        if not output.is_dir():
            raise ValueError("Resume directory does not exist")
        if not execution_revision:
            prepare_resume(profile_path, output, cohort=cohort, validate_only=True)
        if not execution_revision and (output / "profile.json").read_bytes() != profile_bytes:
            raise ValueError("Resume requires the exact frozen profile")
        original_contract = json.loads((output / "run-contract.json").read_text())
        if original_contract["diagnostic"] != diagnostic:
            raise ValueError("Resume cannot change certification mode")
        validate_resume_implementation(output, original_contract)
        # Validate current sources before changing saved evidence.
        if not (output / "references-before.json").is_file():
            raise ValueError("Resume requires frozen references-before.json")
        before = json.loads((output / "references-before.json").read_text())
        phase = "resume-precheck-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        current = capture_references(profile, output, phase)
        if reference_changes(profile, before, current):
            raise ValueError("Reference recheck failed; resume made no provider calls")
        if execution_revision:
            rows = prepare_execution_revision(profile_path, output, execution_revision, cohort=cohort)
        else:
            rows, _ = prepare_resume(profile_path, output, retain_results=original_contract.get("resume_unit") == "logical", cohort=cohort)
        unavailable = []
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "profile.json").write_bytes(profile_bytes)
        sources = freeze_implementation(output)
        from harness.benchmarks.data import dependencies
        write_json(
            output / "run-contract.json",
            dict(
                profile_sha256=digest(profile_bytes),
                implementation=sources,
                installed_benchmark_datasets=dependencies("websearch"),
                physical_call_accounting="write-ahead-reservations/1",
                started_at=datetime.now(UTC).isoformat(),
                diagnostic=diagnostic,
                resume_unit=(
                    "logical" if profile["protocol"].get("order", "complete provider batches")
                    == "complete provider batches" else "case"
                ),
                timer=(
                    "Client process wall time including curl startup, network, "
                    "complete body; " + (
                        "one retry after transport, HTTP, or invalid-response failure"
                        if profile["protocol"].get("retry") else "no retries"
                    )
                ),
            ),
        )
        before = capture_references(profile, output, "before")
        unavailable = [url for url, r in before.items() if not r["available"]]
        if unavailable and not diagnostic:
            raise ValueError("Reference precheck failed; no provider calls made")
    (output / "results.jsonl").touch(exist_ok=True)
    protocol = profile["protocol"]
    existing_ids = {r["id"] for r in rows}
    spent_calls = physical_call_counts(profile, output)
    for round_id, case, method in execution_schedule(profile):
        identity = f"{case['id']}-{method['id']}-r{round_id}"
        if identity in existing_ids:
            continue
        payload = request_body(method["adapter"], case, protocol, method.get("output_options"))
        key = os.environ[method["key_env"]]
        header = adapter_for(method["adapter"]).authorization_header + ": " + adapter_for(method["adapter"]).authorization_prefix + key
        attempts = []
        operation_start = time.perf_counter()
        for attempt_index in range(1 + int(bool(protocol.get("retry")))):
            budget = budgets.get(method["id"])
            if budget is not None:
                spent = spent_calls[method["id"]]
                if spent >= budget:
                    raise ValueError("Physical call budget exhausted for " + method["id"])
            relative = f"calls/{identity}-attempt-{attempt_index + 1}.json"
            reserve_attempt(output, relative, payload)
            spent_calls[method["id"]] += 1
            receipt, raw = http_call(
                method["endpoint"], payload, header, protocol["client_timeout_s"]
            )
            # Provider error text must never echo a credential into evidence.
            if key.encode() in raw:
                raw = raw.replace(key.encode(), b"[REDACTED]")
            receipt["transport_error"] = receipt["transport_error"].replace(key, "[REDACTED]")
            (output / "calls").mkdir(exist_ok=True)
            (output / relative).write_bytes(raw)
            results = []
            decoded = {}
            error = receipt["transport_error"]
            try:
                decoded = json.loads(raw)
                if receipt["http_status"] == 200 and not receipt["transport_exit"]:
                    results = normalize_response(decoded, method, protocol, payload)
                else:
                    error = error or json.dumps(
                        decoded.get("error", decoded), ensure_ascii=False
                    )
            except (ValueError, AttributeError, TypeError) as exc:
                error = error or str(exc)
            cost = request_cost(method, receipt["http_status"], receipt["transport_exit"], decoded)
            attempts.append({
                "evidence": {"path": relative, "sha256": digest(raw)},
                "http_status": receipt["http_status"],
                "transport_exit": receipt["transport_exit"],
                "elapsed_ms": receipt["elapsed_ms"],
                "cost": cost,
            })
            if not error and receipt["http_status"] == 200 and not receipt["transport_exit"]:
                break
        total_elapsed_ms = max(
            (time.perf_counter() - operation_start) * 1000,
            sum(a["elapsed_ms"] for a in attempts),
        )
        total_cost = (
            sum(a["cost"] for a in attempts)
            if all(a["cost"] is not None for a in attempts)
            else None
        )
        receipt["final_call_elapsed_ms"] = receipt["elapsed_ms"]
        receipt["elapsed_ms"] = total_elapsed_ms
        row = dict(
            id=identity,
            case_id=case["id"],
            method=method["id"],
            round=round_id,
            timestamp=datetime.now(UTC).isoformat(),
            request=payload,
            **receipt,
            error=error,
            results=results,
            cost=total_cost,
            currency=method["currency"],
            evidence={"path": relative, "sha256": digest(raw)},
            attempts=attempts,
            metrics=evaluate(case, results),
            server_timing=adapter_for(method["adapter"]).timing(decoded).get("timing"),
            usage=adapter_for(method["adapter"]).timing(decoded).get("usage"),
            cache=adapter_for(method["adapter"]).timing(decoded).get("cache"),
        )
        rows.append(row)
        with (output / "results.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"{identity}: HTTP {receipt['http_status']}, {len(results)} results, "
            f"{receipt['elapsed_ms']:.0f} ms",
            flush=True,
        )
        if receipt["http_status"] in {401, 402}:
            write_json(output / "interruption.json", {
                "reason": "provider_account",
                "result_id": identity,
                "http_status": receipt["http_status"],
                "completed_logical_results": len(rows),
                "results_sha256": digest((output / "results.jsonl").read_bytes()),
                "stopped_at": datetime.now(UTC).isoformat(),
            })
            raise ValueError(
                f"Provider account error HTTP {receipt['http_status']} at {identity}; "
                "collection stopped with evidence preserved, not certified"
            )
        time.sleep(protocol["interval_s"])
    after = capture_references(profile, output, "after")
    changed = reference_changes(profile, before, after)
    write_json(
        output / "receipt.json",
        dict(
            complete=True,
            call_count=len(rows),
            physical_call_count=sum(len(r["attempts"]) for r in rows),
            total_retained_physical_call_count=sum(physical_call_counts(profile, output).values()),
            certified=not diagnostic and not unavailable and not changed,
            unavailable_references=unavailable,
            changed_references=changed,
            ended_at=datetime.now(UTC).isoformat(),
            results_sha256=digest((output / "results.jsonl").read_bytes()),
            profile_sha256=digest(profile_bytes),
            bindings=[
                {"path": p.relative_to(output).as_posix(), "sha256": digest(p.read_bytes())}
                for p in [
                    output / "run-contract.json",
                    output / "references-before.json",
                    output / "references-after.json",
                    *sorted((output / "references").glob("*.bin")),
                    *sorted((output / "reservations").glob("*.json")),
                    *sorted(p for p in (output / "implementation").rglob("*") if p.is_file()),
                    *sorted(output.glob("references-resume-precheck-*.json")),
                    *sorted(p for directory in output.glob("resume-*") for p in directory.rglob("*") if p.is_file()),
                ]
            ],
        ),
    )
    (output / "interruption.json").unlink(missing_ok=True)
    return output


def reference_changes(profile, before, after):
    """Identity-scoped references allow volatile page chrome, never a missing identity."""
    identity = profile["protocol"].get("reference_scope") == "identity"
    return [
        u
        for u in before
        if not before[u]["available"]
        or not after[u]["available"]
        or (not identity and before[u]["sha256"] != after[u]["sha256"])
    ]


def main(argv=None, *, certified=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cohort", default="primary", help="Cohort ID in the current selection registry")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--execution-revision", type=Path, help="Bound native-interface correction; supersede only declared methods")
    args = parser.parse_args(argv)
    if certified and args.diagnostic:
        parser.error("Certified collection cannot use --diagnostic")
    try:
        print(run(args.profile, args.output, args.diagnostic, args.resume, args.execution_revision, certified=certified, cohort=args.cohort))
    except (ValueError, FileExistsError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
