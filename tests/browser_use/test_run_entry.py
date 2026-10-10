import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('environment_model,arguments,expected', [
    (None, ['--model', 'model-a'], 'model-a'),
    ('model-b', [], 'model-b'),
    ('model-b', ['--model=model-a'], 'model-a'),
    (None, ['--help'], None),
    (None, [], None),
])
def test_wrapper_honors_cli_and_caller_directory(tmp_path, environment_model, arguments, expected):
    binary = tmp_path / 'uv'
    log = tmp_path / 'call.json'
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "$CALL_LOG"\n')
    binary.chmod(0o755)
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'CALL_LOG': str(log), 'BROWSER_EVAL_DATA_HOME': str(tmp_path / 'data')}
    env.pop('BROWSER_USE_MODEL', None)
    if environment_model:
        env['BROWSER_USE_MODEL'] = environment_model
    result = subprocess.run(['bash', str(ROOT / 'examples/browser_use/run.sh'),
                             *arguments, '--output', 'relative-evidence'],
                            cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines[0] == str(tmp_path)
    assert lines[-2:] == ['--output', 'relative-evidence']
    # Exercise the actual runner parser without starting sites or provider calls.
    from unittest.mock import patch

    from harness.browser_use import runner
    args = lines[lines.index('harness.browser_use.runner') + 1:]
    with patch('sys.argv', ['runner', *args]):
        if expected is None:
            with pytest.raises(SystemExit) as stopped:
                runner.parse_args()
            assert stopped.value.code == (0 if '--help' in arguments else 2)
        else:
            parsed = runner.parse_args()
            assert parsed.model == expected
            assert parsed.output == 'relative-evidence'
    assert not result.stdout.strip()


def test_cli_prints_actual_completed_evidence_directory(capsys):
    from unittest.mock import patch

    from harness.browser_use import runner
    with (patch.object(runner, 'parse_args'),
          patch.object(runner, 'run', return_value=Path('/evidence'))):
        runner.main()
    assert capsys.readouterr().out.strip() == '/evidence'
