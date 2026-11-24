"""Generate cross-runtime acceptance cases from the actual site scoring functions."""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_finance import compute_score_components, eligible_sec_signals

URLS = ['https://www.sec.gov/identity/one', 'https://www.sec.gov/identity/two']
BASE = dict(id='A000001', name='Fixture Member', chamber='House', state='Florida',
            score=0, flags=['donor', 'custom', 'trade'], total_raised=0,
            edgar_identity_status='verified', edgar_cik='0000000001',
            edgar_identity_cik='0000000001', edgar_identity_evidence=URLS,
            edgar_identity_evidence_checks=[dict(url=u,cik='0000000001',status='available',
                resolved_url=u,checked_at='2026-11-24T00:00:00Z',sha256='a'*64) for u in URLS],
            edgar_data_status='available',edgar_status='verified_manual',corporate_insider_signals=6)


def cases():
    rows = []
    def add(name, changes=None, detail=None):
        member = copy.deepcopy(BASE)
        member.update(copy.deepcopy(changes or {}))
        detail = copy.deepcopy(detail or {})
        eligible = eligible_sec_signals(member)
        components = compute_score_components(member, detail)
        flags = [f for f in member['flags'] if f != 'trade'] + (['trade'] if eligible > 5 else [])
        rows.append(dict(name=name,member=member,detail=detail,expected=dict(
            components=components,total=sum(components.values()),flags=flags,eligible_count=eligible)))
    add('verified ordered receipts positive')
    for n in (0,1,2,3,5,6): add(f'eligible count {n}',{'corporate_insider_signals':n,'flags':[]})
    for key,value in [('edgar_identity_status','unverified'),('edgar_identity_status','verified_auto'),
                      ('edgar_cik',None),('edgar_identity_cik',None),('edgar_identity_cik','0000000002'),
                      ('edgar_identity_evidence',[]),('edgar_identity_evidence',['https://']),
                      ('edgar_identity_evidence',['http://example.com']),
                      ('edgar_identity_evidence',['https://user:password@example.com']),
                      ('edgar_identity_evidence_checks',[]),('edgar_identity_evidence_checks',None),
                      ('edgar_data_status','stale'),('edgar_data_status','unavailable'),
                      ('edgar_status','query_failed'),('corporate_insider_signals',-1),
                      ('corporate_insider_signals',True),('corporate_insider_signals','6')]:
        add(f'ineligible {key}={value}',{key:value})
    for key,value in [('url',URLS[1]),('cik','0000000002'),('status','unavailable'),
                      ('resolved_url','http://example.com'),('resolved_url','https://'),
                      ('checked_at',''),('checked_at',None),('sha256',''),('sha256','A'*64)]:
        checks=copy.deepcopy(BASE['edgar_identity_evidence_checks']);checks[0][key]=value
        add(f'ineligible receipt {key}={value}',{'edgar_identity_evidence_checks':checks})
    checks=BASE['edgar_identity_evidence_checks']
    for name,cs in [('reordered',list(reversed(checks))),('missing',checks[:1]),
                    ('extra',checks+checks[:1]),('duplicate',[checks[0],checks[0]])]:
        add('ineligible receipts '+name,{'edgar_identity_evidence_checks':cs})
    inactive={'edgar_identity_status':'unverified'}
    date=(datetime.now()-timedelta(days=10)).strftime('%Y-%m-%d')
    legacy=(datetime.now()-timedelta(days=10)).strftime('%m/%d/%Y')
    for role in ('member','staff','unverified',None):
        for count in (1,3,5):
            trip={'departure_date':date}
            if role is not None:trip['filer_type']=role
            add(f'travel {role} x{count}',inactive,{'travel':[trip]*count})
    add('unlabeled historical serialization',inactive,{'travel':[{'departure_date':legacy,'total_cost':'$1,234.50','raw':{'keep':True}}]})
    add('mixed travel',inactive,{'travel':[{'departure_date':date,'filer_type':r} for r in ('staff','unverified','member')]})
    for name,date in [('old','2001-01-01'),('future','2099-01-01'),('missing','')]:
        add('travel '+name,inactive,{'travel':[{'departure_date':date}]})
    # These intentionally satisfy the old API's dormant donor/model gates.
    hypothetical={'bills_analysis_version':'official-prose-overlap-v2',
      'donor_alignment_analysis_version':'official-prose-overlap-v2',
      'donor_alignment_status':'available','donor_alignment_score':100,
      'bills':[{'analysis_version':'official-prose-overlap-v2','analysis_status':'analyzed',
        'model_analysis_status':'analyzed','text_source':{'source_format':'govinfo_official_bill_xml',
        'extraction_version':'govinfo-legislative-body-v2','source_url':'https://www.govinfo.gov/example',
        'source_sha256':'a'*64,'text_sha256':'b'*64},'alec_match':{'similarity_score':1,
        'evidence':{'target_text_sha256':'b'*64,'matched_source_sha256':'c'*64,
        'matched_text_sha256':'d'*64,'longest_passage_words':100,
        'target_shingle_coverage':1,'matched_shingle_coverage':1}}}]}
    add('dormant donor and model remain suspended',inactive,hypothetical)
    add('campaign and PAC never wealth',dict(inactive,total_raised=1e12,pac_contributions=1e12),hypothetical)
    return rows


if __name__ == '__main__':
    print(json.dumps({'site_predicates':['fetch_finance.compute_score_components','fetch_finance.eligible_sec_signals',
        'recompute_scores flag rule'],'cases':cases()},indent=2))
