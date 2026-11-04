"""Static report controls for field, method, source and diagnosis drilldown."""

import json
from pathlib import Path

from harness.web_scraper.messages import message

ASSETS = Path(__file__).with_name("assets")
JS = (ASSETS / "report.js").read_text(encoding="utf-8")

CLIENT_MESSAGE_KEYS = (
    "records_qualified_reference",
    "showing_results",
    "audited_reference",
    "stages_client_transport",
    "compare_all_methods_a24f79dd",
    "complete_request_response_and_repair_history",
    "current_call_latency",
    "exact_error",
    "failure_repair",
    "field",
    "field_labels_availability",
    "field_labels_OutOfStock",
    "field_labels_price",
    "field_labels_sku",
    "full_result",
    "load_failed_close_and_retry_or_use_the_raw_file",
    "loading_complete_evidence",
    "no_accepted_value",
    "no_recognizable_product_fields_returned",
    "null",
    "original_call_and_score_retained",
    "outcome_243a87d2",
    "outcomes_correct",
    "outcomes_missing",
    "outcomes_unexpected",
    "outcomes_wrong",
    "outcomes_partial",
    "outcomes_invalid",
    "outcomes_unavailable",
    "output",
    "stages_provider_schema",
    "reference",
    "reviewed_cause",
    "see_field_judgments_and_complete_evidence_below",
    "source_and_annotations_cf191383",
    "source_evidence_and_audited_reference",
    "stage_fb689e6a",
    "stages_fields",
    "stages_model_capacity",
    "stages_navigation",
    "stages_provider_fetch",
    "stages_provider_response",
    "structured_fields_returned_inspect_field_differences",
    "treatment",
)
JS = JS.replace(
    "__REPORT_MESSAGES__",
    json.dumps(
        {
            language: {key: message(key, language, shown="{shown}") for key in CLIENT_MESSAGE_KEYS}
            for language in ("en", "zh")
        },
        ensure_ascii=False,
    ),
)
