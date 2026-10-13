"""Cross-process scheduling outside provider timing and deadline budgets."""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit


class CaseAdmission:
    """Bound local/cloud calls and serialize a shared destination site.

    Only scheduling is controlled. No request, provider result, or score changes.
    The state directory belongs to a single declared execution, outside Git.
    """
    def __init__(self, root: Path, *, group: str, max_parallel: int = 2,
                 site_cooldown: float = 10.1):
        if group not in {'local', 'cloud'} or max_parallel not in {1, 2}:
            raise ValueError('Unsupported admission policy')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.group = group
        self.max_parallel = max_parallel
        self.site_cooldown = site_cooldown

    def describe(self):
        return {'max_parallel': self.max_parallel, 'max_local': 1, 'max_cloud': 1,
                'same_site_parallel': False, 'cross_method_site_cooldown_seconds': self.site_cooldown,
                'same_method_interval': 'unchanged_provider_configuration',
                'queue_wait_in_answer_latency': False,
                'parallel_timeout_or_429': 'reduce_to_serial; do_not_retry_recorded_result'}

    @contextmanager
    def _state(self):
        with (self.root/'state.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root/'state.json'
            state = json.loads(path.read_text()) if path.exists() else {
                'active': {}, 'finished_sites': {}, 'parallel_enabled': True}
            now = time.time()
            for key, item in list(state['active'].items()):
                try:
                    os.kill(item['pid'], 0)
                except ProcessLookupError:
                    state['finished_sites'][item['site']] = {'at': now, 'provider': item['provider']}
                    del state['active'][key]
            try:
                yield state
            finally:
                temporary = self.root/'state.json.tmp'
                temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2)+'\n')
                temporary.replace(path)
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _event(self, value):
        with (self.root/'events.jsonl').open('a') as handle:
            handle.write(json.dumps({'at': time.time(), **value})+'\n')

    def acquire(self, provider, case):
        host = (urlsplit(case.url).hostname or '').lower().rstrip('.')
        # Deliberately conservative: e.g. *.co.uk shares one scheduling group.
        site = '.'.join(host.split('.')[-2:])
        key = str(os.getpid())+':'+provider.name
        queued = time.monotonic()
        while True:
            with self._state() as state:
                active = list(state['active'].values())
                limit = self.max_parallel if state['parallel_enabled'] else 1
                previous = state['finished_sites'].get(site, {})
                cross_method_cooldown = (previous.get('provider') not in {None, provider.name}
                                         and time.time()-previous.get('at', 0) < self.site_cooldown)
                blocked = (len(active) >= limit
                           or any(item['group'] == self.group or item['site'] == site
                                  for item in active)
                           or cross_method_cooldown)
                if not blocked:
                    if key in state['active']:
                        raise ValueError('A method has an unreleased call')
                    token = {'key': key, 'pid': os.getpid(), 'provider': provider.name,
                             'case_id': case.id, 'site': site, 'group': self.group}
                    state['active'][key] = token
                    self._event({'event': 'admitted', **token,
                                 'queue_wait_ms': round((time.monotonic()-queued)*1000, 2),
                                 'active_calls': len(active)+1})
                    return token
            time.sleep(0.1)

    def _release_when_finished(self, token, pending):
        pending.join()
        self.release(token)

    def release(self, token, *, pending=None, record=None):
        with self._state() as state:
            if token['key'] not in state['active']:
                raise ValueError('Admission token was lost')
            record = record or {}
            message = str(record.get('error_message') or '').lower()
            limit_error = (record.get('status_code') == 429
                           or any(value in message for value in
                                  ('too many requests', 'rate limit', 'rate_limit', 'http 429')))
            if len(state['active']) > 1 and (record.get('error_type') == 'timeout' or limit_error):
                state['parallel_enabled'] = False
                self._event({'event': 'reduce_to_serial', 'attempt_id': record.get('attempt_id'),
                             'reason': 'timeout_or_429_observed_during_overlap; causality_unproven'})
            if pending is not None and pending.is_alive():
                state['active'][token['key']]['quarantined'] = True
                self._event({'event': 'quarantined_until_call_exit', **token})
                threading.Thread(target=self._release_when_finished, args=(token, pending),
                                 daemon=True, name='webfetch-admission-cleanup').start()
                return
            del state['active'][token['key']]
            state['finished_sites'][token['site']] = {'at': time.time(), 'provider': token['provider']}
            self._event({'event': 'released', **token})

    def set_serial(self, reason):
        with self._state() as state:
            state['parallel_enabled'] = False
            self._event({'event': 'reduce_to_serial', 'reason': reason})
