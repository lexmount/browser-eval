import unittest
from lexbench_webfetch.metrics import evaluate_content, normalize_for_match


class CurrencyMatchingTests(unittest.TestCase):
    def matches(self, actual, expected):
        return evaluate_content(actual, [], required_facts=[{'alternatives':[expected]}])['content_pass']

    def test_inline_styling_preserves_currency_and_installments(self):
        for symbol in '$£€¥':
            self.assertTrue(self.matches(f'{symbol} **10**.90', f'{symbol}10.90'))
            self.assertTrue(self.matches(f'or 9 x **{symbol}1.21**', f'or 9 x{symbol}1.21'))
            self.assertTrue(self.matches(f'or 9 × {symbol}1.21', f'or 9 x{symbol}1.21'))
            self.assertTrue(self.matches(f'or9x**{symbol}1.21**', f'or 9 x{symbol}1.21'))

    def test_never_merge_digits_decimal_or_sign(self):
        for actual in ('$100.90', '$10.901', '$10 .90', '$1 0.90', '$-10.90', '-$10.90', '- $10.90', '+$10.90', '−$ 10.90', '$−10.90'):
            self.assertFalse(self.matches(actual, '$10.90'), actual)
        self.assertFalse(self.matches('or 19 x $1.21', 'or 9 x$1.21'))
        self.assertFalse(self.matches('or 9 x $11.21', 'or 9 x$1.21'))
        self.assertTrue(self.matches('$ -10.90', '$-10.90'))
        self.assertEqual(normalize_for_match('10 . 90 1 2 - 3'), '10 . 90 1 2 - 3')
