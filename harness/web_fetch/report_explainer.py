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
from urllib.parse import quote

from .metrics import evaluate_content, task_passed, percentile
from .scoring import score_attempt
from .report_style import STYLE
from .safety import contained_path, safe_identifier


TITLE = '网页抓取成功之后，内容离可用还有多远'
HEADLESS_TITLE = '五类浏览器、六种方法取同一批网页：速度、正文与交付边界'
from .publication_target import HUB
ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(__file__).parent / 'templates/interpretation.html'
TRACK_NAMES = {'web': '网站正文', 'document': '文档文字', 'image': '图片',
               'video': '视频', 'challenge': '热门网站正文', 'dynamic': '动态网站正文'}
PROVIDER_NAMES = {'cowagent-built-in': 'CowAgent', 'moli-webfetch': 'Moli',
                  'cloudflare-markdown': 'Cloudflare Markdown', 'exa': 'Exa',
                  'langchain-webbase': 'LangChain WebBaseLoader', 'firecrawl': 'Firecrawl',
                  'crawl4ai': 'Crawl4AI', 'openclaw-built-in': 'OpenClaw',
                  'deepseek-harness-built-in': 'DeepSeek Harness', 'tavily': 'Tavily',
                  'lexmount-global': 'Lexmount 国际', 'lexmount-cn': 'Lexmount 中国区',
                  'moli': 'Moli 1.1.1', 'moli-1.1.3': 'Moli 1.1.3'}

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


def _window_text(window):
    return (' 至 '.join(datetime.fromisoformat(window[k]).strftime('%Y-%m-%d %H:%M:%S')
                       for k in ('first_start_utc', 'last_start_utc')) + ' UTC'
            if window['first_start_utc'] else '未提供调用时间')


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
                '<p>网站正文的分类标签与参考来源不足以逐页证明正文必须执行 JavaScript 后才出现。'
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
        mechanism += (f'<p>{escape(PROVIDER_NAMES.get(sample["provider"], sample["provider"]))} 在动态网站正文中通过 '
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
            '任务难度、源站状态和调用日期没有与网站正文任务构成受控对照；两类结果分别比较。</p>'
            + figure + mechanism
            + '<p>动态呈现与数据变化是两个维度。固定的名言也可以经异步接口或计时器显示；'
              '每日变动的榜单也可能直接写在 HTML 中。任务可以要求一定数量的完整条目、非空名称和指定类型的字段，'
              '不必锁定今天的名字或价格；稳定的标题、栏目和说明仍保留内容核对。'
              '页面内容变化，不妨碍用同一份内容与结构契约验收。</p>'
            + '<p>对这类页面，接入前应试验的是取数路径与完成条件：源 HTML 是否足够，'
              '如果不够，后续数据从哪里来，什么状态才允许交付。浏览器启动成功并没有替应用回答这些问题。</p></section>')


def _is_headless_comparison(records):
    return bool(records) and all(r.get('provider_type') in {'headless_html','headless_markdown'} for r in records)


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


