import pytest
from harness.web_fetch.native_projection import native_fetch_diagnostics

URL='https://example.test/page'
def event(status,url=URL):
    return f'DEBUG fetch request complete method=GET url={url} final_url={url} status={status}\n'.encode()
TURN=b'DEBUG completed one ordinary Page turn source="wake" page_id=1 readiness=Some(Idle)\n'
CAPTURE=b'DEBUG capturing page state while a location navigation is pending\n'
CLOSE=b'DEBUG closing renderer page handle page_id=1 requested_execution_interrupt=true\n'
COMMIT=b'DEBUG detached committed main WindowProxy for replacement context page_id=1\n'

@pytest.mark.parametrize('status',[200,403,418])
def test_exact_target_status_excludes_other_resources(status):
    assert native_fetch_diagnostics(event(status)+event(500,URL+'/asset.js'),URL)==(status,False)

def test_late_navigation_cannot_replace_the_bound_captured_document():
    log=event(418)+TURN+CAPTURE+CLOSE+event(200,URL+'?challenge=1')+COMMIT
    assert native_fetch_diagnostics(log,URL)==(418,False)

@pytest.mark.parametrize('middle',[CAPTURE+CLOSE,TURN+CAPTURE,TURN+CAPTURE+CLOSE.replace(b'page_id=1',b'page_id=2'),TURN+CAPTURE+CLOSE+CAPTURE])
def test_incomplete_or_ambiguous_capture_ownership_is_not_inferred(middle):
    assert native_fetch_diagnostics(event(418)+middle+COMMIT,URL)==(None,False)

@pytest.mark.parametrize('log',[event(403)+event(200),event(302)+event(200,URL+'?next=1'),event(200)+COMMIT,event(403,URL+'/other')])
def test_ambiguous_response_or_navigation_does_not_supply_status(log):
    assert native_fetch_diagnostics(log,URL)==(None,False)


@pytest.mark.parametrize('requested,observed,page_matches,log_status', [
    (URL, URL, True, 200),
    (URL, URL+'?utm_source=newsletter', True, None),
    (URL, URL+'?tracking=1', False, None),
    (URL+'?page=1', URL+'?page=1', True, 200),
    (URL+'?page=1', URL+'?page=2', False, None),
])
def test_page_acceptance_and_request_log_ownership_have_distinct_query_contracts(
        requested, observed, page_matches, log_status):
    from lexbench_webfetch.scoring import _same_page_url
    assert _same_page_url(observed, requested) is page_matches
    assert native_fetch_diagnostics(event(200, observed), requested) == (log_status, False)
