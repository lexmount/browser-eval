"""Hash and JSON helpers stay byte-stable and keep one owner under src/harness."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from harness.record_io import (
    bytes_sha256,
    canonical_json_sha256,
    file_sha256,
    json_bytes,
    optional_file_sha256,
    read_json,
    write_json,
)

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'harness'
# Names with no remaining definition under src/harness. Names that still have a
# behaviorally distinct definition (`digest`, `sha256`, `_read_json`) are not listed;
# the structural rule below still rejects bare copies under those names.
RETIRED_NAMES = {
    '_catalog_file_sha256', '_catalog_read_json', '_catalog_write_json', '_json_sha256',
    '_planned_actions_digest', '_sha256', '_sha256_bytes', '_sha256_file', '_write_json',
    'input_digest', 'json_digest', 'json_sha256', 'sha256_bytes', 'sha256_file', 'sha256_path',
    'text_digest',
}
# These bytes are part of a live hash binding, so their private copy stays:
# the resource orchestrators must equal the `orchestrator_sha256` their protocol declares,
# and reporting/visual.py is named by report visual-proof `checker_sources`.
FROZEN_SOURCE_COPIES = {
    ('lexbench/resource_matrix.py', 'sha'),
    ('lexbench/resource_profiles.py', 'sha256'),
    ('reporting/visual.py', 'file_sha256'),
}
# Forwarding wrappers around these helpers add a name and nothing else.
FORWARDING_HELPERS = {'bytes_sha256', 'file_sha256', 'read_json', 'write_json'}


def _is_sha256_hexdigest(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'hexdigest' and isinstance(node.func.value, ast.Call)
            and ast.unparse(node.func.value.func) == 'hashlib.sha256')


def _is_dumps(node):
    return isinstance(node, ast.Call) and ast.unparse(node.func) == 'json.dumps'


def _aliased_helper(function, body):
    """Name the helper when a wrapper only forwards its parameters to it.

    A canonical digest wrapper stays allowed when its name states a contract: a public
    protocol such as `task_definition_sha256`, or a digest that a writer and a verifier
    share such as `scoring_revision_history_sha256`. A cache decorator or a keyword option
    is a real difference too.
    """
    if function.decorator_list or len(body) != 1 or not isinstance(body[0], (ast.Return, ast.Expr)):
        return None
    value = body[0].value
    if not isinstance(value, ast.Call) or value.keywords:
        return None
    helper = ast.unparse(value.func)
    parameters = [argument.arg for argument in function.args.args]
    forwarded = [ast.unparse(argument) for argument in value.args]
    if helper in FORWARDING_HELPERS and forwarded == parameters:
        return helper
    if helper == 'bytes_sha256' and len(parameters) == 1 and forwarded in (
            [f'{parameters[0]}.encode()'], [f"{parameters[0]}.encode('utf-8')"]):
        return helper
    return None


def _copied_primitive(function):
    """Name the primitive when a function body only re-implements a shared helper."""
    body = list(function.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body.pop(0)
    parameters = {argument.arg for argument in function.args.args}
    alias = _aliased_helper(function, body)
    if alias:
        return alias
    if len(body) == 1 and isinstance(body[0], ast.Return) and body[0].value is not None:
        value = body[0].value
        if _is_sha256_hexdigest(value):
            argument = value.func.value.args[0] if value.func.value.args else None
            if any(_is_dumps(node) for node in ast.walk(value)):
                return 'canonical_json_sha256'
            if isinstance(argument, ast.Name) and argument.id in parameters:
                return 'bytes_sha256'
            if (isinstance(argument, ast.Call) and isinstance(argument.func, ast.Attribute)
                    and argument.func.attr in {'read_bytes', 'encode'}
                    and isinstance(argument.func.value, ast.Name)
                    and argument.func.value.id in parameters):
                return 'file_sha256' if argument.func.attr == 'read_bytes' else 'bytes_sha256'
        if (isinstance(value, ast.Call) and ast.unparse(value.func) == 'json.loads'
                and len(value.args) == 1 and isinstance(value.args[0], ast.Call)
                and isinstance(value.args[0].func, ast.Attribute)
                and value.args[0].func.attr == 'read_text'
                and isinstance(value.args[0].func.value, ast.Name)
                and value.args[0].func.value.id in parameters):
            return 'read_json'
    if len(body) == 2 and isinstance(body[1], ast.Return) and body[1].value is not None:
        first, value = body[0], body[1].value
        if (isinstance(first, ast.Assign) and any(_is_dumps(node) for node in ast.walk(first))
                and _is_sha256_hexdigest(value)):
            return 'canonical_json_sha256'
    if (len(body) == 3 and isinstance(body[0], ast.Assign) and isinstance(body[2], ast.Return)
            and any(isinstance(node, ast.Call) and ast.unparse(node.func) == 'hashlib.sha256'
                    for node in ast.walk(body[0]))
            and any(isinstance(node, ast.Attribute) and node.attr == 'update'
                    for node in ast.walk(body[1]))
            and any(isinstance(node, ast.Attribute) and node.attr == 'read'
                    for node in ast.walk(body[1]))):
        return 'file_sha256'
    statements = [node for node in body if not (
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func).endswith('.mkdir'))]
    if (len(statements) == 1 and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Call)
            and isinstance(statements[0].value.func, ast.Attribute)
            and statements[0].value.func.attr == 'write_text'
            and any(_is_dumps(node) for node in ast.walk(statements[0]))
            and not any(isinstance(node, ast.GeneratorExp) for node in ast.walk(statements[0]))):
        return 'write_json'
    return None


def _is_optional_hash_copy(node):
    """Recognize `<digest of path> if path.is_file() else None`, which has a shared helper."""
    if not (isinstance(node, ast.IfExp) and isinstance(node.orelse, ast.Constant)
            and node.orelse.value is None and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Attribute) and node.test.func.attr == 'is_file'):
        return False
    path = ast.unparse(node.test.func.value)
    return ast.unparse(node.body) in {
        f'file_sha256({path})',
        f'sha256({path}.read_bytes())',
        f'bytes_sha256({path}.read_bytes())',
        f'hashlib.sha256({path}.read_bytes()).hexdigest()',
    }


def _violations(source, relative):
    found = []
    for node in ast.walk(ast.parse(source)):
        if _is_optional_hash_copy(node):
            found.append(f'{relative}:{node.lineno} copies optional_file_sha256')
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in RETIRED_NAMES:
            found.append(f'{relative}:{node.lineno} defines retired helper {node.name}')
        copied = _copied_primitive(node)
        if copied and (relative, node.name) not in FROZEN_SOURCE_COPIES:
            found.append(f'{relative}:{node.lineno} {node.name} copies {copied}')
    return found


def test_harness_defines_no_private_hash_or_json_copies():
    violations = []
    for path in sorted(SOURCE.rglob('*.py')):
        relative = path.relative_to(SOURCE).as_posix()
        if relative != 'record_io.py':
            violations += _violations(path.read_text(encoding='utf-8'), relative)
    assert violations == []


def test_frozen_copies_still_exist_so_their_exemptions_cannot_go_stale():
    for relative, name in FROZEN_SOURCE_COPIES:
        tree = ast.parse((SOURCE / relative).read_text(encoding='utf-8'))
        assert any(isinstance(node, ast.FunctionDef) and node.name == name for node in ast.walk(tree))


@pytest.mark.parametrize('source,expected', [
    ('def _sha256(path):\n    return 1\n', 'retired helper _sha256'),
    ('def f(raw):\n    return hashlib.sha256(raw).hexdigest()\n', 'copies bytes_sha256'),
    ('def f(text):\n    return hashlib.sha256(text.encode("utf-8")).hexdigest()\n', 'copies bytes_sha256'),
    ('def f(path):\n    return hashlib.sha256(path.read_bytes()).hexdigest()\n', 'copies file_sha256'),
    ('def f(value):\n    return hashlib.sha256(json.dumps(value).encode()).hexdigest()\n',
     'copies canonical_json_sha256'),
    ('def f(value):\n    raw = json.dumps(value)\n    return hashlib.sha256(raw.encode()).hexdigest()\n',
     'copies canonical_json_sha256'),
    ('def f(path):\n    return json.loads(path.read_text(encoding="utf-8"))\n', 'copies read_json'),
    ('def f(path, value):\n    path.write_text(json.dumps(value))\n', 'copies write_json'),
    ('def f(text):\n    return bytes_sha256(text.encode())\n', 'copies bytes_sha256'),
    ('def f(text):\n    return bytes_sha256(text.encode("utf-8"))\n', 'copies bytes_sha256'),
    ('def f(raw):\n    return bytes_sha256(raw)\n', 'copies bytes_sha256'),
    ('def f(path):\n    return file_sha256(path)\n', 'copies file_sha256'),
    ('def f(path):\n    return read_json(path)\n', 'copies read_json'),
    ('def f(path, value):\n    write_json(path, value)\n', 'copies write_json'),
    ('def f(path, value):\n    path.parent.mkdir()\n    path.write_text(json.dumps(value))\n',
     'copies write_json'),
    ('def f(path):\n    digest = hashlib.sha256()\n    with path.open("rb") as stream:\n'
     '        for chunk in iter(lambda: stream.read(4096), b""):\n            digest.update(chunk)\n'
     '    return digest.hexdigest()\n', 'copies file_sha256'),
    ('value = file_sha256(path) if path.is_file() else None\n', 'copies optional_file_sha256'),
    ('value = sha256(path.read_bytes()) if path.is_file() else None\n', 'copies optional_file_sha256'),
    ('value = hashlib.sha256(root.path.read_bytes()).hexdigest() if root.path.is_file() else None\n',
     'copies optional_file_sha256'),
])
def test_guard_rejects_each_copied_form(source, expected):
    assert any(expected in item for item in _violations(source, 'example.py'))


@pytest.mark.parametrize('source', [
    # A precondition is a trust boundary the shared primitive deliberately does not own.
    'def f(path):\n    require(path)\n    return file_sha256(path)\n',
    # Different hashed protocols are not copies of a shared helper.
    'def f(ids):\n    return hashlib.sha256(("\\n".join(ids) + "\\n").encode()).hexdigest()\n',
    'def f(value, seed):\n    return hashlib.sha256(f"{seed}\\0{value}".encode()).hexdigest()\n',
    # Tree digests and JSONL framing are their own protocols.
    'def f(root):\n    digest = hashlib.sha256()\n    for path in root.rglob("*"):\n'
    '        digest.update(path.read_bytes())\n    return digest.hexdigest()\n',
    'def f(path, rows):\n    path.write_text("".join(json.dumps(row) + "\\n" for row in rows))\n',
    # Shape checks and alternative readers keep their local wrapper.
    'def f(path):\n    value = read_json(path)\n    assert isinstance(value, dict)\n    return value\n',
    'def f(path):\n    return json.loads(read_dataset_bytes(path))\n',
    # Each of these wrappers adds something the helper it calls does not do.
    '@cache\ndef f(path):\n    return read_json(path)\n',
    'def f(path, value):\n    write_json(path, value, parents=True)\n',
    'def f(path):\n    return file_sha256(unlinked_path(path))\n',
    'def f(resource):\n    return bytes_sha256(resource.read_bytes())\n',
    'def f(value):\n    return bytes_sha256(json_bytes(value))\n',
    # A canonical digest name can state a public protocol or a writer-verifier contract.
    'def f(value):\n    return canonical_json_sha256(value)\n',
    'def f(value):\n    return canonical_json_sha256(value, allow_nan=False)\n',
    # These conditionals return something else, check another path or read another way.
    'value = {"sha256": file_sha256(path)} if path.is_file() else None\n',
    'value = file_sha256(other) if path.is_file() else None\n',
    'value = file_sha256(path) if path.is_file() else ""\n',
    'value = sha256(read_dataset_bytes(path)) if path.is_file() else None\n',
    # json.loads(bytes) also accepts UTF-16/32, which read_json rejects.
    'def f(path):\n    return json.loads(path.read_bytes())\n',
])
def test_guard_keeps_behaviorally_distinct_helpers(source):
    assert _violations(source, 'example.py') == []


def test_guard_exempts_only_the_named_frozen_source():
    source = 'def sha(path):\n    return hashlib.sha256(path.read_bytes()).hexdigest()\n'
    assert _violations(source, 'lexbench/resource_matrix.py') == []
    assert _violations(source, 'lexbench/resource_summary.py') != []


VALUE = {'z': '中文', 'a': [1, {'y': None, 'x': True}]}


@pytest.mark.parametrize('options,raw', [
    ({}, '{"a":[1,{"x":true,"y":null}],"z":"中文"}'),
    ({'allow_nan': False}, '{"a":[1,{"x":true,"y":null}],"z":"中文"}'),
    ({'ensure_ascii': True}, '{"a":[1,{"x":true,"y":null}],"z":"\\u4e2d\\u6587"}'),
])
def test_canonical_options_produce_the_frozen_bytes(options, raw):
    assert canonical_json_sha256(VALUE, **options) == hashlib.sha256(raw.encode()).hexdigest()


def test_each_migrated_option_set_equals_its_original_serialization():
    originals = [
        (dict(ensure_ascii=True), json.dumps(VALUE, sort_keys=True, separators=(',', ':'))),
        (dict(allow_nan=False), json.dumps(VALUE, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':'), allow_nan=False)),
        ({}, json.dumps(VALUE, ensure_ascii=False, sort_keys=True, separators=(',', ':'))),
    ]
    for options, raw in originals:
        assert canonical_json_sha256(VALUE, **options) == hashlib.sha256(raw.encode()).hexdigest()


def test_strict_option_rejects_nan_while_the_default_keeps_legacy_bytes():
    with pytest.raises(ValueError):
        canonical_json_sha256({'n': float('nan')}, allow_nan=False)
    assert canonical_json_sha256({'n': float('nan')}) == bytes_sha256(b'{"n":NaN}')


def test_file_and_bytes_hashes_agree_with_the_whole_file_digest(tmp_path):
    path = tmp_path / 'artifact.bin'
    payload = bytes(range(256)) * 16384  # Larger than any read chunk.
    path.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert file_sha256(path) == bytes_sha256(payload) == optional_file_sha256(path) == expected


def test_only_the_optional_hash_accepts_a_missing_file(tmp_path):
    assert optional_file_sha256(tmp_path / 'absent') is None
    assert optional_file_sha256(tmp_path) is None
    with pytest.raises(FileNotFoundError):
        file_sha256(tmp_path / 'absent')


def test_json_document_bytes_are_unsorted_indented_utf8_with_one_newline():
    value = {'z': '中文', 'a': [1]}
    expected = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    assert json_bytes(value) == expected
    assert json_bytes(value) == '{\n  "z": "中文",\n  "a": [\n    1\n  ]\n}\n'.encode()


def test_write_json_creates_parents_only_on_request(tmp_path):
    target = tmp_path / 'new' / 'value.json'
    with pytest.raises(FileNotFoundError):
        write_json(target, VALUE)
    write_json(target, VALUE, parents=True)
    assert target.read_bytes() == json_bytes(VALUE)
    assert read_json(target) == VALUE


@pytest.mark.parametrize('raw,error', [
    (b'\xef\xbb\xbf{}', json.JSONDecodeError),
    ('{}'.encode('utf-16'), UnicodeDecodeError),
    (b'{"a":\xff}', UnicodeDecodeError),
])
def test_read_json_matches_utf8_text_reading_for_invalid_documents(tmp_path, raw, error):
    path = tmp_path / 'value.json'
    path.write_bytes(raw)
    with pytest.raises(error):
        read_json(path)
    with pytest.raises(error):
        json.loads(path.read_text(encoding='utf-8'))
