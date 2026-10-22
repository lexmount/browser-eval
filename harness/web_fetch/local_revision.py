"""Validate task replacements and offline revisions of a bound local snapshot."""
import hashlib
import json
import fcntl
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from harness.record_io import file_sha256, canonical_json_sha256, load_jsonl

from .cohort_exclusions import require_cohort_exclusions
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.scoring_regressions import require_scoring_regressions
from .scoring_revision import ALLOWED_FIELDS
from .publication_privacy import EvidenceBinding, read_evidence_binding, staged_evidence_reads


@dataclass(frozen=True)
class RevisionStep:
    """Typed input boundary; validation never rewrites the frozen receipt."""

    authority_description: str
    changed_case_ids: tuple[str, ...]
    rescored_attempt_ids: tuple[str, ...]
    input_dataset: EvidenceBinding
    input_results: EvidenceBinding

    @classmethod
    def from_mapping(cls, receipt):
        if not isinstance(receipt, dict):
            raise ValueError('revision must be an object')
        authority = receipt.get('authority')
        if not isinstance(authority, dict):
            raise ValueError('revision.authority must be an object')
        description = authority.get('description')
        if not isinstance(description, str) or not description.strip():
            raise ValueError('revision.authority.description must be a nonempty string')
        scopes = []
        for field in ('changed_case_ids', 'rescored_attempt_ids'):
            value = receipt.get(field)
            if (not isinstance(value, list)
                    or any(not isinstance(item, str) or not item for item in value)
                    or len(value) != len(set(value))):
                raise ValueError(f'revision.{field} must be a list of unique identifiers')
            scopes.append(tuple(value))
        for field in ('task_verdict_changes', 'native_answer_corrections', 'native_context_corrections'):
            value = receipt.get(field, [] if field != 'task_verdict_changes' else None)
            if not isinstance(value, list):
                raise ValueError(f'revision.{field} must be a list')
            for index, item in enumerate(value):
                if (not isinstance(item, dict)
                        or not isinstance(item.get('attempt_id'), str) or not item['attempt_id']):
                    raise ValueError(f'revision.{field}[{index}].attempt_id must be a nonempty string')
        for field in ('output_dataset_sha256', 'output_results_sha256', 'scoring_source_sha256'):
            value = receipt.get(field)
            if (not isinstance(value, str) or len(value) != 64
                    or any(char not in '0123456789abcdef' for char in value)):
                raise ValueError(f'revision.{field} must be a SHA-256 digest')
        regressions = receipt.get('regression_ids')
        if (not isinstance(regressions, list) or not regressions
                or any(not isinstance(item, str) or not item for item in regressions)):
            raise ValueError('revision.regression_ids requires executable regression provenance')
        bindings = [EvidenceBinding.from_mapping(receipt.get(field), field='revision.' + field)
                    for field in ('input_dataset', 'input_results')]
        return cls(description, *scopes, *bindings)


def _require_revision_regressions(identifiers):
    """A revision cites controls in the layer whose boundary it changes."""
    if not identifiers:
        raise ValueError('Local revision requires executable regression provenance')
    from .native_projection import native_projection_regressions
    native = native_projection_regressions()
    scoring = [identifier for identifier in identifiers if identifier not in native]
    if scoring:
        require_scoring_regressions(scoring)
    return list(dict.fromkeys(identifiers))


class _RevisionTransaction:
    """Stage only changed files; validate before installation and recover interrupted commits.

    The source may contain large immutable captures. They are never copied. A
    sibling journal owns backups only for the write set, under a per-source lock.
    """
    def __init__(self, source):
        self.source = source.resolve()
        self.work = self.source.with_name('.' + self.source.name + '-revision')
        self.updates = {}

    def recover(self):
        if not self.work.exists():
            return
        if self.work.is_symlink():
            raise ValueError('Revision journal must not be a symlink')
        marker = self.work / 'owner.json'
        if not marker.is_file() or json.loads(marker.read_text()) != {
                'kind': 'local_revision_transaction', 'source': str(self.source)}:
            raise ValueError('Revision work directory is not owned by this snapshot')
        journal = self.work / 'journal.json'
        if journal.exists():
            manifest = json.loads(journal.read_text())
            for item in reversed(manifest['files']):
                path = contained_path(self.source, item['path'], reject_symlinks=True)
                if item['existed']:
                    backup = self.work / 'before' / item['path']
                    # Recovery is restartable: retain the backup until all restores finish.
                    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                        temporary = Path(handle.name)
                    try:
                        shutil.copy2(backup, temporary)
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                else:
                    path.unlink(missing_ok=True)
            for relative in reversed(manifest['directories']):
                directory = contained_path(self.source, relative, reject_symlinks=True)
                if directory.exists() and not any(directory.iterdir()):
                    directory.rmdir()
        if self.work.exists():
            shutil.rmtree(self.work)

    def write(self, path, raw):
        relative = path.relative_to(self.source).as_posix()
        contained_path(self.source, relative, reject_symlinks=True)
        if not self.work.exists():
            with tempfile.TemporaryDirectory(prefix='.revision-owner-', dir=self.source.parent) as name:
                prepared = Path(name)
                (prepared / 'owner.json').write_text(json.dumps({
                    'kind': 'local_revision_transaction', 'source': str(self.source)}))
                prepared.replace(self.work)
        target = self.work / 'after' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        self.updates[relative] = target

    def read(self, path):
        return self.updates.get(path.relative_to(self.source).as_posix(), path)

    def commit(self):
        files, directories = [], []
        for relative in self.updates:
            path = contained_path(self.source, relative, reject_symlinks=True)
            existed = path.exists()
            if existed:
                backup = self.work / 'before' / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            parent = path.parent
            missing = []
            while not parent.exists():
                missing.append(parent.relative_to(self.source).as_posix())
                parent = parent.parent
            for directory in reversed(missing):
                if directory not in directories:
                    directories.append(directory)
            files.append({'path': relative, 'existed': existed})
        journal = self.work / 'journal.json'
        pending = self.work / 'journal.tmp'
        pending.write_text(json.dumps({'files': files, 'directories': directories}))
        pending.replace(journal)
        try:
            # Evidence and receipts are installed before the two current-state files.
            for relative in sorted(self.updates, key=lambda name: name in {'dataset.json', 'results.jsonl'}):
                target = self.source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                self.updates[relative].replace(target)
        except BaseException:
            self.recover()
            raise
        shutil.rmtree(self.work)


