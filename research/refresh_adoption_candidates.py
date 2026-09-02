"""Verify the union of discovered candidate files at current default-branch HEAD.

Search ranking is a discovery aid, not a reason to discard known candidates.
Only verified file signatures count. All methods use the same metadata filter.
"""
import base64
import datetime as dt
import hashlib
import json
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from discover_webfetch_adopters import metadata_batch
from verify_adoption_sources import matches

ROOT=Path(__file__).resolve().parents[1]


def candidate_union(current, earlier):
    providers={s:p for p,sigs in current['provider_signatures'].items() for s in sigs}
    candidates=defaultdict(set)
    for data in [current,earlier]:
        for item in data.get('candidate_catalog',[]):
            for signature in item['signatures']:
                if signature in providers:candidates[(item['repo'],item['path'])].add(signature)
        for search in data.get('searches',[]):
            if search['signature'] in providers:
                for hit in search['items']:candidates[(hit['repo'],hit['path'])].add(search['signature'])
        for row in data['included']:
            for hit in row['matches']:
                signature={'firecrawl-v2':'firecrawl-api'}.get(hit['signature'],hit['signature'])
                if signature in providers:
                    candidates[(row['repo'],hit['path'])].add(signature)
    return candidates


def refresh(previous_snapshot=None, discovered=None):
    dest=ROOT/'research/provider-adoption.json'
    data=discovered if discovered is not None else json.loads(dest.read_text())
    prior=json.loads((ROOT/'research/webfetch-adopters.json').read_text())
    candidates=candidate_union(data,prior)
    if previous_snapshot:
        for key,sigs in candidate_union(data,previous_snapshot).items(): candidates[key].update(sigs)
    # Known official implementation is a positive control, independent of
    # third-party adoption eligibility (e.g. an archived source repository).
    control=('langchain-ai/langchain-community','libs/community/langchain_community/document_loaders/web_base.py')
    candidates[control].add('langchain-web-base-loader')
    names=sorted({repo for repo,path in candidates});metadata={}
    for i in range(0,len(names),30): metadata.update(metadata_batch(names[i:i+30]))
    now=dt.datetime.now(dt.timezone.utc);cutoff=now-dt.timedelta(days=7)
    eligible={};excluded=[]
    for repo in names:
        m=metadata.get(repo)
        if not m: raise RuntimeError('Metadata unavailable: '+repo)
        target=(m.get('defaultBranchRef') or {}).get('target') or {}
        stamp=target.get('committedDate')
        reasons=[]
        if m.get('isArchived'):reasons.append('archived')
        if m.get('stargazerCount',0)<data['minimum_stars']:reasons.append('below_star_threshold')
        if not stamp or dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))<cutoff:reasons.append('inactive_default_branch')
        if reasons:excluded.append({'repo':repo,'reasons':reasons})
        else:eligible[repo]=m
    providers={s:p for p,sigs in data['provider_signatures'].items() for s in sigs}
    def fetch(item):
        (repo,path),sigs=item
        m=metadata[repo];head=m['defaultBranchRef']['target']['oid']
        proc=subprocess.run(['gh','api',f'repos/{repo}/contents/{quote(path,safe="/")}?ref={head}'],capture_output=True,text=True)
        if proc.returncode:
            if 'HTTP 404' in proc.stderr:return repo,path,[], 'file_removed'
            raise RuntimeError(proc.stderr)
        blob=json.loads(proc.stdout)
        if blob.get('encoding')!='base64':raise RuntimeError('Unavailable file contents: '+repo+'/'+path)
        raw=base64.b64decode(blob['content'])
        if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=blob['sha']:raise RuntimeError('Blob integrity mismatch')
        text=raw.decode('utf-8',errors='replace')
        hits=[{'repo':repo,'path':path,'signature':s,'provider':providers[s],
               'url':f'https://github.com/{repo}/blob/{head}/{quote(path,safe="/")}',
               'blob_sha':blob['sha'],'verified_blob_sha256':hashlib.sha256(raw).hexdigest()}
              for s in sorted(sigs) if matches(s,text,repo)]
        return repo,path,hits,'verified' if hits else 'signature_absent'
    jobs=[(key,sigs) for key,sigs in candidates.items() if key[0] in eligible or key==control]
    print('Verifying',len(jobs),'candidate files across',len(eligible),'eligible repositories',flush=True)
    results=list(ThreadPoolExecutor(max_workers=6).map(fetch,jobs))
    byrepo=defaultdict(list);audit=[];controls=[]
    for repo,path,hits,status in results:
        audit.append({'repo':repo,'path':path,'status':status})
        if (repo,path)==control:
            if not hits:raise RuntimeError('Known-positive implementation check failed')
            controls.append({'provider':'langchain-webbase','repo':repo,'matches':hits,'included_in_count':repo in eligible})
        if repo in eligible:byrepo[repo].extend(hits)
    included=[]
    for repo,hits in sorted(byrepo.items()):
        if not hits:continue
        m=metadata[repo];head=m['defaultBranchRef']['target']
        included.append({'repo':repo,'url':m['url'],'stars':m['stargazerCount'],'is_fork':m['isFork'],
                         'default_branch_head_at':head['committedDate'],'default_branch_head':head['oid'],'matches':hits})
    data.update(generated_at=now.isoformat(),cutoff=cutoff.isoformat(),included=included,
                candidate_refresh={'candidate_repositories':len(names),'candidate_files':len(candidates),
                  'verified_files':len(results),'excluded_repositories':excluded,'file_checks':audit,
                  'discovery_snapshots':[prior['generated_at'],data['generated_at']],
                  'rule':'Union previously discovered provider-specific files; refresh metadata and verify current HEAD blobs.'},
                implementation_controls=controls)
    data['candidate_catalog']=[{'repo':repo,'path':path,'signatures':sorted(sigs)} for (repo,path),sigs in sorted(candidates.items())]
    data['unique_repositories_examined']=len({x['repo'].lower() for search in data['searches'] for x in search['items']} | {repo.lower() for repo in names})
    temporary=dest.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(dest)
    print('Verified',len(included),'repositories; official implementation control passed',flush=True)


if __name__=='__main__':refresh()
