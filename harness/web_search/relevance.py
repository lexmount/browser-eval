
from .messages import report_text
from pathlib import Path
"""Blind, pooled top-k preview relevance scoring for Web Search results."""

from lexbench_websearch.rubric import JUDGE_PROMPT
from lexbench_websearch.relevance import candidate_packet, score


import hashlib
import json

from lexbench_websearch.scoring import candidate_identity


def verify_provenance(profile, rows, evidence, *, authority=None):
    """Make public candidate packets and rank mappings agree with measured outputs."""
    judge = evidence.get("judge", {})
    if judge.get("kind") == "model":
        frozen, _ = candidate_packet(profile, rows, evidence.get("source_requirements"), source_policy=evidence.get("source_policy", "legacy-domain-intersection/1"))
        if evidence["blind_candidate_packets"] != frozen["blind_candidate_packets"]:
            raise ValueError("Model candidate text differs from frozen search outputs")
        validate_model_evidence(evidence, authority=authority)
    elif judge.get("kind") != "human" or not judge.get("name", "").strip():
        raise ValueError("Relevance evaluation requires named human annotations or frozen model evidence")
    else:
        frozen, _ = candidate_packet(profile, rows)
        if evidence["blind_candidate_packets"] != frozen["blind_candidate_packets"]:
            raise ValueError("Human candidate text differs from frozen search outputs")
    plan = evidence["plan"]
    custom = [c["id"] for c in profile["cases"] if "reference_answer" not in c]
    if plan.get("scope") != "all_custom" or plan["case_ids"] != custom:
        raise ValueError("Relevance evaluation must cover every in-house case without sampling")
    packets = evidence["blind_candidate_packets"]
    rankings = evidence["provider_rankings"]
    labels = evidence["labels"]
    if [packet["case_id"] for packet in packets] != plan["case_ids"]:
        raise ValueError("Blind packets do not cover the declared sample")
    indexed = {(row["case_id"], row["method"]): row for row in rows}
    for packet in packets:
        case_id = packet["case_id"]
        ids = {candidate["candidate_id"] for candidate in packet["candidates"]}
        if ids != set(labels[case_id]) or len(ids) != len(packet["candidates"]):
            raise ValueError("Blind packet candidates disagree with grades")
        for candidate in packet["candidates"]:
            expected = candidate_identity(case_id, candidate["url"])
            if candidate["candidate_id"] != expected:
                raise ValueError("Blind packet URL identity mismatch")
        for method in (m["id"] for m in profile["methods"]):
            actual = [candidate_identity(case_id, item["url"])
                      for item in indexed[case_id, method]["results"][:plan["cutoff"]]]
            if rankings[case_id][method] != actual:
                raise ValueError("Published rank mapping disagrees with provider results")
    crosscheck = evidence.get("crosscheck_labels", {})
    if not set(crosscheck).issubset(labels) or any(
        not set(grades).issubset(labels[case_id]) or any(type(v) is not int or v not in range(4) for v in grades.values())
        for case_id, grades in crosscheck.items()
    ):
        raise ValueError("Cross-check grades are invalid")


def import_annotations(profile, rows, submitted):
    """Validate complete frozen human input, then restore private rank mappings."""
    packet, rankings = candidate_packet(profile, rows)
    if submitted.get("packet_sha256") != packet["packet_sha256"]:
        raise ValueError("Annotation packet does not match the current frozen results")
    if set(submitted.get("reviewed_case_ids", [])) != set(packet["plan"]["case_ids"]):
        raise ValueError("Every case requires explicit review, including empty pools")
    evidence = {**packet, "labels": submitted["labels"], "judge": submitted["judge"],
                "provider_rankings": rankings}
    verify_provenance(profile, rows, evidence)
    score(profile, rows, evidence["plan"], evidence["labels"])
    return evidence


