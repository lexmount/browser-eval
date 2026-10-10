import unittest
from harness.reporting.presentation import check_presentation

class PresentationContractTests(unittest.TestCase):
    def table(self, values, marked):
        return '<table>' + ''.join(f'<tr><td data-metric="mean" data-metric-direction="min" data-sort="{v}">'+(f'<b class="comparison-winner">{v}</b>' if i in marked else str(v))+'</td></tr>' for i,v in enumerate(values)) + '</table>'

    def test_extrema_and_negative_controls(self):
        for values,marked in [([10,20],{0}),([10,10],set()),([0,20],{0})]:
            check_presentation(self.table(values,marked))
        for values,marked in [([10,20],set()),([10,20],{1}),([10,10],{0})]:
            with self.subTest(values=values,marked=marked),self.assertRaises(ValueError):
                check_presentation(self.table(values,marked))

    def test_maximum_and_unavailable_values(self):
        check_presentation(self.table([10,20],{1}).replace('"min"','"max"'))
        absent='<table><tr><td data-metric="mean" data-metric-direction="min">—</td></tr></table>'
        check_presentation(absent)
        with self.assertRaises(ValueError):
            check_presentation(absent.replace('—','<b class="comparison-winner">—</b>'))

    def test_evidence_and_negative_controls(self):
        page='''<div data-report-detail="failed"><div data-result-summary></div><dl data-failure-diagnosis></dl><button data-call-log-open="result-logs"></button><dialog id="result-logs" aria-labelledby="title"><h2 id="title">Logs</h2><button data-call-log-close></button><pre data-kind="error">exact error</pre></dialog></div>'''
        check_presentation(page)
        for broken in [page.replace('id="result-logs"','id="missing"'),page.replace('data-kind="error"',''),page.replace('</div><dl','</div><div data-result-summary></div><dl'),page.replace('</dialog>','</dialog><pre>flattened raw log</pre>')]:
            with self.assertRaises(ValueError):check_presentation(broken)
