"""Shared, evidence-derived resource comparison for the report and interpretation."""
from __future__ import annotations

from html import escape

from bs4 import BeautifulSoup

from harness.numbers import positive_number
from harness.reporting.presentation import check_presentation
from harness.web_fetch.methods import report_method
from harness.web_fetch.report_components import bilingual_text
from harness.web_fetch.report_i18n import pair, t

GROUPS = (
    (t('cpu_and_compute', 'CPU and compute'), (
        (t('mean_cpu_time', 'Mean CPU time'), 'cpu_ms', 'mean', 'ms', True),
        (t('cpu_time_p50', 'CPU time P50'), 'cpu_ms', 'p50', 'ms', True),
        (t('cpu_time_p95', 'CPU time P95'), 'cpu_ms', 'p95', 'ms', True),
        (t('mean_cpu_as_one_core_utilization', 'Mean CPU as one-core utilization'), 'cpu_average_utilization_pct', 'mean', 'pct', False),
        (t('within_task_cpu_p95', 'Within-task CPU P95'), 'cpu_sampled_utilization_p95_pct', 'mean', 'pct', False),
    )),
    (t('memory', 'Memory'), (
        (t('mean_memory', 'Mean memory'), 'memory_mean_bytes', 'mean', 'mib', True),
        (t('within_task_memory_p95', 'Within-task memory P95'), 'memory_p95_bytes', 'mean', 'mib', True),
        (t('mean_peak_memory', 'Mean peak memory'), 'memory_peak_bytes', 'mean', 'mib', True),
        (t('peak_memory_p95', 'Peak-memory P95'), 'memory_peak_bytes', 'p95', 'mib', True),
        (t('mean_memory_integral', 'Mean memory integral'), 'memory_mib_seconds', 'mean', 'mib_s', True),
    )),
    (t('network', 'Network'), (
        (t('mean_received', 'Mean received'), 'network_rx_bytes', 'mean', 'mib', True),
        (t('received_p95', 'Received P95'), 'network_rx_bytes', 'p95', 'mib', True),
        (t('mean_sent', 'Mean sent'), 'network_tx_bytes', 'mean', 'kib', True),
        (t('sent_p95', 'Sent P95'), 'network_tx_bytes', 'p95', 'kib', True),
        (t('mean_receive_rate', 'Mean receive rate'), 'network_rx_bytes_per_second', 'mean', 'kib_s', False),
        (t('mean_send_rate', 'Mean send rate'), 'network_tx_bytes_per_second', 'mean', 'kib_s', False),
    )),
)


def _format(value, unit):
    if unit == 'ms':
        return f'{value:,.0f} ms'
    if unit == 'pct':
        return f'{value:.1f}%'
    if unit == 'mib':
        return f'{value / 1048576:,.1f} MiB'
    if unit == 'kib':
        return f'{value / 1024:,.1f} KiB'
    if unit == 'mib_s':
        return f'{value:,.1f} MiB·s'
    if unit == 'kib_s':
        return f'{value / 1024:,.1f} KiB/s'
    raise ValueError(f'Unknown resource unit: {unit}')


def _metric(indexed, provider, key, statistic):
    return indexed[provider]['metrics'][key][statistic]


