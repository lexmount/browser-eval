"""Declared native input and route contracts, independent of URLs and answers."""

INPUT_MEDIA_KINDS = frozenset({
    'html_page', 'direct_pdf', 'direct_docx', 'direct_text',
    'direct_image', 'direct_media',
})


def declared_input_media_kind(case, *, required=True):
    """Read a dataset declaration or its worker projection; never inspect a URL.

    Dataset declarations live in ``source_benchmark.input_media_kind``. Workers
    receive the same value as ``input_media_kind`` in the JSONL request.
    """
    get = case.get if isinstance(case, dict) else lambda key, default=None: getattr(case, key, default)
    source = get('source_benchmark', {}) or {}
    if not isinstance(source, dict):
        raise ValueError('Native input media declaration must be an object')
    nested, direct = source.get('input_media_kind'), get('input_media_kind')
    if nested is not None and direct is not None and nested != direct:
        raise ValueError('Conflicting native input media declarations')
    value = direct if direct is not None else nested
    if value is None and not required:
        return None
    if not isinstance(value, str) or value not in INPUT_MEDIA_KINDS:
        raise ValueError('Native calls require a declared input_media_kind')
    return value


# Native parameter values come from the immutable provider configuration and its
# pinned implementation, not a case URL, expected text, or provider outcome.
def _digest(value):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def native_route_cell(provider, case):
    media = declared_input_media_kind(case)
    kind, track = case.task_kind, case.track
    key = (provider.name, track, kind, media)
    config, method = provider.config, provider.type
    endpoint = None
    if method == 'moli_webfetch':
        transport = 'cli'
        interface = ('raw_file' if kind == 'file' else 'native_resource_html'
                     if kind == 'resource' else 'native_document_bytes'
                     if track == 'document' and media in {'direct_pdf', 'direct_docx'}
                     else 'dynamic_native_markdown' if track == 'dynamic' else 'ordinary_markdown')
        fields = ['original_bytes'] if interface in {'raw_file', 'native_document_bytes'} else ['stdout']
    elif method in {'firecrawl', 'tavily_extract', 'exa_contents', 'cloudflare_markdown', 'cloudflare_content'}:
        transport = 'http'
        if method == 'firecrawl':
            interface = '/v2/scrape'
            endpoint = str(config.get('base_url', 'https://api.firecrawl.dev')).rstrip('/') + interface
            fields = ['data.rawBase64'] if kind == 'file' else ['data.markdown', 'data.links', 'data.images', 'data.videos', 'data.video']
        elif method == 'tavily_extract':
            interface = '/extract'
            endpoint = str(config.get('base_url', 'https://api.tavily.com')).rstrip('/') + interface
            fields = ['results[].raw_content', 'results[].images', 'images']
        elif method == 'exa_contents':
            interface = '/contents'
            endpoint = str(config.get('base_url', 'https://api.exa.ai')).rstrip('/') + interface
            fields = ['results[].text', 'results[].extras']
        else:
            interface = provider.native_http_interface(case)
            endpoint = 'https://api.cloudflare.com/client/v4/accounts/' + str(config.get('account_id', '')) + '/browser-rendering' + interface
            if method=='cloudflare_markdown' and interface=='/markdown' and config.get('browser'):
                from urllib.parse import urlencode
                endpoint += '?'+urlencode({'browser': config['browser']})
            fields = ['result']
    elif method in {'subprocess_jsonl', 'cowagent_builtin'}:
        transport, interface = 'jsonl', 'native_worker_command'
        fields = ['content', 'metadata.links', 'metadata.images', 'metadata.videos', 'metadata.media', 'metadata.extras', 'files']
    elif method == 'fixture':
        transport, interface, fields = 'fixture', 'synthetic_fixture', ['content']
    else:
        raise ValueError('Unsupported fixed-native interface: ' + method)
    options = ('max_bytes', 'max_age', 'max_age_hours', 'only_main_content', 'include_images',
               'extract_depth', 'text_verbosity', 'include_resource_links', 'rich_links_limit',
               'wait_until', 'block_private_networks', 'dns_mode', 'log_level', 'formats')
    cell = {'method': key[0], 'provider_type': method, 'track': track, 'task_kind': kind, 'input_media_kind': media,
            'transport': transport, 'interface': interface, 'answer_fields': fields,
            'configured_parameters': {k: config[k] for k in options if k in config},
            'unspecified_parameters': 'native defaults from the bound implementation',
            **({'command_sha256': _digest(config.get('command'))} if transport == 'jsonl' else {}),
            'provider_configuration_sha256': _digest(config),
            'endpoint_sha256': _digest(endpoint) if endpoint is not None else None}
    return key, cell