def require_local_task_replacement(source, binding, dataset, records):
    """Verify complete new task calls while preserving every unrelated saved row."""
    receipt = json.loads(read_evidence_binding(source, binding).read_text())
    if receipt.get('kind') != 'local_task_replacement' or not receipt.get('reason'):
        raise ValueError('Task replacement needs a declared reason')
    before = json.loads(read_evidence_binding(source, receipt['baseline_dataset']).read_text())
    previous = load_jsonl(read_evidence_binding(source, receipt['baseline_results']))
    cohort = json.loads(read_evidence_binding(source, receipt['cohort_dataset']).read_text())
    calls = load_jsonl(read_evidence_binding(source, receipt['cohort_results']))
    removed, added = receipt['removed_case_id'], receipt['added_case_id']
    old_cases = {c['id']: c for c in before['cases']}
    new_cases = {c['id']: c for c in dataset['cases']}
    if (removed == added or removed not in old_cases or added in old_cases
            or len(old_cases) != len(before['cases']) or len(new_cases) != len(dataset['cases'])
            or set(new_cases) != (set(old_cases) - {removed}) | {added}
            or before.get('coverage_plan') != dataset.get('coverage_plan')
            or any(new_cases[k] != v for k, v in old_cases.items() if k != removed)
            or cohort['cases'] != [new_cases[added]]
            or old_cases[removed].get('track') != new_cases[added].get('track')):
        raise ValueError('Task replacement changed unrelated cases or task family')
    providers = set(before['coverage_plan'][old_cases[removed].get('track', 'web')])
    expected = {(p, added) for p in providers}
    if (len(calls) != len(expected) or receipt.get('provider_calls_added') != len(calls)
            or {(r['provider'], r['case_id']) for r in calls} != expected
            or any(r.get('run') != 1 or r.get('provider_metadata', {}).get('retry') for r in calls)):
        raise ValueError('Task replacement needs one new call from every method')
    inherited = [r for r in previous if r['case_id'] != removed]
    rows = {r['attempt_id']: r for r in records}
    wanted = {r['attempt_id']: r for r in inherited + calls}
    if (len(rows) != len(records) or len(wanted) != len(inherited) + len(calls)
            or len(records) != len(previous) or rows != wanted):
        raise ValueError('Task replacement altered an inherited or newly measured result')
    if any(r['metrics'].get('evaluation_excluded') for r in calls):
        raise ValueError('Replacement task cannot be excluded after seeing its results')
    from .local_execution_contract import require_local_execution_contract
    if not receipt.get('execution_contract') or not receipt.get('cohort_execution'):
        raise ValueError('Task replacement requires its complete execution contract')
    contract = json.loads(read_evidence_binding(source, receipt['execution_contract']).read_text())
    execution = json.loads(read_evidence_binding(source, receipt['cohort_execution']).read_text())
    if not contract.get('runner_inputs'):
        raise ValueError('Task replacement requires retained runner inputs and source observations')
    require_local_execution_contract(source, cohort, calls, contract, execution)
    return {identifier: row['metrics']['scoring_source_sha256']
            for identifier, row in rows.items()}


def validate_revision_chain(source, binding, dataset=None, records=None):
    """Validate each transition, then expose the earliest replacement inputs."""
    if dataset is None:
        dataset = json.loads((source / 'dataset.json').read_text())
    if records is None:
        records = load_jsonl(source / 'results.jsonl')
    receipt = json.loads(read_evidence_binding(source, binding).read_text())
    if not isinstance(receipt, dict):
        raise ValueError('revision must be an object')
    if receipt.get('kind') == 'current_local_snapshot_authority':
        return require_current_snapshot_authority(source, receipt, dataset, records), None, None
    RevisionStep.from_mapping(receipt)
    outputs = {name: file_sha256(read_evidence_binding(source, {
        'path': name, 'sha256': receipt[key]}))
        for name, key in [('dataset.json', 'output_dataset_sha256'),
                          ('results.jsonl', 'output_results_sha256')]}
    seen, latest_scoring = set(), None
    while True:
        identity = (binding['path'], binding['sha256'])
        if identity in seen:
            raise ValueError('Local score revision lineage repeats a receipt')
        seen.add(identity)
        receipt = json.loads(read_evidence_binding(source, binding).read_text())
        expected, before, previous = _require_revision_step(source, receipt, dataset, records, outputs)
        if latest_scoring is None:
            latest_scoring = expected
        parent = receipt.get('predecessor_revision')
        if parent is None:
            return latest_scoring, receipt['input_dataset'], receipt['input_results']
        outputs = {'dataset.json': receipt['input_dataset']['sha256'],
                   'results.jsonl': receipt['input_results']['sha256']}
        dataset, records, binding = before, previous, parent


def require_local_revision(source, binding, dataset, records):
    return validate_revision_chain(source, binding, dataset, records)[0]


def _current_row_identity(case, record):
    from .result_review import review_input_identity
    return {'attempt_id': record['attempt_id'], 'case_sha256': canonical_json_sha256(case),
            'record_sha256': canonical_json_sha256(record),
            'scoring_source_sha256': record['metrics']['scoring_source_sha256'],
            'review_input': review_input_identity(record)}


def _require_current_row_evidence(source, record, diagnostic_projection=None):
    from .result_review import review_input_identity
    identity = review_input_identity(record)
    read_evidence_binding(source, {'path': record['artifact_path'],
                        'sha256': record['metrics']['content_sha256']})
    for item in identity['raw_artifacts']:
        if item:
            read_evidence_binding(source, item)
    diagnostic = identity['diagnostic_log']
    if diagnostic:
        if diagnostic_projection is None:
            read_evidence_binding(source, diagnostic)
        else:
            _require_diagnostic_projection(source, record, diagnostic_projection)


def _require_diagnostic_projection(source, record, proof):
    from .result_review import review_input_identity
    diagnostic = review_input_identity(record)['diagnostic_log']
    call = record.get('provider_metadata', {}).get('call_log')
    if (proof.get('attempt_id') != record['attempt_id']
            or proof.get('kind') != 'private_diagnostic_public_runtime_projection'
            or proof.get('original') != diagnostic or proof.get('call_log') != call
            or proof.get('original_verified_at_compaction') is not True
            or proof.get('verification_scope') != 'public_view_and_disclosed_original_identity'
            or proof.get('original_retained_privately') is not True or not call):
        raise ValueError('Current diagnostic projection lost original or call identity')
    public = proof.get('public', {})
    equal_bytes = public.get('sha256') == diagnostic['sha256'] and public.get('bytes') == diagnostic['bytes']
    if proof.get('public_bytes_equal_original') is not equal_bytes:
        raise ValueError('Current diagnostic projection confuses public and original bytes')
    if public.get('path') != 'logs/'+record['attempt_id']+'.debug.txt':
        raise ValueError('Current diagnostic projection selects another attempt')
    path = read_evidence_binding(source, public)
    if path.stat().st_size != public.get('bytes'):
        raise ValueError('Current public diagnostic size changed')
    payload = json.loads(read_evidence_binding(source, call).read_text())
    if any(payload.get('identity', {}).get(key) != record.get(key)
           for key in ('attempt_id', 'provider', 'case_id', 'run')):
        raise ValueError('Current diagnostic call belongs to another result')


