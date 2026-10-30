"""Opt-in ordinary form labels; synthetic inputs without browser or network."""
from copy import deepcopy
import unittest

from lexbench_webfetch.stable_tasks import (
    evaluate_stable_task, validate_acceptance_contract,
)


def control_contract(attribute="placeholder", selector="#search", label="Search articles…"):
    return {
        "version": 1, "schema": "body_text", "minimum_records": 0,
        "stable_text_anchors": [{
            "id": "control", "alternatives": [label],
            "visible_control": {"selector": selector, "attribute": attribute},
        }],
    }


class ArticleControlTextTests(unittest.TestCase):
    def passes(self, content, rules):
        return evaluate_stable_task(content, rules)["stable_task_pass"]

    def test_full_html_and_markdown_preserve_ordinary_labels(self):
        rules = control_contract()
        html = '<!doctype html><html><body><p>Article text</p><input id="search" type="search" placeholder="Search articles…"></body></html>'
        self.assertTrue(self.passes(html, rules))
        self.assertTrue(self.passes('Article text\n\nSearch articles…', rules))
        self.assertTrue(evaluate_stable_task(html, rules, anchor_text="Article text")["stable_task_pass"])

    def test_allowed_control_types(self):
        for kind in ("text", "search", "email", "url", "tel", "number"):
            with self.subTest(kind=kind):
                self.assertTrue(self.passes(
                    f'<main><input id="search" type="{kind}" placeholder="Search articles…"></main>',
                    control_contract()))
        self.assertTrue(self.passes('<main><textarea id="search" placeholder="Search articles…"></textarea></main>', control_contract()))
        for kind in ("button", "submit", "reset"):
            with self.subTest(kind=kind):
                self.assertTrue(self.passes(
                    f'<main><input id="submit" type="{kind}" value="Post Comment"></main>',
                    control_contract("value", "#submit", "Post Comment")))

    def test_undeclared_control_and_missing_selector_fail(self):
        html = '<main><input id="other" placeholder="Search articles…"></main>'
        self.assertFalse(self.passes(html, control_contract()))
        self.assertFalse(self.passes('<main>Article text</main>', control_contract()))
        rules = control_contract()
        del rules["stable_text_anchors"][0]["visible_control"]
        self.assertFalse(self.passes(html, rules))

    def test_populated_controls_do_not_display_placeholders(self):
        for value in ("Already entered", " "):
            with self.subTest(value=value):
                self.assertFalse(self.passes(
                    f'<main><input id="search" placeholder="Search articles…" value="{value}"></main>',
                    control_contract()))
                self.assertFalse(self.passes(
                    f'<main><textarea id="search" placeholder="Search articles…">{value}</textarea></main>',
                    control_contract()))
        self.assertTrue(self.passes(
            '<main><input id="search" placeholder="Search articles…" value=""></main>',
            control_contract()))

    def test_control_text_cannot_supply_another_anchor_or_page_pattern(self):
        rules = control_contract()
        rules["stable_text_anchors"].append({"id": "separate", "alternatives": ["Search articles…"]})
        html = '<main><input id="search" placeholder="Search articles…"></main>'
        result = evaluate_stable_task(html, rules)
        self.assertIn("missing_stable_anchor:separate", result["stable_task_errors"])
        rules["stable_text_anchors"].pop()
        rules["page_patterns"] = ["Search articles"]
        self.assertFalse(self.passes(html, rules))

    def test_hidden_controls_and_ancestors_fail(self):
        control = '<input id="search" placeholder="Search articles…">'
        for attr in ('hidden', 'style="display:none"', 'style="visibility:hidden"',
                     'style="visibility:collapse"', 'style="opacity:0"'):
            with self.subTest(attr=attr):
                self.assertFalse(self.passes(f'<main {attr}>{control}</main>', control_contract()))
                self.assertFalse(self.passes(f'<main><input {attr} id="search" placeholder="Search articles…"></main>', control_contract()))

    def test_password_hidden_and_non_button_values_fail(self):
        for kind in ("password", "hidden", "text", "email", "checkbox"):
            with self.subTest(kind=kind):
                self.assertFalse(self.passes(
                    f'<main><input id="submit" type="{kind}" value="Post Comment"></main>',
                    control_contract("value", "#submit", "Post Comment")))
        for kind in ("password", "hidden", "submit", "checkbox"):
            with self.subTest(kind=kind):
                self.assertFalse(self.passes(
                    f'<main><input id="search" type="{kind}" placeholder="Search articles…"></main>',
                    control_contract()))

    def test_accessibility_and_metadata_attributes_cannot_supply_text(self):
        for attribute in ("aria-label", "title", "alt"):
            with self.subTest(attribute=attribute):
                self.assertFalse(self.passes(
                    f'<main><input id="search" {attribute}="Search articles…"></main>', control_contract()))
        self.assertFalse(self.passes('<main><div id="search" placeholder="Search articles…"></div></main>', control_contract()))

    def test_invalid_declarations_fail_closed(self):
        invalid = [None, {}, {"selector": "#search", "attribute": "aria-label"},
                   {"selector": "#search", "attribute": "title"},
                   {"selector": "#search", "attribute": []},
                   {"selector": "[", "attribute": "placeholder"},
                   {"selector": ":not-a-real-pseudo", "attribute": "placeholder"},
                   {"selector": " ", "attribute": "placeholder"},
                   {"selector": "#search", "attribute": "value", "unknown": True}]
        for declaration in invalid:
            with self.subTest(declaration=declaration):
                rules = deepcopy(control_contract())
                rules["stable_text_anchors"][0]["visible_control"] = declaration
                with self.assertRaises(ValueError):
                    validate_acceptance_contract(rules)


if __name__ == "__main__":
    unittest.main()
