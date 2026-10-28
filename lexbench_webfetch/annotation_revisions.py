"""Apply score-only annotation edits while preserving frozen collection inputs."""
from copy import deepcopy
from functools import partial
import hashlib
import re

from .body_coverage import tokens


def _binding(text):
    return {'sha256': hashlib.sha256(text.encode()).hexdigest(), 'chars': len(text)}


def move_regions_to_stable_text(case, moves):
    """Reclassify exact-bound short components without dropping required text."""
    body = deepcopy(case.get('body_requirements'))
    contract = deepcopy(case.get('acceptance_contract'))
    if (not isinstance(body, dict) or body.get('version') != 3
            or 'ending' in body or not isinstance(contract, dict)
            or contract.get('schema') not in ('body_text', 'table_row')
            or contract.get('stable_text_role', 'required') != 'required'
            or not isinstance(moves, list) or not moves
            or body.get('minimum_floor') != body.get('minimum_coverage')
            or body.get('minimum_region_passes') != len(body.get('regions', []))):
        raise ValueError('Region reclassification requires a strict version 3 body')
    regions = {r['id']: r for r in body['regions']}
    anchors = contract.setdefault('stable_text_anchors', [])
    anchor_ids = {a['id'] for a in anchors}
    moved = set()
    for move in moves:
        if (not isinstance(move, dict) or set(move) != {'region_id', 'anchor_id', 'text'}
                or not all(isinstance(v, str) and v.strip() for v in move.values())
                or move['region_id'] not in regions or move['region_id'] in moved
                or move['anchor_id'] in anchor_ids):
            raise ValueError('Invalid region reclassification')
        region = regions[move['region_id']]
        expected = region.get('text_binding')
        if 'text' in region:
            actual = _binding(region['text'])
            if expected is not None and expected != actual:
                raise ValueError('Frozen region text differs from its binding')
            expected = actual
        if expected != _binding(move['text']):
            raise ValueError('Required stable text differs from frozen region')
        anchors.append({'id': move['anchor_id'], 'alternatives': [move['text']]})
        anchor_ids.add(move['anchor_id'])
        moved.add(move['region_id'])
    retained = [r for r in body['regions'] if r['id'] not in moved]
    if not retained:
        raise ValueError('Region reclassification must retain prose')
    body['regions'] = retained
    body['minimum_region_passes'] = len(retained)
    case['body_requirements'] = body
    case['acceptance_contract'] = contract