def _compact_diagnostic_projections(source, records, private_manifest, *, public_log_updates=None):
    """Verify private originals once; emit honest public-view receipts only."""
    from .result_review import review_input_identity
    from .report import runtime_log_text
    from .runner import _public_text
    from .publication_privacy import project_file
    private = None
    bound_public_logs = set()
    def collect_paths(value):
        if isinstance(value, dict):
            for item in value.values():
                collect_paths(item)
        elif isinstance(value, list):
            for item in value:
                collect_paths(item)
        elif isinstance(value, str) and value.startswith('logs/') and value.endswith('.debug.txt'):
            bound_public_logs.add(value)
    collect_paths(records)
    if private_manifest is not None:
        manifest_path = Path(private_manifest)
        if manifest_path.is_symlink():
            raise ValueError('Private diagnostic manifest must not be a symlink')
        document = json.loads(manifest_path.read_text())
        if (document.get('kind') != 'current_private_originals'
                or document.get('results_sha256') != file_sha256(source/'results.jsonl')):
            raise ValueError('Private originals do not bind current results')
        private = {(item['relative_path'], item['sha256']): item for item in document['bindings']}
    proofs = []
    for record in records:
        diagnostic = review_input_identity(record)['diagnostic_log']
        if not diagnostic:
            continue
        original = contained_path(source, diagnostic['path'], reject_symlinks=True)
        if not original.is_file():
            item = (private or {}).get((diagnostic['path'], diagnostic['sha256']))
            if (item is None or record['attempt_id'] not in item['attempt_ids']
                    or 'diagnostic_log' not in item['roles'] or item['bytes'] != diagnostic['bytes']):
                raise ValueError('Current private diagnostic lacks its actual original')
            original = contained_path(manifest_path.parent, item['stored_path'], reject_symlinks=True)
        raw = original.read_bytes()
        if len(raw) != diagnostic['bytes'] or hashlib.sha256(raw).hexdigest() != diagnostic['sha256']:
            raise ValueError('Current private diagnostic bytes changed')
        relative = 'logs/'+record['attempt_id']+'.debug.txt'
        path = contained_path(source, relative, reject_symlinks=True)
        if not path.is_file():
            # Non-public local snapshots can still verify their actual originals.
            if public_log_updates is None:
                if private_manifest is not None:
                    raise ValueError('Current public diagnostic projection is missing: '+record['attempt_id'])
                continue
        text = runtime_log_text(raw.decode('utf-8', errors='replace'))
        expected = (_public_text(text, limit=len(text)) or '').encode()
        actual = path.read_bytes() if path.is_file() else None
        projected = expected if actual == expected else project_file(relative, expected)[0]
        if actual != expected and actual != projected:
            if public_log_updates is None:
                raise ValueError('Current public diagnostic differs from shared projection: '+record['attempt_id'])
            # Only the derived debug view may change. A measured row or public
            # call must not directly identify these old public bytes.
            if (relative in bound_public_logs
                    or relative in read_evidence_binding(source, record['provider_metadata']['call_log']).read_text()):
                raise ValueError('Derived diagnostic is bound as measured evidence')
            public_log_updates[path] = projected
            actual = projected
        proof = {'attempt_id': record['attempt_id'],
            'kind': 'private_diagnostic_public_runtime_projection', 'original': diagnostic,
            'public': {'path': relative, 'sha256': hashlib.sha256(actual).hexdigest(), 'bytes': len(actual)},
            'call_log': record.get('provider_metadata', {}).get('call_log'),
            'original_verified_at_compaction': True,
            'verification_scope': 'public_view_and_disclosed_original_identity',
            'public_bytes_equal_original': hashlib.sha256(actual).hexdigest() == diagnostic['sha256']
                                           and len(actual) == diagnostic['bytes'],
            'original_retained_privately': True}
        if path not in (public_log_updates or {}):
            _require_diagnostic_projection(source, record, proof)
        proofs.append(proof)
    return proofs


def compact_revision_authority(source, binding, dataset=None, records=None, *,
                               reference_authorities=(), task_replacement=None,
                               private_original_manifest=None):
    dataset = dataset if dataset is not None else json.loads((source/'dataset.json').read_text())
    records = records if records is not None else load_jsonl(source/'results.jsonl')
    validated = validate_revision_chain(source, binding, dataset, records)
    return _compact_revision_authority(source, binding, dataset, records, validated,
        reference_authorities=reference_authorities, task_replacement=task_replacement,
        private_original_manifest=private_original_manifest)


def _compact_revision_authority(source, binding, dataset, records, validated, *,
                                reference_authorities=(), task_replacement=None,
                                private_original_manifest=None, _diagnostics=None):
    """Validate history once, then describe only the current state and its causes.

    The returned authority deliberately cannot reconstruct or revalidate removed
    transitions. It retains real current source identities, never relabels scores,
    and contains neither before images nor reverse patches. The caller installs
    and hash-binds this document before removing superseded files.
    """
    dataset = dataset if dataset is not None else json.loads((source/'dataset.json').read_text())
    records = records if records is not None else load_jsonl(source/'results.jsonl')
    sources, original_dataset, original_results = validated
    replacement_scope = None
    if task_replacement:
        if original_dataset is None:
            parent = json.loads(read_evidence_binding(source, binding).read_text())
            require_current_task_replacement(source, parent, dataset, records)
            replacement_scope = parent['task_replacement_current_scope']
        else:
            require_local_task_replacement(source, task_replacement,
                json.loads(read_evidence_binding(source, original_dataset).read_text()),
                load_jsonl(read_evidence_binding(source, original_results)))
            replacement = json.loads(read_evidence_binding(source, task_replacement).read_text())
            replacement_scope = {'added_case_id': replacement['added_case_id'],
                'removed_case_id': replacement['removed_case_id'],
                'current_attempt_ids': sorted(row['attempt_id'] for row in records
                                             if row['case_id'] == replacement['added_case_id']),
                'execution_contract': replacement['execution_contract'],
                'execution': replacement['cohort_execution'],
                'pre_captures': replacement['pre_captures'],
                'post_captures': replacement['post_captures'],
                'source_capture_counts': {phase: len(replacement[phase])
                                          for phase in ('pre_captures', 'post_captures')},
                'qualification_state': replacement.get('reference_scope_review', {}).get('result'),
                'historical_transition_validated_before_compaction': True}
    causes = {}
    cursor = binding
    current_source = None
    while cursor:
        receipt = json.loads(read_evidence_binding(source, cursor).read_text())
        if receipt.get('kind') == 'current_local_snapshot_authority':
            for cause in receipt['correction_authorities']:
                causes[canonical_json_sha256(cause)] = cause
            break
        authority = receipt['authority']
        if current_source is None:
            current_source = receipt['scoring_source_sha256']
        if receipt['scoring_source_sha256'] == current_source:
            roots = authority.get('root_cause_bindings') or []
            for root in roots or [None]:
                cause = {'description': root['root_cause'] if root else authority['description'],
                    'regression_ids': root['regression_ids'] if root else receipt['regression_ids'],
                    'root_cause_bindings': [root] if root else []}
                if authority.get('source_commit'):
                    cause['source_commit'] = authority['source_commit']
                causes[canonical_json_sha256(cause)] = cause
        cursor = receipt.get('predecessor_revision')
    cases = {case['id']: case for case in dataset['cases']}
    result = {'kind': 'current_local_snapshot_authority', 'schema_version': 1,
        'provider_calls_added': 0, 'validation_scope': 'current_state_only',
        'history_validated_before_compaction': True,
        'dataset_sha256': file_sha256(source/'dataset.json'),
        'results_sha256': file_sha256(source/'results.jsonl'),
        'correction_authorities': list(causes.values()),
        'current_reference_authorities': list(reference_authorities),
        'diagnostic_projections': (_diagnostics if _diagnostics is not None else
            _compact_diagnostic_projections(source, records, private_original_manifest)),
        'attempts': [_current_row_identity(cases[row['case_id']], row) for row in records]}
    review = dataset.get('comparison', {}).get('independent_review')
    if review is None and (source/'result-review.json').is_file():
        review = {'path': 'result-review.json',
                  'sha256': file_sha256(source/'result-review.json')}
    if review is not None:
        result['current_review'] = review
    if (source/'execution.json').is_file():
        result['current_execution'] = {'path': 'execution.json',
            'sha256': file_sha256(source/'execution.json')}
    expected = require_current_snapshot_authority(source, result, dataset, records)
    if expected != sources:
        raise ValueError('Compaction changed actual scoring source identities')
    if replacement_scope is not None:
        result['task_replacement_current_scope'] = replacement_scope
        require_current_task_replacement(source, result, dataset, records)
    return result


