import unittest

from scripts.apply_latency_correction import validated_final_round


class LatencyCorrectionTests(unittest.TestCase):
    def rows(self):
        return [
            {'round': round_id, 'provider': provider, 'case_id': case_id, 'ok': True}
            for round_id in (1, 2, 3)
            for provider in ('moli', 'moli-1.1.3')
            for case_id in ('a', 'b')
        ]

    def test_selects_complete_final_round(self):
        round_id, selected, keys = validated_final_round(self.rows())
        self.assertEqual(round_id, 3)
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(keys), 4)

    def test_rejects_failed_call_in_an_earlier_round(self):
        rows = self.rows()
        rows[0]['ok'] = False
        with self.assertRaisesRegex(SystemExit, 'failed call'):
            validated_final_round(rows)

    def test_rejects_different_round_scopes(self):
        rows = self.rows()
        rows.pop(0)
        with self.assertRaisesRegex(SystemExit, 'different scopes'):
            validated_final_round(rows)


if __name__ == '__main__':
    unittest.main()