def project_body_reference(case, projection, expected_source_sha256):
    """Apply reviewed character ranges, verifying their materialized text at export."""
    original = case.get('body_requirements')
    policy = case.get('mutable_body_policy')
    annotation = policy.get('stable_annotation') if isinstance(policy, dict) else None
    introducing_body = (original is None and isinstance(policy, dict)
                        and isinstance(annotation, dict)
                        and annotation.get('content_kind') == 'changing_public_body')
    if introducing_body:
        original = {'version': 3, 'unit': 'region_compact_hexagram_recall',
                    'minimum_coverage': .8}
    version = original.get('version') if isinstance(original, dict) else None
    projection_fields = {'source_sha256', 'source_chars', 'regions'}
    if version in (4, 5):
        projection_fields.add('ending')
    if (not isinstance(original, dict) or version not in (3, 4, 5)
            or not isinstance(projection, dict)
            or set(projection) != projection_fields
            or (version == 3 and 'ending' in original)
            or not isinstance(expected_source_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', expected_source_sha256)
            or projection['source_sha256'] != expected_source_sha256
            or type(projection['source_chars']) is not int or projection['source_chars'] < 6
            or not isinstance(projection['regions'], list)
            or not 1 <= len(projection['regions']) <= 5):
        raise ValueError('Invalid body reference projection')
    source = case.get('ground_truth_main_content')
    if source is not None and (not isinstance(source, str) or
            _binding(source) != {'sha256': expected_source_sha256, 'chars': projection['source_chars']}):
        raise ValueError('Body projection source differs from frozen reference')

    def materialize(row, region=False):
        fields = {'start', 'end', 'text_binding'}
        if region:
            fields |= {'id', 'label', 'source_text_binding'}
        if (not isinstance(row, dict) or set(row) != fields
                or type(row['start']) is not int or type(row['end']) is not int
                or not 0 <= row['start'] < row['end'] <= projection['source_chars']):
            raise ValueError('Invalid body projection range')
        keys = ('text', 'source_text') if region else ('text',)
        for key in keys:
            binding = row[key + '_binding']
            if (not isinstance(binding, dict) or set(binding) != {'sha256', 'chars'}
                    or not isinstance(binding['sha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', binding['sha256'])
                    or type(binding['chars']) is not int or binding['chars'] < 1):
                raise ValueError('Invalid body projection text binding')
        result = {'source_char_range': [row['start'], row['end']]}
        if region:
            if any(not isinstance(row[k], str) or not row[k].strip() for k in ('id', 'label')):
                raise ValueError('Body projection regions need an identity and label')
            result.update(id=row['id'], label=row['label'],
                          segmentation_method='reviewed_frozen_source_ranges')
        if source is None:
            result.update({key + '_binding': deepcopy(row[key + '_binding']) for key in keys})
        else:
            text = source[row['start']:row['end']].strip()
            values = {'text': ' '.join(tokens(text)), 'source_text': text} if region else {'text': text}
            for key, text in values.items():
                if _binding(text) != row[key + '_binding']:
                    raise ValueError('Body projection text differs from reviewed binding')
                result[key] = text
        return result

    regions = [materialize(row, True) for row in projection['regions']]
    if (len({r['id'] for r in regions}) != len(regions)
            or any(a['end'] > b['start'] for a, b in zip(projection['regions'], projection['regions'][1:]))):
        raise ValueError('Body projection regions must be unique, ordered and nonoverlapping')
    ending = None
    if version in (4, 5):
        ending = materialize(projection['ending'])
        if not any(r['start'] <= projection['ending']['start'] < projection['ending']['end'] <= r['end']
                   for r in projection['regions']):
            raise ValueError('Body projection ending must belong to a retained region')
    body = deepcopy(original)
    body.update(version=3 if version == 3 else 5, regions=regions,
                minimum_region_passes=len(regions), minimum_floor=body['minimum_coverage'],
                segmentation_method='reviewed_frozen_source_ranges',
                reference_projection={'source_sha256': expected_source_sha256,
                                      'source_chars': projection['source_chars']})
    if ending is not None:
        body['ending'] = ending
    # Keep the original full reference for historical F1 diagnostics. Only the
    # declared coverage scope changes, and every retained character is source-bound.
    case['body_requirements'] = body
    if introducing_body:
        case.pop('mutable_body_policy')


def _patch_acceptance_contract(case, patch):
    """Edit contract fields and identified anchors without copying unchanged rules."""
    if not isinstance(patch, dict) or set(patch) - {'set', 'remove', 'anchors'}:
        raise ValueError('Invalid acceptance patch')
    contract = deepcopy(case.get('acceptance_contract'))
    if not isinstance(contract, dict):
        raise ValueError('Acceptance patch requires an existing contract')
    updates, removals = patch.get('set', {}), patch.get('remove', [])
    if (not isinstance(updates, dict) or not isinstance(removals, list)
            or any(not isinstance(k, str) for k in removals)
            or len(set(removals)) != len(removals) or set(updates) & set(removals)):
        raise ValueError('Invalid acceptance field edits')
    for key in removals:
        if key not in contract:
            raise ValueError('Acceptance removal target is absent')
        del contract[key]
    contract.update(deepcopy(updates))
    if 'anchors' in patch:
        edits = patch['anchors']
        if (not isinstance(edits, dict) or set(edits) - {'remove', 'replace', 'append'}
                or 'stable_text_anchors' in set(updates) | set(removals)
                or any(not isinstance(v, list) for v in edits.values())):
            raise ValueError('Invalid anchor edits')
        anchors = contract.get('stable_text_anchors', [])
        ids = [a['id'] for a in anchors]
        removed = edits.get('remove', [])
        replaced = edits.get('replace', [])
        appended = edits.get('append', [])
        if any(not isinstance(a, dict) or not isinstance(a.get('id'), str) for a in replaced + appended):
            raise ValueError('Anchor edits require IDs')
        touched = removed + [a['id'] for a in replaced + appended]
        if (any(not isinstance(i, str) for i in removed) or len(set(ids)) != len(ids)
                or len(set(touched)) != len(touched)
                or set(removed + [a['id'] for a in replaced]) - set(ids)
                or set(a['id'] for a in appended) & set(ids)):
            raise ValueError('Anchor edits must have unique, valid targets')
        replacements = {a['id']: a for a in replaced}
        contract['stable_text_anchors'] = [deepcopy(replacements.get(a['id'], a))
                                           for a in anchors if a['id'] not in removed] + deepcopy(appended)
    case['acceptance_contract'] = contract


def _revise_body_thresholds(case: dict, thresholds: dict) -> None:
    """Revise quotas without replacing frozen reference text or public bindings."""
    body = case.get('body_requirements')
    fields = {'minimum_coverage', 'minimum_floor', 'minimum_region_passes'}
    if (not isinstance(body, dict) or body.get('version') not in (3, 4, 5)
            or not isinstance(body.get('regions'), list) or not body['regions']
            or not isinstance(thresholds, dict) or set(thresholds) != fields
            or any(type(thresholds[k]) not in (int, float)
                   for k in ('minimum_coverage', 'minimum_floor'))
            or not 0 < thresholds['minimum_floor'] <= thresholds['minimum_coverage'] <= 1
            or type(thresholds['minimum_region_passes']) is not int
            or not 1 <= thresholds['minimum_region_passes'] <= len(body['regions'])
            or (body['version'] in (4, 5) and (
                thresholds['minimum_floor'] != thresholds['minimum_coverage']
                or thresholds['minimum_region_passes'] != len(body['regions'])))):
        raise ValueError('Invalid annotation body thresholds')
    body.update(thresholds)


def _revise_body_ending_order(case: dict, enabled: bool) -> None:
    """Add local ordered matching while retaining every frozen body reference."""
    from .body_coverage import compact_ngrams
    body = case.get('body_requirements')
    ending = body.get('ending') if isinstance(body, dict) else None
    binding = ending.get('text_binding') if isinstance(ending, dict) else None
    valid_ending = isinstance(ending, dict) and (
        isinstance(ending.get('text'), str) and bool(compact_ngrams(ending['text'], 6))
        or isinstance(binding, dict) and isinstance(binding.get('sha256'), str)
        and re.fullmatch('[0-9a-f]{64}', binding['sha256']) is not None
        and type(binding.get('chars')) is int and binding['chars'] >= 6)
    if (enabled is not True or not isinstance(body, dict) or body.get('version') != 4
            or not valid_ending
            or not isinstance(body.get('regions'), list) or not body['regions']
            or body.get('minimum_region_passes') != len(body['regions'])
            or body.get('minimum_floor') != body.get('minimum_coverage')):
        raise ValueError('Ordered ending revision requires a strict version 4 body')
    body['version'] = 5


EDITABLE_FIELDS = {'expected_resources', 'required_facts', 'forbidden_any', 'diagnostic_any',
    'composite_acceptance', 'acceptance_contract', 'required_patterns',
    'document_structure', 'content_score_applicable', 'min_content_chars',
    'fact_coverage_threshold'}


def _set_fields(case, fields):
    case.update(deepcopy(fields))


def _remove_fields(case, fields):
    for field in fields:
        case.pop(field, None)


def apply_annotation_change(case, change, reference_sha256):
    """Apply declared edits in dependency order, independent of JSON key order."""
    updates, removals = change.get('set', {}), change.get('remove', [])
    if (not isinstance(updates, dict) or not isinstance(removals, list)
            or (set(updates) | set(removals)) - EDITABLE_FIELDS):
        raise ValueError('Annotation revision cannot change collection inputs')
    if ('acceptance_patch' in change
            and 'acceptance_contract' in set(updates) | set(removals)):
        raise ValueError('Conflicting acceptance replacement and patch')
    # Thresholds must precede ending upgrades and reference/region edits;
    # field removals happen last. Do not dispatch in input key order.
    handlers = {
        'set': _set_fields,
        'acceptance_patch': _patch_acceptance_contract,
        'body_thresholds': _revise_body_thresholds,
        'body_ending_order': _revise_body_ending_order,
        'body_projection': partial(project_body_reference,
                                   expected_source_sha256=reference_sha256),
        'body_regions_to_stable_text': move_regions_to_stable_text,
        'remove': _remove_fields,
    }
    for kind, handler in handlers.items():
        if kind in change:
            handler(case, change[kind])
