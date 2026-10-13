"""One authored article; all figures use the report builder's normalized inputs."""
import base64
import hashlib
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote, urlsplit

from harness.record_io import parse_jsonl
from harness.web_fetch.reference_inputs import read_dataset_bytes

from .report_resources import render_resource_comparison
from .metrics import task_passed, percentile
from .methods import is_browser_comparison
from .scoring import score_attempt
from .report_style import STYLE
from .safety import contained_path, safe_identifier

TITLE = '网页抓取成功之后，内容离可用还有多远'
HEADLESS_TITLE = '五类浏览器、七种方法取同一批网页：速度、正文与交付边界'
from .publication_target import public_report_url

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).parent / 'templates/interpretation.html'
from .report import TRACKS as TRACK_NAMES

PROVIDER_NAMES = {'cowagent-built-in': 'CowAgent', 'moli-webfetch': 'Moli',
                  'cloudflare-markdown': 'Cloudflare Markdown', 'exa': 'Exa',
                  'langchain-webbase': 'LangChain WebBaseLoader', 'firecrawl': 'Firecrawl',
                  'crawl4ai': 'Crawl4AI', 'openclaw-built-in': 'OpenClaw',
                  'deepseek-harness-built-in': 'DeepSeek Harness', 'tavily': 'Tavily',
                  'lexmount-global': 'Lexmount 国际', 'lexmount-cn': 'Lexmount 中国区',
                  'moli-1.0.6': 'Moli 1.0.6', 'moli': 'Moli 1.1.5',
                  'moli-pr919': 'Moli 1.1.15（PR 919 对照版）',
                  'moli-fixed': 'Moli 1.1.17（main + PR 1476 + PR 1481，60773df3）',
                  'moli-1.1.3': 'Moli 1.1.3', 'moli-1.1.11': 'Moli 1.1.11'}

def provider_name(name, fallback):
    from .methods import measured_method_label
    return measured_method_label(name, PROVIDER_NAMES.get(name, fallback))


METHODOLOGY_FIGURES = (
    ('annotation', '混合标注：稳定文字、易变字段与全文参考',
     '页面中稳定标题、栏目与说明作为内容核对点；变化条目检查数量、字段类型和结构；全文参考单独声明范围。',
     '稳定文字仍做内容核对，易变值不固定为答案；参考文本的范围决定质量指标能解释什么。'),
    ('validation', '校验：独立来源、正反样例与原始输出',
     '独立来源支撑任务标注，完整字段与空占位作为结构校验的正反样例；保存原始输出及其哈希供复核。',
     '来源独立、规则有正反样例、输出可按哈希追溯，是不同的证据要求；哈希本身不证明内容正确。'),
    ('scoring', '计分：任务验收与文本重合分别计算',
     '内容与结构检查产生任务通过或失败；有明确范围的参考另算纯净度、完整度与 F1。零通过仍可能有词语重合。',
     '任务验收与全文 P/R/F1 回答不同问题。未通过不抹去已测得的词语重合，缺少参考也不把 F1 记为零。'),
)


def _methodology_figures(out):
    """Use build-copied assets online and the same bytes in the offline export."""
    online, embedded, evidence = {}, {}, []
    for index, (name, title, alt, caption) in enumerate(METHODOLOGY_FIGURES, 1):
        slot = name + '_figure'
        online[slot] = embedded[slot] = ''
        relative = f'assets/methodology/{name}.png'
        path = contained_path(out, relative)
        info = {'path': relative, 'available': path.is_file()}
        evidence.append(info)
        if not info['available']:
            continue
        raw = path.read_bytes()
        if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
            raise ValueError(f'Expected a PNG methodology figure: {relative}')
        width, height = (int.from_bytes(raw[start:start + 4], 'big') for start in (16, 20))
        if not width or not height:
            raise ValueError(f'Invalid PNG dimensions: {relative}')
        info.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), width=width, height=height)
        data_uri = 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
        for target, uri in ((online, '../' + relative), (embedded, data_uri)):
            # Download also works for data URIs, whose top-level navigation browsers block.
            target[slot] = (
                f'<figure id="methodology-{name}-figure" class="editorial-figure">'
                '<div style="position:relative">'
                f'<img src="{uri}" alt="{escape(alt, quote=True)}" width="{width}" height="{height}" '
                'style="display:block;width:100%;max-width:100%;height:auto;object-fit:contain" />'
                # The packaged downloader replaces anchor text while loading; keep the image outside it.
                f'<a href="{uri}" download="{name}.png" aria-label="获取原图：{escape(title, quote=True)}" '
                'style="position:absolute;inset:0;display:block"></a></div>'
                f'<figcaption>图 {index} · {escape(title)}。{escape(caption)} 点击图像获取原图。</figcaption></figure>')
    return online, embedded, evidence


def evidence_url(path):
    contained_path(ROOT, path)
    if Path(path).suffix != '.html':
        return public_report_url() + '#page=guide%2Findex.html&anchor=evidence'
    return public_report_url() + '#page=' + quote(str(path), safe='')


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


def _window_text(window):
    return (' 至 '.join(datetime.fromisoformat(window[k]).strftime('%Y-%m-%d %H:%M:%S')
                       for k in ('first_start_utc', 'last_start_utc')) + ' UTC'
            if window['first_start_utc'] else '未提供调用时间')


def _bound_initial_audit(cases):
    """Publish a whitelist projection only when the audit matches current input cases."""
    path = ROOT / 'research/initial-reference-audit.json'
    dataset_path = ROOT / 'evaluation/dataset.json'
    if not path.exists():
        return None
    audit = json.loads(path.read_text())
    try:
        raw = read_dataset_bytes(dataset_path)
    except FileNotFoundError:
        return None
    current = {c['id']: c for c in json.loads(raw)['cases']}
    # Other tracks' rules may change after this web-reference audit. Bind the
    # published claim to the complete web-case definitions, references and
    # sources, without making unrelated dynamic retrieval metadata part of the
    # web-reference claim.
    current_web = {key: case for key, case in current.items()
                   if case.get('track', 'web') == 'web'}
    input_web = {key: case for key, case in cases.items()
                 if case.get('track', 'web') == 'web'}
    if current_web != input_web:
        return None
    indexed = {r['case_id']: r for r in audit['cases']}
    if not set(input_web) <= set(indexed):
        return None
    rows = []
    for key, case in input_web.items():
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
            'current_web_cases_sha256': _digest(input_web), 'cases': rows,
            'web_case_count': len(rows), 'exact_reconstructions':
            sum(r['exact_reconstruction'] is True for r in rows),
            'scope': 'frozen reference recoverability from saved initial HTML, not DOM visibility or JS necessity'}


def collect_evidence(cases, records):
    """Read persisted shared scores; presentation never normalizes or rescores."""
    for key in cases:
        safe_identifier(key)
    for r in records:
        for key in ('provider', 'case_id', 'attempt_id'):
            safe_identifier(r[key])

    def passed(r):
        return task_passed(r['ok'], r['metrics'], cases[r['case_id']].get('task_kind', 'body'))

    def summary(rows):
        from .metrics import task_measured
        evaluated = [r for r in rows if task_measured(r['metrics'], cases[r['case_id']].get('task_kind'))]
        from .report_components import comparable_body_reference
        full = [r for r in rows if comparable_body_reference(cases[r['case_id']]) and r['metrics'].get('word_f1') is not None]
        successes = sum(passed(r) for r in rows)
        post = sum(bool(r['ok']) and not passed(r) for r in evaluated)
        failures = len(evaluated) - successes
        return {'records': len(rows), 'provider_count': len({r['provider'] for r in rows}),
                'case_count': len({r['case_id'] for r in rows}),
                'request_ok': sum(bool(r['ok']) for r in rows), 'task_pass': successes,
                'failures': failures, 'post_response_failures': post,
                'request_failures': sum(not r['ok'] for r in evaluated),
                'task_evaluated': len(evaluated), 'task_unmeasured': len(rows)-len(evaluated),
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
                        'records_state': 'persisted shared normalization and scoring; no editorial rescoring'},
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
                                   'summary': summary(rows), 'time_window': window(rows),
                                   'providers': byprovider, 'union_pass_ids': success,
                                   'unpassed_ids': sorted(set(ids) - set(success))}
    result['dynamic']['cases'] = [
        {'case_id': k, 'results': [{'provider': r['provider'], 'attempt_id': r['attempt_id'],
                                    'request_ok': bool(r['ok']), 'task_pass': passed(r)}
                                   for r in dynamic if r['case_id'] == k]}
        for k in result['tracks']['dynamic']['case_ids']]
    zero_sample = max((r for r in dynamic
                       if result['tracks']['dynamic']['providers'][r['provider']]['task_pass'] == 0
                       and (r['metrics'].get('word_f1') or 0) > 0),
                      key=lambda r: r['metrics']['word_f1'], default=None)
    result['dynamic']['zero_pass_nonzero_f1'] = (
        {k: zero_sample[k] for k in ('case_id', 'provider', 'attempt_id', 'metrics')}
        if zero_sample else None)
    result['tracks']['challenge']['score_scope'] = (
        'case-declared stable content and structure checks; scope-bound text overlap is a separate diagnostic')

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


