"""Public-source adoption survey behind `research/provider-adoption.json`.

`search` runs provider-specific GitHub code searches and then verifies the candidates;
`refresh` re-verifies the existing candidate union. Search ranking is a discovery aid,
not a reason to discard known candidates. Only file signatures verified at the current
default-branch HEAD count, and all methods use the same metadata filter. A failed search
raises instead of becoming a zero count.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from harness.distribution import repository_root
from harness.web_fetch.storage import research_evidence_dir

MINIMUM_STARS = 1000
ACTIVE_DAYS = 7
PER_SIGNATURE_CAP = 100
SCOPE = (
    "Provider-specific source, import and interface signatures on public default branches; "
    "first 100 results per query, stars >= 1000 and a default branch active within 7 days. "
    "Includes official repositories, forks, examples and documentation; does not represent "
    "production users or total adoption."
)
QUERIES = {
    'firecrawl': {'firecrawl-sdk': '"FirecrawlApp"', 'firecrawl-api': '"api.firecrawl.dev/v2/scrape"'},
    'tavily': {'tavily-extract': '"api.tavily.com/extract"', 'tavily-sdk-extract': '"tavily_client.extract"'},
    'exa': {'exa-contents': '"api.exa.ai/contents"', 'exa-sdk-contents': '"get_contents" "exa"'},
    'cloudflare-markdown': {'cloudflare-markdown': '"browser-rendering/markdown"'},
    'langchain-webbase': {'langchain-web-base-loader': '"WebBaseLoader"'},
    'crawl4ai': {'crawl4ai': '"AsyncWebCrawler"'},
    'cowagent-built-in': {
        'cowagent-webfetch-import': '"agent.tools.web_fetch.web_fetch"',
        'cowagent-webfetch-source': '"ALL_DOC_SUFFIXES" "WebFetch"',
    },
    'openclaw-built-in': {'openclaw-webfetch-source': '"createWebFetchTool"'},
    'deepseek-harness-built-in': {'dsh-webfetch-module': '"@deepseek-ai/dsh-web-fetch-http"'},
    'lexmount-global': {'lexmount-global-extract': '"api.lexmount.com" "extract"'},
    'lexmount-cn': {'lexmount-cn-extract': '"api.lexmount.cn" "extract"'},
}


def gh(*args, retries=3):
    for attempt in range(retries + 1):
        process = subprocess.run(["gh", "api", *args], text=True, capture_output=True)
        if process.returncode == 0:
            return json.loads(process.stdout)
        if attempt == retries:
            raise RuntimeError(process.stderr.strip() or "gh api failed")
        print("GitHub API retry", attempt + 1, process.stderr.strip()[:180], flush=True)
        time.sleep(15)
    raise AssertionError("unreachable")


def metadata_batch(repos):
    fields = []
    aliases = {}
    for index, full_name in enumerate(repos):
        owner, name = full_name.split("/", 1)
        alias = f"r{index}"
        aliases[alias] = full_name
        fields.append(
            f'{alias}: repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) {{'
            " nameWithOwner url stargazerCount isFork isArchived description "
            " defaultBranchRef { name target { ... on Commit { oid committedDate } } } }"
        )
    query = "query {" + " ".join(fields) + "}"
    payload = gh("graphql", "-f", f"query={query}")
    return {aliases[alias]: value for alias, value in payload.get("data", {}).items() if value}


def matches(signature, text):
    """Confirm a provider signature against file bytes, not search counts."""
    rules = {
        'firecrawl-sdk': lambda: 'FirecrawlApp' in text,
        'firecrawl-api': lambda: 'api.firecrawl.dev/v2/scrape' in text,
        'tavily-extract': lambda: 'api.tavily.com/extract' in text,
        'tavily-sdk-extract': lambda: 'tavily_client.extract' in text,
        'exa-contents': lambda: 'api.exa.ai/contents' in text,
        'exa-sdk-contents': lambda: (
            bool(re.search(r'(?:from exa_py import|import.*exa|Exa\()', text)) and 'get_contents' in text
        ),
        'cloudflare-markdown': lambda: 'browser-rendering/markdown' in text,
        'langchain-web-base-loader': lambda: 'WebBaseLoader' in text,
        'crawl4ai': lambda: 'AsyncWebCrawler' in text,
        'cowagent-webfetch-import': lambda: 'agent.tools.web_fetch.web_fetch' in text,
        'cowagent-webfetch-source': lambda: 'ALL_DOC_SUFFIXES' in text and 'WebFetch' in text,
        # The common createWebFetchTool name alone also occurs in unrelated tools.
        'openclaw-webfetch-source': lambda: 'createWebFetchTool' in text and (
            'resolveWebFetchConfig' in text
            or ('openclaw' in text.lower() and ('import' in text or 'require(' in text))
        ),
        'dsh-webfetch-module': lambda: '@deepseek-ai/dsh-web-fetch-http' in text,
        'lexmount-global-extract': lambda: 'api.lexmount.com' in text and '/v1/extract' in text,
        'lexmount-cn-extract': lambda: 'api.lexmount.cn' in text and '/v1/extract' in text,
    }
    return rules[signature]()


def candidate_union(current, earlier):
    providers = {s: p for p, sigs in current['provider_signatures'].items() for s in sigs}
    candidates = defaultdict(set)
    for data in [current, earlier]:
        for item in data.get('candidate_catalog', []):
            for signature in item['signatures']:
                if signature in providers:
                    candidates[(item['repo'], item['path'])].add(signature)
        for search in data.get('searches', []):
            if search['signature'] in providers:
                for hit in search['items']:
                    candidates[(hit['repo'], hit['path'])].add(search['signature'])
        for row in data['included']:
            for hit in row['matches']:
                signature = {'firecrawl-v2': 'firecrawl-api'}.get(hit['signature'], hit['signature'])
                if signature in providers:
                    candidates[(row['repo'], hit['path'])].add(signature)
    return candidates


def refresh(previous_snapshot=None, discovered=None):
    dest = repository_root() / 'research/provider-adoption.json'
    data = discovered if discovered is not None else json.loads(dest.read_text())
    prior = json.loads((repository_root() / 'research/webfetch-adopters.json').read_text())
    candidates = candidate_union(data, prior)
    if previous_snapshot:
        for key, sigs in candidate_union(data, previous_snapshot).items():
            candidates[key].update(sigs)
    # Known official implementation is a positive control, independent of
    # third-party adoption eligibility (e.g. an archived source repository).
    control = (
        'langchain-ai/langchain-community',
        'libs/community/langchain_community/document_loaders/web_base.py',
    )
    candidates[control].add('langchain-web-base-loader')
    names = sorted({repo for repo, path in candidates})
    metadata = {}
    for i in range(0, len(names), 30):
        metadata.update(metadata_batch(names[i:i + 30]))
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=ACTIVE_DAYS)
    eligible = {}
    excluded = []
    for repo in names:
        m = metadata.get(repo)
        if not m:
            raise RuntimeError('Metadata unavailable: ' + repo)
        target = (m.get('defaultBranchRef') or {}).get('target') or {}
        stamp = target.get('committedDate')
        reasons = []
        if m.get('isArchived'):
            reasons.append('archived')
        if m.get('stargazerCount', 0) < data['minimum_stars']:
            reasons.append('below_star_threshold')
        if not stamp or dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')) < cutoff:
            reasons.append('inactive_default_branch')
        if reasons:
            excluded.append({'repo': repo, 'reasons': reasons})
        else:
            eligible[repo] = m
    providers = {s: p for p, sigs in data['provider_signatures'].items() for s in sigs}

    def fetch(item):
        (repo, path), sigs = item
        head = metadata[repo]['defaultBranchRef']['target']['oid']
        proc = subprocess.run(
            ['gh', 'api', f'repos/{repo}/contents/{quote(path, safe="/")}?ref={head}'],
            capture_output=True, text=True,
        )
        if proc.returncode:
            if 'HTTP 404' in proc.stderr:
                return repo, path, [], 'file_removed'
            raise RuntimeError(proc.stderr)
        blob = json.loads(proc.stdout)
        if blob.get('encoding') != 'base64':
            raise RuntimeError('Unavailable file contents: ' + repo + '/' + path)
        raw = base64.b64decode(blob['content'])
        if hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != blob['sha']:
            raise RuntimeError('Blob integrity mismatch')
        text = raw.decode('utf-8', errors='replace')
        hits = [
            {
                'repo': repo, 'path': path, 'signature': s, 'provider': providers[s],
                'url': f'https://github.com/{repo}/blob/{head}/{quote(path, safe="/")}',
                'blob_sha': blob['sha'], 'verified_blob_sha256': hashlib.sha256(raw).hexdigest(),
            }
            for s in sorted(sigs) if matches(s, text)
        ]
        return repo, path, hits, 'verified' if hits else 'signature_absent'

    jobs = [(key, sigs) for key, sigs in candidates.items() if key[0] in eligible or key == control]
    print('Verifying', len(jobs), 'candidate files across', len(eligible), 'eligible repositories', flush=True)
    results = list(ThreadPoolExecutor(max_workers=6).map(fetch, jobs))
    byrepo = defaultdict(list)
    audit = []
    controls = []
    for repo, path, hits, status in results:
        audit.append({'repo': repo, 'path': path, 'status': status})
        if (repo, path) == control:
            if not hits:
                raise RuntimeError('Known-positive implementation check failed')
            controls.append({
                'provider': 'langchain-webbase', 'repo': repo, 'matches': hits,
                'included_in_count': repo in eligible,
            })
        if repo in eligible:
            byrepo[repo].extend(hits)
    included = []
    for repo, hits in sorted(byrepo.items()):
        if not hits:
            continue
        m = metadata[repo]
        head = m['defaultBranchRef']['target']
        included.append({
            'repo': repo, 'url': m['url'], 'stars': m['stargazerCount'], 'is_fork': m['isFork'],
            'default_branch_head_at': head['committedDate'], 'default_branch_head': head['oid'],
            'matches': hits,
        })
    data.update(
        generated_at=now.isoformat(), cutoff=cutoff.isoformat(), included=included,
        candidate_refresh={
            'candidate_repositories': len(names), 'candidate_files': len(candidates),
            'verified_files': len(results), 'excluded_repositories': excluded, 'file_checks': audit,
            'discovery_snapshots': [prior['generated_at'], data['generated_at']],
            'rule': 'Union previously discovered provider-specific files; refresh metadata and verify current HEAD blobs.',
        },
        implementation_controls=controls,
    )
    data['candidate_catalog'] = [
        {'repo': repo, 'path': path, 'signatures': sorted(sigs)}
        for (repo, path), sigs in sorted(candidates.items())
    ]
    data['unique_repositories_examined'] = len(
        {x['repo'].lower() for search in data['searches'] for x in search['items']}
        | {repo.lower() for repo in names}
    )
    temporary = dest.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(dest)
    print('Verified', len(included), 'repositories; official implementation control passed', flush=True)


def search():
    """Search every provider signature, then verify the candidates through `refresh`."""
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=ACTIVE_DAYS)
    dest = repository_root() / 'research/provider-adoption.json'
    checkpoint = research_evidence_dir() / 'adoption-searches.json'
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(dest.read_text()) if dest.exists() else None
    searches = []
    hits = defaultdict(list)
    for provider, queries in QUERIES.items():
        for signature, query in queries.items():
            result = gh('search/code', '-X', 'GET', '-f', 'q=' + query, '-f', f'per_page={PER_SIGNATURE_CAP}')
            if result.get('incomplete_results'):
                raise RuntimeError('Incomplete search: ' + query)
            if signature == 'firecrawl-sdk' and not result.get('total_count'):
                raise RuntimeError('Known-positive control failed; do not publish zero counts')
            items = [
                {'repo': x['repository']['full_name'], 'path': x['path'], 'url': x['html_url'], 'blob_sha': x['sha']}
                for x in result['items'] if not x['repository'].get('private')
            ]
            searches.append({
                'provider': provider, 'signature': signature, 'query': query,
                'searched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                'total_count': result['total_count'], 'returned': len(items),
                'cap': PER_SIGNATURE_CAP, 'items': items,
            })
            for item in items:
                hits[item['repo']].append({**item, 'signature': signature, 'provider': provider})
            checkpoint.write_text(json.dumps(searches, ensure_ascii=False, indent=2) + '\n')
            print(provider, signature, result['total_count'], len(items), flush=True)
            time.sleep(7)
    names = sorted(hits)
    metadata = {}
    for offset in range(0, len(names), 30):
        metadata.update(metadata_batch(names[offset:offset + 30]))
    included = []
    for repo in names:
        m = metadata.get(repo, {})
        target = (m.get('defaultBranchRef') or {}).get('target') or {}
        stamp = target.get('committedDate')
        if not stamp or m.get('isArchived') or m.get('stargazerCount', 0) < MINIMUM_STARS:
            continue
        if dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')) < cutoff:
            continue
        included.append({
            'repo': repo, 'url': m['url'], 'stars': m['stargazerCount'], 'is_fork': m['isFork'],
            'default_branch_head_at': stamp, 'default_branch_head': target['oid'], 'matches': hits[repo],
        })
    result = {
        'generated_at': now.isoformat(), 'cutoff': cutoff.isoformat(), 'minimum_stars': MINIMUM_STARS,
        'per_signature_cap': PER_SIGNATURE_CAP, 'unique_repositories_examined': len(names),
        'signatures': {k: v for q in QUERIES.values() for k, v in q.items()},
        'provider_signatures': {p: list(q) for p, q in QUERIES.items()},
        'searches': searches, 'included': included, 'scope': SCOPE,
    }
    refresh(previous_snapshot=previous, discovered=result)
    print('COMPLETE', len(QUERIES), 'providers', len(included), 'included repositories', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['search', 'refresh'])
    args = parser.parse_args()
    if args.command == 'search':
        search()
    else:
        refresh()


if __name__ == '__main__':
    main()
