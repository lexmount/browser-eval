"""Render source-backed WCXB comparison figures with exact version labels.

This is a presentation helper. Measured responses and source snippets must be
checked against the certified cohort before changing the examples below.
"""
from __future__ import annotations

import asyncio
from html import escape
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'harness/web_fetch/assets/wcxb-repair'
FIGURES = {
    'code-block': (
        '5412 · 预格式代码块',
        ('冻结 HTML', '&lt;div&gt;ts&lt;/div&gt;\n&lt;div&gt;function identity(…)&lt;/div&gt;'),
        ('Moli v1.1.8 · 稳定版实测', 'tsfunction identity(…)'),
        ('Moli PR #643 · 384d1d2a 实测', 'ts\nfunction identity(…)'),
        '中、右两列均来自同一批 504 页评测的 5412 页完整响应。',
    ),
    'description': (
        '4017 · 商品说明',
        ('冻结 HTML', '&lt;details&gt;&lt;summary&gt;Description&lt;/summary&gt;\n&lt;p&gt;Struggling with discomfort…&lt;/p&gt;&lt;/details&gt;'),
        ('Moli v1.1.8 · 可选清理实测', 'Description 与商品正文消失'),
        ('Moli PR #643 · 384d1d2a 实测', 'Description\nStruggling with discomfort…'),
        '同一冻结页启用可选界面清理的额外实测；不属于 504 页常规调用。',
    ),
    'adjacent-code': (
        '5458 · 相邻代码值',
        ('冻结 HTML', '&lt;div class="flex items-center gap-2 not-prose"&gt;\n  &lt;code class="font-bold text-primary"&gt;name&lt;/code&gt;\n  &lt;code class="font-semibold text-xs bg-white/5 px-1.5 py-1 rounded-md"&gt;string&lt;/code&gt;\n&lt;/div&gt;'),
        ('Moli v1.1.8 · 稳定版实测', 'Markdown: `namestring`\n\n渲染 DOM: &lt;code&gt;namestring&lt;/code&gt;'),
        ('Moli PR #643 · 384d1d2a 实测', 'Markdown: &lt;code&gt;name&lt;/code&gt;`string`\n\n渲染 DOM: &lt;code&gt;name&lt;/code&gt;&lt;code&gt;string&lt;/code&gt;'),
        '中、右两列均来自同一批 504 页评测。右列保留两个 code 节点；可见间距由 CSS gap-2 产生，不在 Markdown 源码中。',
    ),
}


def panel(label: str, content: str, kind: str) -> str:
    return (f'<div class="panel {kind}"><div class="label">{escape(label)}</div>'
            f'<pre>{content}</pre></div>')


def document(name: str) -> str:
    title, source, release, candidate, scope = FIGURES[name]
    pre_style = ('font-size:25px;line-height:1.5;min-height:230px'
                 if name == 'adjacent-code'
                 else 'font-size:29px;line-height:1.55;min-height:192px')
    return f'''<!doctype html><html lang="zh"><meta charset="utf-8"><style>
*{{box-sizing:border-box}}body{{margin:0;background:#fff;color:#1d2c42;font-family:-apple-system,BlinkMacSystemFont,"Noto Sans CJK SC",sans-serif}}
#figure{{width:2048px;padding:60px 64px 50px;background:#fff}}
h1{{font-size:40px;margin:0 0 34px}}.grid{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:28px}}
.panel{{min-width:0;min-height:360px;padding:30px;border:3px solid #b8c8dc;border-radius:20px;background:#f4f8fc}}
.release{{border-color:#e38b86;background:#fff5f3}}.candidate{{border-color:#78c59d;background:#f2fff7}}
.label{{font-size:28px;font-weight:750;line-height:1.35;min-height:82px}}
pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;{pre_style};white-space:pre-wrap;overflow-wrap:anywhere;margin:25px 0 0;padding:24px;border-radius:12px;background:#fff;border:1px solid #cfdae8}}
.scope{{font-size:25px;color:#42546a;margin:28px 0 0}}
</style><div id="figure"><h1>{escape(title)}</h1><div class="grid">{panel(source[0], source[1], 'source')}{panel(release[0], release[1], 'release')}{panel(candidate[0], candidate[1], 'candidate')}</div><p class="scope">{escape(scope)}</p></div></html>'''


async def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        )
        page = await browser.new_page(viewport={'width': 2048, 'height': 720}, device_scale_factor=1)
        for name in FIGURES:
            await page.set_content(document(name))
            await page.locator('#figure').screenshot(path=str(OUTPUT / f'{name}.png'))
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
