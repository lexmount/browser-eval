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
