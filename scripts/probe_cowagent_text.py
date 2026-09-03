"""Run only CowAgent's pure HTML extractor on a synthetic fixture, offline."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

FIXTURE = '''<html><head><title>Fixture</title><script>SECRET_SCRIPT</script></head>
<body><nav>MENU_NOISE</nav><main><h1>Example article</h1><p>BODY_FACT</p>
<a href="https://example.org/detail/1">DETAIL_LABEL</a>
<img src="https://example.org/image.png"><video src="https://example.org/video.mp4"></video>
</main><footer>FOOTER_NOISE</footer></body></html>'''


def probe(source):
    tree = ast.parse(source)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'WebFetch')
    fn = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == '_extract_text')
    fn.decorator_list = []
    module = ast.Module(body=[fn], type_ignores=[])
    namespace = {'re': re}
    exec(compile(ast.fix_missing_locations(module), '<CowAgent pure extractor>', 'exec'), namespace)
    output = namespace['_extract_text'](FIXTURE)
    return {
        'scope': 'Synthetic offline mechanism probe, not an additional benchmark attempt',
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'extractor_source': ast.unparse(fn),
        'fixture': FIXTURE, 'output': output,
        'observations': {name: value in output for name, value in {
            'body_preserved': 'BODY_FACT', 'navigation_preserved': 'MENU_NOISE',
            'footer_preserved': 'FOOTER_NOISE', 'link_label_preserved': 'DETAIL_LABEL',
            'script_preserved': 'SECRET_SCRIPT', 'detail_url_preserved': 'https://example.org/detail/1',
            'image_url_preserved': 'https://example.org/image.png',
            'video_url_preserved': 'https://example.org/video.mp4',
        }.items()},
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.source.read_text())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result['observations']))
