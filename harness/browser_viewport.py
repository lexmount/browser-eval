"""Apply and verify the shared browser viewport, including existing CDP pages."""

from typing import Any

BENCHMARK_VIEWPORT = {"width": 1280, "height": 900}


def validate_viewport_receipt(receipt: Any) -> None:
    """Reject configured-only, missing or mismatched qualification evidence."""
    if not isinstance(receipt, dict) or receipt.get("status") != "verified":
        raise ValueError("qualification requires a verified viewport receipt")
    for field in ("requested", "actual"):
        dimensions = receipt.get(field)
        if (
            not isinstance(dimensions, dict)
            or dimensions != BENCHMARK_VIEWPORT
            or any(type(dimensions.get(key)) is not int for key in BENCHMARK_VIEWPORT)
        ):
            raise ValueError("qualification viewport differs from the shared contract")


def set_verified_viewport(page: Any) -> dict[str, Any]:
    requested = dict(BENCHMARK_VIEWPORT)
    page.set_viewport_size(requested)
    actual = page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    if actual != requested:
        raise RuntimeError(
            f"Browser viewport contract mismatch: expected {requested}, got {actual}"
        )
    return {"requested": requested, "actual": actual, "status": "verified"}
