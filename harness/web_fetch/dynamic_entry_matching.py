"""Match complete, owned dynamic records in normalized visible text.

No I/O and no scoring/normalization dependency: callers inject both callbacks.
Only entry-N-field facts are returned; callers retain ordinary matching for other
IDs. A missing/corrupt field leaves the entire record unmatched (not a claim that
every field is absent). Frozen facts and their alternatives are never modified.

Supported layouts are declared in dynamic_reference.entry_field_orders. Between
fields only whitespace, Markdown delimiters and explicit field labels are allowed;
we never skip arbitrary prose, numbers, other records, or action buttons. Input
must already have visible Markdown links normalized by the caller. Free-form
paraphrases, JSON objects and arbitrary field reorderings are not parsed.
"""
from __future__ import annotations

import re
from collections import defaultdict

from .dynamic_reference import entry_field_orders


_ID = re.compile(r'entry-([1-9][0-9]*)-([a-z]+)\Z')
# Do not consume signs, decimal points, digits, or sentence punctuation here.
_SEP = r'[\s|*#`\\]*'
_LABELS = {
    'name': 'name', 'price': 'price', 'title': 'title',
    'description': 'description', 'date': 'date', 'text': '(?:review|text)',
    'method': 'method', 'path': 'path', 'summary': 'summary',
    'financing': 'financing', 'shipping': 'shipping', 'family': 'family',
    'author': '(?:by|author)', 'tags': 'tags', 'quote': 'quote', 'reviews': 'reviews',
}


def matching_entry_facts(folded: str, facts: list, normalize, fact_match) -> dict[str, list[str]]:
    """Return fact ID -> original matched alternatives for complete entry tuples.

    Recognized entry IDs are always present, with [] on failure. Unsupported
    schemas, duplicate fields/IDs and empty alternatives fail closed. Records
    may occur in any order, but one physical tuple cannot satisfy two records.
    """
    groups = defaultdict(dict)
    result = {}
    invalid = set()
    for fact in facts:
        identifier = str(fact.get('id', ''))
        parsed = _ID.fullmatch(identifier)
        if not parsed:
            continue
        entry, field = parsed.groups()
        result[identifier] = []
        if field in groups[entry]:
            invalid.add(entry)
        groups[entry][field] = fact

    # A linked card containing an image and the same visible title can become
    # ``TitleTitle $amount`` after Markdown visibility normalization. Collapse
    # only that exact, unseparated duplicate immediately before a price; real
    # repeated records retain a separator and remain distinct.
    for fields in groups.values():
        for value in fields.get('name', {}).get('alternatives', []):
            value = normalize(str(value))
            if value:
                folded = re.sub(r'(?<!\w)(' + re.escape(value) + r')\1(?=\s+[$€£¥])',
                                r'\1', folded)

    # Longest known identity wins: Blue T-Shirt inside Marine Blue T-shirt is
    # not a second product. Include occurrences even when their tuple is broken.
    anchors = []
    for fields in groups.values():
        for field in ('name', 'title', 'quote', 'text', 'family'):
            for value in fields.get(field, {}).get('alternatives', []):
                value = normalize(str(value))
                if value:
                    anchors.extend((m.start(), m.end()) for m in re.finditer(re.escape(value), folded))

    candidates = {}
    for entry, fields in groups.items():
        candidates[entry] = []
        if entry in invalid:
            continue
        for order in entry_field_orders(fields):
            parts = []
            for index, field in enumerate(order):
                values = sorted({normalize(str(v)) for v in fields[field].get('alternatives', [])} - {''}, key=len, reverse=True)
                if not values:
                    break
                label = _LABELS[field]
                # Labels are allowed only in the gap for their own field.
                # The label may itself touch the value when an extractor drops
                # an inline DOM boundary (``byAlbert``). The following exact
                # declared field literal closes the ambiguity, so a word
                # boundary after the label would reject complete content.
                gap = _SEP + rf'(?:(?:{label})\s*:?\s*)?' + _SEP
                literal = '|'.join(re.escape(v) for v in values)
                parts.append((gap if index else '') + rf'(?P<f{index}>{literal})')
            else:
                pattern = re.compile('(?=(' + ''.join(parts) + '))')
                for match in pattern.finditer(folded):
                    hits = {}
                    for index, field in enumerate(order):
                        start, end = match.span(f'f{index}')
                        value = match.group(f'f{index}')
                        # Check the actual surrounding characters, not an isolated
                        # captured number, so numeric/sign boundaries stay intact.
                        left_joined = value[0].isalnum() and start and folded[start-1].isalnum()
                        right_joined = value[-1].isalnum() and end < len(folded) and folded[end].isalnum()
                        if left_joined:
                            own_label = _LABELS[field]
                            if re.search(rf'(?:{own_label})\s*:?\s*$', folded[max(0, start - 16):start]):
                                left_joined = False
                        if right_joined and index + 1 < len(order):
                            # A missing DOM whitespace before the *declared next
                            # field label* is still an unambiguous field boundary.
                            next_label = _LABELS[order[index + 1]]
                            if re.match(rf'(?:{next_label})\b\s*:?', folded[end:]):
                                right_joined = False
                        # Native Markdown can emit METHOD[/path](target)Summary.
                        # Link normalization removes the destination and leaves
                        # `/pathsummary` with no whitespace. For a path, an
                        # adjacent word is therefore the next field; a slash or
                        # opening route parameter still proves a longer path and
                        # must not satisfy a shorter one such as /pet.
                        if field == 'path':
                            right_joined = end < len(folded) and folded[end] in '/{'
                        elif field == 'summary' and index and order[index - 1] == 'path':
                            left_joined = False
                        if left_joined or right_joined:
                            break
                        if field in {'name', 'title', 'quote', 'text', 'family'} and any(
                                a <= start and end <= b and (a < start or end < b) for a, b in anchors):
                            break
                        context = folded[max(0, start-3):end+4]
                        alternatives = [v for v in fields[field].get('alternatives', [])
                                        if normalize(str(v)) == value and fact_match(context, value)]
                        if not alternatives:
                            break
                        hits[fields[field]['id']] = alternatives
                    else:
                        candidate = (match.start(1), match.end(1), hits)
                        if candidate not in candidates[entry]:
                            candidates[entry].append(candidate)

    # Maximum disjoint record assignment, not occurrence counts. Most records
    # have one candidate; constrained-first search also handles duplicate tuples.
    ordered = sorted(candidates, key=lambda entry: len(candidates[entry]))
    best = {}

    def assign(index, selected, spans):
        nonlocal best
        if len(selected) + len(ordered) - index <= len(best):
            return
        if index == len(ordered):
            best = dict(selected)
            return
        entry = ordered[index]
        for start, end, hits in candidates[entry]:
            if all(end <= a or b <= start for a, b in spans):
                selected[entry] = hits
                assign(index + 1, selected, spans + [(start, end)])
                del selected[entry]
        assign(index + 1, selected, spans)

    assign(0, {}, [])
    for hits in best.values():
        result.update(hits)
    return result
