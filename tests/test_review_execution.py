"""Review cache reuse must refer to the same frozen inputs, never just the same IDs."""

import hashlib
import json
from unittest.mock import patch

import pytest

from harness.web_fetch.review_execution import anomaly_main, review_main


def fixture(root):
    evidence = root / "evidence"
    evidence.mkdir()
    (evidence / "answer.txt").write_bytes(b"")
    dataset = {
        "comparison": {"require_independent_result_review": True, "require_anomaly_review": True},
        "cases": [{"id": "a", "track": "web", "task_kind": "body"}],
    }
    row = {
        "attempt_id": "custom__a__r001",
        "provider": "custom",
        "case_id": "a",
        "artifact_path": "answer.txt",
        "ok": True,
        "status_code": 200,
        "metrics": {"content_pass": False, "content_sha256": hashlib.sha256(b"").hexdigest()},
    }
    (evidence / "dataset.json").write_text(json.dumps(dataset))
    (evidence / "results.jsonl").write_text(json.dumps(row) + "\n")
    update_prior(evidence)
    return evidence


def update_prior(evidence):
    review = {
        "schema_version": 1,
        "reviewer": {"agent": "synthetic control"},
        "bindings": {
            name + "_sha256": hashlib.sha256((evidence / filename).read_bytes()).hexdigest()
            for name, filename in [("dataset", "dataset.json"), ("results", "results.jsonl")]
        },
        "review_protocol": {"scope": "all", "machine_judgments_withheld": True},
        "attempts": [
            {
                "attempt_id": "custom__a__r001",
                "machine_verdict": "fail",
                "agent_verdict": "fail",
                "final_verdict": "fail",
            }
        ],
    }
    (evidence / "result-review.json").write_text(json.dumps(review))


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("phase", ["blind", "anomaly"])
@pytest.mark.parametrize("change", ["dataset", "results", "model", "artifact"])
def test_resume_rejects_changed_context_before_overwriting_work(tmp_path, phase, change):
    evidence = fixture(tmp_path)
    work = tmp_path / "work"
    main = review_main if phase == "blind" else anomaly_main
    argv = [
        "review",
        "--evidence",
        str(evidence),
        "--work",
        str(work),
        "--model",
        "synthetic-model-a",
        "--reviewer",
        "internal",
        "--prepare-only",
    ]
    with (
        patch("sys.argv", argv),
        patch("subprocess.run", side_effect=AssertionError("No reviewer calls")),
    ):
        main()
        main()  # Exact-input preparation is resumable.
    before = snapshot(work)
    if change == "dataset":
        document = json.loads((evidence / "dataset.json").read_text())
        document["cases"][0]["required_facts"] = ["new independent requirement"]
        (evidence / "dataset.json").write_text(json.dumps(document))
        update_prior(evidence)
    elif change == "results":
        row = json.loads((evidence / "results.jsonl").read_text())
        row["elapsed_ms"] = 123
        (evidence / "results.jsonl").write_text(json.dumps(row) + "\n")
        update_prior(evidence)
    elif change == "model":
        argv[argv.index("synthetic-model-a")] = "synthetic-model-b"
    else:
        (evidence / "answer.txt").write_text("changed answer")
    with (
        patch("sys.argv", argv),
        patch("subprocess.run", side_effect=AssertionError("No reviewer calls")),
    ):
        with pytest.raises(ValueError):
            main()
    assert snapshot(work) == before


def completed_blind_batch(work):
    (work / "batch-001.json").write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "attempt_id": "custom__a__r001",
                        "verdict": "fail",
                        "evidence": "missing: required article body; observed: empty saved answer",
                        "anomalies": [],
                    }
                ]
            }
        )
    )


def test_cached_blind_judgment_is_retained_but_changed_packet_is_rejected(tmp_path):
    evidence = fixture(tmp_path)
    work = tmp_path / "work"
    argv = [
        "review",
        "--evidence",
        str(evidence),
        "--work",
        str(work),
        "--model",
        "synthetic",
        "--reviewer",
        "internal",
    ]
    with patch("sys.argv", argv + ["--prepare-only"]):
        review_main()
    completed_blind_batch(work)
    with (
        patch("sys.argv", argv),
        patch("subprocess.run", side_effect=AssertionError("No reviewer calls")),
    ):
        review_main()
    result = json.loads((work / "result-review.json").read_text())
    assert result["attempts"][0]["agent_verdict"] == "fail"
    packet = work / "batch-001-input.json"
    document = json.loads(packet.read_text())
    document["cases"][0]["required_facts"] = ["different frozen task"]
    packet.write_text(json.dumps(document))
    before = snapshot(work)
    with patch("sys.argv", argv), pytest.raises(ValueError, match="input packet changed"):
        review_main()
    assert snapshot(work) == before


