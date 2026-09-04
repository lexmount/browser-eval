"""Verify reconstructed public files, scored bytes and explicit redaction hashes."""
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from harness.web_fetch.publication_privacy import GOOGLE_KEY, OTHER_SECRET
from bs4 import BeautifulSoup


def check(archive,source):
    with zipfile.ZipFile(archive) as bundle:
        manifest=json.loads(bundle.read('manifest.json'))
        @lru_cache(maxsize=8)
        def pack(name):return gzip.decompress(bundle.read(name))
        def data(name):
            parts=[]
            for digest in manifest['files'][name]['parts']:
                block=manifest['blocks'][digest]
                part=pack(block['pack'])[block['offset']:block['offset']+block['size']]
                if hashlib.sha256(part).hexdigest()!=digest:raise ValueError('Invalid block hash')
                parts.append(part)
            return b''.join(parts)
        for name,row in manifest['files'].items():
            raw=data(name)
            if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ValueError('Published file hash differs: '+name)
            if GOOGLE_KEY.search(raw) or OTHER_SECRET.search(raw):
                raise ValueError('Potential credential remains: '+name)
        rows=[json.loads(line) for line in data('results.jsonl').splitlines()]
        for row in rows:
            raw=data(row['artifact_path'])
            if hashlib.sha256(raw).hexdigest()!=row['metrics']['content_sha256']:
                raise ValueError('Scored output changed')
            if raw!=(source/row['artifact_path']).read_bytes():raise ValueError('Scored bytes differ from local source')
        privacy=json.loads(data('publication-privacy.json'))
        for row in privacy['files']:
            if hashlib.sha256((source/row['path']).read_bytes()).hexdigest()!=row['original_sha256']:
                raise ValueError('Original privacy hash differs')
            if hashlib.sha256(data(row['path'])).hexdigest()!=row['published_sha256']:
                raise ValueError('Projected privacy hash differs')
        article=BeautifulSoup(data('guide/article.html'),'html.parser')
        if article.select('script,link[rel="stylesheet"]'):raise ValueError('Standalone article has runtime dependencies')
        for anchor in article.select('a[href]'):
            if not anchor['href'].startswith(('https://','http://','#')):
                raise ValueError('Standalone article has a local evidence link')
        print(json.dumps({'files':len(manifest['files']),'scored_outputs_unchanged':len(rows),
            'redacted_html_files':len(privacy['files']),'replacements':sum(r['replacements'] for r in privacy['files']),
            'credential_pattern_scan':'pass','standalone_article':'pass'},ensure_ascii=False))


if __name__=='__main__':
    check(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'publish/webfetch-sites.zip',ROOT/'reports/webfetch')
