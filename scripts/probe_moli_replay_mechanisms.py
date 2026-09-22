"""Probe document, keyboard and layout behavior without WebArena or a scorer.

Run with uv run --frozen python scripts/probe_moli_replay_mechanisms.py
--binary /path/to/moli [--binary /path/to/another/moli]. Outputs and browser
logs are retained in a fresh OS temporary directory, never in the repository.
Only processes and profiles created by this probe are stopped or removed.
"""

import json
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from playwright.sync_api import sync_playwright

from harness.trajectory_replay.webarena_replay import _execute_replay_call
from harness.browser_use.har import CdpHarRecorder


class Fixture(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.requests.append({"method": "GET", "path": self.path, "body": "",
                              "referer": self.headers.get('Referer'),
                              "cookie": self.headers.get('Cookie')})
        if self.path == '/data.json':
            content_type = 'application/json'
            body = '{"message":"中文 café <b>literal</b>"}'
        elif self.path == '/redirect-form':
            content_type = 'text/html; charset=utf-8'
            body = '<!doctype html><form method="post" action="/redirect-save"><input name="value" value="probe"><button>Save</button></form>'
        elif self.path == '/redirect-final':
            content_type = 'text/html; charset=utf-8'
            body = '<!doctype html><p>Saved</p>'
        elif self.path == '/disabled-control':
            content_type = 'text/html; charset=utf-8'
            body = '''<!doctype html><button id="action" disabled>Act</button>
            <script>window.clicks=0;document.querySelector('button').addEventListener('click',()=>window.clicks++)</script>'''
        elif self.path.startswith('/header-data'):
            content_type = 'application/json'
            body = '{"ok":true}'
        elif self.path == '/geometry':
            content_type = 'text/html; charset=utf-8'
            body = '''<!doctype html><style id="rules">html,body{margin:0;padding:0}#root{width:200px;height:120px}#split{display:inline}#one{display:flex;width:50px;height:20px}#two{display:block;width:80px;height:30px}#hidden{display:none}</style><div id="root"><a id="split"><div id="one"></div><div id="two"></div></a></div><button id="hidden">Suggestion</button>'''
        else:
            content_type = 'text/html; charset=utf-8'
            body = '''<!doctype html><form action="/submitted"><input name="q" id="q"><button type="submit">Search</button></form>
            <script>window.submits=0; document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.submits++})</script>'''
        payload = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
        self.requests.append({"method": "POST", "path": self.path, "body": body})
        self.send_response(302)
        self.send_header('Location', '/redirect-final')
        self.send_header('Content-Length', '0')
        self.end_headers()


def probe(binary, origin, output):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='replay-mechanism-profile-') as profile:
        with (output / (binary.parent.name + '.log')).open('wb') as log:
            process = subprocess.Popen([str(binary), 'serve', '--host', '127.0.0.1', '--port', str(port), '--layout', '--font', '--profile-dir', profile], stdout=log, stderr=log)
            try:
                endpoint = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with urllib.request.urlopen(endpoint + '/json/version', timeout=1):
                            break
                    except Exception:
                        if time.monotonic() > deadline or process.poll() is not None:
                            raise RuntimeError('CDP startup failed')
                        time.sleep(.1)
                with sync_playwright() as pw:
                    browser = pw.chromium.connect_over_cdp(endpoint, timeout=15000)
                    context = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = context.new_page()
                    session = context.new_cdp_session(page)
                    events = []
                    session.on('Network.requestWillBeSent', lambda event: events.append({
                        'requestId': event['requestId'], 'method': event['request']['method'],
                        'url': event['request']['url'], 'body': event['request'].get('postData'),
                        'redirect_status': (event.get('redirectResponse') or {}).get('status'),
                    }))
                    session.send('Network.enable')
                    wire_start = len(Fixture.requests)
                    page.goto(origin + '/redirect-form', wait_until='load', timeout=10000)
                    page.evaluate("document.querySelector('form').requestSubmit()")
                    page.wait_for_url(origin + '/redirect-final', timeout=10000)
                    page.wait_for_timeout(300)
                    redirect = {'wire': Fixture.requests[wire_start:], 'cdp': list(events)}
                    events.clear()
                    wire_start = len(Fixture.requests)
                    page.evaluate("async () => {const r = await fetch('/redirect-save', {method:'POST', body:'value=probe'}); return await r.text()}")
                    page.wait_for_timeout(300)
                    fetch_redirect = {'wire': Fixture.requests[wire_start:], 'cdp': list(events)}
                    session.detach()
                    page.goto(origin + '/form', wait_until='load', timeout=10000)
                    page.locator('#q').fill('query')
                    press_error = None
                    try:
                        page.locator('#q').press('Enter', timeout=3000)
                    except Exception as exc:
                        press_error = str(exc)
                    form = page.evaluate('({submits:window.submits,value:document.querySelector("#q").value})')
                    page.goto(origin + '/disabled-control', wait_until='load', timeout=10000)
                    service = SimpleNamespace(_page=page, _submit=lambda callback: callback())
                    tool = SimpleNamespace(_get_service=lambda: service)
                    disabled_control = []
                    for enabled in (False, True):
                        if enabled:
                            page.evaluate("document.querySelector('#action').disabled = false")
                        diagnostics = {}
                        status, result = _execute_replay_call(tool, {
                            'action': 'click', 'selector': '#action',
                            'replay_mode': 'snapshot_ref_semantics',
                        }, diagnostics=diagnostics)
                        disabled_control.append({'enabled': enabled, 'status': status,
                                                 'result': result, 'diagnostics': diagnostics,
                                                 'click_events': page.evaluate('window.clicks')})
                    page.goto(origin + '/data.json', wait_until='load', timeout=10000)
                    document = page.evaluate('({text:document.body.innerText,pre:!!document.querySelector("pre"),bold:!!document.querySelector("b"),charset:document.characterSet})')
                    page.goto(origin + '/geometry', wait_until='load', timeout=10000)
                    geometry = page.evaluate('''() => {
                      const a=document.querySelector('#split'), b=document.querySelector('#hidden');
                      const rect=e=>{const r=e.getBoundingClientRect();return [r.width,r.height]};
                      const before={split:rect(a),button:rect(b),offsetParent:b.offsetParent!==null};
                      document.querySelector('#rules').sheet.insertRule('#hidden{display:block;width:100px;height:30px}',document.querySelector('#rules').sheet.cssRules.length);
                      const after={button:rect(b),rectCount:b.getClientRects().length,offsetParent:b.offsetParent!==null};
                      return {before,after};
                    }''')
                    recorder = CdpHarRecorder()
                    recorder.start(context)
                    wire_start = len(Fixture.requests)
                    page.goto(origin + '/header-page', wait_until='load', timeout=10000)
                    for cookie in (False, True):
                        if cookie:
                            page.evaluate("document.cookie='fixture=synthetic;path=/'")
                        for api in ('fetch', 'xhr'):
                            target = f'/header-data?api={api}&cookie={int(cookie)}'
                            page.evaluate('''async ({api,target}) => {
                              if(api==='fetch') return await (await fetch(target)).text();
                              return await new Promise((resolve,reject)=>{
                                const x=new XMLHttpRequest();x.open('GET',target);
                                x.onload=()=>resolve(x.responseText);x.onerror=()=>reject(new Error('XHR failed'));x.send();
                              });
                            }''', {'api': api, 'target': target})
                    har_path = output / (binary.parent.name + '-headers.har')
                    recorder.stop(context, har_path)
                    entries = json.loads(har_path.read_text())['log']['entries']
                    header_recording = {
                        'wire': [entry for entry in Fixture.requests[wire_start:] if entry['path'].startswith('/header-data')],
                        'har': [{'url': entry['request']['url'], 'status': entry['response']['status'],
                                 'headers': entry['request']['headers']}
                                for entry in entries if '/header-data' in entry['request']['url']],
                        'recorder_errors': recorder.errors,
                    }
                    browser.close()
                return {'binary':str(binary), 'redirect':redirect, 'fetch_redirect':fetch_redirect, 'implicit_submit':form, 'press_error':press_error, 'disabled_control':disabled_control, 'json_document':document,'geometry':geometry, 'header_recording': header_recording}
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, action="append", required=True,
                        help="Frozen Moli executable; repeat to compare builds")
    args = parser.parse_args()
    for binary in args.binary:
        if not binary.is_file():
            parser.error(f"binary does not exist: {binary}")
    output = Path(tempfile.mkdtemp(prefix="replay-mechanism-evidence-"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        results = []
        for binary in args.binary:
            results.append(probe(binary.resolve(), f"http://127.0.0.1:{server.server_port}", output))
            (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps({"output": str(output), "results": results}, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
