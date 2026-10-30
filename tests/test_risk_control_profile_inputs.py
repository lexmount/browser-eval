"""Risk-control preflight resolves definitions without weakening evidence bindings."""

import hashlib
import json

import pytest

from harness.web_fetch import risk_control
from harness.web_fetch.reference_inputs import benchmark_input_path


def test_default_profile_resolves_hf_inputs_before_external_reference_check(monkeypatch, tmp_path):
    original = risk_control.DEFAULT_PROFILE.read_bytes()
    profile = json.loads(original)
    for role in ("qualification_manifest", "first_30_rules"):
        binding = profile["source_bindings"][role]
        logical = risk_control.rooted(binding["path"])
        resolved = benchmark_input_path(logical)
        assert not logical.exists()
        assert resolved.is_file()
        assert hashlib.sha256(resolved.read_bytes()).hexdigest() == binding["sha256"]
    # Qualification definitions are installed; historical reference evidence is separate.
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "empty-data-home"))
    with pytest.raises(FileNotFoundError, match="Reference inputs are not installed"):
        risk_control.validate(risk_control.DEFAULT_PROFILE)
    assert risk_control.DEFAULT_PROFILE.read_bytes() == original
    config = risk_control.rooted(profile["provider"]["config_path"])
    assert config.is_file() and benchmark_input_path(config) == config


@pytest.mark.parametrize("defect", ["historical_hash", "missing_evidence"])
def test_profile_rejects_unmatched_history_and_missing_local_evidence(tmp_path, defect):
    profile = json.loads(risk_control.DEFAULT_PROFILE.read_bytes())
    if defect == "historical_hash":
        profile["source_bindings"]["qualification_manifest"]["sha256"] = "0" * 64
    else:
        profile["source_bindings"]["local_evidence"] = {
            "path": str(tmp_path / "missing-private-evidence.json"),
            "sha256": "a" * 64,
        }
    path = tmp_path / "historical-profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    expected = (
        profile["source_bindings"]["qualification_manifest"]["path"]
        if defect == "historical_hash"
        else str(tmp_path / "missing-private-evidence.json")
    )
    with pytest.raises(ValueError, match="Frozen source binding changed") as failure:
        risk_control.validate(path)
    assert expected in str(failure.value)
