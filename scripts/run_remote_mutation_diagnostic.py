"""Run one model-free mutation diagnostic on a receipt-reset remote WebArena slice.

This is not a formal benchmark cohort. It never reuses a historical agent answer
and never resets a site itself; a task-bound external reset receipt is mandatory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from harness.environments.webarena.har import CdpHarRecorder
from harness.environments.webarena.runtime import (
    browser_tool_config,
    login_context,
    prepare_browser_viewport,
)
from harness.trajectory_replay.remote_runtime import (
    file_sha256,
    load_remote_sites,
    rebind_remote_calls,
    remote_benchmark,
    validate_remote_reset_receipt,
)
from harness.trajectory_replay.webarena_plan import replay_site_hosts
from harness.trajectory_replay.webarena_replay import (
    _candidate,
    _clear_browser_state,
    _execute_replay_call,
    _json_sha256,
    select_tasks,
)

NAVIGATION_CONTEXT_LOSS = (
    "Evaluate failed: Page.evaluate: Execution context was destroyed, "
    "most likely because of a navigation"
)
COMPLETION_CANDIDATES = (
    "evaluation/trajectory-replay/webarena-verified/mutation-completion-candidates.json"
)


def _save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _pre_action_readiness(
    candidate_path: Path, task_id: int, source_call_count: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Compile a bounded, read-only AMD readiness check from a frozen rule."""
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    if payload.get("schema") != "webarena_mutation_completion_candidates/1":
        raise ValueError("unknown mutation completion candidate schema")
    rules = [row for row in payload.get("rules", []) if row.get("task_id") == task_id]
    if len(rules) != 1:
        raise ValueError("expected one task-bound mutation completion candidate")
    rule = rules[0]
    if rule.get("action_count") != source_call_count:
        raise ValueError("readiness rule is not bound to canonical action count")
    readiness = rule.get("pre_action_readiness")
    if readiness is None:
        return None, None
    if isinstance(readiness, dict) and readiness.get("kind") == "same_origin_magento_product_type":
        if (
            set(readiness) != {"kind", "before_original_action", "path", "product_id",
                               "expected_type", "timeout_ms"}
            or readiness["before_original_action"] != 1
            or type(readiness["product_id"]) is not int
            or readiness["product_id"] <= 0
            or type(readiness["timeout_ms"]) is not int
            or not 1 <= readiness["timeout_ms"] <= 30000
            or not isinstance(readiness["path"], str)
            or re.fullmatch(r"/admin/catalog/product/edit/id/\d+/", readiness["path"]) is None
            or readiness["path"] != (
                f"/admin/catalog/product/edit/id/{readiness['product_id']}/"
            )
            or not isinstance(readiness["expected_type"], str)
            or re.fullmatch(r"[a-z][a-z0-9_]*", readiness["expected_type"]) is None
        ):
            raise ValueError("invalid Magento product state precondition")
        script = r"""async () => {
          const path = PATH;
          const productId = PRODUCT_ID;
          const expectedType = EXPECTED_TYPE;
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), TIMEOUT);
          try {
            const response = await fetch(path, {method:'GET', credentials:'same-origin',
              redirect:'error', cache:'no-store', signal:controller.signal});
            const finalURL = new URL(response.url);
            if (response.status !== 200 || response.redirected ||
                finalURL.origin !== location.origin || finalURL.pathname !== path)
              throw new Error('fresh_product_edit_get_not_same_origin_200');
            const doc = new DOMParser().parseFromString(await response.text(),'text/html');
            const sources = [];
            for (const node of doc.querySelectorAll('script[type="text/x-magento-init"]')) {
              const init = JSON.parse(node.textContent);
              for (const entry of Object.values(init)) {
                const source = entry?.['Magento_Ui/js/core/app']?.components
                  ?.product_form?.children?.product_form_data_source;
                if (source) sources.push(source);
              }
            }
            if (sources.length !== 1) throw new Error('product_data_source_not_unique');
            const product = sources[0]?.config?.data?.product;
            const actualId = product?.current_product_id;
            const submitURL = new URL(sources[0]?.config?.submit_url, response.url);
            const route = submitURL.pathname.match(
              /^\/admin\/catalog\/product\/save\/id\/(\d+)\/type\/([a-z][a-z0-9_]*)\/store\/\d+\/set\/\d+\/$/);
            const actualType = route?.[2] ?? null;
            if (String(actualId) !== String(productId) || actualType !== expectedType)
              throw new Error('product_identity_or_type_mismatch:' + JSON.stringify({
                actualId:actualId ?? null, actualType:actualType ?? null}));
            if (submitURL.origin !== location.origin ||
                route?.[1] !== String(productId) || submitURL.search)
              throw new Error('product_save_route_not_same_origin_or_identity');
            return {ready:true, productId, productType:actualType,
              responseURL:response.url, sourceCount:sources.length};
          } finally { clearTimeout(timer); }
        }"""
        script = (script.replace("PATH", json.dumps(readiness["path"]))
                        .replace("PRODUCT_ID", str(readiness["product_id"]))
                        .replace("EXPECTED_TYPE", json.dumps(readiness["expected_type"]))
                        .replace("TIMEOUT", str(readiness["timeout_ms"])))
        return readiness, script
    if not isinstance(readiness, dict) or set(readiness) != {
        "kind", "before_original_action", "module", "deferred_method", "timeout_ms"
    }:
        raise ValueError("unsupported pre-action readiness fields")
    index = readiness["before_original_action"]
    timeout = readiness["timeout_ms"]
    module = readiness["module"]
    method = readiness["deferred_method"]
    if (
        readiness["kind"] != "amd_deferred_ready"
        or type(index) is not int or not 1 <= index <= source_call_count
        or type(timeout) is not int or not 1 <= timeout <= 30000
        or not isinstance(module, str)
        or re.fullmatch(r"[A-Za-z0-9_./-]+", module) is None
        or not isinstance(method, str)
        or re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", method) is None
    ):
        raise ValueError("invalid bounded AMD readiness contract")
    script = """async () => {
      const moduleName = MODULE;
      const methodName = METHOD;
      const timeoutMs = TIMEOUT;
      const started = Date.now();
      const before = {defined: !!(window.requirejs &&
        requirejs.defined(moduleName))};
      if (typeof require !== 'function') throw new Error('AMD require is unavailable');
      const withTimeout = promise => Promise.race([promise,
        new Promise((_, reject) => setTimeout(
          () => reject(new Error('AMD initialization readiness timed out')),
          Math.max(0, timeoutMs - (Date.now() - started))))]);
      const component = await withTimeout(new Promise((resolve, reject) =>
        require([moduleName], resolve, reject)));
      if (!component || typeof component[methodName] !== 'function')
        throw new Error('AMD initialization deferred is unavailable');
      const deferred = component[methodName]();
      if (!deferred || typeof deferred.done !== 'function' ||
          typeof deferred.fail !== 'function' || typeof deferred.state !== 'function')
        throw new Error('AMD initialization method did not return a Deferred');
      before.state = deferred.state();
      await withTimeout(new Promise((resolve, reject) =>
        deferred.done(resolve).fail(reject)));
      const after = {state: deferred.state(), defined: requirejs.defined(moduleName)};
      if (after.state !== 'resolved' || !after.defined)
        throw new Error('AMD initialization was not resolved');
      return {ready: true, module: moduleName, method: methodName,
        before, after, wait_ms: Date.now() - started};
    }"""
    script = (script.replace("MODULE", json.dumps(module))
                    .replace("METHOD", json.dumps(method))
                    .replace("TIMEOUT", str(timeout)))
    return readiness, script


