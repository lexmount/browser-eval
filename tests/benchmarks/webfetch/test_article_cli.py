"""The standalone scorer must preserve the independent reference boundary."""
from .article_contract_fixtures import post_labels
import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("include_body,passed", [(True, True), (False, False)])
def test_cli_scores_fixed_posts_without_runtime_reference(tmp_path, include_body, passed):
    body = ("Orchard workers recorded every tree before sunrise. They inspected "
            "the irrigation channels and published the complete harvest schedule.")
    task = {
        "id": "post-feed", "name": "Article feed", "url": "https://example.test/feed",
        "category": "article", "track": "articles", "task_kind": "body", "expected_any": [],
        "acceptance_contract": {"version": 1, "schema": "body_text", "minimum_records": 0,
                                "page_patterns": ["Article feed"]},
        "source_benchmark": {"mode": "article_post_collection", "post_contract": {**post_labels(),
            "version": 2, "hosts": ["example.test"], "entry_path": r"/post/[0-9]+",
            "html_record_selector": "article", "html_permalink_selector": "a.time",
            "html_author_selector": ".author", "html_body_selector": ".body",
            "minimum_posts": 1,
        }},
    }
    (tmp_path / "task.json").write_text(json.dumps(task))
    (tmp_path / "answer.txt").write_text(
        "# Article feed\n\n## Alice [Today](https://example.test/post/1)\n\n" + (body if include_body else ""))
    command = [sys.executable, "-m", "lexbench_webfetch.cli", "score",
               "--task", str(tmp_path / "task.json"),
               "--response", str(tmp_path / "answer.txt")]
    result = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    scored = json.loads(result.stdout)
    assert scored["passed"] is passed
    assert scored["measured"] is True
    assert "article_reference_status" not in scored["metrics"]


@pytest.mark.parametrize("argument", ["--comments-reference", "--reference-context"])
def test_cli_rejects_removed_reference_arguments(tmp_path, argument):
    result = subprocess.run([
        sys.executable, "-m", "lexbench_webfetch.cli", "score",
        "--task", str(tmp_path / "unused-task.json"),
        "--response", str(tmp_path / "unused-answer.txt"),
        argument, str(tmp_path / "unused-reference.json"),
    ], cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_compile_articles_cli_and_output_protection(tmp_path, monkeypatch, capsys):
    from lexbench_webfetch import cli, article_dataset
    dataset = {"case_count": 1, "cases": [{"id": "synthetic"}]}
    def compile_candidate(evidence, definitions):
        assert evidence == tmp_path / "evidence"
        return dataset
    monkeypatch.setattr(article_dataset, "compile_dataset", compile_candidate)
    output = tmp_path / "candidate.json"
    monkeypatch.setattr(sys, "argv", ["lexbench-webfetch", "compile-articles",
        "--evidence-home", str(tmp_path / "evidence"), "--output", str(output)])
    cli.main()
    assert json.loads(output.read_text()) == dataset
    assert json.loads(capsys.readouterr().out)["case_count"] == 1
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert "already exists" in capsys.readouterr().err
    assert json.loads(output.read_text()) == dataset
    output.unlink()
    (tmp_path / "source-inputs.json").write_text("{}")
    with pytest.raises(SystemExit):
        cli.main()
    assert "immutable" in capsys.readouterr().err
    assert not output.exists()


def test_compile_articles_cli_rejects_missing_evidence(tmp_path):
    output = tmp_path / "candidate.json"
    result = subprocess.run([sys.executable, "-m", "lexbench_webfetch.cli",
        "compile-articles", "--evidence-home", str(tmp_path / "missing"),
        "--output", str(output)], text=True, capture_output=True)
    assert result.returncode == 1
    assert not output.exists()
