"""Serial matched-query collection with frozen inputs and append-only calls."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from harness.web_fetch.config import ROOT, load_runtime_env
from harness.record_io import read_completed_records

from .adapters import adapter_for
from .core import digest, normalize, request_body, request_cost, validate_profile, write_json
from lexbench_websearch.scoring import evaluate
from .profiles import load_profile_bytes


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
    )
    sources = {name: ROOT / name for name in names}
    from lexbench_websearch.inputs import implementation_sources
    sources.update({"dependencies/lexbench_websearch/" + name: path for name, path in implementation_sources().items()})
    return {name: digest(source.read_bytes()) for name, source in sources.items()}


def implementation_source(relative):
    from lexbench_websearch.inputs import implementation_sources
    return implementation_sources()[relative.removeprefix("dependencies/lexbench_websearch/")] if relative.startswith("dependencies/") else ROOT / relative


def validate_resume_implementation(output, contract):
    """Reject code drift before reference requests, file mutations or provider calls."""
    for name, sha in contract["implementation"].items():
        if digest((output / "implementation" / name).read_bytes()) != sha:
            raise ValueError("Original collection implementation changed")
    current = collection_implementation()
    if current != contract["implementation"]:
        raise ValueError("Resume requires the same live collection implementation; start a new cohort")


def prepare_resume(profile_path, output, *, cohort="primary", validate_only=False):
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
    retained = [row for row in rows if row["case_id"] in completed]
    if validate_only:
        return retained, None
    number = len(list(output.glob("resume-*"))) + 1
    archive = output / f"resume-{number}"
    archive.mkdir()
    (archive / "previous-results.jsonl").write_bytes(raw)
    kept_paths = {a["evidence"]["path"] for r in retained for a in r.get("attempts", [r])}
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
        "recollect_case_ids": sorted(set(by_case) - completed),
        "reason": "Interrupted or account-failed cases recollected for every method; complete unaffected cases retained",
        "implementation": source_hashes,
        "resumed_at": datetime.now(UTC).isoformat(),
    })
    (output / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in retained), encoding="utf-8")
    return retained, number


def run(profile_path, output, diagnostic=False, resume=False, *, certified=False, cohort="primary"):
    diagnostic = diagnostic or not certified
    profile_bytes = load_profile_bytes(profile_path, cohort=cohort)
    profile = json.loads(profile_bytes)
    validate_profile(profile)
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
        prepare_resume(profile_path, output, cohort=cohort, validate_only=True)
        if (output / "profile.json").read_bytes() != profile_bytes:
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
        rows, _ = prepare_resume(profile_path, output, cohort=cohort)
        unavailable = []
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "profile.json").write_bytes(profile_bytes)
        sources = collection_implementation()
        for relative in sources:
            target = output / "implementation" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(implementation_source(relative).read_bytes())
        write_json(
            output / "run-contract.json",
            dict(
                profile_sha256=digest(profile_bytes),
                implementation=sources,
                started_at=datetime.now(UTC).isoformat(),
                diagnostic=diagnostic,
                timer=(
                    "Client process wall time including curl startup, network, "
                    "complete body; one retry after transport, HTTP, or invalid-response failure"
                ),
            ),
        )
        before = capture_references(profile, output, "before")
        unavailable = [url for url, r in before.items() if not r["available"]]
        if unavailable and not diagnostic:
            raise ValueError("Reference precheck failed; no provider calls made")
    protocol = profile["protocol"]
    existing_ids = {r["id"] for r in rows}
    methods = profile["methods"]
    for round_id in range(protocol["rounds"]):
        for case_index, case in enumerate(profile["cases"]):
            offset = (case_index + round_id) % len(methods)
            for method in methods[offset:] + methods[:offset]:
                identity = f"{case['id']}-{method['id']}-r{round_id}"
                if identity in existing_ids:
                    continue
                payload = request_body(method["adapter"], case, protocol, method.get("output_options"))
                key = os.environ[method["key_env"]]
                header = (
                    adapter_for(method["adapter"]).authorization_header + ": " + adapter_for(method["adapter"]).authorization_prefix
                ) + key
                attempts = []
                operation_start = time.perf_counter()
                for attempt_index in range(1 + int(bool(protocol.get("retry")))):
                    receipt, raw = http_call(
                        method["endpoint"], payload, header, protocol["client_timeout_s"]
                    )
                    # Provider error text must never echo a credential into evidence.
                    if key.encode() in raw:
                        raw = raw.replace(key.encode(), b"[REDACTED]")
                    receipt["transport_error"] = receipt["transport_error"].replace(key, "[REDACTED]")
                    relative = f"calls/{identity}-attempt-{attempt_index + 1}.json"
                    (output / "calls").mkdir(exist_ok=True)
                    (output / relative).write_bytes(raw)
                    results = []
                    decoded = {}
                    error = receipt["transport_error"]
                    try:
                        decoded = json.loads(raw)
                        if receipt["http_status"] == 200 and not receipt["transport_exit"]:
                            results = normalize(decoded, method["adapter"])
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
    parser.add_argument("--cohort", default="primary", help="Cohort ID in the current selection registry")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if certified and args.diagnostic:
        parser.error("Certified collection cannot use --diagnostic")
    try:
        print(run(args.profile, args.output, args.diagnostic, args.resume, certified=certified, cohort=args.cohort))
    except (ValueError, FileExistsError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    main()
