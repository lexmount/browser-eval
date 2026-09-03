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
        evidence = {'case_id': key, 'url': url, 'source': 'Independent HTTP response + separately observed browser DOM; no candidate output',
                    'checked_at': capture['checked_at'], 'initial_checked_at': datetime.now(timezone.utc).isoformat(),
                    'selector': capture['selector'], 'expected_entries': count, 'observed_entries': len(capture['entries']),
                    'completion': f'Initial page automatic loading completed; exactly {count} visible entries, no scrolling/clicking/pagination',
                    'initial_status': response.status_code, 'initial_sha256': hashlib.sha256(response.content).hexdigest(),
                    'rendered_sha256': hashlib.sha256(capture['rendered_text'].encode()).hexdigest(),
                    'reference_sha256': hashlib.sha256(reference.encode()).hexdigest()}
        (dest/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
        dataset['cases'].append({'id':key, 'name':name, 'url':url, 'category':category, 'track':'dynamic', 'task_kind':'body',
            'expected_any':[], 'required_facts':facts, 'min_content_chars':int(len(reference)*.7), 'fact_coverage_threshold':1.0,
            'forbidden_any':['Access denied','CAPTCHA'], 'ground_truth_main_content':reference,
            'task_prompt':f'读取初次自动加载完成后的 {count} 条记录，保留全部文字字段；不要求滚动、翻页或点击。允许渲染、解析内嵌数据或获取公开接口，不限制实现路径。',
            'description':'源站为公开抓取练习站；核对固定练习数据，不代表生产网站分布。',
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
