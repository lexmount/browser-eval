"""Reviewed editorial illustrations, with identical online and offline bytes."""
import base64
import hashlib
import json
from html import escape
from pathlib import Path

from lexbench_webfetch.paths import contained_path
from .storage import data_home

MANIFEST = Path(__file__).resolve().parents[2] / 'research/editorial-figures.json'


def render_article_figures(out):
    manifest = json.loads(MANIFEST.read_text())
    source = data_home() / 'editorial/moli-webfetch'
    online, embedded, provenance = {}, {}, []
    for figure in manifest['figures']:
        urls, uris = {}, {}
        for locale, asset in figure['assets'].items():
            raw = contained_path(source, asset['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != asset['sha256']:
                raise ValueError('Editorial illustration changed; review image and update its binding: ' + asset['file'])
            if raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
                raise ValueError('Editorial illustration must be a PNG: ' + asset['file'])
            relative = 'assets/editorial/' + asset['file']
            destination = contained_path(out, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            urls[locale] = '../' + relative
            uris[locale] = 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')
            provenance.append({'figure': figure['slot'], 'locale': locale, 'path': relative,
                               'sha256': asset['sha256'], 'bytes': len(raw),
                               'kind': figure['kind'], 'generated': True})

        def markup(links):
            main, english = links['zh'], links.get('en')
            en_src = f' data-en-src="{escape(english, quote=True)}"' if english else ''
            en_href = f' data-en-href="{escape(english, quote=True)}"' if english else ''
            return (f'<figure class="m-illustration" id="{escape(figure["slot"], quote=True)}">'
                    f'<a href="{escape(main, quote=True)}"{en_href} target="_blank" rel="noopener" aria-label="打开完整图片">'
                    f'<img src="{escape(main, quote=True)}"{en_src} alt="{escape(figure["alt"], quote=True)}"></a>'
                    f'<figcaption>{escape(figure["caption"])} '
                    f'<a href="{escape(main, quote=True)}"{en_href} download="{escape(figure["slot"], quote=True)}.png">下载图片</a>'
                    '</figcaption></figure>')
        online[figure['slot']] = markup(urls)
        embedded[figure['slot']] = markup(uris)
    return online, embedded, provenance
