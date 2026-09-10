"""Acceptance checks for compact metrics, filters and provider-centred adoption."""
import json,sys
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'reports/webfetch'
for path in out.rglob('*.html'):
    soup=BeautifulSoup(path.read_text(),'html.parser')
    is_english='en' in path.relative_to(out).parts
    if path in {out/'index.html',out/'en/index.html'}:
        overview=soup.select_one('.home-overview, #overview')
        assert overview and (overview.name!='details' or not overview.has_attr('open')),path
        panels=soup.select('#home-rankings > [role="tabpanel"]')
        assert sum(not p.has_attr('hidden') for p in panels)==1,path
        for panel in panels:
            assert len(panel.select('.track-guide'))==1,path
            assert len(panel.select('.track-flow li'))>=4,path
            for selector in ('.track-meaning','.track-flow','.pass-contract'):
                assert panel.select_one(selector).find_parent('details') is None,path
            for selector in ('.track-more','.ranking-metric-help'):
                assert not panel.select_one(selector).has_attr('open'),path
    for pre in soup.select('pre'):pre.decompose()
    assert '条件通过率' not in soup.get_text() and '条件达标率' not in soup.get_text(),path
    for table in soup.select('table'):
        width=len(table.select('thead th'))
        assert all(len(row.find_all('td',recursive=False))==width for row in table.select('tbody > tr')),(path,width)
    for table in soup.select('table.ranking'):
        headers=table.select('thead th')
        sort=int(table['data-col'])
        expected=(['Matched repositories'] if is_english else ['命中仓库数']) if path.parent.name=='adoption' else (
            ['Task pass rate','Body pass rate','Overall task pass rate'] if is_english else
            ['任务通过率','正文通过率','综合任务通过率'])
        assert any(label in headers[sort].get_text() for label in expected),(path,sort)
        legend=table.parent.find_previous_sibling('div',class_='metric-help') or table.parent.find_previous_sibling('details',class_='ranking-metric-help')
        assert legend and legend.select('dt'),path
    track=soup.select_one('select[data-filter="track"]')
    if track:assert track.select('option')[0]['value']=='',path
    if path.parent.name in {'providers','analysis'}:
        assert not soup.select('select[data-filter="transport"],select[data-filter="content"],select[data-filter="resources"]'),path
        expected_results=['Pass','Fail','All'] if is_english else ['任务通过','任务失败','全部']
        assert [x.get_text() for x in soup.select('select[data-filter="result"] option')]==expected_results,path
        assert soup.select_one('select[data-filter="result"] option[selected]')['value']=='fail',path
        expected_failure='Failure class' if is_english else '失败分类'
        assert expected_failure in [h.get_text() for h in soup.select('#filtered-table th')],path
counts=json.loads((out/'adoption/counts.json').read_text())['methods']
names={json.loads(line)['provider'] for line in (out/'results.jsonl').read_text().splitlines()}
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_methods import PROFILES
assert {item['provider'] for item in counts}==set(PROFILES)|names
interfaces=BeautifulSoup((out/'adoption/index.html').read_text(),'html.parser').select('.interface-panel[id^="interface-"]')
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_interfaces import SPECS
assert len(interfaces)==len(SPECS) and sum(p.has_attr('open') for p in interfaces)==bool(interfaces)
for panel in interfaces:
    assert len(panel.select('pre'))==2
for item in counts:
    if item['repository_count'] is None:continue
    assert item['repository_count']==len({x['repo'].lower() for x in item['repositories']})
    assert item['file_count']==len({(r['repo'].lower(),m['path']) for r in item['repositories'] for m in r['matches']})
print('PASS: all tables align, obsolete metrics absent, active sort columns correct, three-state results, all-type defaults, adoption counts traceable')
