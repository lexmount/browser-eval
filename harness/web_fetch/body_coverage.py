"""Declared, distributed body coverage; independent of the full-text F1 metric."""
import re
from collections import Counter


def tokens(text):
    from .metrics import normalize_for_match
    return re.findall(r'[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+', normalize_for_match(text))


def body_requirements(reference):
    words = tokens(reference)
    count = min(5, max(1, len(words) // 20))
    return {'version': 3, 'unit': 'region_compact_hexagram_recall',
            'minimum_coverage': 0.8, 'minimum_floor': 0.6,
            'minimum_region_passes': max(1, count - 1),
            'regions': [{'id': f'body-region-{i+1}', 'text': ' '.join(words[len(words)*i//count:len(words)*(i+1)//count])}
                        for i in range(count)]}


def compact_trigrams(text):
    return compact_ngrams(text, 3)


def compact_ngrams(text, size):
    from .metrics import normalize_for_match
    compact = ''.join(character for character in normalize_for_match(text)
                      if character.isalnum())
    return [compact[index:index+size] for index in range(max(0, len(compact)-size+1))]


def evaluate_body_requirements(content, contract):
    if not contract:
        return {'body_coverage_pass': True, 'body_region_results': []}
    version = contract.get('version')
    unit = contract.get('unit')
    valid_units = {(1, 'region_token_recall'),
                   (2, 'region_compact_trigram_recall'),
                   (3, 'region_compact_hexagram_recall')}
    if ((version, unit) not in valid_units
            or not 0 < contract.get('minimum_coverage', 0) <= 1 or not contract.get('regions')):
        raise ValueError('Invalid distributed body requirements')
    if version == 3:
        floor = contract.get('minimum_floor')
        required_regions = contract.get('minimum_region_passes')
        if (not isinstance(required_regions, int)
                or not 1 <= required_regions <= len(contract['regions'])
                or not isinstance(floor, (int, float))
                or not 0 < floor <= contract['minimum_coverage']):
            raise ValueError('Invalid distributed body requirements')
    else:
        floor = contract['minimum_coverage']
        required_regions = len(contract['regions'])
    units = tokens(content) if version == 1 else compact_ngrams(content, 6 if version == 3 else 3)
    predicted = Counter(units)
    available = predicted.copy()
    checks = []
    for region in contract['regions']:
        expected_units = (region['text'].split() if version == 1
                          else compact_ngrams(region['text'], 6 if version == 3 else 3))
        expected = Counter(expected_units)
        if not expected:
            raise ValueError('Empty body region')
        source = available if version == 3 else predicted
        matched = source & expected
        missing = expected - source
        if version == 3:
            # One returned fragment can satisfy only one reference region. This
            # prevents repeated API/navigation wording elsewhere from being
            # borrowed independently by every omitted region.
            available -= matched
        coverage = sum(matched.values()) / sum(expected.values())
        label = 'token' if version == 1 else 'fragment'
        target_pass = coverage >= contract['minimum_coverage']
        floor_pass = coverage >= floor
        checks.append({'id': region['id'], 'coverage': round(coverage, 4),
                       'expected_units': sum(expected.values()),
                       'matched_units': sum(matched.values()),
                       'missing_units': sum(missing.values()),
                       'missing_sample': [{label: value, 'count': count}
                                          for value, count in missing.most_common(40)],
                       'minimum': contract['minimum_coverage'], 'floor': floor,
                       'target_pass': target_pass, 'floor_pass': floor_pass,
                       'pass': target_pass})
        if version == 1:
            checks[-1].update(expected_tokens=checks[-1]['expected_units'],
                              matched_tokens=checks[-1]['matched_units'],
                              missing_tokens=checks[-1]['missing_units'])
    coverage_pass = (all(c['floor_pass'] for c in checks)
                     and sum(c['target_pass'] for c in checks) >= required_regions)
    return {'body_coverage_pass': coverage_pass,
            'body_target_regions': sum(c['target_pass'] for c in checks),
            'body_required_target_regions': required_regions,
            'body_region_results': checks}