def require_current_task_replacement(source, receipt, dataset, records):
    """Validate the explicit current scope after its historical gate was checked."""
    scope = receipt.get('task_replacement_current_scope', {})
    cases = {case['id'] for case in dataset['cases']}
    identifiers = sorted(row['attempt_id'] for row in records
                         if row['case_id'] == scope.get('added_case_id'))
    if (receipt.get('kind') != 'current_local_snapshot_authority'
            or receipt.get('schema_version') != 1
            or scope.get('historical_transition_validated_before_compaction') is not True
            or scope.get('added_case_id') not in cases
            or scope.get('removed_case_id') in cases
            or not identifiers or scope.get('current_attempt_ids') != identifiers
            or not scope.get('execution_contract') or not scope.get('execution')
            or not scope.get('qualification_state')):
        raise ValueError('Current task replacement scope is missing or differs')
    for phase in ('pre_captures', 'post_captures'):
        captures = scope.get(phase)
        if (not isinstance(captures, list) or not captures
                or len(captures) != scope.get('source_capture_counts', {}).get(phase)):
            raise ValueError('Current task replacement lacks source qualification')
        for capture in captures:
            read_evidence_binding(source, capture)
    contract = json.loads(read_evidence_binding(source, scope['execution_contract']).read_text())
    execution = json.loads(read_evidence_binding(source, scope['execution']).read_text())
    from .local_execution_contract import local_measurement_cases, require_local_execution_contract
    frozen = local_measurement_cases(contract, source)
    current = {case['id']: case for case in dataset['cases']}
    if {case['id'] for case in frozen} != {scope['added_case_id']}:
        raise ValueError('Current replacement contract has a different task')
    # Score-only corrections may change requirements; frozen acquisition inputs
    # and all original physical-call constraints still apply to current calls.
    scoring_fields = ALLOWED_FIELDS | {'diagnostic_any', 'acceptance_policy_version'}
    for case in frozen:
        if ({k: v for k, v in case.items() if k not in scoring_fields}
                != {k: v for k, v in current[case['id']].items() if k not in scoring_fields}):
            raise ValueError('Current replacement acquisition contract changed')
    selected = [row for row in records if row['case_id'] == scope['added_case_id']]
    require_local_execution_contract(source, {'cases': frozen}, selected, contract, execution)


def require_current_snapshot_authority(source, receipt, dataset, records):
    """Verify current bytes and live evidence, without reading deleted history."""
    if (receipt.get('kind') != 'current_local_snapshot_authority'
            or receipt.get('schema_version') != 1
            or receipt.get('provider_calls_added') != 0
            or receipt.get('validation_scope') != 'current_state_only'
            or receipt.get('history_validated_before_compaction') is not True):
        raise ValueError('Invalid compact current snapshot authority')
    for name, key in [('dataset.json', 'dataset_sha256'), ('results.jsonl', 'results_sha256')]:
        read_evidence_binding(source, {'path': name, 'sha256': receipt.get(key)},
                              allow_public_view=False)
    if (json.loads((source/'dataset.json').read_text()) != dataset
            or load_jsonl(source/'results.jsonl') != records):
        raise ValueError('Current snapshot arguments differ from bound bytes')
    cases = {case['id']: case for case in dataset['cases']}
    rows = {row['attempt_id']: row for row in records}
    attempts = receipt.get('attempts', [])
    identities = {row['attempt_id']: row for row in attempts}
    if (len(cases) != len(dataset['cases']) or len(rows) != len(records)
            or len(identities) != len(attempts) or set(identities) != set(rows)):
        raise ValueError('Current snapshot inventory differs')
    causes = receipt.get('correction_authorities', [])
    if not causes:
        raise ValueError('Current snapshot needs correction authority')
    for cause in causes:
        if not cause.get('description') or not cause.get('regression_ids'):
            raise ValueError('Current correction lacks cause or executable controls')
        _require_revision_regressions(cause['regression_ids'])
    for authority in receipt.get('current_reference_authorities', []):
        document = json.loads(read_evidence_binding(source, authority).read_text())
        def require_material(value):
            if isinstance(value, dict):
                if 'path' in value and 'sha256' in value:
                    read_evidence_binding(source, value)
                for item in value.values():
                    require_material(item)
            elif isinstance(value, list):
                for item in value:
                    require_material(item)
        require_material(document)
    projections = receipt.get('diagnostic_projections', [])
    indexed_projections = {item['attempt_id']: item for item in projections}
    if len(indexed_projections) != len(projections) or not set(indexed_projections) <= set(rows):
        raise ValueError('Current diagnostic projection scope differs')
    for identifier, row in rows.items():
        if (row['case_id'] not in cases
                or identities[identifier] != _current_row_identity(cases[row['case_id']], row)):
            raise ValueError('Current snapshot row, case or source changed: ' + identifier)
        _require_current_row_evidence(source, row, indexed_projections.get(identifier))
    if receipt.get('task_replacement_current_scope') is not None:
        require_current_task_replacement(source, receipt, dataset, records)
    execution = source/'execution.json'
    if execution.is_file():
        execution_binding = receipt.get('current_execution')
        if not execution_binding or read_evidence_binding(source, execution_binding) != execution:
            raise ValueError('Current execution inventory lacks its actual binding')
        from collections import Counter
        physical = Counter(identifier for batch in json.loads(execution.read_text())['batches']
                           for identifier in batch['attempt_ids'])
        if physical != Counter(row['attempt_id'] for row in records):
            raise ValueError('Current execution physical/logical inventory differs')
        from .native_routes import verify_native_execution_manifests
        verify_native_execution_manifests(source, json.loads(execution.read_text())['batches'],
                                         dataset, records)
    from .adjudication_evidence import _require_local_review
    review = receipt.get('current_review')
    declared = dataset.get('comparison', {}).get('independent_review')
    if declared is not None and declared != review:
        raise ValueError('Current review binding differs from current authority')
    if (source/'result-review.json').is_file() and review is None:
        raise ValueError('Current authority omitted the saved review')
    document = json.loads(read_evidence_binding(source, review).read_text()) if review else None
    _require_local_review(source, dataset, records, review_document=document,
        scoring_sources={key: value['scoring_source_sha256'] for key, value in identities.items()})
    return {key: value['scoring_source_sha256'] for key, value in identities.items()}


