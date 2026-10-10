"""UTF-8 JSONL framing shared by execution, certification and report readers."""
import json


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
    return parse_jsonl(path.read_bytes(), skip_blank=skip_blank)


def read_completed_records(path):
    """Read an append-only file that may have one still-flushing final record."""
    return parse_jsonl(path.read_bytes(), allow_incomplete_tail=True)
