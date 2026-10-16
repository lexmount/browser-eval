"""Identity-bound title overlap, a diagnostic separate from full-body scoring.

The reference catalog is independent of provider output. It supplies names for
entities, not the membership or order of a live list. Unknown identities and
missing titles are unmeasured, not false names or zero-quality text. Scores are
conditional on returned, reference-covered titles; task acceptance still tests
retrieval. Never write these values to word_* or use them to change task_pass.
"""
import hashlib
import json
import re
import statistics
from pathlib import Path
from lexbench_webfetch.scoring import SCORING_SOURCE_SHA256

from lexbench_webfetch.list_verification import identity
from lexbench_webfetch.metrics import word_overlap
from lexbench_webfetch.paths import contained_path


def reference_title(value):
    """Reject observed card controls, prices, counts and truncated labels.

    Keep numeric work names such as 1917: punctuation/currency, not digits alone,
    distinguishes the captured count and price labels from titles.
    """
    title=value.strip().splitlines()[0].strip() if value.strip() else ''
    controls={'查看选项','单击此处查看价格','账户充值','详情','查看','更多','评论','分享','图片',
              '点击','了解更多','watch','details','read more','view more','see options','buy now','add to cart'}
    if not title or title.casefold() in controls or title.endswith(('...', '…')):
        return None
    if re.fullmatch(r'\(?[\d.,]+[万千kKmM]?\)|\+[\d ]+(?:其他颜色/图案)?',title):
        return None
    if re.match(r'^(?:CNY|USD|RMB|EUR|GBP|[$¥￥€£])\s*[\d.,]',title,re.I):
        return None
    return title


def evaluate_titles(records, reference):
    index = {}
    for item in reference.get('items', []):
        # A multi-line card label can include votes/badges; the first line is
        # its name. Truncated labels are not complete title references.
        title = reference_title(item.get('title', ''))
        if item.get('url') and title:
            index.setdefault(identity(item['url']), set()).add(title)
    checks, seen = [], set()
    for record in records:
        fields = record.get('fields', {})
        url, title = fields.get('url'), fields.get('title')
        if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
            continue
        key = identity(url)
        if key in seen:
            continue
        seen.add(key)
        alternatives = sorted(index.get(key, []))
        scored = [(word_overlap(title, ref), ref) for ref in alternatives]
        # Independently observed aliases are accepted as alternative references.
        best = max(scored, key=lambda x: (x[0][2], x[0][1], x[0][0])) if scored else None
        checks.append({'url': url, 'title': title, 'reference_titles': alternatives,
                       'reference_used': best[1] if best else None,
                       'precision': best[0][0] if best else None,
                       'recall': best[0][1] if best else None,
                       'f1': best[0][2] if best else None})
    measured = [c for c in checks if c['f1'] is not None]
    return {'scope': 'returned_identity_bound_titles', 'checks': checks,
            'returned_titles': len(checks), 'scored_titles': len(measured),
            'reference_identities': len(index),
            **{k: statistics.mean(c[k] for c in measured) if measured else None
               for k in ('precision', 'recall', 'f1')}}


def add_title_quality(record, case, source):
    if case.get('track') != 'challenge' or case.get('acceptance_contract', {}).get('schema') != 'named_link':
        return
    relative = f'list-references/{case["id"]}.json'
    path = contained_path(source, relative)
    if not path.is_file():
        return
    raw = path.read_bytes()
    reference = json.loads(raw)
    if reference.get('case_id') != case['id']:
        raise ValueError('Title reference case mismatch')
    result = evaluate_titles(record['metrics'].get('stable_records', []), reference)
    result.update({'mode': 'offline_title_diagnostic', 'reference_path': relative,
                   'reference_checked_at': reference.get('checked_at'),
                   'reference_sha256': hashlib.sha256(raw).hexdigest(),
                   'output_sha256': record['metrics']['content_sha256'],
                   'scorer_sha256': hashlib.sha256(Path(__file__).read_bytes() +
                       SCORING_SOURCE_SHA256.encode()).hexdigest()})
    record['title_quality'] = result
