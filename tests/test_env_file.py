import os
import re
from pathlib import Path

import pytest

from harness.browser_use.runner import _load_repo_env
from harness.env_file import parse_env_file
from harness.trajectory_replay import catalog_runner
from harness.trajectory_replay.public_webarena import config as webarena_config
from harness.web_fetch import config as web_fetch_config
from harness.web_fetch.wcxb_report_check import binary_from_env

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("VALUE=plain", "plain"),
        ("VALUE = spaced ", "spaced"),
        ('VALUE="double"', "double"),
        ("VALUE='single'", "single"),
        # The three hand-written parsers ignored or misnamed this form.
        ("export VALUE=exported", "exported"),
        # They also kept the comment in the value, as python-dotenv callers did not.
        ("VALUE=token # note", "token"),
        ("VALUE=token#fragment", "token#fragment"),
        ('VALUE="token # kept"', "token # kept"),
        # Quoted escapes follow python-dotenv; the hand-written parsers kept them literal.
        (r'VALUE="line\nbreak"', "line\nbreak"),
        (r"VALUE='line\nliteral'", r"line\nliteral"),
        ("VALUE=", ""),
        # Values are data. python-dotenv's default would expand the second form.
        ("VALUE=$(touch marker)", "$(touch marker)"),
        ("VALUE=${HOME}", "${HOME}"),
    ],
)
def test_value_syntax(tmp_path, line, expected):
    path = tmp_path / ".env"
    path.write_text(line + "\n", encoding="utf-8")
    assert parse_env_file(path) == {"VALUE": expected}


@pytest.mark.parametrize("line", ["VALUE=\"mismatched'", "VALUE='mismatched\""])
def test_mismatched_quotes_are_dropped_not_repaired_or_kept(tmp_path, line):
    # catalog_runner's strip("\"'") repaired this to `mismatched`; Web Fetch and public
    # WebArena passed the quotes on. Dropping it makes the caller report a missing value.
    path = tmp_path / ".env"
    path.write_text(line + "\nAFTER=kept\n", encoding="utf-8")
    assert parse_env_file(path) == {"AFTER": "kept"}


def test_an_unterminated_quote_also_drops_a_following_quoted_assignment(tmp_path):
    # Fails closed: neither name gets a corrupted value, and the caller reports them as missing.
    path = tmp_path / ".env"
    path.write_text('BROKEN="unterminated\nNEXT_KEY="value"\nAFTER=kept\n', encoding="utf-8")
    assert parse_env_file(path) == {"AFTER": "kept"}


def test_last_assignment_of_a_name_wins(tmp_path):
    # Web Fetch credentials and catalog_runner already behaved this way; setdefault
    # made other Web Fetch names and all public WebArena names keep the first.
    path = tmp_path / ".env"
    path.write_text("VALUE=first\nVALUE=second\n", encoding="utf-8")
    assert parse_env_file(path) == {"VALUE": "second"}


def test_lines_without_a_valid_assignment_are_ignored(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\n\nNO_ASSIGNMENT\nINVALID KEY=nope\n1DIGIT=nope\ndotted.name=nope\nGOOD=yes\n",
        encoding="utf-8",
    )
    assert parse_env_file(path) == {"GOOD": "yes"}


def test_missing_file_raises_and_home_is_expanded(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError):
        parse_env_file(tmp_path / "absent.env")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "home.env").write_text("VALUE=home\n", encoding="utf-8")
    assert parse_env_file("~/home.env") == {"VALUE": "home"}


SHARED_SYNTAX = "A='quoted'\nexport B=exported\nC=token # note\nD=${HOME}\nE=1\nE=2\nBROKEN=\"x'\n"
SHARED_VALUES = {"A": "quoted", "B": "exported", "C": "token", "D": "${HOME}", "E": "2"}


def test_every_loader_reads_the_same_values(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(SHARED_SYNTAX, encoding="utf-8")
    assert catalog_runner._load_env_values(path) == SHARED_VALUES
    assert _load_repo_env(tmp_path) == {
        **SHARED_VALUES,
        **{key: os.environ[key] for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "COWAGENT_REPO")
           if os.environ.get(key)},
    }
    for load in (web_fetch_config.load_env_file, webarena_config.load_runtime_env):
        for key in SHARED_VALUES:
            monkeypatch.delenv(key, raising=False)
        load(path)
        assert {key: os.environ[key] for key in SHARED_VALUES} == SHARED_VALUES


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CHROMIUM_BINARY=/bin/chrome\n", "/bin/chrome"),
        # This reader stopped at the first line, kept comments, and could not see export.
        ("CHROMIUM_BINARY=/bin/old\nCHROMIUM_BINARY=/bin/new\n", "/bin/new"),
        ("CHROMIUM_BINARY=/bin/chrome # note\n", "/bin/chrome"),
        ("export CHROMIUM_BINARY=/bin/chrome\n", "/bin/chrome"),
    ],
)
def test_wcxb_visual_check_selects_the_binary_the_shared_parser_reads(tmp_path, text, expected):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    assert binary_from_env(path) == expected == parse_env_file(path)["CHROMIUM_BINARY"]


@pytest.mark.parametrize("text", ["", "CHROMIUM_BINARY=\n", "CHROMIUM_BINARY=\"/bin/chrome'\n"])
def test_wcxb_visual_check_reports_an_unreadable_binary_as_missing(tmp_path, text):
    # strip('"').strip("'") repaired the mismatched quotes in the last form.
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="CHROMIUM_BINARY is missing"):
        binary_from_env(path)


def test_caller_policies_stay_outside_the_parser(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("WEBARENA_REDDIT_USERNAME=from-file\nEXA_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("WEBARENA_REDDIT_USERNAME", "exported")
    monkeypatch.setenv("EXA_API_KEY", "exported")
    webarena_config.load_runtime_env(path)
    assert os.environ["WEBARENA_REDDIT_USERNAME"] == "exported"
    assert os.environ["EXA_API_KEY"] == "exported"
    web_fetch_config.load_env_file(path, override_credentials=True)
    assert os.environ["WEBARENA_REDDIT_USERNAME"] == "exported"
    assert os.environ["EXA_API_KEY"] == "from-file"
    with pytest.raises(ValueError, match="repository .env"):
        web_fetch_config.load_env_file(path, credentials_allowed=False)
    symlink = tmp_path / "link.env"
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match="regular file"):
        catalog_runner._load_env_values(symlink)


def test_only_the_shared_module_parses_env_files():
    forbidden = re.compile(r"^\s*(?:from|import) dotenv\b|\"=\" not in (?:raw_)?line", re.M)
    allowed = {"src/harness/env_file.py"}
    sources = [*(ROOT / "src/harness").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]
    found = [str(path.relative_to(ROOT)) for path in sources
             if str(path.relative_to(ROOT)) not in allowed
             and forbidden.search(path.read_text("utf-8"))]
    assert found == []
    assert forbidden.search('if "=" not in line:')
    assert forbidden.search("from dotenv import dotenv_values")
