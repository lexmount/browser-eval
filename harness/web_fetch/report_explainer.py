"""One authored article; all figures use the report builder's normalized inputs."""
import hashlib
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote

from .metrics import evaluate_content, task_passed
from .report_style import STYLE
from .safety import contained_path, safe_identifier


TITLE = '网页抓取成功之后，内容离可用还有多远'
HUB = 'https://artifact-hub.dev.lexmount.net/api/preview/UeZE1vfcEOrM'
ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).parent / 'templates/interpretation.html'
TRACK_NAMES = {'web': '网页正文', 'document': '文档文字', 'image': '图片',
               'video': '视频', 'challenge': '复杂热门网站', 'dynamic': '动态内容补充实验'}
PROVIDER_NAMES = {'cowagent-built-in': 'CowAgent', 'moli-webfetch': 'Moli',
                  'cloudflare-markdown': 'Cloudflare Markdown', 'exa': 'Exa',
                  'langchain-webbase': 'LangChain WebBaseLoader', 'firecrawl': 'Firecrawl',
                  'crawl4ai': 'Crawl4AI', 'openclaw-built-in': 'OpenClaw',
                  'deepseek-harness-built-in': 'DeepSeek Harness', 'tavily': 'Tavily',
                  'lexmount-global': 'Lexmount 国际', 'lexmount-cn': 'Lexmount 中国区'}


def evidence_url(path):
    contained_path(ROOT, path)
    if Path(path).suffix != '.html':
        return HUB + '#page=guide%2Findex.html&anchor=evidence'
    return HUB + '#page=' + quote(str(path), safe='')


def _link(path, label):
    return f'<a href="{escape(evidence_url(path), quote=True)}">{escape(str(label))}</a>'


def _attempt(record, label):
    return _link('attempts/' + safe_identifier(record['attempt_id']) + '.html', label)


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def _percent(value):
    return f'{value:.1%}' if value is not None else '未测'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _bound_initial_audit(cases):
    """Publish a whitelist projection only when the audit matches current input cases."""
    path = ROOT / 'research/initial-reference-audit.json'
    dataset_path = ROOT / 'evaluation/dataset.json'
    if not path.exists() or not dataset_path.exists():
        return None
    audit = json.loads(path.read_text())
    raw = dataset_path.read_bytes()
    current = {c['id']: c for c in json.loads(raw)['cases']}
    # Other tracks' rules may change after this web-reference audit. Bind the
    # published web claim to every current web reference and source below.
    if current != cases:
        return None
    indexed = {r['case_id']: r for r in audit['cases']}
    if set(indexed) != set(cases):
        return None
    rows = []
    for key, case in cases.items():
        if case.get('track', 'web') != 'web':
            continue
        r = indexed[key]
        reference = case.get('ground_truth_main_content', '')
        expected_hash = hashlib.sha256(reference.encode()).hexdigest() if reference else None
        if r.get('reference_sha256') != expected_hash:
            return None
        if r.get('source_state') == 'present':
            relative = 'sources/' + safe_identifier(key) + '/' + Path(r['source_path']).name
            source = contained_path(ROOT / 'evaluation', relative)
            if not source.exists() or hashlib.sha256(source.read_bytes()).hexdigest() != r['source_sha256']:
                return None
        rows.append({k: r.get(k) for k in ('case_id', 'source_sha256', 'reference_sha256',
                     'reconstructed_sha256', 'exact_reconstruction', 'source_state')})
    return {'audited_dataset_sha256': audit['dataset_sha256'],
            'current_dataset_sha256': hashlib.sha256(raw).hexdigest(),
            'current_web_cases_sha256': _digest({k: c for k, c in cases.items()
                                                if c.get('track', 'web') == 'web'}), 'cases': rows,
            'web_case_count': len(rows), 'exact_reconstructions':
            sum(r['exact_reconstruction'] is True for r in rows),
            'scope': 'frozen reference recoverability from saved initial HTML, not DOM visibility or JS necessity'}


