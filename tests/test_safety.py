import hashlib
from pathlib import Path
from unittest.mock import patch

from harness.web_fetch.safety import evidence_tree_identity


def test_evidence_tree_identity_streams_files(tmp_path):
    payload = b'large evidence block' * 1024
    (tmp_path / 'results.jsonl').write_bytes(payload)

    with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read')):
        identity = evidence_tree_identity(tmp_path)

    assert identity == {
        'results.jsonl': hashlib.sha256(payload).hexdigest(),
    }
