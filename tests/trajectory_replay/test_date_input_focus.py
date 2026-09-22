"""Date-picker callbacks must settle before keyboard-based ref filling."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path('evaluation/trajectory-replay/webarena-verified/trajectory-candidates')


def calls(task_id):
    return json.loads((ROOT / f'task-{task_id}.json').read_text())['tool_calls']


def run(script, setup):
    return subprocess.run(
        ['node', '-e', setup + '\nPromise.resolve().then(()=>(' + script + '))'
         + '.then(x=>console.log(JSON.stringify(x)))'
         + '.catch(e=>{console.error(e.message);process.exitCode=1})'],
        capture_output=True, text=True, timeout=5,
    )


@pytest.mark.parametrize('state', ['ready', 'stolen_once', 'stolen_forever', 'disabled'])
def test_date_focus_preparation_reacquires_stolen_focus_and_is_bounded(state):
    script = next(c['script'] for c in calls(590)
                  if 'focus did not settle' in c.get('script', ''))
    setup = """
let clock=0, ticks=0, focuses=0;
global.performance={now:()=>clock};
const other={};
const field={disabled:STATE==='disabled', focus(){focuses++;document.activeElement=this;}};
global.document={querySelector:()=>field,activeElement:other};
global.setTimeout=fn=>{clock+=10;ticks++;
 if((STATE==='stolen_once'&&ticks===2)||STATE==='stolen_forever')document.activeElement=other;
 queueMicrotask(fn);};
""".replace('STATE', json.dumps(state))
    result = run(script, setup)
    if state in {'ready', 'stolen_once'}:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert ('unavailable' if state == 'disabled' else 'did not settle') in result.stderr


@pytest.mark.parametrize('correct', [True, False])
def test_post_fill_guard_rejects_lost_value(correct):
    script = next(c['script'] for c in calls(590)
                  if 'did not retain its value and focus' in c.get('script', ''))
    setup = """
let clock=0;global.performance={now:()=>clock};
const field={value:VALUE};
global.document={querySelector:()=>field,activeElement:field};
global.setTimeout=fn=>{clock+=10;queueMicrotask(fn);};
""".replace('VALUE', json.dumps('2023-01-16' if correct else '2023-01-30'))
    result = run(script, setup)
    assert (result.returncode == 0) is correct


@pytest.mark.parametrize('task_id', [590, 658])
@pytest.mark.parametrize('correct', [True, False])
def test_date_fields_are_checked_before_submit(task_id, correct):
    rows = calls(task_id)
    index = next(i for i, c in enumerate(rows) if c.get('action') == 'click'
                 and 'Create ' in c.get('selector', ''))
    script = rows[index - 1]['script']
    expected = {'milestone[start_date]': '2023-01-16', 'milestone[due_date]': '2023-01-30'}
    if task_id == 658:
        expected = {'issue[due_date]': '2030-12-31'}
    if not correct:
        expected[next(iter(expected))] = ''
    setup = ('const values=' + json.dumps(expected)
             + ';global.document={querySelector:s=>({value:values[s.split("\\\"")[1]]})};')
    result = run(script, setup)
    assert (result.returncode == 0) is correct, result.stderr
    for i, call in enumerate(rows):
        if call.get('action') == 'fill' and '_date]' in call.get('selector', ''):
            assert call['replay_mode'] == 'snapshot_ref_semantics'
            assert 'focus did not settle' in rows[i - 1]['script']
            assert 'did not retain its value and focus' in rows[i + 1]['script']
