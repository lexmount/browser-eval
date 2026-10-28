"""Pure historical article evidence qualification, independent of scoring."""
from datetime import date, datetime
import re
from .article_comments import validate_comment_contract, valid_comment_records
from .article_posts import validate_post_contract, post_identity
from .article_text import author_key
from .body_coverage import body_requirements, compact_ngrams, tokens

def _utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError('UTC timestamp required')
    return stamp

def _comment_evidence_status(reference, case_id, url):
    if reference is None:
        return 'missing'
    try:
        if (type(reference['version']) is not int or reference['version'] != 1
                or reference['case_id'] != case_id or reference['url'] != url
                or reference['scope'] != 'initial_load_only'
                or reference['observation_complete'] is not True
                or not isinstance(reference['context_id'], str) or not reference['context_id']):
            return 'invalid'
        records, counts = reference['records'], reference['source_observation_counts']
        if (not isinstance(records, list) or not isinstance(counts, list)
                or len(counts) < 1
                or any(type(n) is not int or n < 0 for n in counts) or counts[-1] != len(records)):
            return 'invalid'
        evidence = reference.get('source_evidence')
        if len(counts) == 1:
            if not isinstance(evidence, list) or len(evidence) != 1:
                return 'invalid'
        if evidence is not None and (not isinstance(evidence, list) or not evidence
                or any(not isinstance(item, dict) or not isinstance(item.get('path'), str)
                       or not item['path'].strip() or not isinstance(item.get('sha256'), str)
                       or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])
                       or item.get('scope') != 'complete_initial_page' for item in evidence)):
            return 'invalid'
        seen = set()
        for row in records:
            if (not isinstance(row['id'], str) or not row['id'] or row['id'] in seen
                    or author_key(row['author']) is None
                    or not isinstance(row['text'], str)
                    or (not row['text'].strip() and row.get('empty_body') is not True)
                    or ('empty_body' in row and (row['empty_body'] is not True or row['text'] != ''))
                    or row['complete_initial_text'] is not True
                    or row['parent_id'] is not None and row['parent_id'] not in seen):
                return 'invalid'
            if ('image_only' in row or 'source_media_count' in row) and (
                    row.get('image_only') is not True or row.get('empty_body') is not True
                    or row['text'] != '' or type(row.get('source_media_count')) is not int
                    or row['source_media_count'] < 1):
                return 'invalid'
            seen.add(row['id'])
        by_id = {row['id']: row for row in records}
        for row in records:
            if row.get('empty_body') is not True:
                continue
            # Independent rendered-media evidence can explain an image-only
            # leaf without inventing OCR prose. Otherwise an empty parent must
            # own at least one displayed descendant with ordinary text.
            if row.get('image_only') is True:
                continue
            descendant_text = False
            for child in records:
                parent = child['parent_id']
                while parent is not None:
                    if parent == row['id'] and child['text'].strip():
                        descendant_text = True
                        break
                    parent = by_id[parent]['parent_id']
                if descendant_text:
                    break
            if not descendant_text:
                return 'invalid'
        if not records and any(counts):
            return 'invalid'
        if 'observed_at' not in reference:
            # Historical date-only evidence establishes authoring provenance,
            # never a clock value, freshness, or an execution context match.
            observed_on = reference.get('observed_on')
            if (not isinstance(observed_on, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', observed_on)
                    or date.fromisoformat(observed_on).isoformat() != observed_on or not evidence):
                return 'invalid'
        else:
            _utc(reference['observed_at'])
    except (KeyError, ValueError, TypeError, AttributeError):
        return 'invalid'
    return 'verified'

def qualify_article_comments(contract, reference, *, case_id, url):
    """Validate historical evidence only, without runtime clocks or answers."""
    if contract is not None:
        validate_comment_contract(contract)
    status = _comment_evidence_status(reference, case_id, url)
    result = {'status': 'qualified' if status == 'verified' else status,
              'observation_count': len(reference.get('source_observation_counts', [])) if isinstance(reference, dict) and isinstance(reference.get('source_observation_counts'), list) else 0,
              'comment_count': len(reference.get('records', [])) if isinstance(reference, dict) and isinstance(reference.get('records'), list) else 0}
    if status == 'verified':
        if contract is not None:
            valid_count = len(valid_comment_records(reference['records'], contract))
            result['valid_comment_count'] = valid_count
            if valid_count < contract['minimum_comments']:
                result['status'] = 'insufficient_comments'
        if 'observed_at' not in reference:
            result['observed_on'] = reference['observed_on']
    return result

def _part_contract(part):
    text = part['text']
    contract = body_requirements(text)
    if 'source_line_ranges' in part:
        lines, cursor, regions = text.splitlines(), 1, []
        ranges = part['source_line_ranges']
        if not isinstance(ranges, list) or not 1 <= len(ranges) <= 5:
            raise ValueError('invalid source ranges')
        for span in ranges:
            if (not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span)
                    or span[0] != cursor or not span[0] <= span[1] <= len(lines)):
                raise ValueError('ranges must partition the whole body part')
            region = body_requirements('\n'.join(lines[span[0]-1:span[1]]),
                                       structure_hint='whole_declared_component')['regions']
            if len(region) != 1:
                raise ValueError('empty source region')
            regions.append({**region[0], 'id': f'post-region-{len(regions)+1}'})
            cursor = span[1] + 1
        if cursor != len(lines)+1:
            raise ValueError('ranges omit the end')
        contract['regions'] = regions
    if any(not compact_ngrams(r['text'], 6) for r in contract['regions']):
        raise ValueError('body regions require six-character matching units')
    contract['minimum_region_passes'] = len(contract['regions'])
    contract['minimum_floor'] = contract['minimum_coverage']
    return contract