def render_resource_comparison(summary, providers):
    """Render grouped comparisons and explicit, mechanically checkable winners."""
    if summary is None:
        return (('<p>' + t('the_current_evidence_has_no_independent_client', 'The current evidence has no independent client resource measurement; CPU or memory is not inferred from latency.') + '</p>'),
                {'winner': None, 'winner_count': 0, 'ranked_metric_count': 0})
    indexed = {row['provider']: row for row in summary['rows']}
    def provider_name(provider):
        # This independent cohort must not borrow versions from the main matrix.
        label = report_method(provider).get('label', {})
        return label.get('zh', provider)
    declared = summary.get('providers', list(indexed))
    local = [provider for provider in providers if provider in declared]
    missing = sorted(set(local) - set(indexed))
    if missing:
        raise ValueError('Resource cohort is missing local providers: ' + ', '.join(missing))
    if not local:
        raise ValueError('Resource comparison has no selected measured providers')

    winners = []
    for _, metrics in GROUPS:
        for _, key, statistic, _, lower_is_better in metrics:
            if lower_is_better:
                best = min(_metric(indexed, provider, key, statistic) for provider in local)
                winners.append((key, statistic, [provider for provider in local
                                                if _metric(indexed, provider, key, statistic) == best]))
    wins = {provider: sum(provider in metric_winners for _, _, metric_winners in winners)
            for provider in local}
    leader = max(local, key=lambda provider: wins[provider])
    leaders = [provider for provider in local if wins[provider] == wins[leader]]
    leader_tied = len(leaders) > 1
    leader_label = '、'.join(provider_name(provider) for provider in leaders)

    def lowest(key, statistic):
        best = min(_metric(indexed, provider, key, statistic) for provider in local)
        labels = '、'.join(provider_name(provider) for provider in local
                          if _metric(indexed, provider, key, statistic) == best)
        return best, labels

    cpu, cpu_label = lowest('cpu_ms', 'mean')
    memory, memory_label = lowest('memory_peak_bytes', 'mean')
    rx, rx_label = lowest('network_rx_bytes', 'mean')
    tx, tx_label = lowest('network_tx_bytes', 'mean')
    network_label = (rx_label if rx_label == tx_label else
                     (t('received', 'Received') + f' {rx_label} / 发送 {tx_label}'))
    headline = (
        (f'''<div class="resource-leader" data-resource-winner="{escape(','.join(leaders))}"><div><span class="resource-kicker">{(t('resource_efficiency_co_leaders_in_this_run', 'Resource-efficiency co-leaders in this run') if leader_tied else t('resource_efficiency_leader_in_this_run', 'Resource-efficiency leader in this run'))}</span><strong>{escape(leader_label)}</strong></div><p><b>{wins[leader]}/{len(winners)}</b> ''' + t('lower_is_better_resource_metrics_resource_results', 'lower-is-better resource metrics. Resource results and task pass rate are judged separately and are not combined into a composite score.') + '</p></div>')
    )
    cards = (
        ('<div class="resource-key-grid"><article><span>' + t('cpu_leader', 'CPU leader') + f"</span><strong>{escape(cpu_label)}</strong><b>{_format(cpu, 'ms')}</b><small>" + t('mean_cpu_time', 'Mean CPU time') + '</small></article><article><span>' + t('memory_leader', 'Memory leader') + f"</span><strong>{escape(memory_label)}</strong><b>{_format(memory, 'mib')}</b><small>" + t('mean_peak_memory', 'Mean peak memory') + '</small></article><article><span>' + t('network_traffic_leader', 'Network-traffic leader') + f"</span><strong>{escape(network_label)}</strong><b>{_format(rx, 'mib')} ↓ / {_format(tx, 'kib')} ↑</b><small>" + t('mean_received_mean_sent', 'Mean received / mean sent') + '</small></article></div>')
    )

    sections = []
    for group_index, (group_name, metrics) in enumerate(GROUPS, 1):
        rows = []
        for row_index, (metric_name, key, statistic, unit, lower_is_better) in enumerate(metrics, 1):
            values = {provider: _metric(indexed, provider, key, statistic) for provider in local}
            best = min(values.values()) if lower_is_better else None
            cells = []
            for provider in local:
                rendered = escape(_format(values[provider], unit))
                if lower_is_better and values[provider] == best and list(values.values()).count(best) == 1:
                    rendered = ((f'<strong class="resource-best comparison-winner" data-resource-metric-winner="{escape(provider)}">{rendered}<span>' + t('lowest', 'Lowest') + '</span></strong>'))
                cells.append(f'<td data-resource-provider="{escape(provider)}" '
                    f'data-resource-key="{key}" data-resource-statistic="{statistic}" '
                    f'data-metric="resource-{key}-{statistic}" '
                    f'data-metric-direction="{"min" if lower_is_better else "neutral"}" '
                    f'data-sort="{values[provider]}">' + rendered + '</td>')
            rows.append(f'<tr><td>{row_index}</td><td class="resource-metric">' + escape(metric_name) + '</td>'
                        + ''.join(cells) + '</tr>')
        header = ''.join('<th scope="col">' + escape(provider_name(provider)) + '</th>'
                         for provider in local)
        sections.append(
            (f"""<section class="resource-group"><h3>{escape(group_name)}</h3><p>{(t('green_marks_the_lowest_resource_use_for', 'Green marks the lowest resource use for that metric.') if group_name != t('network', 'Network') else t('traffic_totals_mark_lower_values_rates_only', 'Traffic totals mark lower values; rates only describe transfer behavior and do not determine a winner.'))}</p><div class="resource-table" role="region" tabindex="0" aria-label="resource comparison group {group_index}"><table><thead><tr><th scope="col">""" + t('no', 'No.') + '</th><th scope="col">' + t('metric', 'Metric') + f"</th>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"))

    count = len(summary['planned_cases'])
    completions = '，'.join(provider_name(provider) + ' ' +
        str(indexed[provider].get('successful_attempts', '—')) + '/' + str(count)
        for provider in local)
    sample = ((f'<p class="resource-sample" data-resource-population="all_declared_calls" data-resource-cases="{count}">资源表覆盖预先确定的全部 {count} 个普通网页，包含失败调用。这组任务的通过数：{escape(completions)}。各项平均值、P50、P95 使用同一完整范围。</p><p>' + t('tasks_passed_by_every_listed_configuration', 'Tasks passed by every listed configuration:') + f" <strong>{len(summary['common_pass_cases'])}/{count}</strong>" + t('this_intersection_is_diagnostic_only_and_does', "; this intersection is diagnostic only and does not select the resource-table population. Conditional statistics for each method's successful calls are retained in the resource evidence.") + '</p>'))
    interval = summary.get('environment', {}).get('sample_interval_ms')
    sampling_note = bilingual_text(
        t('cpu_and_network_totals_come_from_cumulative', 'CPU and network totals come from cumulative counters; memory and instantaneous rates are sampled every {interval:g} ms and may miss shorter spikes.', interval=interval)
        if positive_number(interval) else t('cpu_and_network_totals_come_from_cumulative_2', 'CPU and network totals come from cumulative counters; the sampling interval was not recorded, so short-spike coverage cannot be judged.'),
        f'CPU and network totals come from cumulative counters. Memory and instantaneous rates are sampled every {interval:g} ms and may miss shorter spikes.'
        if positive_number(interval) else 'CPU and network totals come from cumulative counters. The sample interval was not recorded, so short-lived peak coverage is unknown.')
    unmeasured = [provider for provider in providers if provider not in declared]
    scope_note = bilingual_text(*pair('each_call_uses_a_disposable_linux_client', 'Each call uses a disposable Linux client container, cgroup v2 and a dedicated network namespace; the host controller and container lifecycle are excluded. Unobservable remote CPU, memory and upstream traffic cannot count as local resource measurements.'))
    if unmeasured:
        scope_note += bilingual_text(
            (t('methods_outside_this_resource_measurement', 'Methods outside this resource measurement:') + f"{'、'.join((provider_name(provider) for provider in unmeasured))}。"),
            'Methods outside this resource cohort: '+ ', '.join(
                report_method(provider).get('label', {}).get('en', provider) for provider in unmeasured)+'.')
    caveat = ('<p class="callout"><b>'+bilingual_text(*pair('comparison_scope', 'Comparison scope: '))+
              '</b>'+scope_note+'</p><p class="source-note">'+sampling_note+
              bilingual_text(*pair('cpu_utilization_and_transfer_rates_are_not', 'CPU utilization and transfer rates are not ranked as lower-is-better. See Methodology and Reproduction for full methods and per-call evidence.'))+'</p>')
    return headline + cards + sample + '<div class="resource-groups">' + ''.join(sections) + '</div>' + caveat, {
        'winner': leader,
        'winner_label': leader_label,
        'winner_count': wins[leader],
        'ranked_metric_count': len(winners),
        'winner_tied': leader_tied,
    }


