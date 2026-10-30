"""Current corrections remain executable beside immutable historical controls."""
import hashlib
import json
from importlib.resources import files

from lexbench_webfetch.scoring_regressions import check_scoring_regressions, load_scoring_regressions


def test_current_corrections_run_through_the_public_scorer_and_task_predicate():
    controls = json.loads(files('harness.benchmarks').joinpath('scoring-regressions.json').read_bytes())['cases']
    identifiers = [row['id'] for row in controls]
    assert len(identifiers) == len(set(identifiers))
    assert {'false-positive', 'false-negative'} == {row['failure_mode'] for row in controls}
    assert check_scoring_regressions(identifiers) == identifiers


def test_consolidated_code_matches_its_migration_and_correction_bindings():
    receipt = json.loads(files('harness.benchmarks').joinpath('code-migration.json').read_bytes())
    controls = load_scoring_regressions()
    for track, source in receipt['sources'].items():
        for name, change in source['changes'].items():
            actual = files('lexbench_' + track).joinpath(name).read_bytes()
            assert hashlib.sha256(actual).hexdigest() == change['after_sha256'], name
            assert len(change['before_sha256']) == 64
        for correction in source.get('corrections', []):
            assert set(correction['modules']) <= source['changes'].keys()
            assert set(correction.get('regression_ids', [])) <= controls.keys()