def export_annotations(profile, rows, destination):
    """Write a self-contained human annotation form; no evaluator API is called."""
    packet, _ = candidate_packet(profile, rows)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "packet.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2))
    payload = json.dumps(packet, ensure_ascii=False).replace("<", "\\u003c")
    page = ANNOTATION_HTML.replace("__PACKET__", payload).replace("__CUTOFF__", str(profile["protocol"]["top_k"]))
    (destination / "index.html").write_text(page, encoding="utf-8")
    return destination / "index.html"


ANNOTATION_HTML = report_text('text_1935e26a86c17eeb')


def judge_input(packet):
    if not packet.get("source_checked"):
        return packet
    return {"case_id": packet["case_id"], "query": packet["query"], "source_requirements_checked": True,
            "candidates": [{k: c[k] for k in ("candidate_id", "previews", "source_eligible")} for c in packet["candidates"]]}


def parse_judgments(packet, response):
    content = response["choices"][0]["message"]["content"]
    items = json.loads(content)["judgments"]
    candidates = {x["candidate_id"]: x for x in packet["candidates"]}
    if len(items) != len(candidates) or {x["candidate_id"] for x in items} != set(candidates):
        raise ValueError("Judge must return every candidate exactly once")
    for item in items:
        if type(item.get("grade")) is not int or item["grade"] not in range(4) or not item.get("reason", "").strip():
            raise ValueError("Invalid relevance grade or reason")
        candidate = candidates[item["candidate_id"]]
        texts = [candidate["url"]] + [value for preview in candidate["previews"] for value in preview.values()]
        reference = item.get("evidence")
        if isinstance(reference, dict):
            field = reference.get("field")
            if field == "url":
                item["evidence"] = candidate["url"]
            elif field in ("title", "snippet") and type(reference.get("preview_index")) is int and 0 <= reference["preview_index"] < len(candidate["previews"]):
                item["evidence"] = candidate["previews"][reference["preview_index"]][field]
            else:
                raise ValueError("Judge evidence reference does not identify a candidate field")
        empty_negative_field = isinstance(reference, dict) and item["grade"] <= 1 and item.get("evidence") == ""
        item["evidence_empty"] = empty_negative_field
        if (not item.get("evidence") and not empty_negative_field) or not any(item["evidence"] in text for text in texts):
            raise ValueError("Judge evidence is not an exact quote from the candidate")
    for item in items:
        candidate = candidates[item["candidate_id"]]
        item["model_grade"] = item["grade"]
        if candidate.get("source_eligible") is False:
            item.update(grade=0, reason=report_text('text_639d5ca71205c4cb'), evidence=candidate["url"], grade_source="source-rule")
        else:
            item["grade_source"] = "model"
    return items


def _validate_judge_field_references(response):
    judgments = json.loads(response["choices"][0]["message"]["content"])["judgments"]
    for item in judgments:
        reference = item.get("evidence")
        if (not isinstance(reference, dict) or reference.get("field") not in ("title", "snippet")
                or type(reference.get("preview_index")) is not int):
            raise ValueError("New model evidence requires an exact title/snippet field reference")


def _judge_retryable(status, error_type):
    return status == 429 or (status is not None and status >= 500) or (
        status is None and error_type is not None
    )


def report_authority(report):
    path = Path(report) / "assessment-authority.json"
    return json.loads(path.read_bytes()) if path.is_file() else {}


def legacy_evidence_allowed(evidence, kind, registry=None):
    identity = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return identity in (registry or {}).get(kind, [])