def check_resource_presentation(html, summary):
    """Verify complete-population values and unique extrema at release time."""
    soup = check_presentation(html)
    if summary.get('primary_population') != 'all_declared_calls':
        raise ValueError('Resource ranking must retain every declared call')
    count = len(summary['planned_cases'])
    sample = soup.select('[data-resource-population="all_declared_calls"]')
    if len(sample) != 1 or sample[0].get('data-resource-cases') != str(count):
        raise ValueError('Resource comparison has the wrong population or denominator')
    headline = soup.select('[data-resource-winner]')
    expected_html, _ = render_resource_comparison(summary, summary['providers'])
    expected_leaders = BeautifulSoup(expected_html, 'html.parser').select_one(
        '[data-resource-winner]')['data-resource-winner']
    if len(headline) != 1 or headline[0]['data-resource-winner'] != expected_leaders:
        raise ValueError('Resource leader differs from measured metric winners')
    expected = {(row['provider'], key, statistic): row['metrics'][key][statistic]
        for row in summary['rows'] for _, metrics in GROUPS
        for _, key, statistic, _, _ in metrics}
    cells = soup.select('td[data-resource-provider][data-resource-key][data-resource-statistic]')
    observed = {}
    for cell in cells:
        identity = (cell['data-resource-provider'], cell['data-resource-key'], cell['data-resource-statistic'])
        if identity in observed or cell.get('data-sort') is None:
            raise ValueError('Resource comparison contains duplicate or unavailable measurements')
        direction = next('min' if lower else 'neutral' for _, metrics in GROUPS
            for _, key, statistic, _, lower in metrics if identity[1:] == (key, statistic))
        if cell.get('data-metric-direction') != direction or cell.get('data-metric') != (
                'resource-' + identity[1] + '-' + identity[2]):
            raise ValueError('Resource metric direction or identity changed')
        observed[identity] = float(cell['data-sort'])
    if observed != expected or any(row['attempts'] != count or any(
            value['n'] != count for value in row['metrics'].values()) for row in summary['rows']):
        raise ValueError('Resource comparison differs from its complete measured cohort')
