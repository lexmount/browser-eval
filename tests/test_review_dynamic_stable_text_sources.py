import unittest

from scripts.review_dynamic_stable_text_sources import adjudicate


def observed(*texts):
    rows = [
        {"text": text, "tag": "a", "landmark": "nav", "linked": True}
        for text in texts
    ]
    return {
        "all_observations_complete": True,
        "observations": [
            {"status": "success", "ordinary_text_nodes": rows}
            for _ in range(3)
        ],
    }


def context(*rows, status=200):
    return {
        "status": "success",
        "http_status": status,
        "scroll_x": 0,
        "scroll_y": 0,
        "rows": list(rows),
    }


def named_link_case(anchors=None):
    return {
        "id": "case",
        "acceptance_contract": {
            "version": 1,
            "schema": "named_link",
            "minimum_records": 1,
            "hosts": ["example.com"],
            "entry_path": "/items/[^/]+",
            "stable_text_anchors": anchors or [],
        },
    }


class StableTextSourceReviewTests(unittest.TestCase):
    def test_keeps_every_qualified_navigation_text_without_upper_limit(self):
        texts = [f"Navigation {index}" for index in range(30)]
        rows = [
            {"text": text, "tag": "a", "landmark": "nav", "linked": True,
             "href": f"https://example.com/{index}", "target_record": False,
             "record_container": "li"}
            for index, text in enumerate(texts)
        ]
        anchors, _ = adjudicate(named_link_case(), observed(*texts), context(*rows))
        self.assertEqual({a["alternatives"][0] for a in anchors}, set(texts))

    def test_excludes_declared_target_record_text(self):
        row = {"text": "Current item", "tag": "a", "landmark": "main",
               "linked": True, "href": "https://example.com/items/current",
               "target_record": True, "record_container": "article"}
        anchors, audit = adjudicate(
            named_link_case(), observed("Current item"), context(row))
        self.assertEqual(anchors, [])
        self.assertEqual(audit["decisions"][0]["reason"],
                         "text_belongs_only_to_declared_target_records")

    def test_excludes_error_page_text_from_non_success_response(self):
        row = {"text": "An error occurred", "tag": "h1", "landmark": "main",
               "linked": False, "href": None, "target_record": False,
               "record_container": None}
        anchors, audit = adjudicate(
            named_link_case(), observed("An error occurred"), context(row, status=403))
        self.assertEqual(anchors, [])
        self.assertEqual(audit["decisions"][0]["reason"],
                         "later_source_context_load_not_usable")

    def test_excludes_pagination_link(self):
        row = {"text": "Next", "tag": "a", "landmark": "nav", "linked": True,
               "href": "https://example.com/page/2/", "target_record": False,
               "record_container": "li"}
        anchors, audit = adjudicate(named_link_case(), observed("Next"), context(row))
        self.assertEqual(anchors, [])
        self.assertEqual(audit["decisions"][0]["reason"],
                         "pagination_or_navigation_control")

    def test_excludes_current_headline_even_when_repeated_three_times(self):
        row = {"text": "A current headline", "tag": "h3", "landmark": "main",
               "linked": True, "href": "https://example.com/news/today",
               "target_record": False, "record_container": None}
        anchors, audit = adjudicate(
            named_link_case(), observed("A current headline"), context(row))
        self.assertEqual(anchors, [])
        self.assertEqual(audit["decisions"][0]["reason"],
                         "current_headline_or_content_card")

    def test_does_not_retain_link_anchor_when_three_load_check_is_incomplete(self):
        old = [{"id": "historical", "alternatives": ["Known stable text"]}]
        anchors, audit = adjudicate(named_link_case(old), None, None)
        self.assertEqual(anchors, [])
        self.assertEqual(audit["removed_previous_anchors"], old)
        self.assertEqual(
            audit["decision"],
            "removed_unverified_link_text_observation_incomplete")


if __name__ == "__main__":
    unittest.main()