def _speed_comparison(out):
    """Render the bound speed cohort without changing its declared denominator."""
    path = contained_path(out, 'speed-cohort/speed-comparison.json')
    if not path.is_file():
        return '', '', None
    speed = json.loads(path.read_text())
    rows = speed.get('rows', [])
    planned_cases = speed.get('planned_cases', [])
    common_cases = speed.get('common_pass_cases', [])
    provider_count = len(speed.get('providers') or rows)

    def num(value):
        return '—' if value is None else f'{value:,.0f}'

    if speed.get('schema_version', 1) == 1:
        table = _table(['方法', '共同成功样本', 'P50 ms', '样本 P95 ms'], [
            (escape(provider_name(row['provider'], row['provider'])),
             str(row['attempts']), num(row['p50_ms']), num(row['p95_ms']))
            for row in rows],
            f'同窗速度子集：预先声明 {len(planned_cases)} 题，{provider_count} 种方法共同通过 '
            f'{len(common_cases)} 题；所有方法使用完全相同的成功样本。P95 只作本次样本诊断。')
        note = (f'<p class="callout">这份旧版测速汇总只使用 {len(common_cases)}/{len(planned_cases)} 个全部 '
                f'{provider_count} 种方法共同通过的同窗任务；它保留原报告的共同成功样本口径，'
                '不能补充解释失败等待，也不能与跨批次主结果直接排名。</p>')
        return table, note, speed

    if speed.get('schema_version') != 2:
        raise ValueError(f'Unsupported speed-comparison schema: {speed.get("schema_version")}')
    max_observations = max((row.get('planned_attempts', 0) for row in rows), default=0)
    total_calls = sum(row.get('planned_attempts', 0) for row in rows)
    table = _table(
        ['方法', '计划调用', '任务通过', '通过率', '全调用 P50 ms', '全调用 P95 ms',
         '通过调用 P50 ms', '通过调用 P95 ms'], [
            (escape(provider_name(row['provider'], row['provider'])),
             str(row['planned_attempts']), str(row['successful_attempts']),
             _percent(row['task_pass_rate']), num(row['all_calls_p50_ms']),
             num(row['all_calls_p95_ms']), num(row['successful_calls_p50_ms']),
             num(row['successful_calls_p95_ms']))
            for row in rows],
        f'同窗速度子集：预先声明 {len(planned_cases)} 题，{provider_count} 种方法使用同一完整任务样本。'
        '全调用耗时包含成功、失败和截止超时；通过调用耗时按各方法自己的成功子集计算，分母必须同时阅读。'
        f'每种方法最多 {max_observations} 个单次观察，P95 只作本次样本诊断。')
    note = (f'<p class="callout">全部 {provider_count} 种方法共同通过 '
            f'{len(common_cases)}/{len(planned_cases)} 题；共同通过数只作诊断，不用于筛选测速样本。'
            f'报告保留预声明的全部 {total_calls} 次调用，不按结果重新选题；全调用耗时可在相同分母下阅读，'
            '通过调用耗时只描述各方法的条件分布，不能解释为同题速度排名。</p>')
    return table, note, speed


def _price_experiment(out, cases, records):
    sample = next((r for r in records if r['provider'] == 'cowagent-built-in'
                   and r['case_id'] == 'ecommerce-list'), None)
    if not sample:
        return None, '<p>当前输入未包含书目页的 CowAgent 执行记录，无法核算价格反事实。</p>'
    case = cases[sample['case_id']]
    raw = contained_path(out, sample['artifact_path']).read_text()
    kwargs = {'ok': sample['ok'], 'status_code': sample.get('status_code'),
              'metadata': sample.get('provider_metadata', {})}
    # Declared experimental intervention, not a benchmark statistic.
    before, after = '51.77', '999.99'
    original = score_attempt(case, raw.encode(), **kwargs)
    altered = score_attempt(case, raw.replace(before, after).encode(), **kwargs)
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
            f'再运行统一的 <code>score_attempt</code>。原文正文规则{state(original["content_pass"])}；'
            f'替换后长度规则{state(altered["length_pass"])}，价格核对点{state(fact["pass"] if fact else False)}，'
            f'正文规则{state(altered["content_pass"])}。请求状态没有改变，但内容验收改变了。'
            '这个离线反事实只改副本，不改实测记录、不进入排名。</p>')
    return data, html


