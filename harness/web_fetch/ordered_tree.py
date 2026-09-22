"""Ordered-tree edit distance over explicit nodes, never serialized strings.

The node label/cost follows the pinned WebMainBench TableConfig. Parsing and
content selection belong to the caller. Errors propagate instead of producing
an optimistic node-count fallback.
"""
from __future__ import annotations


def node_count(node: dict) -> int:
    if not isinstance(node, dict) or not isinstance(node.get('tag'), str):
        raise ValueError('An ordered tree requires explicit tagged nodes')
    children = node.get('children', [])
    if not isinstance(children, list):
        raise ValueError('Tree children must be a list')
    return 1 + sum(node_count(child) for child in children)


def tree_distance(predicted: dict, reference: dict) -> dict:
    from apted import APTED, Config
    from rapidfuzz.distance import Levenshtein

    class NodeConfig(Config):
        def children(self, node):
            return node.get('children', [])

        def rename(self, left, right):
            if left['tag'] != right['tag']:
                return 1
            # Preserve the pinned scorer's label normalization and text costs.
            def label(node):
                return node.get('text', '').replace('(', '[').replace(')', ']').replace(',', ';')
            a, b = label(left), label(right)
            if a == b:
                return 0
            if not a or not b:
                return max(len(a), len(b))
            return Levenshtein.distance(a, b) / max(len(a), len(b))

    predicted_nodes, groundtruth_nodes = node_count(predicted), node_count(reference)
    config = NodeConfig()
    def aligned_cost(left, right):
        a, b = config.children(left), config.children(right)
        if len(a) != len(b):
            return float('inf')
        return config.rename(left, right) + sum(aligned_cost(x, y) for x, y in zip(a, b))
    aligned = aligned_cost(predicted, reference) if predicted_nodes == groundtruth_nodes else float('inf')
    # For equal-size, equal-shape trees the no-insert/delete mapping is unique.
    # Any competing structural mapping needs >=1 deletion + >=1 insertion,
    # costing >=2. Thus an aligned cost <=2 is the exact optimum, not a heuristic.
    if aligned <= 2:
        distance = float(aligned)
    elif predicted_nodes * groundtruth_nodes > 4_000_000:
        # Independent label lower bound: every source node must either be
        # deleted (cost 1) or renamed to some target node. Minimize that cost
        # without imposing order, ancestry or one-to-one matching. This can
        # only LOWER the true distance. Equality with the valid aligned mapping
        # certifies the exact optimum even for very large near-identical tables.
        if aligned == float('inf'):
            raise ValueError('Exact tree calculation exceeds safe workspace bound; no approximate score emitted')
        targets = {}
        pending = [reference]
        while pending:
            node = pending.pop()
            label = node.get('text', '')
            targets.setdefault(node['tag'], {}).setdefault(len(label), {})[label] = node
            pending.extend(config.children(node))
        pairs = [(predicted, reference)]
        while pairs:
            left, right = pairs.pop()
            aligned_node_cost = config.rename(left, right)
            cost = min(1., aligned_node_cost)
            if cost < aligned_node_cost:
                raise ValueError('Exact tree bound not certified; no approximate score emitted')
            if cost:
                length = len(left.get('text', ''))
                for candidate_length, candidates in targets.get(left['tag'], {}).items():
                    maximum = max(length, candidate_length)
                    if maximum and abs(length - candidate_length) / maximum >= cost:
                        continue
                    for candidate in candidates.values():
                        cost = min(cost, 1., config.rename(left, candidate))
                        if cost < aligned_node_cost:
                            raise ValueError('Exact tree bound not certified; no approximate score emitted')
                        if cost == 0:
                            break
                    if cost == 0:
                        break
            pairs.extend(zip(config.children(left), config.children(right)))
        distance = float(aligned)
    else:
        distance = float(APTED(predicted, reference, config).compute_edit_distance())
    if distance + 1e-9 < abs(predicted_nodes - groundtruth_nodes):
        raise ValueError('Tree distance violates the node-count lower bound')
    maximum = max(predicted_nodes, groundtruth_nodes)
    return {'score': max(0., min(1., 1 - distance / maximum)),
            'edit_distance': distance, 'predicted_nodes': predicted_nodes,
            'groundtruth_nodes': groundtruth_nodes, 'max_nodes': maximum}
