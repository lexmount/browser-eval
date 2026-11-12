"""Scenarios for explaining parallel upstream attempts and missing telemetry."""

import unittest

from harness.web_search.diagnostics import operational


class TimingScenarios(unittest.TestCase):
    def test_parallel_durations_are_not_added_to_wall_time(self):
        row = dict(
            id="a",
            case_id="q",
            method="m",
            round=0,
            elapsed_ms=1300,
            server_timing={"elapsed_ms": 1000},
        )
        data = {
            "a": {
                "attempts": [
                    dict(
                        engine="a",
                        started_after_ms=0,
                        elapsed_ms=300,
                        outcome="results",
                        parsed_count=5,
                    ),
                    dict(
                        engine="b",
                        started_after_ms=100,
                        elapsed_ms=850,
                        outcome="results",
                        parsed_count=5,
                    ),
                    dict(engine="c", started_after_ms=0, elapsed_ms=990, outcome="cancelled"),
                ]
            }
        }
        result = operational([row], data)["calls"][0]
        self.assertEqual(result["first_results_ms"], 300)
        self.assertEqual(result["after_first_ms"], 700)
        self.assertEqual(result["outside_server_ms"], 300)
        self.assertEqual(result["attempts"], 3)

    def test_empty_attempt_is_not_a_first_useful_result(self):
        row = dict(id="a", case_id="q", method="m", round=0, elapsed_ms=100)
        result = operational(
            [row],
            {
                "a": {
                    "attempts": [
                        dict(started_after_ms=0, elapsed_ms=1, outcome="results", parsed_count=0)
                    ]
                }
            },
        )
        self.assertIsNone(result["calls"][0]["first_results_ms"])
        self.assertIsNone(result["methods"]["m"]["server_ms"]["mean"])
        self.assertEqual(result["methods"]["m"]["server_ms"]["n"], 0)

    def test_no_response_timeout_is_not_body_transfer_but_interrupted_download_is(self):
        rows = [
            dict(id="timeout", case_id="q", method="m", round=0, elapsed_ms=60000,
                 http_status=0, ttfb_ms=0,
                 network={"body_ms": 60000, "download_bytes": 0}),
            dict(id="interrupted", case_id="r", method="m", round=0, elapsed_ms=60000,
                 http_status=200, ttfb_ms=100,
                 network={"body_ms": 59900, "download_bytes": 128}),
        ]
        result = operational(rows, {})
        absent, partial = result["calls"]
        self.assertIsNone(absent["ttfb_ms"])
        self.assertIsNone(absent["body_ms"])
        self.assertEqual(partial["ttfb_ms"], 100)
        self.assertEqual(partial["body_ms"], 59900)
        self.assertEqual(result["methods"]["m"]["client_ms"], {"n": 2, "mean": 60000})
        self.assertEqual(result["methods"]["m"]["body_ms"], {"n": 1, "mean": 59900})
        self.assertEqual(result["methods"]["m"]["ttfb_ms"], {"n": 1, "mean": 100})
        # Zero bytes is an actual measurement; it is not missing transfer telemetry.
        self.assertEqual(result["methods"]["m"]["bytes"], {"n": 2, "mean": 64})

    def test_independent_first_byte_evidence_preserves_zero_clock(self):
        for evidence in ({"http_status": 204}, {"network": {"download_bytes": 1}},
                         {"ttfb_ms": 50}):
            with self.subTest(evidence=evidence):
                row = dict(id="a", case_id="q", method="m", round=0, elapsed_ms=100,
                           http_status=0, ttfb_ms=0, network={"body_ms": 100})
                row.update(evidence)
                row["network"] = {"body_ms": 100, **row["network"]}
                call = operational([row], {})["calls"][0]
                self.assertEqual(call["body_ms"], 100)
                self.assertEqual(call["ttfb_ms"], row["ttfb_ms"])

    def test_unreported_attempts_do_not_count_as_zero_work(self):
        rows = [dict(id=str(i), case_id=str(i), method="m", round=0, elapsed_ms=100)
                for i in range(4)]
        payloads = {"0": {}, "1": {"attempts": None}, "2": {"attempts": []},
                    "3": {"attempts": [{"outcome": "cancelled"}, {"outcome": "error"}]}}
        result = operational(rows, payloads)
        self.assertEqual([c["attempts"] for c in result["calls"]], [None, None, 0, 2])
        self.assertEqual(result["methods"]["m"]["attempts"], {"n": 2, "mean": 1})
        self.assertEqual(result["methods"]["m"]["outcomes"], {"cancelled": 1, "error": 1})
        self.assertEqual(result["methods"]["m"]["body_ms"], {"n": 0, "mean": None})
        self.assertEqual(result["methods"]["m"]["ttfb_ms"], {"n": 0, "mean": None})


def test_url_identity_diagnostic_never_changes_search_scores_or_references():
    from copy import deepcopy

    from harness.web_search.diagnostics import factual_source_diagnostic
    from lexbench_websearch.scoring import evaluate

    target, alias = 'https://reference.test/fact', 'https://old.reference.test/fact'
    case = dict(id='fact', reference_answer='42', reference_urls=[target])
    profile = {'cases': [case]}
    results = [{'url': alias, 'snippet': '42'}]
    rows = [dict(id=method, method=method, case_id='fact', results=results,
                 metrics=evaluate(case, results)) for method in ('a', 'b')]
    observation = dict(url=alias, final=target, status=200, verified_at='2026-01-01',
                       chain=[dict(url=alias, status=301, location=target)])
    original = deepcopy((profile, rows))
    diagnostic = factual_source_diagnostic(profile, rows, [observation])
    for method in ('a', 'b'):
        counts = diagnostic['methods'][method]
        assert counts['source_hit'] == 0
        assert counts['answer_only'] == 1
        assert counts['observed_redirect_extra'] == 1
        assert counts['same_page_diagnostic_hit'] == 1
    assert (profile, rows) == original
    assert all(row['metrics']['labeled_source_hit_5'] == 0 for row in rows)
    for changed in (dict(observation, status=403), dict(observation, chain=[]),
                    dict(observation, final='https://reference.test/other')):
        counts = factual_source_diagnostic(profile, rows, [changed])['methods']['a']
        assert counts['observed_redirect_extra'] == 0
    late = deepcopy(rows)
    for row in late:
        row['results'] = [{'url': f'https://other.test/{i}'} for i in range(5)] + results
    counts = factual_source_diagnostic(profile, late, [observation])['methods']['a']
    assert counts['observed_redirect_extra'] == 0
