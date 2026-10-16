"""Declared, distributed body coverage; independent of the full-text F1 metric."""
import re
import hashlib
from collections import Counter
from itertools import combinations
from math import ceil


def native_markup_body_text(content, fallback):
    """Read existing HTML layout in native Markdown, preserving literal code."""
    import html
    from .dynamic_lists import visible_html_text
    protected = {}
    prefix = "NATIVEBODYLITERAL"
    while prefix in content:
        prefix += "X"
    def protect(match):
        token = prefix + str(len(protected)) + "END"
        protected[token] = html.unescape(match[0])
        return token
    masked = re.sub(r"(`+|~{3,}).*?\1|\\<[^>\n]+>", protect, content, flags=re.S)
    if not re.search(r"<(?:table|thead|tbody|tfoot|tr|th|td|ul|ol|li|h[1-6]|p|div|section|article|br|span|strong|em|b|a)\b", masked, re.I):
        return fallback
    # Native converters can expose unfenced code samples as standalone
    # table tags. Their original text remains authoritative; do not erase it
    # by interpreting an ambiguous tag-only line as document structure.
    if re.search(r"(?m)^[ \t]*</?(?:table|tr|th|td)>[ \t]*$", masked):
        return fallback
    from bs4 import ParserRejectedMarkup
    try:
        visible = visible_html_text(masked, preserve_links=True)
    except ParserRejectedMarkup:
        return fallback
    for token, literal in protected.items():
        visible = visible.replace(token, literal)
    return visible


def tokens(text):
    from .metrics import normalize_for_match
    return re.findall(r'[\u3400-\u9fff]|[^\W_\u3400-\u9fff]+', normalize_for_match(text))


def _group_contiguous(units, count):
    """Group natural units contiguously without splitting an individual unit."""
    count = min(count, len(units))
    if count <= 1:
        return ['\n\n'.join(units)]
    weights = [max(1, len(tokens(unit))) for unit in units]
    total = sum(weights)
    groups = []
    start = 0
    consumed = 0
    for index in range(1, count):
        target = total * index / count
        end = start
        while end + 1 <= len(units) - (count - index):
            next_total = consumed + sum(weights[start:end + 1])
            after_total = consumed + sum(weights[start:end + 2])
            if end > start and abs(next_total - target) <= abs(after_total - target):
                break
            end += 1
        end = max(start + 1, end)
        groups.append('\n\n'.join(units[start:end]))
        consumed += sum(weights[start:end])
        start = end
    groups.append('\n\n'.join(units[start:]))
    return [group for group in groups if group.strip()]


