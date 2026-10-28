import unittest

from lexbench_webfetch.ordered_tree import tree_distance


class OrderedTreeTests(unittest.TestCase):
    def test_removed_tree_contract_does_not_retain_prior_tree_metrics(self):
        from lexbench_webfetch.scoring import score_attempt
        result = score_attempt({'url': 'https://example.test'}, b'normal text', ok=True,
                               previous_metrics={'ordered_tree_similarity': 1, 'ordered_tree': {}})
        self.assertNotIn('ordered_tree_similarity', result)
        self.assertNotIn('ordered_tree', result)

    def test_ignored_attributes_do_not_defeat_identical_tree_shortcut(self):
        left = {'tag': 'table', 'attrs': {'class': 'x'}, 'children': [{'tag': 'td', 'text': 'A'}]}
        right = {'tag': 'table', 'children': [{'tag': 'td', 'text': 'A'}]}
        self.assertEqual(tree_distance(left, right)['score'], 1)

    def test_large_same_shape_near_match_has_exact_substitution_cost(self):
        left = {'tag': 'table', 'children': [{'tag': 'td', 'text': 'A'} for _ in range(3000)]}
        right = {'tag': 'table', 'children': [{'tag': 'td', 'text': 'A'} for _ in range(3000)]}
        right['children'][4]['text'] = 'B'
        self.assertEqual(tree_distance(left, right)['edit_distance'], 1)

    def test_reordered_children_are_not_identical(self):
        left = {'tag': 'table', 'children': [{'tag': 'td', 'text': x} for x in 'ABC']}
        right = {'tag': 'table', 'children': [{'tag': 'td', 'text': x} for x in 'BCA']}
        self.assertEqual(tree_distance(left, right)['edit_distance'], 2)
        self.assertEqual(tree_distance(right, left)['edit_distance'], 2)

    def test_large_multiple_renames_are_certified_by_label_lower_bound(self):
        left = {'tag': 'table', 'children': [{'tag': 'td', 'text': 'A'} for _ in range(3000)]}
        right = {'tag': 'table', 'children': [{'tag': 'td', 'text': 'B'} for _ in range(3000)]}
        self.assertEqual(tree_distance(left, right)['edit_distance'], 3000)

    def test_large_unique_near_matches_have_exact_normalized_cost(self):
        predicted = [f'Item {index}: 5\\.00' for index in range(3000)]
        reference = [item.replace('\\.', '.') for item in predicted]
        left = {'tag': 'table', 'children': [{'tag': 'td', 'text': item} for item in predicted]}
        right = {'tag': 'table', 'children': [{'tag': 'td', 'text': item} for item in reference]}
        expected = sum(1 / len(item) for item in predicted)
        self.assertAlmostEqual(tree_distance(left, right)['edit_distance'], expected)

    def test_large_shift_does_not_claim_aligned_cost_is_exact(self):
        left = {'tag': 'table', 'children': [{'tag': 'td', 'text': str(x)} for x in range(3000)]}
        right = {'tag': 'table', 'children': left['children'][1:] + left['children'][:1]}
        with self.assertRaisesRegex(ValueError, 'not certified'):
            tree_distance(left, right)

    def test_large_different_shape_fails_closed(self):
        left = {'tag': 'table', 'children': [{'tag': 'td'} for _ in range(3000)]}
        right = {'tag': 'table', 'children': [{'tag': 'tr', 'children': [{'tag': 'td'}]} for _ in range(2000)]}
        with self.assertRaisesRegex(ValueError, 'no approximate score'):
            tree_distance(left, right)
