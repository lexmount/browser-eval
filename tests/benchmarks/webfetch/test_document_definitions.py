"""Definition contracts require explicit signatures and local descriptions."""
import copy
import unittest

from lexbench_webfetch.document_structure import evaluate_document_structure, validate_document_structure


class DefinitionStructureTests(unittest.TestCase):
    def setUp(self):
        self.contract = {
            'version': 1, 'type': 'definition', 'minimum_heading_coverage': 1,
            'minimum_body_chars': 20,
            'headings': [
                {'id': 'join', 'text': 'os.path.join(path, *paths)', 'level': 1, 'body_required': True},
                {'id': 'split', 'text': 'os.path.split(path)', 'level': 1, 'body_required': True},
            ],
        }
        self.html = '<main><dl><dt>os.path.join(path, *paths)</dt><dd>Join pathname components together.</dd><dt>os.path.split(path)</dt><dd>Split a pathname into two parts.</dd></dl></main>'

    def result(self, text, html=True):
        return evaluate_document_structure(text, self.contract, html_document=html)

    def test_html_definitions(self):
        self.assertTrue(self.result(self.html)['document_structure_pass'])

    def test_plain_and_markdown_signatures(self):
        for prefix, suffix in [('', ''), ('`', '`'), ('### `', '`')]:
            with self.subTest(prefix=prefix):
                value = prefix + 'os.path.join(path, *paths)' + suffix + '\nJoin pathname components together.\n' + prefix + 'os.path.split(path)' + suffix + '\nSplit a pathname into two parts.'
                self.assertTrue(self.result(value, False)['document_structure_pass'])

    def test_names_and_padding_rejected(self):
        for value in ['os.path.join\nos.path.split\n' + 'Padding ' * 100,
                      'os.path.join(path, *paths)\nos.path.split(path)\n' + 'Padding ' * 100,
                      'We describe os.path.join(path, *paths) here.\nWe describe os.path.split(path) here.\n' + 'Padding ' * 100]:
            with self.subTest(value=value[:60]):
                self.assertFalse(self.result(value, False)['document_structure_pass'])

    def test_empty_dd_and_external_padding_rejected(self):
        value = '<main><p>' + 'Padding ' * 100 + '</p><dl><dt>os.path.join(path, *paths)</dt><dd></dd><dt>os.path.split(path)</dt><dd></dd></dl></main>'
        result = self.result(value)
        self.assertFalse(result['document_structure_pass'])
        self.assertEqual(result['document_body_chars'], 0)

    def test_only_immediately_following_dd_counts(self):
        self.assertFalse(self.result(self.html.replace('<dd>', '<p>unrelated</p><dd>'))['document_structure_pass'])

    def test_navigation_and_hidden_descriptions_rejected(self):
        for value in [self.html.replace('<main>', '<main><nav>').replace('</main>', '</nav></main>'),
                      self.html.replace('<dd>', '<dd style="display:none">')]:
            with self.subTest(value=value[:70]):
                self.assertFalse(self.result(value)['document_structure_pass'])

    def test_aria_hidden_is_visually_visible(self):
        self.assertTrue(self.result(self.html.replace('<dd>', '<dd aria-hidden="true">'))['document_structure_pass'])

    def test_signature_punctuation_is_required(self):
        self.assertFalse(self.result(self.html.replace('(path)', 'path'))['document_structure_pass'])

    def test_code_fence_cannot_declare_signatures(self):
        value = '```\nos.path.join(path, *paths)\nA description\nos.path.split(path)\nA description\n```'
        self.assertFalse(self.result(value, False)['document_structure_pass'])

    def test_contract_must_explicitly_require_descriptions(self):
        for field, value in [('body_required', False), ('level', 2)]:
            contract = copy.deepcopy(self.contract)
            contract['headings'][0][field] = value
            with self.assertRaises(ValueError):
                validate_document_structure(contract)

    def test_undeclared_definition_mode_does_not_promote_terms(self):
        del self.contract['type']
        self.assertFalse(self.result(self.html)['document_structure_pass'])

    def test_undeclared_signature_cannot_supply_previous_description(self):
        for signature in ['os.path.other(path)', '`os.path.other(path)`', '### `os.path.other(path)`',
                          'os.path.other(path) -> str', '`os.path.other(path) -> str`']:
            with self.subTest(signature=signature):
                text = ('os.path.join(path, *paths)\n' + signature
                        + '\nDescription of other.\nos.path.split(path)\nDescription of split.')
                self.assertFalse(self.result(text, False)['document_structure_pass'])
                text = text.replace('os.path.join(path, *paths)\n',
                                    'os.path.join(path, *paths)\nJoin pathname components.\n')
                self.assertTrue(self.result(text, False)['document_structure_pass'])

    def test_nested_parameter_list_is_local_description(self):
        contract = copy.deepcopy(self.contract)
        contract['headings'] = contract['headings'][:1]
        description = '<dl><dt>path</dt><dd>The initial pathname component to join.</dd></dl>'
        value = '<dl><dt>os.path.join(path, *paths)</dt><dd>' + description + '</dd></dl>'
        self.assertTrue(evaluate_document_structure(value, contract, html_document=True)['document_structure_pass'])
        outside = '<dl><dt>os.path.join(path, *paths)</dt><dd></dd></dl>' + description
        self.assertFalse(evaluate_document_structure(outside, contract, html_document=True)['document_structure_pass'])

    def test_distinct_signature_punctuation_is_not_duplicate(self):
        for names in [('foo_bar()', 'foobar()'), ('foo(x=1)', 'foo(x=-1)')]:
            with self.subTest(names=names):
                contract = copy.deepcopy(self.contract)
                for heading, name in zip(contract['headings'], names):
                    heading['text'] = name
                text = '\n'.join(name + '\nA substantial local description.' for name in names)
                self.assertTrue(evaluate_document_structure(text, contract)['document_structure_pass'])
                contract['headings'][1]['text'] = names[0]
                with self.assertRaises(ValueError):
                    validate_document_structure(contract)