def collect_evidence(cases, records):
    """Do not rescore or normalize: the report builder owns that boundary."""
    for key in cases:
        safe_identifier(key)
    for r in records:
        for key in ('provider', 'case_id', 'attempt_id'):
            safe_identifier(r[key])

    def passed(r):
        return task_passed(r['ok'], r['metrics'], cases[r['case_id']].get('task_kind', 'body'))

    def summary(rows):
        full = [r for r in rows if r['metrics'].get('word_f1') is not None]
        successes = sum(passed(r) for r in rows)
        post = sum(bool(r['ok']) and not passed(r) for r in rows)
        failures = len(rows) - successes
        return {'records': len(rows), 'provider_count': len({r['provider'] for r in rows}),
                'case_count': len({r['case_id'] for r in rows}),
                'request_ok': sum(bool(r['ok']) for r in rows), 'task_pass': successes,
                'failures': failures, 'post_response_failures': post,
                'request_failures': sum(not r['ok'] for r in rows),
                'post_response_failure_share': post / failures if failures else None,
                'f1_records': len(full), 'mean_f1': _mean(r['metrics']['word_f1'] for r in full),
                'attempt_ids': [r['attempt_id'] for r in rows]}

    def window(rows):
        dates = [datetime.fromisoformat(r['started_at']).astimezone(timezone.utc)
                 for r in rows if r.get('started_at')]
        return {'first_start_utc': min(dates).isoformat() if dates else None,
                'last_start_utc': max(dates).isoformat() if dates else None}

    providers = sorted({r['provider'] for r in records})
    historical = [r for r in records if cases[r['case_id']].get('track', 'web') != 'dynamic']
    dynamic = [r for r in records if cases[r['case_id']].get('track') == 'dynamic']
    result = {'schema_version': 1,
              'input': {'cases_sha256': _digest(cases), 'records_sha256': _digest(records),
                        'records_state': 'report-builder normalized; no editorial rescoring'},
              'totals': summary(records), 'case_count': len(cases),
              'provider_count': len(providers), 'tracks': {},
              'historical': summary(historical), 'dynamic': summary(dynamic),
              'time_window': window(records), 'historical_time_window': window(historical),
              'dynamic_time_window': window(dynamic)}
    for track in TRACK_NAMES:
        ids = sorted(k for k, c in cases.items() if c.get('track', 'web') == track)
        rows = [r for r in records if r['case_id'] in ids]
        byprovider = {p: summary([r for r in rows if r['provider'] == p]) for p in providers
                      if any(r['provider'] == p for r in rows)}
        success = sorted({r['case_id'] for r in rows if passed(r)})
        result['tracks'][track] = {'case_ids': ids, 'case_count': len(ids),
                                   'providers': byprovider, 'union_pass_ids': success,
                                   'unpassed_ids': sorted(set(ids) - set(success))}
    result['dynamic']['cases'] = [
        {'case_id': k, 'results': [{'provider': r['provider'], 'attempt_id': r['attempt_id'],
                                    'request_ok': bool(r['ok']), 'task_pass': passed(r)}
                                   for r in dynamic if r['case_id'] == k]}
        for k in result['tracks']['dynamic']['case_ids']]

    cow = [r for r in historical if r['provider'] == 'cowagent-built-in'
           and cases[r['case_id']].get('track', 'web') == 'web']
    low = min((r for r in cow if passed(r) and r['metrics'].get('word_f1') is not None),
              key=lambda r: r['metrics']['word_f1'], default=None)
    result['lowest_f1_passing_cow'] = ({'case_id': low['case_id'], 'attempt_id': low['attempt_id'],
                                       **{k: low['metrics'][k] for k in
                                          ('word_f1', 'word_recall', 'word_precision')}} if low else None)
    # A union of repeated attempts would silently reward retries. Only pair single observations.
    moli = [r for r in historical if r['provider'] == 'moli-webfetch'
            and cases[r['case_id']].get('track', 'web') == 'web']
    result['cow_moli'] = None
    if cow and moli and all(n == 1 for n in Counter((r['provider'], r['case_id'])
                                                    for r in cow + moli).values()):
        a, b = ({r['case_id']: r for r in rows} for rows in (cow, moli))
        matched = a.keys() & b.keys()
        pa = {k for k in matched if passed(a[k])}
        pb = {k for k in matched if passed(b[k])}
        common = sorted(pa & pb)
        timed = [k for k in common if isinstance(a[k].get('elapsed_ms'), (int, float))
                 and isinstance(b[k].get('elapsed_ms'), (int, float))]
        result['cow_moli'] = {
            'scope': 'offline matched-case analysis, not a fallback execution',
            'matched_case_ids': sorted(matched), 'cow_pass_ids': sorted(pa), 'moli_pass_ids': sorted(pb),
            'both_pass_ids': common, 'cow_only_ids': sorted(pa - pb), 'moli_only_ids': sorted(pb - pa),
            'neither_ids': sorted(matched - (pa | pb)), 'union_pass_ids': sorted(pa | pb),
            'timed_common_case_ids': timed,
            'cow_common_median_ms': statistics.median(a[k]['elapsed_ms'] for k in timed) if timed else None,
            'moli_common_median_ms': statistics.median(b[k]['elapsed_ms'] for k in timed) if timed else None,
            'rescues': [{'case_id': k, 'cow_attempt_id': a[k]['attempt_id'],
                         'moli_attempt_id': b[k]['attempt_id'], 'cow_request_ok': bool(a[k]['ok'])}
                        for k in sorted(pb - pa)]}
    files = [r for r in records if cases[r['case_id']].get('task_kind') == 'file']
    result['raw_file'] = {'records': len(files), 'missing_raw_file_pass':
                          sum('raw_file_pass' not in r['metrics'] for r in files),
                          'verified_pass': sum(passed(r) for r in files)}
    return result


