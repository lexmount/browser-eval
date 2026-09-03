"""Retain independent sample evidence and annotate mixed stable acceptance rules.

Browser captures are reviewed source observations, never provider outputs. Run this
before candidates; --output must not exist. Initial HTTP captures are bounded.
Dynamic loading does not imply changing content: the registry preserves fixed
demo text while treating commercial/list values structurally where declared.
Saved text/facts are captured_sample_diagnostic evidence, not current live truth.
"""
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness.web_fetch.reference_quality import clean_reference_text
from harness.web_fetch.metrics import normalize_for_match
from harness.web_fetch.render_evidence import validate_render_reference
from harness.web_fetch.dynamic_reference import entry_facts
from harness.web_fetch.reference_lifecycle import timestamp
from harness.web_fetch.stable_annotations import annotate_case, SNAPSHOT_METRIC_SCOPE
from harness.web_fetch.stable_tasks import evaluate_stable_task

SPECS = {
    'dynamic-quotes-api': ('Quotes · 异步引文', 'https://quotes.toscrape.com/scroll', 'async_api', 10),
    'dynamic-products-api': ('Scraping Course · 异步商品', 'https://www.scrapingcourse.com/javascript-rendering', 'async_api', 12),
    'dynamic-catalog': ('Web Scraper · 客户端商品目录', 'https://webscraper.io/test-sites/e-commerce/ajax/computers/laptops', 'client_catalog', 6),
    'dynamic-delayed': ('Quotes · 延迟呈现', 'https://quotes.toscrape.com/js-delayed/?delay=3000', 'delayed_render', 10),
    'dynamic-reviews-graphql': ('ScrapFly · GraphQL 评论', 'https://web-scraping.dev/reviews', 'graphql', 20),
    'dynamic-react-cart': ('React · 商品与分期信息', 'https://react-shopping-cart-67954.firebaseapp.com/', 'react_app', 16),
    'dynamic-angular-phones': ('Angular · 手机目录', 'https://angular.github.io/angular-phonecat/step-12/app/', 'client_router', 20),
    'dynamic-swagger': ('Swagger · 接口目录', 'https://petstore.swagger.io/', 'schema_render', 20),
    'dynamic-product-reviews': ('ScrapFly · 内嵌评论数据', 'https://web-scraping.dev/product/1', 'embedded_state', 5),
    'dynamic-iframe': ('Scrape This Site · 内嵌分类目录', 'https://www.scrapethissite.com/pages/frames/', 'embedded_frame', 14),
}

SCOPES = {
    'dynamic-reviews-graphql': '首次自动加载的 20 条评论：日期与评论全文；不含星级图形，不点击 Load More',
    'dynamic-react-cart': '默认未筛选目录的全部 16 个商品：名称、售价、可见分期与免邮文字；不含按钮、图片及广告',
    'dynamic-angular-phones': '默认手机列表全部 20 条名称与描述；不搜索、不排序、不进入详情',
    'dynamic-swagger': '默认展开分组中全部 20 条操作摘要：HTTP 方法、路径和说明；不展开参数、不执行 API、不含 Cookie 横幅',
    'dynamic-product-reviews': '商品页首次自动呈现的 5 条评论：日期和评论全文；不含星级图形、商品推荐，不点击 Load More',
    'dynamic-iframe': '父页自动加载的 iframe 中全部 14 个龟类科名称；不进入详情，不要求图片或按钮文字',
}


