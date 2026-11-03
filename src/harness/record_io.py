"""Hashing and UTF-8 JSON framing shared by execution, certification and report code."""
import hashlib
import json
from pathlib import Path


def parse_utf8_json(body: bytes):
    """Use the same strict wire encoding for acquisition and evidence replay."""
    return json.loads(body.decode('utf-8'))


def file_sha256(path):
    """Hash file bytes without loading the entire artifact into memory.

    This is a digest primitive, not a trust boundary: callers retain their
    containment, symlink, expected-hash and public-view policies.
    """
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def optional_file_sha256(path):
    """Hash a regular file, or return None when it is absent."""
    return file_sha256(path) if Path(path).is_file() else None


def bytes_sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_json_sha256(value, *, ensure_ascii=False, allow_nan=True):
    """Hash the shared compact sorted JSON protocol, including legacy NaN bytes.

    The defaults are the current protocol. A caller whose digests are already
    bound names its frozen ``ensure_ascii`` or strict ``allow_nan`` choice.
    """
    raw = json.dumps(value, ensure_ascii=ensure_ascii, sort_keys=True,
                     separators=(',', ':'), allow_nan=allow_nan).encode('utf-8')
    return bytes_sha256(raw)


def json_bytes(value):
    """Encode the indented, unsorted document form with a trailing newline."""
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def read_json(path):
    """Read one strict UTF-8 JSON document; callers own containment and shape checks."""
    return parse_utf8_json(Path(path).read_bytes())


def write_json(path, value, *, parents=False):
    """Write ``json_bytes`` in place; use an atomic writer where readers may race."""
    path = Path(path)
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))


def same_json_record(left, right):
    """Compare JSON records without equating boolean and numeric scalar values."""
    options = dict(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return json.dumps(left, **options) == json.dumps(right, **options)


def parse_jsonl(raw: bytes | str, *, skip_blank=False, allow_incomplete_tail=False):
    """Parse LF-delimited records without treating Unicode text as framing.

    Only a final unterminated JSON fragment or incomplete UTF-8 character may
    be ignored while a writer is flushing. Completed-line corruption fails.
    """
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    lines = raw.split(b'\n')
    if lines[-1] == b'':
        lines.pop()
    records = []
    for index, line in enumerate(lines):
        if skip_blank and not line.strip():
            continue
        try:
            records.append(json.loads(line.decode('utf-8')))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            partial_tail = (allow_incomplete_tail and index == len(lines) - 1
                            and not raw.endswith((b'\n', b'\r')))
            if partial_tail and (isinstance(error, json.JSONDecodeError)
                                 or error.reason == 'unexpected end of data'):
                continue
            raise
    return records


def load_jsonl(path, *, skip_blank=False):
    """Read a finalized file; every non-skipped record must be valid."""
    records = []
    with Path(path).open('rb') as source:
        for line in source:
            line = line.removesuffix(b'\n')
            if skip_blank and not line.strip():
                continue
            records.append(json.loads(line.decode('utf-8')))
    return records


def read_completed_records(path):
    """Read an append-only file that may have one still-flushing final record."""
    return parse_jsonl(path.read_bytes(), allow_incomplete_tail=True)