def _table(headers, rows, caption):
    headers = ['序号'] + list(headers)
    rows = [(str(index), *row) for index, row in enumerate(rows, 1)]
    head = ''.join(f'<th scope="col">{escape(x)}</th>' for x in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{x}</td>' for x in row) + '</tr>' for row in rows)
    return ('<p class="editorial-table-hint">表格可横向滚动查看全部列。</p>'
            '<div class="editorial-table" role="region" tabindex="0" aria-label="' + escape(caption) + '">'
            '<table><caption>' + escape(caption) + '</caption><thead><tr>' + head + '</tr></thead>'
            '<tbody>' + body + '</tbody></table></div>')


def _price_experiment(out, cases, records):
    sample = next((r for r in records if r['provider'] == 'cowagent-built-in'
                   and r['case_id'] == 'ecommerce-list'), None)
    if not sample:
        return None, '<p>当前输入未包含书目页的 CowAgent 执行记录，无法核算价格反事实。</p>'
    case = cases[sample['case_id']]
    raw = contained_path(out, sample['artifact_path']).read_text()
    kwargs = {key: case[key] for key in ('required_facts', 'forbidden_any', 'min_content_chars',
              'fact_coverage_threshold', 'ground_truth_main_content', 'required_patterns') if key in case}
    # Declared experimental intervention, not a benchmark statistic.
    before, after = '51.77', '999.99'
    original = evaluate_content(raw, case['expected_any'], **kwargs)
    altered = evaluate_content(raw.replace(before, after), case['expected_any'], **kwargs)
    fact = next((f for f in altered['fact_results'] if f['id'] == 'first-price'), None)
    data = {'attempt_id': sample['attempt_id'], 'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'replace_from': before, 'replace_to': after, 'replacements': raw.count(before),
            'original_content_pass': original['content_pass'], 'altered_content_pass': altered['content_pass'],
            'altered_length_pass': altered['length_pass'], 'altered_price_pass': fact['pass'] if fact else None,
            'original_chars': len(raw), 'minimum_chars': case['min_content_chars'],
            'fact_results': original['fact_results']}
    rows = [(escape(f['id']), escape(' / '.join(f['alternatives'])),
             '命中' if f['pass'] else '缺失') for f in original['fact_results']]
    state = lambda flag: '通过' if flag else '未通过'
    html = (f'<p>{_link("datasets/ecommerce-list.html", "Books to Scrape 的书目页")}要求书名、价格和库存核对点满足，'
            f'返回长度至少为 {case["min_content_chars"]:,} 字符。'
            f'{_attempt(sample, "CowAgent 的已保存输出")}有 {len(raw):,} 字符。</p>'
            + '<details class="editorial-probe"><summary>核对本例的书名、价格与库存规则</summary>'
            + _table(['核对点', '可接受表达', '实际命中'], rows, '书目页：规则实际检查了什么') + '</details>'
            + f'<p>在这份输出的内存副本中，把 <code>{before}</code> 换成 <code>{after}</code>，'
            f'再运行同一个 <code>evaluate_content</code>。原文正文规则{state(original["content_pass"])}；'
            f'替换后长度规则{state(altered["length_pass"])}，价格核对点{state(fact["pass"] if fact else False)}，'
            f'正文规则{state(altered["content_pass"])}。请求状态没有改变，但内容验收改变了。'
            '这个离线反事实只改副本，不改实测记录、不进入排名。</p>')
    return data, html


def _dynamic_section(data, cases):
    scope, total = data['tracks']['dynamic'], data['dynamic']
    if not scope['case_count']:
        return ('<section id="dynamic-coverage"><h2>动态内容的覆盖还需要单独验证</h2>'
                '<p>网页组的分类标签与参考来源不足以逐页证明正文必须执行 JavaScript 后才出现。'
                '当前输入没有独立动态内容补充实验，不能把所有网页称为静态任务，'
                '也不能把动态类别标签当作浏览器必需的证据。</p></section>')
    providers = sorted(scope['providers'])
    rows = []
    for p in providers:
        row = [_link('providers/' + p + '.html', PROVIDER_NAMES.get(p, p))]
        for item in total['cases']:
            r = next((r for r in item['results'] if r['provider'] == p), None)
            row.append(_attempt(r, '通过' if r['task_pass'] else '未通过') if r else '未运行')
        rows.append(row)
    figure = _table(['方法'] + [cases[item['case_id']]['name'] for item in total['cases']], rows,
                    f'独立补充组：{scope["case_count"]} 个任务、{len(providers)} 种已运行方法；未运行不算失败')
    # Task-specific interpretation is only attached when its evidence cases are present.
    mechanism = ''
    ids = set(scope['case_ids'])
    if {'dynamic-quotes-api', 'dynamic-products-api'} <= ids:
        mechanism += ('<p>补充组把初始 HTML 中缺少目标内容的任务单独列出。Quotes to Scrape 的 '
            '<code>/scroll</code> 名言列表与 ScrapingCourse 的 JavaScript 商品页，经独立 GET 和浏览器核对，'
            '目标条目未出现在初始 HTML 中。它们要求在首次加载后交付指定范围的名言或商品。'
            '这支持“单靠初始 HTML 不够”，但不支持“必须执行 JavaScript”：'
            '直接调用页面使用的内容 API，只要返回满足同一任务契约，也是一条有效路线。</p>')
    if 'dynamic-delayed' in ids:
        delayed = next(item for item in total['cases'] if item['case_id']=='dynamic-delayed')['results']
        delayed_passes = sum(r['task_pass'] for r in delayed)
        delayed_observation = (f'本次延迟页的 {len(delayed)} 次调用中，{delayed_passes} 次满足完整内容规则。'
                               if delayed else '')
        mechanism += ('<p>延迟名言页把等待条件的问题暴露得更直接。页面可以已经完成初始请求，'
            '网络暂时空闲，正文却还要等计时器触发。' + delayed_observation + '浏览器启动成功或 network-idle 到达，'
            '都不能单独充当内容完成的证据。应按目标条目出现、数量与关键内容稳定来判断何时取结果；'
            '本轮没有对各种等待策略做消融，不能从一次失败计算换策略后的收益。</p>')
    loading = total.get('loading_attempts', [])
    if loading:
        mechanism = ('<p>' + '、'.join(_attempt(r, cases[r['case_id']]['name']) for r in loading)
            + '的 Moli 已保存输出仍包含 <code>Loading...</code>，内容规则未通过。'
            '请求成功在这里记录了一个正常返回的结果，却没有等来任务要求的内容。'
            '这比方法是否具备浏览器功能更接近本次接入要解决的问题。</p>') + mechanism
    if 'dynamic-products-api' in ids:
        expected = cases['dynamic-products-api'].get('source_benchmark', {}).get('expected_entries')
        count = f'{expected} 个' if isinstance(expected, int) else '指定数量的'
        mechanism += (f'<p>商品页的参考采集还遇到了一个会制造假完成的细节：初始页面已有 {count}占位卡片，'
            '但卡片文字为空。只等 DOM 数量达标，会把空占位当成加载完成。独立复核改用“数量达标且必需字段非空”，'
            '在有上限的等待内取得目标条目，并与运行前冻结参考核对。'
            '数量与字段要同时满足，才能把“布局已经出现”与“内容可以交付”区分开。</p>')
    return (f'<section id="dynamic-coverage"><h2>动态内容补测，让网页高分遇到新的边界</h2>'
            f'<p>历史网页组保持不变，另加 {scope["case_count"]} 个动态内容任务。'
            f'补充组实际有 {len(providers)} 种方法、{total["records"]} 条调用，'
            f'{total["request_ok"]} 次请求正常返回，{total["task_pass"]} 次任务通过。'
            '各方法的运行时间见原始记录。历史任务没有在这一批重跑；两组分开排名，不能拼成同批次的新总榜。</p>'
            + figure + mechanism
            + '<p>对这类页面，接入前应试验的是取数路径与完成条件：源 HTML 是否足够，'
              '如果不够，后续数据从哪里来，什么状态才允许交付。浏览器启动成功并没有替应用回答这些问题。</p></section>')


def render_explainer(out, cases, records, *, table=None, cell=None, link=None):
    """Dashboard entry point; standalone export shares its exact authored article body."""
    out = Path(out)
    data = collect_evidence(cases, records)
    data['initial_reference_audit'] = _bound_initial_audit(cases)
    data['dynamic']['loading_attempts'] = []
    for r in records:
        if (cases[r['case_id']].get('track') == 'dynamic' and r['provider'] == 'moli-webfetch'
                and not task_passed(r['ok'], r['metrics'], cases[r['case_id']].get('task_kind', 'body'))):
            artifact = contained_path(out, r['artifact_path'])
            if artifact.exists() and 'Loading...' in artifact.read_text():
                data['dynamic']['loading_attempts'].append(
                    {'case_id': r['case_id'], 'attempt_id': r['attempt_id']})
    t, total = data['historical'], data['totals']
    tracks, web = data['tracks'], data['tracks']['web']
    values = {'title': escape(TITLE), 'cases': str(data['case_count']),
              'providers': str(data['provider_count']), 'records': f'{total["records"]:,}',
              'historical_cases': str(t['case_count']), 'historical_records': f'{t["records"]:,}',
              'historical_providers': str(t['provider_count']),
              'request_ok': f'{t["request_ok"]:,}', 'task_pass': f'{t["task_pass"]:,}',
              'failures': str(t['failures']), 'post_response': str(t['post_response_failures']),
              'post_response_share': _percent(t['post_response_failure_share']),
              'web_cases': str(web['case_count']),
              'track_mix': '、'.join(f'{TRACK_NAMES[k]} {v["case_count"]} 例' for k, v in tracks.items()
                                  if v['case_count'] and k != 'dynamic'),
              'failure_interpretation': ('失败集中在正常返回之后' if
                  (t['post_response_failure_share'] or 0) > .5 else '请求状态没有覆盖全部任务失败'),
              'results_link': _link('results.jsonl', '规范化执行记录'),
              'rules_link': _link('methodology/index.html', '判定规则与指标口径'),
              'environment_link': _link('environment/index.html', '执行批次与接口配置'),
              'evidence_link': _link('guide/interpretation-evidence.json', '本文计算明细'),
              'reproduce_link': _link('reproduce/index.html', '复现说明'),
              'article_link': _link('guide/interpretation-evidence.json', '独立 HTML 与证据下载'),
              'raw_file_records': str(data['raw_file']['records']),
              'raw_file_missing': str(data['raw_file']['missing_raw_file_pass']),
              'raw_file_verified': str(data['raw_file']['verified_pass']),
              'dynamic_section': _dynamic_section(data, cases)}
    audit = data['initial_reference_audit']
    values['initial_audit'] = (
        f'<p>初始源文档审计进一步缩小了这批样本的解释范围：网页组 {audit["web_case_count"]} 例中，'
        f'{audit["exact_reconstructions"]} 例的冻结参考，可以从已保存的初始 HTML 经参考提取与规范化后精确复现。'
        '这是对参考文本可恢复性的检查，既不意味着它们都是可见静态正文，也不意味着 CowAgent 使用了同样的提取器。'
        '嵌入脚本的载荷仍需要正确解析。高网页通过率因此不能直接外推到初始响应之外才到达的内容。'
        + _link('guide/interpretation-evidence.json', '逐例审计摘要与校验值') + '保留了核查依据。</p>'
        if audit else '<p>当前输入没有与案例及参考校验值完整匹配的初始源文档审计，'
        '不能汇总动态内容依赖比例。类别标签既不能证明正文已经在初始 HTML 中，也不能证明必须执行 JavaScript。</p>')
    window = data['historical_time_window']
    values['time_window'] = (' 至 '.join(datetime.fromisoformat(window[k]).strftime('%Y-%m-%d %H:%M:%S')
                            for k in ('first_start_utc', 'last_start_utc')) + ' UTC'
                            if window['first_start_utc'] else '未提供调用时间')
    dynamic = data['dynamic']
    wm = web['providers'].get('moli-webfetch')
    dm = tracks['dynamic']['providers'].get('moli-webfetch')
    values['lead'] = (f'同一配置的 Moli，在网页正文组通过 {wm["task_pass"]}/{wm["records"]}，'
        f'在新加入的动态内容任务上通过 {dm["task_pass"]}/{dm["records"]}。'
        '网页高分为什么没有自然延伸到新任务？关键在于目标内容在哪里、何时到达，以及接口最终保留了什么。'
        if wm and dm else '抓取接口正常返回之后，正文仍可能缺失、混入导航，或丢掉详情地址。'
        '这轮实测从这些差异出发，判断哪些方法值得接入，哪些结论还需要试验。')
    values['dateline'] = (datetime.fromisoformat(data['time_window']['last_start_utc']).strftime('%Y-%m-%d')
                          if data['time_window']['last_start_utc'] else '调用日期未提供')
    values['supplement_note'] = (f'另有动态内容补充组 {tracks["dynamic"]["case_count"]} 例、'
        f'{dynamic["provider_count"]} 种已运行方法、{dynamic["records"]} 条记录，单独分析。'
        if tracks['dynamic']['case_count'] else '当前输入未包含独立动态内容补充组。')
    failure_rows = [('任务通过', t['task_pass']), ('正常返回但任务未通过', t['post_response_failures']),
                    ('请求失败', t['request_failures'])]
    values['failure_figure'] = '<figure class="editorial-figure" id="failure-figure"><div class="editorial-bars">' + ''.join(
        f'<div class="editorial-bar-row"><span>{label}</span><span class="editorial-bar-track" aria-hidden="true">'
        f'<i style="width:{count / max(t["records"], 1) * 100:.4f}%"></i></span>'
        f'<b>{count:,} / {t["records"]:,}</b></div>' for label, count in failure_rows
    ) + f'</div><figcaption>历史组互斥状态分解；调用开始时间 {values["time_window"]}。不混入动态补测，正常返回不等于源站 HTTP 200。</figcaption></figure>'
    selected = ['cloudflare-markdown', 'exa', 'cowagent-built-in']
    values['quality_figure'] = _table(['网页方法', '任务通过 / 调用', '平均 F1', 'F1 样本数'], [
        (_link('providers/' + p + '.html', PROVIDER_NAMES[p]), f'{s["task_pass"]}/{s["records"]}',
         f'{s["mean_f1"]:.4f}' if s['mean_f1'] is not None else '未测', str(s['f1_records']))
        for p in selected if (s := web['providers'].get(p))],
        '用于比较排名逆转与提取机制的方法：同一网页组，F1 为有指标记录的逐例算术平均')
    cf, exa = (web['providers'].get(p) for p in ('cloudflare-markdown', 'exa'))
    values['rank_reversal'] = '<p>当前输入不足以比较 Cloudflare Markdown 与 Exa。</p>'
    if cf and exa:
        reversal = (cf['task_pass'] > exa['task_pass'] and cf['mean_f1'] is not None
                    and exa['mean_f1'] is not None and cf['mean_f1'] < exa['mean_f1'])
        values['rank_reversal'] = (f'<p>Cloudflare Markdown 在 {cf["records"]} 次网页调用中通过 '
            f'{cf["task_pass"]} 次，Exa 为 {exa["task_pass"]}/{exa["records"]}。'
            + ('按全文 F1 看，次序却倒过来了。' if reversal else '全文 F1 给出另一种观察角度。')
            + '任务验收关心指定内容是否交付，F1 关心输出与参考正文的词频重合。'
              '同一个“最好”，在这两个目标下可能指向不同方法。</p>')
    low = data['lowest_f1_passing_cow']
    values['low_f1_case'] = '<p>当前输入没有可展示的 CowAgent 低 F1 通过案例。</p>'
    if low:
        values['low_f1_case'] = (f'<p>CowAgent 已通过网页中 F1 最低的是 '
            f'{_attempt(low, cases[low["case_id"]]["name"])}：完整度 {_percent(low["word_recall"])}，'
            f'纯净度 {_percent(low["word_precision"])}，F1 {_percent(low["word_f1"])}。'
            '几乎找齐参考词语，却同时返回了大量参考范围以外的文字。按这份参考选取正文的任务，'
            '关键点保住了，阅读和索引负担仍然存在。</p>')
    data['price_counterfactual'], values['price_case'] = _price_experiment(out, cases, records)
    probe_path = ROOT / 'research/cowagent-text-probe.json'
    probe = json.loads(probe_path.read_text()) if probe_path.exists() else None
    data['probe'] = ({'source_sha256': probe['source_sha256'], 'observations': probe['observations']}
                     if probe else None)
    values['probe_figure'] = '<p>当前构建未提供提取函数的离线实验文件，机制仍需复核。</p>'
    if probe:
        labels = {'body_preserved': '正文', 'navigation_preserved': '导航', 'footer_preserved': '页脚',
                  'link_label_preserved': '链接文字', 'script_preserved': '脚本内容',
                  'detail_url_preserved': '详情 URL（href）', 'image_url_preserved': '图片 URL（src）',
                  'video_url_preserved': '视频 URL（src）'}
        values['probe_figure'] = _table(['合成输入中的信息', '提取后'], [
            (label, '保留' if probe['observations'][key] else '未保留') for key, label in labels.items()],
            '离线机制实验：直接执行纯文本提取函数，没有联网或新增榜单调用')
        values['probe_figure'] += ('<details class="editorial-probe"><summary>核对合成输入与实际输出</summary>'
            f'<h3>输入 HTML</h3><pre>{escape(probe["fixture"])}</pre>'
            f'<h3>实际输出</h3><pre>{escape(probe["output"])}</pre><p>'
            + _link('guide/cowagent-text-probe.json', '实验代码、观察结果与源码 SHA-256') + '</p></details>')
    cow = web['providers'].get('cowagent-built-in')
    values['cow_result'] = (f'<p>CowAgent 在网页组请求成功 {cow["request_ok"]}/{cow["records"]}，'
        f'任务通过 {cow["task_pass"]}/{cow["records"]}。一个以 HTTP 取页、再去除标签的实现，'
        '在这批网页上已能满足大部分关键内容规则。这使它值得作为正文入口的基线，'
        '但还不足以解释全部成功来自哪里。</p>' if cow else '<p>当前输入没有 CowAgent 网页记录。</p>')
    pair = data['cow_moli']
    values['overlap_analysis'] = '<p>当前输入不满足逐例配对条件，不能计算 CowAgent 与 Moli 的离线互补覆盖。</p>'
    if pair:
        n = len(pair['matched_case_ids'])
        values['overlap_analysis'] = (f'<p>在两者都有一次记录的 {n} 个网页任务上，CowAgent 通过 '
            f'{len(pair["cow_pass_ids"])} 例，Moli 通过 {len(pair["moli_pass_ids"])} 例。'
            f'按案例 ID 对齐后，Moli 能补上 CowAgent 未通过的 {len(pair["moli_only_ids"])} 例；'
            f'反方向，CowAgent 也能补上 Moli 未通过的 {len(pair["cow_only_ids"])} 例。'
            '只看单方法分数，会丢掉这层信息。</p>')
        values['overlap_analysis'] += _table(['两种方法的结果组合', '网页案例数'], [
            ('都通过', str(len(pair['both_pass_ids']))), ('仅 CowAgent 通过', str(len(pair['cow_only_ids']))),
            ('仅 Moli 通过', str(len(pair['moli_only_ids']))), ('都未通过', str(len(pair['neither_ids'])))],
            f'离线成功集分解，分母 {n} 个配对网页任务；每例只进入一个分组')
        values['overlap_analysis'] += (f'<p>二者成功集的离线并集为 {len(pair["union_pass_ids"])}/{n}。'
            '这给出的决策是开展一次有内容校验的候选 fallback 试验。'
            '它尚未测试串行调用、失败识别与实时重试，更不构成线上全覆盖保证。</p>')
        rows = []
        for rescue in pair['rescues']:
            state = '正常返回；任务未通过' if rescue['cow_request_ok'] else '请求失败'
            rows.append((_link('datasets/' + rescue['case_id'] + '.html', cases[rescue['case_id']]['name']),
                         _attempt({'attempt_id': rescue['cow_attempt_id']}, state),
                         _attempt({'attempt_id': rescue['moli_attempt_id']}, '任务通过')))
        values['overlap_analysis'] += _table(['互补案例', 'CowAgent 本次结果', 'Moli 本次结果'], rows,
                                            'Moli 通过而 CowAgent 未通过的网页，逐例回到原始记录')
        if pair['cow_common_median_ms'] is not None:
            values['overlap_analysis'] += (f'<p>在共同通过且都有耗时的 {len(pair["timed_common_case_ids"])} 例中，'
                f'CowAgent 的客户端墙钟耗时中位数为 {pair["cow_common_median_ms"]:.2f} ms，'
                f'Moli 为 {pair["moli_common_median_ms"]:.2f} ms。对齐成功任务后，'
                '这个比较排除了各自成功集不同的影响；失败等待仍未计入，'
                '不同时间批次、页面状态和网络条件也没有被消除。它不能用来相加计算生产 fallback 延迟，'
                '本轮也没有实测金额，不能据此推出成本节省。</p>')
    values['rescue_mechanism'] = ''
    if pair and {'javascript-spa', 'lm-feishu-help', 'web-retail-returns'} <= set(pair['moli_only_ids']):
        values['rescue_mechanism'] = (
            '<p>这些互补案例暴露了不同缺口。Quotes to Scrape 的 <code>/js/</code> 页面在 CowAgent '
            '输出里留下页面壳，名言和作者核对点缺失；飞书帮助文档只有标题，没有要求的正文。'
            '零售退换货页则是请求被拒绝。前两例要靠内容判断才能触发后备方法，最后一例可以由请求错误触发。'
            '因此，只在网络异常时切换方法，无法复现这里看到的全部互补覆盖。</p>'
            '<p>页面壳也不能直接证明正文必须经异步请求取得。对初始源文档的复核发现，'
            '<code>/js/</code> 的名言载荷和飞书文章内容已经嵌入 HTML；取回源字节与正确提取嵌入内容是两件事。'
            '它们揭示的是当前提取路径的缺口，不能作为浏览器执行能力的独立证据。</p>')
    challenge = tracks['challenge']
    best = max((v['task_pass'] for v in challenge['providers'].values()), default=0)
    values.update(challenge_cases=str(challenge['case_count']), challenge_best=str(best),
                  challenge_union=str(len(challenge['union_pass_ids'])),
                  challenge_unpassed='、'.join(_link('datasets/' + k + '.html', cases[k]['name'])
                                             for k in challenge['unpassed_ids']) or '无',
                  challenge_missing_count=str(len(challenge['unpassed_ids'])))
    catalog = {r['case_id']: r for r in records if r['provider'] == 'cowagent-built-in'
               and r['case_id'] in {'ecommerce-list', 'image-03'}}
    values['media_case'] = '<p>同一网页的正文与媒体地址是两种交付目标；文字保留完整，地址仍可能丢失。</p>'
    if set(catalog) == {'ecommerce-list', 'image-03'}:
        states = {k: '通过' if task_passed(r['ok'], r['metrics'], cases[k].get('task_kind', 'body'))
                  else '未通过' for k, r in catalog.items()}
        values['media_case'] = (f'<p>同一个 Books to Scrape 站点，CowAgent 的 '
            f'{_attempt(catalog["ecommerce-list"], "书目正文任务")}{states["ecommerce-list"]}，'
            f'{_attempt(catalog["image-03"], "商品封面地址任务")}{states["image-03"]}。'
            '书名和价格留在文本里，不能保证封面地址也交付了。'
            '下游需要分别验收文字和资源地址，才能发现这种交付差异。</p>')
    body = Template(TEMPLATE.read_text()).substitute(values)
    contained_path(out, 'guide').mkdir(parents=True, exist_ok=True)
    if probe:
        contained_path(out, 'guide/cowagent-text-probe.json').write_text(
            json.dumps(probe, ensure_ascii=False, indent=2) + '\n')
    contained_path(out, 'guide/interpretation-evidence.json').write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    contained_path(out, 'guide/article.html').write_text(
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(TITLE)}</title><style>{STYLE}</style></head>'
        f'<body class="standalone-article"><main class="wrap essay">{body}</main></body></html>')
    downloads = ('<aside id="evidence" class="editorial-downloads" aria-label="文章与证据下载">'
        '<h2>文章与证据下载</h2><p>'
        '<a href="article.html" download="web-fetch-article.html">下载独立 HTML</a> · '
        '<a href="interpretation-evidence.json" download>下载本文计算明细</a> · '
        '<a href="../results.jsonl" download>下载规范化执行记录</a>'
        + (' · <a href="cowagent-text-probe.json" download>下载离线提取实验</a>' if probe else '')
        + '</p></aside>')
    return f'<div class="wrap essay">{body}{downloads}</div>'
