"""Validate the frozen WebArena trajectory-replay run contract."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from harness.environments.browser import configured_browser_images
from harness.environments.webarena.topology import SITE_PORTS
from harness.trajectory_replay import webarena_corrections
from harness.trajectory_replay.browser_use_seeds import (
    _authoritative_rows,
    _contains_ref,
    compile_replay_calls,
    extract_browser_calls,
)
from harness.trajectory_replay.retrieval_acceptance import load_contracts

PROFILE_SCHEMA = "lex_webarena_trajectory_replay_profile/1"
SEED_SCHEMA = "lex_browser_use_replay_seeds/2"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay_site_hosts(calls: list[dict[str, Any]]) -> set[str]:
    site_ports = {port for pair in SITE_PORTS.values() for port in pair}
    return {
        str(parsed.hostname)
        for call in calls
        if isinstance(call.get("url"), str)
        for parsed in [urlsplit(call["url"])]
        if parsed.scheme in {"http", "https"} and parsed.port in site_ports and parsed.hostname
    }


def replay_calls_binding_sha256(calls: list[dict[str, Any]], *, allowed_hosts: set[str]) -> str:
    """Hash calls while ignoring only the deployment-specific WebArena gateway host."""

    site_ports = {port for pair in SITE_PORTS.values() for port in pair}
    normalized = []
    for raw_call in calls:
        call = dict(raw_call)
        url = call.get("url")
        if isinstance(url, str):
            parsed = urlsplit(url)
            if (
                parsed.scheme in {"http", "https"}
                and parsed.port in site_ports
                and parsed.hostname in allowed_hosts
            ):
                call["url"] = urlunsplit(
                    (
                        parsed.scheme,
                        f"__WEBARENA_GATEWAY__:{parsed.port}",
                        parsed.path,
                        parsed.query,
                        parsed.fragment,
                    )
                )
        normalized.append(call)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_canonical_candidate(
    task: dict[str, Any],
    candidate: dict[str, Any],
    repair_sha256: str,
    repair_record: dict[str, Any] | None,
    authoritative_row: dict[str, Any] | None,
) -> None:
    task_id = int(task["task_id"])
    calls = candidate.get("tool_calls")
    if (
        not isinstance(calls, list)
        or not calls
        or int(candidate.get("action_count") or 0) != len(calls)
        or not isinstance(candidate.get("recorded_response"), dict)
    ):
        raise ValueError(f"task {task_id} has an invalid canonical trajectory payload")
    source = candidate.get("source") or {}
    if candidate.get("candidate_kind") == "expert_repair":
        if (
            repair_record is None
            or source.get("kind") != "expert_repair_spec"
            or source.get("repair_spec_sha256") != repair_sha256
            or candidate.get("id")
            != str(repair_record.get("candidate_id") or f"expert__task-{task_id}")
            or calls != repair_record.get("tool_calls")
            or candidate["recorded_response"] != repair_record.get("expected_response")
            or source.get("details") != repair_record.get("source")
            or candidate.get("repair_basis") != repair_record.get("repair_basis")
        ):
            raise ValueError(f"task {task_id} expert trajectory is not bound to the repair spec")
        return

    if candidate.get("official_score") != 1.0 or candidate.get("clean") is not True:
        raise ValueError(f"task {task_id} autonomous trajectory is not a clean official success")
    method = candidate.get("method") or {}
    if (
        authoritative_row is None
        or int(candidate.get("source_execution") or 0) != int(authoritative_row.get("attempt") or 0)
        or str(candidate.get("source_run_id") or "") != str(authoritative_row.get("run_id") or "")
        or method.get("model") != authoritative_row.get("model")
        or method.get("engine") != authoritative_row.get("engine")
        or float(authoritative_row.get("score") or 0) != 1.0
    ):
        raise ValueError(
            f"task {task_id} autonomous trajectory is not the authoritative final execution"
        )
    paths = {
        kind: Path(str(source.get(f"{kind}_path") or "")).expanduser().resolve()
        for kind in ("events", "response", "evaluation")
    }
    for kind, path in paths.items():
        if not path.is_file() or _sha256(path) != source.get(f"{kind}_sha256"):
            raise ValueError(f"task {task_id} autonomous {kind} evidence is missing or changed")
        if (
            path
            != Path(str((authoritative_row.get("paths") or {}).get(kind) or ""))
            .expanduser()
            .resolve()
        ):
            raise ValueError(
                f"task {task_id} autonomous {kind} evidence is not from the final execution"
            )
    source_calls = extract_browser_calls(paths["events"])
    if (
        compile_replay_calls(paths["events"]) != calls
        or any(item["status"] != "success" for item in source_calls)
        or _read_json(paths["response"]) != candidate["recorded_response"]
    ):
        raise ValueError(f"task {task_id} autonomous trajectory differs from source evidence")
    evaluation = _read_json(paths["evaluation"])
    if (
        int(evaluation.get("task_id") or -1) != task_id
        or float(evaluation.get("score") or 0) != 1.0
    ):
        raise ValueError(f"task {task_id} autonomous source evaluation is not passing")


def load_replay_plan(profile_path: str | Path, seed_manifest_path: str | Path) -> dict[str, Any]:
    """Return a validated, exact method and task plan for one formal replay suite."""

    profile_file = Path(profile_path).expanduser().resolve()
    repo_root = profile_file.parents[3]
    profile = _read_json(profile_file)
    if profile.get("schema") != PROFILE_SCHEMA:
        raise ValueError("unsupported trajectory replay profile")

    selection = profile.get("task_selection") or {}
    if selection.get('benchmark_dataset'):
        from harness.trajectory_replay.report_inputs import replay_input_path
        selection_file = replay_input_path(selection, current='corrected-90')
    else:
        selection_file = (repo_root / str(selection.get("path") or "")).resolve()
        if repo_root not in selection_file.parents:
            raise ValueError("task selection must stay inside the repository")
    if not selection_file.is_file() or _sha256(selection_file) != selection.get("sha256"):
        raise ValueError("trajectory replay task selection does not match the frozen profile")
    selection_payload = _read_json(selection_file)
    task_ids = [int(item["task_id"]) for item in selection_payload.get("tasks") or []]
    if len(task_ids) != int(selection.get("task_count") or 0) or len(task_ids) != len(
        set(task_ids)
    ):
        raise ValueError("trajectory replay task selection cardinality is invalid")

    repair = profile.get("seed_inventory") or {}
    repair_file = (repo_root / str(repair.get("expert_repairs_path") or "")).resolve()
    if repo_root not in repair_file.parents:
        raise ValueError("expert repairs must stay inside the repository")
    if not repair_file.is_file() or _sha256(repair_file) != repair.get("expert_repairs_sha256"):
        raise ValueError("expert repairs do not match the frozen replay profile")
    repair_payload = _read_json(repair_file)
    repair_records = {int(item["task_id"]): item for item in repair_payload.get("repairs") or []}
    if len(repair_records) != len(repair_payload.get("repairs") or []):
        raise ValueError("expert repairs contain duplicate task IDs")

    seed_file = Path(seed_manifest_path).expanduser().resolve()
    seeds = _read_json(seed_file)
    if seeds.get("schema") != SEED_SCHEMA:
        raise ValueError("unsupported replay seed manifest")
    seed_source = seeds.get("source") or {}
    if seed_source.get("selection_sha256") != selection.get("sha256"):
        raise ValueError("replay seeds are not bound to the frozen task selection")
    seed_repairs = seeds.get("expert_repairs") or {}
    if seed_repairs.get("repair_spec_sha256") != repair.get("expert_repairs_sha256"):
        raise ValueError("replay seeds are not bound to the frozen expert repairs")
    authoritative: dict[tuple[str, str, int], dict[str, Any]] = {}
    for declared_model, declared in (seed_source.get("evidence") or {}).items():
        root = Path(str((declared or {}).get("root") or "")).expanduser().resolve()
        attempts_file = root / "attempts.jsonl"
        manifest_file = root / "manifest.json"
        if (
            not attempts_file.is_file()
            or not manifest_file.is_file()
            or _sha256(attempts_file) != declared.get("attempts_sha256")
            or _sha256(manifest_file) != declared.get("manifest_sha256")
        ):
            raise ValueError("replay seed authority manifest is missing or changed")
        manifest, rows = _authoritative_rows(root)
        model = str((manifest.get("protocol") or {}).get("model") or "")
        if model != declared_model or manifest.get("selection_sha256") != selection.get("sha256"):
            raise ValueError("replay seed authority differs from the frozen selection")
        for row in rows:
            key = (model, str(row["engine"]), int(row["task_id"]))
            if key in authoritative:
                raise ValueError("replay seed authority contains duplicate final executions")
            authoritative[key] = row
    seed_tasks = seeds.get("tasks") or []
    seed_task_ids = [int(item["task_id"]) for item in seed_tasks]
    if len(seed_task_ids) != len(set(seed_task_ids)) or set(seed_task_ids) != set(task_ids):
        raise ValueError("replay seeds do not match the frozen task selection")
    ready = [item for item in seed_tasks if item.get("replay_status") == "ready"]
    if len(ready) != int(repair.get("required_ready_tasks") or 0):
        raise ValueError("replay seeds do not provide the required ready-task coverage")
    for item in seed_tasks:
        candidate_ids = [
            str(candidate.get("id") or "") for candidate in item.get("candidates") or []
        ]
        canonical_id = str(item.get("canonical_candidate_id") or "")
        if (
            not canonical_id
            or candidate_ids.count(canonical_id) != 1
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise ValueError("every replay task must identify one unique canonical trajectory")
        canonical = next(
            candidate
            for candidate in item.get("candidates") or []
            if candidate.get("id") == canonical_id
        )
        if _contains_ref(canonical["tool_calls"]):
            raise ValueError(
                f"task {item['task_id']} canonical trajectory contains an ephemeral ref"
            )
        _validate_canonical_candidate(
            item,
            canonical,
            str(repair["expert_repairs_sha256"]),
            repair_records.get(int(item["task_id"])),
            authoritative.get(
                (
                    str((canonical.get("method") or {}).get("model") or ""),
                    str((canonical.get("method") or {}).get("engine") or ""),
                    int(item["task_id"]),
                )
            ),
        )

    methods = profile.get("methods") or []
    method_ids = [str(item.get("id") or "") for item in methods]
    engines = [str(item.get("engine") or "") for item in methods]
    if (
        not methods
        or len(method_ids) != len(set(method_ids))
        or len(engines) != len(set(engines))
        or any(engine not in configured_browser_images() for engine in engines)
    ):
        raise ValueError("trajectory replay methods must be unique registered engines")

    qualification = profile.get("qualification") or {}
    qualification_engine = str(qualification.get("reference_engine") or "")
    if qualification_engine not in engines:
        raise ValueError("trajectory qualification requires one registered reference engine")
    if int(qualification.get("required_task_count") or 0) != len(task_ids):
        raise ValueError("trajectory qualification must cover the frozen task set")

    execution = profile.get("execution") or {}
    if execution.get("environment_pool_count") != 1:
        raise ValueError("the fixed-port WebArena stack supports exactly one environment pool")
    if execution.get("method_parallelism") != 1 or execution.get("task_parallelism") != 1:
        raise ValueError("one environment pool requires serial methods and serial tasks")
    if execution.get("teardown_between_methods") is not True:
        raise ValueError("formal replay requires method teardown")
    if "retry_failed_task" in execution and type(execution["retry_failed_task"]) is not bool:
        raise ValueError("retry_failed_task must be a boolean")
    if execution.get("reset_before_every_task") is not True and (
        execution.get("reset_before_every_task") is not False
        or execution.get("reset_before_every_method") is not True
        or execution.get("retry_failed_task") is not False
    ):
        raise ValueError(
            "method-scoped site preparation requires an explicit method reset "
            "and no failed-task retry"
        )
    trusted_source_site_hosts = {
        str(host) for host in execution.get("trusted_source_site_hosts") or []
    }
    if not trusted_source_site_hosts or any(
        not host or ":" in host or "/" in host for host in trusted_source_site_hosts
    ):
        raise ValueError("formal replay requires explicit trusted source site hosts")
    for task in seed_tasks:
        canonical = next(
            candidate
            for candidate in task.get("candidates") or []
            if candidate.get("id") == task.get("canonical_candidate_id")
        )
        untrusted = replay_site_hosts(canonical["tool_calls"]) - trusted_source_site_hosts
        if untrusted:
            raise ValueError(
                f"task {task['task_id']} canonical trajectory uses untrusted site hosts: "
                f"{sorted(untrusted)}"
            )
    if profile.get("runs_per_task") != 1:
        raise ValueError("formal replay requires exactly one logical run per task and method")
    if repair.get("canonical_trajectories_per_task") != 1:
        raise ValueError("formal replay requires exactly one canonical trajectory per task")
    if (profile.get("scoring") or {}).get("model_calls") != 0:
        raise ValueError("trajectory replay must not call a model")

    retrieval_candidates = {}
    for task in seed_tasks:
        canonical = next(
            item for item in task["candidates"] if item["id"] == task["canonical_candidate_id"]
        )
        if str(canonical["recorded_response"].get("task_type", "")).upper() == "RETRIEVE":
            retrieval_candidates[int(task["task_id"])] = canonical
    retrieval_contracts = load_contracts(
        (profile.get("scoring") or {}).get("retrieval_acceptance"), repo_root, retrieval_candidates
    )
    from harness.webarena_state import load_profile_contracts, require_supersession


    state_contracts = load_profile_contracts(
        (profile.get("scoring") or {}).get("observed_state"), repo_root
    )
    if state_contracts:
        corrections = webarena_corrections.load_corrections()
        for task_id, contract in state_contracts.items():
            if task_id not in {int(task["task_id"]) for task in seed_tasks}:
                raise ValueError("state contract outside replay task selection")
            if task_id in corrections.records:
                require_supersession(
                    contract,
                    corrections.records[task_id]["correction_id"],
                    corrections.record_sha256s[task_id],
                )
    return {
        "profile": profile,
        "retrieval_task_ids": sorted(retrieval_candidates),
        "retrieval_contracts": retrieval_contracts,
        "retrieval_protocols": {
            task_id: contract["schema"] for task_id, contract in retrieval_contracts.items()
        },
        "state_contracts": state_contracts,
        "profile_path": str(profile_file),
        "profile_sha256": _sha256(profile_file),
        "seed_manifest": seeds,
        "seed_manifest_path": str(seed_file),
        "seed_manifest_sha256": _sha256(seed_file),
        "task_ids": task_ids,
        "methods": methods,
        "engines": engines,
        "qualification_engine": qualification_engine,
        "trusted_source_site_hosts": trusted_source_site_hosts,
    }


NAVIGATION_CONTEXT_LOSS = (
    "Evaluate failed: Page.evaluate: Execution context was destroyed, "
    "most likely because of a navigation"
)
COMPLETION_CANDIDATES = (
    "evaluation/trajectory-replay/webarena-verified/mutation-completion-candidates.json"
)


def _render_observation_script(script: str, values: dict[str, Any]) -> str:
    """Insert JSON literals once without interpreting their contents as template tokens."""
    tokens = re.compile(r"\b(?:" + "|".join(re.escape(key) for key in values) + r")\b")
    return tokens.sub(lambda match: json.dumps(values[match.group()]), script)


def compile_mutation_readiness(
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
        script = _render_observation_script(script, {
            "PATH": readiness["path"], "PRODUCT_ID": readiness["product_id"],
            "EXPECTED_TYPE": readiness["expected_type"], "TIMEOUT": readiness["timeout_ms"],
        })
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
    script = _render_observation_script(script, {
        "MODULE": module, "METHOD": method, "TIMEOUT": timeout,
    })
    return readiness, script


def compile_mutation_observer(
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
        value = observer[key]
        parsed = urlsplit(value)
        if (
            not value.startswith("/") or value.startswith("//") or "\\" in value
            or parsed.scheme or parsed.netloc or parsed.fragment
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
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
    script = _render_observation_script(script, replacements)
    return rule, script


def verify_compiled_mutation_proposal(
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
        or proposal.get("completion_candidates_sha256") != _sha256(candidate_path)
    ):
        raise ValueError("proposal observer is stale or differs from the completion contract")


def mutation_observer_retry_policy(
    policy: Any, source_call_count: int, compiled_script: str | None
) -> dict[str, Any]:
    """Only retry a bound compiled observation; canonical mutation calls are never retried."""
    if policy is None or policy == {}:
        return {}
    fields = {
        "after_frozen_call_count", "retry_only_final_read_only_observer", "retryable_error_exact",
        "max_observer_attempts", "original_action_retries", "navigation_ready_timeout_ms",
    }
    if (
        not isinstance(policy, dict) or set(policy) != fields or compiled_script is None
        or type(policy.get("after_frozen_call_count")) is not int
        or policy["after_frozen_call_count"] != source_call_count
        or policy["retry_only_final_read_only_observer"] is not True
        or policy["retryable_error_exact"] != NAVIGATION_CONTEXT_LOSS
        or type(policy["max_observer_attempts"]) is not int
        or not 1 <= policy["max_observer_attempts"] <= 3
        or type(policy["original_action_retries"]) is not int
        or policy["original_action_retries"] != 0
        or type(policy["navigation_ready_timeout_ms"]) is not int
        or not 1 <= policy["navigation_ready_timeout_ms"] <= 30_000
    ):
        raise ValueError("observer retry policy requires a bounded compiled read-only observer")
    return policy
