"""Scorer controls added after the historical dynamic-audit test inventory froze."""

from harness.web_fetch.stable_tasks import evaluate_stable_task


def test_title_with_angle_bracket_does_not_expose_hidden_html_anchor():
    rules = {
        "version": 1,
        "schema": "body_text",
        "minimum_records": 0,
        "stable_text_anchors": [{"id": "anchor", "alternatives": ["Secret Anchor"]}],
    }
    hidden = ("A < B\n<html><body><script>Secret Anchor</script>"
              "<p>Visible content</p></body></html>")
    visible = hidden.replace("<script>Secret Anchor</script>", "<p>Secret Anchor</p>")
    assert evaluate_stable_task(hidden, rules)["stable_task_pass"] is False
    assert evaluate_stable_task(visible, rules)["stable_task_pass"] is True
