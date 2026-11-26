"""Exercise the shipped page through search/cards/tabs, plus shared gate fixtures."""
import functools
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_sec_identity import verified, negative_cases


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass


def main():
    artifacts=ROOT/'validation/artifacts';artifacts.mkdir(exist_ok=True)
    cases=json.loads((ROOT/'validation/sec_wrong_owners.json').read_text())['cases']
    members=json.loads((ROOT/'data/members.json').read_text())
    by_id={m['id']:m for m in members}
    # Independently changed donor/PTR scores must survive the review reconciliation.
    profile_cases=cases+[{'id':'B001278'},{'id':'O000177'}]
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(QuietHandler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}'
    errors=[];results=[];fixture_count=0
    try:
        with sync_playwright() as p:
            options={'headless':True,'args':['--no-sandbox']}
            if os.environ.get('CHROMIUM_EXECUTABLE'):
                options['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
            browser=p.chromium.launch(**options)
            for width,height in [(1365,900),(390,844)]:
                page=browser.new_page(viewport={'width':width,'height':height})
                page.on('pageerror',lambda e:errors.append(str(e)))
                # Remote images/fonts/analytics do not affect SEC behavior; no production writes.
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) else route.abort())
                page.goto(origin,wait_until='domcontentloaded')
                page.wait_for_function('allMembers.length === 539')
                for case in profile_cases:
                    bid=case['id'];m=by_id[bid]
                    if page.locator('#profile-view.active').count():
                        page.locator('.back-btn').click()
                    page.locator('#cw-search').fill(m['name'])
                    card=page.locator('.m-card').filter(has=page.get_by_text(m['name'],exact=True)).first
                    card.wait_for();assert 'INSIDER' not in card.inner_text()
                    card.click()
                    page.wait_for_function('(bid) => detailCache.has(bid)',arg=bid)
                    assert page.locator('#pf-score-big').inner_text()==str(m['score'])
                    for panel,selector in [('overview','#ppanel-overview'),('stocks','#pf-stocks-content'),('patterns','#pf-patterns-content')]:
                        page.locator(f'.ptab[data-panel="{panel}"]').click()
                        text=page.locator(selector).inner_text()
                        assert 'CORPORATE INSIDER ACTIVITY' not in text,(bid,panel,text)
                        assert 'CORPORATE INSIDER SIGNALS DETECTED' not in text,(bid,panel,text)
                        assert 'No stock trades or corporate insider signals found' not in text
                    if bid in ['L000566','M001169']:
                        page.locator('.ptab[data-panel="stocks"]').click()
                        page.screenshot(path=str(artifacts/f'{bid}-{width}.png'),full_page=True)
                    results.append({'id':bid,'width':width,'score':m['score'],'panels':3})
                for name,m in [('verified',verified()),*negative_cases()]:
                    accepted=name=='verified'
                    value=page.evaluate('m=>eligibleSecSignals(m)',m)
                    assert value==accepted,(name,value)
                    observed=page.evaluate('''m=>{
                        renderOverview(m,0,0);fillStocks(m,0);fillPatterns(m,0);
                        return ['ppanel-overview','pf-stocks-content','pf-patterns-content'].map(
                          id=>document.getElementById(id).textContent.includes('CORPORATE INSIDER'));
                    }''',m)
                    assert observed==[accepted]*3,(name,observed)
                    fixture_count+=1
                # A stale vault cannot inherit verified identity or receipts from the roster.
                m=verified();deep={'id':m['id'],'edgar_cik':m['edgar_cik'],'corporate_insider_signals':6}
                assert not page.evaluate('([m,d])=>eligibleSecSignals(mergeMemberDetail(m,d))',[m,deep])
                # The old production snapshot must not retain a trusted score or breakdown.
                for case in cases:
                    assert page.evaluate('m=>computeScore(m)',case['original']) is None
                # Genuine PTR rows remain visible even when SEC identity fails.
                m=verified();m['edgar_identity_status']='unverified';m['trades']=[{'ticker':'REALPTR','transaction_date':'2026-11-25','type':'purchase','amount':'$1,001 - $15,000','owner':'Self'}]
                page.evaluate('m=>fillStocks(m,0)',m)
                assert 'REALPTR' in page.locator('#pf-stocks-content').inner_text()
                assert not errors,errors
                page.close()
            browser.close()
    finally:
        server.shutdown();server.server_close()
    result={'entry':'index.html -> search -> member card -> Overview/Stocks/Patterns',
            'profile_checks':results,'fixture_panel_cases':fixture_count,'javascript_errors':errors,
            'external_assets':'blocked in test; not needed for SEC behavior'}
    (artifacts/'browser-sec.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'profiles':len(results),'panels':len(results)*3,'fixture_panel_cases':fixture_count,'javascript_errors':errors}))


if __name__=='__main__':
    main()
