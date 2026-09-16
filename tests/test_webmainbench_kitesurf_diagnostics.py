import unittest

from scripts.diagnose_webmainbench_kitesurf import remove_scripts, variants, TARGETS, keep_inline_scripts


class KitesurfDiagnosticTests(unittest.TestCase):
    def test_script_controls_partition_external_and_inline_blocks(self):
        html = '<p>x</p><script src="x.js"></script><script>go()</script><p>y</p>'
        self.assertEqual(remove_scripts(html, external=True),
                         '<p>x</p><script>go()</script><p>y</p>')
        self.assertEqual(remove_scripts(html, external=False),
                         '<p>x</p><script src="x.js"></script><p>y</p>')
        self.assertEqual(remove_scripts(html, external=None), '<p>x</p><p>y</p>')

    def test_targeted_control_removes_exactly_one_bound_statement(self):
        case_id = next(iter(TARGETS))
        target = TARGETS[case_id]
        html = '<p>before</p><script>' + target + '</script><p>after</p>'
        result = variants(case_id, html)
        self.assertEqual(result['original'], html)
        self.assertEqual(result['without_target_statement'],
                         '<p>before</p><script></script><p>after</p>')
        with self.assertRaises(ValueError):
            variants(case_id, '<p>missing</p>')

    def test_structure_controls_reduce_tables_and_keep_text_control(self):
        source = '<html><body><table><tr><td>A</td></tr><tr><td>B</td></tr></table></body></html>'
        result = variants('case', source, 'structure')
        self.assertIn('A', result['text_only'])
        self.assertNotIn('<table', result['without_tables'])
        self.assertEqual(result['first_100_table_rows'].count('<tr>'), 2)

    def test_second_structure_profile_partitions_tables(self):
        rows = ''.join('<tr><td>%d</td></tr>' % index for index in range(4100))
        result = variants('30b05f1d-56b3-48d3-b587-aea4e99bc003',
                          '<table>' + rows + '</table>', 'structure2')
        self.assertEqual(result['first_1000_table_rows'].count('<tr>'), 1000)
        tables = ''.join('<table><tr><td>%d</td></tr></table>' % index for index in range(25))
        result = variants('a5791c85-1701-4ffd-97e1-779aeefae3b8', tables, 'structure2')
        self.assertEqual(result['first_12_tables'].count('<table>'), 12)
        self.assertEqual(result['last_13_tables'].count('<table>'), 13)

    def test_inline_bisection_preserves_external_scripts(self):
        source = ''.join('<script src="e%d.js"></script><script>i%d()</script>' % (i, i)
                         for i in range(63))
        result = keep_inline_scripts(source, 31, 63)
        self.assertEqual(result.count(' src='), 63)
        self.assertNotIn('i30()', result)
        self.assertIn('i31()', result)
        self.assertIn('i62()', result)

    def test_root_controls_each_change_one_declared_dependency(self):
        handler = 'window.onerror=function(){clickURL=cleanClickURL(document.location.href);return true;}'
        call = 'if(!self.clickURL) clickURL=cleanClickURL(parent.location.href);'
        source = '<html><head></head><body><script>' + handler + call + '</script></body></html>'
        result = variants('4c631939-b5d2-4750-a19e-687459751b91', source, 'inline_root')
        self.assertNotIn(handler, result['without_recursive_error_handler'])
        self.assertNotIn(call, result['without_missing_function_call'])
        self.assertIn('function cleanClickURL(value)', result['with_cleanClickURL_stub'])
