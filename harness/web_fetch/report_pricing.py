"""Source-backed commercial prices, separate from measured benchmark outcomes."""
import json
import math
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / 'research/provider-pricing.json'


def per_thousand(price, credits, credits_per_page=1):
    """Fully utilized commitment, not a marginal price or a success-rate estimate."""
    if price < 0 or credits <= 0 or credits_per_page <= 0:
        raise ValueError('Invalid pricing inputs')
    return price / credits * credits_per_page * 1000


def browser_monthly(pages, seconds, base=5, included_hours=10, hourly=0.09):
    if pages < 0 or seconds < 0:
        raise ValueError('Invalid usage')
    return base + max(0, math.floor(pages * seconds / 3600 + .5) - included_hours) * hourly


def money(value):
    return '$' + f'{value:,.3f}'.rstrip('0').rstrip('.')


def render_pricing(out, names, *, table, cell, link, esc):
    data = json.loads(SOURCE.read_text())
    dest = out / 'adoption'
    dest.mkdir(exist_ok=True)
    (dest / 'pricing.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')

    def sources(p):
        return ' · '.join(link(s['url'], s['label'] + ' ↗') for s in p['sources'])

    if set(names) <= {'kitesurf','lightpanda','obscura','moli-1.0.6','moli','moli-1.1.3','moli-1.1.7','moli-1.1.11','chromium'}:
        rows=[('',cell(esc(p['label']))+cell(esc(p['status']))+cell(esc(p['note']))+cell(sources(p)))
              for p in data['other_methods'] if set(p['ids']) & set(names)]
        return '<h3>本轮执行模式的成本边界</h3><p>这是客户端实测，不是计费或同机 CPU / 内存测试；各方法的资料核验日期见表内说明。</p>'+table(['方法','价格状态','成本边界','官方来源'],rows,cls='pricing-table',metrics=[])

    body = '<h3>获取 1,000 个网页，究竟付哪一种费用？</h3>'
    body += '<p>按给定 URL 获取正文比较，不包含搜索、研究 Agent 或聊天模型。按页产品可以换算为每千页；浏览器时长产品必须带上耗时假设。套餐价、超额价与试用额度分开列示。</p>'
    body += '<p class="source-note">官方公开价格核验：' + esc(data['checked_at']) + '；币种 USD，未加地区税费。以下为报价换算，不是本轮账单；供应商定义的提取成功不等于评测任务通过。' + link('pricing.json', '下载价格与来源数据') + '</p>'
    rows = []
    for p in data['providers']:
        if p['id'] not in names:
            continue
        rows.append(('', cell(link('#pricing-' + p['id'], p['label'])) +
                     ''.join(cell(esc(p[k])) for k in ['unit', 'comparison', 'commitment', 'discount']) +
                     cell(link(p['sources'][0]['url'], '官方定价 ↗'))))
    body += table(['产品 / 模式', '原始计价', '每千页参考价', '付费门槛', '优惠与限制', '来源'], rows, cls='pricing-table', metrics=[])
    body += '<div class="worked-example"><strong>如何读这个价格</strong><span>套餐折算价＝套餐费用 ÷ 可用积分 × 每页积分 × 1,000。只用完 20% 的额度，实际平均单价就是满额折算价的 5 倍。超额单价用于追加用量，不能替代套餐平均价。免费额度内供应商费用可为零；超额费用及自身运行成本另计。</span></div>'
    body += '<h3>各提供方计费方案</h3><p>展开查看原价、年付、追加用量、功能加价与失败扣费规则。</p>'
    for p in data['providers']:
        if p['id'] not in names:
            continue
        body += '<details class="interface-panel" id="pricing-' + esc(p['id']) + '"><summary>' + esc(p['label']) + ' · ' + esc(p['headline']) + '</summary>'
        body += '<p>' + esc(p['scope']) + '</p>'
        if p.get('verified_at'):
            body += '<p class="source-note">本条官方资料核验：' + esc(p['verified_at']) + '。</p>'
        body += '<p class="source-note">' + sources(p) + '</p>'
        if p.get('plans'):
            plans = []
            for plan in p['plans']:
                monthly = plan['monthly_usd']
                quota = plan['credits_monthly']
                annual = plan.get('annual_usd')
                monthly_unit = per_thousand(monthly, quota, p['credits_per_page'])
                annual_unit = per_thousand(annual / 12, quota, p['credits_per_page']) if annual is not None else None
                annual_text = (money(annual) + '/年；月均 ' + money(annual / 12) +
                               '（较月付省 ' + f'{(1 - annual / (12 * monthly)) * 100:.1f}%' + '）') if annual is not None else '未公开年付优惠'
                plans.append(('', cell(esc(plan['name'])) + cell(f'{quota:,}') + cell(money(monthly) + '/月') +
                              cell(annual_text) + cell(money(monthly_unit) + (' / ' + money(annual_unit) if annual_unit is not None else ' / —')) +
                              cell(esc(plan['overage']))))
            body += table(['套餐', '每月积分', '月付原价', '年付总额与优惠', '满额每千页（月付 / 年付）', '额度外用量'], plans, cls='pricing-table', metrics=[])
        if p.get('rates'):
            body += table(p['rate_headers'], [('', ''.join(cell(esc(v)) for v in row)) for row in p['rates']], cls='pricing-table', metrics=[])
        body += '<dl class="interface-fields">' + ''.join('<div><dt>' + esc(title) + '</dt><dd>' + esc(text) + '</dd></div>' for title, text in p['terms']) + '</dl>'
        for offer in p.get('offers', []):
            body += '<p><strong>' + esc(offer['label']) + '</strong>：' + esc(offer['text']) + ' ' + link(offer['url'], '官方资格与条款 ↗') + '</p>'
        if p['id'] == 'cloudflare-markdown':
            rows = []
            for seconds in (5, 10, 40):
                rows.append(('', cell(str(seconds) + ' 秒') + cell(money(seconds * .025)) +
                             cell(money(browser_monthly(1000, seconds))) + cell(money(browser_monthly(100000, seconds)))))
            body += '<h4>时长敏感性示例</h4><p>假设仅使用 /markdown，全部用量可计费；按响应头 X-Browser-Ms-Used 计时。每千页边际价＝每页秒数 × $0.025，不含最低月费、赠送时长和月度取整。</p>'
            body += table(['假设计费时长 / 页', '每千页边际价', '1,000 页/月总支出', '100,000 页/月总支出'], rows, cls='pricing-table', metrics=[])
            body += '<p class="source-note">月支出＝$5＋$0.09 × max(0, round(月浏览器小时数)−10)，半小时向上取整。仅含 Workers 基础月费与浏览器时长；已有付费 Workers 时，增量成本不应重复加 $5。不是用榜单的客户端耗时推算账单。</p>'
        body += '</details>'
    body += '<h3>其他参评方法的成本归属</h3>'
    rows = [('', cell(esc(p['label'])) + cell(esc(p['status'])) + cell(esc(p['note'])) + cell(sources(p))) for p in data['other_methods'] if set(p['ids']) & set(names)]
    body += table(['方法', '价格状态', '成本边界', '来源'], rows, cls='pricing-table', metrics=[])
    body += '''<h3>对定价设计的启示</h3>
<p>建议以“成功取得一个 URL 的内容”为基础计费单位，前台显示每千页价格；后台可以用积分记账，但要公开各能力的换算。不按 HTTP 请求次数简单收费：一次批量调用可能含多个 URL，一次取数也可能经历重试。</p>
<div class="guide-grid">
<article class="guide-card"><h4>基础取数与增强能力分开</h4><p>普通正文是一档；高成本代理、长时间交互、OCR、模型结构化抽取单列加价。不能只因网站执行了 JavaScript 就加价，应由真实资源成本和服务承诺决定，并给用户加价上限。</p></article>
<article class="guide-card"><h4>先按量，再按承诺量折扣</h4><p>低门槛按量价便于试用；月套餐降低稳定用量的单价，年付再交换资金与用量承诺。公布超额价、额度有效期、并发上限和自动续费上限，避免套餐便宜、追加昂贵却看不出来。</p></article>
<article class="guide-card"><h4>把“成功”写成可执行规则</h4><p>建议网络错误、空内容、可识别的登录或验证码页不收基础提取费；服务内部重试不重复计页。同一 URL 的再次主动刷新可计新页。成功取回内容不承诺每项业务判断正确，边界需写入接口契约。</p></article>
<article class="guide-card"><h4>价格区间不能代替成本测算</h4><p>用代理、浏览器时间、带宽、解析、失败重试和支持成本测算每千个可计费页面的成本，再用目标毛利反推售价。公开竞品价格是参照，不是成本下限；本报告没有完整账单，不能直接给出“最便宜且最好”的排名。</p></article>
</div>
<details><summary>用于报价的成本公式与比较边界</summary><p>单位成本＝该档全部请求的资源与运营成本 ÷ 可计费成功页数；价格底线＝单位成本 ÷（1−目标毛利率）。即使失败不向客户收费，失败重试成本仍须纳入分子。套餐定价还需考虑未用额度、峰值并发及承诺服务。</p><p>比较实际采购成本时，用实际总账单 ÷ 满足相同业务验收条件的页面数。不要直接把当前公开价格除以本轮任务通过率：执行日期、付费档位、供应商成功口径、缓存与功能加价都未必一致。以上为定价设计建议，不是提供方现行条款。</p></details>'''
    return body
