import statistics
import unittest

from lexbench_webfetch.metrics import percentile


class PercentileTests(unittest.TestCase):
    def test_median_even_and_odd(self):
        for values in ([1, 5], [5, 1, 3], [100, 2113.13, 2613.91, 9000]):
            self.assertEqual(percentile(values, .5), round(statistics.median(values), 2))

    def test_p95_nearest_rank_coverage(self):
        for size in range(1, 101):
            values=list(range(1, size+1))
            self.assertGreaterEqual(sum(x <= percentile(values, .95) for x in values)/size, .95)
        self.assertEqual(percentile(list(range(1, 13)), .95), 12)
        self.assertIsNone(percentile([], .95))
        self.assertEqual(percentile([7], .5), 7)
