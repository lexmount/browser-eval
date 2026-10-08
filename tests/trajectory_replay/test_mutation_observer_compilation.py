"""Execute compiled observations against an offline same-origin DOM specimen."""

import json
import subprocess
from pathlib import Path

import pytest

from harness.trajectory_replay.webarena_plan import compile_mutation_observer

CANDIDATES = (
    Path(__file__).resolve().parents[2]
    / 'evaluation/trajectory-replay/webarena-verified/mutation-completion-candidates.json'
)
NODE_SPECIMEN = r'''
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const origin = 'https://shop.example.test';
const requests = [];
const link = (textContent, href) => ({textContent, getAttribute: key => {
  if (key !== 'href') throw new Error('Unexpected attribute');
  return href;
}});
const params = input.observer;
const item = {querySelectorAll: selector => {
  if (selector !== 'a[href]') throw new Error('Unexpected item selector');
  return [link(params.product_name, '/product/42'), link('Edit', '/EDIT/42/')];
}};
class DOMParser {
  parseFromString() {return {querySelectorAll: selector => {
    if (selector === 'a[href]')
      return [link('Sign Out', params.require_authenticated_sign_out_path)];
    if (selector !== params.within) throw new Error('Selector was changed: ' + selector);
    return [item];
  }}};
}
const context = {URL, DOMParser, setTimeout, location: {origin},
  fetch: async (path, options) => {
    requests.push({path, options});
    if (path !== params.path) throw new Error('Request path was changed: ' + path);
    return {status: 200, redirected: false, url: new URL(path, origin).href,
      text: async () => '<offline specimen>'};
  }};
(async () => {
  const result = await vm.runInNewContext(input.script, context);
  process.stdout.write(JSON.stringify({result, requests}));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''


@pytest.mark.parametrize('name', ['Normal item', 'TIMEOUT PATH WITHIN PRODUCT EDIT SIGNOUT',
                                  'Quoted "item" with \\ and 中文'])
def test_compiled_observer_preserves_literal_values_and_reads_same_origin(tmp_path, name):
    data = json.loads(CANDIDATES.read_text())
    rule = next(row for row in data['rules'] if row['task_id'] == 465)
    observer = rule['observer']
    observer.update({'path': '/WITHIN/PATH', 'within': '.PRODUCT', 'product_name': name,
                     'edit_href_path_pattern': '^/EDIT/[0-9]+/$',
                     'require_authenticated_sign_out_path': '/SIGNOUT'})
    rule['observer_timeout_ms'] = 1000
    path = tmp_path / 'candidates.json'
    path.write_text(json.dumps(data))
    _, script = compile_mutation_observer(path, 465, 8)
    result = subprocess.run(['node', '-e', NODE_SPECIMEN],
                            input=json.dumps({'script': script, 'observer': observer}),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['result']['completion'] == 'persisted_wishlist_item_observed'
    assert output['result']['matchingItems'] == 1
    assert output['requests'] == [{
        'path': observer['path'],
        'options': {'method': 'GET', 'credentials': 'same-origin',
                    'redirect': 'error', 'cache': 'no-store'},
    }]


@pytest.mark.parametrize('module, method', [('Customer/data', 'ready'),
                                           ('MODULE/METHOD/TIMEOUT', 'TIMEOUT')])
def test_readiness_preserves_module_and_method_literals(tmp_path, module, method):
    from harness.trajectory_replay.webarena_plan import compile_mutation_readiness

    data = json.loads(CANDIDATES.read_text())
    rule = next(row for row in data['rules'] if row['task_id'] == 465)
    rule['pre_action_readiness'].update({'module': module, 'deferred_method': method})
    path = tmp_path / 'candidates.json'
    path.write_text(json.dumps(data))
    _, script = compile_mutation_readiness(path, 465, 8)
    specimen = r'''
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const deferred = {state: () => 'resolved', done: callback => {
  callback(); return deferred;
}, fail: () => deferred};
const component = {[input.method]: () => deferred};
const context = {setTimeout: (callback, ms) => setTimeout(callback, ms).unref(),
  requirejs: {defined: name => name === input.module},
  require: (names, resolve) => {
    if (JSON.stringify(names) !== JSON.stringify([input.module]))
      throw new Error('Module name was changed');
    resolve(component);
  }};
context.window = context;
(async () => {
  const result = await vm.runInNewContext(input.script, context)();
  process.stdout.write(JSON.stringify(result));
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run(['node', '-e', specimen],
                            input=json.dumps({'script': script, 'module': module,
                                              'method': method}),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['ready'] is True and output['after']['state'] == 'resolved'
    assert output['module'] == module and output['method'] == method


@pytest.mark.parametrize('path_value', ['//other.test/path', '/\\other.test/path',
                                       '/safe\npath', '/safe\tpath', '/safe#fragment'])
def test_compiler_rejects_paths_that_do_not_preserve_same_origin(tmp_path, path_value):
    data = json.loads(CANDIDATES.read_text())
    rule = next(row for row in data['rules'] if row['task_id'] == 465)
    rule['observer']['path'] = path_value
    path = tmp_path / 'candidates.json'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='invalid path'):
        compile_mutation_observer(path, 465, 8)