def test_partial_provider_review_cannot_replace_a_complete_installed_review(tmp_path):
    evidence = fixture(tmp_path)
    row = json.loads((evidence / "results.jsonl").read_text())
    second = {**row, "provider": "other", "attempt_id": "other__a__r001"}
    (evidence / "results.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(second) + "\n")
    before = (evidence / "result-review.json").read_bytes()
    work = tmp_path / "work"
    argv = [
        "review",
        "--evidence",
        str(evidence),
        "--work",
        str(work),
        "--model",
        "synthetic",
        "--reviewer",
        "internal",
        "--provider",
        "custom",
    ]
    with patch("sys.argv", argv + ["--prepare-only"]):
        review_main()
    completed_blind_batch(work)
    with patch("sys.argv", argv + ["--install"]), pytest.raises(ValueError, match="every attempt"):
        review_main()
    assert (evidence / "result-review.json").read_bytes() == before


@pytest.mark.parametrize("phase", ["blind", "anomaly"])
def test_legacy_unbound_workspace_is_preserved_and_rejected(tmp_path, phase):
    evidence = fixture(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    (work / "batch-001.json").write_text('{"historical": "judgment"}')
    before = snapshot(work)
    argv = [
        "review",
        "--evidence",
        str(evidence),
        "--work",
        str(work),
        "--model",
        "synthetic",
        "--reviewer",
        "internal",
        "--prepare-only",
    ]
    main = review_main if phase == "blind" else anomaly_main
    with patch("sys.argv", argv), pytest.raises(ValueError, match="Unbound review workspace"):
        main()
    assert snapshot(work) == before


@pytest.mark.parametrize("phase", ["blind", "anomaly"])
@pytest.mark.parametrize(
    "option,value",
    [("workers", "0"), ("timeout-seconds", "0"), ("protocol-retries", "-1"), ("batch", "0")],
)
def test_invalid_execution_limits_fail_before_work_or_calls(tmp_path, phase, option, value):
    evidence = fixture(tmp_path)
    work = tmp_path / "work"
    if option == "batch":
        option = "batch-cases" if phase == "blind" else "groups-per-batch"
    argv = [
        "review",
        "--evidence",
        str(evidence),
        "--work",
        str(work),
        "--model",
        "synthetic",
        "--reviewer",
        "internal",
        "--" + option,
        value,
    ]
    main = review_main if phase == "blind" else anomaly_main
    with patch("sys.argv", argv), patch("subprocess.run", side_effect=AssertionError("No calls")):
        with pytest.raises(SystemExit):
            main()
    assert not work.exists()


@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_body_evidence_accepts_concrete_two_character_chinese_table_headings(verdict):
    from harness.web_fetch.review_execution import validate_review_evidence
    headings = "编号 | 姓名 | 状态 | 邮箱 | 性别 | 年龄 | 城市 | 签名 | 时间 | 操作"
    validate_review_evidence({"attempt_id": "table", "verdict": verdict,
        "evidence": "beginning: " + headings + "; middle: 姓名 | 邮箱; end: 城市 | 签名",
        "anomalies": []}, "body")


@pytest.mark.parametrize("value", [
    "占位", "占位符", "证据 | 内容 | 正文", "开头 | 中间 | 结尾",
    "得分 90 | 覆盖率 80% | 阈值 60%", "评分 | 分数 | 通过率 | 百分比",
    "90 | 80% | 60%", "score 90 | coverage 80% | threshold 60%",
    "<actual passage>", "符合要求 | 达到阈值",
])
def test_body_evidence_still_rejects_placeholders_and_scoring_only(value):
    from harness.web_fetch.review_execution import validate_review_evidence
    with pytest.raises(ValueError, match="direct artifact evidence"):
        validate_review_evidence({"attempt_id": "placeholder", "verdict": "pass",
            "evidence": "; ".join(label + ": " + value for label in ("beginning", "middle", "end")),
            "anomalies": []}, "body")


@pytest.mark.parametrize("headings", [
    ("الرئيسية", "مسلسلات وأفلام", "رفض الكل تأكيد خياراتي"),
    ("Главная", "Каталог фильмов", "Настройки подписки"),
    ("मुखपृष्ठ", "नाम", "संपर्क"),
    ("ראשי", "סרטים", "הגדרות"),
])
def test_body_evidence_accepts_direct_unicode_script_observations(headings):
    from harness.web_fetch.review_execution import validate_review_evidence

    validate_review_evidence({
        "attempt_id": "unicode", "verdict": "pass", "anomalies": [],
        "evidence": "; ".join(label + ": " + heading for label, heading in
                              zip(("beginning", "middle", "end"), headings)),
    }, "body")


@pytest.mark.parametrize("value", [
    "٩٠ | ٨٠٪ | ٦٠٪", "９０ | ８０% | ６０%", "① | ½ | Ⅳ",
    "النتيجة ٩٠ | التغطية ٨٠٪ | العتبة ٦٠٪", "٩٠ بالمئة | ٨٠ نقاط",
    "ناجح | موجود | متطلبات", "دليل | محتوى | نتيجة", "<actual passage>",
    "نَتِيجَة | تَغْطِيَة | عَتَبَة",
])
def test_unicode_body_evidence_rejects_numbers_generic_terms_and_placeholders(value):
    from harness.web_fetch.review_execution import validate_review_evidence

    with pytest.raises(ValueError, match="direct artifact evidence"):
        validate_review_evidence({
            "attempt_id": "unicode-placeholder", "verdict": "pass", "anomalies": [],
            "evidence": "; ".join(label + ": " + value for label in
                                  ("beginning", "middle", "end")),
        }, "body")
