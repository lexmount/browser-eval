import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch.report_build import validate_projected_stable_text_evidence


def fixture(tmp_path: Path):
    text = b"Independent source evidence\nObserved ordinary text: Stable navigation\n"
    manifest = {
        "case_id": "case",
        "url": "https://example.test/page",
        "provider_output_used": False,
    }
    manifest_raw = (json.dumps(manifest) + "\n").encode()
    (tmp_path / "evidence.txt").write_bytes(text)
    (tmp_path / "manifest.json").write_bytes(manifest_raw)
    case = {
        "id": "case",
        "url": "https://example.test/page",
        "acceptance_contract": {
            "stable_text_anchors": [
                {"id": "stable", "alternatives": ["Stable navigation"]},
            ],
        },
        "stable_text_evidence": {
            "path": "evidence.txt",
            "sha256": hashlib.sha256(text).hexdigest(),
            "manifest_path": "manifest.json",
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        },
    }
    return case


def test_projected_stable_text_evidence_accepts_bound_source_text(tmp_path):
    assert validate_projected_stable_text_evidence(fixture(tmp_path), tmp_path)


def test_projected_stable_text_evidence_rejects_missing_anchor(tmp_path):
    case = fixture(tmp_path)
    case["acceptance_contract"]["stable_text_anchors"][0]["alternatives"] = ["Missing"]
    with pytest.raises(ValueError, match="lacks independent evidence"):
        validate_projected_stable_text_evidence(case, tmp_path)


def test_projected_stable_text_evidence_rejects_provider_derived_manifest(tmp_path):
    case = fixture(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["provider_output_used"] = True
    raw = (json.dumps(manifest) + "\n").encode()
    manifest_path.write_bytes(raw)
    case["stable_text_evidence"]["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="out of scope"):
        validate_projected_stable_text_evidence(case, tmp_path)
