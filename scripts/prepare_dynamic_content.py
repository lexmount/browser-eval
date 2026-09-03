"""Freeze independently inspected initial HTML and rendered content for a supplement.

Browser captures are reviewed source observations, never provider outputs. Run this
before candidates; --output must not exist. Initial HTTP captures are bounded.
"""
import argparse
import hashlib
import json
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
    output.mkdir(parents=True, exist_ok=False)
    dataset = {'id': 'web-fetch-dynamic-content', 'name': '动态内容独立补测', 'tool': 'web_fetch', 'cases': []}
    for capture in captures:
        key = capture['id']
        name, url, category, count = SPECS[key]
        if len(capture['entries']) != count or capture['expected_count'] != count:
            raise ValueError(f'Incomplete rendered capture: {key}')
        reference = clean_reference_text(capture['rendered_text'])
        check = next(x for x in completion_checks if x['id'] == key)
        if check['entries'] != capture['entries'] or not check['satisfied']:
            raise ValueError(f'Bounded completion does not reproduce frozen reference: {key}')
        facts = entry_facts(key, capture['entries'])
        if any(not any(normalize_for_match(v) in normalize_for_match(reference) for v in f['alternatives']) for f in facts):
            raise ValueError(f'Reference omits facts: {key}')
        response = requests.get(url, timeout=(12, 35), headers={'User-Agent': 'Mozilla/5.0 (compatible; WebFetchEvaluation/1.0)'})
        response.raise_for_status()
        dest = output/'sources'/key
        dest.mkdir(parents=True)
        (dest/'initial.html.txt').write_bytes(response.content)
        (dest/'rendered.txt').write_text(capture['rendered_text'])
        (dest/'reference.txt').write_text(reference)
        scope = SCOPES.get(key, f'Initial page automatic loading completed; exactly {count} visible entries, no scrolling/clicking/pagination')
        evidence = {'case_id': key, 'url': url, 'source': 'Independent HTTP response + separately observed browser DOM; no candidate output',
                    'checked_at': capture['checked_at'], 'initial_checked_at': datetime.now(timezone.utc).isoformat(),
                    'selector': capture['selector'], 'expected_entries': count, 'observed_entries': len(capture['entries']),
                    'completion': scope,
                    'initial_status': response.status_code, 'initial_sha256': hashlib.sha256(response.content).hexdigest(),
                    'rendered_sha256': hashlib.sha256(capture['rendered_text'].encode()).hexdigest(),
                    'reference_sha256': hashlib.sha256(reference.encode()).hexdigest()}
        (dest/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
        dataset['cases'].append({'id':key, 'name':name, 'url':url, 'category':category, 'track':'dynamic', 'task_kind':'body',
            'expected_any':[], 'required_facts':facts, 'min_content_chars':int(len(reference)*.7), 'fact_coverage_threshold':1.0,
            'forbidden_any':['Access denied','CAPTCHA'], 'ground_truth_main_content':reference,
            'task_prompt':f'{scope}。允许渲染、解析内嵌数据、加载子文档或获取公开接口，不限制实现路径。',
            'description':'公开练习站及应用示例；核对声明范围内固定示例数据，不代表生产网站分布。',
            'source_benchmark':{'capture_scope':evidence['completion'], 'expected_entries':count,
                'render_evidence':{'scope':evidence['completion'], 'rendered_truncated':False,
                    'completion':{'condition':f'{count} target records visible without interaction',
                        'timeout_ms':check['timeout_ms'], 'elapsed_ms':check['elapsed_ms'],
                        'satisfied':check['satisfied'], 'checked_at':check['checked_at']}}},
            'source_verification':{'state':'valid','checked_at':capture['checked_at'],'method':'独立 HTTP 与浏览器可见正文对照','reference_sha256':evidence['reference_sha256']},
            'ground_truth_evidence':[{'source':url,'supports':f'独立观察的全部 {count} 条记录及其文字字段'}]})
        audit = validate_render_reference(dataset['cases'][-1], response.content, capture['rendered_text'])
        if not audit['valid']:
            raise ValueError(audit['errors'])
        (dest/'render-audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2)+'\n')
    (output/'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2)+'\n')
    print(f'Frozen {len(dataset["cases"])} independent dynamic-content cases: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures', type=Path, default=ROOT/'research/dynamic-browser-captures.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--completion-checks', type=Path, default=ROOT/'research/dynamic-completion-checks.json')
    args = parser.parse_args()
    prepare(json.loads(args.captures.read_text()), args.output, json.loads(args.completion_checks.read_text()))
