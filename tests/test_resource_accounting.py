from harness import resource_accounting
from harness.browser_use import metrics


def test_all_benchmarks_share_one_resource_accounting_contract():
    assert resource_accounting.ResourceSampler is metrics.ResourceSampler
    assert resource_accounting.DockerStatsResources is metrics.DockerStatsResources
    assert resource_accounting.delta_counters is metrics.delta_counters