def validate_judge_url(value):
    from ipaddress import ip_address
    from urllib.parse import urlsplit
    base = urlsplit(value)
    hostname = base.hostname
    try:
        loopback = hostname == "localhost" or ip_address(hostname or "").is_loopback
    except ValueError:
        loopback = hostname == "localhost"
    if (not hostname or base.username or base.password or base.query or base.fragment
            or not (base.scheme == "https" or base.scheme == "http" and loopback)
            or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError("Judge base URL requires HTTPS without credentials or query/fragment; HTTP is allowed only on loopback")
    try:
        _ = base.port
    except ValueError as error:
        raise ValueError("Judge base URL has an invalid port") from error
    return base


def validate_model_evidence(evidence, *, _depth=0, authority=None):
    if _depth > 8:
        raise ValueError("Judge inheritance chain is too deep")
    judge = evidence["judge"]
    calls = judge.get("calls", [])
    packets = evidence["blind_candidate_packets"]
    if not judge.get("prompt", "").strip() or not judge.get("name") or len(calls) != len(packets):
        raise ValueError("Model judge requires complete frozen prompts and calls")
    configuration = judge.get("configuration", {})
    if configuration and (
        configuration.get("prompt") != judge["prompt"]
        or configuration.get("model") != judge["name"]
        or configuration.get("packet_sha256") != evidence["packet_sha256"]
    ):
        raise ValueError("Frozen judge contract disagrees with the published prompt or inputs")
    if (
        "source_requirements_sha256" in configuration
        and configuration["source_requirements_sha256"]
        != hashlib.sha256(
            json.dumps(evidence["source_requirements"], sort_keys=True).encode()
        ).hexdigest()
    ):
        raise ValueError("Explicit source requirements differ from the frozen judge contract")
    strict_receipts = configuration.get("receipt_schema") in (2, 3)
    if not strict_receipts and not legacy_evidence_allowed(evidence, "model", authority):
        raise ValueError("Model evidence requires strict configuration and physical receipts; legacy evidence must be hash-pinned")
    if judge["prompt"] != JUDGE_PROMPT:
        raise ValueError("Model evidence uses an unapproved grading prompt")
    expected_packet = hashlib.sha256(json.dumps({"schema": evidence["schema"], "plan": evidence["plan"], "blind_candidate_packets": packets, **({"source_policy": evidence["source_policy"]} if "source_policy" in evidence else {})}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if evidence.get("packet_sha256") != expected_packet:
        raise ValueError("Model packet hash differs from frozen inputs")
    if configuration.get("base_url"):
        validate_judge_url(configuration["base_url"])

    inherited = {}
    if configuration.get("receipt_schema") == 3:
        predecessor = judge["predecessor"]
        if hashlib.sha256(json.dumps(predecessor, ensure_ascii=False, sort_keys=True).encode()).hexdigest() != configuration.get("predecessor_sha256"):
            raise ValueError("Inherited judge evidence changed")
        validate_model_evidence(predecessor, _depth=_depth + 1, authority=authority)
        old_configuration = predecessor["judge"].get("configuration", {})
        if any(configuration.get(k) != old_configuration.get(k) for k in ("base_url", "request_headers")):
            raise ValueError("Inherited judge transport configuration changed")
        selected = judge.get("inherited_case_ids", [])
        if len(selected) != len(set(selected)) or not set(selected).issubset(p["case_id"] for p in packets):
            raise ValueError("Invalid inherited judge case inventory")
        inherited = {c["case_id"]: c for c in predecessor["judge"]["calls"] if c["case_id"] in selected}
        if set(inherited) != set(selected):
            raise ValueError("Inherited judge calls are missing")
    strict = strict_receipts
    if any("attempts" in call for call in calls) and not strict:
        raise ValueError("Physical judge attempts require their complete frozen configuration")
    if strict and not all(
        k in configuration
        for k in (
            "max_tokens",
            "temperature",
            "response_format",
            "request_headers",
            "attempt_limit",
        )
    ):
        raise ValueError("Incomplete model judge configuration")
    if strict and configuration["attempt_limit"] != 3:
        raise ValueError("Model judge attempt limit differs from the supported policy")
    for packet, call in zip(packets, calls, strict=True):
        strict = strict_receipts and packet["case_id"] not in inherited
        if packet["case_id"] in inherited and call != inherited[packet["case_id"]]:
            raise ValueError("Inherited judge call differs from its original evidence")
        if call.get("case_id") != packet["case_id"]:
            raise ValueError("Model judge call identity differs from the candidate packet")
        request = call["request"]
        expected = [
            {"role": "system", "content": judge["prompt"]},
            {
                "role": "user",
                "content": json.dumps(judge_input(packet), ensure_ascii=False, sort_keys=True),
            },
        ]
        if (
            request.get("messages") != expected
            or request.get("model") != judge["name"]
            or request.get("temperature") != 0
        ):
            raise ValueError("Model judge request does not match the blind candidate packet")
        for key in ("max_tokens", "extra_params", "response_format"):
            if (strict or key in configuration) and request.get(key) != configuration.get(key):
                raise ValueError("Model judge configuration differs across calls")
        if strict:
            _validate_judge_field_references(call["response"])
            import base64

            attempts = call.get("attempts", [])
            if not attempts or len(attempts) > configuration["attempt_limit"]:
                raise ValueError("Missing or excessive physical judge attempts")
            request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
            for index, attempt in enumerate(attempts, 1):
                if (
                    attempt.get("attempt") != index
                    or attempt.get("request_sha256") != request_hash
                    or not attempt.get("completed")
                    or attempt.get("url")
                    != configuration["base_url"].rstrip("/") + "/chat/completions"
                    or attempt.get("request_headers") != configuration["request_headers"]
                ):
                    raise ValueError("Physical judge attempt identity is invalid")
                try:
                    raw = base64.b64decode(attempt["response_bytes_base64"], validate=True)
                except (KeyError, ValueError) as error:
                    raise ValueError("Physical judge response bytes are invalid") from error
                if hashlib.sha256(raw).hexdigest() != attempt.get("response_sha256"):
                    raise ValueError("Physical judge response hash differs")
                if attempt.get("retryable") != _judge_retryable(
                    attempt.get("status"), attempt.get("error_type")
                ):
                    raise ValueError("Physical judge retry classification differs from HTTP status")
                if index < len(attempts) and not attempt.get("retryable"):
                    raise ValueError("Judge retried a deterministic failure")
            final = attempts[-1]
            if (
                final.get("status") != 200
                or json.loads(base64.b64decode(final["response_bytes_base64"])) != call["response"]
            ):
                raise ValueError("Final judge response differs from preserved bytes")
        actual_model = call["response"].get("model", "").lower()
        if actual_model != judge["name"].split("/")[-1].lower():
            raise ValueError("Judge response reports a different model")
        labels = {x["candidate_id"]: x["grade"] for x in parse_judgments(packet, call["response"])}
        if labels != evidence["labels"][packet["case_id"]]:
            raise ValueError("Published labels disagree with the frozen model response")


def judge_main(argv=None):
    """Explicit model judging, resumable from immutable per-query receipts."""
    import argparse
    import base64
    import os
    import time
    import urllib.error
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pathlib import Path

    from harness.web_fetch.config import load_runtime_env

    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", default="SEARCH_JUDGE_API_KEY")
    parser.add_argument("--reuse-evidence", type=Path,
                        help="Reuse byte-equivalent prior requests; call only changed judge inputs")
    parser.add_argument(
        "--workers",
        type=int,
        choices=range(1, 5),
        default=4,
        help="Maximum concurrent judge requests",
    )
    parser.add_argument(
        "--source-requirements",
        type=Path,
        help="Frozen explicit-source rules and official evidence",
    )
    parser.add_argument(
        "--disable-thinking", action="store_true", help="Pass enable_thinking=false through Bifrost"
    )
    args = parser.parse_args(argv)
    import re
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.token_env):
        raise ValueError("Judge credential variable must be a valid environment variable name")
    if args.token_env not in {"SEARCH_JUDGE_API_KEY", "BIFROST_API_KEY"}:
        raise ValueError("Use a dedicated SEARCH_JUDGE_API_KEY or BIFROST_API_KEY credential")
    validate_judge_url(args.base_url)
    os.environ.pop(args.token_env, None)
    load_runtime_env()
    token = os.environ.get(args.token_env, "")
    if not token.strip() or "\r" in token or "\n" in token:
        raise ValueError(f"Set a nonempty {args.token_env} in the repository .env")
    from dotenv import dotenv_values
    from harness.web_fetch import config as runtime_config
    configured_base = dotenv_values(runtime_config.ROOT / ".env").get("SEARCH_JUDGE_BASE_URL", "")
    if not configured_base or validate_judge_url(configured_base).netloc != validate_judge_url(args.base_url).netloc:
        raise ValueError("Judge endpoint host must match repository SEARCH_JUDGE_BASE_URL")
    from .report import check

    profile, rows = check(args.report, return_data=True, evidence_only=True)
    source_requirements = (
        json.loads(args.source_requirements.read_text()) if args.source_requirements else {}
    )
    packet, rankings = candidate_packet(profile, rows, source_requirements)
    if source_requirements is not None:
        packet["source_requirements"] = source_requirements
    predecessor = json.loads(args.reuse_evidence.read_bytes()) if args.reuse_evidence else None
    if predecessor:
        verify_provenance(profile, rows, predecessor, authority=report_authority(args.report))
    args.output.mkdir(parents=True, exist_ok=True)
    contract = {
        "packet_sha256": packet["packet_sha256"],
        "model": args.model,
        "base_url": args.base_url,
        "prompt": JUDGE_PROMPT,
        "temperature": 0,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
        "request_headers": {},
        "receipt_schema": 2,
        "attempt_limit": 3,
    }
    if source_requirements is not None:
        contract["source_requirements_sha256"] = hashlib.sha256(
            json.dumps(source_requirements, sort_keys=True).encode()
        ).hexdigest()
    if args.disable_thinking:
        contract["extra_params"] = {"enable_thinking": False}
        contract["request_headers"] = {"x-bf-passthrough-extra-params": "true"}
    if predecessor:
        contract.update(receipt_schema=3, predecessor_sha256=hashlib.sha256(
            json.dumps(predecessor, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
        previous_configuration = predecessor["judge"].get("configuration", {})
        if any(contract.get(k) != previous_configuration.get(k) for k in ("base_url", "request_headers")):
            raise ValueError("Inherited judge transport configuration changed")
    inherited_calls = {call["case_id"]: call for call in predecessor["judge"]["calls"]} if predecessor else {}
    reused_case_ids = set()
    binding = args.output / "contract.json"
    if binding.exists() and json.loads(binding.read_text()) != contract:
        raise ValueError("Existing judge run has different inputs or configuration")
    binding.write_text(json.dumps(contract, ensure_ascii=False, indent=2))

    def call_one(p):
        path = args.output / (hashlib.sha256(p["case_id"].encode()).hexdigest()[:16] + ".json")
        request = {
            "model": args.model,
            "temperature": 0,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": JUDGE_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(judge_input(p), ensure_ascii=False, sort_keys=True),
                },
            ],
        }
        if args.disable_thinking:
            request["extra_params"] = {"enable_thinking": False}
        previous = inherited_calls.get(p["case_id"])
        if previous and previous["request"] == request:
            if path.exists() and json.loads(path.read_text()) != previous:
                raise ValueError("Saved inherited judge call changed")
            path.write_text(json.dumps(previous, ensure_ascii=False, indent=2))
            parse_judgments(p, previous["response"])
            reused_case_ids.add(p["case_id"])
            return previous
        if path.exists():
            saved = json.loads(path.read_text())
            if saved["request"] != request:
                raise ValueError("Saved judge request changed")
            # A saved invalid model answer is an inspection task, never permission
            # to spend again or select a different answer on each invocation.
            _validate_judge_field_references(saved["response"])
            parse_judgments(p, saved["response"])
            return saved
        journal = path.with_name(path.stem + "-attempts.json")
        attempts = json.loads(journal.read_text()) if journal.exists() else []
        request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        for index, previous in enumerate(attempts, 1):
            if (
                previous.get("request_sha256") != request_hash
                or previous.get("attempt") != index
                or previous.get("url") != args.base_url.rstrip("/") + "/chat/completions"
                or previous.get("request_headers") != contract["request_headers"]
            ):
                raise ValueError("Saved physical judge request changed")
            if previous.get("completed"):
                if previous.get("retryable") != _judge_retryable(
                    previous.get("status"), previous.get("error_type")
                ):
                    raise ValueError("Saved physical judge retry classification changed")
                body = base64.b64decode(previous["response_bytes_base64"], validate=True)
                if hashlib.sha256(body).hexdigest() != previous.get("response_sha256"):
                    raise ValueError("Saved physical judge response changed")
        if attempts and (not attempts[-1].get("completed") or not attempts[-1].get("retryable")):
            raise ValueError("Saved judge response requires investigation; no automatic new call")
        started = time.monotonic()

        def preserve():
            temporary = journal.with_suffix(".tmp")
            temporary.write_text(json.dumps(attempts, ensure_ascii=False, indent=2))
            temporary.replace(journal)

        for number in range(len(attempts) + 1, contract["attempt_limit"] + 1):
            attempt = {
                "attempt": number,
                "request_sha256": request_hash,
                "started_at_ns": time.time_ns(),
                "completed": False,
                "url": args.base_url.rstrip("/") + "/chat/completions",
                "request_headers": contract["request_headers"],
            }
            attempts.append(attempt)
            preserve()  # Reserve the physical attempt before any network activity.
            req = urllib.request.Request(
                args.base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(request).encode(),
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    **contract.get("request_headers", {}),
                },
            )
            attempt_started = time.monotonic()
            body, status, error = b"", None, None
            try:
                with urllib.request.urlopen(req, timeout=180) as response:
                    status = response.status
                    body = response.read()
            except urllib.error.HTTPError as caught:
                status, error = caught.code, type(caught).__name__
                body = caught.read()
            except (urllib.error.URLError, TimeoutError) as caught:
                error = type(caught).__name__
            retryable = _judge_retryable(status, error)
            attempt.update(
                completed=True,
                status=status,
                error_type=error,
                retryable=retryable,
                elapsed_ms=(time.monotonic() - attempt_started) * 1000,
                response_bytes_base64=base64.b64encode(body).decode("ascii"),
                response_sha256=hashlib.sha256(body).hexdigest(),
            )
            preserve()  # Raw bytes survive malformed JSON and model-output failures.
            if status == 200:
                raw = json.loads(body)
                saved = {
                    "case_id": p["case_id"],
                    "request": request,
                    "response": raw,
                    "elapsed_ms": (time.monotonic() - started) * 1000,
                    "attempts": attempts,
                }
                path.write_text(json.dumps(saved, ensure_ascii=False, indent=2))
                _validate_judge_field_references(raw)
                parse_judgments(p, raw)
                break
            if not retryable or number == contract["attempt_limit"]:
                raise ValueError(
                    "Judge request failed; preserved physical attempts require investigation"
                )
            time.sleep(30 if status in (429, 503) else 2)
        else:
            raise ValueError("Judge physical attempt limit exhausted; no automatic new call")
        print(p["case_id"], "scored", flush=True)
        return saved

    completed, failures = {}, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(call_one, p): p["case_id"] for p in packet["blind_candidate_packets"]
        }
        for future in as_completed(futures):
            cid = futures[future]
            try:
                completed[cid] = future.result()
            except Exception as error:
                failures.append(cid)
                print(cid, type(error).__name__, flush=True)
    if failures:
        raise ValueError("Incomplete judge run; retained completed queries: " + ", ".join(failures))
    calls = [completed[p["case_id"]] for p in packet["blind_candidate_packets"]]
    evidence = {
        **packet,
        "provider_rankings": rankings,
        "labels": {
            p["case_id"]: {
                x["candidate_id"]: x["grade"] for x in parse_judgments(p, call["response"])
            }
            for p, call in zip(packet["blind_candidate_packets"], calls, strict=True)
        },
        "judge": {
            "kind": "model",
            "name": args.model,
            "prompt": JUDGE_PROMPT,
            "configuration": contract,
            "calls": calls,
        },
    }
    if predecessor:
        evidence["judge"].update(predecessor=predecessor, inherited_case_ids=sorted(reused_case_ids))
    verify_provenance(profile, rows, evidence, authority=report_authority(args.report))
    score(profile, rows, evidence["plan"], evidence["labels"])
    (args.output / "relevance.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
