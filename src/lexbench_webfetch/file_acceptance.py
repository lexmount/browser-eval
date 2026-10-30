"""Deterministic acceptance of actual delivered file bytes."""
import hashlib


def _ebml_integer(raw, offset, *, identifier=False):
    if offset >= len(raw) or not raw[offset]:
        raise ValueError('Missing EBML integer')
    width = 9 - raw[offset].bit_length()
    end = offset + width
    if width > (4 if identifier else 8) or end > len(raw):
        raise ValueError('Invalid EBML integer')
    value = int.from_bytes(raw[offset:end], 'big')
    if not identifier:
        value &= (1 << (7 * width)) - 1
        if value == (1 << (7 * width)) - 1:
            raise ValueError('Unbounded EBML header')
    return value, end


def _media_mime(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\x1aE\xdf\xa3'):
        try:
            size, cursor = _ebml_integer(raw, 4)
            end = cursor + size
            if end > len(raw):
                return None
            types = []
            while cursor < end:
                identity, cursor = _ebml_integer(raw, cursor, identifier=True)
                size, cursor = _ebml_integer(raw, cursor)
                if cursor + size > end:
                    return None
                if identity == 0x4282:
                    types.append(raw[cursor:cursor + size].rstrip(b'\0'))
                cursor += size
            return 'video/webm' if types == [b'webm'] else None
        except ValueError:
            return None
    if len(raw) >= 16 and raw[4:8] == b'ftyp':
        size, cursor = int.from_bytes(raw[:4], 'big'), 8
        if size == 1:
            size, cursor = int.from_bytes(raw[8:16], 'big'), 16
        elif size == 0:
            size = len(raw)
        if size < cursor + 8 or size > len(raw) or (size - cursor - 8) % 4:
            return None
        # ISO BMFF also hosts still images and other media. Only supported MP4
        # brands identify this container; ftyp alone is insufficient.
        major = raw[cursor:cursor + 4]
        brands = {b'isom', b'iso2', b'iso3', b'iso4', b'iso5', b'iso6', b'iso7', b'iso8', b'iso9',
                  b'mp41', b'mp42', b'avc1', b'av01', b'M4V ', b'dash', b'msdh', b'msix'}
        return 'video/mp4' if major in brands else None
    return None


def evaluate_files(case: dict, files: list[bytes]) -> dict:
    rows = []
    verification = case.get('source_verification', {})
    for raw in files:
        if not isinstance(raw, bytes):
            raise ValueError('Provider file data must be bytes')
        mime = _media_mime(raw)
        expected = verification.get('mime', '').split(';')[0]
        digest = hashlib.sha256(raw).hexdigest()
        expected_hash = verification.get('file_sha256')
        rows.append({'bytes': len(raw), 'sha256': digest, 'mime': mime,
                     'pass': bool(len(raw) >= 32 and mime and (not expected or mime == expected)
                                  and (not expected_hash or digest == expected_hash))})
    return {'files': rows, 'pass': any(row['pass'] for row in rows),
            'scope': 'returned_bytes_signature_length_sha256; not semantic media validation'}
