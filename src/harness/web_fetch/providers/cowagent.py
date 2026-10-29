from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

from harness.web_fetch.providers.subprocess_jsonl import SubprocessJsonlProvider
from harness.web_fetch.safety import unlinked_path, write_new_file
from harness.web_fetch.storage import data_home


class CowAgentBuiltinProvider(SubprocessJsonlProvider):
    """Invoke CowAgent's repository-local WebFetch implementation unchanged."""

    def __init__(self, name, config):
        worker = Path(__file__).parents[1] / 'workers/cowagent_worker.py'
        workspace = Path(str(config.get(
            "workspace", data_home() / "runs/cowagent-workspace"
        ))).expanduser().resolve()
        derived = {**config, 'method_type': 'cowagent_builtin',
                   'command': [sys.executable, '-u', str(worker), str(workspace)],
                   'expected_python_version': config.get(
                       'expected_python_version', 'Python ' + sys.version.split()[0])}
        paths = derived.get('required_paths', [])
        if isinstance(paths, list):
            derived['required_paths'] = [*paths,
                '{harness_root}/harness/web_fetch/workers/cowagent_worker.py']
        super().__init__(name, derived)


EXTRACTOR_FIXTURE = '''<html><head><title>Fixture</title><script>SECRET_SCRIPT</script></head>
<body><nav>MENU_NOISE</nav><main><h1>Example article</h1><p>BODY_FACT</p>
<a href="https://example.org/detail/1">DETAIL_LABEL</a>
<img src="https://example.org/image.png"><video src="https://example.org/video.mp4"></video>
</main><footer>FOOTER_NOISE</footer></body></html>'''


def probe_extractor(source):
    tree = ast.parse(source)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'WebFetch')
    fn = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == '_extract_text')
    fn.decorator_list = []
    module = ast.Module(body=[fn], type_ignores=[])
    namespace = {'re': re}
    exec(compile(ast.fix_missing_locations(module), '<CowAgent pure extractor>', 'exec'), namespace)
    output = namespace['_extract_text'](EXTRACTOR_FIXTURE)
    return {
        'scope': 'Synthetic offline mechanism probe, not an additional benchmark attempt',
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'extractor_source': ast.unparse(fn),
        'fixture': EXTRACTOR_FIXTURE, 'output': output,
        'observations': {name: value in output for name, value in {
            'body_preserved': 'BODY_FACT', 'navigation_preserved': 'MENU_NOISE',
            'footer_preserved': 'FOOTER_NOISE', 'link_label_preserved': 'DETAIL_LABEL',
            'script_preserved': 'SECRET_SCRIPT', 'detail_url_preserved': 'https://example.org/detail/1',
            'image_url_preserved': 'https://example.org/image.png',
            'video_url_preserved': 'https://example.org/video.mp4',
        }.items()},
    }


def probe_main(argv=None):
    """Execute an extractor from trusted local source, without importing its module."""

    parser = argparse.ArgumentParser(description="Offline synthetic CowAgent extractor probe")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = unlinked_path(args.output)
    if output.exists():
        raise ValueError("output already exists")
    result = probe_extractor(args.source.read_text())
    write_new_file(output, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result["observations"]))