def _request_parameters(provider, case, cell, timeout_seconds):
    """Explicit pre-call transport contract, separately checked against adapters."""
    if cell['transport'] == 'http':
        endpoint, arguments = provider.native_http_arguments(case, timeout_seconds)
        if _digest(endpoint) != cell['endpoint_sha256']:
            raise ValueError('Native request builder differs from its declared endpoint')
        return normalized_http_parameters(case, arguments)
    if cell['transport'] == 'cli':
        command = provider.native_cli_command(case)
        return {'argv': ['<native-binary>', *command[1:-1], '<input-url>'],
                'binary_sha256': provider.config.get('expected_binary_sha256')}
    if cell['transport'] == 'jsonl':
        request = provider.native_worker_request(case, timeout_seconds,
            '<diagnostic-output>' if provider.config.get('log_level') == 'debug' else None)
        request['url'] = '<input-url>'
        command = [provider._resolve_token(part) for part in provider.config.get('command', [])]
        return {'request': request, 'command_sha256': _digest(command)}
    return {}


def normalized_http_parameters(case, arguments):
    from copy import deepcopy
    body = deepcopy(arguments.get('json_body'))
    if not isinstance(body, dict):
        raise ValueError('Native HTTP request lacks its declared input field')
    slots = [key for key in ('url', 'urls') if key in body]
    if slots == ['url'] and body['url'] == case.url:
        body['url'] = '<input-url>'
    elif slots == ['urls'] and body['urls'] == [case.url]:
        body['urls'] = ['<input-url>']
    else:
        raise ValueError('Native HTTP input differs from the frozen task URL')
    return {'method': arguments.get('method', 'GET'), 'body': body,
            'headers_sha256': _digest(arguments.get('headers', {})),
            'timeout_seconds': arguments.get('timeout'), 'max_bytes': arguments.get('max_bytes')}



