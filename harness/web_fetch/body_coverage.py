"""Declared, distributed body coverage; independent of the full-text F1 metric."""
import re
from collections import Counter


def tokens(text):
    from .metrics import normalize_for_match
    return re.findall(r'[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+', normalize_for_match(text))


def body_requirements(reference):
    words = tokens(reference)
    count = min(5, max(1, len(words) // 20))
    return {'version': 1, 'unit': 'region_token_recall', 'minimum_coverage': 0.8,
            'regions': [{'id': f'body-region-{i+1}', 'text': ' '.join(words[len(words)*i//count:len(words)*(i+1)//count])}
                        for i in range(count)]}


def evaluate_body_requirements(content, contract):
    if not contract:
        return {'body_coverage_pass': True, 'body_region_results': []}
    if (contract.get('version') != 1 or contract.get('unit') != 'region_token_recall'
            or not 0 < contract.get('minimum_coverage', 0) <= 1 or not contract.get('regions')):
        raise ValueError('Invalid distributed body requirements')
    predicted = Counter(tokens(content))
    checks = []
    for region in contract['regions']:
        expected = Counter(region['text'].split())
        if not expected:
            raise ValueError('Empty body region')
        coverage = sum((predicted & expected).values()) / sum(expected.values())
        checks.append({'id': region['id'], 'coverage': round(coverage, 4),
                       'minimum': contract['minimum_coverage'], 'pass': coverage >= contract['minimum_coverage']})
    return {'body_coverage_pass': all(c['pass'] for c in checks), 'body_region_results': checks}
