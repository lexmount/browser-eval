import argparse
import json
import re
from pathlib import Path

import pytest

from harness.data_home import configured_data_home, data_home
from harness.lexbench import report as lexbench_report
from harness.lexbench import verification as lexbench_verification
from harness.trajectory_replay.public_webarena import release as webarena_release

ROOT = Path(__file__).resolve().parents[1]


def test_configured_home_expands_and_resolves(tmp_path, monkeypatch):
    # Two earlier copies kept a literal "~" directory and an unresolved path.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", "~/nested/../data")
    assert data_home() == (tmp_path / "data").resolve()


def test_relative_home_becomes_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", "relative")
    assert data_home() == (tmp_path / "relative").resolve()


def test_empty_value_means_unset_instead_of_the_working_directory(tmp_path, monkeypatch):
    # storage.py chose the default; the other copies silently used the working directory.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", "")
    assert configured_data_home() is None
    assert data_home() == (tmp_path / ".browser-eval").resolve()


def test_default_home_is_resolved_under_a_symlinked_home(tmp_path, monkeypatch):
    # storage.py left the default unresolved, so containment checks against
    # resolved evidence paths disagreed with web_search/publication.py.
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path / "link"))
    monkeypatch.delenv("BROWSER_EVAL_DATA_HOME", raising=False)
    assert configured_data_home() is None
    assert data_home() == real.resolve() / ".browser-eval"


def test_lexbench_report_reads_the_home_when_it_runs_not_when_it_is_imported(tmp_path, monkeypatch):
    home = tmp_path / "selected-after-import"
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(home))
    (home / "runs").mkdir(parents=True)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "paths": {
            name: f"runs/{name}"
            for name in ("cohort", "source", "output", "vendor_dir", "functional_correction",
                         "runner", "version_runs", "version_candidate", "layout_policy_runner")
        },
        "resource_population": dict.fromkeys(
            ("tasks", "attempts_per_task", "chart_tasks", "chart_calls"), 1),
    }), encoding="utf-8")
    with pytest.raises(FileNotFoundError) as failure:
        lexbench_report.main(["--profile", str(profile)])
    assert Path(failure.value.filename) == home.resolve() / "runs/cohort"


@pytest.mark.parametrize(
    ("run", "option", "relative"),
    [
        (lambda root: lexbench_verification.main([]), "site", "reports/moli-1.1.8-failures-site"),
        (webarena_release.main, "formal_root", "evidence/current/formal/webarena-public-trajectories"),
    ],
)
def test_retained_report_entry_points_default_under_the_selected_home(
    tmp_path, monkeypatch, run, option, relative
):
    # Both hard-coded ~/.browser-eval. Stopping at parse_args keeps the release untouched.
    class ParserReached(Exception):
        pass

    def stop_at_parse(parser, *_args, **_kwargs):
        raise ParserReached(parser.get_default(option))

    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(tmp_path / "selected"))
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", stop_at_parse)
    with pytest.raises(ParserReached) as reached:
        run(tmp_path)
    assert reached.value.args[0] == (tmp_path / "selected").resolve() / relative


def test_only_the_shared_module_reads_the_data_home():
    # lexbench_webfetch owns a different default home and precedence, and the
    # dataset cache has its own documented override (docs/benchmarks.md).
    allowed = {"src/harness/data_home.py", "src/harness/benchmarks/data.py"}
    forbidden = re.compile(
        r"(?:environ|getenv)[^\n]*BROWSER_EVAL_DATA_HOME|home\(\)\s*/\s*[\"']\.browser-eval"
        r"|~/\.browser-eval|storage import [^\n]*\bdata_home\b"
    )
    sources = [*(ROOT / "src/harness").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]
    found = [str(path.relative_to(ROOT)) for path in sources
             if str(path.relative_to(ROOT)) not in allowed and forbidden.search(path.read_text("utf-8"))]
    assert found == []
    assert forbidden.search('Path(os.environ.get("BROWSER_EVAL_DATA_HOME", x))')
    assert forbidden.search('Path.home() / ".browser-eval"')
    assert forbidden.search('Path(os.getenv("BROWSER_EVAL_DATA_HOME", x))')
    assert forbidden.search('Path("~/.browser-eval").expanduser()')