def _compiled_completion_observer(
    candidate_path: Path, task_id: int, source_call_count: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Compile the read-only final observer when the rule opts in."""
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    rules = [row for row in payload.get("rules", []) if row.get("task_id") == task_id]
    if payload.get("schema") != "webarena_mutation_completion_candidates/1" or len(rules) != 1:
        raise ValueError("unknown or ambiguous mutation completion candidate")
    rule = rules[0]
    if rule.get("action_count") != source_call_count:
        raise ValueError("completion observer is not bound to canonical action count")
    if "observer_compiler" not in rule:
        return None, None
    observer = rule.get("observer")
    if (
        rule["observer_compiler"] != "same_origin_authenticated_item_html/1"
        or not isinstance(observer, dict)
        or observer.get("kind") != "same_origin_get_html"
        or observer.get("href_origin") != "response_url_origin"
        or observer.get("require_same_origin_sign_out_href") is not True
        or observer.get("require_same_origin_edit_href") is not True
        or observer.get("require_product_name_and_edit_link_in_same_item") is not True
        or observer.get("require_exactly_one_matching_item") is not True
        or type(rule.get("observer_timeout_ms")) is not int
        or not 1 <= rule["observer_timeout_ms"] <= 30000
        or rule.get("expected_completion") != "persisted_wishlist_item_observed"
    ):
        raise ValueError("unsupported compiled completion observer contract")
    for key in ("path", "within", "require_authenticated_sign_out_path",
                "product_name", "edit_href_path_pattern"):
        if not isinstance(observer.get(key), str) or not observer[key]:
            raise ValueError(f"completion observer lacks {key}")
    for key in ("path", "require_authenticated_sign_out_path"):
        if not observer[key].startswith("/") or observer[key].startswith("//"):
            raise ValueError(f"completion observer has invalid {key}")
    script = """(async () => {
      const path = PATH;
      const within = WITHIN;
      const signOutPath = SIGNOUT;
      const productName = PRODUCT;
      const editPath = new RegExp(EDIT);
      const timeoutMs = TIMEOUT;
      const started = Date.now();
      const origin = location.origin;
      const normalized = value => (value || '').replace(/\\s+/g, ' ').trim();
      const sameOriginPath = (href, base) => {
        try { const url = new URL(href, base);
          return url.origin === origin ? url.pathname : null; }
        catch (_) { return null; }
      };
      let last = null;
      while (Date.now() - started <= timeoutMs) {
        try {
          const response = await fetch(path, {method: 'GET', credentials: 'same-origin',
            redirect: 'error', cache: 'no-store'});
          if (response.status !== 200 || response.redirected ||
              new URL(response.url).origin !== origin)
            throw new Error('same-origin GET did not return HTTP 200');
          const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
          const signedIn = [...doc.querySelectorAll('a[href]')].filter(link =>
            normalized(link.textContent) === 'Sign Out' &&
            sameOriginPath(link.getAttribute('href'), response.url) === signOutPath).length === 1;
          const items = [...doc.querySelectorAll(within)];
          const matching = items.filter(item => {
            const links = [...item.querySelectorAll('a[href]')];
            const named = links.some(link => normalized(link.textContent) === productName);
            const edit = links.some(link => normalized(link.textContent) === 'Edit' &&
              editPath.test(sameOriginPath(link.getAttribute('href'), response.url) || ''));
            return named && edit;
          });
          last = {signedIn, itemCount: items.length, matchingItems: matching.length};
          if (signedIn && matching.length === 1)
            return {completion: 'persisted_wishlist_item_observed', ...last,
              elapsedMs: Date.now() - started};
        } catch (error) { last = {error: String(error)}; }
        if (Date.now() - started >= timeoutMs) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      throw new Error('Persisted completion not observed: ' + JSON.stringify(last));
    })()"""
    replacements = {
        "PATH": observer["path"], "WITHIN": observer["within"],
        "SIGNOUT": observer["require_authenticated_sign_out_path"],
        "PRODUCT": observer["product_name"], "EDIT": observer["edit_href_path_pattern"],
        "TIMEOUT": rule["observer_timeout_ms"],
    }
    for placeholder, value in replacements.items():
        script = script.replace(placeholder, json.dumps(value))
    return rule, script


def _verify_compiled_proposal(
    proposal: dict[str, Any], rule: dict[str, Any] | None,
    compiled_script: str | None, candidate_path: Path
) -> None:
    if compiled_script is None:
        return
    if rule is None or (
        proposal["tool_calls"][-1].get("script") != compiled_script
        or proposal.get("expected_completion") != rule["expected_completion"]
        or proposal.get("observer_contract_sha256")
        != hashlib.sha256(compiled_script.encode()).hexdigest()
        or proposal.get("completion_candidates_sha256") != file_sha256(candidate_path)
    ):
        raise ValueError("proposal observer is stale or differs from the completion contract")


def _execute_readiness(
    tool: Any, readiness: dict[str, Any], script: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    index = readiness["before_original_action"]
    arguments = {"action": "evaluate", "script": script}
    started = time.perf_counter()
    try:
        status, result = _execute_replay_call(tool, arguments)
    except Exception as exc:
        status, result = "error", f"{type(exc).__name__}: {exc}"
    row = {
        "index": f"before-{index}",
        "kind": "pre_action_readiness",
        "arguments": arguments,
        "status": status,
        "result": result,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if status != "success":
        row["validation_error"] = "BrowserTool readiness evaluate failed"
        return row, None
    try:
        observed = json.loads(result)
    except json.JSONDecodeError:
        row["validation_error"] = "Readiness result is not JSON"
        return row, None
    if readiness["kind"] == "same_origin_magento_product_type":
        proven = (
            isinstance(observed, dict) and observed.get("ready") is True
            and observed.get("productId") == readiness["product_id"]
            and observed.get("productType") == readiness["expected_type"]
            and observed.get("sourceCount") == 1
        )
        error = "Readiness result did not prove the frozen product identity and type"
    else:
        proven = (
            isinstance(observed, dict) and observed.get("ready") is True
            and observed.get("module") == readiness["module"]
            and observed.get("method") == readiness["deferred_method"]
            and isinstance(observed.get("after"), dict)
            and observed["after"].get("state") == "resolved"
        )
        error = "Readiness result did not prove resolved Deferred"
    if not proven:
        row["validation_error"] = error
        return row, observed if isinstance(observed, dict) else None
    return row, observed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, required=True)
    parser.add_argument("--proposal", type=Path)
    parser.add_argument("--emit-proposal", type=Path)
    parser.add_argument("--check-proposal", action="store_true")
    parser.add_argument("--site-map", type=Path)
    parser.add_argument("--reset-receipt", type=Path)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--cdp")
    parser.add_argument("--cowagent-repo", type=Path)
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.emit_proposal and args.check_proposal:
        parser.error("choose either --emit-proposal or --check-proposal")
    if args.emit_proposal:
        if args.proposal or args.output or args.cdp:
            parser.error("proposal emission cannot also execute a browser diagnostic")
    elif args.check_proposal:
        if not args.proposal or args.output or args.cdp:
            parser.error("proposal check needs --proposal and no browser execution arguments")
    else:
        physical = ("proposal", "site_map", "reset_receipt", "binary", "cdp",
                    "cowagent_repo", "output")
        missing = [name for name in physical if getattr(args, name) is None]
        if missing:
            parser.error(f"physical diagnostic requires: {', '.join(missing)}")
        if urlsplit(args.cdp).hostname not in {"127.0.0.1", "localhost", "::1"}:
            parser.error("a dedicated local CDP endpoint is required")
    manifest = json.loads(args.seeds.read_text(encoding="utf-8"))
    dataset_path = (
        args.source_root / "evaluation/trajectory-replay/webarena-verified/trajectories.json"
    )
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if dataset.get("provenance", {}).get("seed_manifest_sha256") != file_sha256(args.seeds):
        raise ValueError("qualified trajectories are not bound to these frozen seeds")
    task_seed = select_tasks(manifest, [args.task])[0]
    canonical = _candidate(task_seed)
    qualified = [row for row in dataset.get("trajectories", []) if row.get("task_id") == args.task]
    if len(qualified) != 1 or qualified[0].get("tool_calls") != canonical.get("tool_calls"):
        raise ValueError("selected task differs from its qualified trajectory")
    source_calls = canonical["tool_calls"]
    completion_path = args.source_root / COMPLETION_CANDIDATES
    frozen_completion_sha256 = file_sha256(completion_path)
    readiness, readiness_script = _pre_action_readiness(
        completion_path, args.task, len(source_calls)
    )
    observer_rule, compiled_observer = _compiled_completion_observer(
        completion_path, args.task, len(source_calls)
    )
    if file_sha256(completion_path) != frozen_completion_sha256:
        raise ValueError("completion candidates changed during input compilation")
    if args.emit_proposal:
        if compiled_observer is None or observer_rule is None:
            raise ValueError("task has no compiled completion observer")
        proposal = {
            "task_id": args.task,
            "tool_calls": source_calls + [{"action": "evaluate", "script": compiled_observer}],
            "expected_completion": observer_rule["expected_completion"],
            "observer_contract_sha256": hashlib.sha256(compiled_observer.encode()).hexdigest(),
            "completion_candidates_sha256": frozen_completion_sha256,
        }
        path = args.emit_proposal.resolve()
        if path.exists():
            raise ValueError("proposal output exists")
        _save(path, proposal)
        print(json.dumps({"status": "proposal_emitted", "path": str(path),
                          "sha256": file_sha256(path), "physical_calls": 0}))
        return 0
    proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
    proposed_calls = proposal.get("tool_calls")
    if (
        proposal.get("task_id") != args.task
        or not isinstance(proposed_calls, list)
        or proposed_calls[: len(source_calls)] != source_calls
        or len(proposed_calls) != len(source_calls) + 1
        or proposed_calls[-1].get("action") != "evaluate"
        or not isinstance(proposal.get("expected_completion"), str)
    ):
        raise ValueError("proposal must preserve the canonical actions and add one observer")
    _verify_compiled_proposal(proposal, observer_rule, compiled_observer, completion_path)
    if args.check_proposal:
        print(json.dumps({"status": "proposal_valid", "task_id": args.task,
                          "proposal_sha256": file_sha256(args.proposal),
                          "completion_candidates_sha256": file_sha256(completion_path),
                          "pre_action_readiness": readiness is not None,
                          "physical_calls": 0}))
        return 0
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    policy = proposal.get("observer_retry_policy") or {}
    if policy and (
        policy.get("after_frozen_call_count") != len(source_calls)
        or policy.get("retry_only_final_read_only_observer") is not True
        or policy.get("retryable_error_exact") != NAVIGATION_CONTEXT_LOSS
        or not 1 <= policy.get("max_observer_attempts", 0) <= 3
        or policy.get("original_action_retries") != 0
    ):
        raise ValueError("observer retry policy may not repeat a site mutation")
    sites = load_remote_sites(args.site_map)
    benchmark = remote_benchmark(sites)
    task = benchmark.get_task(args.task)
    if not task.is_mutate_task:
        raise ValueError("this diagnostic accepts mutation tasks only")
    reset = validate_remote_reset_receipt(
        args.reset_receipt,
        required_sites=set(task.sites),
        site_map_sha256=file_sha256(args.site_map),
    )
    if reset["task_id"] != args.task:
        raise ValueError("reset receipt belongs to another task")
    evaluators = [item.model_dump(mode="json") for item in task.eval]
    if not any(item.get("evaluator") == "NetworkEventEvaluator" for item in evaluators):
        raise ValueError("mutation task has no official network event evaluator")
    if any(
        item.get("evaluator") not in {"NetworkEventEvaluator", "AgentResponseEvaluator"}
        for item in evaluators
    ):
        raise ValueError("mutation task has an unsupported official evaluator")
    calls = rebind_remote_calls(
        proposed_calls, sites, replay_site_hosts(source_calls)
    )
    starts = list(benchmark.config.render_url(list(task.start_urls), list(task.sites)))
    if str(args.cowagent_repo.resolve()) not in sys.path:
        sys.path.insert(0, str(args.cowagent_repo.resolve()))
    from agent.tools.browser.browser_tool import BrowserTool  # noqa: PLC0415

    tool = BrowserTool(config=browser_tool_config(args.cdp))
    recorder = CdpHarRecorder()
    har = output / "network.har"
    trace: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {
        "schema": "lex_webarena_remote_mutation_diagnostic/1",
        "run_id": uuid.uuid4().hex,
        "task_id": args.task,
        "scope": "diagnostic_only",
        "promotable": False,
        "binary_sha256": file_sha256(args.binary),
        "proposal_sha256": file_sha256(args.proposal),
        "seeds_sha256": file_sha256(args.seeds),
        "dataset_sha256": file_sha256(dataset_path),
        "site_map_sha256": file_sha256(args.site_map),
        "reset_receipt_sha256": file_sha256(args.reset_receipt),
        "source_calls_sha256": _json_sha256(source_calls),
        "source_action_count": len(source_calls),
        "executed_action_count": len(calls),
        "completion_candidates_sha256": frozen_completion_sha256,
        "pre_action_readiness": readiness,
        "pre_action_script_sha256": (
            hashlib.sha256(readiness_script.encode()).hexdigest()
            if readiness_script is not None else None
        ),
        "recorded_agent_response_used": False,
        "status": "running",
    }
    _save(output / "receipt.json", receipt)
    service = None
    started = time.perf_counter()
    try:
        service = tool._get_service()
        _clear_browser_state(service, {_origin(url) for url in sites.values()})
        receipt["viewport"] = prepare_browser_viewport(service)
        receipt["login_retries"] = login_context(service, tuple(task.sites), benchmark, starts)
        service._submit(lambda: recorder.start(service._context))
        for index, arguments in enumerate(calls, 1):
            if readiness is not None and index == readiness["before_original_action"]:
                guard_row, observed_guard = _execute_readiness(
                    tool, readiness, readiness_script
                )
                trace.append(guard_row)
                _save(output / "trace.json", trace)
                if observed_guard is not None:
                    _save(output / "pre-action-readiness.json", observed_guard)
                if guard_row.get("validation_error"):
                    raise RuntimeError(
                        f"pre-action readiness before {index} failed: "
                        f"{guard_row['validation_error']}"
                    )
            action_started = time.perf_counter()
            observer_attempts = []
            if index == len(calls) and policy:
                for number in range(1, policy["max_observer_attempts"] + 1):
                    status, result = _execute_replay_call(tool, arguments)
                    observer_attempts.append(
                        {"attempt": number, "status": status, "result": result}
                    )
                    if status == "success" or result != NAVIGATION_CONTEXT_LOSS:
                        break
                    if number < policy["max_observer_attempts"]:
                        service._submit(
                            lambda: service._page.wait_for_load_state(
                                "domcontentloaded",
                                timeout=policy["navigation_ready_timeout_ms"],
                            )
                        )
            else:
                status, result = _execute_replay_call(tool, arguments)
            trace.append({
                "index": index,
                "arguments": arguments,
                "status": status,
                "result": result,
                "observer_attempts": observer_attempts,
                "duration_ms": round((time.perf_counter() - action_started) * 1000, 3),
            })
            _save(output / "trace.json", trace)
            if status != "success":
                raise RuntimeError(f"replay action {index} failed")
        service._submit(lambda: recorder.stop(service._context, har))
        observed = json.loads(trace[-1]["result"])
        _save(output / "completion-observation.json", observed)
        if observed.get("completion") != proposal["expected_completion"]:
            raise RuntimeError("completion observer did not prove the expected saved state")
        response = {"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None}
        evaluation = benchmark.evaluate_task(
            task_id=args.task, agent_response=response, network_trace=har
        )
        _save(output / "evaluation.json", evaluation.model_dump(mode="json"))
        receipt["official_score"] = evaluation.score
        receipt["official_status"] = str(evaluation.status)
        receipt["status"] = (
            "diagnostic_pass" if evaluation.score == 1 and not recorder.errors
            else "diagnostic_fail"
        )
    except Exception:
        receipt["status"] = "diagnostic_incomplete"
        receipt["error"] = traceback.format_exc()
    finally:
        if service is not None:
            try:
                service._submit(lambda: recorder.stop(service._context, har))
            except Exception:
                pass
            try:
                service._submit(
                    lambda: service._page.screenshot(path=str(output / "last-page.png"))
                )
            except Exception:
                pass
        tool.close()
        receipt["completed_action_count"] = sum(
            row["status"] == "success" and row.get("kind") != "pre_action_readiness"
            for row in trace
        )
        receipt["input_hashes_stable"] = all((
            file_sha256(args.binary) == receipt["binary_sha256"],
            file_sha256(args.proposal) == receipt["proposal_sha256"],
            file_sha256(args.seeds) == receipt["seeds_sha256"],
            file_sha256(dataset_path) == receipt["dataset_sha256"],
            file_sha256(completion_path) == receipt["completion_candidates_sha256"],
            file_sha256(args.site_map) == receipt["site_map_sha256"],
            file_sha256(args.reset_receipt) == receipt["reset_receipt_sha256"],
        ))
        if not receipt["input_hashes_stable"]:
            receipt["status"] = "diagnostic_incomplete"
            receipt["error"] = "Frozen inputs changed during the diagnostic"
        receipt["har_errors"] = recorder.errors
        receipt["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        receipt["evidence"] = {
            path.name: file_sha256(path)
            for path in output.iterdir()
            if path.is_file() and path.name != "receipt.json"
        }
        _save(output / "receipt.json", receipt)
    print(json.dumps({"status": receipt["status"], "output": str(output)}))
    return 0 if receipt["status"] == "diagnostic_pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
