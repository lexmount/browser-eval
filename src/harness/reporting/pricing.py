"""Convert declared usage and unit prices into comparable workload estimates."""

import math


def usd_per_thousand(calls, components):
    """Sum explicit quantity intervals; never infer missing billing usage as zero."""
    if type(calls) is not int or calls <= 0 or not components:
        raise ValueError("Cost conversion requires calls and billable components")
    total = [0.0, 0.0]
    for component in components:
        quantity = component["quantity"]
        bounds = quantity if isinstance(quantity, list) else [quantity, quantity]
        rate = component["usd_per_unit"]
        rates = rate if isinstance(rate, list) else [rate, rate]
        if (
            len(bounds) != 2
            or len(rates) != 2
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < 0
                for v in [*bounds, *rates]
            )
            or bounds[0] > bounds[1]
            or rates[0] > rates[1]
        ):
            raise ValueError("Invalid usage or rate interval")
        for i, amount in enumerate(bounds):
            total[i] += amount * rates[i] * 1000 / calls
    return total
