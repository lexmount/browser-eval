import unittest
import json
from pathlib import Path

from harness.web_fetch.public_benchmark_sample import AXES, _blank, _distribution, select_records


class PublicBenchmarkSampleTest(unittest.TestCase):
    def test_null_release_fields_are_counted_as_blank(self):
        self.assertTrue(_blank(None))
        self.assertTrue(_blank(""))
        self.assertFalse(_blank("# answer"))

    def test_selection_is_deterministic_and_covers_each_official_category(self):
        records = []
        for index in range(30):
            records.append({
                "track_id": f"id-{index:02d}",
                "meta": {
                    "language": ["en", "zh", "es"][index % 3],
                    "style": ["Article", "Forum", "Other"][index % 3],
                    "level": ["simple", "mid", "hard"][index % 3],
                    "table": [] if index % 2 else ["data"],
                    "code": [] if index % 5 else ["interline"],
                    "equation": [] if index % 7 else ["inline"],
                },
            })
        first = select_records(records, size=12, seed="fixed")
        second = select_records(list(reversed(records)), size=12, seed="fixed")
        self.assertEqual([row["track_id"] for row in first], [row["track_id"] for row in second])
        parent = _distribution(records)
        sample = _distribution(first)
        for axis in AXES:
            self.assertEqual(set(sample[axis]), set(parent[axis]))

    def test_committed_manifest_has_complete_declared_coverage(self):
        path = Path(__file__).resolve().parents[1] / "evaluation/public-webmainbench-200.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["cases"]), 200)
        self.assertEqual(len({case["track_id"] for case in manifest["cases"]}), 200)
        self.assertEqual(len({case["url"] for case in manifest["cases"]}), 200)
        self.assertEqual(manifest["parent"]["records"], 545)
        self.assertEqual(manifest["quality_audit"]["unique_track_ids"], 545)
        self.assertEqual(manifest["quality_audit"]["empty_groundtruth_content"], 0)
        self.assertEqual(manifest["quality_audit"]["non_null_style"], 0)
        for axis in manifest["sampling"]["official_axes"]:
            self.assertEqual(sum(manifest["sample_distribution"][axis].values()), 200)
            self.assertEqual(
                set(manifest["sample_distribution"][axis]),
                set(manifest["parent_distribution"][axis]),
            )


if __name__ == "__main__":
    unittest.main()