def _post_evidence_status(reference, contract, case_id, url):
    if reference is None:
        return 'missing'
    try:
        if (type(reference['version']) is not int or reference['version'] != 1 or reference['case_id'] != case_id or reference['url'] != url
                or reference['scope'] != 'initial_load_only'
                or not isinstance(reference['context_id'], str) or not reference['context_id']):
            return 'invalid'
        _utc(reference['observed_at'])
        records, seen = reference['records'], set()
        if not isinstance(records, list) or not records:
            return 'invalid'
        for row in records:
            key = post_identity(row['url'], contract, url)
            if key in seen or not isinstance(row['author'], str) or not tokens(row['author']):
                return 'invalid'
            seen.add(key)
            parts = row['body_parts']
            if (not isinstance(parts, list) or not parts or parts[0].get('kind') != 'post'
                    or any(p.get('kind') != 'quote' for p in parts[1:])):
                return 'invalid'
            for index, part in enumerate(parts):
                if (part['kind'] not in {'post', 'quote'} or part['complete_initial_text'] is not True
                        or not isinstance(part['text'], str)):
                    return 'invalid'
                if 'author' in part and (not isinstance(part['author'], str) or not tokens(part['author'])):
                    return 'invalid'
                if 'empty_outer_text' in part:
                    # This independent observation cannot excuse omitted prose or
                    # an empty quote. Every owned quote still needs attribution.
                    if (not contract.get('allow_empty_outer_with_quotes', False)
                            or part['empty_outer_text'] is not True or index != 0
                            or part['text'] != '' or 'source_line_ranges' in part or len(parts) < 2
                            or any(not isinstance(p.get('author'), str) or not tokens(p['author'])
                                   for p in parts[1:])):
                        return 'invalid'
                    continue
                if not tokens(part['text']):
                    return 'invalid'
                _part_contract(part)
    except (KeyError, TypeError, ValueError, AttributeError):
        return 'invalid'
    return 'verified'

def qualify_article_posts(contract, reference, *, case_id, url):
    """Validate complete historically observed post ownership and count."""
    validate_post_contract(contract)
    status = _post_evidence_status(reference, contract, case_id, url)
    if status == 'verified':
        counts = reference.get('source_observation_counts')
        if (not isinstance(counts, list) or len(counts) < 2
                or any(type(n) is not int or n < 1 for n in counts)
                or counts[-1] != len(reference['records'])
                or min(counts) < contract['minimum_posts']):
            status = 'invalid'
    return {'status': 'qualified' if status == 'verified' else status}
