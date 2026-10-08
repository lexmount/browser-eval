"""Shared, evidence-derived resource comparison for the report and interpretation."""
from __future__ import annotations

from html import escape

PROVIDER_NAMES = {
    'chromium': 'Chromium',
    'lightpanda': 'Lightpanda',
    'moli': 'Moli 1.1.1',
    'obscura': 'Obscura',
}


GROUPS = (
    ('CPU 与计算', (
        ('平均 CPU 时间', 'cpu_ms', 'mean', 'ms', True),
        ('CPU 时间 P50', 'cpu_ms', 'p50', 'ms', True),
        ('CPU 时间 P95', 'cpu_ms', 'p95', 'ms', True),
        ('平均 CPU 占单核', 'cpu_average_utilization_pct', 'mean', 'pct', False),
        ('任务内 CPU P95', 'cpu_sampled_utilization_p95_pct', 'mean', 'pct', False),
    )),
    ('内存', (
        ('平均内存', 'memory_mean_bytes', 'mean', 'mib', True),
        ('任务内存 P95', 'memory_p95_bytes', 'mean', 'mib', True),
        ('平均峰值内存', 'memory_peak_bytes', 'mean', 'mib', True),
        ('峰值内存 P95', 'memory_peak_bytes', 'p95', 'mib', True),
        ('平均内存占用量', 'memory_mib_seconds', 'mean', 'mib_s', True),
    )),
    ('网络', (
        ('平均接收', 'network_rx_bytes', 'mean', 'mib', True),
        ('接收 P95', 'network_rx_bytes', 'p95', 'mib', True),
        ('平均发送', 'network_tx_bytes', 'mean', 'kib', True),
        ('发送 P95', 'network_tx_bytes', 'p95', 'kib', True),
        ('平均接收速率', 'network_rx_bytes_per_second', 'mean', 'kib_s', False),
        ('平均发送速率', 'network_tx_bytes_per_second', 'mean', 'kib_s', False),
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
        return ('<p>当前证据没有独立的客户端资源测量；不从耗时反推 CPU 或内存。</p>',
                {'winner': None, 'winner_count': 0, 'ranked_metric_count': 0})
    indexed = {row['provider']: row for row in summary['rows']}
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
    leader_label = '、'.join(PROVIDER_NAMES.get(provider, provider) for provider in leaders)

    def lowest(key, statistic):
        best = min(_metric(indexed, provider, key, statistic) for provider in local)
        labels = '、'.join(PROVIDER_NAMES.get(provider, provider) for provider in local
                          if _metric(indexed, provider, key, statistic) == best)
        return best, labels

    cpu, cpu_label = lowest('cpu_ms', 'mean')
    memory, memory_label = lowest('memory_peak_bytes', 'mean')
    rx, rx_label = lowest('network_rx_bytes', 'mean')
    tx, tx_label = lowest('network_tx_bytes', 'mean')
    network_label = (rx_label if rx_label == tx_label else
                     f'接收 {rx_label} / 发送 {tx_label}')
    headline = (
        '<div class="resource-leader" data-resource-winner="' + escape(','.join(leaders)) + '">'
        '<div><span class="resource-kicker">'
        + ('本轮资源效率并列领先' if leader_tied else '本轮资源效率领先') + '</span>'
        f'<strong>{escape(leader_label)}</strong></div>'
        f'<p><b>{wins[leader]}/{len(winners)}</b> 个可按低消耗比较的指标最低。'
        '资源结果与任务通过率分别判断，不合成总分。</p></div>'
    )
    cards = (
        '<div class="resource-key-grid">'
        f'<article><span>CPU 领先者</span><strong>{escape(cpu_label)}</strong><b>{_format(cpu, "ms")}</b><small>平均 CPU 时间</small></article>'
        f'<article><span>内存领先者</span><strong>{escape(memory_label)}</strong><b>{_format(memory, "mib")}</b><small>平均峰值内存</small></article>'
        f'<article><span>网络流量领先者</span><strong>{escape(network_label)}</strong><b>{_format(rx, "mib")} ↓ / {_format(tx, "kib")} ↑</b><small>平均接收 / 平均发送</small></article>'
        '</div>'
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
                if lower_is_better and values[provider] == best:
                    rendered = (f'<strong class="resource-best" data-resource-metric-winner="{escape(provider)}">'
                                f'{rendered}<span>本项最低</span></strong>')
                cells.append('<td>' + rendered + '</td>')
            rows.append(f'<tr><td>{row_index}</td><td class="resource-metric">' + escape(metric_name) + '</td>'
                        + ''.join(cells) + '</tr>')
        header = ''.join('<th scope="col">' + escape(PROVIDER_NAMES.get(provider, provider)) + '</th>'
                         for provider in local)
        sections.append(
            '<section class="resource-group"><h3>' + escape(group_name) + '</h3>'
            '<p>' + ('绿色标记表示该项消耗最低。' if group_name != '网络' else
                     '流量总量按低值标记；速率仅描述传输形态，不判赢家。') + '</p>'
            f'<div class="resource-table" role="region" tabindex="0" aria-label="resource comparison group {group_index}">'
            '<table><thead><tr><th scope="col">序号</th><th scope="col">指标</th>' + header + '</tr></thead><tbody>'
            + ''.join(rows) + '</tbody></table></div></section>')

    sample = (f'<p class="resource-sample">同题口径：所列配置共同通过 '
              f'<strong>{len(summary["common_pass_cases"])}/{len(summary["planned_cases"])}</strong> 个普通网页；'
              '失败任务不进入资源排名。</p>')
    caveat = ('<p class="callout"><b>可比边界：</b>每次调用使用一次性 Linux 客户端容器、cgroup v2 与独立网络命名空间；'
              '宿主控制器和容器启停不计入。Kitesurf 的远端浏览器 CPU、内存和远端上游流量不可见，'
              '因此不进入资源表或本地引擎资源排名。</p>'
              '<p class="source-note">CPU 与网络总量来自累计计数；内存与瞬时速率按 50 ms 采样，可能遗漏更短尖峰。'
              'CPU 占用、传输速率不按“越低越好”评选。完整方法与逐次证据见环境与复现页。</p>')
    return headline + cards + sample + '<div class="resource-groups">' + ''.join(sections) + '</div>' + caveat, {
        'winner': leader,
        'winner_label': leader_label,
        'winner_count': wins[leader],
        'ranked_metric_count': len(winners),
        'winner_tied': leader_tied,
    }