def _natural_regions(reference, desired):
    text = str(reference or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n+', text) if part.strip()]
    heading = re.compile(r'^(?:#{1,6}\s+|[A-Z][^.!?。！？]{0,70}:?$)')
    has_explicit_headings = any(
        re.match(r'^#{1,6}\s+', paragraph.splitlines()[0].strip())
        for paragraph in paragraphs
    )
    if len(paragraphs) >= 2:
        sections = []
        current = []
        for paragraph in paragraphs:
            first = paragraph.splitlines()[0].strip()
            is_heading = (bool(re.match(r'^#{1,6}\s+', first))
                          or (not has_explicit_headings
                              and len(paragraph.splitlines()) == 1
                              and len(tokens(first)) <= 12
                              and bool(heading.match(first))))
            if is_heading and current:
                sections.append('\n\n'.join(current))
                current = [paragraph]
            else:
                current.append(paragraph)
        if current:
            sections.append('\n\n'.join(current))
        if len(sections) >= 2:
            return _group_contiguous(sections, desired), 'sections'
        if has_explicit_headings:
            units = []
            index = 0
            while index < len(paragraphs):
                paragraph = paragraphs[index]
                if (re.match(r'^#{1,6}\s+', paragraph.splitlines()[0].strip())
                        and index + 1 < len(paragraphs)):
                    units.append(paragraph + '\n\n' + paragraphs[index + 1])
                    index += 2
                else:
                    units.append(paragraph)
                    index += 1
            return _group_contiguous(units, desired), 'paragraphs'
        return _group_contiguous(paragraphs, desired), 'paragraphs'
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 4:
        sections = []
        current = []
        for index, line in enumerate(lines):
            next_line = lines[index + 1] if index + 1 < len(lines) else ''
            is_heading = (
                bool(re.match(r'^#{1,6}\s+', line))
                or (len(tokens(line)) <= 12
                    and not re.search(r'[.!?。！？;；]$', line)
                    and bool(next_line)
                    and (len(tokens(next_line)) > len(tokens(line))
                         or re.search(r'[.!?。！？]$', next_line)))
            )
            if is_heading and current:
                sections.append('\n'.join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append('\n'.join(current))
        if len(sections) >= 2:
            return _group_contiguous(sections, desired), 'sections'
        return _group_contiguous(lines, desired), 'line_blocks'
    sentences = [part.strip() for part in re.split(r'(?<=[.!?。！？])\s+', text)
                 if part.strip()]
    if len(sentences) >= 2:
        return _group_contiguous(sentences, desired), 'sentences'
    words = tokens(text)
    return ([' '.join(words[len(words) * index // desired:
                            len(words) * (index + 1) // desired])
             for index in range(desired)], 'balanced_tokens_fallback')


def body_requirements(reference, *, structure_hint=None):
    words = tokens(reference)
    desired = min(5, max(1, len(words) // 20))
    if structure_hint == 'whole_declared_component':
        natural, method = [reference], 'whole_declared_component'
    else:
        natural, method = _natural_regions(reference, desired)
    regions = []
    for index, text in enumerate(natural):
        normalized = ' '.join(tokens(text))
        if normalized:
            regions.append({'id': f'body-region-{index + 1}', 'text': normalized,
                            'source_text': text, 'segmentation_method': method})
    count = len(regions)
    return {'version': 3, 'unit': 'region_compact_hexagram_recall',
            'minimum_coverage': 0.8, 'minimum_floor': 0.6,
            'minimum_region_passes': max(1, count - 1),
            'segmentation_method': method, 'regions': regions}


def compact_trigrams(text):
    return compact_ngrams(text, 3)


def compact_ngrams(text, size):
    from .metrics import normalize_for_match
    compact = ''.join(character for character in normalize_for_match(text)
                      if character.isalnum())
    return [compact[index:index+size] for index in range(max(0, len(compact)-size+1))]


def _compact_with_positions(text):
    from .metrics import normalize_for_match
    normalized = normalize_for_match(text)
    positions = [index for index, character in enumerate(normalized) if character.isalnum()]
    return normalized, ''.join(normalized[index] for index in positions), positions


def _merge_compact_spans(spans, positions):
    if not spans:
        return []
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{
        'compact_start': start,
        'compact_end': end,
        'normalized_start': positions[start],
        'normalized_end': positions[end - 1] + 1,
    } for start, end in merged]


def _physical_match_spans(content, expected_by_id, assigned_by_id):
    """Bind allocated fragment counts to deterministic physical occurrences."""
    normalized, compact, positions = _compact_with_positions(content)
    all_units = sorted(set().union(*(set(row) for row in assigned_by_id.values())))
    wanted = set(all_units)
    occurrences = {unit: [] for unit in all_units}
    for start in range(max(0, len(compact) - 5)):
        unit = compact[start:start + 6]
        if unit in wanted:
            occurrences[unit].append(start)
    cursor = Counter()
    spans = {identifier: [] for identifier in assigned_by_id}
    for unit in all_units:
        for identifier in sorted(assigned_by_id):
            for _ in range(assigned_by_id[identifier][unit]):
                index = cursor[unit]
                if index >= len(occurrences[unit]):
                    break
                start = occurrences[unit][index]
                spans[identifier].append((start, start + len(unit)))
                cursor[unit] += 1
    return normalized, {
        identifier: _merge_compact_spans(rows, positions)
        for identifier, rows in spans.items()
    }


def _reference_match_spans(reference, matched):
    normalized, compact, positions = _compact_with_positions(reference)
    starts_by_unit = {unit: [] for unit in matched}
    for start in range(max(0, len(compact) - 5)):
        unit = compact[start:start + 6]
        if unit in starts_by_unit:
            starts_by_unit[unit].append(start)
    spans = []
    for unit in sorted(matched):
        starts = starts_by_unit[unit]
        spans.extend((start, start + len(unit)) for start in starts[:matched[unit]])
    return normalized, _merge_compact_spans(spans, positions)


def _span_passages(text, spans):
    return [{**span, 'text': text[span['normalized_start']:span['normalized_end']]}
            for span in spans]


def _unmatched_passages(text, spans):
    passages = []
    cursor = 0
    for span in spans:
        start, end = span['normalized_start'], span['normalized_end']
        if text[cursor:start].strip():
            passages.append({'normalized_start': cursor, 'normalized_end': start,
                             'text': text[cursor:start]})
        cursor = max(cursor, end)
    if text[cursor:].strip():
        passages.append({'normalized_start': cursor, 'normalized_end': len(text),
                         'text': text[cursor:]})
    return passages


def _maximum_allocation(capacities, demands, limits):
    """Allocate fragment instances globally, with deterministic max-flow ties."""
    region_ids = sorted(demands)
    allocation = {identifier: Counter() for identifier in region_ids}

    # Units owned by only one region cannot affect another region's quota. Assign
    # them directly so the flow graph only contains genuinely shared fragments.
    shared = {}
    for unit in sorted(set().union(*(set(row) for row in demands.values()))):
        claimants = [identifier for identifier in region_ids if demands[identifier][unit]]
        capacity = min(capacities[unit], sum(demands[row][unit] for row in claimants))
        if capacity <= 0:
            continue
        if len(claimants) == 1:
            identifier = claimants[0]
            allocation[identifier][unit] = min(capacity, demands[identifier][unit])
        else:
            shared[unit] = capacity

    residual_limits = {
        identifier: max(0, limits[identifier] - sum(allocation[identifier].values()))
        for identifier in region_ids
    }
    required_flow = sum(residual_limits.values())
    if not required_flow:
        return allocation, True

    source = ('source',)
    sink = ('sink',)
    graph = {}

    def add_edge(start, end, capacity):
        graph.setdefault(start, {})
        graph.setdefault(end, {})
        graph[start][end] = graph[start].get(end, 0) + capacity
        graph[end].setdefault(start, 0)

    for unit, capacity in shared.items():
        unit_node = ('unit', unit)
        add_edge(source, unit_node, capacity)
        for identifier in region_ids:
            capacity = demands[identifier][unit]
            if capacity:
                add_edge(unit_node, ('region', identifier), capacity)
    for identifier in region_ids:
        add_edge(('region', identifier), sink, residual_limits[identifier])

    total = 0
    ordered = {node: sorted(neighbors, key=repr) for node, neighbors in graph.items()}
    while total < required_flow:
        level = {source: 0}
        queue = [source]
        for node in queue:
            for neighbor in ordered.get(node, ()):
                if graph[node][neighbor] > 0 and neighbor not in level:
                    level[neighbor] = level[node] + 1
                    queue.append(neighbor)
        if sink not in level:
            break
        cursor = {node: 0 for node in graph}

        def send(node, amount):
            if node == sink:
                return amount
            sent = 0
            neighbors = ordered.get(node, ())
            while cursor[node] < len(neighbors) and sent < amount:
                neighbor = neighbors[cursor[node]]
                capacity = graph[node][neighbor]
                if capacity > 0 and level.get(neighbor) == level[node] + 1:
                    pushed = send(neighbor, min(amount - sent, capacity))
                    if pushed:
                        graph[node][neighbor] -= pushed
                        graph[neighbor][node] += pushed
                        sent += pushed
                        continue
                cursor[node] += 1
            return sent

        while total < required_flow:
            pushed = send(source, required_flow - total)
            if not pushed:
                break
            total += pushed

    for unit, capacity in shared.items():
        unit_node = ('unit', unit)
        for identifier in region_ids:
            original = demands[identifier][unit]
            remaining = graph[unit_node].get(('region', identifier), 0)
            if original > remaining:
                allocation[identifier][unit] += original - remaining
    return allocation, total == required_flow


def _global_region_allocation(predicted, expected, floor_counts, target_counts,
                              required_regions):
    """Find one simultaneous allocation satisfying the declared region quotas."""
    region_ids = sorted(expected)
    total_expected = Counter()
    for row in expected.values():
        total_expected.update(row)
    if all(predicted[unit] >= count for unit, count in total_expected.items()):
        return {identifier: expected[identifier].copy() for identifier in region_ids}, True
    available_total = sum(min(predicted[unit], count)
                          for unit, count in total_expected.items())
    minimum_total = (sum(floor_counts.values())
                     + sum(sorted(target_counts[identifier] - floor_counts[identifier]
                                  for identifier in region_ids)[:required_regions]))
    if available_total < minimum_total:
        # The total-capacity lower bound already proves infeasibility. Produce a
        # deterministic maximum-size diagnostic allocation without building flow
        # graphs for every possible target-region subset.
        allocation = {identifier: Counter() for identifier in region_ids}
        for unit in sorted(total_expected):
            remaining = min(predicted[unit], total_expected[unit])
            for identifier in region_ids:
                matched = min(remaining, expected[identifier][unit])
                if matched:
                    allocation[identifier][unit] = matched
                    remaining -= matched
                if not remaining:
                    break
        return allocation, False
    for targets in combinations(region_ids, required_regions):
        target_set = set(targets)
        limits = {identifier: (target_counts[identifier] if identifier in target_set
                               else floor_counts[identifier])
                  for identifier in region_ids}
        allocation, feasible = _maximum_allocation(predicted, expected, limits)
        if feasible:
            break
    else:
        limits = floor_counts
        allocation, feasible = _maximum_allocation(predicted, expected, limits)
        if not feasible:
            limits = {identifier: sum(row.values()) for identifier, row in expected.items()}
            allocation, _ = _maximum_allocation(predicted, expected, limits)
        return allocation, False

    # Quotas establish the verdict; spend remaining fragment capacity globally so
    # the per-region diagnostic counts describe as much of the answer as possible.
    used = Counter()
    for row in allocation.values():
        used.update(row)
    remaining_capacity = predicted - used
    remaining_demand = {
        identifier: expected[identifier] - allocation[identifier]
        for identifier in region_ids
    }
    extra_limits = {identifier: sum(row.values()) for identifier, row in remaining_demand.items()}
    extras, _ = _maximum_allocation(remaining_capacity, remaining_demand, extra_limits)
    for identifier in region_ids:
        allocation[identifier].update(extras[identifier])
    return allocation, True


def _evaluate_body_surface(content, contract):
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
    expected_by_id = {}
    regions_by_id = {}
    if version == 3:
        for region in contract['regions']:
            identifier = region.get('id')
            if not isinstance(identifier, str) or not identifier or identifier in regions_by_id:
                raise ValueError('Invalid distributed body requirements')
            expected_units = compact_ngrams(region.get('text', ''), 6)
            if not expected_units:
                raise ValueError('Empty body region')
            regions_by_id[identifier] = region
            expected_by_id[identifier] = Counter(expected_units)
        floor_counts = {identifier: ceil(floor * sum(row.values()) - 1e-12)
                        for identifier, row in expected_by_id.items()}
        target_counts = {identifier: ceil(contract['minimum_coverage'] * sum(row.values()) - 1e-12)
                         for identifier, row in expected_by_id.items()}
        assigned_by_id, coverage_pass = _global_region_allocation(
            predicted, expected_by_id, floor_counts, target_counts, required_regions)
        matching_text, output_spans_by_id = _physical_match_spans(
            content, expected_by_id, assigned_by_id)
    checks = []
    for region in contract['regions']:
        expected_units = (region['text'].split() if version == 1
                          else compact_ngrams(region['text'], 6 if version == 3 else 3))
        expected = Counter(expected_units)
        if not expected:
            raise ValueError('Empty body region')
        matched = assigned_by_id[region['id']] if version == 3 else predicted & expected
        missing = expected - matched
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
        if version == 3:
            reference_matching_text, reference_spans = _reference_match_spans(
                region.get('text', ''), matched)
            output_spans = output_spans_by_id[region['id']]
            checks[-1].update({
                'matching_text_sha256': hashlib.sha256(matching_text.encode()).hexdigest(),
                'matched_output_spans': output_spans,
                'matched_output_passages': _span_passages(matching_text, output_spans),
                'reference_matching_text_sha256': hashlib.sha256(
                    reference_matching_text.encode()).hexdigest(),
                'matched_reference_spans': reference_spans,
                'matched_reference_passages': _span_passages(
                    reference_matching_text, reference_spans),
                'unmatched_reference_passages': _unmatched_passages(
                    reference_matching_text, reference_spans),
            })
        if version == 1:
            checks[-1].update(expected_tokens=checks[-1]['expected_units'],
                              matched_tokens=checks[-1]['matched_units'],
                              missing_tokens=checks[-1]['missing_units'])
    if version != 3:
        coverage_pass = (all(c['floor_pass'] for c in checks)
                         and sum(c['target_pass'] for c in checks) >= required_regions)
    return {'body_coverage_pass': coverage_pass,
            'body_target_regions': sum(c['target_pass'] for c in checks),
            'body_required_target_regions': required_regions,
            **({'body_matching_text_sha256': hashlib.sha256(
                matching_text.encode()).hexdigest()} if version == 3 else {}),
            'body_region_results': checks}


def evaluate_body_requirements(content, contract):
    """Evaluate prose directly, then one structure-preserving table column at a time."""
    result = _evaluate_body_surface(content, contract)
    if not contract or result['body_coverage_pass']:
        return result
    from .structured_tables import table_header_column_surfaces
    for surface in table_header_column_surfaces(content):
        candidate = _evaluate_body_surface(surface['text'], contract)
        if candidate['body_coverage_pass']:
            candidate.update({
                'body_matching_surface': 'table_header_and_column',
                'body_matching_table_index': surface['table_index'],
                'body_matching_column_index': surface['column_index'],
            })
            return candidate
    return result
