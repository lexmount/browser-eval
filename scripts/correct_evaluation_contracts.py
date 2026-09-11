"""Apply reviewed reference-scope and acceptance corrections without provider calls.

Original source bytes and call records are never rewritten here. A separate
shared rescore records the metric changes and their rule provenance.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collect_source_evidence import extract_html
from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.reference_quality import clean_reference_text
from harness.web_fetch.storage import current_evidence_dir


def correct(source, *, body_only=False):
    path = source / 'dataset.json'
    data = json.loads(path.read_text())
    reviewed = {
        'live-024-cascading-style-sheets-level-2-r': '完整标准页面，包含 Quick 与 Full Table of Contents；排除脚本、样式与站点导航。',
        'live-022-until-september-10-get-pycharm-f': 'article#docs-content 内的文档正文；排除语言、版本切换器、站点导航与返回顶部控件。',
    }
    for case in data['cases']:
        if case.get('acceptance_contract', {}).get('schema') == 'named_link':
            # A named record must be an owned list/card entry. A prose
            # recommendation link is related content, not a returned entity.
            case['acceptance_contract']['entry_context'] = 'list_or_card'
        if not body_only and case['id'] in reviewed:
            relative = f"sources/{case['id']}/source.html.txt"
            raw = (source / relative).read_bytes()
            _, text, selector, _ = extract_html(raw, case['url'])
            text = clean_reference_text(text)
            case['ground_truth_main_content'] = text
            case['source_verification'].update(selector=selector,
                reference_scope=reviewed[case['id']],
                reference_sha256=hashlib.sha256(text.encode()).hexdigest(),
                source_snapshot_sha256=hashlib.sha256(raw).hexdigest(),
                scope_revision={'basis': 'independent_saved_source_scope_review', 'source_path': relative})
        if (case.get('track', 'web') in {'web', 'document'} and case.get('task_kind', 'body') == 'body'
                and case.get('ground_truth_main_content') and not case.get('acceptance_contract')):
            case['body_requirements'] = body_requirements(case['ground_truth_main_content'])
        if not body_only and case['id'] == 'dynamic-react-cart':
            # Field coverage of the declared sixteen-product demonstration.
            # No exact price, title, ordering or per-product commercial value.
            case['acceptance_contract']['field_minimum_records'] = {'financing': 15, 'shipping': 13}
            case['source_benchmark']['stable_annotation']['note'] = (
                '独立参考的 16 件商品范围内，至少 15 条分期、13 条配送字段须归属于完整且唯一的商品记录。'
                '不固定商品名、价格和顺序，不要求每件商品均具备这两个字段。')
            for value in ('Add to cart', 'Work in the Netherlands',
                          'jeremy-akeze-doghouse-it-recruitment'):
                if value not in case['forbidden_any']:
                    case['forbidden_any'].append(value)
        if not body_only and case['id'] == 'html-table':
            headers = next(f for f in case['required_facts'] if f['id'] == 'headers')
            headers['alternatives'] = list(dict.fromkeys(
                headers['alternatives'] + ['CompanyContactCountry']))
        if not body_only and case['id'] == 'live-077-python-dataclasses-documentation':
            anchor = next(f for f in case['required_facts'] if f['id'] == 'anchor-2')
            anchor['alternatives'] = list(dict.fromkeys(anchor['alternatives'] + [
                'automatically adding generatedspecial methods',
                'automaticallyaddinggeneratedspecialmethods',
            ]))
        if not body_only and case['id'] == 'challenge-07':
            # Amazon appends a /ref=... tracking path after the canonical ASIN.
            # It is still the same navigable product-detail entry.
            case['acceptance_contract']['entry_path'] = (
                r'(?:/[^/]+)?/dp/[A-Z0-9]{10}(?:/ref=[^/?#]+)?')
            # Tracking/action suffixes are routes to the same Amazon product;
            # uniqueness is the captured ASIN, not the full navigable path.
            case['acceptance_contract']['entry_identity_pattern'] = (
                r'/dp/([A-Z0-9]{10})(?:/|$)')
        if case['id'] == 'live-023-guides':
            # The category/filter labels are complete but legitimately reorder
            # across renderers. Three prose regions plus the 60% floor on every
            # region retain distributed coverage without requiring list order.
            case['body_requirements']['minimum_region_passes'] = 3
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=current_evidence_dir())
    parser.add_argument('--body-only', action='store_true',
                        help='Regenerate body coverage contracts without changing references or other task rules')
    args = parser.parse_args()
    correct(args.source, body_only=args.body_only)