def _install_compact_local_snapshot(source, binding, *, reference_authorities=(), task_replacement=None,
                           authority_path='local-evidence/offline-revisions/current/receipt.json',
                           retained_path='local-evidence/retained-review-current.json',
                           comparison_updates=None, private_original_manifest=None,
                           _validated_history=None, _diagnostics=None):
    """Install current authority after validating history; never delete user files.

    Only review dependency bindings change. Calls, scores, contracts and review
    opinions remain exact. The caller may delete obsolete states after this
    succeeds and the ordinary presentation boundary accepts the returned binding.
    """
    from .adjudication_evidence import compact_retained_review_dependencies
    source = Path(source)
    dataset = json.loads((source/'dataset.json').read_text())
    records = load_jsonl(source/'results.jsonl')
    authority = _compact_revision_authority(source, binding, dataset, records, _validated_history,
        reference_authorities=reference_authorities, task_replacement=task_replacement,
        private_original_manifest=private_original_manifest, _diagnostics=_diagnostics)
    review_binding = authority.get('current_review')
    updates = {}
    if review_binding:
        read_evidence_binding(source, review_binding)
        review_path = contained_path(source, review_binding['path'], reject_symlinks=True)
        review = json.loads(review_path.read_text())
        if review.get('retained_reviews'):
            retained = compact_retained_review_dependencies(source, dataset, records, review)
            retained_bytes = (json.dumps(retained, ensure_ascii=False, indent=2)+'\n').encode()
            updates[contained_path(source, retained_path, reject_symlinks=True)] = retained_bytes
            review['retained_reviews'] = {'path': retained_path,
                'sha256': hashlib.sha256(retained_bytes).hexdigest()}
        review.pop('retained_assertion_refresh', None)
        review['review_rounds'] = [{
            'title': '当前结果复核', 'title_en': 'Current result review',
            'scope': f"已复核 {review['reviewed_attempts']}/{len(records)} 条；"
                     f"其中 {review.get('fresh_blind_reviewed_attempts', 0)} 条独立盲核，"
                     f"{review.get('renewed_context_reviewed_attempts', 0)} 条已知结论复核，"
                     f"{review.get('retained_reviewed_attempts', 0)} 条沿用原意见。",
            'scope_en': f"{review['reviewed_attempts']}/{len(records)} reviewed; "
                        f"{review.get('fresh_blind_reviewed_attempts', 0)} blind judgments, "
                        f"{review.get('renewed_context_reviewed_attempts', 0)} known-outcome reviews, "
                        f"{review.get('retained_reviewed_attempts', 0)} original opinions retained.",
            'reviewed_attempts': review['reviewed_attempts'], 'total_attempts': len(records),
            'retained_attempts': review.get('retained_reviewed_attempts', 0),
            'unresolved': review['unresolved'], 'provider_calls_added': 0}]
        supplements = []
        for supplement_binding in review.get('supplemental_reviews', []):
            document = json.loads(read_evidence_binding(source, supplement_binding).read_text())
            kept = {key: document[key] for key in ('kind', 'results_sha256', 'provider_calls_added',
                'reviewed_attempts', 'reviewer', 'review_protocol', 'attempts')}
            if document.get('blind_initial_outputs'):
                kept['blind_initial_outputs'] = document['blind_initial_outputs']
            content = (json.dumps(kept, ensure_ascii=False, indent=2)+'\n').encode()
            path = contained_path(source, supplement_binding['path'], reject_symlinks=True)
            updates[path] = content
            supplements.append({**supplement_binding, 'sha256': hashlib.sha256(content).hexdigest()})
        if supplements:
            review['supplemental_reviews'] = supplements
        review_bytes = (json.dumps(review, ensure_ascii=False, indent=2)+'\n').encode()
        updates[review_path] = review_bytes
        review_binding = {**review_binding, 'sha256': hashlib.sha256(review_bytes).hexdigest()}
        authority['current_review'] = review_binding
        dataset.setdefault('comparison', {})['independent_review'] = review_binding
    comparison = dataset.setdefault('comparison', {})
    for key in ('historical_independent_review', 'inline_card_resolution', 'prompt_policy',
                'result_ledger', 'task_replacement'):
        comparison.pop(key, None)
    if authority.get('task_replacement_current_scope'):
        comparison['task_replacement_current_scope'] = authority['task_replacement_current_scope']
    page_binding = comparison.get('page_identity_review')
    if page_binding:
        old = json.loads(read_evidence_binding(source, page_binding).read_text())
        page = {key: old[key] for key in ('kind', 'provider_calls_added', 'captures_path',
                                          'captures_sha256', 'attempts')}
        page_path = 'local-evidence/page-identity-review/current.json'
        page_bytes = (json.dumps(page, ensure_ascii=False, indent=2)+'\n').encode()
        updates[contained_path(source, page_path, reject_symlinks=True)] = page_bytes
        comparison['page_identity_review'] = {'path': page_path,
            'sha256': hashlib.sha256(page_bytes).hexdigest()}
    if set(comparison_updates or {}) - {'call_chain_audit'}:
        raise ValueError('Compaction comparison updates must be current explanatory audit text')
    comparison.update(comparison_updates or {})
    dataset_bytes = (json.dumps(dataset, ensure_ascii=False, indent=2)+'\n').encode()
    updates[source/'dataset.json'] = dataset_bytes
    authority['dataset_sha256'] = hashlib.sha256(dataset_bytes).hexdigest()
    authority_bytes = (json.dumps(authority, ensure_ascii=False, indent=2)+'\n').encode()
    destination = contained_path(source, authority_path, reject_symlinks=True)
    updates[destination] = authority_bytes
    originals = {path: path.read_bytes() if path.exists() else None for path in updates}
    try:
        for path, content in updates.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        require_current_snapshot_authority(source, authority, dataset, records)
    except Exception:
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    return {'path': authority_path, 'sha256': hashlib.sha256(authority_bytes).hexdigest()}


def compact_local_snapshot(source, binding, **options):
    """Validate the old state, then install its current-only authority atomically."""
    source = Path(source)
    if any(key.startswith('_') for key in options):
        raise ValueError('Internal validation state cannot be supplied by a caller')
    dataset = json.loads((source/'dataset.json').read_text())
    records = load_jsonl(source/'results.jsonl')
    validated_history = validate_revision_chain(source, binding, dataset, records)
    execution_path = source/'execution.json'
    original_execution = execution_path.read_bytes() if execution_path.is_file() else None
    if original_execution is not None:
        from collections import Counter
        execution = json.loads(original_execution)
        current = {row['attempt_id'] for row in records}
        physical = Counter(identifier for batch in execution['batches']
                           for identifier in batch['attempt_ids'])
        retired = set(physical) - current
        if retired:
            replacement = options.get('task_replacement')
            if not replacement:
                raise ValueError('Current execution has undeclared retired calls')
            old = json.loads(read_evidence_binding(source, replacement).read_text())
            removed = {row['attempt_id'] for row in load_jsonl(read_evidence_binding(source, old['baseline_results']))
                       if row['case_id'] == old['removed_case_id']}
            if retired != removed or any(physical[key] != 1 for key in retired):
                raise ValueError('Current execution has unknown retired calls')
            for batch in execution['batches']:
                identifiers = batch['attempt_ids']
                batch['attempt_ids'] = [key for key in identifiers if key in current]
                if len(batch['attempt_ids']) != len(identifiers):
                    batch['attempt_count'] = len(batch['attempt_ids'])
                    batch['inventory_scope'] = 'current_snapshot_attempts'
            execution['batches'] = [batch for batch in execution['batches'] if batch['attempt_ids']]
            aggregate = execution.get('aggregate', {})
            if 'case_ids' in aggregate:
                aggregate['case_ids'] = [key for key in aggregate['case_ids']
                                         if key != old['removed_case_id']]
            aggregate['batch_count'] = len(execution['batches'])
            execution['inventory_scope'] = 'current_snapshot_attempts'
            execution_path.write_text(json.dumps(execution, ensure_ascii=False, indent=2)+'\n')
    import tempfile
    import shutil
    temporary = tempfile.TemporaryDirectory(prefix='current-diagnostic-projection-')
    backups = {}
    try:
        changed_logs = {}
        diagnostic_proofs = _compact_diagnostic_projections(source, records,
            options.get('private_original_manifest'), public_log_updates=changed_logs)
        for index, (path, content) in enumerate(changed_logs.items()):
            backup = Path(temporary.name)/str(index)
            if path.exists():
                shutil.copyfile(path, backup)
                backups[path] = backup
            else:
                backups[path] = None
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return _install_compact_local_snapshot(source, binding,
            _validated_history=validated_history, _diagnostics=diagnostic_proofs, **options)
    except Exception:
        for path, backup in backups.items():
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                shutil.copyfile(backup, path)
        if original_execution is not None:
            execution_path.write_bytes(original_execution)
        raise
    finally:
        temporary.cleanup()


