"""Structured-extraction report messages through the shared locale owner."""

from pathlib import Path

from harness.record_io import bytes_sha256, read_json
from harness.reporting.locales import translate

MESSAGES = Path(__file__).with_name("messages.json")


def message(key, language, **values):
    return translate(MESSAGES, key, language, **values)


def validate_catalog():
    catalog = read_json(MESSAGES)
    if set(catalog) != {"en", "zh"} or set(catalog["en"]) != set(catalog["zh"]):
        raise ValueError("Report message catalog must cover both languages")
    for key, value in catalog["en"].items():
        other = catalog["zh"][key]
        if type(value) is not type(other) or not isinstance(value, (str, list)):
            raise ValueError("Report translation type differs: " + key)
        if isinstance(value, list) and (
            len(value) != len(other) or not all(isinstance(v, str) for v in value + other)
        ):
            raise ValueError("Report translation inventory differs: " + key)
    return catalog


def messages_sha256():
    return bytes_sha256(MESSAGES.read_bytes())