def prepare(captures, output, completion_checks):
    # This path prepares stable contracts, not exact-snapshot run certificates.
    # Old timestamps remain old. Validate them, never rewrite them as fresh.
    now = datetime.now(timezone.utc)
    if (not isinstance(captures, list) or not captures
            or any(not isinstance(c, dict) or c.get('id') not in SPECS for c in captures)
            or len({c['id'] for c in captures}) != len(captures)):
        raise ValueError('Require nonempty, unique independent captures')
    if (not isinstance(completion_checks, list)
            or any(not isinstance(c, dict) or c.get('id') not in SPECS for c in completion_checks)
            or len({c['id'] for c in completion_checks}) != len(completion_checks)
            or {c['id'] for c in completion_checks} != {c['id'] for c in captures}):
        raise ValueError('Require one completion check per captured case; no unknown observations')
    prepared = []
    for capture in captures:
        key = capture['id']
        name, url, category, count = SPECS[key]
        check = next(x for x in completion_checks if x['id'] == key)
        for observation in (capture, check):
            if timestamp(observation.get('checked_at')) > now:
                raise ValueError('Capture cannot be future-dated')
        elapsed, timeout = check.get('elapsed_ms'), check.get('timeout_ms')
        if (check.get('satisfied') is not True or type(elapsed) not in (int, float)
                or type(timeout) not in (int, float) or not math.isfinite(elapsed)
                or not math.isfinite(timeout) or not 0 <= elapsed <= timeout or timeout <= 0):
            raise ValueError(f'Incomplete or unbounded completion: {key}')
        if (not isinstance(capture.get('entries'), list) or len(capture['entries']) != count
                or any(not isinstance(entry, str) or not entry.strip() for entry in capture['entries'])
                or type(capture.get('expected_count')) is not int or capture['expected_count'] != count):
            raise ValueError(f'Incomplete rendered capture: {key}')
        if not isinstance(capture.get('selector'), str) or not capture['selector'].strip():
            raise ValueError(f'Missing captured scope selector: {key}')
        if not isinstance(capture.get('rendered_text'), str) or not capture['rendered_text'].strip():
            raise ValueError(f'Missing rendered source text: {key}')
        reference = clean_reference_text(capture['rendered_text'])
        if (check.get('entries') != capture['entries']
                or normalize_for_match('\n\n'.join(capture['entries'])) != normalize_for_match(reference)):
            raise ValueError(f'Bounded completion does not reproduce frozen reference: {key}')
        for hash_key, text in (('rendered_sha256', capture['rendered_text']), ('reference_sha256', reference)):
            if hash_key in capture and capture[hash_key] != hashlib.sha256(text.encode()).hexdigest():
                raise ValueError(f'Captured source hash mismatch: {key}/{hash_key}')
        facts = entry_facts(key, capture['entries'])
        if any(not any(normalize_for_match(v) in normalize_for_match(reference) for v in f['alternatives']) for f in facts):
            raise ValueError(f'Reference omits facts: {key}')
        scope = SCOPES.get(key, f'Initial page automatic loading completed; exactly {count} visible entries, no scrolling/clicking/pagination')
        case = annotate_case({'id':key, 'name':name, 'url':url, 'category':category, 'track':'dynamic', 'task_kind':'body',
            'expected_any':[], 'required_facts':facts, 'min_content_chars':int(len(reference)*.7), 'fact_coverage_threshold':1.0,
            'forbidden_any':['Access denied','CAPTCHA'], 'ground_truth_main_content':reference,
            'task_prompt':f'{scope}。允许渲染、解析内嵌数据、加载子文档或获取公开接口，不限制实现路径。',
            'description':'公开练习站及应用示例；动态加载不等于内容易变，按逐例契约核对结构与稳定内容。历史全文仅用于捕获样本诊断。',
            'source_benchmark':{'capture_scope':scope, 'expected_entries':count,
                'render_evidence':{'scope':scope, 'rendered_truncated':False,
                    'completion':{'condition':f'{count} target records visible without interaction',
                        'timeout_ms':check['timeout_ms'], 'elapsed_ms':check['elapsed_ms'],
                        'satisfied':check['satisfied'], 'checked_at':check['checked_at']}}},
            'source_verification':{'state':'valid','checked_at':capture['checked_at'],'method':'独立 HTTP 与浏览器可见正文对照',
                                   'reference_sha256':hashlib.sha256(reference.encode()).hexdigest()},
            'ground_truth_evidence':[{'source':url,'supports':f'独立观察的全部 {count} 条记录及其文字字段'}]})
        validation = evaluate_stable_task(reference, case['acceptance_contract'], url)
        if not validation['stable_task_pass']:
            raise ValueError(f'Independent sample violates reviewed acceptance contract: {key}: {validation["stable_task_errors"]}')
        prepared.append((capture, case, scope))
    # Validate every supplied capture before any output or HTTP call.
    output.mkdir(parents=True, exist_ok=False)
    dataset = {'id': 'web-fetch-dynamic-content', 'name': '动态网站正文', 'tool': 'web_fetch', 'cases': []}
    for capture, case, scope in prepared:
        key, url = case['id'], case['url']
        response = requests.get(url, timeout=(12, 35), headers={'User-Agent': 'Mozilla/5.0 (compatible; WebFetchEvaluation/1.0)'})
        response.raise_for_status()
        audit = validate_render_reference(case, response.content, capture['rendered_text'])
        if not audit['valid']:
            raise ValueError(audit['errors'])
        reference = case['ground_truth_main_content']
        count = case['source_benchmark']['expected_entries']
        evidence = {'case_id': key, 'url': url, 'source': 'Independent HTTP response + separately observed browser DOM; no candidate output',
                    'checked_at': capture['checked_at'], 'initial_checked_at': datetime.now(timezone.utc).isoformat(),
                    'selector': capture['selector'], 'expected_entries': count, 'observed_entries': len(capture['entries']),
                    'completion': scope, 'metric_scope': SNAPSHOT_METRIC_SCOPE,
                    'contemporaneous_pair_claimed': False,
                    'initial_status': response.status_code, 'initial_sha256': hashlib.sha256(response.content).hexdigest(),
                    'rendered_sha256': hashlib.sha256(capture['rendered_text'].encode()).hexdigest(),
                    'reference_sha256': hashlib.sha256(reference.encode()).hexdigest()}
        dest = output/'sources'/key
        dest.mkdir(parents=True)
        (dest/'initial.html.txt').write_bytes(response.content)
        (dest/'rendered.txt').write_text(capture['rendered_text'], encoding='utf-8')
        (dest/'reference.txt').write_text(reference, encoding='utf-8')
        (dest/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (dest/'render-audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n')
        dataset['cases'].append(case)
    (output/'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2)+'\n')
    print(f'Frozen {len(dataset["cases"])} independent dynamic-content cases: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures', type=Path, default=ROOT/'research/dynamic-browser-captures.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--completion-checks', type=Path, default=ROOT/'research/dynamic-completion-checks.json')
    args = parser.parse_args()
    prepare(json.loads(args.captures.read_text()), args.output, json.loads(args.completion_checks.read_text()))