def _project_native_correction(source, original, request, *, historical_scoring_source=None):
    """One projection path for preparing and verifying an offline correction."""
    from .native_projection import project_native_answer, project_native_result_fields
    from .publication_privacy import original_evidence_size
    metadata = original.get('provider_metadata', {})
    native = read_evidence_binding(source, request['native_response'])
    if (request['native_response']['sha256'] != metadata.get('raw_response_sha256')
            or original_evidence_size(source, request['native_response']) != metadata.get('raw_response_bytes')):
        raise ValueError('Native answer correction changed its original response')
    declaration = request['projection']
    payload = (native.read_bytes() if declaration.get('source_format') == 'bytes'
               else json.loads(native.read_bytes()))
    text, fields = project_native_answer(payload, declaration, historical_scoring_source=historical_scoring_source)
    diagnostic = None
    if any(spec.get('format') == 'fetch_log_final_url'
           for spec in declaration.get('result_fields', {}).values()):
        diagnostic = read_evidence_binding(source, {'path': metadata['diagnostic_log_path'],
            'sha256': metadata['diagnostic_log_sha256']}).read_bytes()
    result_fields = (project_native_result_fields(payload, declaration,
        request_url=original.get('url'), diagnostic=diagnostic,
        transport_receipt=metadata.get('source_transport_receipt'))
        if 'result_fields' in declaration else {})
    for field in declaration.get('answer_fields', {}).values():
        if (field.get('format') == 'html_attribute_urls'
                and field.get('base_url') != (result_fields.get('final_url') or original['url'])):
            raise ValueError('Native URL projection must use the verified result or request URL')
    return native, text, fields, result_fields


def _require_native_answer_correction(source, original, corrected, proof, *, historical_scoring_source=None):
    """Verify a derived answer against the same immutable native response."""
    if proof.get('attempt_id') != original['attempt_id']:
        raise ValueError('Native answer correction lost its attempt identity')
    native, text, fields, result_fields = _project_native_correction(source, original, proof, historical_scoring_source=historical_scoring_source)
    metadata = original.get('provider_metadata', {})
    before = proof['original_artifact']
    after = proof['corrected_artifact']
    if (before['path'] != original['artifact_path']
            or before['sha256'] != original['metrics']['content_sha256']
            or after['path'] != corrected['artifact_path']
            or after['sha256'] != corrected['metrics']['content_sha256']
            or before['path'] == after['path']):
        raise ValueError('Native answer correction must preserve its old artifact')
    read_evidence_binding(source, before)
    if any(corrected.get(key) != value for key, value in result_fields.items()):
        raise ValueError('Native result context correction differs from original fields')
    after_path = read_evidence_binding(source, after)
    if file_sha256(native) != proof['native_response']['sha256']:
        # A disclosed native public view projects public answer bytes. Compare
        # its explicitly paired answer view even while private scoring bytes
        # are temporarily restored; neither view reconstructs the original.
        from .publication_privacy import bound_public_view
        native_view = bound_public_view(source, proof['native_response'])
        answer_view = bound_public_view(source, after)
        if (native_view is None or answer_view is None
                or native_view[0] != native
                or native_view[1]['original_sha256'] != proof['native_response']['sha256']
                or answer_view[1]['original_sha256'] != after['sha256']):
            raise ValueError('Native public projection requires its bound public answer pair')
        after_path = answer_view[0]
    if after_path.read_bytes() != text.encode('utf-8', errors='replace'):
        raise ValueError('Native answer correction differs from declared original fields')
    expected_metadata = {**metadata, **fields, 'native_answer_projection': proof['projection']}
    if corrected.get('provider_metadata') != expected_metadata:
        raise ValueError('Native answer correction changed unrelated response metadata')
    derived = {'metrics', 'scoring', 'diagnosis', 'failure_category',
               'artifact_path', 'provider_metadata'} | set(result_fields)
    if ({k: v for k, v in original.items() if k not in derived}
            != {k: v for k, v in corrected.items() if k not in derived}):
        raise ValueError('Native answer correction changed physical call facts')


def _native_context_proof(source, original, request):
    """Replay context from one bound physical call, preserving its answer bytes."""
    from .providers.moli import replay_native_http_error_context
    from .publication_privacy import original_evidence_size
    if (set(request) != {'attempt_id', 'stdout', 'diagnostic', 'call_log', 'projection'}
            or request['attempt_id'] != original['attempt_id']
            or original.get('provider_type') != 'moli_webfetch'):
        raise ValueError('Native context correction has an invalid call scope')
    stdout = request['stdout']
    metadata = original.get('provider_metadata', {})
    if (stdout['path'] != original['artifact_path']
            or stdout['sha256'] != original['metrics']['content_sha256']
            or any(request['call_log'].get(k) != metadata.get('call_log', {}).get(k)
                   for k in ('path', 'sha256'))):
        raise ValueError('Native context correction changed original output or call binding')
    raw = read_evidence_binding(source, stdout).read_bytes()
    call = json.loads(read_evidence_binding(source, request['call_log']).read_bytes())
    identity, process = call.get('identity', {}), call.get('process', {})
    output, capture = call.get('output', {}), call.get('capture', {})
    if (identity.get('attempt_id') != original['attempt_id']
            or identity.get('case_id') != original['case_id']
            or identity.get('provider') != original['provider']
            or call.get('request', {}).get('url') != original.get('url')
            or call.get('request', {}).get('route') != 'ordinary_markdown'
            or output.get('path') != stdout['path']
            or output.get('sha256') != stdout['sha256']
            or output.get('bytes') != original_evidence_size(source, stdout)
            or process.get('stdout_truncated') is not False
            or process.get('stderr_truncated') is not False
            or capture.get('process_stdout') != 'bound_as_output'
            or capture.get('process_stderr') != 'captured_complete'
            or not isinstance(process.get('stderr'), str)):
        raise ValueError('Native context correction lacks complete same-call ownership')
    diagnostic = read_evidence_binding(source, request['diagnostic']).read_bytes()
    if diagnostic != process['stderr'].encode('utf-8'):
        raise ValueError('Native context diagnostic differs from bound physical call')
    fields = replay_native_http_error_context(raw, diagnostic, original['url'],
        process.get('exit_code'), request['projection'])
    before = {key: original.get(key) for key in fields}
    if before == fields:
        raise ValueError('Native context correction does not change request context')
    return {**request, 'original_context': before, 'corrected_context': fields,
            'diagnostic_source': 'bound_physical_call_process_stderr',
            'historical_diagnostic': {key: metadata.get(key) for key in
                ('diagnostic_log_path', 'diagnostic_log_sha256')}}, fields


def _require_native_context_correction(source, original, corrected, proof):
    request = {key: proof[key] for key in
        ('attempt_id', 'stdout', 'diagnostic', 'call_log', 'projection')}
    expected, fields = _native_context_proof(source, original, request)
    if proof != expected or any(corrected.get(key) != value for key, value in fields.items()):
        raise ValueError('Native context correction differs from replayed same-call context')
    derived = {'metrics', 'scoring', 'diagnosis', 'failure_category'} | set(fields)
    if ({key: value for key, value in original.items() if key not in derived}
            != {key: value for key, value in corrected.items() if key not in derived}
            or original['metrics']['content_sha256'] != corrected['metrics']['content_sha256']):
        raise ValueError('Native context correction changed physical call facts or answer')


def _read_input_verdicts(source, binding, input_results_sha256, identifiers):
    """Validate frozen input provenance before rescoring or installing history."""
    proof = json.loads(read_evidence_binding(source, binding).read_text())
    verdicts = proof.get('verdicts', {})
    if (proof.get('input_results_sha256') != input_results_sha256
            or set(verdicts) != set(identifiers)
            or any(type(value) is not bool for value in verdicts.values())
            or not proof.get('frozen_predicate') or not proof.get('snapshot_manifest')):
        raise ValueError('Frozen input verdict proof is incomplete')
    read_evidence_binding(source, proof['frozen_predicate'])
    read_evidence_binding(source, proof['snapshot_manifest'])
    return verdicts


