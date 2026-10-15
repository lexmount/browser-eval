import pytest
from harness.web_fetch.report_check import require_public_evidence_page


@pytest.fixture
def specimen():
    answer = {'original_sha256': 'a'*64, 'published_sha256': 'b'*64,
              'path': 'public-evidence/answer.txt', 'receipt': 'public-evidence/answer-receipt.json'}
    native = {'path': 'public-evidence/native.json', 'receipt': 'public-evidence/native-receipt.json'}
    return {'scored_answer': answer, 'native_response': native}


def page(views, prefix='../'):
    answer = views['scored_answer']
    links = ''.join('<a href="'+prefix+v+'">Public download</a>'
                    for item in views.values() for key,v in item.items() if key in {'path','receipt'})
    return '<html><body><p>'+answer['original_sha256']+' '+answer['published_sha256']+'</p>'+links+'</body></html>'


@pytest.mark.parametrize('relative,prefix', [('attempts/p.html','../'),('en/attempts/p.html','../../')])
def test_public_evidence_links_work_in_both_editions(specimen,relative,prefix):
    require_public_evidence_page(page(specimen,prefix),relative,specimen)


@pytest.mark.parametrize('defect', ['original_hash','public_hash','native_link','receipt_link','escaped_markup'])
def test_release_rejects_missing_identity_or_download_evidence(specimen,defect):
    html = page(specimen)
    if defect == 'original_hash': html = html.replace('a'*64, 'wrong original')
    if defect == 'public_hash': html = html.replace('b'*64, 'wrong public')
    if defect == 'native_link': html = html.replace('../public-evidence/native.json','../unrelated.json')
    if defect == 'receipt_link': html = html.replace('../public-evidence/answer-receipt.json','../unrelated.json')
    if defect == 'escaped_markup': html = html.replace('Public download','&lt;span data-report-en="Download"&gt;Download&lt;/span&gt;',1)
    # Payload text cannot stand in for a working reader-facing evidence link.
    html += '<pre>'+('a'*64)+' '+('b'*64)+' public-evidence/native.json</pre>'
    with pytest.raises(ValueError):require_public_evidence_page(html,'attempts/p.html',specimen)
