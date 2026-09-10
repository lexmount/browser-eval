"""Five article examples: frozen references beside hash-verified actual output."""
import base64
import hashlib
import re
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit

from .metrics import task_passed
from .publication_target import HUB
from .safety import contained_path, safe_identifier


CASE_TABS = (
    ('document-01', 'PDF 正文'),
    ('static-simple', '网页正文'),
    ('challenge-01', '商品列表'),
    ('image-pexels-detail', '图片地址'),
    ('image-10', '原始文件'),
)
CASE_TITLES = {
    'static-simple': 'Example Domain 网页',
    'document-01': 'Attention Is All You Need 论文',
    'challenge-01': 'Anthropologie 蜡烛列表',
    'image-pexels-detail': 'Pexels 图片下载地址',
    'image-10': 'httpbin PNG 文件',
}


def _link(url, label):
    if urlsplit(url).scheme not in {'http', 'https'}:
        raise ValueError('Article case link requires HTTP(S)')
    return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'


def _attempt_link(record):
    route = 'attempts/' + safe_identifier(record['attempt_id']) + '.html'
    return _link(HUB + '#page=' + quote(route, safe=''), '完整输出与逐项判定')


def _read_output(out, record):
    raw = contained_path(out, record['artifact_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['metrics']['content_sha256']:
        raise ValueError('Article case output hash mismatch: ' + record['attempt_id'])
    return raw.decode('utf-8', errors='replace')


def _pre(text, marks=()):
    rendered = escape(text)
    terms = sorted({escape(term) for term in marks if term}, key=len, reverse=True)
    if terms:
        pattern = '|'.join(re.escape(term) for term in terms)
        rendered = re.sub(pattern, lambda match: '<mark>' + match.group() + '</mark>', rendered)
    return '<pre class="case-excerpt">' + rendered + '</pre>'


def _row(name, expected, actual, passed):
    return (f'<tr><td class="case-check-name">{escape(str(name))}</td><td>{escape(str(expected))}</td>'
            f'<td>{escape(str(actual))}</td><td>{"通过" if passed else "未通过"}</td></tr>')


def _text_example(case, record, output, *, pdf=False):
    metrics = record['metrics']
    reference = case['ground_truth_main_content']
    marks = [term for item in metrics['fact_results'] for term in item['matched']]
    if pdf:
        # Corresponding abstract fragments, without inventing or translating text.
        marker = 'The dominant sequence transduction models'
        if marker not in reference or marker not in output:
            raise ValueError('Reviewed PDF abstract excerpt is missing')
        def abstract_excerpt(text):
            start = text.index(marker)
            endings = list(re.finditer(r'\.(?=\s|$)', text[start:]))
            if len(endings) < 3:
                raise ValueError('Reviewed PDF abstract requires three complete sentences')
            return text[start:start + endings[2].end()]
        reference_excerpt = abstract_excerpt(reference)
        output_excerpt = abstract_excerpt(output)
        left = '<p>独立参考正文 · 摘要节选</p>' + _pre(reference_excerpt, marks)
        right = '<p>Moli 1.1.1 · 同一摘要位置节选</p>' + _pre(output_excerpt, marks)
        # The abstract demonstrates Transformer; expose the other two checks
        # with corresponding source lines, rather than only asserting a match.
        def remaining_fact_lines(text, excerpt):
            lines = []
            for term in marks:
                if term in excerpt:
                    continue
                line = next((line for line in text.splitlines() if term in line), None)
                if line is None:
                    raise ValueError('Reviewed PDF fact excerpt is missing: ' + term)
                if line not in lines:
                    lines.append(line)
            return '\n'.join(lines)
        left += '<p>标题与注意力机制 · 原文行节选</p>' + _pre(remaining_fact_lines(reference, reference_excerpt), marks)
        right += '<p>标题与注意力机制 · 原文行节选</p>' + _pre(remaining_fact_lines(output, output_excerpt), marks)
        meta = record['provider_metadata']
        right += ('<p class="case-route">'
                  f'Moli 取得 PDF <strong>{meta["raw_fetch_ms"]/1000:.2f} 秒</strong>'
                  f' → pypdf 提取正文 <strong>{meta["document_extract_ms"]/1000:.2f} 秒</strong>'
                  f' → <strong>{metrics["content_chars"]:,} 字符</strong></p>'
                  f'<p>从输入到交付共 {record["elapsed_ms"]/1000:.2f} 秒，包含流程开销。本题预先声明为 PDF 文档，未测试未知链接的自动类型识别。</p>')
        note = '两侧均为节选；评分使用完整正文。参考正文按词数从头到尾近似等分为五段，再检查每段连续六字符片段在输出中找回的比例。Moli 取得 PDF 后，由本地 pypdf 在同一预算内提取文字。'
    else:
        left = '<p>独立参考正文 · 全文</p>' + _pre(reference, marks)
        right = '<p>Moli 1.1.1 · 原始 Markdown 全文</p>' + _pre(output, marks)
        note = '参考以正文保存，实际输出保留 Markdown 标记；按文字内容验收。'
    checks = []
    for item in metrics['fact_results']:
        checks.append(_row('必要文字', ' / '.join(item['alternatives']),
                           ' / '.join(item['matched']) or '未命中', item['pass']))
    checks.append(_row('输出长度', f'至少 {case["min_content_chars"]:,} 字符',
                       f'{metrics["content_chars"]:,} 字符', metrics['length_pass']))
    regions = metrics['body_region_results']
    required = metrics['body_required_target_regions']
    threshold = case['body_requirements']['minimum_coverage']
    floor = case['body_requirements']['minimum_floor']
    checks.append(_row('正文分区覆盖',
                       f'至少 {required}/{len(regions)} 区达到 {threshold:.0%}；每区不低于 {floor:.0%}',
                       ' / '.join(f'{r["coverage"]:.0%}' for r in regions),
                       metrics['body_coverage_pass']))
    if 'forbidden_hits' in metrics:
        forbidden = metrics['forbidden_hits']
        checks.append(_row('排除错误页', '排除：' + ' / '.join(case.get('forbidden_any', [])),
                           ' / '.join(forbidden) if forbidden else '未命中排除项',
                           not forbidden))
    if 'text_encoding_corruption' in metrics:
        checks.append(_row('文字解码', '替换字符数 ≥20 且占比 ≥2% 时拒绝',
                           f'{metrics.get("replacement_character_count", 0)} 个替换字符',
                           not metrics['text_encoding_corruption']))
    return left, right, checks, note


def _list_example(case, record, output):
    contract, metrics = case['acceptance_contract'], record['metrics']
    anchors = [' / '.join(a['alternatives']) for a in contract['stable_text_anchors']]
    left = '<p>独立观察的稳定文字</p>' + _pre('\n'.join(anchors), anchors)
    left += ('<div class="case-contract"><p>记录要求</p>'
             f'<p>至少 {contract["minimum_records"]} 条不同记录</p>'
             '<p>每条：非空名称 + 本站详情链接</p>'
             '<details><summary>链接识别细节</summary>'
             '<p>名称至少 2 个字符且含字母；以 http(s) 开头的名称，以及“详情”“Read more”等登记的入口标签会被剔除。</p>'
             '<p>按域名与路径去重；查询参数变化不增加记录数。</p>'
             '<p>域名：<code>' + escape(' / '.join(contract['hosts'])) + '</code></p>'
             '<p>详情路径规则：<code>' + escape(contract['entry_path']) + '</code></p></details></div>')
    excerpt = [line for line in output.splitlines()
               if line.startswith('[## ') and '](/shop/' in line][:3]
    if len(excerpt) != 3:
        raise ValueError('Reviewed product-list excerpts are missing')
    anchor_lines = []
    for anchor in contract['stable_text_anchors']:
        candidates = [line for line in output.splitlines()
                      if any(word in line for word in anchor['alternatives'])]
        matched = next((line for line in candidates
                        if line.strip().lstrip('# ').strip() in anchor['alternatives']),
                       candidates[0] if candidates else None)
        if matched is None:
            raise ValueError('Reviewed stable-text excerpt is missing: ' + anchor['id'])
        anchor_lines.append(matched)
    right = '<p>Moli 1.1.1 · 四组稳定文字对应的原文行</p>' + _pre('\n'.join(anchor_lines), anchors)
    right += '<p>原始 Markdown 中三条商品记录节选</p>' + _pre('\n\n'.join(excerpt))
    checks = [_row('稳定文字', ' / '.join(anchors),
                   f'{metrics["stable_anchor_hits"]}/{metrics["stable_anchor_total"]} 组命中',
                   metrics['stable_content_pass']),
              _row('完整记录', f'至少 {contract["minimum_records"]} 条，字段与链接规则合格',
                   f'{metrics["stable_complete_record_count"]} 条', metrics['stable_structure_pass']),
              _row('唯一记录', f'去重后至少 {contract["minimum_records"]} 条',
                   f'{metrics["stable_unique_record_count"]} 条', metrics['stable_structure_pass'])]
    forbidden = metrics['forbidden_hits']
    blocked = metrics['stable_blocked_page']
    checks.append(_row('排除错误页',
                       '排除：' + ' / '.join(case.get('forbidden_any', [])) + '；不得返回安全验证或拒绝访问页',
                       ' / '.join(forbidden) if forbidden else ('命中拦截页特征' if blocked else '未命中排除项或拦截页特征'),
                       not forbidden and not blocked))
    checks.append(_row('文字解码', '替换字符数 ≥20 且占比 ≥2% 时拒绝',
                       f'{metrics["replacement_character_count"]} 个替换字符',
                       not metrics['text_encoding_corruption']))
    note = '商品名称、价格和排序不作为固定答案；本题不核验名称的事实正确性或信息时效。右侧节选不是完整列表。'
    note += '后续三次固定诊断调用中，Moli 一次取得 72 条记录，两次返回空内容；单次通过尚不能说明长期稳定性。这三次用于观察波动，不替换主榜的那次结果。'
    return left, right, checks, note


def _resource_example(case, record, output):
    metrics = record['metrics']
    expected = case['expected_resources'][0]['urls'][0]
    lines = [line for line in output.splitlines() if expected in line]
    if not lines:
        raise ValueError('Reviewed target image URL is absent from output')
    left = '<p>独立确认的目标资源</p>' + _pre(expected)
    left += _pre(case['reference_answer'])
    right = '<p>Moli 1.1.1 · 含目标下载链接的原始输出节选</p>' + _pre(lines[0])
    checks = [_row('目标图片地址', '与上方登记的完整 URL 匹配',
                   '规范化后相同' if item['matched_urls'] else '未命中', item['pass'])
              for item in metrics['resource_results']]
    note = '本题的资源匹配规则忽略 URL 查询参数，因此实际下载链接与登记目标匹配；这不是对任意下载地址的通用等价判断。本题不要求下载图片，也不检查推荐图片的数量。'
    return left, right, checks, note


def _file_example(out, case, record, *, media_records=(), cases=None):
    expected_hash = case['source_verification']['file_sha256']
    files = record['provider_metadata']['file_evaluation']['files']
    if len(files) != 1:
        raise ValueError('Reviewed PNG example requires one delivered file')
    delivered = files[0]
    raw = contained_path(out, delivered['artifact_path']).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != delivered['sha256'] or len(raw) != delivered['bytes']:
        raise ValueError('Article case delivered-file integrity mismatch')
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Article case PNG signature mismatch')
    left = '<p>独立目标文件</p>' + _pre(case['reference_answer'])
    left += '<p>目标 SHA-256</p>' + _pre(expected_hash)
    uri = 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
    right = '<p>Moli 1.1.1 · 实际交付的 PNG</p>'
    right += f'<img class="case-file-preview" src="{uri}" alt="PNG file returned by Moli in the recorded benchmark" />'
    right += '<p>实际 SHA-256</p>' + _pre(digest)
    checks = [_row('文件签名', 'PNG 文件头：89504e470d0a1a0a', raw[:8].hex(),
                   raw[:8] == b'\x89PNG\r\n\x1a\n'),
              _row('文件长度', '至少 32 字节；独立目标为 8,090 字节',
                   f'{len(raw):,} 字节', len(raw) >= 32),
              _row('文件一致性', '匹配独立目标 SHA-256',
                   '一致' if digest == expected_hash else '不一致', digest == expected_hash)]
    note = '文件长度的通用门槛是 32 字节。SHA-256 是按文件字节计算的指纹，用来核对是否同一份文件；不评价图片识别或媒体语义。'
    media_facts = []
    for media in media_records:
        target = cases[media['case_id']]
        delivered_files = media['provider_metadata']['file_evaluation']['files']
        if len(delivered_files) != 1 or not task_passed(media['ok'], media['metrics'], 'file'):
            raise ValueError('Reviewed media example requires one accepted file')
        file = delivered_files[0]
        data = contained_path(out, file['artifact_path']).read_bytes()
        file_hash = hashlib.sha256(data).hexdigest()
        if (file_hash != file['sha256'] or len(data) != file['bytes']
                or file_hash != target['source_verification']['file_sha256']):
            raise ValueError('Article media-file integrity mismatch')
        name = {'video/webm': 'WebM', 'video/mp4': 'MP4'}[file['mime']]
        media_facts.append(f'{name} {len(data):,} 字节，用时 {media["elapsed_ms"]/1000:.2f} 秒')
    if media_facts:
        note += '同组另外两道原文件题也通过：' + '；'.join(media_facts) + '，均与独立目标文件哈希一致。'
    return left, right, checks, note


def render_article_cases(out, cases, records):
    """Return five CSS-radio panels; no calls, rescoring, or artifact mutation."""
    out = Path(out)
    indexed = {}
    for record in records:
        key = (record['provider'], record['case_id'])
        if key in indexed:
            raise ValueError('Article examples require one authoritative record per provider and case')
        indexed[key] = record
    panels = []
    for index, (case_id, tab) in enumerate(CASE_TABS, 1):
        case = cases[case_id]
        record = indexed[('moli', case_id)]
        output = _read_output(out, record)
        if case_id == 'challenge-01':
            left, right, checks, note = _list_example(case, record, output)
        elif case_id == 'image-pexels-detail':
            left, right, checks, note = _resource_example(case, record, output)
        elif case_id == 'image-10':
            left, right, checks, note = _file_example(
                out, case, record, cases=cases,
                media_records=[indexed[('moli', cid)] for cid in ('video-05', 'video-08')])
        else:
            left, right, checks, note = _text_example(case, record, output, pdf=case_id == 'document-01')
        chromium = indexed.get(('chromium', case_id))
        comparison = ''
        if chromium and not task_passed(chromium['ok'], chromium['metrics'], case.get('task_kind')):
            other = _read_output(out, chromium)
            comparison = '<div class="case-counterexample"><p>Chromium · 本轮对照输出</p>'
            comparison += _pre(other[:400]) if other else '<p>空输出 · 0 字符</p>'
            if len(other) > 400:
                comparison += '<p>以上为前 400 字符节选。</p>'
            other_metrics = chromium['metrics']
            if case_id == 'document-01':
                reason = (f'正文 {other_metrics["content_chars"]} 字符；必要文字命中 '
                          f'{other_metrics["fact_hits"]}/{other_metrics["fact_total"]}。')
            elif case_id == 'challenge-01':
                reason = (f'完整记录 {other_metrics["stable_complete_record_count"]} 条；稳定文字命中 '
                          f'{other_metrics["stable_anchor_hits"]}/{other_metrics["stable_anchor_total"]}。')
            elif case_id == 'image-pexels-detail':
                reason = f'目标图片地址命中 {other_metrics["resource_hits"]}/{other_metrics["resource_total"]}。'
            elif case_id == 'image-10':
                count = len(chromium['provider_metadata']['file_evaluation']['files'])
                reason = f'实际交付 {count} 个原始文件；这里只返回了 Markdown 图片链接。'
            else:
                reason = '未满足本题全部验收要求。'
            comparison += '<p>' + escape(reason) + '</p><p>' + _attempt_link(chromium) + '</p>'
            comparison += '</div>'
        passed = task_passed(record['ok'], record['metrics'], case.get('task_kind'))
        status = '通过' if passed else '未通过'
        panels.append(
            f'<section class="case-panel" id="article-panel-{index}" aria-labelledby="article-label-{index}">'
            f'<h3>{escape(CASE_TITLES[case_id])}</h3><p class="case-input">输入：{_link(case["url"], case["url"])}</p>'
            '<div class="case-compare"><div class="case-reference"><h4>标准答案与要求</h4>' + left + '</div>'
            '<div class="case-actual"><h4>本次真实输出</h4>' + right + comparison + '</div></div>'
            '<p class="case-scroll-hint">左右滑动查看完整判定。</p>'
            '<div class="case-check-wrap"><table class="case-checks"><thead><tr><th>序号</th><th>检查项</th><th>验收要求</th><th>Moli 实际结果</th><th>判定</th></tr></thead>'
            '<tbody>' + ''.join(row.replace('<tr>', f'<tr><td>{i}</td>', 1) for i, row in enumerate(checks, 1)) + '</tbody></table></div>'
            f'<p class="case-conclusion"><strong>本题{status}。</strong> {escape(note)}</p>'
            f'<p class="case-evidence">{_attempt_link(record)}</p></section>')
    radios = ''.join(
        f'<input class="case-choice" type="radio" name="article-case" id="article-case-{i}"'
        f' aria-labelledby="article-label-{i}"'
        + (' checked' if i == 1 else '') + '>' for i in range(1, 6))
    labels = ''.join(f'<label id="article-label-{i}" for="article-case-{i}">{escape(tab)}</label>'
                     for i, (_, tab) in enumerate(CASE_TABS, 1))
    return ('<div class="case-workbench">' + radios + '<div class="case-tab-nav">' + labels +
            '</div><div class="case-panels">' + ''.join(panels) + '</div></div>')