class NativeRouteLedger:
    """Freeze class-level intent before calls; reject observed per-case drift.

    Native defaults are bound by configuration and implementation identities.
    Interfaces and normalized transport parameters are frozen for every cell
    before the cohort. Actual arguments are checked before transport. No
    response, URL pattern, or expected answer selects a route.
    """
    def __init__(self, cases, providers, *, timeout_seconds=45):
        from datetime import datetime, timezone
        import threading
        self.cells, self.observed, self.calls, self.errors = {}, {}, {}, []
        self.lock = threading.Lock()
        self.providers = {p.name: p for p in providers if p.config.get('uniform_native_parameters')}
        self.inventory = []
        for case in cases:
            media = declared_input_media_kind(case) if self.providers else None
            if self.providers:
                self.inventory.append({'case_id': case.id, 'track': case.track,
                                       'task_kind': case.task_kind, 'input_media_kind': media,
                                       'input_url_sha256': _digest(case.url)})
            for provider in self.providers.values():
                key, cell = native_route_cell(provider, case)
                if case.track == 'dynamic':
                    from .retrieval_hints import native_default_case
                    if not native_default_case(case):
                        raise ValueError('Native dynamic inputs require a stable acceptance contract')
                cell['request_parameters'] = _request_parameters(provider, case, cell, timeout_seconds)
                if key in self.cells and self.cells[key] != cell:
                    raise ValueError('Native route intent differs within its declared class')
                self.cells[key] = cell
        self.document = {'schema_version': 1, 'kind': 'declared_native_route_contract',
                         'frozen_at': datetime.now(timezone.utc).isoformat(),
                         'pre_call_frozen': True,
                         'parameter_policy': 'Interfaces, adapter request parameters and configured options frozen before the cohort; actual transport arguments must match. Unspecified product internals retain pinned native defaults.',
                         'cases': self.inventory, 'cells': list(self.cells.values())}
        self.binding = None

    def write(self, source, *, dataset_sha256, runtime_source_sha256):
        import hashlib
        import json
        self.document.update(dataset_sha256=dataset_sha256, runtime_source_sha256=runtime_source_sha256)
        raw = (json.dumps(self.document, ensure_ascii=False, indent=2)+'\n').encode()
        path = source/'native-route-contract.json'
        if path.exists():
            raise ValueError('Native route contract already exists; do not overwrite a run')
        path.write_bytes(raw)
        self.binding = {'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest()}
        for provider in self.providers.values():
            provider._native_route_ledger = self
        return self.binding

    def reject(self, provider, case, reason):
        with self.lock:
            self.errors.append({'method': provider.name, 'case_id': case.id, 'reason': reason})
        raise ValueError(reason)

    def observe(self, provider, case, *, transport, parameters, endpoint=None, interface=None):
        key, current = native_route_cell(provider, case)
        cell = self.cells.get(key, {})
        with self.lock:
            message = 'Native route protocol was already violated' if self.errors else None
            if {k:v for k,v in cell.items() if k!='request_parameters'} != current:
                message = 'Native route configuration changed after the pre-call freeze'
            elif transport != cell['transport']:
                message = 'Native transport differs from its frozen route'
            elif endpoint is not None and _digest(endpoint) != cell['endpoint_sha256']:
                message = 'Native endpoint differs from its frozen route'
            elif interface is not None and interface != cell['interface']:
                message = 'Native command differs from its frozen route'
            signature = {'transport': transport, 'interface': cell['interface'],
                         'endpoint_sha256': cell['endpoint_sha256'], 'parameters': parameters}
            digest = _digest(signature)
            if not message and parameters != cell.get('request_parameters'):
                message = 'Native request parameters differ from the pre-call contract'
            if not message and key in self.observed and self.observed[key] != digest:
                message = 'Native request signature changed within the same declared class'
            identity = (provider.name, case.id)
            if not message and self.calls.get(identity):
                message = 'Fixed-native calls permit only one adapter invocation per task'
            if message:
                self.errors.append({'method': provider.name, 'case_id': case.id, 'reason': message})
                raise ValueError(message)
            self.observed[key] = digest
            self.calls.setdefault(identity, []).append({'signature': signature, 'sha256': digest})

    def receipt(self, provider, case, *, ok=None):
        """Transport started means entry to the outbound call, not server receipt."""
        from copy import deepcopy
        key, _ = native_route_cell(provider, case)
        with self.lock:
            return {'contract_sha256': self.binding['sha256'], 'cell': list(key),
                    'answer_fields': list(self.cells[key]['answer_fields']),
                    'requests': deepcopy(self.calls.get((provider.name, case.id), [])),
                    'transport_state': 'started' if self.calls.get((provider.name, case.id)) else 'not_started',
                    **({'failure_stage': 'after_transport' if self.calls.get((provider.name, case.id))
                        else 'before_transport'} if ok is False else {}),
                    'protocol_errors': [dict(e) for e in self.errors if e['method']==provider.name and e['case_id']==case.id]}


def verify_native_route_contract(source, manifest, dataset, records):
    """Shared execution/release check for newly declared native cohorts only."""
    import hashlib
    import json
    from datetime import datetime
    from .safety import contained_path
    if manifest.get('native_route_errors'):
        raise ValueError('Native route protocol was violated')
    binding = manifest.get('native_route_contract')
    if not binding:
        if manifest.get('native_routes_required'):
            raise ValueError('Fixed-native cohort lacks its pre-call route contract')
        return None
    path = contained_path(source, binding['path'], reject_symlinks=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding.get('sha256'):
        raise ValueError('Native route contract hash changed')
    doc = json.loads(raw)
    if (doc.get('kind') != 'declared_native_route_contract' or doc.get('pre_call_frozen') is not True
            or doc.get('dataset_sha256') != manifest.get('dataset_sha256')
            or doc.get('runtime_source_sha256') != manifest.get('runtime_source_sha256')):
        raise ValueError('Native route contract differs from the frozen execution')
    cells = {(c['method'], c['track'], c['task_kind'], c['input_media_kind']): c for c in doc['cells']}
    cases = {c['id']: c for c in dataset['cases']}
    inventory = {c['case_id']: c for c in doc['cases']}
    if len(cells) != len(doc['cells']) or len(inventory) != len(doc['cases']):
        raise ValueError('Native route contract has duplicate declarations')
    names = {key[0] for key in cells}
    required = {p['name'] for p in manifest.get('providers', []) if p.get('fixed_native_parameters')}
    projection = manifest.get('projection', {}).get('kind')
    allowed_projection = projection in {'complete-provider-case-subset-from-retained-run',
        'complete-provider-from-retained-incomplete-matrix', 'completed-case-from-live-preflight'}
    if (not required or not required <= names or (required != names and not allowed_projection)):
        raise ValueError('Native route method scope differs from the frozen execution')
    names = required
    selected_cases = ({row['case_id'] for row in records if row['provider'] in names}
                      if allowed_projection else set(inventory))
    if not selected_cases <= set(inventory):
        raise ValueError('Native route projection changes its frozen input scope')
    expected = {(name, identifier) for name in names for identifier in selected_cases}
    actual, signatures = set(), {}
    for row in records:
        if row['provider'] not in names:
            continue
        identity = row['provider'], row['case_id']
        if identity in actual or identity not in expected:
            raise ValueError('Native route result inventory differs')
        actual.add(identity)
        case = cases[row['case_id']]
        media = declared_input_media_kind(case)
        if (_digest(case['url']) != inventory[row['case_id']]['input_url_sha256']
                or inventory[row['case_id']] != {'case_id': row['case_id'], 'track': case.get('track', 'web'),
                'task_kind': case.get('task_kind', 'body'), 'input_media_kind': media,
                'input_url_sha256': _digest(row['url'])}
                or datetime.fromisoformat(doc['frozen_at']) > datetime.fromisoformat(row['started_at'])):
            raise ValueError('Native input type or pre-call freeze differs')
        key = row['provider'], case.get('track', 'web'), case.get('task_kind', 'body'), media
        receipt = row.get('provider_metadata', {}).get('native_route_receipt', {})
        if receipt.get('protocol_errors'):
            raise ValueError('Native request violated its route contract')
        if receipt.get('contract_sha256') != binding['sha256'] or receipt.get('cell') != list(key) or key not in cells:
            raise ValueError('Native result lacks its bound route receipt')
        if receipt.get('answer_fields') != cells[key]['answer_fields']:
            raise ValueError('Native answer fields differ from the frozen route')
        requests = receipt.get('requests', [])
        state = receipt.get('transport_state')
        if state not in {'started', 'not_started'}:
            raise ValueError('Native result lacks its transport stage')
        if bool(requests) != (state == 'started'):
            raise ValueError('Native transport stage lacks its invocation signature')
        if not row['ok'] and receipt.get('failure_stage') != (
                'after_transport' if requests else 'before_transport'):
            raise ValueError('Native failed result lacks an explicit failure stage')
        if len(requests) > 1 or (row['ok'] and not requests and cells[key]['transport'] != 'fixture'):
            raise ValueError('Native result lacks one complete invocation signature')
        for item in requests:
            signature = item['signature']
            if (_digest(signature) != item.get('sha256')
                    or signature['transport'] != cells[key]['transport']
                    or signature['interface'] != cells[key]['interface']
                    or signature['endpoint_sha256'] != cells[key]['endpoint_sha256']
                    or signature.get('parameters') != cells[key]['request_parameters']):
                raise ValueError('Native invocation differs from its frozen interface')
            if key in signatures and signatures[key] != item['sha256']:
                raise ValueError('Native request signature differs within its declared class')
            signatures[key] = item['sha256']
    if actual != expected:
        raise ValueError('Native route contract has an incomplete result scope')
    return doc


def verify_native_execution_manifests(source, manifests, dataset, records):
    """Check native contracts at every existing aggregate/report boundary."""
    checked = []
    for manifest in manifests:
        if not manifest.get('native_routes_required') and not manifest.get('native_route_contract'):
            continue
        if source is None or dataset is None or records is None:
            raise ValueError('Native route verification requires retained inputs and complete records')
        identifiers = manifest.get('attempt_ids', [])
        if len(identifiers) != len(set(identifiers)):
            raise ValueError('Native execution repeats attempt identities')
        identifier_set = set(identifiers)
        selected = [row for row in records if row['attempt_id'] in identifier_set]
        if {r['attempt_id'] for r in selected} != set(identifiers):
            raise ValueError('Native execution omits retained attempt evidence')
        checked.append(verify_native_route_contract(source, manifest, dataset, selected))
    return checked


def materialize_native_route_contracts(output, source, manifests):
    """Copy hash-bound frozen contracts, deduplicating multiple cohort roots."""
    import hashlib
    import re
    import shutil
    from .safety import contained_path
    for manifest in manifests:
        binding = manifest.get('native_route_contract')
        if not binding:
            if manifest.get('native_routes_required'):
                raise ValueError('Native cohort is missing its frozen route contract')
            continue
        digest = binding.get('sha256', '')
        if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
            raise ValueError('Invalid native route contract digest')
        relative = binding.get('path')
        if relative not in {'native-route-contract.json', 'native-route-contracts/'+digest+'.json'}:
            raise ValueError('Invalid native route contract path')
        path = contained_path(source, relative, reject_symlinks=True)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Native route contract bytes changed')
        target_relative = 'native-route-contracts/'+digest+'.json'
        target = contained_path(output, target_relative, reject_symlinks=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('Conflicting native route contract bytes')
        else:
            shutil.copy2(path, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError('Copied native route contract bytes changed')
        manifest['native_route_contract'] = {'path': target_relative, 'sha256': digest}