def _dynamic_section(data, cases):
    scope, total = data['tracks']['dynamic'], data['dynamic']
    if not scope['case_count']:
        return ('<section id="dynamic-coverage"><h2>动态网站正文：内容何时可以交付</h2>'
                '<p>静态网站正文的分类标签与参考来源不足以逐页证明正文必须执行 JavaScript 后才出现。'
                '当前输入没有动态网站正文实测，不能把所有网页称为静态任务，'
                '也不能把动态类别标签当作浏览器必需的证据。</p></section>')
    providers = sorted(scope['providers'])
    routes = {'async_api':'异步接口', 'client_catalog':'客户端目录',
              'delayed_render':'内嵌数据＋延迟呈现', 'graphql':'GraphQL 异步接口',
              'react_app':'React 应用', 'client_router':'Angular 路由＋JSON',
              'schema_render':'接口规范转为可读目录', 'embedded_state':'内嵌 JSON 呈现',
              'embedded_frame':'iframe 子文档（不依赖 JS）'}
    rows = []
    for item in total['cases']:
        case = cases[item['case_id']]
        count = len(item['results'])
        passed = sum(r['task_pass'] for r in item['results'])
        rows.append([_link('datasets/' + item['case_id'] + '.html', case['name']),
                     escape(routes.get(case.get('category'),case.get('category','未分类'))),
                     f'{passed}/{count}' if count else '未运行'])
    figure = _table(['任务','内容呈现方式','通过 / 已运行'], rows,
                    f'动态网站正文：{scope["case_count"]} 个任务、{len(providers)} 种已运行方法；分母按每个任务实际运行数计算')
    # Task-specific interpretation is only attached when its evidence cases are present.
    mechanism = ''
    ids = set(scope['case_ids'])
    if {'dynamic-quotes-api', 'dynamic-products-api'} <= ids:
        mechanism += ('<p>Quotes to Scrape 的 '
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
        mechanism += (f'<p>商品页还有一个会制造假完成的细节：初始页面已有 {count}占位卡片，'
            '但卡片文字为空。只等 DOM 数量达标，会把空占位当成加载完成。参考采集要求“数量达标且必需字段非空”，'
            '在有上限的等待内取得目标条目，并由独立观察核对完整范围。'
            '数量与字段要同时满足，才能把“布局已经出现”与“内容可以交付”区分开。</p>')
    if {'dynamic-iframe','dynamic-swagger','dynamic-product-reviews'} <= ids:
        mechanism += ('<p>动态取数也不只涉及脚本等待。Swagger 页要把外部接口规范变成可读目录；'
            '商品评论已经放在内嵌 JSON 中，页面稍后才把它显示出来；龟类目录位于另一个 iframe 文档，'
            '沿着父页给出的地址继续获取即可，不需要 JavaScript。它们都说明初始页面的文字抽取不等于内容交付，'
            '但所需的补救路径并不相同。这组样本来自公开练习站和应用示例，适合检验机制，不能代表生产网站的分布。</p>')
    sample = total.get('zero_pass_nonzero_f1')
    if sample:
        stats = scope['providers'][sample['provider']]
        metrics = sample['metrics']
        mechanism += (f'<p>{escape(provider_name(sample["provider"], sample["provider"]))} 在动态网站正文中通过 '
            f'{stats["task_pass"]}/{stats["records"]}，平均 F1 却为 {_percent(stats["mean_f1"])}。'
            f'其中{_attempt(sample, cases[sample["case_id"]]["name"])}的 F1 为 {_percent(metrics["word_f1"])}，'
            '但没有满足该例任务规则。'
            '零通过表示没有一次满足全部任务门槛，不表示输出与参考毫无词语重合。'
            '这里的 F1 对照已保存的样本文本，只诊断文字重合，不承担实时事实核验。'
            + ('这份输出仍有 <code>setTimeout</code>、JSON 字段和转义字符；脚本载荷里的词语也进入 F1 的重合计算，'
               '但尚未成为任务要求的逐条名言、作者与标签。解析载荷并正确组织条目可能是一条取数路径，'
               '直接交付脚本文字则没有完成正文契约。' if sample.get('script_payload_observed') else
               '词频相似度允许部分文字得分，任务规则检查指定的内容或字段结构；二者应保留各自的含义。')
            + '</p>')
    return (f'<section id="dynamic-coverage"><h2>动态网站正文：取到页面之后，还要等什么</h2>'
            f'<p>{scope["case_count"]} 个任务覆盖异步取数、客户端呈现与子文档获取。'
            f'实际运行 {len(providers)} 种方法、{total["records"]} 次调用，'
            f'{total["request_ok"]} 次请求正常返回，{total["task_pass"]} 次任务通过。'
            '任务难度、源站状态和调用日期没有与静态网站正文任务构成受控对照；两类结果分别比较。</p>'
            + figure + mechanism
            + '<p>动态呈现与数据变化是两个维度。固定的名言也可以经异步接口或计时器显示；'
              '每日变动的榜单也可能直接写在 HTML 中。任务可以要求一定数量的完整条目、非空名称和指定类型的字段，'
              '不必锁定今天的名字或价格；稳定的标题、栏目和说明仍保留内容核对。'
              '页面内容变化，不妨碍用同一份内容与结构契约验收。</p>'
            + '<p>对这类页面，接入前应试验的是取数路径与完成条件：源 HTML 是否足够，'
              '如果不够，后续数据从哪里来，什么状态才允许交付。浏览器启动成功并没有替应用回答这些问题。</p></section>')


def _is_headless_comparison(records):
    return is_browser_comparison(records)


def article_title(records):
    return HEADLESS_TITLE if _is_headless_comparison(records) else TITLE


def headless_evidence(cases, records):
    """Matched-task comparisons; never compare unaligned successful subsets."""
    names = sorted({r['provider'] for r in records})
    def passed(r):
        return task_passed(r['ok'], r['metrics'], cases[r['case_id']].get('task_kind', 'body'))
    selection_path = ROOT / 'research/chromium-markdown-selection.json'
    selection = json.loads(selection_path.read_text()) if selection_path.is_file() else None
    data = {'providers': names, 'cases': len(cases), 'attempts': len(records), 'tracks': {},
            'time_window': [min(r['started_at'] for r in records), max(r.get('completed_at',r['started_at']) for r in records)],
            'method': 'one URL, fresh process/context, provider-fastest Markdown path, 45s end-to-end deadline',
            'pairs': [], 'runtime': {}, 'encyclopedia': [], 'quality': {},
            'independent_result_audit': None, 'chromium_markdown_selection': selection}
    for name in names:
        meta = next(r['provider_metadata'] for r in records if r['provider']==name)
        data['runtime'][name] = {k:v for k,v in meta.items() if k in ('binary_sha256','browser_version','worker_sha256','extractor_version','playwright_version','deployment','wait_until')}
        text_rows=[r for r in records if r['provider']==name and cases[r['case_id']].get('track','web')=='web' and r['metrics'].get('word_f1') is not None]
        data['quality'][name]={'attempts':len(text_rows), **{key:_mean([r['metrics'].get(key) for r in text_rows]) for key in ('word_precision','word_recall','word_f1')}}
    for r in records:
        if r['case_id']=='english-encyclopedia':
            meta=r.get('provider_metadata',{})
            data['encyclopedia'].append({'provider':r['provider'],'attempt_id':r['attempt_id'],
                'ok':r['ok'],'elapsed_ms':r['elapsed_ms'],
                'extractor':meta.get('extractor'),'deployment':meta.get('deployment'),
                'native_markdown_ms':meta.get('native_markdown_ms'),'browser_ms_used':meta.get('browser_ms_used'),
                'fetch_html_ms':meta.get('fetch_html_ms'),'conversion_ms':meta.get('conversion_ms')})
    for track in TRACK_NAMES:
        ids = {c['id'] for c in cases.values() if c.get('track','web')==track}
        byname = {}
        for name in names:
            rows = [r for r in records if r['provider']==name and r['case_id'] in ids]
            if not rows:
                continue
            byname[name] = {'attempts':len(rows), 'request_ok':sum(r['ok'] for r in rows),
                'passed':sum(passed(r) for r in rows),
                'p50_ms':statistics.median(r['elapsed_ms'] for r in rows),
                'p95_ms':percentile([r['elapsed_ms'] for r in rows], .95),
                'pass_ids':sorted(r['case_id'] for r in rows if passed(r)),
                'request_failures':dict(Counter(r.get('error_type') or 'unknown' for r in rows if not r['ok']))}
        data['tracks'][track] = {'cases':len(ids),'providers':byname}
    web = {name:{r['case_id']:r for r in records if r['provider']==name and cases[r['case_id']].get('track','web')=='web'} for name in names}
    for name in names:
        if name=='chromium' or 'chromium' not in web:
            continue
        a,b=web[name],web['chromium']
        matched = sorted(a.keys() & b.keys())
        common = [cid for cid in matched if passed(a[cid]) and passed(b[cid])]
        pa,pb=({cid for cid in matched if passed(rows[cid])} for rows in (a,b))
        data['pairs'].append({'provider':name,'baseline':'chromium','matched':len(matched),
            'both_pass_ids':common,'provider_only':sorted(pa-pb),'chromium_only':sorted(pb-pa),
            'union':len(pa|pb),
            'provider_p50_ms':statistics.median(a[cid]['elapsed_ms'] for cid in common) if common else None,
            'chromium_p50_ms':statistics.median(b[cid]['elapsed_ms'] for cid in common) if common else None})
    return data


def _headless_article_input(cases, records, providers):
    """Bind measured article inputs while ignoring report-only annotations."""
    derived_fields = {'diagnosis', 'failure_category', 'title_quality'}
    def measured_record(record):
        result = {key: value for key, value in record.items() if key not in derived_fields}
        if 'provider_metadata' in result:
            result['provider_metadata'] = {key: value for key, value in result['provider_metadata'].items()
                if key != 'local_report_complete_diagnostic_links'}
        return result
    return {
        'cases': cases,
        'records': sorted(
            [measured_record(record) for record in records if record['provider'] in providers],
            key=lambda record: record['attempt_id']),
    }


def _article_speed_population(cases, records, providers, *, execution_scope):
    """Describe one common-pass diagnostic over a complete declared matrix."""
    indexed = {}
    for record in records:
        if record['provider'] not in providers:
            continue
        key = (record['provider'], record['case_id'])
        if key in indexed:
            raise ValueError('Article latency requires one authoritative record per provider and task')
        indexed[key] = record
    if any((provider, cid) not in indexed for provider in providers for cid in cases):
        raise ValueError('Article latency requires the complete declared matrix')
    common = sorted(cid for cid, case in cases.items() if all(
        task_passed(indexed[provider, cid]['ok'], indexed[provider, cid]['metrics'],
                    case.get('task_kind', 'body'))
        for provider in providers))
    if not common:
        raise ValueError('Article latency has no commonly accepted tasks')
    rows = []
    for provider in providers:
        records_for_provider = [indexed[provider, cid] for cid in common]
        values = [record['elapsed_ms'] for record in records_for_provider]
        times = [record['started_at'] for record in records_for_provider if record.get('started_at')]
        rows.append({
            'provider': provider,
            'attempts': len(common),
            'values_ms': values,
            'mean_ms': statistics.mean(values),
            'p50_ms': percentile(values, .5),
            'p95_ms': percentile(values, .95),
            'recorded_start_dates': dict(sorted(Counter(time[:10] for time in times).items())),
            'latency_corrected_cases': [
                record['case_id'] for record in records_for_provider
                if 'latency_correction' in record.get('provider_metadata', {})
            ],
        })
    return {
        'providers': providers,
        'planned_case_count': len(cases),
        'common_pass_count': len(common),
        'common_pass_cases': common,
        'track_counts': dict(sorted(Counter(
            cases[cid].get('track', 'web') for cid in common).items())),
        'execution_scope': execution_scope,
        'rows': rows,
    }


def _render_article_speed(population):
    size = population['common_pass_count']
    metrics = [('mean_ms', '平均耗时', '全部耗时相加，再除以任务数'),
               ('p50_ms', 'P50 · 中位数', '一半任务的等待不超过这个值'),
               ('p95_ms', 'P95 · 较慢一端',
                f'本组仅 {size} 题，按报告算法等于最慢一题' if size < 20
                else '95% 的任务耗时不超过这个值')]
    html = '<div class="m-speed-grid">'
    for metric, title, explanation in metrics:
        values = [(row['provider'], row[metric]) for row in population['rows']]
        best, top = min(value for _, value in values), max(value for _, value in values)
        html += f'<div class="m-speed-column"><h4>{title}</h4><p>{explanation}</p>'
        for provider, value in values:
            label = escape(provider_name(provider, provider.title()))
            html += (f'<div class="m-speed-row"><span>{label}</span><b>{value/1000:.2f} s</b>'
                     '<div class="m-speed-track">'
                     f'<i class="{"best" if value == best else ""}" style="width:{value/top*100:.3f}%"></i>'
                     '</div></div>')
        html += '</div>'
    return html + '</div>'


def _render_article_resources(summary, selected):
    if summary is None:
        return ('<p>当前证据没有独立的客户端资源测量；本文不从耗时反推 CPU 或内存。</p>',
                '资源指标待独立同题测量后补充。')
    rendered, result = render_resource_comparison(summary, selected)
    interpretation = (
        f'{result["winner_label"]} 是本轮资源效率领先者，在 '
        f'{result["ranked_metric_count"]} 个可按低消耗比较的指标中取得 '
        f'{result["winner_count"]} 项最低。这里的资源领先不等于综合任务通过率领先。'
    )
    return rendered, interpretation


def _render_headless(out, cases, records):
    """Five-browser editorial view, derived from the unchanged report evidence."""
    from .report_components import comparable_body_reference
    data = headless_evidence(cases, records)
    audit_path = contained_path(out, 'result-review.json')
    data['independent_result_audit'] = json.loads(audit_path.read_text()) if audit_path.is_file() else None
    selected = ['moli', 'chromium', 'lightpanda', 'obscura', 'kitesurf']
    missing = sorted(set(selected) - set(data['providers']))
    if missing:
        raise ValueError('Five-browser article requires every reviewed provider; missing: ' + ', '.join(missing))
    speed_path = contained_path(out, 'speed-cohort/speed-comparison.json')
    if not speed_path.is_file():
        raise ValueError('Five-browser article requires speed evidence; missing speed-comparison.json')
    # The authored case explanations contain measured numbers as prose. Bind the
    # complete five-browser edition to its reviewed evidence so a later rebuild
    # cannot silently combine fresh charts with stale narrative measurements.
    reviewed_input = _headless_article_input(cases, records, selected)
    if _digest(reviewed_input) != 'd8ec77788689869fcfe0be31bf303bdcaa28c52947a09fcbda172c47e96422e5':
        raise ValueError('Five-browser article evidence changed; review case prose and rebind the article before rebuilding')
    def passed(r):
        return task_passed(r['ok'], r['metrics'], cases[r['case_id']].get('task_kind', 'body'))
    rows = {n: {r['case_id']: r for r in records if r['provider'] == n} for n in selected}
    pass_ids = {n: {cid for cid, r in rows[n].items() if passed(r)} for n in selected}
    totals = {n: len(pass_ids[n]) for n in selected}
    def label(n):
        return escape(provider_name(n, n.title()))
    def bars(values, unit='', maximum=None):
        top = maximum or max(v for _, v in values) or 1
        return '<div class="m-bars">' + ''.join(
            f'<div class="m-bar-row"><span class="m-bar-label">{label(n)}</span><div class="m-bar-track"><i style="width:{v/top*100:.3f}%" class="{"primary" if n == "moli" else ""}"></i></div><b>{v:g}{unit}</b></div>'
            for n, v in values) + '</div>'
    groups = [('网页类', ('web', 'dynamic', 'challenge')), ('文档与媒体', ('document', 'image', 'video'))]
    group_data = []
    for name, tracks in groups:
        ids = {cid for cid, c in cases.items() if c.get('track', 'web') in tracks}
        group_data.append({'name': name, 'cases': len(ids), 'moli': len(pass_ids.get('moli', set()) & ids), 'chromium': len(pass_ids.get('chromium', set()) & ids)})
    split = ''.join(f'<div class="m-split-panel"><span>{g["cases"]} 个任务 · {g["name"]}</span><div class="m-duel"><b>{g["moli"]}<small>Moli 1.1.1</small></b><em>:</em><b>{g["chromium"]}<small>Chromium</small></b></div><p>{"两者完成数相同" if g["moli"] == g["chromium"] else "净差 +" + str(g["moli"]-g["chromium"])+" 题"}</p></div>' for g in group_data)
    ma, ch = pass_ids.get('moli', set()), pass_ids.get('chromium', set())
    disagreement = [('共同通过', len(ma & ch)), ('Moli 单独通过', len(ma-ch)), ('Chromium 单独通过', len(ch-ma)), ('共同未通过', len(set(cases)-(ma|ch)))]
    four = ''.join(f'<div><b>{n}</b><span>{escape(name)}</span></div>' for name,n in disagreement)
    matrix = _table(['任务类型', *(label(n) for n in selected)], [(name, *(str(data['tracks'][track]['providers'].get(n,{}).get('passed',0))+'/'+str(data['tracks'][track]['cases']) for n in selected)) for track,name in [('web','普通网页'),('dynamic','动态网页'),('challenge','热门站点'),('document','文档'),('image','图片'),('video','视频')]], '每题等权。速度和全文指标不计入任务通过率；普通网页占总样本的三分之二。')
    quality_data = {}
    for n in ('moli','chromium'):
        eligible = [r for r in rows.get(n,{}).values() if cases[r['case_id']].get('track','web')=='web' and comparable_body_reference(cases[r['case_id']]) and r['metrics'].get('word_f1') is not None]
        quality_data[n] = {'cases':len(eligible), **{k:_mean(r['metrics'].get(k) for r in eligible) for k in ('word_precision','word_recall','word_f1')}}
    quality = _table(['全文指标','Moli 1.1.1','Chromium'], [(name,*(_percent(quality_data[n][k]) for n in ('moli','chromium'))) for k,name in [('word_recall','参考正文找回比例 · Recall'),('word_precision','输出文字纯净度 · Precision'),('word_f1','综合文字重合 · F1')]], f'{quality_data["moli"]["cases"]} 个具有完整可比参考的普通网页，取逐题算术平均，包含已测失败项。文字重合不等于事实准确率。')
    speed = json.loads(speed_path.read_text())
    if _digest(speed) != '53ebd9a2ec147e397ab4def009262701883aa22bb7f4b4dfb2c56dad5c0538db':
        raise ValueError('Article speed evidence changed; review latency prose before rebuilding')
    speed_inputs = {}
    for name in ('dataset', 'results'):
        extension = 'json' if name == 'dataset' else 'jsonl'
        raw = contained_path(out, f'speed-cohort/{name}.{extension}').read_bytes()
        if hashlib.sha256(raw).hexdigest() != speed['bindings'][name + '_sha256']:
            raise ValueError('Article speed source evidence changed; review latency prose before rebuilding')
        speed_inputs[name] = (json.loads(raw) if name == 'dataset'
                              else parse_jsonl(raw, skip_blank=True))
    main_speed = _article_speed_population(cases, records, selected,
        execution_scope='Historical authoritative main-evaluation calls assembled across dates and batches; includes declared latency corrections. Not a same-window controlled speed comparison.')
    paired_speed = _article_speed_population(
        {case['id']: case for case in speed_inputs['dataset']['cases']}, speed_inputs['results'], selected,
        execution_scope='Separate predeclared 20-page ordinary-web speed run; use all tasks accepted by the five displayed configurations. Small-sample P95 is the observed maximum.')
    paired_speed['bindings'] = speed['bindings']
    data['editorial_speed'] = {'main': main_speed, 'same_window': paired_speed}
    resource_path = contained_path(out, 'resource-cohort/resource-comparison.json')
    resource_summary = json.loads(resource_path.read_text()) if resource_path.is_file() else None
    resource_table, resource_interpretation = _render_article_resources(resource_summary, selected)
    data['editorial_resources'] = resource_summary
    pdf = rows.get('moli',{}).get('document-01')
    pdf_chars = pdf['metrics'].get('content_chars',0) if pdf else 0
    pdf_ms = pdf['elapsed_ms'] if pdf else 0
    pdf_meta = pdf.get('provider_metadata',{}) if pdf else {}
    cases_links = ' · '.join(_attempt(rows['moli'][cid], name) for cid,name in [('document-01','PDF 正文'),('image-10','PNG 原文件'),('challenge-01','商品列表')] if cid in rows.get('moli',{}))
    from .report_article_cases import render_article_cases
    data['editorial'] = {'providers':selected,'version_selection':'Moli 1.1.1 selected after observing version totals; one complete version, no per-case selection','totals':totals,'groups':group_data,'disagreements':dict(disagreement),'full_reference_quality':quality_data}
    latency_rows = {row['provider']: row for row in main_speed['rows']}
    reductions = {metric: (1 - latency_rows['moli'][metric] / latency_rows['chromium'][metric]) * 100
                  for metric in ('mean_ms', 'p50_ms', 'p95_ms')}
    data['editorial']['chromium_latency_reduction_pct'] = reductions
    values = {
        'time_window': escape(' 至 '.join(
            datetime.fromisoformat(t).astimezone(timezone.utc).strftime('%Y-%m-%d')
            for t in data['time_window'])),
        'title': HEADLESS_TITLE,
        'total': str(len(cases)),
        'moli_total': str(totals['moli']),
        'moli_rate': f'{totals["moli"]/len(cases):.0%}',
        'delta': str(totals['moli'] - totals['chromium']),
        'ranking': bars([(n, totals[n]) for n in selected], maximum=len(cases)),
        'overview_ranking': _table(
            ['浏览器', '综合任务通过率', '普通网页', '动态网页', '热门站点', '文档', '图片', '视频'],
            [(label(n), f'<strong>{totals[n]/len(cases):.1%}</strong><br>{totals[n]}/{len(cases)}',
              *(str(data['tracks'][track]['providers'][n]['passed']) + '/' + str(data['tracks'][track]['cases'])
                for track in ('web', 'dynamic', 'challenge', 'document', 'image', 'video')))
             for n in sorted(selected, key=lambda n: -totals[n])],
            '五款浏览器综合通过率。每款执行相同的 150 道任务，每题等权；分组列为通过数 / 本组任务数。'),
        'split': split, 'four': four, 'matrix': matrix,
        'quality': quality, 'speed_main': _render_article_speed(main_speed),
        'speed': _render_article_speed(paired_speed),
        'resources': resource_table,
        'resource_interpretation': escape(resource_interpretation),
        'resource_methodology': (
            '<details class="m-details"><summary>资源指标怎样测</summary><p>资源数据来自四种本地方法的独立同题测量，不回填到历史通过率调用。每次调用启动一个受限的 Linux 客户端容器及独立网络；宿主控制器和容器启停不计入。CPU 与网络总量读取累计计数，内存与速率分位数按 50 ms 采样。</p><p>资源主表统计预先确定的全部任务，包括失败调用，并列出各方法的通过数；成功调用另作条件统计，共同通过的任务仅作诊断。短于采样间隔的内存或速率峰值可能遗漏；Kitesurf 等远端服务的服务端 CPU、内存与上游流量不可观测，因而不进入资源表。</p></details>'
            if resource_summary is not None else ''
        ),
        'pdf_chars': f'{pdf_chars:,}',
        'pdf_seconds': f'{pdf_ms/1000:.2f}',
        'pdf_fetch': f'{pdf_meta.get("raw_fetch_ms", 0)/1000:.2f}',
        'pdf_parse': f'{pdf_meta.get("document_extract_ms", 0)/1000:.2f}',
        'case_links': cases_links,
        'report_url': escape(public_report_url(), quote=True),
        'browser_profiles': '',
        'case_workbench': render_article_cases(out, cases, records),
        'moli_web': str(data['tracks']['web']['providers']['moli']['passed']),
        'chromium_web': str(data['tracks']['web']['providers']['chromium']['passed']),
        'moli_docs': str(data['tracks']['document']['providers']['moli']['passed']),
        'chromium_docs': str(data['tracks']['document']['providers']['chromium']['passed']),
        'quality_count': str(quality_data['moli']['cases']),
        'mean_reduction': f'{reductions["mean_ms"]:.1f}',
        'p50_reduction': f'{reductions["p50_ms"]:.1f}',
        'p95_reduction': f'{reductions["p95_ms"]:.1f}',
    }
    from .report_article_figures import render_article_figures
    online_figures, embedded_figures, data['editorial_figures'] = render_article_figures(out)
    template = Template((Path(__file__).parent/'templates/interpretation-headless.html').read_text())
    body = template.substitute(values | online_figures)
    standalone_body = template.substitute(values | embedded_figures)
    contained_path(out,'guide').mkdir(parents=True,exist_ok=True)
    contained_path(out,'guide/interpretation-evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    contained_path(out,'guide/article.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+f'<title>{escape(HEADLESS_TITLE)}</title></head><body class="standalone-article">{standalone_body}</body></html>')
    return body + '<aside id="evidence" class="m-downloads"><a href="article.html" download="moli-web-fetch.html">下载独立 HTML</a> · <a href="interpretation-evidence.json" download>本文计算明细</a></aside>'


def current_browser_article_model(out, cases, records, binding):
    """Bind new cohort prose to measured inputs; derive every comparison value."""
    path = contained_path(out, binding['path'])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding['sha256']:
        raise ValueError('Browser interpretation content hash mismatch')
    content = json.loads(raw)
    providers = sorted({row['provider'] for row in records})
    if content.get('schema') != 'webfetch_browser_interpretation/1':
        raise ValueError('Unsupported browser interpretation content schema')
    if content.get('evidence_sha256') != _digest(_headless_article_input(cases, records, providers)):
        raise ValueError('Browser interpretation evidence changed')
    if set(content.get('providers', {})) != set(providers):
        raise ValueError('Browser interpretation must explain every participant')
    if not cases or not providers or any(c.get('track', 'web') not in TRACK_NAMES for c in cases.values()):
        raise ValueError('Browser interpretation requires supported nonempty task families')
    expected = {(name, cid) for name in providers for cid in cases}
    indexed = {(row['provider'], row['case_id']): row for row in records}
    if len(indexed) != len(records) or set(indexed) != expected:
        raise ValueError('Browser interpretation requires the complete declared matrix')
    def passed(row):
        return task_passed(row['ok'], row['metrics'], cases[row['case_id']].get('task_kind', 'body'))
    def distribution(rows):
        times = [row['elapsed_ms'] for row in rows]
        return {'n': len(rows), 'mean': statistics.mean(times) if times else None,
                'p50': statistics.median(times) if times else None,
                'p95': percentile(times, .95) if times else None}
    tracks = [track for track in TRACK_NAMES if any(c.get('track', 'web') == track for c in cases.values())]
    stats = {}
    from .report_interfaces import method_provenance, method_version
    provenance = method_provenance(out)
    for name in providers:
        rows = [indexed[name, cid] for cid in cases]
        successes = [row for row in rows if passed(row)]
        failures = [row for row in rows if not passed(row)]
        chapter = content['providers'][name]
        specimen = indexed.get((name, chapter.get('specimen_case_id')))
        if specimen is None or (failures and passed(specimen)):
            raise ValueError('Browser chapter requires an evidenced failure specimen when failures exist')
        explanation = chapter.get('explanation')
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError('Browser chapter requires a measured explanation')
        versions = sorted({str(method_version(row,provenance) or '') for row in rows} - {''})
        groups = {track: [row for row in rows if cases[row['case_id']].get('track', 'web') == track] for track in tracks}
        stats[name] = {'version': ' / '.join(versions) if versions else '托管服务，内部版本未公开' if name == 'kitesurf' else '未提供版本',
                       'passed': len(successes), 'pass_rate': len(successes)/len(cases),
                       'tracks': {track: {'passed': sum(passed(row) for row in subset), 'n': len(subset)} for track,subset in groups.items()},
                       'all': distribution(rows), 'successful': distribution(successes),
                       'failure_counts': dict(Counter(row.get('error_type') or 'acceptance' for row in failures)),
                       'specimen': specimen}
    for field in ('title', 'intro', 'scoring', 'conclusions'):
        if not isinstance(content.get(field), str) or not content[field].strip():
            raise ValueError('Browser interpretation is missing prose: ' + field)
    tracks = [track for track in TRACK_NAMES if any(c.get('track', 'web') == track for c in cases.values())]
    return {'content': content, 'providers': providers, 'stats': stats, 'tracks': tracks,
            'cases': len(cases), 'results': len(records)}


def _offline_browser_article(body, out, cases, model):
    """Embed specimen evidence; an independent download needs no report host."""
    from bs4 import BeautifulSoup
    from .artifacts import record_public_call_log_bindings, verify_public_call_log
    document = BeautifulSoup(body, 'html.parser')
    for script in document.select('script'):
        script.decompose()
    targets = {}
    appendices = []
    def evidence(path, anchor, title, payload):
        if path in targets:
            return
        targets[path] = '#'+anchor
        appendices.append('<details class="wrap" id="'+escape(anchor)+'"><summary>'
                          +escape(title)+'</summary>'+payload+'</details>')
    for name in model['providers']:
        row = model['stats'][name]['specimen']
        case = cases[row['case_id']]
        evidence('../datasets/'+case['id']+'.html', 'offline-task-'+case['id'],
                 '任务与固定验收规则 · '+case['id'],
                 '<pre>'+escape(json.dumps(case,ensure_ascii=False,indent=2))+'</pre>')
        raw = contained_path(out,row['artifact_path']).read_text(encoding='utf-8',errors='replace')
        payload = '<h3>完整实际返回</h3><pre data-standalone-output="'+escape(name)+'">'+escape(raw)+'</pre>'
        payload += '<h3>结果与评分</h3><pre>'+escape(json.dumps(row,ensure_ascii=False,indent=2))+'</pre>'
        for binding in record_public_call_log_bindings(row):
            log = verify_public_call_log(out,binding).read_text()
            payload += '<h3>调用日志</h3><pre>'+escape(log)+'</pre>'
        evidence('../attempts/'+row['attempt_id']+'.html', 'offline-result-'+row['attempt_id'],
                 '完整结果与调用证据 · '+name, payload)
    if (out/'result-review.json').is_file():
        evidence('../review/index.html','offline-review','独立审查记录',
                 '<pre>'+escape((out/'result-review.json').read_text())+'</pre>')
    for anchor in document.select('a[href]'):
        href = anchor['href']
        if href in targets:
            anchor['href'] = targets[href]
        elif not href.startswith(('https://','http://','#')):
            raise ValueError('Independent article has an unbound evidence target: '+href)
    return str(document)+''.join(appendices)


def _render_current_browser_article(out, cases, records, binding):
    model = current_browser_article_model(out, cases, records, binding)
    content, names, stats = model['content'], model['providers'], model['stats']
    from .methods import browser_method_label, native_browser_engine
    engine_count = len({native_browser_engine(name) or name for name in names})
    def label(name):
        return escape(browser_method_label(name) if browser_method_label(name) != name else name.title())
    def paragraph(value):
        return '<p>' + escape(value).replace('\n\n', '</p><p>') + '</p>'
    def section(title, body, anchor):
        return '<section class="section" id="'+anchor+'"><h2>'+escape(title)+'</h2>'+body+'</section>'
    def metric(name, key, value, values, higher=False, suffix=''):
        valid = [v for v in values if v is not None]
        extreme = (max(valid) if higher else min(valid)) if valid else None
        winner = value is not None and value == extreme and valid.count(extreme) == 1
        text = '—' if value is None else (f'{value:.1%}' if key == 'pass_rate' or key.startswith('family_') else f'{value:,.0f}{suffix}')
        return ('<span data-browser-metric="'+escape(key)+'" data-provider="'+escape(name)
                +'" data-value="'+('' if value is None else str(value))+'"'
                + (' data-winner="true" class="good"' if winner else '')+'>'+text+'</span>')
    def latency_table(population):
        return _table(['浏览器', '样本数', '平均耗时 · ms', 'P50 · ms', 'P95 · ms'], [
            (label(name), str(stats[name][population]['n']), *(
                metric(name, population+'_'+key, stats[name][population][key],
                       [stats[n][population][key] for n in names]) for key in ('mean','p50','p95')))
            for name in names], '同一行三个耗时指标使用相同样本；每个逻辑结果只计一次。')
    body = '<article class="wrap browser-interpretation" data-browser-interpretation="1"><h1>'+escape(content['title'])+'</h1>'
    dates = sorted({row['started_at'][:10] for row in records})
    body += '<p class="tiny">调用日期：'+escape('、'.join(dates))+'</p>'
    body += '<p class="lead">'+str(model['cases'])+' 个任务；'+str(engine_count)+' 种浏览器、'+str(len(names))+' 种配置，'+str(model['results'])+' 个逻辑结果。</p>'+paragraph(content['intro'])
    body += section('整体任务通过率', _table(['浏览器','实测版本','通过数 / 总数','任务通过率'], [
        (label(name), escape(stats[name]['version']), f'{stats[name]["passed"]}/{model["cases"]}',
         metric(name,'pass_rate',stats[name]['pass_rate'],[stats[n]['pass_rate'] for n in names],True))
        for name in names], '全体任务每题等权；通过表示满足该题的正文、结构、资源或文件验收规则。'), 'overall')
    def family_cell(name, track):
        group = stats[name]['tracks'][track]
        rate = group['passed']/group['n']
        values = [stats[n]['tracks'][track]['passed']/stats[n]['tracks'][track]['n'] for n in names]
        return f"{group['passed']}/{group['n']}<br>" + metric(name,'family_'+track,rate,values,True)
    body += section('各专项结果', _table(['任务类型',*(label(name) for name in names)], [
        (escape(TRACK_NAMES[track]), *(family_cell(name,track) for name in names))
        for track in model['tracks']], '分组单元格为通过数 / 该组总数及通过率；专项结果不能代替整体排名。'), 'families')
    from .report_interfaces import browser_task_interface_matrix
    interfaces = browser_task_interface_matrix(cases, names)
    native = _table(['浏览器','Markdown 能力与本次调用'], [
        ('<span style="white-space:nowrap">'+label(name)+'</span>', escape(interfaces['capabilities'][name])) for name in names], 'Markdown 能力指网页转换；不代表 PDF、DOCX 或媒体文件解析能力。')
    route_rows = []
    for row in interfaces['rows']:
        cells = []
        for name in names:
            parts = row['cells'][name]
            cells.append('<div data-browser-interface="'+escape(name)+'" data-track="'+escape(row['track'])+'">'
                         + '<br><br>'.join('<span data-route-mode="'+escape(part['mode'])+'" data-case-count="'+str(part['count'])+'">'
                           +str(part['count'])+' 题：'+escape(part['description'])+'<br>等待：'+escape(part['wait'])+'</span>' for part in parts)+'</div>')
        route_rows.append((escape(TRACK_NAMES[row['track']])+f" · {row['count']} 题", *cells))
    matrix = _table(['任务类型',*(label(name) for name in names)], route_rows, '每格列出获取接口、额外处理及最终输出；同类中的不同交付格式分别列出题数。')
    body += section('各类任务的获取与转换方式',
        '<p>按题目类型使用参评浏览器合适的官方原生接口。正文直接采用该接口返回的文字；不会改取 HTML、JSON DOM 或文档字节后替它提取正文。Chromium 没有原生 Markdown，网页 HTML 使用标准转换包。下表按固定任务输入列出实际选择的处理链，失败不补成成功，也不切换引擎。每例完成了哪些步骤、返回什么内容，均可在结果详情中查看。文档正文、资源地址、原始文件和媒体元数据有各自的交付格式，不强行转成 Markdown。</p>'
        +native+'<div class="browser-interface-matrix">'+matrix+'</div>'
        +'<p>Obscura 的正文提取脚本与官方 CLI、LP.getMarkdown 相同；评测包装只附加最终网址和标题，不修改转换算法。JSON 包装只解包原生输出，不改写内容。只有 Chromium 的网页 HTML 使用 node-html-markdown 2.0.0；所有浏览器都不使用外部 PDF/DOCX 解析器补正文。资源脚本只读取已加载页面，嵌入文档只读浏览器内已加载的查看器文字。最终网址作为辅助信息，不单独决定正文任务是否通过。获取、等待、网页转换、原生内容投影与附件读取计入同一 45 秒预算。Moli 附件的原生 CDP 导航与原始附件接口是两步操作，次数和耗时保留在结果记录，不因评分失败而触发。所有结果采用同一评分器和固定验收规则。</p>', 'interfaces')
    flow = 'flowchart LR\n A[同一目标网址与验收规则] --> B[各浏览器原生访问与提取]\n B --> C[保留完整输出与调用日志]\n C --> D[统一评分]\n D --> E[整体与专项结果]'
    fallback = ' → '.join('<span class="tag">'+x+'</span>' for x in ('目标网址与规则','浏览器原生访问','完整输出与日志','统一评分','整体与专项'))
    mechanism = '<div class="wmb-flow"><div data-wmb-flow-fallback>'+fallback+'</div><pre class="mermaid" hidden>'+escape(flow)+'</pre></div>'
    body += section('怎样判断一次获取是否可用', mechanism+paragraph(content['scoring']), 'scoring')
    body += section('全部调用的耗时', '<p>所有浏览器使用相同任务分母，包含成功与失败调用。耗时较短只说明更快返回，不等于更高的任务通过率。</p>'+latency_table('all'), 'all-latency')
    body += section('通过任务的耗时', '<p>只计各浏览器自己通过的任务。各行样本可能不同，须结合通过数阅读；这张表不改变任务范围或整体排名。</p>'+latency_table('successful'), 'successful-latency')
    resource = contained_path(out, 'resource-cohort/resource-comparison.json')
    if resource.is_file():
        table, explanation = _render_article_resources(json.loads(resource.read_text()), names)
        body += section('客户端资源消耗', table+paragraph(explanation), 'resources')
    errors = {'acceptance':'内容或交付验收未通过','timeout':'访问或提取超时',
              'browser_exit':'浏览器进程返回错误','browser_error':'浏览器访问或运行错误',
              'http_status':'网页返回错误状态','empty_response':'返回内容为空','tool_error':'工具执行错误'}
    for name in names:
        stat, chapter = stats[name], content['providers'][name]
        counts = stat['failure_counts']
        distribution = _table(['程序分类','未通过数量'], [
            (escape(errors.get(key, key)), '<span data-browser-failure="'+escape(name)+'" data-category="'+escape(key)+'" data-count="'+str(count)+'">'+str(count)+'</span>')
            for key,count in sorted(counts.items())], f'{name} 的全部未通过结果，每个结果归入一个程序分类。') if counts else '<p>本轮所有任务均通过。</p>'
        row = stat['specimen'];case = cases[row['case_id']]
        raw = contained_path(out,row['artifact_path']).read_text(encoding='utf-8',errors='replace')
        reference = case.get('ground_truth_main_content') or case.get('reference_answer') or json.dumps({
            key:case[key] for key in ('required_facts','body_requirements','acceptance_contract','expected_resources','resource_output_contract') if case.get(key)},ensure_ascii=False,indent=2)
        error = row.get('error_message') or ('未满足完整验收规则；逐项期望与实际值见结果详情。' if counts else '满足该题验收规则。')
        example = '<details open data-browser-specimen="'+escape(name)+'"><summary>'+escape(case.get('name',case['id']))+' · '+escape(case['id'])+'</summary>'
        example += '<p><a href="'+escape(case['url'],quote=True)+'">原始页面</a> · <a href="../datasets/'+escape(case['id'],quote=True)+'.html">任务与验收规则</a> · <a data-browser-result="'+escape(name)+'" href="../attempts/'+escape(row['attempt_id'],quote=True)+'.html">完整结果、评分与日志</a></p>'
        example += '<h4>参考范围或验收要求（节选）</h4><pre>'+escape(reference[:1800])+'</pre><h4>实际返回（节选）</h4><pre>'+escape(raw[:1800])+'</pre><h4>程序返回或验收结果</h4><pre data-browser-error="'+escape(name)+'">'+escape(error)+'</pre></details>'
        body += section((browser_method_label(name) if browser_method_label(name) != name else name.title())+'：'+str(stat['passed'])+'/'+str(model['cases'])+' 题通过',
                        '<div data-browser-chapter="'+escape(name)+'">'+paragraph(chapter['explanation'])+distribution+example+'</div>', 'browser-'+name)
    review_path = contained_path(out,'result-review.json')
    review = json.loads(review_path.read_text()) if review_path.is_file() else None
    body += section('结论与审查范围', paragraph(content['conclusions'])+
                    ('<p>独立审查范围见<a href="../review/index.html">审查记录</a>，未审查结果仍按程序验收结果展示。</p>' if review else
                     '<p>独立结果审查：0/'+str(model['results'])+'。未经独立复判的程序通过结果：'+str(sum(stats[n]['passed'] for n in names))+'。本轮报告使用统一评分器的原始验收结果。</p>'), 'conclusions')
    body += '</article><script>document.addEventListener("DOMContentLoaded",()=>{if(typeof wmbRenderFlow==="function")wmbRenderFlow()});</script>'
    evidence = {'schema':'webfetch_browser_interpretation_evidence/1','input_sha256':content['evidence_sha256'],
                'cases':model['cases'],'results':model['results'],'providers':names,'interfaces':interfaces,
                'stats':{n:{k:v for k,v in stats[n].items() if k!='specimen'} for n in names}}
    guide = contained_path(out,'guide');guide.mkdir(parents=True,exist_ok=True)
    (guide/'interpretation-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
    from .report import EXTRA
    standalone = _offline_browser_article(body,out,cases,model)
    (guide/'article.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(content['title'])+'</title><style>'+STYLE+EXTRA+'</style></head><body>'+standalone+'</body></html>')
    return body+'<aside class="wrap"><a href="article.html" download>下载独立 HTML</a> · <a href="interpretation-evidence.json">本文计算明细</a></aside>'


def check_current_browser_article(out, cases, records, binding):
    """Verify numerical populations, unique highlights and complete drilldowns."""
    from html.parser import HTMLParser
    from urllib.parse import urlsplit
    model = current_browser_article_model(out, cases, records, binding)
    from .report_interfaces import browser_task_interface_matrix
    interfaces = browser_task_interface_matrix(cases, model['providers'])
    expected_interfaces = {(name,row['track']):[(part['mode'],part['count'],str(part['count'])+' 题：'+part['description']+'等待：'+part['wait']) for part in row['cells'][name]]
                           for row in interfaces['rows'] for name in model['providers']}
    expected = {}
    failures = {}
    for name in model['providers']:
        stats = model['stats'][name]
        expected[name, 'pass_rate'] = stats['pass_rate']
        for population in ('all','successful'):
            for key in ('mean','p50','p95'):
                expected[name, population+'_'+key] = stats[population][key]
        for track, group in stats['tracks'].items():
            expected[name, 'family_'+track] = group['passed']/group['n']
        failures.update({(name,key):count for key,count in stats['failure_counts'].items()})
    class Inspect(HTMLParser):
        def __init__(self):
            super().__init__();self.metrics=[];self.failures=[];self.chapters=[];self.errors=[];self.links=[];self.active=None
            self.interfaces={};self.interface=None;self.route=None
        def handle_starttag(self, tag, attrs):
            data=dict(attrs)
            if 'data-browser-interface' in data:
                self.interface=(data['data-browser-interface'],data['data-track'])
                if self.interface in self.interfaces:raise ValueError('Duplicate browser task interface')
                self.interfaces[self.interface]=[]
            if 'data-route-mode' in data:
                if self.interface is None:raise ValueError('Browser route has no task interface')
                self.route=[data['data-route-mode'],int(data['data-case-count']),'']
                self.interfaces[self.interface].append(self.route)
            if 'data-browser-metric' in data:
                self.active=[data,''];self.metrics.append(self.active)
            if 'data-browser-failure' in data:
                self.failures.append((data['data-browser-failure'],data['data-category'],int(data['data-count'])))
            if 'data-browser-chapter' in data:self.chapters.append(data['data-browser-chapter'])
            if 'data-browser-error' in data:self.errors.append(data['data-browser-error'])
            if 'data-browser-result' in data:self.links.append((data['data-browser-result'],data.get('href')))
        def handle_data(self,text):
            if self.active is not None:self.active[1]+=text
            if self.route is not None:self.route[2]+=text
        def handle_endtag(self,tag):
            if tag=='span':self.active=None;self.route=None
    for relative in ('guide/index.html','guide/article.html'):
        page=contained_path(out,relative)
        inspect=Inspect();inspect.feed(page.read_text())
        actual_interfaces={key:[tuple(part) for part in parts] for key,parts in inspect.interfaces.items()}
        if actual_interfaces != expected_interfaces:
            raise ValueError('Browser task interface matrix is missing or differs from declared tasks')
        actual={}
        for data,text in inspect.metrics:
            key=(data['data-provider'],data['data-browser-metric'])
            if key in actual or key not in expected:raise ValueError('Duplicate or unknown browser metric')
            value=expected[key]
            if data.get('data-value') != ('' if value is None else str(value)):
                raise ValueError('Browser metric value differs from evidence')
            peers=[v for (name,metric),v in expected.items() if metric==key[1] and v is not None]
            high=key[1]=='pass_rate' or key[1].startswith('family_')
            extreme=(max(peers) if high else min(peers)) if peers else None
            winner=value is not None and value==extreme and peers.count(extreme)==1
            if (data.get('data-winner')=='true') != winner:
                raise ValueError('Browser metric unique highlight is missing or wrong')
            visible='—' if value is None else (f'{value:.1%}' if high else f'{value:,.0f}')
            if text.strip()!=visible:raise ValueError('Visible browser metric differs from evidence')
            actual[key]=value
        if actual != expected:raise ValueError('Browser metric population is incomplete')
        if len(inspect.failures)!=len(failures) or {(n,k):v for n,k,v in inspect.failures}!=failures:
            raise ValueError('Browser failure distribution differs from evidence')
        for values in (inspect.chapters,inspect.errors):
            if Counter(values)!=Counter(model['providers']):raise ValueError('Browser chapter or program error evidence is missing')
        if Counter(n for n,link in inspect.links)!=Counter(model['providers']):
            raise ValueError('Browser result drilldown is missing')
        for name,link in inspect.links:
            attempt = model['stats'][name]['specimen']['attempt_id']
            expected_link = '#offline-result-'+attempt if relative.endswith('article.html') else '../attempts/'+attempt+'.html'
            if link!=expected_link or (not link.startswith('#') and not (page.parent/urlsplit(link).path).is_file()):
                raise ValueError('Browser result evidence target is broken')
        if relative.endswith('article.html'):
            from bs4 import BeautifulSoup
            offline = BeautifulSoup(page.read_text(),'html.parser')
            if offline.select('script,link[rel="stylesheet"]'):
                raise ValueError('Independent article requires external runtime')
            for name in model['providers']:
                row = model['stats'][name]['specimen']
                anchor = offline.find(id='offline-result-'+row['attempt_id'])
                output = anchor.select_one('[data-standalone-output]') if anchor else None
                if output is None or output.get_text() != contained_path(out,row['artifact_path']).read_text(encoding='utf-8',errors='replace'):
                    raise ValueError('Independent article omits complete specimen output')
    return model


def supports_authored_explainer(cases):
    """Do not apply a mixed-track article to a different task family."""
    # Historical authored prose has no risk-control chapter. New bound cohort
    # content is selected before this legacy presentation guard.
    return all(case.get('track', 'web') in set(TRACK_NAMES) - {'risk_control'} for case in cases.values())


def render_explainer(out, cases, records, *, table=None, cell=None, link=None, authored=True, content_binding=None):
    """One authored body; only image URL serialization differs for the offline export."""
    out = Path(out)
    dataset_path = out / 'dataset.json'
    if content_binding is None and dataset_path.is_file():
        content_binding = json.loads(dataset_path.read_text()).get('comparison', {}).get('report_interpretation')
    if authored and content_binding is not None:
        return _render_current_browser_article(out, cases, records, content_binding)
    if (not authored or not supports_authored_explainer(cases)
            or (records and all(row.get('provider_type') == 'fixture' for row in records))):
        passes = sum(task_passed(row['ok'], row['metrics'],
                     cases[row['case_id']].get('task_kind', 'body')) for row in records)
        return (f'<section class="wrap"><h1>本报告的任务范围与验收规则</h1>'
                f'<p>本报告包含 {len(cases)} 个任务、{len(records)} 个逻辑结果，'
                f'任务通过 {passes}/{len(records)}。结论仅适用于已登记的任务和规则。</p>'
                '<p><a href="../datasets/index.html">查看全部任务及验收规则</a> · '
                '<a href="../analysis/index.html">查看结果、实际返回及失败证据</a></p></section>')
    if _is_headless_comparison(records):
        return _render_headless(out,cases,records)
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
    t, total = data['tracks']['web']['summary'], data['totals']
    tracks, web = data['tracks'], data['tracks']['web']
    values = {'title': escape(TITLE), 'cases': str(data['case_count']),
              'annotation_figure': '', 'validation_figure': '', 'scoring_figure': '',
              'providers': str(data['provider_count']), 'records': f'{total["records"]:,}',
              'web_records': f'{t["records"]:,}', 'web_providers': str(t['provider_count']),
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
              'environment_link': _link('reproduce/index.html', '执行批次与接口配置'),
              'evidence_link': _link('guide/interpretation-evidence.json', '本文计算明细'),
              'reproduce_link': _link('reproduce/index.html', '复现说明'),
              'article_link': _link('guide/interpretation-evidence.json', '独立 HTML 与证据下载'),
              'raw_file_records': str(data['raw_file']['records']),
              'raw_file_missing': str(data['raw_file']['missing_raw_file_pass']),
              'raw_file_verified': str(data['raw_file']['verified_pass']),
              'dynamic_section': ''}
    sample = data['dynamic']['zero_pass_nonzero_f1']
    if sample:
        record = next(r for r in records if r['attempt_id'] == sample['attempt_id'])
        artifact = contained_path(out, record['artifact_path'])
        raw = artifact.read_text() if artifact.exists() else ''
        sample['script_payload_observed'] = (sample['case_id'] == 'dynamic-delayed'
            and 'setTimeout' in raw and '\\u201c' in raw and '"tags"' in raw)
    values['dynamic_section'] = _dynamic_section(data, cases)
    audit = data['initial_reference_audit']
    values['initial_audit'] = (
        f'<p>初始源文档审计进一步缩小了这批样本的解释范围：静态网站正文 {audit["web_case_count"]} 例中，'
        f'{audit["exact_reconstructions"]} 例的冻结参考，可以从已保存的初始 HTML 经参考提取与规范化后精确复现。'
        '这是对参考文本可恢复性的检查，既不意味着它们都是可见静态正文，也不意味着 CowAgent 使用了同样的提取器。'
        '嵌入脚本的载荷仍需要正确解析。高网页通过率因此不能直接外推到初始响应之外才到达的内容。'
        + _link('guide/interpretation-evidence.json', '逐例审计摘要与校验值') + '保留了核查依据。</p>'
        if audit else '<p>当前输入没有与案例及参考校验值完整匹配的初始源文档审计，'
        '不能汇总动态内容依赖比例。类别标签既不能证明正文已经在初始 HTML 中，也不能证明必须执行 JavaScript。</p>')
    values['time_window'] = _window_text(web['time_window'])
    values['scope_dates'] = _table(['任务范围', '任务数', '实际方法数', '调用开始时间（UTC）'], [
        (TRACK_NAMES[k], str(v['case_count']), str(len(v['providers'])), escape(_window_text(v['time_window'])))
        for k, v in tracks.items() if v['case_count']],
        '样本与时间范围：各类任务按各自契约验收，日期不构成同时间受控对照')
    speed_table, speed_note, speed = _speed_comparison(out)
    data['same_window_speed'] = speed
    values['speed_section'] = (
        '<section id="speed"><h2>同一时间窗的速度怎样读</h2>'
        '<p>主结果来自不同批次，耗时只用于逐次诊断；下面单列预先声明的同窗子集。'
        '全调用统计保留失败与截止超时，回答完整任务队列要等多久；通过调用统计只描述各方法已通过任务，'
        '成功分母可能不同，不能单独拿来排同题速度。</p>' + speed_note + speed_table
        + '<p>' + _link('speed-cohort/speed-comparison.json', '速度汇总与样本分母') + '与'
        + _link('speed-cohort/results.jsonl', '测速逐次记录') + '保留可复核证据。</p></section>'
        if speed else '')
    focused_hosts = {'github.com', 'docs.python.org'}
    focused_cases = sum(urlsplit(case.get('url', '')).hostname in focused_hosts
                        for case in cases.values() if case.get('track', 'web') == 'web')
    values['web_sample_scope'] = (
        f'{web["case_count"]} 个普通网页中，GitHub 与 Python 官方文档占 {focused_cases} 个，'
        '样本偏向技术文档')
    wm = web['providers'].get('moli-webfetch')
    dm = tracks['dynamic']['providers'].get('moli-webfetch')
    values['lead'] = (f'同一配置的 Moli，在静态网站正文任务中通过 {wm["task_pass"]}/{wm["records"]}，'
        f'在动态网站正文中通过 {dm["task_pass"]}/{dm["records"]}。'
        '网页高分为什么没有自然延伸到动态内容？关键在于目标内容在哪里、何时到达，以及接口最终保留了什么。'
        if wm and dm else '抓取接口正常返回之后，正文仍可能缺失、混入导航，或丢掉详情地址。'
        '这轮实测从这些差异出发，判断哪些方法值得接入，哪些结论还需要试验。')
    values['dateline'] = (datetime.fromisoformat(data['time_window']['last_start_utc']).strftime('%Y-%m-%d')
                          if data['time_window']['last_start_utc'] else '调用日期未提供')
    failure_rows = [('任务通过', t['task_pass']), ('正常返回但任务未通过', t['post_response_failures']),
                    ('请求失败', t['request_failures'])]
    values['failure_figure'] = '<figure class="editorial-figure" id="failure-figure"><div class="editorial-bars">' + ''.join(
        f'<div class="editorial-bar-row"><span>{label}</span><span class="editorial-bar-track" aria-hidden="true">'
        f'<i style="width:{count / max(t["records"], 1) * 100:.4f}%"></i></span>'
        f'<b>{count:,} / {t["records"]:,}</b></div>' for label, count in failure_rows
    ) + f'</div><figcaption>静态网站正文调用的互斥状态分解；{values["time_window"]}。正常返回不等于源站 HTTP 200。</figcaption></figure>'
    selected = ['cloudflare-markdown', 'exa', 'cowagent-built-in']
    values['quality_figure'] = _table(['方法', '任务通过 / 调用', '平均 F1', 'F1 样本数'], [
        (_link('providers/' + p + '.html', PROVIDER_NAMES[p]), f'{s["task_pass"]}/{s["records"]}',
         f'{s["mean_f1"]:.4f}' if s['mean_f1'] is not None else '未测', str(s['f1_records']))
        for p in selected if (s := web['providers'].get(p))],
        '用于比较排名逆转与提取机制的方法：同一静态网站正文范围，F1 为有指标记录的逐例算术平均')
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
            '这些指标描述输出与该例参考的重合程度。参考外文字可能是导航，也可能是有用的目录或附录，'
            '仅凭低纯净度不能认定它们全是噪声，应先核对任务的正文范围。</p>')
    css = next((r for r in records if r['provider']=='cowagent-built-in' and r['case_id']=='live-024-cascading-style-sheets-level-2-r'), None)
    values['scope_case'] = (f'<p>CSS 2.1 标准是一个具体例子：文档目录属于本例要求保留的正文，'
        f'CowAgent 这次输出相对完整范围的 F1 为 {_percent(css["metrics"]["word_f1"])}。'
        '若参考剔除长目录，保留目录的方法会被大量扣分；这样的分差反映参考范围不一致，'
        '不能用来证明方法提取了大量噪声。'+_attempt(css, '原始输出与当前参考')+'</p>') if css else ''
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
            '离线机制实验：纯文本提取函数对合成 HTML 的输出，不计入方法排名')
        values['probe_figure'] += ('<details class="editorial-probe"><summary>核对合成输入与实际输出</summary>'
            f'<h3>输入 HTML</h3><pre>{escape(probe["fixture"])}</pre>'
            f'<h3>实际输出</h3><pre>{escape(probe["output"])}</pre><p>'
            + _link('guide/cowagent-text-probe.json', '实验代码、观察结果与源码 SHA-256') + '</p></details>')
    cow = web['providers'].get('cowagent-built-in')
    values['cow_result'] = (f'<p>CowAgent 在静态网站正文中请求成功 {cow["request_ok"]}/{cow["records"]}，'
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
    values['popular_scope'] = (
        f'<p>热门网站正文的 {challenge["case_count"]} 个任务有 {challenge["summary"]["records"]} 次调用，'
        f'其中 {challenge["summary"]["task_pass"]} 次满足各例任务规则。'
        f'单方法最好通过 {best}/{challenge["case_count"]}，'
        f'各方法成功集的离线并集为 {len(challenge["union_pass_ids"])}/{challenge["case_count"]}。'
        '这里的通过指任务声明的内容与结构契约，并不认证全部文字与某份实时快照一致。</p>')
    title_records = [r for r in records if cases[r['case_id']].get('track') == 'challenge' and 'title_quality' in r]
    text_cases = [c for c in cases.values() if c.get('track') == 'challenge' and c.get('acceptance_contract',{}).get('stable_text_anchors')]
    values['popular_scope'] += (f'<p>其中 {len(text_cases)}/{challenge["case_count"]} 个案例还登记了任务相关的固定文字：'
        '例如 IMDb 的榜单说明、JustWatch 的观看方式说明，以及商品或餐馆的字段标签。'
        '这些文字与列表条目共同决定任务是否通过，不能只拿到三个链接就算完成。'
        '每例命中多少必需文字组，由文字核对覆盖率回答；没有文字依据的案例不计入这项均值。'
        '少量核对句不是整页参考，因此不能拿它们计算整页纯净度和 F1，否则完整返回的其它正文反而会被当成杂文扣分。</p>')
    if title_records:
        measured = sum(r['title_quality']['scored_titles'] for r in title_records)
        returned = sum(r['title_quality']['returned_titles'] for r in title_records)
        values['popular_scope'] += (
            f'<p>这组另有 {measured}/{returned} 条返回标题能按详情链接对应到独立名称参考，'
            '据此计算标题完整度、纯净度和 F1。例如一部电影从第三名移到第十名，只要仍返回同一详情链接，'
            '就比较这部电影的名称，不比较名次，也不要求旧榜单里的电影继续出现。'
            '只返回电影名称可能取得很高的标题分，却仍不足以完成至少三条结果的任务。'
            '因此这三个分数须与任务通过率和参考覆盖量一起读，不能当作整页正文质量。'
            '新条目没有独立名称参考时不计文字分；同一条目的标题也可能改写，重合度只是相对已记录名称的诊断，不认证当前内容真假。</p>')
    missing = challenge['unpassed_ids']
    values['popular_failure'] = (
        '<p>没有任何方法满足任务规则的案例包括：'
        + '、'.join(_link('datasets/' + k + '.html', cases[k]['name']) for k in missing)
        + '。这些共同缺口使单纯增加后备方法的收益存疑；应先查实际输出缺少哪些字段、'
          '是否只有加载占位，或是否收到拦截页面。登录、地区和访问限制本身不使任务无效，'
          '它们可能就是目标业务需要处理的条件。</p>' if missing else
        '<p>成功集并集覆盖了全部案例，仍需验证线上能否识别缺项并触发后备；离线覆盖不能直接当作服务保证。</p>')
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
    online_figures, embedded_figures, data['methodology_figures'] = _methodology_figures(out)
    template = Template(TEMPLATE.read_text())
    body = template.substitute(values | online_figures)
    standalone_body = template.substitute(values | embedded_figures)
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
        f'<body class="standalone-article"><main class="wrap essay">{standalone_body}</main></body></html>')
    downloads = ('<aside id="evidence" class="editorial-downloads" aria-label="文章与证据下载">'
        '<h2>文章与证据下载</h2><p>'
        '<a href="article.html" download="web-fetch-article.html">下载独立 HTML</a> · '
        '<a href="interpretation-evidence.json" download>下载本文计算明细</a> · '
        '<a href="../results.jsonl" download>下载规范化执行记录</a>'
        + (' · <a href="cowagent-text-probe.json" download>下载离线提取实验</a>' if probe else '')
        + '</p></aside>')
    return f'<div class="wrap essay">{body}{downloads}</div>'
