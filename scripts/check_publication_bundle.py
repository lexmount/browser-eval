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
from harness.web_fetch.publication_privacy import publication_risk
from harness.web_fetch.webmainbench_diagnostics import require_valid_teds_for_release
from bs4 import BeautifulSoup


def scored_output_hash(row: dict) -> str:
    expected = row.get('output_sha256') or row.get('metrics', {}).get('content_sha256')
    if not expected:
        raise ValueError('Record missing scored output hash: ' + str(row.get('artifact_path')))
    return expected


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
            risk=publication_risk(raw)
            if risk:raise ValueError('Potential '+risk+' remains: '+name)
        privacy=json.loads(data('publication-privacy.json'))
        privacy_by_path={row['path']:row for row in privacy['files']}
        rows=[json.loads(line) for line in data('results.jsonl').splitlines()]
        require_valid_teds_for_release(rows)
        projected_scored=0
        for row in rows:
            raw=data(row['artifact_path'])
            original=(source/row['artifact_path']).read_bytes()
            expected_hash=scored_output_hash(row)
            if hashlib.sha256(original).hexdigest()!=expected_hash:
                raise ValueError('Original scored output hash differs')
            disclosure=privacy_by_path.get(row['artifact_path'])
            if disclosure:
                if disclosure.get('scored_output') is not True:
                    raise ValueError('Scored projection is not disclosed')
                projected_scored+=1
            elif raw!=original:
                raise ValueError('Undisclosed scored output projection')
        for row in privacy['files']:
            if hashlib.sha256((source/row['path']).read_bytes()).hexdigest()!=row['original_sha256']:
                raise ValueError('Original privacy hash differs')
            if hashlib.sha256(data(row['path'])).hexdigest()!=row['published_sha256']:
                raise ValueError('Projected privacy hash differs')
        standalone='not-present'
        if 'guide/article.html' in manifest['files']:
            article=BeautifulSoup(data('guide/article.html'),'html.parser')
            if article.select('script,link[rel="stylesheet"]'):raise ValueError('Standalone article has runtime dependencies')
            for anchor in article.select('a[href]'):
                figure = anchor.find_parent('figure', class_='m-illustration')
                image = figure.find('img') if figure else None
                if image and anchor['href'].startswith('data:image/png;base64,') and anchor['href'] == image.get('src'):
                    continue
                if not anchor['href'].startswith(('https://','http://','#')):
                    raise ValueError('Standalone article has a local evidence link')
            standalone='pass'
        print(json.dumps({'files':len(manifest['files']),
            'scored_outputs_byte_identical':len(rows)-projected_scored,
            'scored_outputs_privacy_projected':projected_scored,
            'redacted_public_files':len(privacy['files']),'replacements':sum(r['replacements'] for r in privacy['files']),
            'public_sensitive_data_scan':'pass','standalone_article':standalone},ensure_ascii=False))


if __name__=='__main__':
    check(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'publish/webfetch-sites.zip',
          Path(sys.argv[2]) if len(sys.argv)>2 else ROOT/'reports/webfetch')
