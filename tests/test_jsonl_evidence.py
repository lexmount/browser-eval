import json
import unittest

from harness.record_io import parse_jsonl


class JsonlEvidenceTests(unittest.TestCase):
    def test_unicode_content_survives_physical_line_boundaries(self):
        rows = [{"text": "正文" + separator + "保留", "index": index}
                for index, separator in enumerate(("\u0085", "\u2028", "\u2029", "\v", "\f"))]
        raw = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
        for payload in (raw, raw.encode("utf-8"), raw.replace("\n", "\r\n")):
            with self.subTest(kind=type(payload).__name__):
                self.assertEqual(parse_jsonl(payload), rows)

    def test_malformed_record_is_rejected_without_discarding_it(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_jsonl('{"case":"a"}\ninvalid\n')
