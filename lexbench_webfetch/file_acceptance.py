"""Deterministic acceptance of actual delivered file bytes."""
import hashlib


def evaluate_files(case: dict, files: list[bytes]) -> dict:
    rows = []
    verification = case.get('source_verification', {})
    for raw in files:
        if not isinstance(raw, bytes):
            raise ValueError('Provider file data must be bytes')
        mime = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else
                'video/webm' if raw.startswith(b'\x1aE\xdf\xa3') else
                'video/mp4' if len(raw) >= 12 and raw[4:8] == b'ftyp' else None)
        expected = verification.get('mime', '').split(';')[0]
        digest = hashlib.sha256(raw).hexdigest()
        expected_hash = verification.get('file_sha256')
        rows.append({'bytes': len(raw), 'sha256': digest, 'mime': mime,
                     'pass': bool(len(raw) >= 32 and mime and (not expected or mime == expected)
                                  and (not expected_hash or digest == expected_hash))})
    return {'files': rows, 'pass': any(row['pass'] for row in rows),
            'scope': 'returned_bytes_signature_length_sha256; not semantic media validation'}
