"""Evidence recorded before the Webstone rename stays readable."""
import json

from harness.browser_use.report import recorded_commit
from harness.browser_use.runner import _write_or_validate_run_config
from harness.trajectory_replay.public_webarena.qualification import KNOWN_HAR_CREATORS


def test_recorded_commit_reads_both_field_names():
    assert recorded_commit({"webstone_commit": "a"}) == "a"
    assert recorded_commit({"browser_eval_commit": "b"}) == "b"
    assert recorded_commit({}) is None


def test_resume_accepts_a_run_config_recorded_before_the_rename(tmp_path):
    path = tmp_path / "run-config.json"
    path.write_text(json.dumps({"schema": "s", "browser_eval_commit": "c"}), encoding="utf-8")
    _write_or_validate_run_config(path, {"schema": "s", "webstone_commit": "c"})


def test_qualification_accepts_the_recorded_har_creator():
    assert {creator["name"] for creator in KNOWN_HAR_CREATORS} == {"webstone-cdp", "browser-eval-cdp"}