def _render_headless(out, cases, records):
    data = headless_evidence(cases, records)
    audit_path=contained_path(out,'result-review.json')
    audit=json.loads(audit_path.read_text()) if audit_path.is_file() else None
    speed_audit_path=contained_path(out,'speed-cohort/result-review.json')
    speed_audit=json.loads(speed_audit_path.read_text()) if speed_audit_path.is_file() else None
    data['independent_result_audit']=audit
    provenance={}
    provenance_path=contained_path(out,'implementation-provenance.json')
    if provenance_path.is_file():
        provenance={x['binary_sha256']:x for x in json.loads(provenance_path.read_text()).get('implementations',[])}
    def num(value):
        return '—' if value is None else f'{value:,.0f}'
    overview = _table(['浏览器', *TRACK_NAMES.values()], [
        (escape(PROVIDER_NAMES.get(name,name)), *(f'{data["tracks"][t]["providers"][name]["passed"]}/{data["tracks"][t]["providers"][name]["attempts"]}'
            if name in data['tracks'][t]['providers'] else '—' for t in TRACK_NAMES))
        for name in data['providers']], '任务通过数 / 实际调用数。六类任务不合成总分；未执行不记为失败。')
    pairs = _table(['浏览器 vs Chromium','共同通过网页','本方法 P50 ms','Chromium P50 ms','仅本方法通过','仅 Chromium 通过'],[
        (escape(PROVIDER_NAMES.get(p['provider'],p['provider'])),str(len(p['both_pass_ids'])),num(p['provider_p50_ms']),num(p['chromium_p50_ms']),str(len(p['provider_only'])),str(len(p['chromium_only'])))
        for p in data['pairs']], '每行只比较双方共同通过的网页。同一行分母相同，不同行的配对样本可能不同；P50 包含客户端、启动、取页与形成 Markdown 的全部开销。')
    latency = _table(['浏览器','网页请求成功','网页任务通过','全部网页 P50 ms','全部网页 P95 ms'],[
        (escape(PROVIDER_NAMES.get(name,name)),str(row['request_ok']),str(row['passed']),num(row['p50_ms']),num(row['p95_ms']))
        for name,row in data['tracks']['web']['providers'].items()], '普通网页全部调用，包含失败及超时；分位数与完整榜单共用仓库的线性插值实现。')
    speed_path=contained_path(out,'speed-cohort/speed-comparison.json')
    if speed_path.is_file():
        speed=json.loads(speed_path.read_text())
        latency=_table(['方法','共同成功样本','P50 ms','P95 ms'],[
            (escape(PROVIDER_NAMES.get(row['provider'],row['provider'])),str(row['attempts']),num(row['p50_ms']),num(row['p95_ms']))
            for row in speed['rows']],f'同窗速度子集：预先声明 {len(speed["planned_cases"])} 题，六种方法共同通过 {len(speed["common_pass_cases"])} 题；所有方法使用完全相同的成功样本。')
        pairs='<p class="callout">速度仅使用 '+str(len(speed['common_pass_cases']))+'/'+str(len(speed['planned_cases']))+' 个六方法共同通过的同窗任务；主结果中的跨批次耗时只作逐次诊断，不参与此表排名。</p>'
    dynamic = _table(['任务',*data['providers']], [
        (_link('datasets/'+cid+'.html',cases[cid]['name']), *('通过' if cid in data['tracks']['dynamic']['providers'].get(n,{}).get('pass_ids',[]) else '未通过' for n in data['providers']))
        for cid in sorted(cases) if cases[cid].get('track')=='dynamic'], '动态网页逐例验收。延迟、异步取数与 iframe 分别检查；固定 DOMContentLoaded 不是内容完成判据。')
    stages=_table(['浏览器','实际路径','本次请求','原生 Markdown ms','取 HTML ms','转换 ms','服务端浏览器 ms','端到端 ms'],[
        (_link('attempts/'+r['attempt_id']+'.html',PROVIDER_NAMES.get(r['provider'],r['provider'])),escape(r.get('extractor') or '—'),'成功' if r['ok'] else '未成功',num(r['native_markdown_ms']),num(r['fetch_html_ms']),num(r['conversion_ms']),num(float(r['browser_ms_used'])) if r.get('browser_ms_used') else '—',num(r['elapsed_ms']))
        for r in data['encyclopedia']], 'Wikipedia 单页实测。原生路径不会虚构 HTML/转换拆分；Cloudflare 的 browser_ms_used 是服务端计费诊断，不等于客户端端到端耗时；超时缺少阶段数据时记为 —。')
    quality=_table(['浏览器','有全文指标的网页调用','纯净度','完整度','F1'],[
        (escape(PROVIDER_NAMES.get(n,n)),str(r['attempts']),*(_percent(r[k]) for k in ('word_precision','word_recall','word_f1')))
        for n,r in data['quality'].items()], '逐次词级指标的算术平均，包含已测失败调用；未通过任务不会把已有重合归零。完整度与纯净度均受已声明参考范围约束。')
    runtime=_table(['方法','版本','部署','完成条件'],[
        (escape(PROVIDER_NAMES.get(name,name)),escape(str(data['runtime'][name].get('browser_version') or provenance.get(data['runtime'][name].get('binary_sha256'),{}).get('version') or provenance.get(data['runtime'][name].get('binary_sha256'),{}).get('identity') or '版本未记录')),
         escape(str(data['runtime'][name].get('deployment') or '—')),
         escape(str(data['runtime'][name].get('wait_until') or '—')))
        for name in data['providers']], '版本未记录不是可忽略信息；二进制哈希仍保留在环境与复现页。')
    web_rows=data['tracks']['web']['providers']
    summary='；'.join(f'{PROVIDER_NAMES.get(n,n)} {r["passed"]}/{r["attempts"]}' for n,r in web_rows.items())
    dynamic_summary='；'.join(f'{PROVIDER_NAMES.get(n,n)} {r["passed"]}/{r["attempts"]}' for n,r in data['tracks']['dynamic']['providers'].items())
    if audit:
        reviewer=audit.get('reviewer',{})
        reviewed=audit.get('reviewed_attempts',0)+(speed_audit or {}).get('reviewed_attempts',0)
        expected=audit.get('expected_attempts',0)+(speed_audit or {}).get('expected_attempts',0)
        disagreements=audit.get('disagreements',0)+(speed_audit or {}).get('disagreements',0)
        main_subject='自动失败项' if audit.get('review_protocol',{}).get('scope')=='machine-failures' else '全部输出'
        speed_subject=('自动失败项' if (speed_audit or {}).get('review_protocol',{}).get('scope')=='machine-failures' else '全部输出')
        scopes=(f'主结果{main_subject} {audit.get("reviewed_attempts",0)}/{audit.get("expected_attempts",0)}' +
                (f'；同窗测速{speed_subject} {speed_audit.get("reviewed_attempts",0)}/{speed_audit.get("expected_attempts",0)}' if speed_audit else ''))
        revision=audit.get('review_protocol',{}).get('scoring_rule_revision_review') or {}
        revision_note=(f'评分规则修订涉及 {revision.get("reviewed_attempts",0)} 条输出，已另行盲审；'
                       if revision else '')
        review_summary=(f'{scopes}，由 '
            f'{reviewer.get("agent","独立代理")}（{reviewer.get("model","—")}，{reviewer.get("effort","—")}）逐条核验；'
            f'{revision_note}与自动判定不一致 {disagreements} 次，发布前未裁定数为 {audit.get("unresolved",0)+(speed_audit or {}).get("unresolved",0)}。')
    else:review_summary='本报告没有绑定独立结果核验记录。'
    values={'title':HEADLESS_TITLE,'attempts':str(data['attempts']),'cases':str(data['cases']),
        'providers':str(len(data['providers'])),'review_summary':escape(review_summary),'runtime':runtime,
        'time_window':escape(' 至 '.join(datetime.fromisoformat(t).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') for t in data['time_window'])),'overview':overview,
        'pairs':pairs,'latency':latency,'dynamic':dynamic,'stages':stages,'quality':quality,
        'web_summary':escape(summary),'dynamic_summary':escape(dynamic_summary)}
    template=Template((Path(__file__).parent/'templates/interpretation-headless.html').read_text())
    body=template.substitute(values)
    contained_path(out,'guide').mkdir(parents=True,exist_ok=True)
    contained_path(out,'guide/interpretation-evidence.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    contained_path(out,'guide/article.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+
        f'<title>{HEADLESS_TITLE}</title><style>{STYLE}</style></head><body class="standalone-article"><main class="wrap essay">{body}</main></body></html>')
    return '<div class="wrap essay">'+body+'<aside id="evidence"><h2>文章与证据下载</h2><p><a href="article.html" download>下载独立 HTML</a> · <a href="interpretation-evidence.json" download>计算明细</a> · <a href="../results.jsonl" download>逐次记录</a></p></aside></div>'


def render_explainer(out, cases, records, *, table=None, cell=None, link=None):
    """One authored body; only image URL serialization differs for the offline export."""
    out = Path(out)
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
        f'<p>初始源文档审计进一步缩小了这批样本的解释范围：网站正文 {audit["web_case_count"]} 例中，'
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
    wm = web['providers'].get('moli-webfetch')
    dm = tracks['dynamic']['providers'].get('moli-webfetch')
    values['lead'] = (f'同一配置的 Moli，在网站正文任务中通过 {wm["task_pass"]}/{wm["records"]}，'
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
    ) + f'</div><figcaption>网站正文调用的互斥状态分解；{values["time_window"]}。正常返回不等于源站 HTTP 200。</figcaption></figure>'
    selected = ['cloudflare-markdown', 'exa', 'cowagent-built-in']
    values['quality_figure'] = _table(['方法', '任务通过 / 调用', '平均 F1', 'F1 样本数'], [
        (_link('providers/' + p + '.html', PROVIDER_NAMES[p]), f'{s["task_pass"]}/{s["records"]}',
         f'{s["mean_f1"]:.4f}' if s['mean_f1'] is not None else '未测', str(s['f1_records']))
        for p in selected if (s := web['providers'].get(p))],
        '用于比较排名逆转与提取机制的方法：同一网站正文范围，F1 为有指标记录的逐例算术平均')
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
    values['cow_result'] = (f'<p>CowAgent 在网站正文中请求成功 {cow["request_ok"]}/{cow["records"]}，'
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
