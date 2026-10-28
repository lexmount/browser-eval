"""Optional components must participate in the shared verdict and rescore path."""
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.benchmark import public_task


def test_arbitrary_required_component_cannot_be_bypassed_by_stable_pass():
    metrics = {'stable_task_pass': True, 'content_pass': True, 'length_pass': True,
               'required_components': {'future_component': False}}
    assert not task_passed(True, metrics)
    metrics['required_components']['future_component'] = True
    assert task_passed(True, metrics)
    assert not task_passed(False, metrics)


def test_component_failure_is_removed_when_rescoring_unrelated_task():
    task = {'id': 'synthetic', 'url': 'https://example.test/article',
            'min_content_chars': 1, 'expected_any': []}
    result = score_attempt(task, b'Ordinary visible body text.', ok=True,
                           previous_metrics={'required_components': {'retired': False},
                                             'article_comments_pass': False})
    assert 'required_components' not in result
    assert 'article_comments_pass' not in result
    assert task_passed(True, result)


def test_registered_component_contracts_are_public_without_private_evidence():
    from lexbench_webfetch.required_components import COMPONENTS
    benchmark = {key: {'version': 2} for key in COMPONENTS}
    benchmark['comment_qualification_proof'] = {'path': 'private-capture.json'}
    task = public_task({'id': 'synthetic', 'source_benchmark': benchmark})
    assert set(task['source_benchmark']) == set(COMPONENTS)
