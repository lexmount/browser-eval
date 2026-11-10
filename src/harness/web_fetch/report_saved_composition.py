"""Combine validated saved comparisons without changing their measured records."""
from copy import deepcopy
import json
import shutil
from pathlib import Path

from lexbench_webfetch.paths import contained_path, safe_identifier
from harness.record_io import file_sha256 as _hash


def _read(path):
    return json.loads(path.read_bytes())


def _project(value, paths):
    if isinstance(value, dict):
        return {k: _project(v, paths) for k, v in value.items()}
    if isinstance(value, list):
        return [_project(v, paths) for v in value]
    return paths.get(value, value) if isinstance(value, str) else value


def _public_spec(spec):
    result = {k: deepcopy(spec[k]) for k in ('title', 'limitations', 'presentation', 'combined_ranking') if k in spec}
    if spec.get('native_policy_registry'):
        result['native_policy_registry'] = True
    return result


def composition_model(output, spec, snapshots):
    from harness.web_fetch.report_saved_matrix import _case_identity, _totals, check_saved_matrix
    providers, rows, cases, suites, families = [], [], {}, None, None
    expected_population = None
    for snapshot in snapshots:
        if snapshot['path'] != 'evidence/saved-reports/' + safe_identifier(snapshot['id']):
            raise ValueError('Invalid saved composition snapshot path')
        root = contained_path(output, snapshot['path'], reject_symlinks=True)
        if _hash(root / 'saved-matrix-audit.json') != snapshot['sha256']:
            raise ValueError('Saved composition source audit changed')
        check_saved_matrix(root)
        audit, model = _read(root / 'saved-matrix-audit.json'), _read(root / 'matrix.json')
        if 'providers' in snapshot:
            selected = snapshot['providers']
            available = {p['id'] for p in model['providers']}
            if (not isinstance(selected, list) or not selected
                    or any(not isinstance(p, str) for p in selected)
                    or len(selected) != len(set(selected)) or not set(selected) <= available):
                raise ValueError('Invalid saved report provider selection')
            model['providers'] = [p for p in model['providers'] if p['id'] in selected]
            model['rows'] = [r for r in model['rows'] if r['provider'] in selected]
        if suites is None:
            suites, families = model['suites'], model.get('report_families')
        if suites != model['suites'] or families != model.get('report_families'):
            raise ValueError('Saved comparisons have different task groups')
        if {p['id'] for p in providers} & {p['id'] for p in model['providers']}:
            raise ValueError('Saved comparisons have duplicate methods')
        providers.extend(model['providers'])
        paths = {name: snapshot['path'] + '/' + name for name in audit['files']}
        population = set()
        for row in model['rows']:
            key = row['suite'], row['case_id']
            case = _case_identity(_read(root / row['case_path']))
            if key in cases and cases[key] != case:
                raise ValueError('Saved comparisons have different frozen tasks')
            cases[key] = case
            population.add(key)
        if expected_population is not None and population != expected_population:
            raise ValueError('Saved comparisons have different case populations')
        expected_population = population
        rows.extend(_project(model['rows'], paths))
    if not rows:
        raise ValueError('Saved composition requires reports')
    result = dict(title=spec['title'], providers=providers, suites=suites, rows=rows,
                  totals=_totals(rows), limitations=spec.get('limitations', []),
                  presentation=spec.get('presentation', 'audit'),
                  combined_ranking=spec.get('combined_ranking', True),
                  source_bindings=[{'name': s['id'], 'sha256': s['sha256']} for s in snapshots],
                  scores_changed=any(row.get('score_revision') for row in rows))
    if families is not None:
        result['report_families'] = families
    if spec.get('native_policy_registry'):
        result['native_policy_registry'] = {'path': 'native-policy-registry.json',
                                           'sha256': _hash(output / 'native-policy-registry.json')}
    return result


def build_composition(spec_path, output):
    from harness.web_fetch.report import generate_saved_matrix
    from harness.web_fetch.report_saved_matrix import _path, _write, check_saved_matrix
    spec = _read(spec_path)
    if spec.get('schema_version') != 1 or not isinstance(spec.get('saved_reports'), list):
        raise ValueError('Unsupported saved composition specification')
    snapshots, identifiers = [], set()
    output.mkdir()
    for item in spec['saved_reports']:
        if item.get('role', 'main') != 'main':
            raise ValueError('Saved composition supports main methods only')
        identifier = safe_identifier(item['id'])
        if identifier in identifiers:
            raise ValueError('Duplicate saved report identifier')
        identifiers.add(identifier)
        root = _path(spec_path.parent, item['root'])
        audit_path = root / 'saved-matrix-audit.json'
        if _hash(audit_path) != item['audit']['sha256']:
            raise ValueError('Saved report audit differs from binding')
        check_saved_matrix(root)
        relative = 'evidence/saved-reports/' + identifier
        shutil.copytree(root, output / relative)
        snapshot = {'id': identifier, 'path': relative, 'sha256': _hash(audit_path)}
        if 'providers' in item:
            snapshot['providers'] = item['providers']
        snapshots.append(snapshot)
        # Revision receipts use report-root review attachment links. Preserve
        # those exact bytes, rejecting ambiguous collisions across sources.
        model = _read(root / 'matrix.json')
        receipt_paths = {row['score_revision']['receipt_path'] for row in model['rows']
                         if row.get('score_revision')}
        for receipt_path in sorted(receipt_paths):
            receipt = _read(root / receipt_path)
            for name in receipt.get('review_files', {}).values():
                target = output / name
                if target.exists() and target.read_bytes() != (root / name).read_bytes():
                    raise ValueError('Conflicting saved review attachments')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
    registry = spec.get('native_policy_registry')
    if registry:
        source = _path(spec_path.parent, registry['path'])
        if _hash(source) != registry['sha256']:
            raise ValueError('Native policy registry changed')
        shutil.copyfile(source, output / 'native-policy-registry.json')
    model = composition_model(output, spec, snapshots)
    _write(output / 'matrix.json', model)
    _write(output / 'saved-composition.json', {'spec': _public_spec(spec), 'snapshots': snapshots})
    entry = generate_saved_matrix(output, model)
    files = {p.relative_to(output).as_posix(): _hash(p) for p in output.rglob('*') if p.is_file()}
    _write(output / 'saved-matrix-audit.json', dict(schema_version=1, spec_sha256=_hash(spec_path),
        sources=model['source_bindings'], files=files, totals=model['totals'],
        provider_calls_added=0, scores_changed=model['scores_changed']))
    check_saved_matrix(output)
    return entry


def check_composition(output, spec_path=None):
    stored = _read(output / 'saved-composition.json')
    if spec_path is not None and _public_spec(_read(Path(spec_path))) != stored['spec']:
        raise ValueError('Saved composition specification changed')
    if composition_model(output, stored['spec'], stored['snapshots']) != _read(output / 'matrix.json'):
        raise ValueError('Saved composition differs from its immutable source reports')
