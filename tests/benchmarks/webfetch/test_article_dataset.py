"""Article authoring must reject incomplete references and incomplete answers."""
from .article_contract_fixtures import comment_labels, post_labels
import copy
import hashlib
import html
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lexbench_webfetch.article_dataset import compile_case, compile_dataset, DEFINITIONS
from lexbench_webfetch.benchmark import public_task
from lexbench_webfetch.evaluation import evaluate_response
from lexbench_webfetch.models import EvalCase, validate_case
from lexbench_webfetch.scoring import load_list_reference, score_attempt


class ArticleContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paragraphs = [
            "A comet travels beyond the distant planets. Astronomers measure its icy "
            "nucleus through reflected sunlight while spectrometers distinguish carbon "
            "molecules from frozen dust. Their observations establish the opening survey.",
            "Across mountain valleys, botanists catalogue alpine flowers. Tiny seeds "
            "survive winter beneath protective snow before spring temperatures awaken "
            "their roots. Field notebooks describe every habitat in this second study.",
            "An underwater microphone records whale songs near the coastline. Divers "
            "compare low frequency pulses across migrating families and carefully mark "
            "the changing tide. This acoustic experiment occupies the central section.",
            "Inside a pottery workshop, artisans shape clay into cooking vessels. "
            "Kiln temperatures transform fragile surfaces into durable ceramics, and "
            "glazes produce vivid copper colours. Excavations explain the fourth investigation.",
            "Finally, railway engineers inspect a suspended bridge. Steel cables "
            "distribute heavy carriage loads between stone towers as instruments record "
            "wind pressure. The closing assessment recommends regular structural inspections.",
        ]
        self.body = "\n\n".join(self.paragraphs) + "\n"
        raw = self.body.encode()
        (self.root / "body.txt").write_bytes(raw)
        self.case = {"id": "article-001", "name": "Comet field report",
                     "url": "https://example.test/article", "task_prompt": "Return the full article"}
        self.rule = {
            "id": "article-001", "status": "verified", "target_kind": "fixed_article",
            "reason": "Complete synthetic article used to exercise the public scorer.",
            "body_reference": {"path": "body.txt", "sha256": hashlib.sha256(raw).hexdigest(),
                               "chars": len(self.body)},
            "reference_scope": {"complete_initial_body": True,
                                "start": self.paragraphs[0], "end": self.paragraphs[-1]},
            "body_regions": [[1, 2], [3, 4], [5, 6], [7, 8], [9, 9]],
            "acceptance_contract": {
                "version": 1, "schema": "body_text", "minimum_records": 0,
                "page_patterns": ["Comet field report"], "stable_text_role": "required",
                "stable_text_anchors": [{"id": "archive", "alternatives": ["Article archive"]}],
            },
            "stable_text_review": [{"id": "archive", "region": "navigation",
                                    "basis": "Fixed archive label in the synthetic page",
                                    "observed_on": "2026-10-19"}],
            "excluded_regions": ["Unrelated changing recommendations and comments."],
        }
        self.task = compile_case(self.case, self.rule, self.root)
        self.prefix = "# Comet field report\n\nArticle archive\n\n"

    def score(self, text):
        return evaluate_response(self.task, text.encode())["passed"]

    def test_compiler_requires_every_region_and_binds_ending(self):
        body = self.task["body_requirements"]
        self.assertEqual(body["version"], 4)
        self.assertEqual(body["minimum_floor"], 0.8)
        self.assertEqual(body["minimum_region_passes"], len(body["regions"]))
        self.assertEqual(body["ending"]["text"], self.rule["reference_scope"]["end"])
        self.assertTrue(self.score(self.prefix + self.body))
        self.assertTrue(self.score(self.prefix + "\n\n".join("**" + p + "**" for p in self.paragraphs)))
        self.assertFalse(self.score(self.prefix + "\n\n".join(self.paragraphs[:-1])))
        public = public_task(self.task)["body_requirements"]["ending"]
        self.assertNotIn("text", public)
        self.assertEqual(public["text_binding"]["chars"], len(self.paragraphs[-1]))

    def test_review_defaults_preserve_acceptance_and_explicit_overrides(self):
        rule = copy.deepcopy(self.rule)
        original = rule["stable_text_review"][0]
        rule["stable_text_review_defaults"] = {k: original.pop(k) for k in ("basis", "observed_on")}
        self.assertEqual(compile_case(self.case, rule, self.root), self.task)
        rule["stable_text_review"][0]["basis"] = ""
        with self.assertRaises(ValueError):
            compile_case(self.case, rule, self.root)
        del rule["stable_text_review"][0]["basis"]
        rule["stable_text_review_defaults"]["region"] = "Should remain explicit"
        with self.assertRaises(ValueError):
            compile_case(self.case, rule, self.root)

    def test_one_region_below_eighty_no_longer_uses_sixty_percent_allowance(self):
        text = self.prefix + "\n\n".join([self.paragraphs[0][:int(len(self.paragraphs[0]) * .72)], *self.paragraphs[1:]])
        legacy = copy.deepcopy(self.task)
        legacy["body_requirements"].update(version=3, minimum_floor=.6, minimum_region_passes=4)
        del legacy["body_requirements"]["ending"]
        self.assertTrue(evaluate_response(legacy, text.encode())["passed"])
        self.assertFalse(self.score(text))

    def test_legacy_regions_remain_readable_but_current_regions_are_capped(self):
        paragraphs = self.paragraphs + [
            "A desert observatory records meteor paths across the darkest sky.",
            "Marine researchers preserve specimens in a coastal laboratory.",
        ]
        raw = ("\n".join(paragraphs) + "\n").encode()
        (self.root / "body.txt").write_bytes(raw)
        rule = copy.deepcopy(self.rule)
        rule["body_reference"].update(sha256=hashlib.sha256(raw).hexdigest(), chars=len(raw.decode()))
        rule["reference_scope"].update(start=paragraphs[0], end=paragraphs[-1])
        rule["body_regions"] = [[i, i] for i in range(1, 8)]
        task = compile_case(self.case, rule, self.root)
        self.assertEqual(len(task["body_requirements"]["regions"]), 7)
        current = copy.deepcopy(rule)
        current["body_segmentation"] = {"version": 3}
        with self.assertRaisesRegex(ValueError, "at most five"):
            compile_case(self.case, current, self.root)
        self.assertTrue(evaluate_response(task, (self.prefix + raw.decode()).encode())["passed"])
        for omitted in range(7):
            with self.subTest(omitted=omitted):
                incomplete = self.prefix + "\n".join(p for i, p in enumerate(paragraphs) if i != omitted)
                self.assertFalse(evaluate_response(task, incomplete.encode())["passed"])

    def test_semantic_regions_keep_labels_and_reject_misbound_reviews(self):
        rule = copy.deepcopy(self.rule)
        rule["body_segmentation"] = {"version": 3, "method": "source_sections",
                                    "reference_sha256": rule["body_reference"]["sha256"]}
        rule["body_region_review"] = [
            {"lines": bounds, "label": f"Observed section {i}"}
            for i, bounds in enumerate(rule["body_regions"], 1)]
        task = compile_case(self.case, rule, self.root)
        self.assertEqual(task["body_requirements"]["regions"][0]["label"], "Observed section 1")
        self.assertEqual(task["body_requirements"]["regions"][0]["source_line_range"], [1, 2])
        for field, value in [("label", ""), ("lines", [2, 3])]:
            bad = copy.deepcopy(rule)
            bad["body_region_review"][0][field] = value
            with self.assertRaisesRegex(ValueError, "source-bound readable label"):
                compile_case(self.case, bad, self.root)

    def test_historical_comment_qualification_binds_actual_source_bytes(self):
        source = b"<h1>Comet field report</h1><p>Independent complete initial page.</p>"
        (self.root / "source.html").write_bytes(source)
        reference = {"version": 1, "case_id": self.case["id"], "url": self.case["url"],
                     "scope": "initial_load_only", "context_id": "historical-authoring",
                     "observed_on": "2026-10-19", "observation_complete": True,
                     "source_observation_counts": [0], "records": [],
                     "source_evidence": [{"path": "source.html",
                         "sha256": hashlib.sha256(source).hexdigest(),
                         "scope": "complete_initial_page"}]}
        raw = (json.dumps(reference) + "\n").encode()
        (self.root / "comments.json").write_bytes(raw)
        rule = copy.deepcopy(self.rule)
        rule["comment_coverage_review"] = {"status": "observed_absent",
                                           "basis": "Independent initial page has no comments."}
        with self.assertRaisesRegex(ValueError, "independent qualification evidence"):
            compile_case(self.case, rule, self.root)
        rule["comment_qualification_reference"] = {
            "path": "comments.json", "sha256": hashlib.sha256(raw).hexdigest()}
        task = compile_case(self.case, rule, self.root)
        # Historical qualification establishes absence without a runtime reference.
        unavailable = evaluate_response(task, (self.prefix + self.body).encode())
        self.assertTrue(unavailable["passed"])
        self.assertTrue(unavailable["measured"])
        (self.root / "source.html").write_bytes(source + b"changed")
        with self.assertRaisesRegex(ValueError, "Changed article comment source evidence"):
            compile_case(self.case, rule, self.root)
        (self.root / "source.html").write_bytes(source)
        (self.root / "comments.json").write_bytes(raw + b" ")
        with self.assertRaisesRegex(ValueError, "Changed article comment qualification reference"):
            compile_case(self.case, rule, self.root)

    def test_absence_review_keeps_evidence_without_runtime_comment_contract(self):
        source = b"<article>Complete source without comments.</article>"
        (self.root / "source.html").write_bytes(source)
        reference = {"version": 1, "case_id": self.case["id"], "url": self.case["url"],
                     "scope": "initial_load_only", "context_id": "historical-authoring",
                     "observed_on": "2026-10-19", "observation_complete": True,
                     "source_observation_counts": [0], "records": [],
                     "source_evidence": [{"path": "source.html",
                         "sha256": hashlib.sha256(source).hexdigest(),
                         "scope": "complete_initial_page"}]}
        raw = (json.dumps(reference) + "\n").encode()
        (self.root / "comments.json").write_bytes(raw)
        rule = copy.deepcopy(self.rule)
        rule["comment_coverage_review"] = {
            "status": "observed_absent", "basis": "Complete initial source reviewed."}
        with self.assertRaisesRegex(ValueError, "qualification evidence"):
            compile_case(self.case, rule, self.root)
        rule["comment_qualification_reference"] = {
            "path": "comments.json", "sha256": hashlib.sha256(raw).hexdigest()}
        task = compile_case(self.case, rule, self.root)
        self.assertNotIn("comment_contract", task["source_benchmark"])
        self.assertEqual(task["source_benchmark"]["comment_presence_review"]["status"],
                         "observed_absent")
        result = evaluate_response(task, (self.prefix + self.body).encode())
        self.assertTrue(result["passed"])
        self.assertTrue(result["measured"])
        for status in ("unknown", None, "visible_text"):
            invalid = copy.deepcopy(rule)
            invalid["comment_coverage_review"]["status"] = status
            with self.assertRaises(ValueError):
                compile_case(self.case, invalid, self.root)
        invalid = copy.deepcopy(rule)
        invalid["comment_contract"] = {**comment_labels(),
            "version": 2, "minimum_comments": 1, "record_selector": ".comment",
            "author_selector": ".author", "body_selector": ".body",
            "section_markers": ["Comments"]}
        invalid.pop("comment_coverage_review")
        with self.assertRaisesRegex(ValueError, "presence review"):
            compile_case(self.case, invalid, self.root)
        (self.root / "source.html").write_bytes(source + b"changed")
        with self.assertRaisesRegex(ValueError, "Changed article comment source evidence"):
            compile_case(self.case, rule, self.root)

    def test_placeholder_only_review_cannot_hide_actual_comment_text(self):
        source = b"<div class='comment'>Comment hidden</div>"
        (self.root / "source.html").write_bytes(source)
        reference = {"version": 1, "case_id": self.case["id"], "url": self.case["url"],
                     "scope": "initial_load_only", "context_id": "historical-authoring",
                     "observed_on": "2026-10-19", "observation_complete": True,
                     "source_observation_counts": [1],
                     "records": [{"id": "c1", "author": "Reader", "text": "Comment hidden",
                                  "parent_id": None, "complete_initial_text": True}],
                     "source_evidence": [{"path": "source.html",
                         "sha256": hashlib.sha256(source).hexdigest(),
                         "scope": "complete_initial_page"}]}
        rule = copy.deepcopy(self.rule)
        rule["comment_coverage_review"] = {
            "status": "visible_placeholders_only", "basis": "Only a moderation notice is displayed.",
            "placeholder_texts": ["Comment hidden"], "historical_contract": {
                "version": 1, "record_selector": ".comment", "author_selector": ".author",
                "body_selector": ".body", "section_markers": ["Comments"],
                "max_reference_age_seconds": 900}}
        for text, accepted in [("Comment hidden", True), ("Actual comment prose", False)]:
            reference["records"][0]["text"] = text
            raw = (json.dumps(reference) + "\n").encode()
            (self.root / "comments.json").write_bytes(raw)
            rule["comment_qualification_reference"] = {
                "path": "comments.json", "sha256": hashlib.sha256(raw).hexdigest()}
            if accepted:
                task = compile_case(self.case, rule, self.root)
                self.assertNotIn("comment_contract", task["source_benchmark"])
                for placeholders in ("Comment hidden", [""], [None]):
                    invalid = copy.deepcopy(rule)
                    invalid["comment_coverage_review"]["placeholder_texts"] = placeholders
                    with self.assertRaisesRegex(ValueError, "Placeholder-only"):
                        compile_case(self.case, invalid, self.root)
            else:
                with self.assertRaisesRegex(ValueError, "Placeholder-only"):
                    compile_case(self.case, rule, self.root)

    def test_complete_plain_and_markdown_articles_pass(self):
        validate_case(EvalCase(**self.task))
        self.assertTrue(self.score(self.prefix + self.body))
        self.assertTrue(self.score(self.prefix + "\n\n".join(
            f"## Section {i}\n\n{p}" for i, p in enumerate(self.paragraphs))))

    def test_each_missing_whole_paragraph_fails(self):
        for omitted in range(len(self.paragraphs)):
            with self.subTest(omitted=omitted):
                text = "\n\n".join(p for i, p in enumerate(self.paragraphs) if i != omitted)
                self.assertFalse(self.score(self.prefix + text))

    def test_title_navigation_opening_repetition_and_wrong_article_fail(self):
        for text in (self.prefix, self.prefix + self.paragraphs[0],
                     self.prefix + (self.paragraphs[0] + "\n") * 8,
                     self.prefix + "An unrelated article discusses a different research project."):
            with self.subTest(text=text[:80]):
                self.assertFalse(self.score(text))

    def test_complete_body_cannot_replace_required_identity_or_stable_text(self):
        self.assertFalse(self.score(self.body))
        self.assertFalse(self.score("Comet field report\n" + self.body))

    def test_article_identity_cannot_match_empty_text(self):
        for pattern in ("^", ".*", r"\s*", "(?:Comet field report)?"):
            with self.subTest(pattern=pattern):
                rule = copy.deepcopy(self.rule)
                rule['acceptance_contract']['page_patterns'] = [pattern]
                with self.assertRaisesRegex(ValueError, "cannot match empty"):
                    compile_case(self.case, rule, self.root)
        rule = copy.deepcopy(self.rule)
        rule['acceptance_contract']['page_patterns'] = ["("]
        with self.assertRaisesRegex(ValueError, "Invalid stable acceptance regex"):
            compile_case(self.case, rule, self.root)
        rule = copy.deepcopy(self.rule)
        rule['acceptance_contract']['page_patterns'] = ["Comet(?: field)? report"]
        task = compile_case(self.case, rule, self.root)
        self.assertTrue(evaluate_response(task, (self.prefix + self.body).encode())['passed'])

    def test_invalid_task_fields_fail_during_compilation(self):
        for field, value in (("url", "javascript:alert(1)"), ("url", "/article"),
                             ("id", "../article-001"), ("name", "  ")):
            with self.subTest(field=field, value=value):
                case = {**self.case, field: value}
                rule = {**self.rule, "id": case['id']}
                with self.assertRaises(ValueError):
                    compile_case(case, rule, self.root)
        directory = self.root / "invalid-definitions"
        directory.mkdir()
        (directory / "cohort.json").write_text(json.dumps({
            "schema_version": 1, "case_count": 1,
            "cases": [{**self.case, "url": "javascript:alert(1)"}]}))
        (directory / "rules.json").write_text(json.dumps({
            "schema_version": 1, "rules": [self.rule]}))
        with self.assertRaisesRegex(ValueError, "HTTP"):
            compile_dataset(self.root, directory)

    def test_public_projection_binds_the_entire_reference_without_copying_it(self):
        projected = public_task(self.task)
        encoded = json.dumps(projected)
        for paragraph in self.paragraphs:
            self.assertNotIn(paragraph, encoded)
        self.assertEqual(len(projected['body_requirements']['regions']), 5)
        self.assertEqual(sum(r['source_text_binding']['chars'] for r in
                             projected['body_requirements']['regions']),
                         sum(len(p) for p in self.paragraphs))

    def test_unresolved_scope_changed_bytes_and_escaping_paths_fail(self):
        changes = [
            ("status", "unresolved"),
            ("reference_scope", {**self.rule['reference_scope'], "complete_initial_body": False}),
            ("reference_scope", {**self.rule['reference_scope'], "end": "Invented ending"}),
            ("body_reference", {**self.rule['body_reference'], "sha256": "0" * 64}),
            ("body_reference", {**self.rule['body_reference'], "path": "../body.txt"}),
            ("target_kind", "article_feed"),
            ("body_regions", [[1, 2], [4, 9]]),
            ("body_regions", [[1, 2], [2, 9]]),
            ("body_regions", [[1, 8]]),
            ("body_regions", []),
            ("stable_text_review", []),
            ("stable_text_review", [{**self.rule['stable_text_review'][0], "id": "other"}]),
            ("stable_text_review", [{**self.rule['stable_text_review'][0], "basis": ""}]),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                rule = copy.deepcopy(self.rule)
                rule[key] = value
                with self.assertRaises(ValueError):
                    compile_case(self.case, rule, self.root)

    def test_body_region_requires_scorable_text(self):
        raw = b"Tiny\n"
        (self.root / "body.txt").write_bytes(raw)
        rule = copy.deepcopy(self.rule)
        rule.update(body_reference={"path": "body.txt",
                                    "sha256": hashlib.sha256(raw).hexdigest(), "chars": 5},
                    reference_scope={"complete_initial_body": True,
                                     "start": "Tiny", "end": "Tiny"},
                    body_regions=[[1, 1]])
        with self.assertRaisesRegex(ValueError, "empty coverage region"):
            compile_case(self.case, rule, self.root)

    def test_collection_cannot_freeze_current_feed_as_fixed_article(self):
        with self.assertRaisesRegex(ValueError, "separate record review"):
            compile_case({**self.case, "page_type": "collection"}, self.rule, self.root)

    def test_post_collection_uses_fixed_coverage_with_independent_qualification(self):
        case = {**self.case, "url": "https://example.test/feed", "page_type": "collection"}
        contract = {**post_labels(),
            "version": 2, "hosts": ["example.test"], "entry_path": r"/research/[a-z]+",
            "html_record_selector": "article", "html_permalink_selector": "a.permalink",
            "html_author_selector": ".author", "html_body_selector": ".post-body",
            "html_quote_selector": "blockquote", "quote_markers": ["Quote"],
            "minimum_posts": 2,
        }
        reference = {
            "version": 1, "case_id": case['id'], "url": case['url'],
            "scope": "initial_load_only", "context_id": "independent-control-1",
            "observed_at": "2026-10-19T00:00:00Z", "source_observation_counts": [2, 2],
            "records": [{"url": f"https://example.test/research/{name}", "author": author,
                         "body_parts": [{"kind": "post", "text": body,
                                         "complete_initial_text": True}]}
                        for name, author, body in [
                            ("one", "Dr Rivera", self.paragraphs[0]),
                            ("two", "Dr Chen", self.paragraphs[2])]],
        }
        raw = json.dumps(reference).encode()
        (self.root / 'qualification.json').write_bytes(raw)
        rule = {key: value for key, value in self.rule.items()
                if key not in {'body_reference', 'reference_scope', 'body_regions'}}
        rule.update(target_kind='post_collection', post_contract=contract,
                    qualification_reference={'path': 'qualification.json',
                                             'sha256': hashlib.sha256(raw).hexdigest()})
        source = self.root / 'independent-source.txt'
        source.write_text('Two independently displayed posts with visible author and text.')
        rule['post_coverage_review'] = {'source_evidence': [
            {'path': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}
        task = compile_case(case, rule, self.root)
        validate_case(EvalCase(**task))
        projected = public_task(task)
        self.assertEqual(projected['source_benchmark']['post_contract'], contract)
        for record in reference['records']:
            self.assertNotIn(record['url'], json.dumps(projected))
            self.assertNotIn(record['body_parts'][0]['text'], json.dumps(projected))
        self.assertNotIn('qualification_proof', projected['source_benchmark'])
        refs = self.root / 'list-references'
        refs.mkdir()
        self.assertIsNone(load_list_reference(task, self.root))
        (refs / f"{case['id']}.json").write_bytes(raw)
        self.assertIsNone(load_list_reference(task, self.root))

        def response(records):
            text = '<h1>Comet field report</h1><nav>Article archive</nav>'
            for record in records:
                text += (f'<article><a class="permalink" href="{record["url"]}">Post</a>'
                         f'<header class="author">{record["author"]}</header>'
                         f'<div class="post-body">{html.escape(record["body_parts"][0]["text"])}</div>'
                         '</article>')
            return text.encode()

        original = response(reference['records'])
        complete = evaluate_response(task, original)
        self.assertTrue(complete['passed'])
        self.assertTrue(complete['measured'])
        insufficient = evaluate_response(task, response(reference['records'][:1]))
        self.assertFalse(insufficient['passed'])
        self.assertTrue(insufficient['measured'])
        updated = copy.deepcopy(reference['records'])
        for row in updated:
            row['body_parts'][0]['text'] = 'New visible discussion text.'
        self.assertTrue(evaluate_response(task, response(updated))['passed'])

        rescored = score_attempt(self.task, (self.prefix + self.body).encode(), ok=True,
                                 previous_metrics=complete['metrics'])
        self.assertNotIn('article_reference_status', rescored)
        self.assertNotIn('article_posts_pass', rescored)

        static = copy.deepcopy(rule)
        static['post_contract'] = {key: value for key, value in contract.items()
                                   if key not in {'required_ratio', 'max_reference_age_seconds'}}
        static['post_contract'].update(version=2, minimum_posts=2,
                                       allow_empty_outer_with_quotes=True)
        source = self.root / 'independent-source.txt'
        source.write_text('Two independently displayed posts with visible author and text.')
        static['post_coverage_review'] = {'source_evidence': [
            {'path': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}
        static_task = compile_case(case, static, self.root)
        self.assertEqual(static_task['source_benchmark']['post_contract']['minimum_posts'], 2)
        static['post_contract']['minimum_posts'] = 3
        with self.assertRaises(ValueError):
            compile_case(case, static, self.root)
        static['post_contract']['minimum_posts'] = 2
        source.write_text('Changed capture')
        with self.assertRaisesRegex(ValueError, 'Changed article collection source'):
            compile_case(case, static, self.root)
        static['post_coverage_review']['source_evidence'] = []
        with self.assertRaisesRegex(ValueError, 'requires source evidence'):
            compile_case(case, static, self.root)

        source.write_text('Two independently displayed posts with visible author and text.')
        incomplete = copy.deepcopy(reference)
        incomplete['records'][0]['body_parts'][0]['complete_initial_text'] = False
        raw = json.dumps(incomplete).encode()
        (self.root / 'qualification.json').write_bytes(raw)
        rule['qualification_reference']['sha256'] = hashlib.sha256(raw).hexdigest()
        with self.assertRaisesRegex(ValueError, 'complete owned post'):
            compile_case(case, rule, self.root)

    def test_whole_population_cannot_silently_drop_or_ignore_pending_cases(self):
        directory = self.root / "definitions"
        directory.mkdir()
        second = {**self.case, "id": "article-002"}
        (directory / "cohort.json").write_text(json.dumps({
            "schema_version": 1, "case_count": 2, "cases": [self.case, second]}))
        path = directory / "rules.json"
        path.write_text(json.dumps({"schema_version": 1, "rules": [self.rule]}))
        with self.assertRaisesRegex(ValueError, "inventory"):
            compile_dataset(self.root, directory)
        path.write_text(json.dumps({"schema_version": 1, "rules": [self.rule, {
            "id": second['id'], "status": "unresolved"}]}))
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            compile_dataset(self.root, directory)

        # Resolving the same approved inventory produces a bound suite; it does
        # not require dropping a difficult case or rewriting an existing suite.
        second_rule = {**self.rule, "id": second['id']}
        path.write_text(json.dumps({"schema_version": 1,
                                    "rules": [self.rule, second_rule]}))
        dataset = compile_dataset(self.root, directory)
        self.assertEqual(dataset['case_count'], 2)
        self.assertEqual(dataset['site_count'], 1)
        self.assertEqual(dataset['source_bindings']['rules_sha256'],
                         hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual([case['id'] for case in dataset['cases']],
                         [self.case['id'], second['id']])

    def test_approved_inventory_keeps_all_cases_and_disclosed_removals(self):
        cohort = json.loads((DEFINITIONS / "cohort.json").read_text())
        rules = json.loads((DEFINITIONS / "rules.json").read_text())['rules']
        self.assertEqual(cohort['case_count'], 100)
        self.assertEqual([c['id'] for c in cohort['cases']], [r['id'] for r in rules])
        self.assertEqual([c['id'] for c in cohort['cases']], [
            f'article-{number:03}' for number in range(1, 108)
            if number not in {4, 42, 43, 62, 83, 97, 105}])
        self.assertEqual(cohort['provenance']['removed_candidate_ids'],
                         ['article-candidate-043', 'article-candidate-105'])
        revision = next(r for r in cohort['revisions'] if r['revision'] == 3)
        self.assertEqual({change['id'] for change in revision['changes']},
                         {'article-042', 'article-062', 'article-083', 'article-097'})
        self.assertTrue(all(change['action'] == 'remove'
                            and change['previous']['id'] == change['id']
                            for change in revision['changes']))
        self.assertTrue(all(c['platform'] != '搜狗微信' for c in cohort['cases']))
        final = next(r for r in cohort['revisions'] if r['revision'] == 4)
        self.assertEqual(cohort['revisions'][-1]['revision'], 14)
        self.assertEqual({c['id'] for c in final['changes'] if c['action'] == 'replace'},
                         {'article-001', 'article-002', 'article-003', 'article-079', 'article-082'})
        self.assertEqual([c['id'] for c in final['changes'] if c['action'] == 'remove'],
                         ['article-004'])
        self.assertTrue(all(rule['status'] == 'verified' for rule in rules))
        self.assertEqual(sum(rule['target_kind'] == 'post_collection' for rule in rules), 5)
        fixed = [r for r in rules if r['target_kind'] == 'fixed_article']
        self.assertEqual(cohort['id'], 'articles-v11')
        self.assertEqual(len(fixed), 95)
        self.assertEqual(sum('title_contract' in r for r in fixed), 90)
        self.assertEqual(sum('comment_contract' in r for r in fixed), 20)
        self.assertTrue(all(r['comment_contract']['version'] == 2
                            for r in fixed if 'comment_contract' in r))
        self.assertTrue(all('comment_qualification_reference' in r
                            and 'comment_coverage_review' in r for r in fixed))
        self.assertTrue(all(r['body_segmentation']['version'] == 3 for r in fixed))
        self.assertTrue(all(r['body_segmentation']['reference_sha256'] == r['body_reference']['sha256']
                            for r in fixed))
        for rule in fixed:
            self.assertEqual([r['lines'] for r in rule['body_region_review']], rule['body_regions'])
            self.assertTrue(all(r['label'].strip() for r in rule['body_region_review']))
        self.assertTrue(all(1 <= len(r['body_regions']) <= 5 for r in fixed))
        self.assertEqual(len(next(r for r in cohort['revisions'] if r['revision'] == 6)['changes']), 57)
        revised = {c['id']: c['action'] for c in next(r for r in cohort['revisions'] if r['revision'] == 5)['changes']}
        self.assertEqual(revised, {'article-049': 'revise_entry_url',
                                   'article-090': 'revise_source_reference',
                                   'article-061': 'replace',
                                   'article-038': 'require_all_initial_posts',
                                   'article-040': 'require_all_initial_posts',
                                   'article-041': 'require_all_initial_posts'})
        posts = [r for r in rules if r['target_kind'] == 'post_collection']
        self.assertTrue(all(r['post_contract']['version'] == 2 for r in posts))
        self.assertEqual([r['post_contract']['minimum_posts'] for r in posts], [4, 8, 4, 4, 16])
        self.assertTrue(all(r['post_coverage_review']['source_evidence'] for r in posts))

    def test_published_contracts_match_authoring_rules(self):
        rules = json.loads((DEFINITIONS / "rules.json").read_text())["rules"]
        tasks = json.loads((DEFINITIONS / "tasks.json").read_text())["cases"]
        by_id = {task["id"]: task for task in tasks}
        self.assertEqual(len(by_id), len(tasks), "Duplicate public task IDs")
        self.assertEqual(len({rule["id"] for rule in rules}), len(rules))
        self.assertEqual({rule["id"] for rule in rules}, set(by_id))
        for rule in rules:
            task = by_id[rule["id"]]
            with self.subTest(case=rule["id"]):
                self.assertEqual(rule["acceptance_contract"], task["acceptance_contract"])
                for component in ("title_contract", "comment_contract", "post_contract"):
                    self.assertEqual(rule.get(component),
                                     task.get("source_benchmark", {}).get(component))

    def test_published_tasks_bind_current_authoring_bytes(self):
        tasks = json.loads((DEFINITIONS / "tasks.json").read_text())
        for name in ("cohort", "rules"):
            with self.subTest(source=name):
                self.assertEqual(tasks["source_bindings"][name + "_sha256"],
                                 hashlib.sha256((DEFINITIONS / (name + ".json")).read_bytes()).hexdigest())

    def test_definition_hashes_bind_the_parsed_snapshot(self):
        directory = self.root / "snapshot-definitions"
        directory.mkdir()
        cohort = {"schema_version": 1, "case_count": 1, "cases": [self.case]}
        rules = {"schema_version": 1, "rules": [self.rule]}
        original = {"cohort.json": json.dumps(cohort).encode(),
                    "rules.json": json.dumps(rules).encode()}
        for name, raw in original.items():
            (directory / name).write_bytes(raw)

        def replace_sources_then_compile(case, rule, evidence_home):
            (directory / "cohort.json").write_text(json.dumps({**cohort, "case_count": 0}))
            (directory / "rules.json").write_text(json.dumps({
                "schema_version": 1,
                "rules": [{**self.rule, "status": "unresolved"}]}))
            return compile_case(case, rule, evidence_home)

        with patch("lexbench_webfetch.article_dataset.compile_case",
                   side_effect=replace_sources_then_compile):
            dataset = compile_dataset(self.root, directory)
        self.assertEqual(dataset['case_count'], 1)
        for name, raw in original.items():
            key = name.replace('.json', '_sha256')
            self.assertEqual(dataset['source_bindings'][key], hashlib.sha256(raw).hexdigest())
            self.assertNotEqual(dataset['source_bindings'][key], hashlib.sha256(
                (directory / name).read_bytes()).hexdigest())
