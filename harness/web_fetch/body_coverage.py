"""Declared, distributed body coverage; independent of the full-text F1 metric."""
import re
from collections import Counter
from itertools import combinations
from math import ceil


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
            'body_region_results': checks}