def _require_revision_step(source, receipt, dataset, records, outputs):
    parsed = RevisionStep.from_mapping(receipt)
    if receipt.get('kind') != 'local_offline_score_revision' or receipt.get('provider_calls_added') != 0:
        raise ValueError('Local revision must preserve original provider calls')
    before_dataset = json.loads(read_evidence_binding(source, receipt['input_dataset']).read_text())
    previous = load_jsonl(read_evidence_binding(source, receipt['input_results']))
    for name, key in [('dataset.json', 'output_dataset_sha256'), ('results.jsonl', 'output_results_sha256')]:
        if outputs[name] != receipt.get(key):
            raise ValueError('Local revision output binding changed: ' + name)
    regressions = receipt.get('regression_ids')
    if not regressions:
        raise ValueError('Local revision requires executable regression provenance')
    _require_revision_regressions(regressions)
    old_cases = {case['id']: case for case in before_dataset['cases']}
    new_cases = {case['id']: case for case in dataset['cases']}
    if (len(old_cases) != len(before_dataset['cases']) or len(new_cases) != len(dataset['cases'])
            or set(old_cases) != set(new_cases)
            or before_dataset.get('coverage_plan') != dataset.get('coverage_plan')):
        raise ValueError('Local revision changed the original case/method matrix')
    changed_cases = set()
    for identifier, old in old_cases.items():
        new = new_cases[identifier]
        fields = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
        if fields - (ALLOWED_FIELDS | {'diagnostic_any', 'acceptance_policy_version'}):
            raise ValueError('Local score revision changed source or acquisition inputs')
        if fields:
            changed_cases.add(identifier)
    if changed_cases != set(parsed.changed_case_ids):
        raise ValueError('Local revision case scope differs')
    if changed_cases and receipt.get('rule_revision') is not True:
        raise ValueError('Changed acceptance contract requires an explicit rule revision')
    old_rows = {row['attempt_id']: row for row in previous}
    new_rows = {row['attempt_id']: row for row in records}
    if len(old_rows) != len(previous) or len(new_rows) != len(records) or set(old_rows) != set(new_rows):
        raise ValueError('Local revision changed the call inventory')
    scored = set(parsed.rescored_attempt_ids)
    if not scored <= set(new_rows):
        raise ValueError('Local revision rescoring scope differs')
    native_proofs = receipt.get('native_answer_corrections', [])
    corrected_answers = {item['attempt_id']: item for item in native_proofs}
    if (len(corrected_answers) != len(native_proofs)
            or not set(corrected_answers) <= scored):
        raise ValueError('Native answer correction scope differs from rescoring scope')
    context_proofs = receipt.get('native_context_corrections', [])
    corrected_contexts = {item['attempt_id']: item for item in context_proofs}
    if (len(corrected_contexts) != len(context_proofs)
            or not set(corrected_contexts) <= scored
            or set(corrected_contexts) & set(corrected_answers)):
        raise ValueError('Native context correction scope differs from rescoring scope')
    expected_scoring = {}
    for identifier, old in old_rows.items():
        new = new_rows[identifier]
        if identifier in corrected_answers:
            _require_native_answer_correction(source, old, new, corrected_answers[identifier],
                                              historical_scoring_source=receipt['scoring_source_sha256'])
        elif identifier in corrected_contexts:
            _require_native_context_correction(source, old, new, corrected_contexts[identifier])
        elif ({k: v for k, v in old.items() if k not in {'metrics', 'scoring', 'diagnosis', 'failure_category'}}
                != {k: v for k, v in new.items() if k not in {'metrics', 'scoring', 'diagnosis', 'failure_category'}}
                or old['metrics']['content_sha256'] != new['metrics']['content_sha256']):
            raise ValueError('Local revision changed measured call facts: ' + identifier)
        if identifier in scored:
            expected_scoring[identifier] = receipt['scoring_source_sha256']
            if (receipt.get('rule_revision') and
                    new.get('scoring', {}).get('rule_revision') is not True):
                raise ValueError('Scoring policy change cannot be recorded as a parser-only correction')
        else:
            expected_scoring[identifier] = old['metrics']['scoring_source_sha256']
            before = {k: v for k, v in old['metrics'].items() if not k.startswith('evaluation_exclu')}
            after = {k: v for k, v in new['metrics'].items() if not k.startswith('evaluation_exclu')}
            if before != after:
                raise ValueError('Unselected score changed: ' + identifier)
        if new['case_id'] in changed_cases and identifier not in scored:
            raise ValueError('Changed case was not rescored across every method')
    if any(new_rows[key]['metrics'].get('scoring_source_sha256') != value
           for key, value in expected_scoring.items()):
        raise ValueError('Local revision scoring source binding changed')
    from lexbench_webfetch.metrics import task_passed
    if receipt.get('input_verdicts'):
        verdicts = _read_input_verdicts(source, receipt['input_verdicts'],
            receipt['input_results']['sha256'], old_rows)
    else:
        verdicts = {key: task_passed(row['ok'], row['metrics']) for key, row in old_rows.items()}
    transitions = [{'attempt_id': row['attempt_id'], 'before': verdicts[row['attempt_id']],
                    'after': task_passed(row['ok'], row['metrics'])}
                   for row in records if row['attempt_id'] in scored
                   and verdicts[row['attempt_id']] != task_passed(row['ok'], row['metrics'])]
    if transitions != receipt['task_verdict_changes']:
        raise ValueError('Local revision verdict transitions differ from input proof')
    from .adjudication_evidence import _require_page_identity_review
    _require_page_identity_review(source, dataset, records, scoring_sources=expected_scoring)
    require_cohort_exclusions(source, dataset, records)
    return expected_scoring, before_dataset, previous


def revise_local_snapshot(source, configuration):
    """Stage and validate a complete revision before installing its write set."""
    source = Path(source).resolve()
    lock_id = hashlib.sha256(str(source).encode()).hexdigest()[:20]
    lock_path = Path(tempfile.gettempdir()) / ('browser-eval-local-revision-' + lock_id + '.lock')
    with lock_path.open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        transaction = _RevisionTransaction(source)
        transaction.recover()
        try:
            with staged_evidence_reads(source, transaction.read):
                receipt = _revise_local_snapshot(source, configuration, transaction)
                transaction.commit()
                return receipt
        finally:
            transaction.recover()


