"""Present a complete, explicitly bound matrix of saved results without rescoring.

The external specification owns the population, source selection and documented
missing cells. Rendering remains in the Web Fetch report owner. Private response
and debug files are verified but are not copied into the portable public views.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.models import EvalCase
from lexbench_webfetch.paths import contained_path, safe_identifier

from harness.web_fetch.artifacts import (
    artifact_bindings, record_private_bindings, record_public_call_log_bindings,
    verify_record_artifacts,
)


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _json(path):
    return json.loads(Path(path).read_bytes())


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def _path(base, value):
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlinked source evidence')
    return path.absolute()


def _case_identity(case):
    value = deepcopy(case)
    value.setdefault('task_kind', 'body')
    value.setdefault('track', 'web')
    value.get('source_benchmark', {}).pop('input_media_kind', None)
    if not value.get('source_benchmark'):
        value.pop('source_benchmark', None)
    return EvalCase(**value)


def _receipt_covers(value, suite, provider, case_id, status, inherited=None):
    inherited = inherited or {}
    if isinstance(value, list):
        return any(_receipt_covers(x, suite, provider, case_id, status, inherited) for x in value)
    if not isinstance(value, dict):
        return False
    if value.get('kind') == 'user_requested_case_skip':
        return (status == 'not_called' and value.get('status') == 'not_called'
                and value.get('suite') == suite and value.get('case_id') == case_id
                and isinstance(value.get('providers'), list) and provider in value['providers']
                and value.get('score', 'absent') is None
                and isinstance(value.get('user_instruction'), str) and bool(value['user_instruction'].strip()))
    context = {**inherited, **{k: value[k] for k in ('suite', 'provider') if k in value}}
    if (value.get('case_id') == case_id and context.get('suite', suite) == suite
            and context.get('provider', provider) == provider):
        if status == 'not_called':
            return (value.get('state') == 'pre_call_blocked_no_method_calls'
                    or 'error' in value and 'state' not in value)
        if value.get('state') == 'called_but_uncommitted':
            return True
    return any(_receipt_covers(x, suite, provider, case_id, status, context)
               for x in value.values() if isinstance(x, (list, dict)))


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
            'adapter_rejected': sum(r['status'] == 'adapter_rejected' for r in rows),
            'uncommitted': sum(r['status'] == 'uncommitted' for r in rows),
            'not_called': sum(r['status'] == 'not_called' for r in rows)}


def build_saved_matrix(spec_path: Path, output: Path) -> Path:
    """Verify immutable sources and create a new portable report directory."""
    from harness.web_fetch.report import generate_saved_matrix

    spec_path = Path(spec_path)
    spec_path = spec_path.parent.resolve() / spec_path.name
    output = Path(output).absolute()
    if output.exists():
        raise ValueError('Saved-matrix output must be a new directory')
    spec = _json(spec_path)
    if spec.get('schema_version') != 1:
        raise ValueError('Unsupported saved-matrix specification')
    tracked = {}
    copies = {}

    def bind(path, expected=None):
        path = _path(spec_path.parent, path)
        digest = _sha(path)
        if expected is not None and digest != expected:
            raise ValueError('Source hash differs: ' + path.name)
        if path in tracked and tracked[path] != digest:
            raise ValueError('Source changed during assembly')
        tracked[path] = digest
        return path

    def binding(value):
        if not isinstance(value, dict) or set(value) != {'path', 'sha256'}:
            raise ValueError('Evidence requires an explicit path/hash binding')
        return bind(value['path'], value['sha256'])

    def copy(source, relative):
        source = bind(source)
        target = contained_path(output, relative, reject_symlinks=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != tracked[source]:
            raise ValueError('Source changed while copying')
        target.write_bytes(raw)
        copies[relative] = tracked[source]
        return relative

    def registry(items):
        ids = [safe_identifier(x['id']) for x in items]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('Duplicate or empty matrix registry')
        return ids

    providers = registry(spec['providers'])
    suites = registry(spec['suites'])
    cases = {}
    bind(spec_path)
    for suite in spec['suites']:
        dataset = _json(binding(suite['dataset']))
        entries = dataset['cases']
        ids = [safe_identifier(x['id']) for x in entries]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate case in suite')
        cases[suite['id']] = dict(zip(ids, entries, strict=True))
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
        source_cases = {x['id']: x for x in _json(source_dataset)['cases']}
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
        return retry.get('physical_attempts', len(retry.get('attempts', [])) or 1) == 1
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
        if launch.get('total_authorized_physical_calls_per_case', launch.get('total_authorized_physical_calls')) != 2:
            raise ValueError('Retry continuation must preserve the two-call budget')
        continuation_groups.setdefault(launch_path, set()).add((key[1], key[2]))
        prior = None
        if entry.get('prior_source'):
            source = entry['prior_source']
            root = _path(spec_path.parent, source['root'])
            result_path = binding(source['results'])
            result_path.relative_to(root)
            prior_cases = {x['id']: x for x in _json(binding(source['dataset']))['cases']}
            matches = [(json.loads(line), line) for line in result_path.read_bytes().splitlines(keepends=True)
                       if line.strip() and (json.loads(line)['provider'], json.loads(line)['case_id']) == key[1:]]
            if len(matches) != 1:
                raise ValueError('Retry continuation prior record is missing or ambiguous')
            old, old_line = matches[0]
            if (not one_call(old) or not contains_record(launch, old)
                    or _case_identity(prior_cases[key[2]]) != _case_identity(cases[key[0]][key[2]])
                    or old_line == selected[key][2]):
                raise ValueError('Retry continuation prior evidence differs from declared first call')
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
        continuations[key] = (entry, prior, launch_path)
    for launch_path, actual in continuation_groups.items():
        launch = _json(launch_path)
        if launch.get('case_ids'):
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
        if not _receipt_covers(_json(receipt), *key, entry['status']):
            raise ValueError('Missing receipt does not identify the declared cell')
        for diagnostic_source in entry.get('diagnostic_sources', []):
            binding(diagnostic_source)
        if entry.get('recovery'):
            raise ValueError('Recovery projections are not supported; bind the original missing receipt')
        missing[key] = (entry, receipt)
    for key in historical:
        if key not in selected and (key not in missing or missing[key][0]['status'] != 'uncommitted'):
            raise ValueError('Superseded result requires a current call or explicit uncommitted call')
    if set(selected) | set(missing) != expected:
        raise ValueError('Unexplained missing matrix cells')
    output.mkdir(parents=True, exist_ok=False)
    if replacement_scope:
        _write(output / 'replacement-scope.json', {
            'kind': 'predeclared_replacement_scope_public_view',
            'original_sha256': tracked[replacement_scope],
            'cells': [{'suite': x['suite'], 'provider': x['provider'], 'case_id': x['case_id'],
                       'old_record_sha256': x['old_record']['sha256']} for x in cells]})
    model_rows = []
    for suite in suites:
        for case_id, case in cases[suite].items():
            for provider in providers:
                key = (suite, provider, case_id)
                prefix = f'evidence/{suite}/{provider}/{case_id}'
                case_path = prefix + '/case.json'
                _write(output / case_path, case)
                item = {'suite': suite, 'provider': provider, 'case_id': case_id,
                        'name': case.get('name', case_id), 'url': case['url'], 'checks': {},
                        'case_path': case_path, 'record_path': None, 'answer_path': None,
                        'log_path': None, 'receipt_path': None, 'elapsed_ms': None, 'retries': None}
                if key in missing:
                    entry, receipt = missing[key]
                    item.update(status=entry['status'], called=entry['status'] == 'uncommitted',
                                normal=False, passed=None, error=entry['reason'],
                                receipt_path=copy(receipt, prefix + '/receipt.json'))
                else:
                    root, record, raw_line, result_path = selected[key]
                    record_path = prefix + '/record.json'
                    (output / record_path).write_bytes(raw_line)
                    copies[record_path] = hashlib.sha256(raw_line).hexdigest()
                    for relative, digest in artifact_bindings(record):
                        copy(contained_path(root, relative, reject_symlinks=True), prefix + '/' + relative)
                    called = _called(record)
                    metrics = record['metrics']
                    passed = task_passed(record['ok'], metrics, case.get('task_kind', 'body'))
                    metadata = record.get('provider_metadata', {})
                    logs = record_public_call_log_bindings(record)
                    final_log = metadata.get('call_log') or (logs[-1] if logs else None)
                    item.update(status=('adapter_rejected' if not called else 'passed' if passed else 'failed'),
                                called=called, normal=bool(called and record['ok'] and metrics.get('request_ok', record['ok'])),
                                passed=passed, elapsed_ms=record.get('elapsed_ms'),
                                retries=metadata.get('retry', {}).get('retries_used', max(0, metadata.get('retry', {}).get('physical_attempts', 1) - 1)),
                                error=record.get('error_message') or record.get('error_type') or '',
                                checks=_checks(metrics), record_path=record_path,
                                answer_path=prefix + '/' + record['artifact_path'],
                                log_path=prefix + '/' + final_log['path'] if final_log else None,
                                receipt_path=prefix + '/receipt.json')
                    _write(output / item['receipt_path'], {
                        'kind': 'immutable_saved_result_view', 'source_results_sha256': tracked[result_path],
                        'record_sha256': copies[record_path], 'suite': suite, 'provider': provider,
                        'case_id': case_id, 'public_artifacts': artifact_bindings(record),
                        'private_artifacts_verified_not_copied': record_private_bindings(record),
                        'scores_changed': False, 'provider_calls_added': 0})
                if key in historical:
                    old_root, old_record, old_line, old_results = historical[key]
                    history_prefix = prefix + '/history/' + safe_identifier(old_record['attempt_id'])
                    history_record = history_prefix + '/record.json'
                    (output / history_record).parent.mkdir(parents=True, exist_ok=True)
                    (output / history_record).write_bytes(old_line)
                    copies[history_record] = hashlib.sha256(old_line).hexdigest()
                    for relative, digest in artifact_bindings(old_record):
                        copy(contained_path(old_root, relative, reject_symlinks=True), history_prefix + '/' + relative)
                    history_scope = 'replacement-scope.json'
                    item['history'] = [{'record_path': history_record,
                        'answer_path': history_prefix + '/' + old_record['artifact_path'],
                        'scope_path': history_scope, 'attempt_id': old_record['attempt_id'],
                        'called': _called(old_record), 'started_at': old_record.get('started_at'),
                        'source_results_sha256': tracked[old_results],
                        'private_artifacts_verified_not_copied': record_private_bindings(old_record)}]
                if key in continuations:
                    entry, prior, launch_path = continuations[key]
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
                        (output / old_path).write_bytes(old_line)
                        copies[old_path] = hashlib.sha256(old_line).hexdigest()
                        for relative, digest in artifact_bindings(old_record):
                            copy(contained_path(old_root, relative, reject_symlinks=True), history_prefix + '/' + relative)
                        note['execution_cost_ms'] = old_record['elapsed_ms'] + record['elapsed_ms']
                        note['prior_record_path'] = old_path
                        note['prior_answer_path'] = history_prefix + '/' + old_record['artifact_path']
                    else:
                        note['prior_uncommitted_path'] = copy(binding(entry['prior_uncommitted']), prefix+'/prior-uncommitted.json')
                        note['prior_log_sha256'] = entry['prior_log']['sha256']
                    item['retry_continuation'] = note
                    item['retries'] = 1
                model_rows.append(item)
    model = {'title': spec['title'], 'providers': [{'id': x['id'], 'label': x['label']} for x in spec['providers']],
             'suites': [{'id': x['id'], 'label': x['label']} for x in spec['suites']],
             'rows': model_rows, 'totals': _totals(model_rows),
             'source_bindings': [{'sha256': digest, 'name': path.name} for path, digest in tracked.items()],
             'limitations': spec.get('limitations', [])}
    entry = generate_saved_matrix(output, model)
    _write(output / 'matrix.json', model)
    # Bind every portable byte after generation; source files remain immutable.
    for path in output.rglob('*'):
        if path.is_file():
            copies[path.relative_to(output).as_posix()] = _sha(path)
    for path, digest in tracked.items():
        if _sha(path) != digest:
            raise ValueError('Original evidence changed during report generation')
    _write(output / 'saved-matrix-audit.json', {
        'schema_version': 1, 'spec_sha256': tracked[spec_path],
        'sources': [{'path': str(path), 'sha256': digest} for path, digest in tracked.items()],
        'files': copies, 'totals': model['totals'], 'provider_calls_added': 0, 'scores_changed': False,
        'private_debug_policy': 'Private raw responses and debug files verified at source, not copied.'})
    check_saved_matrix(output)
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


def check_saved_matrix(output: Path) -> dict:
    """Check the complete portable tree and unchanged sources without rescoring."""
    output = Path(output)
    audit = _json(output / 'saved-matrix-audit.json')
    for binding in audit['sources']:
        path = _path(output, binding['path'])
        if _sha(path) != binding['sha256']:
            raise ValueError('Original saved-matrix source changed')
    for relative, digest in audit['files'].items():
        if _sha(contained_path(output, relative, reject_symlinks=True)) != digest:
            raise ValueError('Portable saved-matrix file changed')
    model = _json(output / 'matrix.json')
    keys = [(r['suite'], r['provider'], r['case_id']) for r in model['rows']]
    if len(keys) != len(set(keys)) or _totals(model['rows']) != audit['totals']:
        raise ValueError('Saved-matrix population changed')
    for row in model['rows']:
        for key in ('record_path', 'answer_path', 'log_path', 'case_path', 'receipt_path', 'recovery_path'):
            if row.get(key) and not contained_path(output, row[key], reject_symlinks=True).is_file():
                raise ValueError('Missing saved-matrix evidence link')
        continuation = row.get('retry_continuation') or {}
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
    # Source HTML captures are immutable evidence, not generated navigation routes.
    routes = sorted(p for p in output.rglob('*.html')
                    if p.relative_to(output).parts[0] != 'evidence')
    virtual_root = 'https://saved-matrix.invalid/__report__/'
    for page in routes:
        relative = page.relative_to(output).as_posix()
        if relative not in audit['files']:
            raise ValueError('Unbound saved-matrix HTML route')
        parser = _Links()
        parser.feed(page.read_text())
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
    return {'valid': True, 'totals': audit['totals'], 'source_files_verified': len(audit['sources']),
            'portable_files_verified': len(audit['files']), 'html_routes_verified': len(routes), 'provider_calls_added': 0, 'scores_changed': False}


def render_saved_with_standard_generator(output, model):
    """Adapt immutable multi-suite inputs to the existing full report renderer.

    Only report route IDs and local evidence paths are projected. Original case
    definitions, record bytes and metrics remain in their evidence files.
    """
    from harness.web_fetch.report import generate
    cases, records, identities = {}, [], []
    for row in model['rows']:
        route = safe_identifier(row['suite'] + '--' + row['case_id'])
        case = _json(contained_path(output, row['case_path']))
        if case['id'] != row['case_id']:
            raise ValueError('Saved case identity differs from matrix')
        case['id'] = route
        cases.setdefault(route, case)
        if not row.get('record_path'):
            continue
        raw = _json(contained_path(output, row['record_path']))
        if raw['case_id'] != row['case_id'] or raw['provider'] != row['provider']:
            raise ValueError('Saved record identity differs from matrix')
        if task_passed(raw['ok'], raw['metrics'], case.get('task_kind', 'body')) != row['passed']:
            raise ValueError('Saved verdict differs from matrix')
        prefix = str(Path(row['record_path']).parent)
        def project(value, key=''):
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
        record['report_invocation_status'] = row['status']
        record['report_superseded_history'] = deepcopy(row.get('history', []))
        record['report_retry_continuation'] = deepcopy(row.get('retry_continuation'))
        record['case_id'] = route
        record['attempt_id'] = safe_identifier(row['suite'] + '--' + raw['attempt_id'])
        records.append(record)
        identities.append({'case_route': route, 'attempt_route': record['attempt_id'],
                           'source_record': row['record_path'], 'source_case': row['case_path']})
    scope = {'providers': [], 'limitations': model.get('limitations', []), 'missing': [r for r in model['rows']
                                        if r['passed'] is None or not r['called']]}
    providers = [p['id'] for p in model['providers']]
    for provider in providers:
        rs = [r for r in model['rows'] if r['provider'] == provider]
        scope['providers'].append(dict(provider=provider, planned=len(rs),
            called=sum(r['called'] for r in rs), saved=sum(r['passed'] is not None for r in rs),
            normal=sum(bool(r['normal']) for r in rs), passed=sum(r['passed'] is True for r in rs),
            rejected=sum(r['status']=='adapter_rejected' for r in rs),
            uncommitted=sum(r['status']=='uncommitted' for r in rs),
            not_called=sum(r['status']=='not_called' for r in rs)))
    dataset = {'name': model['title'], 'cases': list(cases.values()),
        'coverage_plan': {c.get('track','web'): providers for c in cases.values()},
        'comparison': {'publication_state': 'local_audit_only_not_for_release',
                       'presentation': {'ranking': 'saved_task_pass_rate'}},
        'saved_matrix_scope': scope}
    dataset['report_suites'] = [dict(id=s['id'], label=s['label'],
        case_ids=[key for key in cases if key.startswith(s['id']+'--')]) for s in model['suites']]
    grouped=[key for s in dataset['report_suites'] for key in s['case_ids']]
    if len(grouped)!=len(set(grouped)) or set(grouped)!=set(cases):
        raise ValueError('Report suites must partition every case exactly once')
    _write(output/'dataset.json', dataset)
    (output/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
    _write(output/'summary.json', scope)
    _write(output/'execution.json', {'kind':'saved_report_projection', 'provider_calls_added':0,
           'scores_changed':False, 'identities':identities, 'source_bindings':model['source_bindings']})
    from harness.web_fetch.report import _REPORT_SUITES, _REPORT_PROVIDER_LABELS, provider_label
    token = _REPORT_SUITES.set(tuple(dataset['report_suites']))
    label_token = _REPORT_PROVIDER_LABELS.set({p['id']: p['label'] for p in model['providers']})
    try:
        entry = generate(output,dataset,records,[],authored_interpretation=False,frozen_scores=True)
        from harness.web_fetch.report import write, hero, saved_matrix_scope_section
        for provider in providers:
            if not any(r['provider'] == provider for r in records):
                write(output, 'providers/'+provider+'.html', provider_label(provider),
                      hero(provider_label(provider), '')+saved_matrix_scope_section(scope), root_links=True)
    finally:
        _REPORT_SUITES.reset(token)
        _REPORT_PROVIDER_LABELS.reset(label_token)
    return entry
