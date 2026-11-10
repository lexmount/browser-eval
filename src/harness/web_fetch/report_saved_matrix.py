"""Present a complete, explicitly bound matrix of saved results without rescoring.

The external specification owns the population, source selection and documented
missing cells. Rendering remains in the Web Fetch report owner. Private response
and debug files are verified but are not copied into the portable public views.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import errno
from html.parser import HTMLParser
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from lexbench_webfetch.metrics import task_measured, task_passed
from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.paths import contained_path, safe_identifier

from harness.web_fetch.artifacts import (
    artifact_bindings, record_private_bindings, record_public_call_log_bindings,
    verify_record_artifacts,
)
from harness.web_fetch.models import report_family
from harness.record_io import canonical_json_sha256, file_sha256 as _sha, json_bytes


# Only source validation can establish final-render admission. Serialized summaries
# remain useful diagnostics but cannot authorize their own publication state.
_FINAL_ADMISSION = ContextVar('saved_matrix_final_admission', default=None)


@contextmanager
def _source_validated_render(output, model):
    token = _FINAL_ADMISSION.set({'output': Path(output).resolve(),
                                  'model': canonical_json_sha256(model)})
    try:
        yield
    finally:
        _FINAL_ADMISSION.reset(token)


def _require_source_admission(output, model):
    admission = _FINAL_ADMISSION.get()
    if (admission is None or admission['output'] != Path(output).resolve()
            or admission['model'] != canonical_json_sha256(model)):
        raise ValueError('A portable final report requires source-backed admission; use the saved-matrix builder')
    return admission


def _require_final_projection(output, dataset, records):
    if dataset.get('comparison', {}).get('publication_state') != 'final_saved_results':
        return
    admission = _FINAL_ADMISSION.get()
    if (admission is None or admission['output'] != Path(output).resolve()
            or admission.get('projection') != canonical_json_sha256([dataset, records])):
        raise ValueError('Final saved results require source-backed admission')


def _json(path):
    return json.loads(Path(path).read_bytes())


def _write_bytes(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not path.is_file() or path.read_bytes() != raw):
        raise ValueError('Saved report destination collision: ' + str(path))
    path.write_bytes(raw)


def _write(path, value):
    _write_bytes(path, json_bytes(value))


def _require_public_json(receipt, *, label="Missing receipt"):
    """Reject private values and absolute path fields in distributable JSON."""
    from harness.web_fetch.publication_privacy import publication_risk
    risk = publication_risk(json_bytes(receipt))

    def local_path(value):
        if isinstance(value, dict):
            return any((key.endswith('path') and isinstance(item, str)
                        and (item.startswith(('/', '~', '\\')) or re.match(r'^[A-Za-z]:', item)))
                       or local_path(item) for key, item in value.items())
        if isinstance(value, list):
            return any(local_path(item) for item in value)
        return False

    if risk or local_path(receipt):
        raise ValueError(label + ' contains private content; use portable public evidence')


def _path(base, value):
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlinked source evidence')
    return path.absolute()


def _index_cases(dataset):
    entries = dataset['cases']
    ids = [safe_identifier(case['id']) for case in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate case ID in saved-matrix dataset')
    return dict(zip(ids, entries, strict=True))


def _case_identity(case):
    value = deepcopy(case)
    value.setdefault('task_kind', 'body')
    value.setdefault('track', 'web')
    value.get('source_benchmark', {}).pop('input_media_kind', None)
    if not value.get('source_benchmark'):
        value.pop('source_benchmark', None)
    return EvalCase(**value)


def _presentation_family(case, suite, groups):
    matches = [g['id'] for g in groups
               if g.get('task_family', g['id']) == report_family(case)
               and ('source_suites' not in g or suite in g['source_suites'])]
    if len(matches) != 1:
        raise ValueError('Report families must partition every task exactly once')
    return matches[0]


def _diagnostic_call_matches(call_id, provider, case_id):
    return isinstance(call_id, str) and re.fullmatch(
        re.escape(provider+'__'+case_id)+r'__r[0-9]{3}(?:__p[0-9]{3})?', call_id) is not None


def _receipt_covers(value, suite, provider, case_id, status, inherited=None, observations=None):
    """Consider all events: a skip cannot override evidence of an invocation."""
    states = set()
    def visit(node, context):
        if isinstance(node, list):
            for child in node:
                visit(child, context)
        elif isinstance(node, dict):
            if context.get('call_id') or node.get('call_id'):
                for field in ('suite', 'provider', 'case_id', 'call_id'):
                    if field in context and field in node and context[field] != node[field]:
                        raise ValueError('Conflicting diagnostic invocation identities')
            context = {**context, **{k: node[k] for k in ('suite', 'provider', 'case_id', 'call_id') if k in node}}
            call_id = context.get('call_id')
            if call_id:
                encoded = re.fullmatch(r'(.+?)__(.+)__r[0-9]{3}(?:__p[0-9]{3})?', call_id)
                if not encoded or any(context.get(field, encoded[index]) != encoded[index]
                                      for field, index in (('provider', 1), ('case_id', 2))):
                    raise ValueError('Conflicting diagnostic invocation identities')
            same_case = context.get('suite') == suite and context.get('case_id') == case_id
            diagnostic_matching = (context.get('suite') in (None, suite)
                                   and _diagnostic_call_matches(context.get('call_id'), provider, case_id))
            matching = (same_case and context.get('provider') == provider) or diagnostic_matching
            if (same_case and node.get('kind') == 'user_requested_case_skip'
                    and node.get('status') == 'not_called' and isinstance(node.get('providers'), list)
                    and provider in node['providers']
                    and node.get('score', 'absent') is None
                    and isinstance(node.get('user_instruction'), str) and node['user_instruction'].strip()):
                states.add('not_called')
            if matching:
                if observations is not None:
                    observations.append({**context, **node})
                if (node.get('state') == 'pre_call_blocked_no_method_calls'
                        and node.get('native_route_receipt', {}).get('transport_state') == 'not_started'):
                    states.add('not_called')
                if node.get('state') == 'called_but_uncommitted':
                    states.add('uncommitted')
                if node.get('transport_state') == 'started' or node.get('event') == 'http_request_started':
                    states.add('started')
            for child in node.values():
                if isinstance(child, (list, dict)):
                    visit(child, context)
    visit(value, inherited or {})
    return status in states and (status != 'not_called' or not states & {'uncommitted', 'started'})


def _called(record):
    metadata = record.get('provider_metadata', {})
    receipts = [metadata.get('native_route_receipt', {})]
    receipts += [a.get('native_route_receipt', {})
                 for a in metadata.get('retry', {}).get('attempts', [])]
    states = {r.get('transport_state') for r in receipts if r}
    if 'started' in states:
        return True
    if record.get('error_type') == 'unsupported_task' and states:
        return False
    if states and states <= {'not_started'}:
        return False
    raise ValueError('Saved result lacks explicit transport evidence')


def _saved_row_values(record, case):
    """Derive the visible scalar result from the retained call."""
    called = _called(record)
    metrics = record['metrics']
    passed = (None if called and not task_measured(metrics, case.get('task_kind', 'body'))
              else task_passed(record['ok'], metrics, case.get('task_kind', 'body')))
    retry = record.get('provider_metadata', {}).get('retry', {})
    return dict(status=('adapter_rejected' if not called else 'undecidable' if passed is None else 'passed' if passed else 'failed'),
                called=called, normal=bool(called and record['ok'] and metrics.get('request_ok', record['ok'])),
                passed=passed, elapsed_ms=record.get('elapsed_ms'),
                retries=retry.get('retries_used', max(0, retry.get('physical_attempts', 1) - 1)),
                error=record.get('error_message') or record.get('error_type') or '', checks=_checks(metrics))


def _checks(metrics):
    """Keep saved scalar verdicts in the UI; full metrics remain in record.json."""
    fields = {'acceptance_checks', 'required_components', 'deadline_overrun',
              'request_ok', 'task_kind', 'scoring_source_sha256', 'evaluation_excluded'}
    return {key: deepcopy(value) for key, value in metrics.items()
            if key in fields or (key.endswith('_pass') and isinstance(value, (bool, type(None))))}


def _totals(rows):
    return {'planned': len(rows), 'saved': sum(r['record_path'] is not None for r in rows),
            'called': sum(r['called'] for r in rows), 'normal': sum(r['normal'] for r in rows),
            'passed': sum(r['passed'] is True for r in rows),
            'failed': sum(r['called'] and r['passed'] is False for r in rows),
            'undecidable': sum(r['status'] == 'undecidable' for r in rows),
            'adapter_rejected': sum(r['status'] == 'adapter_rejected' for r in rows),
            'uncommitted': sum(r['status'] == 'uncommitted' for r in rows),
            'not_called': sum(r['status'] == 'not_called' for r in rows)}


class _SourceBindings:
    def __init__(self, base):
        self.base = base
        self.tracked = {}

    def bind(self, path, expected=None):
        path = _path(self.base, path)
        digest = _sha(path)
        if expected is not None and digest != expected:
            raise ValueError('Source hash differs: ' + path.name)
        if path in self.tracked and self.tracked[path] != digest:
            raise ValueError('Source changed during assembly')
        self.tracked[path] = digest
        return path

    def binding(self, value):
        if not isinstance(value, dict) or set(value) != {'path', 'sha256'}:
            raise ValueError('Evidence requires an explicit path/hash binding')
        digest = value['sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Evidence requires a nonempty SHA-256 digest')
        return self.bind(value['path'], digest)

def _execution_policy(binding, sources):
    """Read a frozen policy shared by adoption and replacement admission."""
    policy = _json(sources.binding(binding))
    if policy.get('provider_config'):
        sources.binding(policy['provider_config'])
    policy = {**policy,
              'created_at': policy.get('created_at', policy.get('started_at')),
              'provider_config_sha256': policy.get('provider_config_sha256',
                  policy.get('config_sha256', policy.get('provider_config', {}).get('sha256')))}
    if any(policy.get(field) is None for field in (
            'created_at', 'provider_config_sha256', 'timeout_seconds', 'max_failure_retries')):
        raise ValueError('Execution authorization is incomplete')
    return policy


def _completed_missing(spec, sources, selected, continuations=None):
    """Bind new calls to an exact, previously uncalled population."""
    configs = ([spec['completion']] if spec.get('completion') else []) + spec.get('completions', [])
    completed = set()
    for config in configs:
        scope = _completed_scope(config, sources, selected, continuations)
        if completed & scope:
            raise ValueError('Completion scopes overlap')
        completed.update(scope)
    return completed


def _completed_scope(config, sources, selected, continuations=None):
    """Validate one separately frozen completion cohort."""
    baseline = _json(sources.binding(config['baseline_matrix']))
    contract = _json(sources.binding(config['contract']))
    if 'jobs' in contract:
        declared = [(job['suite'], contract['provider'], cid)
                    for job in contract['jobs'] for cid in job['case_ids']]
    else:
        declared = [(config['suite'], provider, cid)
                    for provider in contract['providers'] for cid in contract['case_ids']]
    if len(set(declared)) != len(declared) or len(declared) != contract.get('cases', len(declared)):
        raise ValueError('Completion contract has duplicate or inconsistent scope')
    before = {(r['suite'], r['provider'], r['case_id']): r for r in baseline['rows']}
    started = datetime.fromisoformat(contract['created_at'])
    for key in declared:
        old = before.get(key, {})
        if old.get('status') != 'not_called' or old.get('called') is not False or old.get('record_path'):
            raise ValueError('Completion must fill a previously uncalled position')
        if key not in selected:
            raise ValueError('Completion requires every declared result')
        root, record, _, _ = selected[key]
        if not _called(record):
            raise ValueError('Completion requires actual invocation')
        dataset = _json(sources.bind(root / 'dataset.json'))
        matching = [case for case in dataset['cases'] if case['id'] == key[2]]
        if len(matching) != 1:
            raise ValueError('Completion dataset requires exactly one matching task')
        case = matching[0]
        policy = dict(contract)
        if contract.get('provider_config'):
            sources.binding(contract['provider_config'])
            policy['provider_config_sha256'] = contract['provider_config']['sha256']
        continuation = (continuations or {}).get(key)
        _validate_call_sequence(record, case, policy, continuation, require_saved_settings=True)
        _parameter_execution(root, record, case, policy, sources, None, 'current' if continuation else None)
        if continuation and continuation[1] is not None:
            prior_root, prior_record, _ = continuation[1]
            _parameter_execution(prior_root, prior_record, case, policy, sources, None, 'prior')
        attempts = record.get('provider_metadata', {}).get('retry', {}).get('attempts', [])
        if any(datetime.fromisoformat(call['started_at']) < started
               for call in [record, *attempts]):
            raise ValueError('Completion call predates its contract')
    return set(declared)


def _saved_revision(spec, sources, cases, selected, completed=()):
    """Bind an offline revision to exactly the original matrix and call evidence."""
    if not spec.get('offline_revision'):
        return None
    from harness.web_fetch.local_revision import validate_revision_chain
    config = spec['offline_revision']
    root = _path(sources.base, config['root'])
    receipt_path = sources.binding(config['receipt'])
    receipt_path.relative_to(root)
    receipt_binding = {'path': receipt_path.relative_to(root).as_posix(),
                       'sha256': _sha(receipt_path)}
    dataset_path = sources.bind(root/'dataset.json')
    results_path = sources.bind(root/'results.jsonl')
    dataset = _json(dataset_path)
    records = [json.loads(line) for line in results_path.read_bytes().splitlines() if line.strip()]
    _, before_dataset, before_results = validate_revision_chain(root, receipt_binding, dataset, records)
    if before_dataset is None or before_results is None:
        raise ValueError('Saved revision requires its complete predecessor history')
    def revision_file(binding):
        return sources.bind(contained_path(root, binding['path'], reject_symlinks=True), binding['sha256'])
    binding = receipt_binding
    receipts = []
    while binding:
        receipt = _json(revision_file(binding))
        revision_file(receipt['input_dataset'])
        revision_file(receipt['input_results'])
        receipts.append(receipt)
        binding = receipt.get('predecessor_revision')
    native_answer_attempts = {
        proof['attempt_id'] for receipt in receipts
        for proof in receipt.get('native_answer_corrections', [])
    }
    original_dataset = _json(revision_file(before_dataset))
    original_records = [json.loads(line) for line in revision_file(before_results).read_bytes().splitlines() if line.strip()]
    originals = {r['attempt_id']: r for r in original_records}
    revised = {r['attempt_id']: r for r in records}
    original_cases = _index_cases(original_dataset)
    revised_cases = _index_cases(dataset)
    expected_cases = {s+'--'+cid for s, group in cases.items() for cid in group}
    if set(original_cases) != expected_cases or set(revised_cases) != expected_cases:
        raise ValueError('Saved revision case population differs')
    case_updates, record_updates = {}, {}
    for suite, group in cases.items():
        for cid, case in group.items():
            route = suite+'--'+cid
            def restore_case(value, cid=cid):
                restored = deepcopy(value)
                restored['id'] = cid
                restored.pop('report_family', None)
                restored.pop('report_family_label', None)
                return restored
            if _case_identity(restore_case(original_cases[route])) != _case_identity(case):
                raise ValueError('Saved revision predecessor case differs')
            current = restore_case(revised_cases[route])
            if current != restore_case(original_cases[route]):
                case_updates[(suite, cid)] = current
    expected_attempts = set()
    for key, (_, raw, _, _) in selected.items():
        suite, provider, cid = key
        attempt = suite+'--'+raw['attempt_id']
        if key in completed:
            if attempt in originals:
                raise ValueError('Completion cannot replace a frozen revision call')
            current_case = deepcopy(revised_cases[suite+'--'+cid])
            current_case['id'] = cid
            current_case.pop('report_family', None)
            current_case.pop('report_family_label', None)
            if _case_identity(cases[suite][cid]) != _case_identity(current_case):
                raise ValueError('Completion task differs from revised task')
            continue
        expected_attempts.add(attempt)
        prefix = f'evidence/{suite}/{provider}/{cid}/'
        public_paths = {path for path, _ in artifact_bindings(raw)}
        def project(value, key='', public_paths=public_paths, prefix=prefix):
            if isinstance(value, dict):
                return {k: project(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [project(v, key) for v in value]
            if isinstance(value, str) and (key == 'path' or key.endswith('_path')) and value in public_paths:
                return prefix+value
            return value
        expected = project(raw)
        expected.update(case_id=suite+'--'+cid, attempt_id=attempt)
        original = originals.get(attempt, {})
        actual = {k: v for k, v in original.items() if k not in {
            'report_invocation_status', 'report_superseded_history', 'report_retry_continuation',
            'report_parameter_rerun', 'report_completion_note'}}
        # Composed snapshots nest the same immutable artifacts under a report
        # prefix. Accept that relocation only after verifying every original
        # artifact hash; all other call fields must still match exactly.
        original_answer = actual.get('artifact_path', '')
        answer = raw.get('artifact_path', '')
        if answer and original_answer.endswith('/' + answer):
            original_prefix = original_answer[:-len(answer)]
            paths = {}
            for relative, digest in artifact_bindings(raw):
                sources.bind(contained_path(root, original_prefix + relative, reject_symlinks=True), digest)
                paths[original_prefix + relative] = prefix + relative
            def relocate(value, key='', paths=paths):
                if isinstance(value, dict):
                    return {k: relocate(v, k) for k, v in value.items()}
                if isinstance(value, list):
                    return [relocate(v, key) for v in value]
                return (paths.get(value, value)
                        if isinstance(value, str) and (key == 'path' or key.endswith('_path')) else value)
            actual = relocate(actual)
        if expected != actual:
            raise ValueError('Saved revision predecessor result differs: '+attempt)
        current = revised[attempt]
        sources.bind(contained_path(root, current['artifact_path'], reject_symlinks=True),
                     current['metrics']['content_sha256'])
        if current != original:
            record = deepcopy(raw)
            for field in ('metrics', 'scoring', 'diagnosis', 'failure_category'):
                if field in current:
                    record[field] = deepcopy(current[field])
                else:
                    record.pop(field, None)
            if attempt in native_answer_attempts:
                from harness.web_fetch.native_projection import SCORE_ANSWER_FIELDS

                if current['metrics']['content_sha256'] != raw['metrics']['content_sha256']:
                    raise ValueError('Saved revision changed the public answer bytes: ' + attempt)
                # Retain the verified scoring inputs without exposing the native envelope.
                current_metadata = current.get('provider_metadata', {})
                metadata = record.setdefault('provider_metadata', {})
                for field in sorted(SCORE_ANSWER_FIELDS | {'answer_format'}):
                    if field in current_metadata:
                        metadata[field] = deepcopy(current_metadata[field])
            record_updates[key] = record
    if set(originals) != expected_attempts or set(revised) != expected_attempts:
        raise ValueError('Saved revision result population differs')
    attachments = {safe_identifier(name): sources.binding(value)
                   for name, value in config.get('review_files', {}).items()}
    return {'cases': case_updates, 'records': record_updates,
            'attachments': attachments,
            'receipt': {'kind': 'saved_matrix_offline_revision', 'provider_calls_added': 0,
                        'receipt_sha256': _sha(receipt_path),
                        'changed_cases': [s+'--'+c for s, c in sorted(case_updates)],
                        'rescored_results': len(record_updates),
                        'review_files': {name: 'review-assets/'+name+'.json' for name in attachments},
                        'steps': [{'authority': r['authority'], 'regression_ids': r['regression_ids'],
                                   'rule_revision': r['rule_revision'],
                                   'task_verdict_changes': r['task_verdict_changes']} for r in reversed(receipts)]}}


def _load_saved_matrix(spec_path):
    """Validate selection and immutable sources without writing a report."""
    spec_path = Path(spec_path)
    spec_path = spec_path.parent.resolve() / spec_path.name
    spec = _json(spec_path)
    if spec.get('schema_version') != 1:
        raise ValueError('Unsupported saved-matrix specification')
    if spec.get('ranking', 'diagnostic') not in {'diagnostic', 'saved_task_pass_rate'}:
        raise ValueError('Unsupported saved-matrix ranking selection')
    if spec.get('presentation', 'audit') not in {'audit', 'final'}:
        raise ValueError('Unknown saved-matrix presentation')
    sources = _SourceBindings(spec_path.parent)
    bind, binding, tracked = sources.bind, sources.binding, sources.tracked
    def registry(items):
        ids = [safe_identifier(x['id']) for x in items]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('Duplicate or empty matrix registry')
        return ids

    providers = registry(spec['providers'])
    suites = registry(spec['suites'])
    cases = {}
    bind(spec_path)
    if spec.get('native_policy_registry'):
        binding(spec['native_policy_registry'])
    for suite in spec['suites']:
        dataset = _json(binding(suite['dataset']))
        cases[suite['id']] = _index_cases(dataset)
    routes = [suite + '--' + cid for suite in suites for cid in cases[suite]]
    if len(routes) != len(set(routes)):
        raise ValueError('Colliding suite-qualified case routes')
    if 'report_families' in spec:
        families = registry(spec['report_families'])
        for group in spec['report_families']:
            if 'source_suites' in group and (not isinstance(group['source_suites'], list)
                    or not group['source_suites'] or not set(group['source_suites']) <= set(suites)):
                raise ValueError('Report family names invalid source suites')
        actual = {_presentation_family(c, suite, spec['report_families'])
                  for suite, group in cases.items() for c in group.values()}
        if set(families) != actual or any(not str(f.get('label', '')).strip() for f in spec['report_families']):
            raise ValueError('Report families must label every observed task family exactly once')
    exclusions = spec.get('excluded_report_families', [])
    family_ids = {group['id'] for group in spec.get('report_families', [])}
    if (not isinstance(exclusions, list) or any(not isinstance(x, str) for x in exclusions)
            or len(set(exclusions)) != len(exclusions) or not set(exclusions) <= family_ids
            or exclusions and set(exclusions) == family_ids):
        raise ValueError('Invalid excluded report families')
    omitted = spec.get('excluded_report_providers', [])
    if (not isinstance(omitted, list) or any(not isinstance(x, str) for x in omitted)
            or len(set(omitted)) != len(omitted) or not set(omitted) < set(providers)):
        raise ValueError('Invalid excluded report providers')
    expected = {(s, p, cid) for s in suites for p in providers for cid in cases[s]}
    selected, historical = {}, {}
    source_groups = [(False, x) for x in spec['sources']] + [(True, x) for x in spec.get('superseded_sources', [])]
    for is_history, source in source_groups:
        destination = historical if is_history else selected
        suite = source['suite']
        if suite not in cases:
            raise ValueError('Unknown source suite')
        root = _path(spec_path.parent, source['root'])
        result_path = binding(source['results'])
        result_path.relative_to(root)
        source_dataset = binding(source['dataset']) if source.get('dataset') else bind(root / 'dataset.json')
        source_cases = _index_cases(_json(source_dataset))
        ids = source['case_ids']
        if len(ids) != len(set(ids)) or not set(ids) <= set(cases[suite]):
            raise ValueError('Unknown or duplicate selected case IDs')
        if source.get('provider') is not None and source['provider'] not in providers:
            raise ValueError('Unknown source provider')
        if source.get('job'):
            job = _json(binding(source['job']))
            if (_path(spec_path.parent, job['output']) != root or job['suite'] != suite
                    or not set(ids) <= set(job['case_ids'])
                    or source.get('provider', job['provider']) != job['provider']):
                raise ValueError('Job differs from declared source selection')
        seen_ids = set()
        for line in result_path.read_bytes().splitlines(keepends=True):
            if not line.strip():
                continue
            record = json.loads(line)
            if record['case_id'] not in ids or (source.get('provider') and record['provider'] != source['provider']):
                continue
            key = (suite, record['provider'], record['case_id'])
            if key not in expected or key in destination:
                raise ValueError('Duplicate or undeclared suite/provider/case result')
            case = cases[suite][record['case_id']]
            if (record['case_id'] not in source_cases
                    or _case_identity(source_cases[record['case_id']]) != _case_identity(case)
                    or record['url'] != case['url']):
                raise ValueError('Source case differs from frozen task')
            native_cases = {cid: {**x, 'task_kind': x.get('task_kind', 'body'),
                                  'track': x.get('track', 'web')}
                            for cid, x in source_cases.items()}
            verify_record_artifacts(root, record, native_cases=native_cases)
            for relative, digest in artifact_bindings(record):
                bind(contained_path(root, relative, reject_symlinks=True), digest)
            for item in record_private_bindings(record):
                bind(contained_path(root, item['path'], reject_symlinks=True), item['sha256'])
            destination[key] = (root, record, line, result_path)
            seen_ids.add(record['case_id'])
        if seen_ids != set(ids):
            raise ValueError('Selected case has no source result')
    continuations = {}
    continuation_groups = {}
    def contains_record(value, record):
        if isinstance(value, dict):
            return value == record or any(contains_record(x, record) for x in value.values())
        return isinstance(value, list) and any(contains_record(x, record) for x in value)
    def one_call(record):
        retry = record.get('provider_metadata', {}).get('retry', {})
        return (_called(record) and retry.get('physical_attempts', len(retry.get('attempts', [])) or 1) == 1)
    for entry in spec.get('retry_continuations', []):
        key = entry['suite'], entry['provider'], entry['case_id']
        if key in continuations or key not in selected:
            raise ValueError('Retry continuation must identify one current result')
        current = selected[key][1]
        if hashlib.sha256(selected[key][2]).hexdigest() != entry['current_record_sha256'] or not one_call(current):
            raise ValueError('Retry continuation current record differs or adds extra calls')
        launch_path = binding(entry['launch_contract'])
        launch = _json(launch_path)
        if datetime.fromisoformat(launch['started_at']) > datetime.fromisoformat(current['started_at']):
            raise ValueError('Retry continuation was not declared before its call')
        source_bound_launch = launch.get('continuation_kind') == 'remaining_one_retry'
        if source_bound_launch:
            ids = launch.get('case_ids', [])
            if (not ids or len(set(ids)) != len(ids) or launch.get('max_failure_retries') != 0
                    or launch.get('prior_physical_calls') != {cid: 1 for cid in ids}
                    or set(launch.get('prior_sources', {})) != set(ids)):
                raise ValueError('Retry continuation must preserve the two-call budget')
        elif launch.get('total_authorized_physical_calls_per_case', launch.get('total_authorized_physical_calls')) != 2:
            raise ValueError('Retry continuation must preserve the two-call budget')
        continuation_groups.setdefault(launch_path, set()).add((key[1], key[2]))
        prior = None
        if entry.get('prior_source'):
            source = entry['prior_source']
            root = _path(spec_path.parent, source['root'])
            result_path = binding(source['results'])
            result_path.relative_to(root)
            prior_cases = _index_cases(_json(binding(source['dataset'])))
            matches = []
            for line in result_path.read_bytes().splitlines(keepends=True):
                if not line.strip():
                    continue
                saved = json.loads(line)
                if (saved['provider'], saved['case_id']) == key[1:]:
                    matches.append((saved, line))
            if len(matches) != 1:
                raise ValueError('Retry continuation prior record is missing or ambiguous')
            old, old_line = matches[0]
            prior_bound = (root == _path(spec_path.parent, launch['prior_sources'].get(key[2], ''))
                           if source_bound_launch else contains_record(launch, old))
            if (not one_call(old) or not prior_bound
                    or _case_identity(prior_cases[key[2]]) != _case_identity(cases[key[0]][key[2]])
                    or old == current):
                raise ValueError('Retry continuation prior evidence differs from declared first call')
            end = datetime.fromisoformat(old['started_at']) + timedelta(milliseconds=old['elapsed_ms'])
            if old.get('completed_at'):
                end = max(end, datetime.fromisoformat(old['completed_at']))
            if end > datetime.fromisoformat(launch['started_at']):
                raise ValueError('Retry continuation first call must finish before its launch')
            if task_passed(old['ok'], old['metrics'], prior_cases[key[2]].get('task_kind', 'body')):
                raise ValueError('Retry continuation requires a failed first call')
            verify_record_artifacts(root, old, native_cases=prior_cases)
            for relative, digest in artifact_bindings(old):
                bind(contained_path(root, relative, reject_symlinks=True), digest)
            for item in record_private_bindings(old):
                bind(contained_path(root, item['path'], reject_symlinks=True), item['sha256'])
            prior = (root, old, old_line)
        else:
            receipt = binding(entry['prior_uncommitted'])
            old = _json(receipt)
            log = binding(entry['prior_log'])
            events = [json.loads(line) for line in log.read_bytes().splitlines() if line.strip()]
            if (not _receipt_covers(old, *key, 'uncommitted') or old.get('elapsed_ms') is not None
                    or sum(x.get('event') == 'http_request_started' for x in events) != 1
                    or launch.get('first_uncommitted', {}).get('sha256') != tracked[log]
                    or launch.get('first_uncommitted', {}).get('case_id') != key[2]):
                raise ValueError('Uncommitted first call lacks its declared single-call evidence')
            start = next(event for event in events if event.get('event') == 'http_request_started')
            call_id = start.get('call_id')
            if not isinstance(call_id, str) or not re.fullmatch(
                    re.escape(key[1]+'__'+key[2])+r'__r[0-9]{3}(?:__p001)?', call_id):
                raise ValueError('Uncommitted first-call log identifies another invocation')
            try:
                if any(event.get('call_id') != call_id for event in events):
                    raise ValueError('Uncommitted first-call log has missing or conflicting event identities')
                matching_events = events
                timestamps = [datetime.fromisoformat(event['at']) for event in matching_events]
                if timestamps != sorted(timestamps):
                    raise ValueError('Uncommitted first-call log timestamps are out of order')
                request_index = matching_events.index(start)
                terminal = {'http_response_received', 'provider_returned', 'answer_prepared', 'collection_completed'}
                if any(event.get('event') in terminal for event in matching_events[:request_index]):
                    raise ValueError('Uncommitted first-call log completed before request start')
                if timestamps[-1] > datetime.fromisoformat(launch['started_at']):
                    raise ValueError('Uncommitted first-call log occurs after continuation launch')
            except (KeyError, TypeError) as exc:
                raise ValueError('Uncommitted first-call log lacks timestamp evidence') from exc
            # Derived only from the bound log, never from caller-supplied metadata.
            entry = {**entry, '_uncommitted_started_at': start['at']}

        continuations[key] = (entry, prior, launch_path)
    for launch_path, actual in continuation_groups.items():
        launch = _json(launch_path)
        if launch.get('continuation_kind') == 'remaining_one_retry':
            methods = {provider for provider, _ in actual}
            if len(methods) != 1:
                raise ValueError('Retry continuation launch mixes providers')
            declared = {(next(iter(methods)), cid) for cid in launch['case_ids']}
        elif launch.get('case_ids'):
            declared = {(launch['provider'], cid) for cid in launch['case_ids']}
        else:
            first = launch['first_record']['record']
            declared = {(first['provider'], first['case_id'])}
        if actual != declared:
            raise ValueError('Retry continuation must preserve its complete declared scope')
    replacement_scope = None
    if historical:
        replacement_scope = binding(spec['supersession_scope'])
        scope = _json(replacement_scope)
        if scope.get('kind') != 'predeclared_replacement_scope':
            raise ValueError('Supersession requires a predeclared replacement scope')
        if not isinstance(scope.get('created_at'), str):
            raise ValueError('Supersession scope requires a pre-call timestamp')
        scope_started = datetime.fromisoformat(scope['created_at'])
        cells = scope.get('cells', [])
        scoped = {(x['suite'], x['provider'], x['case_id']): x for x in cells}
        if len(scoped) != len(cells) or set(scoped) != set(historical):
            raise ValueError('Superseded history differs from complete replacement scope')
        for key, (_, record, raw_line, _) in historical.items():
            if key in selected and selected[key][2] == raw_line:
                raise ValueError('Superseded result requires a distinct current result')
            original = binding(scoped[key]['old_record'])
            if original.read_bytes() != raw_line:
                raise ValueError('Superseded record differs from predeclared original')
    elif spec.get('supersession_scope'):
        raise ValueError('Supersession scope has no historical sources')
    missing = {}
    for entry in spec.get('missing', []):
        key = (entry['suite'], entry['provider'], entry['case_id'])
        if key not in expected or key in missing or key in selected:
            raise ValueError('Duplicate or undeclared missing cell')
        if entry['status'] not in {'uncommitted', 'not_called'} or not entry.get('reason'):
            raise ValueError('Missing cell needs an explicit state and reason')
        receipt = binding(entry['receipt'])
        evidence = [_json(receipt)]
        for diagnostic_source in entry.get('diagnostic_sources', []):
            diagnostic = binding(diagnostic_source)
            raw = diagnostic.read_bytes()
            try:
                evidence.append(json.loads(raw))
            except json.JSONDecodeError:
                evidence.append([json.loads(line) for line in raw.splitlines() if line.strip()])
        if (not _receipt_covers(_json(receipt), *key, entry['status'])
                or not _receipt_covers(evidence, *key, entry['status'])):
            raise ValueError('Missing receipt does not identify the declared cell')
        if entry.get('recovery'):
            raise ValueError('Recovery projections are not supported; bind the original missing receipt')
        missing[key] = (entry, receipt)
    for key in historical:
        if key not in selected and (key not in missing or missing[key][0]['status'] != 'uncommitted'):
            raise ValueError('Superseded result requires a current call or explicit uncommitted call')
    if set(selected) | set(missing) != expected:
        raise ValueError('Unexplained missing matrix cells')
    uncommitted_execution = {}
    for key in historical:
        replacement = selected[key][1] if key in selected else _json(missing[key][1])
        physical = replacement.get('provider_metadata', {}).get('retry', {}).get('attempts') or [replacement]
        continuation = continuations.get(key)
        if continuation:
            entry, prior, _ = continuation
            first = prior[1] if prior else _json(binding(entry['prior_uncommitted']))
            physical = [*(first.get('provider_metadata', {}).get('retry', {}).get('attempts') or [first]), *physical]
        if any(not call.get('started_at') or datetime.fromisoformat(call['started_at']) < scope_started
               for call in physical):
            raise ValueError('Replacement call predates its declared scope or lacks chronology')
        execution = scoped[key].get('execution_contract', scope.get('execution_contract'))
        if not execution:
            raise ValueError('Replacement requires execution authorization')
        policy = _execution_policy(execution, sources)
        if any(datetime.fromisoformat(call['started_at']) < datetime.fromisoformat(policy['created_at'])
               for call in physical):
            raise ValueError('Replacement call predates execution authorization')
        if key not in selected:
            for field in ('provider_config_sha256', 'timeout_seconds'):
                if replacement.get(field) != policy[field]:
                    raise ValueError('Uncommitted call differs from execution authorization')
            count = replacement.get('physical_attempts')
            if type(count) is not int or not 1 <= count <= 1 + policy['max_failure_retries']:
                raise ValueError('Uncommitted call exceeds execution authorization')
            if not replacement.get('execution_log'):
                raise ValueError('Uncommitted call lacks execution authorization evidence')
            log = sources.binding(replacement['execution_log'])
            events = [json.loads(line) for line in log.read_bytes().splitlines() if line.strip()]
            starts = [event for event in events if event.get('event') == 'http_request_started']
            call_ids = [event.get('call_id') for event in starts]
            if (len(starts) != count
                    or any(not _diagnostic_call_matches(call_id, key[1], key[2]) for call_id in call_ids)
                    or len(set(call_ids)) != count):
                raise ValueError('Uncommitted call log differs from execution authorization count or identities')
            for event in starts:
                if (event.get('provider') != key[1] or event.get('case_id') != key[2]
                        or any(event.get(field) != policy[field]
                               for field in ('provider_config_sha256', 'timeout_seconds'))
                        or not event.get('started_at')
                        or datetime.fromisoformat(event['started_at']) < max(scope_started, datetime.fromisoformat(policy['created_at']))):
                    raise ValueError('Uncommitted call log differs from execution authorization')
            if (datetime.fromisoformat(starts[0]['started_at']) != datetime.fromisoformat(replacement['started_at'])
                    or any(datetime.fromisoformat(left['started_at']) > datetime.fromisoformat(right['started_at'])
                           for left, right in zip(starts, starts[1:], strict=False))):
                raise ValueError('Uncommitted call chronology differs from execution authorization')
            uncommitted_execution[key] = dict(physical_calls=count, events=events)

        else:
            root, record, _, _ = selected[key]
            case = cases[key[0]][key[2]]
            _validate_call_sequence(record, case, policy, continuations.get(key))
            _parameter_execution(root, record, case, policy, sources, None,
                                 'current' if continuations.get(key) else None)
            continuation = continuations.get(key)
            if continuation and continuation[1]:
                prior_root, prior_record, _ = continuation[1]
                _parameter_execution(prior_root, prior_record, case, policy, sources, None, 'prior')
    completed = _completed_missing(spec, sources, selected, continuations)
    for config in ([spec['completion']] if spec.get('completion') else []) + spec.get('completions', []):
        baseline = _json(sources.binding(config['baseline_matrix']))
        baseline_keys = [(row['suite'], row['provider'], row['case_id']) for row in baseline['rows']]
        if len(set(baseline_keys)) != len(baseline_keys) or set(baseline_keys) != expected:
            raise ValueError('Completion baseline population differs from the complete report')
        for row, key in zip(baseline['rows'], baseline_keys, strict=True):
            now_called = (_called(selected[key][1]) if key in selected
                          else missing[key][0]['status'] == 'uncommitted')
            if key not in completed and not row.get('called') and now_called:
                raise ValueError('Completion fills an undeclared position')
    return dict(spec=spec, sources=sources, providers=providers, suites=suites, cases=cases,
                selected=selected, historical=historical, continuations=continuations,
                missing=missing, uncommitted_execution=uncommitted_execution, replacement_scope=replacement_scope,
                replacement_cells=cells if replacement_scope else [],
                revision=_saved_revision(spec, sources, cases, selected, completed))


def _native_execution_manifest(root, record, sources):
    metadata = record.get('provider_metadata', {})
    if (root / 'manifest.json').is_file():
        manifest = _json(sources.bind(root / 'manifest.json'))
    else:
        batches = _json(sources.bind(root / 'execution.json'))['batches']
        matches = [batch for batch in batches if record['attempt_id'] in batch.get('attempt_ids', [])
                   and batch.get('native_route_contract', {}).get('sha256') ==
                   metadata['native_route_receipt']['contract_sha256']]
        if len(matches) != 1:
            raise ValueError('Native execution manifest is missing or ambiguous')
        manifest = matches[0]
    return manifest


def _saved_retry_budget(manifest, split_role=None):
    """Normalize only a proved split budget; never replace missing evidence."""
    parameters = manifest.get('parameters')
    budget = parameters.get('max_failure_retries') if isinstance(parameters, dict) else None
    if type(budget) is not int or budget < 0:
        raise ValueError('Saved execution requires an explicit nonnegative retry budget')
    if split_role is not None:
        expected = {'prior': 1, 'current': 0}
        if split_role not in expected or budget != expected[split_role]:
            raise ValueError('Saved split retry budget differs from its continuation role')
        return 1
    return budget


def _parameter_execution(root, record, case, contract, sources, baseline=None, split_role=None):
    """Check actual native requests against the authorized configuration, not just themselves."""
    from harness.web_fetch.native_routes import (
        native_route_cell, _request_parameters, verify_native_route_contract,
    )
    from harness.web_fetch.providers.base import build_provider
    from harness.web_fetch.config import expand_env
    metadata = record.get('provider_metadata', {})
    if not metadata.get('native_route_receipt', {}).get('contract_sha256'):
        raise ValueError('Parameter rerun lacks its execution contract')
    manifest = _native_execution_manifest(root, record, sources)
    allowance = contract.get('max_failure_retries')
    if (type(allowance) is not int or allowance < 0
            or _saved_retry_budget(manifest, split_role) != allowance):
        raise ValueError('Saved retry budget differs from execution authorization')
    expected_hash = contract.get('provider_config_sha256')
    if not expected_hash or manifest.get('provider_config_sha256') != expected_hash:
        raise ValueError('Parameter rerun configuration differs from authorization')
    config_path = sources.bind(manifest['provider_config_source'], expected_hash)
    config = expand_env(_json(config_path)['providers'][record['provider']])
    if manifest['parameters'].get('timeout') != contract['timeout_seconds']:
        raise ValueError('Parameter rerun timeout differs from authorization')
    for setting, observed in [('expected_binary_sha256', 'binary_sha256'),
                              ('expected_reported_version', 'reported_version')]:
        if setting in config and metadata.get(observed) != config[setting]:
            raise ValueError('Completion binary or version differs from authorization')
    failure_without_metadata = ('dns_mode' not in metadata and not record.get('ok')
                                and metadata.get('native_route_receipt', {}).get('failure_stage')
                                == 'after_transport')
    # A missing duplicate result field is not evidence of a DNS difference.
    # Apply the same system-default rule to every adapter; the bound native
    # configuration and request below must still prove the authorized settings.
    historical_system_dns = ('dns_mode' not in metadata and config.get('dns_mode', 'system') == 'system')
    if (metadata.get('dns_mode') != config.get('dns_mode', 'system')
            and not failure_without_metadata and not historical_system_dns):
        raise ValueError('Parameter rerun DNS differs from authorization')
    # Failed calls may return before adapter metadata is available. The bound native contract
    # below must still prove the authorized DNS configuration; no DNS trace is invented.
    native = _json(sources.bind(root / manifest['native_route_contract']['path'],
                                manifest['native_route_contract']['sha256']))
    # A projection selects one already retained call; the shared verifier still
    # checks every physical request and the original frozen cell and inventory.
    projection = {**manifest, 'providers': [p for p in manifest['providers'] if p['name'] == record['provider']],
                  'projection': {'kind': 'complete-provider-case-subset-from-retained-run'}}
    dataset = {'cases': [case]}
    verify_native_route_contract(root, projection, dataset, [record])
    provider = build_provider(record['provider'], config)
    key, expected = native_route_cell(provider, EvalCase(**case), timeout_seconds=contract['timeout_seconds'])
    cells = {tuple((c['method'], c['track'], c['task_kind'], c['input_media_kind'])): c for c in native['cells']}
    actual = cells[key]
    for field in ('provider_type', 'transport', 'interface', 'endpoint_sha256'):
        if actual.get(field) != expected.get(field):
            raise ValueError('Parameter rerun native options differ from authorization')
    configured = dict(expected['configured_parameters'])
    # These diagnostics were added after the options themselves. Historical
    # contracts still prove them through the exact interface/request comparison.
    for field in ('native_output', 'document_output', 'scrape_selectors'):
        if field not in actual.get('configured_parameters', {}):
            configured.pop(field, None)
    if actual.get('configured_parameters') != configured:
        raise ValueError('Parameter rerun native options differ from authorization')
    parameters = _request_parameters(provider, EvalCase(**case), expected, contract['timeout_seconds'])
    # Reporting does not load credentials or require the collection token.
    # Non-authentication environment options must match the frozen request.
    actual_parameters = {k: v for k, v in actual['request_parameters'].items() if k != 'headers_sha256'}
    expected_parameters = {k: v for k, v in parameters.items() if k != 'headers_sha256'}
    baseline_requests = (baseline or {}).get('provider_metadata', {}).get('native_route_receipt', {}).get('requests', [])
    baseline_parameters = baseline_requests[-1]['signature']['parameters'] if baseline_requests else {}
    def resolve_unchanged(value, previous):
        if isinstance(value, dict):
            return {k: resolve_unchanged(v, previous.get(k) if isinstance(previous, dict) else None)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_unchanged(v, previous[i] if isinstance(previous, list) and i < len(previous) else None)
                    for i, v in enumerate(value)]
        if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
            if previous is None:
                raise ValueError('Parameter rerun lacks baseline evidence for an unchanged environment option')
            return previous
        return value
    expected_parameters = resolve_unchanged(expected_parameters, baseline_parameters)
    if actual_parameters != expected_parameters:
        raise ValueError('Parameter rerun request differs from authorization')
    return {'provider_type': actual['provider_type'], 'transport': actual['transport'],
            'endpoint_sha256': actual.get('endpoint_sha256'), 'request': actual_parameters,
            'input_class': list(key[1:]),
            'dns_mode': config.get('dns_mode', 'system'),
            'timeout_seconds': contract['timeout_seconds'],
            'max_failure_retries': contract.get('max_failure_retries')}


def _uniform_execution_settings(calls):
    """Require uniform requests per input class and a shared call budget."""
    def signature(value):
        def normalize(item):
            if isinstance(item, dict):
                return {key: normalize(child) for key, child in item.items()}
            if isinstance(item, list):
                return [normalize(child) for child in item]
            if isinstance(item, float) and item.is_integer():
                return int(item)
            return item
        return json.dumps(normalize(value), sort_keys=True, allow_nan=False)
    by_class, shared = {}, set()
    for attempts in calls.values():
        if not attempts:
            raise ValueError('Whole-group selection lacks execution settings')
        for settings in attempts:
            if not isinstance(settings, dict) or not settings:
                raise ValueError('Whole-group selection lacks execution settings')
            input_class = tuple(settings.get('input_class', ()))
            by_class.setdefault(input_class, set()).add(signature(settings))
            shared.add(signature([settings.get(key) for key in
                                  ('dns_mode', 'timeout_seconds', 'max_failure_retries')]))
    return len(shared) == 1 and all(len(values) == 1 for values in by_class.values())


def _matrix_execution_settings(inputs, candidates, decisions, adoptions):
    """Audit every displayed group, including unchanged and completion-only groups.

    Native routes may differ by declared input class. Budgets and DNS must still
    agree across methods in a family; product-specific request options need not.
    Unknown historical settings remain unknown, never an implicit exemption.
    """
    spec, sources = inputs['spec'], inputs['sources']
    groups = {}
    for key, original in inputs['selected'].items():
        suite, provider, cid = key
        case = inputs['cases'][suite][cid]
        family = (_presentation_family(case, suite, spec['report_families'])
                  if spec.get('report_families') else suite)
        if (provider in spec.get('excluded_report_providers', []) or
                family in spec.get('excluded_report_families', [])):
            continue
        selected = original
        continuation = inputs['continuations'].get(key)
        if key in decisions and decisions[key]['selected_new']:
            selected = candidates[key]
            continuation = inputs.get('parameter_continuations', {}).get(key)
        if key in adoptions:
            selected = adoptions[key]['original']
            case = adoptions[key]['before_case']
            continuation = adoptions[key]['continuations'].get(key)
        root, record = selected[:2]
        group = groups.setdefault((provider, family), dict(
            provider=provider, family=family, calls=0, verified_calls=0,
            settings=[], errors=[], collection_start=None, collection_end=None))
        calls = [(root, record)]
        policies = []
        if continuation:
            if continuation[1] is None:
                group['calls'] += 1
                started = continuation[0]['_uncommitted_started_at']
                group['collection_start'] = min(group['collection_start'] or started, started,
                                                key=datetime.fromisoformat)
                group['errors'].append('A physical call has no saved native settings')
            else:
                calls.append(continuation[1][:2])
        for index, (call_root, call) in enumerate(calls):
            physical = call.get('provider_metadata', {}).get('retry', {}).get('attempts') or [call]
            started_physical = [attempt for attempt in physical
                if (attempt.get('native_route_receipt') or
                    attempt.get('provider_metadata', {}).get('native_route_receipt', {})).get('transport_state') == 'started']
            if len(started_physical) != len(physical):
                error = 'Saved attempts include pre-transport rejections or unverified transport'
                if error not in group['errors']:
                    group['errors'].append(error)
            physical = started_physical
            group['calls'] += len(physical)
            for attempt in physical:
                started = attempt.get('started_at')
                if started:
                    group['collection_start'] = min(group['collection_start'] or started, started,
                                                    key=datetime.fromisoformat)
                    ends = []
                    if isinstance(attempt.get('elapsed_ms'), (int, float)):
                        ends.append(datetime.fromisoformat(started) + timedelta(milliseconds=attempt['elapsed_ms']))
                    if attempt.get('completed_at'):
                        ends.append(datetime.fromisoformat(attempt['completed_at']))
                    if ends:
                        end = max(ends).isoformat()
                        group['collection_end'] = max(group['collection_end'] or end, end,
                                                      key=datetime.fromisoformat)
            try:
                manifest = _native_execution_manifest(call_root, call, sources)
                split_role = ('current' if index == 0 else 'prior') if continuation else None
                policy = {'provider_config_sha256': manifest.get('provider_config_sha256'),
                          'created_at': manifest.get('created_at', manifest.get('started_at')),
                          'timeout_seconds': manifest['parameters']['timeout'],
                          'max_failure_retries': _saved_retry_budget(manifest, split_role)}
                if (type(policy['max_failure_retries']) is not int or
                        policy['max_failure_retries'] < 0 or not policy['created_at']):
                    raise ValueError('Missing retry budget')
                settings = _parameter_execution(call_root, call, case, policy, sources, call, split_role)
                policies.append(policy)
                group['verified_calls'] += len(physical)
                if settings not in group['settings']:
                    group['settings'].append(settings)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                # Do not expose private paths or configuration values in the report.
                error = 'Native execution settings could not be verified (' + type(exc).__name__ + ')'
                if error not in group['errors']:
                    group['errors'].append(error)
        if len(policies) == len(calls):
            policy = {**policies[0], 'created_at': min((p['created_at'] for p in policies),
                                                     key=datetime.fromisoformat)}
            try:
                _validate_call_sequence(record, case, policy, continuation, require_saved_settings=True)
            except (ValueError, KeyError):
                group['errors'].append('Physical call sequence differs from its frozen retry policy')
    for (suite, provider, cid), (entry, receipt) in inputs['missing'].items():
        if entry['status'] != 'uncommitted':
            continue
        case = inputs['cases'][suite][cid]
        family = (_presentation_family(case, suite, spec['report_families'])
                  if spec.get('report_families') else suite)
        if (provider in spec.get('excluded_report_providers', []) or
                family in spec.get('excluded_report_families', [])):
            continue
        group = groups.setdefault((provider, family), dict(
            provider=provider, family=family, calls=0, verified_calls=0,
            settings=[], errors=[], collection_start=None, collection_end=None))
        evidence = [_json(receipt)]
        validated = inputs['uncommitted_execution'].get((suite, provider, cid))
        if validated:
            evidence.append(dict(suite=suite, provider=provider, case_id=cid, **validated))
        for binding in entry.get('diagnostic_sources', []):
            raw = sources.binding(binding).read_bytes()
            try:
                evidence.append(json.loads(raw))
            except json.JSONDecodeError:
                evidence.append([json.loads(line) for line in raw.splitlines() if line.strip()])
        observations = []
        _receipt_covers(evidence, suite, provider, cid, 'uncommitted', observations=observations)
        starts = {}
        bounds = {'collection_start': [], 'collection_end': []}
        counts = []
        for node in observations:
            count = node.get('physical_calls')
            if type(count) is int and count > 0:
                counts.append(count)
            start = node.get('started_at') or (node.get('at') if node.get('event') == 'http_request_started' else None)
            if node.get('event') == 'http_request_started':
                identity = node.get('call_id')
                if identity is not None:
                    if identity in starts and starts[identity] != start:
                        raise ValueError('Conflicting request starts for one invocation identity')
                    starts[identity] = start
                else:
                    group['errors'].append('Uncommitted request start lacks invocation identity')
            for field, value in [('collection_start', start), ('collection_end', node.get('completed_at'))]:
                if value is None:
                    continue
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None:
                        raise ValueError('Missing timezone')
                    bounds[field].append(value)
                except (TypeError, ValueError):
                    group['errors'].append('Uncommitted call has an invalid collection timestamp')
        if len(set(counts)) > 1 or (counts and len(starts) > counts[0]):
            raise ValueError('Uncommitted physical-call count conflicts with invocation evidence')
        group['calls'] += counts[0] if counts else max(1, len(starts))
        group['errors'].append('Uncommitted invocation has no verified native settings')
        if not counts:
            group['errors'].append('Uncommitted physical-call count is a lower bound')
        for field, values in bounds.items():
            if not values:
                group['errors'].append('Uncommitted invocation lacks ' + field)
                continue
            if group[field]:
                values.append(group[field])
            select = min if field == 'collection_start' else max
            group[field] = select(values, key=datetime.fromisoformat)
    for group in groups.values():
        group['uniform'] = bool(group['settings']) and not group['errors'] and (
            _uniform_execution_settings({'group': group['settings']}))
    return list(groups.values())


def _require_comparable_matrix(settings, expected_groups=None):
    """A final comparison cannot hide unknown settings or unequal shared budgets."""
    observed = {(group['provider'], group['family']) for group in settings}
    if expected_groups is not None and (observed != expected_groups or len(observed) != len(settings)):
        raise ValueError('Final report execution settings must cover every displayed method/family')
    if not settings or any(not group['uniform'] or group['errors'] or
                          group['verified_calls'] != group['calls'] or group['calls'] <= 0
                          for group in settings):
        raise ValueError('Final report requires verified uniform settings for every method/family')
    families = {}
    for group in settings:
        by_class = {}
        if not group['settings']:
            raise ValueError('Final report lacks observed execution settings')
        for value in group['settings']:
            if (type(value.get('max_failure_retries')) is not int or value['max_failure_retries'] < 0
                    or type(value.get('timeout_seconds')) not in (int, float)
                    or not math.isfinite(value['timeout_seconds']) or value['timeout_seconds'] <= 0
                    or not isinstance(value.get('dns_mode'), str) or not value['dns_mode']):
                raise ValueError('Final report has invalid shared execution settings')
            by_class.setdefault(tuple(value['input_class']), []).append(value)
            families.setdefault(group['family'], []).append({key: value[key] for key in
                ('dns_mode', 'timeout_seconds', 'max_failure_retries')})
        if any(not _uniform_execution_settings({'class': values}) for values in by_class.values()):
            raise ValueError('Final report has nonuniform native request settings')
    if any(not _uniform_execution_settings({'family': values}) for values in families.values()):
        raise ValueError('Final report requires equal DNS, timeout and retry budgets across methods')


def _same_task_rules(before, after):
    """Compare executable task requirements, not diagnostic-only annotations."""
    from harness.web_fetch.scoring_revision import ALLOWED_FIELDS
    # diagnostic_any produces diagnostic_hits only; it never changes acceptance.
    fields = (ALLOWED_FIELDS | {'acceptance_policy_version'}) - {'diagnostic_any'}
    return all(before.get(field) == after.get(field) for field in fields)


def _require_final_selection(inputs):
    """Keep post-outcome tuning and changed task contracts out of a primary ranking."""
    spec = inputs['spec']
    def displayed(key):
        suite, provider, cid = key
        family = (_presentation_family(inputs['cases'][suite][cid], suite, spec['report_families'])
                  if spec.get('report_families') else suite)
        return (provider not in spec.get('excluded_report_providers', []) and
                family not in spec.get('excluded_report_families', []))
    if any(displayed(key) for key in inputs['missing']):
        raise ValueError('Final report requires complete judged call evidence')
    if spec.get('parameter_reruns') or spec.get('adoptions'):
        raise ValueError('Post-outcome parameter selection belongs in a separate audit report')
    revision = inputs['revision']
    if revision and any(not _same_task_rules(inputs['cases'][suite][cid], case)
                        for (suite, cid), case in revision['cases'].items()):
        raise ValueError('Changed task contracts belong in a separate audit report, not the primary ranking')


def _continuation_execution_cost(*records):
    """Sum complete saved sequence costs, never just the last physical calls."""
    from harness.web_fetch.report import logical_execution_cost_ms
    costs = [logical_execution_cost_ms(record) for record in records]
    return sum(costs) if all(cost is not None for cost in costs) else None


def _require_common_scoring(records, message):
    """Compare saved scorer identities after any validated offline revision."""
    identities = [row['metrics'].get('scoring_source_sha256') for row in records]
    if (not identities or any(not isinstance(value, str) or len(value) != 64
            or any(char not in '0123456789abcdef' for char in value) for value in identities)
            or len(set(identities)) != 1):
        raise ValueError(message)


def _parameter_scoring(before, record):
    """Require an explicit offline revision before comparing different scorers."""
    _require_common_scoring([before, record], 'Parameter comparison needs an explicit scoring revision')


def _validate_call_sequence(record, case, contract, continuation=None, *, require_saved_settings=False):
    """Validate the complete authorized physical-call sequence."""
    allowance = contract.get('max_failure_retries')
    if type(allowance) is not int or allowance < 0:
        raise ValueError('Execution authorization requires an explicit nonnegative retry allowance')
    retry = record.get('provider_metadata', {}).get('retry', {})
    calls = retry.get('attempts') or [record]
    uncommitted_calls = 0
    if retry and (retry.get('physical_attempts') != len(calls)
                  or retry.get('retries_used') != len(calls) - 1):
        raise ValueError('Parameter rerun has inconsistent physical call counts')
    if continuation:
        entry, prior, _ = continuation
        if prior is None:
            if require_saved_settings:
                raise ValueError('Configuration selection requires saved native settings for every physical call')
            started = entry.get('_uncommitted_started_at')
            if not started:
                raise ValueError('Parameter rerun needs timestamped first-call evidence')
            if datetime.fromisoformat(started) < datetime.fromisoformat(contract['created_at']):
                raise ValueError('Parameter rerun predates its contract')
            # The loader has verified the single-call log and continuation order.
            # Count the invocation without inventing its score or elapsed time.
            uncommitted_calls = 1
        else:
            calls = [prior[1], *calls]
    if any(datetime.fromisoformat(call['started_at']) < datetime.fromisoformat(contract['created_at'])
           for call in [record, *calls]):
        raise ValueError('Parameter rerun predates its contract')
    passes = []
    for call in calls:
        if 'metrics' in call:
            passed = task_passed(call['ok'], call['metrics'], case.get('task_kind', 'body'))
            if 'task_pass' in call and call['task_pass'] is not passed:
                raise ValueError('Physical call verdict differs from its scoring metrics')
        else:
            passed = call.get('task_pass')
        if type(passed) is not bool:
            raise ValueError('Physical call requires an explicit boolean verdict')
        passes.append(passed)
    physical_count = len(calls) + uncommitted_calls
    if any(passes[:-1]) or (not passes[-1] and physical_count <= contract['max_failure_retries']):
        raise ValueError('Parameter rerun violates stop-on-pass or requires its failure retry')
    if retry.get('attempts') and (calls[-1].get('content_sha256') != record['metrics']['content_sha256']
            or calls[-1].get('elapsed_ms') != record['elapsed_ms']
            or passes[-1] != task_passed(record['ok'], record['metrics'], case.get('task_kind', 'body'))):
        raise ValueError('Parameter rerun final physical call differs from selected result')
    for first, second in zip(calls, calls[1:], strict=False):
        first_end = datetime.fromisoformat(first['started_at']) + timedelta(milliseconds=first['elapsed_ms'])
        if first.get('completed_at'):
            first_end = max(first_end, datetime.fromisoformat(first['completed_at']))
        if first_end > datetime.fromisoformat(second['started_at']):
            raise ValueError('Parameter rerun physical calls overlap or are out of order')
    if physical_count - 1 > contract['max_failure_retries']:
        raise ValueError('Parameter rerun exceeds retry allowance')


_PARAMETER_SELECTIONS = {'replace_track_if_pass_count_improves',
                         'replace_family_if_pass_count_improves'}


def _parameter_reruns(inputs):
    """Validate complete declared reruns before applying an explicit retention policy."""
    config = inputs['spec'].get('parameter_reruns')
    if not config:
        return {}
    sources = inputs['sources']
    contract = _json(sources.binding(config['contract']))
    if (contract.get('selection') is not None and contract['selection'] != config.get('selection')
            and not config.get('selection_amendment')):
        raise ValueError('Changing a frozen parameter selection requires an amendment')
    if config.get('selection_amendment'):
        amendment = _json(sources.binding(config['selection_amendment']))
        if amendment.get('selection') != config.get('selection'):
            raise ValueError('Parameter selection amendment differs from report policy')
    if config.get('selection') not in _PARAMETER_SELECTIONS:
        raise ValueError('Unsupported parameter rerun selection policy')
    jobs = contract['jobs']
    if len({job['id'] for job in jobs}) != len(jobs):
        raise ValueError('Duplicate parameter rerun job ID')
    if any(len(job['case_ids']) != len(set(job['case_ids'])) for job in jobs):
        raise ValueError('Duplicate parameter rerun contract case ID')
    declared = {job['id']: job for job in jobs}
    candidates = {}
    covered = {name: set() for name in declared}
    for source in config['sources']:
        job = declared[source['job_id']]
        source_ids = source.get('case_ids', job['case_ids'])
        if len(source_ids) != len(set(source_ids)) or not set(source_ids) <= set(job['case_ids']):
            raise ValueError('Parameter rerun source exceeds declared population')
        if source.get('saved_matrix_spec'):
            nested = _load_saved_matrix(sources.binding(source['saved_matrix_spec']))
            if nested['missing'] or nested['revision'] or nested['historical']:
                raise ValueError('Parameter candidate view must contain complete unmodified calls')
            if set(nested['selected']) != {(job['suite'], job['provider'], cid) for cid in source_ids}:
                raise ValueError('Parameter candidate view differs from declared population')
            for path, digest in nested['sources'].tracked.items():
                sources.bind(path, digest)
            inputs.setdefault('parameter_continuations', {}).update(nested['continuations'])
            cases = nested['cases'][job['suite']]
            source_rows = list(nested['selected'].values())
        else:
            root = _path(sources.base, source['root'])
            dataset = _json(sources.binding(source['dataset']))
            path = sources.binding(source['results'])
            cases = _index_cases(dataset)
            source_rows = [(root, json.loads(line), line, path)
                           for line in path.read_bytes().splitlines(keepends=True) if line.strip()]
        seen = set()
        for root, record, line, path in source_rows:
            cid = record['case_id']
            if cid not in source_ids:
                continue
            key = job['suite'], job['provider'], cid
            if (cid in seen or key in candidates
                    or record['provider'] != job['provider'] or key not in inputs['selected']):
                raise ValueError('Parameter rerun differs from declared population')
            seen.add(cid)
            original_case = inputs['cases'][job['suite']][cid]
            revision = inputs['revision']
            case = revision['cases'].get((job['suite'], cid), original_case) if revision else original_case
            if _case_identity(cases[cid]) != _case_identity(case) or record['url'] != case['url']:
                raise ValueError('Parameter rerun changed task rules')
            continuation = inputs.get('parameter_continuations', {}).get(key)
            _validate_call_sequence(record, case, contract, continuation, require_saved_settings=True)
            verify_record_artifacts(root, record, native_cases=cases)
            for relative, digest in artifact_bindings(record):
                sources.bind(contained_path(root, relative, reject_symlinks=True), digest)
            for item in record_private_bindings(record):
                sources.bind(contained_path(root, item['path'], reject_symlinks=True), item['sha256'])
            _, before, _, _ = inputs['selected'][key]
            if revision:
                before = revision['records'].get(key, before)
            candidate_settings = [_parameter_execution(root, record, cases[cid], contract, sources, before,
                                                       'current' if continuation else None)]
            if continuation and continuation[1] is not None:
                prior_root, prior_record, _ = continuation[1]
                candidate_settings.append(_parameter_execution(prior_root, prior_record, cases[cid], contract, sources, before, 'prior'))
            if config['selection'] == 'replace_family_if_pass_count_improves':
                baseline_settings = []
                baseline_policies = []
                baseline_calls = [inputs['selected'][key][:2]]
                prior = inputs.get('continuations', {}).get(key)
                if prior and prior[1] is None:
                    raise ValueError('Configuration selection requires saved native settings for every physical call')
                if prior and prior[1] is not None:
                    baseline_calls.append(prior[1][:2])
                for index, (old_root, old_record) in enumerate(baseline_calls):
                    manifest = _native_execution_manifest(old_root, old_record, sources)
                    split_role = ('current' if index == 0 else 'prior') if prior else None
                    policy = {'provider_config_sha256': manifest.get('provider_config_sha256'),
                              'created_at': manifest.get('created_at', manifest.get('started_at')),
                              'timeout_seconds': manifest['parameters']['timeout'],
                              'max_failure_retries': _saved_retry_budget(manifest, split_role)}
                    baseline_policies.append(policy)
                    baseline_settings.append(_parameter_execution(old_root, old_record, original_case,
                                                                  policy, sources, old_record, split_role))
                try:
                    for (_, old_record), policy in zip(baseline_calls, baseline_policies, strict=True):
                        created = datetime.fromisoformat(policy['created_at'])
                        attempts = old_record.get('provider_metadata', {}).get('retry', {}).get('attempts') or [old_record]
                        if any(datetime.fromisoformat(attempt['started_at']) < created for attempt in [old_record, *attempts]):
                            raise ValueError('Baseline call predates its saved policy')
                    sequence_policy = {**baseline_policies[0],
                        'created_at': min((policy['created_at'] for policy in baseline_policies),
                                          key=datetime.fromisoformat)}
                    _validate_call_sequence(baseline_calls[0][1], original_case, sequence_policy, prior,
                                            require_saved_settings=True)
                    baseline_retry_policy_valid = True
                except (ValueError, KeyError, TypeError):
                    baseline_retry_policy_valid = False
                inputs.setdefault('parameter_settings', {})[key] = {
                    'before': baseline_settings, 'candidate': candidate_settings,
                    'before_retry_policy_valid': baseline_retry_policy_valid}
            _parameter_scoring(before, record)
            candidates[key] = root, record, line, path
        if seen != set(source_ids):
            raise ValueError('Incomplete parameter rerun cohort')
        covered[source['job_id']].update(seen)
    if any(covered[name] != set(job['case_ids']) for name, job in declared.items()):
        raise ValueError('Incomplete parameter rerun job inventory')
    return candidates


def _parameter_group_decisions(inputs, candidates):
    """Use the authorized partition without changing frozen historical track policies."""
    groups, membership = {}, {}
    revision = inputs['revision']
    spec = inputs.get('spec', {})
    by_family = spec.get('parameter_reruns', {}).get('selection') == 'replace_family_if_pass_count_improves'
    if by_family and not spec.get('report_families'):
        raise ValueError('Family parameter selection requires the report family registry')
    def group_for(key):
        suite, provider, cid = key
        case = inputs['cases'][suite][cid]
        if revision:
            case = revision['cases'].get((suite, cid), case)
        family = _presentation_family(case, suite, spec['report_families']) if by_family else case.get('track', 'web')
        return provider, family
    for key, (_, candidate, _, _) in candidates.items():
        suite, provider, cid = key
        original_case = inputs['cases'][suite][cid]
        case = revision['cases'].get((suite, cid), original_case) if revision else original_case
        group = group_for(key)
        if by_family and group[1] in spec.get('excluded_report_families', []):
            raise ValueError('Family parameter selection includes an excluded report family')
        membership[key] = group
        counts = groups.setdefault(group, {'provider': provider, 'family' if by_family else 'track': group[1],
                                          'count': 0, 'before_passed': 0, 'candidate_passed': 0})
        before = inputs['selected'][key][1]
        if revision:
            before = revision['records'].get(key, before)
        counts['count'] += 1
        for label, record in [('before_passed', before), ('candidate_passed', candidate)]:
            counts[label] += bool(task_passed(record['ok'], record['metrics'], case.get('task_kind', 'body')))
    for group in groups:
        population = set(inputs['selected']) | set(inputs.get('missing', {}))
        if by_family:
            population = {(suite, group[0], cid) for suite, cases in inputs['cases'].items() for cid in cases}
        expected = {key for key in population if group_for(key) == group}
        if expected != {key for key, value in membership.items() if value == group}:
            raise ValueError('Parameter selection must cover the complete method/' + ('family' if by_family else 'track') + ' group')
    for group, counts in groups.items():
        counts['selected_new'] = counts['candidate_passed'] > counts['before_passed']
        if by_family:
            keys = [key for key, value in membership.items() if value == group]
            uniform = {label: _uniform_execution_settings({
                key: inputs.get('parameter_settings', {}).get(key, {}).get(label, []) for key in keys})
                for label in ('before', 'candidate')}
            baseline_retry_valid = all(inputs.get('parameter_settings', {}).get(key, {}).get('before_retry_policy_valid') is True
                                       for key in keys)
            eligible = {**uniform, 'before': uniform['before'] and baseline_retry_valid}
            if not any(eligible.values()):
                raise ValueError('Neither complete report family has eligible native settings and retry history')
            counts['before_uniform'] = uniform['before']
            counts['candidate_uniform'] = uniform['candidate']
            counts['before_retry_policy_valid'] = baseline_retry_valid
            counts['selected_new'] = eligible['candidate'] and (
                not eligible['before'] or counts['selected_new'])
    return {key: groups[group] for key, group in membership.items()}


def _validate_completion_retention(inputs, candidates, decisions=None):
    """Preserve every baseline call outside separately authorized completion scopes."""
    spec, sources = inputs['spec'], inputs['sources']
    decisions = _parameter_group_decisions(inputs, candidates) if decisions is None else decisions
    configs = ([spec['completion']] if spec.get('completion') else []) + spec.get('completions', [])
    completed = _completed_missing(spec, sources, inputs['selected'], inputs['continuations'])
    derived = {'metrics', 'scoring', 'diagnosis', 'failure_category'} if inputs['revision'] else set()
    def call_identity(record):
        return {key: value for key, value in record.items() if key not in derived}
    for config in configs:
        matrix_path = sources.binding(config['baseline_matrix'])
        audit_path = sources.binding(config['baseline_audit'])
        audit = _json(audit_path)
        if audit['files'].get('matrix.json') != _sha(matrix_path):
            raise ValueError('Completion baseline matrix differs from its bound audit')
        def evidence(relative, audit_path=audit_path, audit=audit):
            return _json(sources.bind(contained_path(audit_path.parent, relative, reject_symlinks=True),
                                      audit['files'][relative]))
        for row in _json(matrix_path)['rows']:
            key = row['suite'], row['provider'], row['case_id']
            original_case = inputs['cases'][key[0]][key[2]]
            allowed_cases = [original_case]
            if inputs['revision']:
                allowed_cases.append(inputs['revision']['cases'].get((key[0], key[2]), original_case))
            if _case_identity(evidence(row['case_path'])) not in [_case_identity(c) for c in allowed_cases]:
                raise ValueError('Completion changed an undeclared task')
            if key in completed:
                continue
            if not row.get('record_path'):
                retained = inputs['missing'].get(key)
                if (retained is None or retained[0]['status'] != row['status']
                        or _json(retained[1]) != evidence(row['receipt_path'])):
                    raise ValueError('Completion changed a retained invocation state or receipt')
                continue
            previous = call_identity(evidence(row['record_path']))
            final = inputs['selected'].get(key)
            if key in decisions and decisions[key]['selected_new']:
                final = candidates[key]
            if final is not None and previous == call_identity(final[1]):
                continue
            authorized_selection = (key in candidates and
                spec.get('parameter_reruns', {}).get('selection') in _PARAMETER_SELECTIONS)
            alternatives = [inputs['selected'][key][1], candidates[key][1]] if authorized_selection else []
            if previous not in [call_identity(record) for record in alternatives]:
                raise ValueError('Completion replaced a retained baseline call: ' + '/'.join(key))


def _adopted_groups(inputs, candidates):
    """Adopt explicitly selected complete report families, including regressions."""
    spec, sources = inputs['spec'], inputs['sources']
    adopted = {}
    for index, config in enumerate(spec.get('adoptions', [])):
        authority_path = sources.binding(config['authority'])
        authority = _json(authority_path)
        if authority.get('kind') != 'user_selected_complete_report_groups':
            raise ValueError('Adoption requires explicit whole-group authority')
        groups = [(group['provider'], group['family']) for group in authority['groups']]
        if len(set(groups)) != len(groups) or not groups:
            raise ValueError('Adoption groups are empty or duplicated')
        expected = set()
        for provider, family in groups:
            group = {(suite, provider, cid) for suite, cases in inputs['cases'].items()
                     for cid, case in cases.items() if provider in inputs['providers']
                     and _presentation_family(case, suite, spec['report_families']) == family
                     and family not in spec.get('excluded_report_families', [])}
            if not group:
                raise ValueError('Adoption names an unknown or excluded group')
            expected.update(group)
        nested = _load_saved_matrix(sources.binding(config['saved_matrix_spec']))
        if (nested['missing'] or nested['historical'] or nested['spec'].get('adoptions')
                or nested['spec'].get('parameter_reruns') or set(nested['selected']) != expected
                or expected & (set(adopted) | set(candidates))):
            raise ValueError('Adoption must replace complete, disjoint groups')
        policies = {}
        if config.get('execution_contract'):
            policies = dict.fromkeys(expected, _execution_policy(config['execution_contract'], sources))
        for cohort in config.get('execution_contracts', []):
            identifiers = cohort['case_ids']
            scope = {(cohort['suite'], cohort['provider'], cid) for cid in identifiers}
            if len(scope) != len(identifiers) or not scope <= expected or scope & set(policies):
                raise ValueError('Adoption execution scopes overlap or exceed the selected group')
            policies.update(dict.fromkeys(scope, _execution_policy(cohort['contract'], sources)))
        if set(policies) != expected:
            raise ValueError('Adoption requires execution authorization for every selected call')
        settings_by_group = {}
        for key, original in nested['selected'].items():
            suite, provider, cid = key
            case = inputs['cases'][suite][cid]
            if inputs['revision']:
                case = inputs['revision']['cases'].get((suite, cid), case)
            source_case = nested['cases'][suite][cid]
            if nested['revision']:
                source_case = nested['revision']['cases'].get((suite, cid), source_case)
            if _case_identity(case) != _case_identity(source_case):
                raise ValueError('Adoption changes the task definition')
            root, record, raw, results = original
            before_score = record
            if nested['revision'] and key in nested['revision']['records']:
                record = nested['revision']['records'][key]
                raw = (json.dumps(record, ensure_ascii=False) + '\n').encode()
            before = inputs['selected'][key][1]
            if inputs['revision']:
                before = inputs['revision']['records'].get(key, before)
            _parameter_scoring(before, record)
            if any(record.get('provider_metadata', {}).get(name) != value
                   for name, value in authority.get('required_metadata', {}).items()):
                raise ValueError('Adoption differs from required settings')
            policy = policies[key]
            execution_case = nested['cases'][suite][cid]
            # Retry decisions belong to the frozen acquisition score, not to a
            # later offline revision of that saved answer.
            _validate_call_sequence(before_score, execution_case, policy, nested['continuations'].get(key),
                                    require_saved_settings=True)
            group = provider, _presentation_family(case, suite, spec['report_families'])
            settings = settings_by_group.setdefault(group, {}).setdefault(key, [])
            continuation = nested['continuations'].get(key)
            settings.append(_parameter_execution(root, before_score, execution_case, policy, sources, before,
                                                  'current' if continuation else None))
            if continuation and continuation[1]:
                prior_root, prior_record, _ = continuation[1]
                settings.append(_parameter_execution(prior_root, prior_record, execution_case, policy, sources, before, 'prior'))
            adopted[key] = {'selected': (root, record, raw, results), 'original': original,
                            'authority_path': authority_path, 'index': index,
                            'note': authority['description'],
                            'continuations': nested['continuations'],
                            'before_case': nested['cases'][suite][cid],
                            'attachments': nested['revision']['attachments'] if nested['revision'] else {},
                            'revision': nested['revision']['receipt'] if record != before_score else None}
        if authority.get('configuration_exceptions'):
            raise ValueError('Configuration exceptions cannot establish uniform execution settings')
        for settings in settings_by_group.values():
            if not _uniform_execution_settings(settings):
                raise ValueError('Adoption mixes native settings within a report family')
        for path, digest in nested['sources'].tracked.items():
            sources.bind(path, digest)
    return adopted


def _bound_native_registry(base, binding):
    if binding is None:
        return None
    if not isinstance(binding, dict) or set(binding) != {'path', 'sha256'}:
        raise ValueError('Native policy registry requires an explicit path/hash binding')
    path = _path(base, binding['path'])
    if _sha(path) != binding['sha256']:
        raise ValueError('Native policy registry differs from its approved binding')
    return _json(path)


def build_saved_matrix(spec_path: Path, output: Path) -> Path:
    """Build with only the historical policies explicitly approved by the caller."""
    from harness.web_fetch.native_projection import approved_native_policies
    spec_path = Path(spec_path)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Saved-matrix output must be a new directory')
    registry = _bound_native_registry(spec_path.parent, _json(spec_path).get('native_policy_registry'))
    output.parent.mkdir(parents=True, exist_ok=True)
    with approved_native_policies(registry), tempfile.TemporaryDirectory(
            dir=output.parent, prefix='.' + output.name + '-build-') as temporary:
        staged = Path(temporary) / 'report'
        entry = _build_saved_matrix(spec_path, staged)
        if output.exists() or output.is_symlink():
            raise ValueError('Saved-matrix output must be a new directory')
        staged.rename(output)
        return output / entry.relative_to(staged)


def _build_saved_matrix(spec_path: Path, output: Path) -> Path:
    """Verify immutable sources and create a new portable report directory."""
    from harness.web_fetch.report import generate_saved_matrix

    if 'saved_reports' in _json(spec_path):
        from harness.web_fetch.report_saved_composition import build_composition
        return build_composition(spec_path, output)

    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Saved-matrix output must be a new directory')
    inputs = _load_saved_matrix(spec_path)
    candidates = _parameter_reruns(inputs)
    decisions = _parameter_group_decisions(inputs, candidates)
    _validate_completion_retention(inputs, candidates, decisions)
    adoptions = _adopted_groups(inputs, candidates)
    spec = inputs['spec']
    execution_settings = _matrix_execution_settings(inputs, candidates, decisions, adoptions)
    if spec.get('presentation') == 'final':
        _require_final_selection(inputs)
        _require_comparable_matrix(execution_settings)
    sources = inputs['sources']
    bind, binding, tracked = sources.bind, sources.binding, sources.tracked
    spec_path = sources.base / Path(spec_path).name
    providers = [p for p in inputs['providers'] if p not in spec.get('excluded_report_providers', [])]
    suites, cases = inputs['suites'], inputs['cases']
    selected, historical = inputs['selected'], inputs['historical']
    continuations, missing = inputs['continuations'], inputs['missing']
    replacement_scope, cells = inputs['replacement_scope'], inputs['replacement_cells']
    revision = inputs['revision']
    copies = {}

    def copy(source, relative):
        source = bind(source)
        target = contained_path(output, relative, reject_symlinks=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != tracked[source]:
            raise ValueError('Source changed while copying')
        _write_bytes(target, raw)
        copies[relative] = tracked[source]
        return relative

    output.mkdir(parents=True, exist_ok=False)
    if revision:
        for name, path in revision['attachments'].items():
            copy(path, 'review-assets/'+name+'.json')
        if spec.get('excluded_report_families') or spec.get('excluded_report_providers'):
            _write(output/'score-revision-history.json', revision['receipt'])
            visible = {(suite, cid) for suite, group in cases.items() for cid, case in group.items()
                       if not spec.get('excluded_report_families') or
                       _presentation_family(case, suite, spec['report_families'])
                       not in spec['excluded_report_families']}
            routes = {suite+'--'+cid for suite, cid in visible}
            attempts = {suite+'--'+record['attempt_id']
                        for (suite, provider, cid), (_, record, _, _) in selected.items()
                        if (suite, cid) in visible and provider in providers}
            revision = {**revision, 'receipt': deepcopy(revision['receipt'])}
            receipt = revision['receipt']
            receipt['historical_scope_path'] = 'score-revision-history.json'
            receipt['changed_cases'] = [cid for cid in receipt['changed_cases'] if cid in routes]
            receipt['rescored_results'] = sum((suite, cid) in visible and
                provider not in spec.get('excluded_report_providers', [])
                for suite, provider, cid in revision['records'])
            for step in receipt['steps']:
                step['task_verdict_changes'] = [change for change in step['task_verdict_changes']
                                                if change['attempt_id'] in attempts]
            if 'decisions' in revision['attachments']:
                review_decisions = _json(revision['attachments']['decisions'])
                scoped = [item for item in review_decisions if (item['suite'], item['case_id']) in visible]
                _write(output/'report-review-assets/decisions.json', scoped)
                receipt['review_files']['decisions'] = 'report-review-assets/decisions.json'
        _write(output/'score-revision.json', revision['receipt'])
    if replacement_scope:
        _write(output / 'replacement-scope.json', {
            'kind': 'predeclared_replacement_scope_public_view',
            'original_sha256': tracked[replacement_scope],
            'cells': [{'suite': x['suite'], 'provider': x['provider'], 'case_id': x['case_id'],
                       'old_record_sha256': x['old_record']['sha256']} for x in cells]})
    completion_notes = {}
    for config in ([spec['completion']] if spec.get('completion') else []) + spec.get('completions', []):
        if config.get('note'):
            for key in _completed_scope(config, inputs['sources'], selected, continuations):
                completion_notes[key] = config['note']
    model_rows = []
    for suite in suites:
        for case_id, original_case in cases[suite].items():
            if (spec.get('excluded_report_families') and
                    _presentation_family(original_case, suite, spec['report_families'])
                    in spec['excluded_report_families']):
                continue
            case = revision['cases'].get((suite, case_id), original_case) if revision else original_case
            for provider in providers:
                key = (suite, provider, case_id)
                prefix = f'evidence/{suite}/{provider}/{case_id}'
                case_path = prefix + '/case.json'
                _write(output / case_path, case)
                item = {'suite': suite, 'provider': provider, 'case_id': case_id,
                        'name': case.get('name', case_id), 'url': case['url'], 'checks': {},
                        'case_path': case_path, 'record_path': None, 'answer_path': None,
                        'log_path': None, 'receipt_path': None, 'elapsed_ms': None, 'retries': None}
                if key in completion_notes:
                    item['completion_note'] = completion_notes[key]
                if key in missing:
                    entry, receipt = missing[key]
                    _require_public_json(_json(receipt))
                    item.update(status=entry['status'], called=entry['status'] == 'uncommitted',
                                normal=False, passed=None, error=entry['reason'],
                                receipt_path=copy(receipt, prefix + '/receipt.json'))
                else:
                    root, record, raw_line, result_path = selected[key]
                    revised = bool(revision and key in revision['records'])
                    if revised:
                        _write(output/(prefix+'/score-before-case.json'), original_case)
                        _write_bytes(output/(prefix+'/score-before/score-before-record.json'), raw_line)
                        for relative, digest in artifact_bindings(record):
                            copy(contained_path(root, relative, reject_symlinks=True), prefix+'/score-before/'+relative)
                        item['score_revision'] = {'receipt_path':'score-revision.json',
                            'before_case_path':prefix+'/score-before-case.json',
                            'before_record_path':prefix+'/score-before/score-before-record.json'}
                        item['score_revision_history'] = deepcopy(item['score_revision'])
                        record = revision['records'][key]
                        raw_line = (json.dumps(record, ensure_ascii=False)+'\n').encode()
                    if key in candidates:
                        old = root, record, raw_line, result_path
                        candidate = candidates[key]
                        old_pass = task_passed(record['ok'], record['metrics'], case.get('task_kind', 'body'))
                        new_record = candidate[1]
                        new_pass = task_passed(new_record['ok'], new_record['metrics'], case.get('task_kind', 'body'))
                        replace = decisions[key]['selected_new']
                        proof = {'selected_new': replace, 'before_passed': old_pass,
                                 'candidate_passed': new_pass,
                                 'selection': spec['parameter_reruns']['selection'],
                                 'group': decisions[key]}
                        for label, evidence in [('before', old), ('candidate', candidate)]:
                            evidence_root, saved, saved_line, saved_path = evidence
                            relative = prefix + '/parameter-rerun/' + label
                            (output/relative).mkdir(parents=True, exist_ok=True)
                            _write_bytes(output/(relative+'/record.json'), saved_line)
                            for name, digest in artifact_bindings(saved):
                                copy(contained_path(evidence_root, name, reject_symlinks=True), relative+'/'+name)
                            proof[label+'_record_path'] = relative+'/record.json'
                            proof[label+'_answer_path'] = relative+'/'+saved['artifact_path']
                            proof[label+'_results_sha256'] = tracked[saved_path]
                        item['parameter_rerun'] = proof
                        if key in inputs.get('parameter_continuations', {}):
                            _, prior, _ = inputs['parameter_continuations'][key]
                            if prior:
                                prior_root, prior_record, prior_line = prior
                                relative = prefix+'/parameter-rerun/candidate-prior'
                                (output/relative).mkdir(parents=True, exist_ok=True)
                                _write_bytes(output/(relative+'/record.json'), prior_line)
                                for name, digest in artifact_bindings(prior_record):
                                    copy(contained_path(prior_root, name, reject_symlinks=True), relative+'/'+name)
                                proof['candidate_prior_record_path'] = relative+'/record.json'
                                proof['candidate_prior_answer_path'] = relative+'/'+prior_record['artifact_path']
                        if replace:
                            root, record, raw_line, result_path = candidate
                            revised = False
                            item.pop('score_revision', None)
                    if key in adoptions:
                        adoption = adoptions[key]
                        before_prefix = prefix + '/adoption-before'
                        (output/before_prefix).mkdir(parents=True, exist_ok=True)
                        before_path = before_prefix + '/record.json'
                        _write_bytes(output/before_path, raw_line)
                        for name, digest in artifact_bindings(record):
                            copy(contained_path(root, name, reject_symlinks=True), before_prefix+'/'+name)
                        authority_path = copy(adoption['authority_path'],
                                              f'adoption-authority-{adoption["index"]}.json')
                        item.setdefault('history', []).append({'record_path': before_path,
                            'answer_path': before_prefix+'/'+record['artifact_path'],
                            'scope_path': authority_path, 'attempt_id': record['attempt_id'],
                            'called': _called(record), 'started_at': record.get('started_at')})
                        root, record, raw_line, result_path = adoption['selected']
                        revised = bool(adoption['revision'])
                        item.pop('score_revision', None)
                        item['completion_note'] = adoption['note']
                        item['result_adoption'] = {'authority_path': authority_path,
                                                   'selection': 'complete_group_regardless_of_verdict'}
                        if revised:
                            receipt_path = prefix+'/adoption-score-revision.json'
                            adopted_receipt = deepcopy(adoption['revision'])
                            adopted_receipt['review_files'] = {
                                name: copy(path, f'adoption-review-assets/{adoption["index"]}/{name}.json')
                                for name, path in adoption['attachments'].items()}
                            _write(output/receipt_path, adopted_receipt)
                            _write(output/(prefix+'/adoption-before-case.json'), adoption['before_case'])
                            _write_bytes(output/(prefix+'/adoption-before-score.json'), adoption['original'][2])
                            item['score_revision'] = {'receipt_path': receipt_path,
                                'before_case_path': prefix+'/adoption-before-case.json',
                                'before_record_path': prefix+'/adoption-before-score.json'}
                    record_path = prefix + '/record.json'
                    _write_bytes(output / record_path, raw_line)
                    copies[record_path] = hashlib.sha256(raw_line).hexdigest()
                    for relative, digest in artifact_bindings(record):
                        copy(contained_path(root, relative, reject_symlinks=True), prefix + '/' + relative)
                    metadata = record.get('provider_metadata', {})
                    logs = record_public_call_log_bindings(record)
                    final_log = metadata.get('call_log') or (logs[-1] if logs else None)
                    item.update(**_saved_row_values(record, case), record_path=record_path,
                                answer_path=prefix + '/' + record['artifact_path'],
                                log_path=prefix + '/' + final_log['path'] if final_log else None,
                                receipt_path=prefix + '/receipt.json')
                    _write(output / item['receipt_path'], {
                        'kind': 'offline_revised_saved_result_view' if revised else 'immutable_saved_result_view', 'source_results_sha256': tracked[result_path],
                        'record_sha256': copies[record_path], 'suite': suite, 'provider': provider,
                        'case_id': case_id, 'public_artifacts': artifact_bindings(record),
                        'private_artifacts_verified_not_copied': record_private_bindings(record),
                        'scores_changed': revised, 'provider_calls_added': 0,
                        **({'score_revision': item['score_revision']} if revised else {})})
                if key in historical:
                    old_root, old_record, old_line, old_results = historical[key]
                    history_prefix = prefix + '/history/' + safe_identifier(old_record['attempt_id'])
                    history_record = history_prefix + '/record.json'
                    (output / history_record).parent.mkdir(parents=True, exist_ok=True)
                    _write_bytes(output / history_record, old_line)
                    copies[history_record] = hashlib.sha256(old_line).hexdigest()
                    for relative, digest in artifact_bindings(old_record):
                        copy(contained_path(old_root, relative, reject_symlinks=True), history_prefix + '/' + relative)
                    history_scope = 'replacement-scope.json'
                    item.setdefault('history', []).append({'record_path': history_record,
                        'answer_path': history_prefix + '/' + old_record['artifact_path'],
                        'scope_path': history_scope, 'attempt_id': old_record['attempt_id'],
                        'called': _called(old_record), 'started_at': old_record.get('started_at'),
                        'source_results_sha256': tracked[old_results],
                        'private_artifacts_verified_not_copied': record_private_bindings(old_record)})
                active_continuations = (inputs.get('parameter_continuations', {})
                                        if item.get('parameter_rerun', {}).get('selected_new') else continuations)
                if key in adoptions:
                    active_continuations = adoptions[key]['continuations']
                if key in active_continuations:
                    entry, prior, launch_path = active_continuations[key]
                    note = {'prior_physical_calls': 1, 'continued_physical_calls': 1,
                            'total_physical_calls': 2, 'max_failure_retries': 1,
                            'final_call_elapsed_ms': record['elapsed_ms'],
                            'execution_cost_ms': None, 'launch_contract_sha256': tracked[launch_path],
                            'selection': 'final_call_regardless_of_verdict'}
                    if prior:
                        old_root, old_record, old_line = prior
                        history_prefix = prefix + '/history/prior-call-' + hashlib.sha256(old_line).hexdigest()[:16]
                        old_path = history_prefix + '/record.json'
                        (output / old_path).parent.mkdir(parents=True, exist_ok=True)
                        _write_bytes(output / old_path, old_line)
                        copies[old_path] = hashlib.sha256(old_line).hexdigest()
                        for relative, digest in artifact_bindings(old_record):
                            copy(contained_path(old_root, relative, reject_symlinks=True), history_prefix + '/' + relative)
                        note['execution_cost_ms'] = _continuation_execution_cost(old_record, record)
                        note['prior_record_path'] = old_path
                        note['prior_answer_path'] = history_prefix + '/' + old_record['artifact_path']
                    else:
                        prior_receipt = binding(entry['prior_uncommitted'])
                        _require_public_json(_json(prior_receipt))
                        note['prior_uncommitted_path'] = copy(prior_receipt, prefix+'/prior-uncommitted.json')
                        note['prior_log_sha256'] = entry['prior_log']['sha256']
                    item['retry_continuation'] = note
                    item['retries'] = 1
                model_rows.append(item)
    model = {'title': spec['title'], 'providers': [{'id': x['id'], 'label': x['label']} for x in spec['providers']
                         if x['id'] not in spec.get('excluded_report_providers', [])],
             'suites': [{'id': x['id'], 'label': x['label']} for x in spec['suites']],
             'rows': model_rows, 'totals': _totals(model_rows),
             'execution_settings': execution_settings,
             'source_bindings': [{'sha256': digest, 'name': path.name} for path, digest in tracked.items()],
             'limitations': spec.get('limitations', [])}
    if spec.get('native_policy_registry'):
        approved = binding(spec['native_policy_registry'])
        relative = 'native-policy-registry.json'
        copy(approved, relative)
        model['native_policy_registry'] = {'path': relative, 'sha256': _sha(approved)}
    if 'report_families' in spec:
        model['report_families'] = [deepcopy(group) for group in spec['report_families']
                                    if group['id'] not in spec.get('excluded_report_families', [])]
    if spec.get('excluded_report_families') or spec.get('excluded_report_providers'):
        model['report_scope'] = {'excluded_families': spec.get('excluded_report_families', []),
                                 **({'excluded_providers': spec['excluded_report_providers']}
                                    if spec.get('excluded_report_providers') else {}),
                                 'source_positions': len(selected) + len(missing),
                                 'reported_positions': len(model_rows)}
    if spec.get('presentation') == 'final':
        model['presentation'] = 'final'
    model['ranking'] = spec.get('ranking', 'diagnostic')
    model['combined_ranking'] = spec.get('combined_ranking', True)
    if revision:
        model['offline_revision'] = revision['receipt']
    model['scores_changed'] = bool(revision) or any(row.get('score_revision') for row in model_rows)
    _write(output / 'matrix.json', model)
    with _source_validated_render(output, model):
        entry = generate_saved_matrix(output, model)
    # Bind every portable byte after generation; source files remain immutable.
    for path in output.rglob('*'):
        if path.is_file():
            copies[path.relative_to(output).as_posix()] = _sha(path)
    for path, digest in tracked.items():
        if _sha(path) != digest:
            raise ValueError('Original evidence changed during report generation')
    _write(output / 'saved-matrix-audit.json', {
        'schema_version': 1, 'spec_sha256': tracked[spec_path],
        'sources': [{'name': path.name, 'sha256': digest} for path, digest in tracked.items()],
        'files': copies, 'totals': model['totals'], 'provider_calls_added': 0, 'scores_changed': model['scores_changed'],
        'private_debug_policy': 'Private raw responses and debug files verified at source, not copied.'})
    check_saved_matrix(output, spec_path=spec_path if model.get('presentation') == 'final' else None)
    return entry


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.base = None

    def handle_starttag(self, tag, attrs):
        if tag == 'base':
            if self.base is None:
                self.base = next((value for name, value in attrs if name == 'href'), None)
            return
        self.links.extend(value for name, value in attrs if name in {'src', 'href'} and value)


def _source_record_inventory(inputs, candidates, decisions, adoptions, keys):
    """Bind every copied call record, including nonselected provenance, to its source."""
    expected = {}
    revision = inputs['revision'] or {'records': {}}
    for key in keys:
        prefix = 'evidence/' + '/'.join(key)
        selected = inputs['selected'].get(key)
        if selected:
            base = ((json.dumps(revision['records'][key], ensure_ascii=False)+'\n').encode()
                    if key in revision['records'] else selected[2])
            final = base
            if key in revision['records']:
                expected[prefix+'/score-before/score-before-record.json'] = selected[2]
            if key in candidates:
                expected[prefix+'/parameter-rerun/before/record.json'] = base
                expected[prefix+'/parameter-rerun/candidate/record.json'] = candidates[key][2]
                prior = inputs.get('parameter_continuations', {}).get(key)
                if prior and prior[1]:
                    expected[prefix+'/parameter-rerun/candidate-prior/record.json'] = prior[1][2]
                if decisions[key]['selected_new']:
                    final = candidates[key][2]
            if key in adoptions:
                adoption = adoptions[key]
                expected[prefix+'/adoption-before/record.json'] = final
                final = adoption['selected'][2]
                if adoption['revision']:
                    expected[prefix+'/adoption-before-score.json'] = adoption['original'][2]
            expected[prefix+'/record.json'] = final
        if key in inputs['historical']:
            record = inputs['historical'][key][1]
            expected[prefix+'/history/'+safe_identifier(record['attempt_id'])+'/record.json'] = inputs['historical'][key][2]
        continuations = (inputs.get('parameter_continuations', {})
                         if decisions.get(key, {}).get('selected_new') else inputs['continuations'])
        if key in adoptions:
            continuations = adoptions[key]['continuations']
        if key in continuations and continuations[key][1]:
            _, record, raw = continuations[key][1]
            expected[prefix+'/history/prior-call-'+hashlib.sha256(raw).hexdigest()[:16]+'/record.json'] = raw
    return expected


def _validate_source_disclosures(output, model, inputs, adoptions, keys):
    """Bind report organization, explanations and revision documents to their sources."""
    spec, revision = inputs['spec'], inputs['revision']
    expected = {'title': spec['title'],
        'providers': [{'id': p['id'], 'label': p['label']} for p in spec['providers']
                      if p['id'] not in spec.get('excluded_report_providers', [])],
        'suites': [{'id': p['id'], 'label': p['label']} for p in spec['suites']],
        'limitations': spec.get('limitations', []), 'ranking': spec.get('ranking', 'diagnostic'), 'combined_ranking': spec.get('combined_ranking', True),
        'report_families': ([g for g in spec['report_families']
                            if g['id'] not in spec.get('excluded_report_families', [])]
                           if 'report_families' in spec else None),
        'report_scope': ({'excluded_families': spec.get('excluded_report_families', []),
                          **({'excluded_providers': spec['excluded_report_providers']}
                             if spec.get('excluded_report_providers') else {}),
                          'source_positions': len(inputs['selected'])+len(inputs['missing']),
                          'reported_positions': len(keys)} if spec.get('excluded_report_families') or spec.get('excluded_report_providers') else None)}
    if any(model.get(name) != value for name, value in expected.items()):
        raise ValueError('Saved report configuration differs from validated source')
    notes = {}
    for config in ([spec['completion']] if spec.get('completion') else [])+spec.get('completions', []):
        if config.get('note'):
            for key in _completed_scope(config, inputs['sources'], inputs['selected'], inputs['continuations']):
                notes[key] = config['note']
    notes.update({key: value['note'] for key, value in adoptions.items()})
    for row in model['rows']:
        key = row['suite'], row['provider'], row['case_id']
        if row.get('completion_note') != notes.get(key):
            raise ValueError('Saved completion note differs from validated source')
    def check_json(relative, value):
        if _json(contained_path(output, relative, reject_symlinks=True)) != value:
            raise ValueError('Saved revision document differs from validated source: '+relative)
    def check_attachment(relative, source):
        if contained_path(output, relative, reject_symlinks=True).read_bytes() != source.read_bytes():
            raise ValueError('Saved revision attachment differs from validated source: '+relative)
    receipt = None
    if revision:
        receipt = deepcopy(revision['receipt'])
        for name, source in revision['attachments'].items():
            check_attachment('review-assets/'+name+'.json', source)
        if spec.get('excluded_report_families') or spec.get('excluded_report_providers'):
            check_json('score-revision-history.json', receipt)
            visible = {(suite, cid) for suite, _, cid in keys}
            routes = {suite+'--'+cid for suite, cid in visible}
            attempts = {suite+'--'+record['attempt_id']
                        for (suite, provider, cid), (_, record, _, _) in inputs['selected'].items()
                        if (suite, provider, cid) in keys}
            receipt['historical_scope_path'] = 'score-revision-history.json'
            receipt['changed_cases'] = [cid for cid in receipt['changed_cases'] if cid in routes]
            receipt['rescored_results'] = sum((suite, cid) in visible and
                provider not in spec.get('excluded_report_providers', [])
                for suite, provider, cid in revision['records'])
            for step in receipt['steps']:
                step['task_verdict_changes'] = [change for change in step['task_verdict_changes']
                                                if change['attempt_id'] in attempts]
            if 'decisions' in revision['attachments']:
                scoped = [item for item in _json(revision['attachments']['decisions'])
                          if (item['suite'], item['case_id']) in visible]
                check_json('report-review-assets/decisions.json', scoped)
                receipt['review_files']['decisions'] = 'report-review-assets/decisions.json'
        check_json('score-revision.json', receipt)
    if model.get('offline_revision') != receipt:
        raise ValueError('Saved revision summary differs from validated source')
    for key in keys:
        prefix = 'evidence/'+'/'.join(key)
        if revision and key in revision['records']:
            check_json(prefix+'/score-before-case.json', inputs['cases'][key[0]][key[2]])
        adoption = adoptions.get(key)
        if adoption and adoption['revision']:
            adopted_receipt = deepcopy(adoption['revision'])
            adopted_receipt['review_files'] = {
                name: f"adoption-review-assets/{adoption['index']}/{name}.json"
                for name in adoption['attachments']}
            check_json(prefix+'/adoption-score-revision.json', adopted_receipt)
            check_json(prefix+'/adoption-before-case.json', adoption['before_case'])
            for name, source in adoption['attachments'].items():
                check_attachment(adopted_receipt['review_files'][name], source)


def _validate_revision_and_authority_proofs(output, row, inputs, candidates, decisions, adoptions):
    """Require revision and replacement disclosures, including their authority links."""
    key = row['suite'], row['provider'], row['case_id']
    prefix = 'evidence/'+'/'.join(key)
    revision = inputs['revision'] or {'records': {}}
    score = None
    if key in revision['records']:
        score = {'receipt_path': 'score-revision.json',
                 'before_case_path': prefix+'/score-before-case.json',
                 'before_record_path': prefix+'/score-before/score-before-record.json'}
    if row.get('score_revision_history') != score:
        raise ValueError('Saved historical revision proof differs from validated source')
    if decisions.get(key, {}).get('selected_new'):
        score = None
    expected_adoption = None
    history = []
    if key in adoptions:
        adoption = adoptions[key]
        authority_path = f"adoption-authority-{adoption['index']}.json"
        expected_adoption = {'authority_path': authority_path, 'selection': 'complete_group_regardless_of_verdict'}
        if _json(output/authority_path) != _json(adoption['authority_path']):
            raise ValueError('Saved adoption authority differs from validated source')
        before = revision['records'].get(key, inputs['selected'][key][1])
        if decisions.get(key, {}).get('selected_new'):
            before = candidates[key][1]
        history.append({'record_path': prefix+'/adoption-before/record.json',
            'answer_path': prefix+'/adoption-before/'+before['artifact_path'],
            'scope_path': authority_path, 'attempt_id': before['attempt_id'],
            'called': _called(before), 'started_at': before.get('started_at')})
        score = ({'receipt_path': prefix+'/adoption-score-revision.json',
                  'before_case_path': prefix+'/adoption-before-case.json',
                  'before_record_path': prefix+'/adoption-before-score.json'} if adoption['revision'] else None)
    if row.get('result_adoption') != expected_adoption:
        raise ValueError('Saved adoption proof differs from validated source')
    if row.get('score_revision') != score:
        raise ValueError('Saved revision proof differs from validated source')
    if key in inputs['selected']:
        selected = inputs['selected'][key]
        record, raw, result_path = selected[1:]
        revised = key in revision['records']
        if revised:
            record = revision['records'][key]
            raw = (json.dumps(record, ensure_ascii=False)+'\n').encode()
        if decisions.get(key, {}).get('selected_new'):
            record, raw, result_path = candidates[key][1:]
            revised = False
        if key in adoptions:
            record, raw, result_path = adoptions[key]['selected'][1:]
            revised = bool(adoptions[key]['revision'])
        expected_receipt = {
            'kind': 'offline_revised_saved_result_view' if revised else 'immutable_saved_result_view',
            'source_results_sha256': inputs['sources'].tracked[result_path],
            'record_sha256': hashlib.sha256(raw).hexdigest(),
            'suite': key[0], 'provider': key[1], 'case_id': key[2],
            'public_artifacts': artifact_bindings(record),
            'private_artifacts_verified_not_copied': record_private_bindings(record),
            'scores_changed': revised, 'provider_calls_added': 0,
            **({'score_revision': score} if revised else {})}
        if (row.get('receipt_path') != prefix+'/receipt.json'
                or _json(output/row['receipt_path']) != json.loads(json.dumps(expected_receipt))
                or _sha(output/row['record_path']) != expected_receipt['record_sha256']):
            raise ValueError('Saved result receipt differs from validated source')

    if key in inputs['historical']:
        _, record, _, results = inputs['historical'][key]
        relative = prefix+'/history/'+safe_identifier(record['attempt_id'])
        history.append({'record_path': relative+'/record.json',
            'answer_path': relative+'/'+record['artifact_path'], 'scope_path': 'replacement-scope.json',
            'attempt_id': record['attempt_id'], 'called': _called(record), 'started_at': record.get('started_at'),
            'source_results_sha256': inputs['sources'].tracked[results],
            'private_artifacts_verified_not_copied': record_private_bindings(record)})
        expected_scope = {'kind': 'predeclared_replacement_scope_public_view',
            'original_sha256': inputs['sources'].tracked[inputs['replacement_scope']],
            'cells': [{'suite': cell['suite'], 'provider': cell['provider'], 'case_id': cell['case_id'],
                       'old_record_sha256': cell['old_record']['sha256']} for cell in inputs['replacement_cells']]}
        if _json(output/'replacement-scope.json') != expected_scope:
            raise ValueError('Saved replacement authority differs from validated source')
    if row.get('history', []) != history:
        raise ValueError('Saved history proof differs from validated source')


def _validate_selection_proofs(output, row, inputs, candidates, decisions, adoptions):
    """Reconcile published selection and split-call explanations with source evidence."""
    key = row['suite'], row['provider'], row['case_id']
    prefix = 'evidence/'+'/'.join(key)
    revision = inputs['revision'] or {'records': {}, 'cases': {}}
    expected_parameter = None
    if key in candidates:
        case = revision['cases'].get((key[0], key[2]), inputs['cases'][key[0]][key[2]])
        base = revision['records'].get(key, inputs['selected'][key][1])
        candidate = candidates[key][1]
        expected_parameter = {
            'selected_new': decisions[key]['selected_new'],
            'before_passed': task_passed(base['ok'], base['metrics'], case.get('task_kind', 'body')),
            'candidate_passed': task_passed(candidate['ok'], candidate['metrics'], case.get('task_kind', 'body')),
            'selection': inputs['spec']['parameter_reruns']['selection'], 'group': decisions[key]}
        for label, record, result_path in [('before', base, inputs['selected'][key][3]),
                                            ('candidate', candidate, candidates[key][3])]:
            relative = prefix+'/parameter-rerun/'+label
            expected_parameter.update({label+'_record_path': relative+'/record.json',
                label+'_answer_path': relative+'/'+record['artifact_path'],
                label+'_results_sha256': inputs['sources'].tracked[result_path]})
        prior = inputs.get('parameter_continuations', {}).get(key)
        if prior and prior[1]:
            relative = prefix+'/parameter-rerun/candidate-prior'
            expected_parameter.update(candidate_prior_record_path=relative+'/record.json',
                                      candidate_prior_answer_path=relative+'/'+prior[1][1]['artifact_path'])
    if row.get('parameter_rerun') != expected_parameter:
        raise ValueError('Saved parameter proof differs from validated source')
    continuations = (inputs.get('parameter_continuations', {})
                     if decisions.get(key, {}).get('selected_new') else inputs['continuations'])
    if key in adoptions:
        continuations = adoptions[key]['continuations']
    expected_retry = None
    if key in continuations:
        entry, prior, launch = continuations[key]
        record = _json(contained_path(output, row['record_path'], reject_symlinks=True))
        expected_retry = {'prior_physical_calls': 1, 'continued_physical_calls': 1,
            'total_physical_calls': 2, 'max_failure_retries': 1,
            'final_call_elapsed_ms': record['elapsed_ms'], 'execution_cost_ms': None,
            'launch_contract_sha256': inputs['sources'].tracked[launch],
            'selection': 'final_call_regardless_of_verdict'}
        if prior:
            _, old, raw = prior
            relative = prefix+'/history/prior-call-'+hashlib.sha256(raw).hexdigest()[:16]
            expected_retry.update(execution_cost_ms=_continuation_execution_cost(old, record),
                prior_record_path=relative+'/record.json', prior_answer_path=relative+'/'+old['artifact_path'])
        else:
            expected_retry.update(prior_uncommitted_path=prefix+'/prior-uncommitted.json',
                                  prior_log_sha256=entry['prior_log']['sha256'])
            if _json(output/expected_retry['prior_uncommitted_path']) != _json(inputs['sources'].binding(entry['prior_uncommitted'])):
                raise ValueError('Saved retry receipt differs from validated source')
    if row.get('retry_continuation') != expected_retry:
        raise ValueError('Saved retry proof differs from validated source')


def _validate_record_artifacts(path):
    for artifact, expected in artifact_bindings(_json(path)):
        target = contained_path(path.parent, artifact, reject_symlinks=True)
        if not target.is_file() or _sha(target) != expected:
            raise ValueError('Portable saved record artifact differs')


def _validate_record_links(output, value):
    """Keep displayed answers and logs attached to their own call record."""
    if isinstance(value, list):
        for child in value:
            _validate_record_links(output, child)
    elif isinstance(value, dict):
        for name, relative in value.items():
            for suffix in ('answer_path', 'log_path'):
                if name != suffix and not name.endswith('_'+suffix):
                    continue
                record_key = name[:-len(suffix)]+'record_path'
                record_path = value.get(record_key)
                if not record_path:
                    if relative:
                        raise ValueError('Saved evidence link has no associated record')
                    continue
                path = contained_path(output, record_path, reject_symlinks=True)
                record = _json(path)
                if suffix == 'answer_path':
                    artifact = record['artifact_path']
                else:
                    logs = record_public_call_log_bindings(record)
                    log = record.get('provider_metadata', {}).get('call_log') or (logs[-1] if logs else None)
                    artifact = log['path'] if log else None
                expected = (path.parent/ artifact).relative_to(output).as_posix() if artifact else None
                if relative != expected:
                    raise ValueError('Saved evidence link differs from associated record')
            if isinstance(relative, (dict, list)):
                _validate_record_links(output, relative)


def _validate_rendered_pages(output, model):
    """Compare generated pages with a fresh render of the validated report model."""
    with tempfile.TemporaryDirectory(prefix='saved-matrix-render-check-') as temporary:
        expected = Path(temporary)
        def link_or_copy(source, destination):
            try:
                os.link(source, destination)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                shutil.copyfile(source, destination)
        # Only immutable evidence is linked. Generated files must have independent
        # inodes so verification cannot modify the report being checked.
        for name in ('evidence', 'review-assets', 'report-review-assets', 'adoption-review-assets'):
            source = output/name
            if source.is_dir():
                shutil.copytree(source, expected/name, copy_function=link_or_copy)
        # Seed rendering only with documents already checked against source bindings.
        documents = {'score-revision.json', 'score-revision-history.json', 'replacement-scope.json'}
        documents.update(row['result_adoption']['authority_path'] for row in model['rows']
                         if row.get('result_adoption'))
        for name in documents:
            source = output/name
            if source.is_file():
                shutil.copy2(source, expected/name)
        _write(expected/'matrix.json', model)
        with _source_validated_render(expected, model):
            render_saved_with_standard_generator(expected, model)
        pages = {path.relative_to(expected) for path in expected.rglob('*.html')
                 if path.relative_to(expected).parts[0] != 'evidence'}
        actual = {path.relative_to(output) for path in output.rglob('*.html')
                  if path.relative_to(output).parts[0] != 'evidence'}
        if actual != pages:
            raise ValueError('Rendered page population differs from validated source')
        for relative in pages:
            if (output/relative).read_bytes() != (expected/relative).read_bytes():
                raise ValueError('Rendered page differs from validated source: '+str(relative))
        assets = {path.relative_to(expected) for path in (expected/'assets').rglob('*') if path.is_file()}
        actual_assets = {path.relative_to(output) for path in (output/'assets').rglob('*') if path.is_file()}
        if assets != actual_assets:
            raise ValueError('Rendered asset population differs from validated source')
        for relative in assets:
            if (output/relative).read_bytes() != (expected/relative).read_bytes():
                raise ValueError('Rendered asset differs from validated source: '+str(relative))



def check_saved_matrix(output: Path, *, spec_path: Path | None = None) -> dict:
    """Verify the portable registry, and its caller authority when supplied."""
    from harness.web_fetch.native_projection import approved_native_policies
    output = _path(Path.cwd(), output).resolve()
    binding = _json(output / 'matrix.json').get('native_policy_registry')
    if binding is not None:
        contained_path(output, binding['path'], reject_symlinks=True)
    registry = _bound_native_registry(output, binding)
    if spec_path is not None:
        spec_path = Path(spec_path)
        selected = _json(spec_path).get('native_policy_registry')
        original = _bound_native_registry(spec_path.parent, selected)
        if original != registry:
            raise ValueError('Portable native policy registry differs from caller approval')
    with approved_native_policies(registry):
        return _check_saved_matrix(output, spec_path=spec_path)


def _check_saved_matrix(output: Path, *, spec_path: Path | None = None) -> dict:
    """Check the complete portable tree and unchanged sources without rescoring."""
    output = _path(Path.cwd(), output).resolve()
    audit = _json(output / 'saved-matrix-audit.json')
    source_count = 0
    inputs = None
    if (output / 'saved-composition.json').is_file():
        from harness.web_fetch.report_saved_composition import check_composition
        if spec_path is not None and _sha(Path(spec_path)) != audit['spec_sha256']:
            raise ValueError('Saved composition specification changed')
        check_composition(output, spec_path)
        spec_path = None
    if spec_path is not None:
        if _sha(_path(Path(spec_path).parent, Path(spec_path).name)) != audit['spec_sha256']:
            raise ValueError('Original saved-matrix source changed: specification')
        try:
            inputs = _load_saved_matrix(spec_path)
            candidates = _parameter_reruns(inputs)
            decisions = _parameter_group_decisions(inputs, candidates)
            _validate_completion_retention(inputs, candidates, decisions)
            adoptions = _adopted_groups(inputs, candidates)
            execution_settings = _matrix_execution_settings(inputs, candidates, decisions, adoptions)
            if inputs['spec'].get('presentation') == 'final':
                _require_final_selection(inputs)
                _require_comparable_matrix(execution_settings)
        except ValueError as exc:
            raise ValueError('Original saved-matrix source changed or invalid') from exc
        actual = sorted((p.name, digest) for p, digest in inputs['sources'].tracked.items())
        expected = sorted((item['name'], item['sha256']) for item in audit['sources'])
        if actual != expected:
            raise ValueError('Original saved-matrix source changed')
        source_count = len(actual)
    actual_files = set()
    for path in output.rglob('*'):
        if path.is_symlink():
            raise ValueError('Portable saved-matrix symlink is forbidden')
        if path.is_file():
            actual_files.add(path.relative_to(output).as_posix())
    if actual_files != set(audit['files']) | {'saved-matrix-audit.json'}:
        raise ValueError('Portable saved-matrix file inventory changed')
    for relative, digest in audit['files'].items():
        if _sha(contained_path(output, relative, reject_symlinks=True)) != digest:
            raise ValueError('Portable saved-matrix file changed')
    # Scan the complete distributable tree, not just selected receipt types.
    # The builder verifies staging here before installing the output directory.
    from harness.web_fetch.publication_privacy import publication_file_risk
    for relative in sorted(actual_files):
        raw = contained_path(output, relative, reject_symlinks=True).read_bytes()
        if publication_file_risk(relative, raw):
            raise ValueError('Saved report file contains private content: ' + relative)
        if relative.endswith(('.json', '.jsonl')):
            try:
                values = [json.loads(line) for line in raw.splitlines() if line.strip()] if relative.endswith('.jsonl') else [json.loads(raw)]
            except (ValueError, UnicodeError):
                # Raw provider answers may have a JSON suffix without valid JSON.
                # The shared text privacy scan above still applies to their bytes.
                values = []
            for value in values:
                _require_public_json(value, label='Saved report file')
    model = _json(output / 'matrix.json')
    if model.get('presentation') == 'final' and (inputs is None or inputs['spec'].get('presentation') != 'final'):
        raise ValueError('Final saved results require the original final source specification')
    if inputs is not None and model.get('execution_settings') != execution_settings:
        raise ValueError('Saved execution settings differ from validated sources')
    keys = [(r['suite'], r['provider'], r['case_id']) for r in model['rows']]
    if (len(keys) != len(set(keys)) or _totals(model['rows']) != audit['totals']
            or model['totals'] != audit['totals']):
        raise ValueError('Saved-matrix population changed')
    dataset = _json(output / 'dataset.json')
    expected_cells = {(provider['id'], case['id']) for provider in model['providers']
                      for case in dataset['cases']}
    if {(provider, suite+'--'+cid) for suite, provider, cid in keys} != expected_cells:
        raise ValueError('Saved-matrix population differs from task inventory')
    if inputs is not None:
        spec = inputs['spec']
        excluded = set(spec.get('excluded_report_families', []))
        expected_keys = {(suite, provider, cid) for suite, cases in inputs['cases'].items()
                         for cid, case in cases.items() for provider in inputs['providers']
                         if provider not in spec.get('excluded_report_providers', [])
                         and (not excluded or _presentation_family(case, suite, spec['report_families']) not in excluded)}
        if set(keys) != expected_keys:
            raise ValueError('Saved-matrix population differs from validated source')
        if model.get('presentation', 'audit') != spec.get('presentation', 'audit'):
            raise ValueError('Report presentation differs from approved specification')
        _validate_source_disclosures(output, model, inputs, adoptions, keys)
    def record_paths(value):
        if isinstance(value, dict):
            for name, child in value.items():
                if isinstance(child, str) and (name == 'record_path' or name.endswith('_record_path')):
                    yield child
                else:
                    yield from record_paths(child)
        elif isinstance(value, list):
            for child in value:
                yield from record_paths(child)
    for row in model['rows']:
        identity = row['suite'], row['provider'], row['case_id']
        case = _json(contained_path(output, row['case_path'], reject_symlinks=True))
        if case['id'] != row['case_id']:
            raise ValueError('Saved case identity differs from matrix')
        if row.get('url') != case['url'] or row.get('name') != case.get('name', row['case_id']):
            raise ValueError('Saved case metadata differs from matrix')
        if row.get('record_path'):
            record = _json(contained_path(output, row['record_path'], reject_symlinks=True))
            if (record['provider'], record['case_id']) != identity[1:]:
                raise ValueError('Saved record identity differs from matrix')
            values = _saved_row_values(record, case)
            if row.get('retry_continuation'):
                values['retries'] = 1
                if row['retry_continuation']['final_call_elapsed_ms'] != record.get('elapsed_ms'):
                    raise ValueError('Saved retry timing differs from matrix')
            if any(row.get(name) != value for name, value in values.items()):
                raise ValueError('Saved record values differ from matrix')
        else:
            receipt = _json(contained_path(output, row['receipt_path'], reject_symlinks=True))
            _require_public_json(receipt)
            if (row['status'] not in {'not_called', 'uncommitted'}
                    or not _receipt_covers(receipt, *identity, row['status'])
                    or row['called'] != (row['status'] == 'uncommitted')
                    or row['passed'] is not None or row['elapsed_ms'] is not None or row['normal']):
                raise ValueError('Saved missing state differs from matrix')
        if inputs is not None:
            revision = inputs['revision'] or {'cases': {}, 'records': {}}
            expected_case = revision['cases'].get((identity[0], identity[2]),
                                                  inputs['cases'][identity[0]][identity[2]])
            if case != expected_case:
                raise ValueError('Saved case differs from validated source')
            expected_record = inputs['selected'].get(identity)
            if expected_record is not None:
                expected_record = revision['records'].get(identity, expected_record[1])
            if identity in decisions and decisions[identity]['selected_new']:
                expected_record = candidates[identity][1]
            if identity in adoptions:
                expected_record = adoptions[identity]['selected'][1]
            if expected_record is not None:
                if not row.get('record_path') or record != expected_record:
                    raise ValueError('Saved record differs from validated source selection')
            elif (row.get('record_path') or row['status'] != inputs['missing'][identity][0]['status']
                    or receipt != _json(inputs['missing'][identity][1])
                    or row['error'] != inputs['missing'][identity][0]['reason']):
                raise ValueError('Saved missing receipt differs from validated source')
            _validate_revision_and_authority_proofs(output, row, inputs, candidates, decisions, adoptions)
            _validate_selection_proofs(output, row, inputs, candidates, decisions, adoptions)
        _validate_record_links(output, row)
        for relative in set(record_paths(row)):
            path = contained_path(output, relative, reject_symlinks=True)
            _validate_record_artifacts(path)
        if row.get('score_revision'):
            receipt = _json(contained_path(output, row['score_revision']['receipt_path'], reject_symlinks=True))
            for target in receipt.get('review_files', {}).values():
                if target not in audit['files'] or not contained_path(output, target, reject_symlinks=True).is_file():
                    raise ValueError('Missing score revision attachment')
        for key in ('record_path', 'answer_path', 'log_path', 'case_path', 'receipt_path'):
            if row.get(key) and not contained_path(output, row[key], reject_symlinks=True).is_file():
                raise ValueError('Missing saved-matrix evidence link')
        continuation = row.get('retry_continuation') or {}
        if continuation.get('prior_uncommitted_path'):
            _require_public_json(_json(contained_path(output, continuation['prior_uncommitted_path'], reject_symlinks=True)))
        for field in ('prior_record_path', 'prior_answer_path', 'prior_uncommitted_path'):
            if continuation.get(field) and not contained_path(output, continuation[field], reject_symlinks=True).is_file():
                raise ValueError('Missing retry continuation evidence')
        for history in row.get('history', []):
            for key in ('record_path', 'answer_path', 'scope_path'):
                if not contained_path(output, history[key], reject_symlinks=True).is_file():
                    raise ValueError('Missing superseded evidence link')
            previous = _json(contained_path(output, history['record_path']))
            if (previous['provider'], previous['case_id']) != (row['provider'], row['case_id']):
                raise ValueError('Superseded evidence identity differs')
    if inputs is not None:
        for relative, expected_record in _source_record_inventory(inputs, candidates, decisions, adoptions, keys).items():
            path = contained_path(output, relative, reject_symlinks=True)
            if not path.is_file() or path.read_bytes() != expected_record:
                raise ValueError('Published provenance record differs from validated source: '+relative)
            _validate_record_artifacts(path)
    with _source_validated_render(output, model):
        projected_dataset, projected_records, projected_identities, projected_scope = _saved_renderer_projection(output, model)
    saved_records = [json.loads(line) for line in (output/'results.jsonl').read_bytes().splitlines() if line.strip()]
    execution = _json(output/'execution.json')
    scores_changed = (bool(inputs['revision']) or any(adoptions[key]['revision'] for key in keys if key in adoptions)
                      if inputs is not None else bool(model.get('offline_revision')) or any(row.get('score_revision') for row in model['rows']))
    scores_changed = bool(scores_changed)
    if any(value.get('scores_changed') is not scores_changed for value in (audit, model, execution)):
        raise ValueError('Saved scoring-change flags differ from revision evidence')
    if (dataset != projected_dataset or saved_records != projected_records
            or _json(output/'summary.json') != projected_scope
            or execution.get('identities') != projected_identities):
        raise ValueError('Saved renderer projection differs from validated rows')
    if inputs is not None:
        _validate_rendered_pages(output, model)
    # Source HTML captures are immutable evidence, not generated navigation routes.
    routes = sorted(p for p in output.rglob('*.html')
                    if p.relative_to(output).parts[0] != 'evidence')
    from harness.web_fetch.report_check import VisibleLocalScope, check_local_diagnostic_presentation
    check_local_diagnostic_presentation(output, ranked=model.get('presentation') == 'final' or model.get('ranking') == 'saved_task_pass_rate')
    from harness.report_languages import english_notice
    virtual_root = 'https://saved-matrix.invalid/__report__/'
    for page in routes:
        relative = page.relative_to(output).as_posix()
        if relative not in audit['files']:
            raise ValueError('Unbound saved-matrix HTML route')
        content = page.read_text(encoding='utf-8')
        disclosure = VisibleLocalScope()
        disclosure.feed(content)
        is_notice = relative == 'en/index.html' and content == english_notice()
        if not is_notice and (not disclosure.is_report or not ({'evidence', 'ranking'} if model.get('presentation') == 'final' or model.get('ranking') == 'saved_task_pass_rate' else {'evidence'}) <= disclosure.visible):
            raise ValueError('Saved-result evidence/ranking scope disclosure missing: ' + relative)
        parser = _Links()
        parser.feed(content)
        page_url = virtual_root + relative
        base_url = urljoin(page_url, unquote(parser.base)) if parser.base is not None else page_url
        base = urlsplit(base_url)
        if (base.scheme != 'https' or base.netloc != 'saved-matrix.invalid'
                or not base.path.startswith('/__report__/') or '\\' in base.path):
            raise ValueError('Saved-matrix base escapes output root')
        for link in parser.links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = urlsplit(urljoin(base_url, unquote(link)))
            if not resolved.path.startswith('/__report__/') or '\\' in resolved.path:
                raise ValueError('Saved-matrix static link escapes output root')
            target = resolved.path.removeprefix('/__report__/')
            if not contained_path(output, target, reject_symlinks=True).is_file():
                raise ValueError('Broken saved-matrix static link: ' + relative + ': ' + link)
    return {'valid': True, 'totals': audit['totals'], 'source_files_verified': source_count,
            'portable_files_verified': len(audit['files']), 'html_routes_verified': len(routes), 'provider_calls_added': 0, 'scores_changed': scores_changed}


def _saved_renderer_projection(output, model):
    """Adapt immutable multi-suite inputs to the existing full report renderer.

    Only report route IDs and local evidence paths are projected. Original case
    definitions, record bytes and metrics remain in their evidence files.
    """
    final = model.get('presentation') == 'final'
    if model.get('presentation', 'audit') not in {'audit', 'final'}:
        raise ValueError('Unknown saved-matrix presentation')
    if final and (not model['rows'] or any(
            not row['called'] or row['passed'] is None or not row['record_path']
            for row in model['rows'])):
        raise ValueError('Final report requires complete judged call evidence')
    if final and any(row['status'] == 'adapter_rejected' for row in model['rows']):
        raise ValueError('Final report cannot exclude adapter rejections from its denominator')
    if final:
        _require_comparable_matrix(model.get('execution_settings', []))
        if any(row.get('parameter_rerun') or row.get('result_adoption') for row in model['rows']):
            raise ValueError('Post-outcome selection cannot enter a portable final report')
    cases, records, identities = {}, [], []
    attempt_routes = set()
    groups = model.get('report_families', model['suites'])
    labels = {g['id']: g['label'] for g in groups}
    for row in model['rows']:
        route = safe_identifier(row['suite'] + '--' + row['case_id'])
        case = _json(contained_path(output, row['case_path']))
        if final:
            revision_proofs = [row[name] for name in ('score_revision', 'score_revision_history') if row.get(name)]
            changed = route in model.get('offline_revision', {}).get('changed_cases', [])
            if changed and not revision_proofs:
                raise ValueError('Final revised tasks require retained original task evidence')
            for proof in revision_proofs:
                before = _json(contained_path(output, proof['before_case_path'], reject_symlinks=True))
                if not _same_task_rules(before, case):
                    raise ValueError('Changed task contracts cannot enter a portable final report')
        if case['id'] != row['case_id']:
            raise ValueError('Saved case identity differs from matrix')
        if row.get('url') != case['url'] or row.get('name') != case.get('name', row['case_id']):
            raise ValueError('Saved case metadata differs from matrix')
        case['id'] = route
        family = _presentation_family(case, row['suite'], groups) if 'report_families' in model else row['suite']
        case['report_family'] = family
        case['report_family_label'] = labels[family]
        if cases.setdefault(route, case) != case:
            raise ValueError(f"Conflicting saved case definitions for {route}")
        if not row.get('record_path'):
            continue
        raw = _json(contained_path(output, row['record_path']))
        if raw['case_id'] != row['case_id'] or raw['provider'] != row['provider']:
            raise ValueError('Saved record identity differs from matrix')
        if _saved_row_values(raw, case)['passed'] != row['passed']:
            raise ValueError('Saved verdict differs from matrix')
        prefix = str(Path(row['record_path']).parent)
        def project(value, key='', prefix=prefix):
            if isinstance(value, dict):
                return {k: project(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [project(v, key) for v in value]
            if isinstance(value, str) and (key == 'path' or key.endswith('_path')):
                candidate = prefix + '/' + value
                if contained_path(output, candidate).is_file():
                    return candidate
            return value
        record = project(raw)
        if row.get('completion_note'):
            record['report_completion_note'] = row['completion_note']
        record['report_invocation_status'] = row['status']
        record['report_superseded_history'] = deepcopy(row.get('history', []))
        record['report_retry_continuation'] = deepcopy(row.get('retry_continuation'))
        record['report_parameter_rerun'] = deepcopy(row.get('parameter_rerun'))
        record['case_id'] = route
        record['attempt_id'] = safe_identifier(row['suite'] + '--' + raw['attempt_id'])
        if record['attempt_id'] in attempt_routes:
            raise ValueError('Colliding suite-qualified attempt routes')
        attempt_routes.add(record['attempt_id'])
        records.append(record)
        identities.append({'case_route': route, 'attempt_route': record['attempt_id'],
                           'source_record': row['record_path'], 'source_case': row['case_path']})
    scope = {'providers': [], 'limitations': model.get('limitations', []), 'missing': [r for r in model['rows']
                                        if r['passed'] is None or not r['called']]}
    scope['execution_settings'] = model.get('execution_settings', [])
    reruns = [r['parameter_rerun'] for r in model['rows'] if r.get('parameter_rerun')]
    if reruns:
        scope['parameter_reruns'] = {'family_selection': any(r['selection'] == 'replace_family_if_pass_count_improves' for r in reruns),
                                     'evaluated': len(reruns),
                                     'selected': sum(r['selected_new'] for r in reruns)}
    providers = [p['id'] for p in model['providers']]
    for provider in providers:
        rs = [r for r in model['rows'] if r['provider'] == provider]
        scope['providers'].append(dict(provider=provider, planned=len(rs),
            called=sum(r['called'] for r in rs), saved=sum(r['record_path'] is not None for r in rs),
            normal=sum(bool(r['normal']) for r in rs), passed=sum(r['passed'] is True for r in rs),
            rejected=sum(r['status']=='adapter_rejected' for r in rs),
            uncommitted=sum(r['status']=='uncommitted' for r in rs),
            not_called=sum(r['status']=='not_called' for r in rs)))
    dataset = {'name': model['title'], 'cases': list(cases.values()),
        'coverage_plan': {c.get('track','web'): providers for c in cases.values()},
        'comparison': {'publication_state': ('final_saved_results' if final else 'local_audit_only_not_for_release'),
                       'presentation': {'ranking': 'saved_task_pass_rate' if final else model.get('ranking', 'diagnostic'),
                                        'combined_ranking': model.get('combined_ranking', True)}},
        'saved_matrix_scope': scope}
    if model.get('offline_revision'):
        dataset['comparison']['offline_revision'] = model['offline_revision']
    dataset['report_suites'] = [dict(id=s['id'], label=s['label'],
        case_ids=[key for key, case in cases.items() if case['report_family'] == s['id']]) for s in groups]
    grouped=[key for s in dataset['report_suites'] for key in s['case_ids']]
    if len(grouped)!=len(set(grouped)) or set(grouped)!=set(cases):
        raise ValueError('Report suites must partition every case exactly once')
    if final:
        from harness.web_fetch.reliability import require_reliable
        _require_comparable_matrix(model.get('execution_settings', []),
            {(record['provider'], cases[record['case_id']]['report_family']) for record in records})
        _require_common_scoring(records, 'Final report requires one valid scoring identity for all selected results')
        require_reliable(dataset, records)
        admission = _require_source_admission(output, model)
        admission['projection'] = canonical_json_sha256([dataset, records])
    return dataset, records, identities, scope


def render_saved_with_standard_generator(output, model):
    """Render the verified route/path projection through the shared generator."""
    from harness.web_fetch.report import generate
    dataset, records, identities, scope = _saved_renderer_projection(output, model)
    providers = [p['id'] for p in model['providers']]
    _write(output/'dataset.json', dataset)
    (output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records), encoding='utf-8')
    _write(output/'summary.json', scope)
    _write(output/'execution.json', {'kind':'saved_report_projection', 'provider_calls_added':0,
           'scores_changed':model.get('scores_changed', bool(model.get('offline_revision'))), 'identities':identities, 'source_bindings':model['source_bindings']})
    from harness.web_fetch.report import _REPORT_SUITES, _REPORT_PROVIDER_LABELS, provider_label
    token = _REPORT_SUITES.set(tuple(dataset['report_suites']))
    label_token = _REPORT_PROVIDER_LABELS.set({p['id']: p['label'] for p in model['providers']})
    try:
        entry = generate(output,dataset,records,[],authored_interpretation=False,frozen_scores=True)
        from harness.web_fetch.report import write, hero, esc, saved_matrix_scope_section, local_audit_banner
        for provider in providers:
            if not any(r['provider'] == provider for r in records):
                write(output, 'providers/'+provider+'.html', provider_label(provider),
                      local_audit_banner(dataset)+hero(esc(provider_label(provider)), '')+saved_matrix_scope_section(scope), root_links=True)
    finally:
        _REPORT_SUITES.reset(token)
        _REPORT_PROVIDER_LABELS.reset(label_token)
    return entry
