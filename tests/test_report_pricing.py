import json
import tempfile
from pathlib import Path, PurePosixPath

import pytest

from harness.web_fetch.report import table, cell, link, esc
from harness.web_fetch.report_i18n import localize_html, untranslated_english_narrative
from harness.web_fetch.report_pricing import SOURCE, per_thousand, browser_monthly, render_pricing


def test_exact_commitment_conversion_not_rounded_annual_label():
    assert per_thousand(19, 5000) == 3.8
    assert per_thousand(190 / 12, 5000) == pytest.approx(3.1666666667)
    assert per_thousand(7190 / 12, 1000000) == pytest.approx(.5991666667)
    assert per_thousand(100, 15000, .2) == pytest.approx(1.3333333333)
    assert per_thousand(220, 38000, .4) == pytest.approx(2.3157894737)
    with pytest.raises(ValueError):
        per_thousand(20, 0)


def test_browser_bill_includes_base_allowance_and_half_up_monthly_rounding():
    assert browser_monthly(1000, 5) == 5
    assert browser_monthly(100000, 5) == pytest.approx(16.61)
    assert browser_monthly(1, 10.5 * 3600 - 1) == 5
    assert browser_monthly(1, 10.5 * 3600) == pytest.approx(5.09)
    assert browser_monthly(1, 50 * 3600) == pytest.approx(8.6)
    with pytest.raises(ValueError):
        browser_monthly(-1, 10)


def test_catalog_has_sources_dates_and_no_invented_annual_prices():
    data = json.loads(SOURCE.read_text())
    assert data['checked_at'] == '2026-09-03'
    providers = {p['id']:p for p in data['providers']}
    assert set(providers) == {'firecrawl','tavily','exa','cloudflare-markdown'}
    firecrawl = providers['firecrawl']
    assert firecrawl['verified_at'] == '2026-09-11'
    assert 'proxy=auto' in firecrawl['scope']
    assert 'parsers=[]' in firecrawl['scope']
    assert '不代表调用方的实际账单' in firecrawl['scope']
    assert '不代表实际扣费' in firecrawl['scope']
    assert firecrawl['rates'][0] == [
        '基础 Scrape / auto / enhanced',
        '1 credit/页',
        'auto 在特定源站状态下最多升级重试一次；当前 enhanced 与重试均不加收积分',
    ]
    assert firecrawl['rates'][3][1] == '基础价之上 +1 credit/PDF 页'
    assert 'rawBase64 整份 PDF 固定 1 credit' in firecrawl['rates'][3][2]
    assert [p['annual_usd'] for p in providers['firecrawl']['plans']] == [190, 990, 3990, 7190]
    assert all('annual_usd' not in p for p in providers['tavily']['plans'])
    for p in providers.values():
        assert p['terms'] and p['sources']
        assert all(s['url'].startswith('https://') for s in p['sources'])
    ids = [i for p in data['other_methods'] for i in p['ids']]
    assert len(ids) == len(set(ids))
    assert {'moli','moli-1.1.3','moli-1.1.11','kitesurf','chromium'} <= set(ids)
    moli_111 = next(p for p in data['other_methods'] if p['ids'] == ['moli-1.1.11'])
    assert '2026-09-28' in moli_111['note']
    assert moli_111['sources'][0]['url'].endswith('/v1.1.11')


def test_render_has_summary_details_download_and_explicit_billing_limits():
    data = json.loads(SOURCE.read_text())
    names = [p['id'] for p in data['providers']] + [i for p in data['other_methods'] for i in p['ids']]
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory)
        rendered = render_pricing(out, names, table=table, cell=cell, link=link, esc=esc)
        assert json.loads((out/'adoption/pricing.json').read_text()) == data
    for name in names[:4]:
        assert f'id="pricing-{name}"' in rendered
        assert f'href="#pricing-{name}"' in rendered
    for text in ['每千页参考价', '$190/年', '$7,190/年', '20.0%', '未公开年付优惠',
                 '不是本轮账单', '未查到公开价格', 'X-Browser-Ms-Used', '403/404',
                 '创业公司优惠', '不是用榜单的客户端耗时推算账单',
                 '本条官方资料核验：2026-09-11。', 'proxy=auto', 'parsers=[]',
                 'rawBase64 PDF 整份固定 1 credit', '容量预检']:
        assert text in rendered
    assert '<summary>Firecrawl' in rendered
    assert 'id="pricing-firecrawl" open' not in rendered


def test_render_only_registered_providers():
    with tempfile.TemporaryDirectory() as directory:
        rendered = render_pricing(Path(directory), ['exa'], table=table, cell=cell, link=link, esc=esc)
    assert 'id="pricing-exa"' in rendered
    assert 'id="pricing-firecrawl"' not in rendered


def test_report_bound_pricing_overrides_legacy_catalog(tmp_path):
    bound = {
        "checked_at": "2026-10-16",
        "providers": [],
        "other_methods": [{
            "ids": ["local-browser"], "label": "Local browser",
            "status": "Self-hosted", "note": "Caller pays infrastructure.",
            "sources": [{"label": "Source", "url": "https://example.test/pricing"}],
        }],
    }
    path = tmp_path / "report-assets/provider-pricing.json"
    path.parent.mkdir()
    path.write_text(json.dumps(bound))
    rendered = render_pricing(
        tmp_path, ["local-browser"], table=table, cell=cell, link=link, esc=esc
    )
    assert "Local browser" in rendered
    assert json.loads((tmp_path / "adoption/pricing.json").read_text()) == bound


def test_firecrawl_current_route_and_billing_boundaries_are_localized_one_to_one():
    with tempfile.TemporaryDirectory() as directory:
        rendered = render_pricing(
            Path(directory), ['firecrawl'], table=table, cell=cell, link=link, esc=esc
        )
    english = localize_html(
        '<html lang="zh-CN"><body>' + rendered + '</body></html>',
        PurePosixPath('adoption/index.html'),
    )
    for text in [
        'Under the current official rules',
        'Official sources for this entry verified: 2026-09-11.',
        'Base Scrape / auto / enhanced',
        '+1 credit/PDF page on top of the base price',
        'Capacity preflight only checks whether the account can cover the declared call count',
    ]:
        assert text in english
    assert untranslated_english_narrative(english) == []


def test_bound_pricing_supplies_new_bilingual_statement_without_catalog_edits(tmp_path):
    data = json.loads(SOURCE.read_text())
    provider = next(p for p in data['providers'] if p['id'] == 'exa')
    provider['scope'] = '模拟说明：新的套餐边界。'
    provider['verified_at'] = '2031-02-03'
    data['translations'] = {'模拟说明：新的套餐边界。': 'Synthetic note: revised plan scope.'}
    assets = tmp_path / 'report-assets'
    assets.mkdir()
    (assets / 'provider-pricing.json').write_text(json.dumps(data))
    rendered = render_pricing(tmp_path, ['exa'], table=table, cell=cell, link=link, esc=esc)
    english = localize_html(rendered, PurePosixPath('adoption/index.html'))
    assert '模拟说明：新的套餐边界。' in rendered
    assert 'Synthetic note: revised plan scope.' in english
    assert '模拟说明' not in english
    assert '2031-02-03' in english
    assert json.loads((tmp_path / 'adoption/pricing.json').read_text()) == data
