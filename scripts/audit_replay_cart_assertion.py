"""Compare frozen and candidate cart assertions on isolated DOM counterexamples.

Executes only the cart assertion, never navigation or checkout. Network is blocked.
Outputs are diagnostic evidence, not WebArena task scores.
"""

import argparse
import hashlib
import html
import json
from pathlib import Path

from playwright.sync_api import Error, sync_playwright


def cart_html(quantity="1", *, rows=1, fields=1, name):
    field = f'<input name="cart[1][qty]" value="{html.escape(quantity, quote=True)}">'
    row = '<tbody class="cart item"><tr><td><strong class="product-item-name">' + html.escape(name) + '</strong>' + field * fields + '</td></tr></tbody>'
    return '<!doctype html><table id="shopping-cart-table">' + row * rows + '</table>'


def bound_scripts(dataset, candidate):
    payload = json.loads(dataset.read_text())
    revised = json.loads(candidate.read_text())
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != revised['repair']['predecessor_dataset_sha256']:
        raise ValueError('candidate predecessor changed')
    matches = [row for row in payload['trajectories'] if row['task_id'] == revised['task_id']]
    if len(matches) != 1:
        raise ValueError('candidate task is missing or duplicated')
    before, after = matches[0]['tool_calls'], revised['tool_calls']
    index = revised['repair']['changed_step'] - 1
    if len(before) != len(after) or [i for i in range(len(before)) if before[i] != after[i]] != [index]:
        raise ValueError('candidate must change only its declared assertion')
    if before[index]['action'] != 'evaluate' or after[index]['action'] != 'evaluate':
        raise ValueError('declared correction is not an evaluate assertion')
    return before[index]['script'], after[index]['script']


def audit(dataset, candidate, browser_binary, *, product):
    scripts = bound_scripts(dataset, candidate)
    cases = [('quantity-one', {}, True), ('quantity-two', {'quantity': '2'}, False),
             ('quantity-zero', {'quantity': '0'}, False), ('quantity-empty', {'quantity': ''}, False),
             ('quantity-invalid', {'quantity': 'NaN'}, False), ('quantity-missing', {'fields': 0}, False),
             ('quantity-duplicated', {'fields': 2}, False), ('two-product-rows', {'rows': 2}, False),
             ('wrong-product', {'name': 'Different product'}, False)]
    observations = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(browser_binary), headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            for identity, values, expected in cases:
                outcomes = {}
                for label, script in zip(('frozen', 'candidate'), scripts):
                    page.set_content(cart_html(**{"name": product, **values}))
                    try:
                        result = page.evaluate(script)
                        outcomes[label] = {'passed': True, 'result': result}
                    except Error as exc:
                        outcomes[label] = {'passed': False, 'error': str(exc).splitlines()[0]}
                if outcomes['candidate']['passed'] != expected:
                    raise AssertionError(f'candidate violates quantity contract: {identity}')
                observations.append({'control': identity, 'expected_acceptance': expected, **outcomes})
        finally:
            browser.close()
    return {'schema': 'lex_replay_cart_assertion_controls/1', 'scope': 'isolated_dom_not_task_scores',
            'network_allowed': False, 'task_id': json.loads(candidate.read_text())['task_id'],
            'dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest(),
            'candidate_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
            'observations': observations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--product', required=True, help='Expected product name from the task contract')
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = audit(args.dataset, args.candidate, args.browser, product=args.product)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Diagnostic calls have explicit output identities; never overwrite one.
    with args.output.open('x') as stream:
        json.dump(output, stream, indent=2)
    print(json.dumps({'output': str(args.output), 'controls': len(output['observations'])}))


if __name__ == '__main__':
    main()
