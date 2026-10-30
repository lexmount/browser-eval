"""Media MIME acceptance distinguishes related containers from delivered headers."""
import pytest

from lexbench_webfetch.evaluation import evaluate_response


def ebml(doctype, *, extra=b''):
    fields = b'\x42\x82' + bytes([0x80 + len(doctype)]) + doctype + extra
    return b'\x1aE\xdf\xa3' + bytes([0x80 + len(fields)]) + fields + b'\0' * 32


def bmff(major, *, extended=False):
    fields = major + b'\0' * 4 + b'isommp42'
    header = ((1).to_bytes(4, 'big') + b'ftyp' + (16 + len(fields)).to_bytes(8, 'big')
              if extended else (8 + len(fields)).to_bytes(4, 'big') + b'ftyp')
    return header + fields + b'\0' * 32


@pytest.mark.parametrize('mime,raw,expected', [
    ('video/webm', ebml(b'webm'), True),
    ('video/webm', ebml(b'matroska'), False),
    ('video/webm', ebml(b'matroska', extra=b'\xec\x84webm'), False),
    ('video/webm', b'\x1aE\xdf\xa3' + b'\0' * 32, False),
    ('video/webm', b'\x1aE\xdf\xa3\xff' + b'webm' * 8, False),
    ('video/webm', ebml(b'webm')[:8] + b'\0' * 32, False),
    ('video/mp4', bmff(b'mp42'), True),
    ('video/mp4', bmff(b'isom'), True),
    ('video/mp4', bmff(b'mp41', extended=True), True),
    ('video/mp4', bmff(b'avif'), False),
    ('video/mp4', bmff(b'heic'), False),
    ('video/mp4', bmff(b'mif1'), False),
    ('video/mp4', b'\0\0\0\x08ftyp' + b'x' * 32, False),
    ('video/mp4', b'\0\0\xff\xffftypmp42' + b'x' * 32, False),
])
def test_public_file_scoring_requires_the_declared_container_header(mime, raw, expected):
    case = {'url': 'https://example.org/media', 'task_kind': 'file',
            'content_score_applicable': False, 'source_verification': {'mime': mime}}
    result = evaluate_response(case, b'', files=[raw])
    assert result['measured']
    assert result['passed'] is expected
    assert result['metrics']['raw_file_pass'] is expected
