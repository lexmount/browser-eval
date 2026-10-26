"""Run one configured CowAgent checkout on the shared JSONL worker protocol."""
from __future__ import annotations

import importlib
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

# Direct execution starts in the upstream checkout, without an installed harness.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from harness.web_fetch.diagnostics import diagnostic_context, redact
from harness.web_fetch.network_dns import public_dns_context, validate_dns_mode


def main():
    root = Path.cwd()
    workspace = Path(sys.argv[1])
    workspace.mkdir(parents=True, exist_ok=True)
    # Only this process imports upstream code. Its prints cannot impersonate a
    # JSONL response, including prints that happen during module initialization.
    sys.path.insert(1, str(root))

    class NativeLogSink:
        def emit(self, source, event, **fields):
            print(json.dumps(redact({"source": source, "event": event, **fields}),
                             ensure_ascii=False, default=str), file=sys.stderr, flush=True)
    module = None
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        metadata = {
            'source_file': str(root / 'agent/tools/web_fetch/web_fetch.py'),
            'harness_timeout_seconds': request['timeout_seconds'],
        }
        try:
            with redirect_stdout(sys.stderr):
                if module is None:
                    module = importlib.import_module('agent.tools.web_fetch.web_fetch')
                metadata['cowagent_default_timeout_seconds'] = getattr(
                    module, 'DEFAULT_TIMEOUT', None)
                tool = module.WebFetch({'cwd': str(workspace)})
                dns_mode = validate_dns_mode(request.get('dns_mode', 'system'))
                with diagnostic_context(
                    NativeLogSink() if request.get('log_level') == 'debug' else None,
                    names=('log', 'urllib3', 'requests', 'langchain_community'),
                ), public_dns_context(dns_mode, request['timeout_seconds']) as dns_trace:
                    result = tool.execute({'url': request['url']})
                metadata.update(dns_mode=dns_mode, dns_resolutions=dns_trace,
                                effective_timeout_seconds=metadata['cowagent_default_timeout_seconds'],
                                timeout_adapter='native_default')
            content = str(result.result or '')
            ok = result.status == 'success'
            metadata['tool_status'] = result.status
            response = {'ok': ok, 'content': content, 'metadata': metadata,
                        'raw_tool_response': {'status': result.status, 'result': result.result,
                            'ext_data': getattr(result, 'ext_data', None),
                            'display': getattr(result, 'display', None)}}
            if not ok:
                response.update(error_type='tool_error', error_message=content)
        except Exception as error:
            response = {'ok': False, 'content': repr(error), 'metadata': metadata,
                        'error_type': type(error).__name__, 'error_message': str(error)}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
