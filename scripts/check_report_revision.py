"""Acceptance checks for compact metrics, filters and provider-centred adoption."""
import json,sys
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'reports/webfetch'
for path in out.rglob('*.html'):
    soup=BeautifulSoup(path.read_text(),'html.parser')
    for pre in soup.select('pre'):pre.decompose()
    assert '条件通过率' not in soup.get_text() and '条件达标率' not in soup.get_text(),path
    for table in soup.select('table'):
        width=len(table.select('thead th'))
        assert all(len(row.find_all('td',recursive=False))==width for row in table.select('tbody > tr')),(path,width)
    for table in soup.select('table.ranking'):
        headers=table.select('thead th')
        sort=int(table['data-col'])
        expected='命中仓库数' if path.parent.name=='adoption' else '任务通过率'
        assert expected in headers[sort].get_text(),(path,sort)
        assert table.parent.find_previous_sibling('div',class_='metric-help'),path
    track=soup.select_one('select[data-filter="track"]')
    if track:assert track.select('option')[0]['value']=='',path
    if path.parent.name in {'providers','analysis'}:
        assert not soup.select('select[data-filter="transport"],select[data-filter="content"],select[data-filter="resources"]'),path
        assert [x.get_text() for x in soup.select('select[data-filter="result"] option')]==['任务通过','任务失败','全部'],path
        assert soup.select_one('select[data-filter="result"] option[selected]')['value']=='fail',path
        assert '失败分类' in [h.get_text() for h in soup.select('#filtered-table th')],path
counts=json.loads((out/'adoption/counts.json').read_text())['methods']
names={json.loads(line)['provider'] for line in (out/'results.jsonl').read_text().splitlines()}
assert {item['provider'] for item in counts}==names
interfaces=BeautifulSoup((out/'adoption/index.html').read_text(),'html.parser').select('.interface-panel[id^="interface-"]')
sys.path.insert(0,str(ROOT))
from harness.web_fetch.report_interfaces import SPECS
assert len(interfaces)==len(names & SPECS.keys()) and sum(p.has_attr('open') for p in interfaces)==bool(interfaces)
for panel in interfaces:
    assert len(panel.select('pre'))==2
for item in counts:
    if item['repository_count'] is None:continue
    assert item['repository_count']==len({x['repo'].lower() for x in item['repositories']})
    assert item['file_count']==len({(r['repo'].lower(),m['path']) for r in item['repositories'] for m in r['matches']})
print('PASS: all tables align, obsolete metrics absent, active sort columns correct, three-state results, all-type defaults, adoption counts traceable')