def _revise_local_snapshot(source, configuration, transaction):
    """Apply declared score-only changes in a separate snapshot, retaining predecessors."""
    import copy
    from datetime import datetime, timezone
    from lexbench_webfetch.scoring import score_attempt, load_list_reference, SCORING_SOURCE_SHA256
    from .cohort_exclusions import apply_cohort_exclusions
    from lexbench_webfetch.metrics import task_passed

    config = json.loads(configuration.read_text())
    regressions = config.get('regression_ids', [])
    if not regressions or not config.get('authority', {}).get('description'):
        raise ValueError('Local revision requires authority and regression provenance')
    dataset_bytes = (source / 'dataset.json').read_bytes()
    results_bytes = (source / 'results.jsonl').read_bytes()
    input_results_sha256 = hashlib.sha256(results_bytes).hexdigest()
    original_dataset = json.loads(dataset_bytes)
    dataset = copy.deepcopy(original_dataset)
    records = load_jsonl(source / 'results.jsonl')
    if config.get('predecessor_revision'):
        validate_revision_chain(source, config['predecessor_revision'], original_dataset, records)
    input_verdicts = (_read_input_verdicts(source, config['input_verdicts'],
        input_results_sha256, {row['attempt_id'] for row in records})
        if config.get('input_verdicts') else None)
    _require_revision_regressions(regressions)
    replacements = {case['id']: case for case in config['cases']}
    if len(replacements) != len(config['cases']) or not replacements.keys() <= {c['id'] for c in dataset['cases']}:
        raise ValueError('Local revision has an invalid case scope')
    dataset['cases'] = [replacements.get(case['id'], case) for case in dataset['cases']]
    if dataset['cases'] != original_dataset['cases'] and config.get('rule_revision') is not True:
        raise ValueError('Changed acceptance contract requires an explicit rule revision')
    dataset['comparison']['excluded_cases'] = config.get('excluded_cases', [])
    if config.get('rule_revision'):
        dataset['comparison'].pop('scoring_policy_version', None)
        prior_review = dataset['comparison'].pop('independent_review', None)
        if prior_review:
            dataset['comparison']['historical_independent_review'] = prior_review
    additional = set(config.get('rescore_attempt_ids', []))
    if not additional <= {row['attempt_id'] for row in records}:
        raise ValueError('Local revision has an invalid additional rescoring scope')
    revision_key = (input_results_sha256[:16] + '-' +
                    file_sha256(configuration)[:16])
    history = source / 'local-evidence/offline-revisions' / revision_key
    if history.exists():
        raise ValueError('Local revision already exists; preserve the prior receipt')
    native_inputs = config.get('native_answer_corrections', [])
    native_requests = {item['attempt_id']: item for item in native_inputs}
    if (len(native_requests) != len(native_inputs)
            or not set(native_requests) <= {row['attempt_id'] for row in records}):
        raise ValueError('Native answer correction has an invalid input scope')
    context_inputs = config.get('native_context_corrections', [])
    context_requests = {item['attempt_id']: item for item in context_inputs}
    if (len(context_requests) != len(context_inputs)
            or not set(context_requests) <= {row['attempt_id'] for row in records}
            or set(context_requests) & set(native_requests)):
        raise ValueError('Native context correction has an invalid input scope')
    additional.update(native_requests)
    additional.update(context_requests)
    indexed = {case['id']: case for case in dataset['cases']}
    selected = []
    native_proofs = []
    context_proofs = []
    before_verdicts = {}
    for row in records:
        if row['case_id'] not in replacements and row['attempt_id'] not in additional:
            continue
        case = indexed[row['case_id']]
        raw = read_evidence_binding(source, {
            'path': row['artifact_path'], 'sha256': row['metrics']['content_sha256'],
        }, allow_public_view=False).read_bytes()
        before = (input_verdicts[row['attempt_id']] if input_verdicts is not None
                  else task_passed(row['ok'], row['metrics']))
        before_verdicts[row['attempt_id']] = before
        if row['attempt_id'] in native_requests:
            request = native_requests[row['attempt_id']]
            metadata = row.get('provider_metadata', {})
            _, answer, fields, result_fields = _project_native_correction(source, row, request)
            row.update(result_fields)
            raw = answer.encode('utf-8', errors='replace')
            original_artifact = {'path': row['artifact_path'], 'sha256': row['metrics']['content_sha256']}
            digest = hashlib.sha256(raw).hexdigest()
            old_path = Path(row['artifact_path'])
            new_relative = old_path.with_name(old_path.stem + '-native-' + digest[:12] + old_path.suffix)
            corrected_path = contained_path(source, new_relative, reject_symlinks=True)
            if corrected_path.exists() and corrected_path.read_bytes() != raw:
                raise ValueError('Native correction would overwrite a different artifact')
            transaction.write(corrected_path, raw)
            row['artifact_path'] = new_relative.as_posix()
            row['provider_metadata'] = {**metadata, **fields, 'native_answer_projection': request['projection']}
            native_proofs.append({'attempt_id': row['attempt_id'],
                'native_response': request['native_response'], 'projection': request['projection'],
                'original_artifact': original_artifact,
                'corrected_artifact': {'path': row['artifact_path'], 'sha256': digest}})
        if row['attempt_id'] in context_requests:
            proof, fields = _native_context_proof(source, row, context_requests[row['attempt_id']])
            row.update(fields)
            context_proofs.append(proof)
        row['metrics'] = score_attempt(case, raw, ok=row['ok'], status_code=row.get('status_code'),
            title=row.get('title'), final_url=row.get('final_url'), metadata=row.get('provider_metadata'),
            previous_metrics=row['metrics'], list_reference=load_list_reference(case, source))
        # Diagnoses describe the former score and must be regenerated from this answer.
        row.pop('diagnosis', None)
        row.pop('failure_category', None)
        row['scoring'] = {**row.get('scoring', {}), 'mode': 'offline_rescore',
            'scored_at': datetime.now(timezone.utc).isoformat(),
            'rule_revision': bool(config.get('rule_revision')),
            'input_results_sha256': input_results_sha256}
        selected.append(row['attempt_id'])
    from .adjudication_evidence import prepare_reviewed_page_identities
    identity_updates = prepare_reviewed_page_identities(source, original_dataset, dataset, records)
    identity_binding = identity_updates.binding if identity_updates else None
    if identity_updates:
        dataset['comparison']['page_identity_review'] = identity_binding
        for row in records:
            if row['attempt_id'] in identity_updates.metrics_by_attempt:
                row['metrics'] = identity_updates.metrics_by_attempt[row['attempt_id']]
    changes = [{'attempt_id': row['attempt_id'], 'before': before_verdicts[row['attempt_id']],
                'after': task_passed(row['ok'], row['metrics'])}
               for row in records if row['attempt_id'] in before_verdicts
               and before_verdicts[row['attempt_id']] != task_passed(row['ok'], row['metrics'])]
    apply_cohort_exclusions(source, dataset, records)
    transaction.write(history / 'dataset-before.json', dataset_bytes)
    transaction.write(history / 'results-before.jsonl', results_bytes)
    def binding(path):
        return {'path': str(path.relative_to(source)), 'sha256': file_sha256(transaction.read(path))}
    new_dataset = (json.dumps(dataset, ensure_ascii=False, indent=2) + '\n').encode()
    new_results = ''.join(json.dumps(row, ensure_ascii=True) + '\n' for row in records).encode()
    receipt = {'kind': 'local_offline_score_revision', 'provider_calls_added': 0,
        'input_dataset': binding(history / 'dataset-before.json'),
        'input_results': binding(history / 'results-before.jsonl'),
        'output_dataset_sha256': hashlib.sha256(new_dataset).hexdigest(),
        'output_results_sha256': hashlib.sha256(new_results).hexdigest(),
        'scoring_source_sha256': SCORING_SOURCE_SHA256,
        'changed_case_ids': sorted(case['id'] for case in dataset['cases']
            if case != next(c for c in original_dataset['cases'] if c['id'] == case['id'])),
        'rescored_attempt_ids': selected, 'rule_revision': bool(config.get('rule_revision')),
        **({'native_answer_corrections': native_proofs} if native_proofs else {}),
        **({'native_context_corrections': context_proofs} if context_proofs else {}),
        'input_verdicts': config.get('input_verdicts'),
        **({'predecessor_revision': config['predecessor_revision']} if config.get('predecessor_revision') else {}),
        'superseded_controls': config.get('superseded_controls', []),
        'page_identity_review': identity_binding,
        'task_verdict_changes': changes, 'regression_ids': regressions, 'authority': config['authority']}
    receipt_path = history / 'receipt.json'
    transaction.write(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode())
    if (source / 'dataset.json').read_bytes() != dataset_bytes or (source / 'results.jsonl').read_bytes() != results_bytes:
        raise ValueError('Local revision source changed during scoring')
    transaction.write(source / 'dataset.json', new_dataset)
    transaction.write(source / 'results.jsonl', new_results)
    require_local_revision(source, binding(receipt_path), dataset, records)
    if (source / 'dataset.json').read_bytes() != dataset_bytes or (source / 'results.jsonl').read_bytes() != results_bytes:
        raise ValueError('Local revision source changed during validation')
    return receipt
