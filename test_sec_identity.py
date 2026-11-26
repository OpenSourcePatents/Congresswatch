"""Offline regressions for SEC identity, retrieval, scoring and preserved PTR data."""
import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import fetch_finance as finance
from sec_identity import eligible_sec_signals, retrieve_identity_evidence
from validation.scrub_sec_outputs import scrub_record


def verified():
    cik = '0001234567'
    url = 'https://example.org/reviewed-member-identity'
    return dict(id='X000001', name='Fixture Member', edgar_identity_member_id='X000001',
                corporate_insider_signals=6, edgar_cik=cik,
                edgar_identity_cik=cik, edgar_identity_status='verified',
                edgar_data_status='available', edgar_status='verified_manual',
                edgar_identity_evidence=[url], edgar_identity_evidence_checks=[dict(
                    url=url, cik=cik, status='available', resolved_url=url,
                    checked_at='2026-11-26T12:00:00Z', sha256='a'*64)])


def negative_cases():
    changes = [
        ('automatic', {'edgar_identity_status':'unverified','edgar_status':'verified_auto'}),
        ('bare-manual', {'edgar_identity_status':None}),
        ('missing-member', {'edgar_identity_member_id':None}),
        ('wrong-member', {'edgar_identity_member_id':'Y000002'}),
        ('missing-cik', {'edgar_cik':None}),
        ('wrong-cik', {'edgar_cik':'0007654321'}),
        ('missing-identity-cik', {'edgar_identity_cik':None}),
        ('missing-evidence', {'edgar_identity_evidence':[]}),
        ('http-evidence', {'edgar_identity_evidence':['http://example.org/evidence']}),
        ('malformed-evidence', {'edgar_identity_evidence':['https://[']}),
        ('missing-receipt', {'edgar_identity_evidence_checks':[]}),
        ('failed-sec', {'edgar_status':'query_failed'}),
        ('stale-sec', {'edgar_data_status':'stale'}),
        ('missing-fetch', {'edgar_data_status':None}),
        ('negative-count', {'corporate_insider_signals':-1}),
        ('string-count', {'corporate_insider_signals':'6'}),
        ('bool-count', {'corporate_insider_signals':True}),
        ('fraction-count', {'corporate_insider_signals':1.5}),
    ]
    cases=[]
    for name,change in changes:
        m=verified();m.update(change);cases.append((name,m))
    for key,value in [('cik','0007654321'),('url','https://example.org/unrelated'),
                      ('status','unavailable'),('resolved_url','http://example.org/evidence'),
                      ('checked_at',''),('sha256','not-a-hash')]:
        m=verified();m['edgar_identity_evidence_checks'][0][key]=value
        cases.append(('receipt-'+key,m))
    m=verified();m['edgar_identity_evidence_checks']*=2;cases.append(('extra-receipt',m))
    m=verified();m['edgar_identity_evidence']*=2;m['edgar_identity_evidence_checks']*=2
    cases.append(('duplicate-url',m))
    m=verified();url2='https://example.org/second';m['edgar_identity_evidence'].append(url2)
    check=copy.deepcopy(m['edgar_identity_evidence_checks'][0]);check.update(url=url2,resolved_url=url2)
    m['edgar_identity_evidence_checks'].append(check);m['edgar_identity_evidence_checks'].reverse()
    cases.append(('reordered-receipts',m))
    return cases


class SecIdentityTests(unittest.TestCase):
    def test_verified_positive_scores_and_flags(self):
        m=verified()
        self.assertEqual(eligible_sec_signals(m),6)
        self.assertEqual(finance.compute_score_components(m)['trade_timing'],25)
        finance.update_flags(m);self.assertIn('trade',m['flags'])

    def test_all_missing_mismatched_and_failed_paths(self):
        for name,m in negative_cases():
            with self.subTest(name=name):
                self.assertEqual(eligible_sec_signals(m),0)
                self.assertEqual(finance.compute_score_components(m)['trade_timing'],0)
                finance.update_flags(m);self.assertNotIn('trade',m['flags'])

    def test_current_wrong_owners_and_historical_regressions(self):
        cases=json.loads(Path('validation/sec_wrong_owners.json').read_text())['cases']
        self.assertEqual(len(cases),9)
        for case in cases:
            with self.subTest(member=case['member_name']):
                m=case['original']
                self.assertEqual(case['status'],200)
                self.assertNotEqual(case['name'],case['member_name'])
                self.assertEqual(eligible_sec_signals(m),0)
                self.assertEqual(finance.compute_score_components(m)['trade_timing'],0)
                revised=scrub_record(m,m)
                self.assertNotIn('trade',revised['flags'])
                self.assertEqual(revised['corporate_insider_signals'],m['corporate_insider_signals'])

    def test_real_ptr_trades_and_other_components_survive(self):
        m=verified();m['edgar_identity_status']='unverified'
        m['trades']=[{'transaction_date':datetime.now().strftime('%Y-%m-%d'),'ticker':'REAL'}]*20
        m['score_components']={'trade_timing':25,'wealth_gap':10,'donor_alignment':4,
                               'alec_similarity':8,'foreign_travel':3,'attendance':1}
        m['flags']=['trade','donor'];m['score']=51
        out=scrub_record(m,m)
        self.assertEqual(out['score_components']['trade_timing'],15)
        self.assertEqual(out['trades'],m['trades'])
        self.assertEqual(out['flags'],['donor'])
        for k,v in m['score_components'].items():
            if k!='trade_timing': self.assertEqual(out['score_components'][k],v)
        self.assertEqual(scrub_record(out,out),out)

    def test_fresh_ptr_without_sec_does_not_expand_this_correction(self):
        m=verified();m['corporate_insider_signals']=0;m['flags']=[]
        m['trades']=[{'transaction_date':datetime.now().strftime('%Y-%m-%d')}]*20
        m['score_components']={'trade_timing':0,'wealth_gap':10,'donor_alignment':4,
                               'alec_similarity':8,'foreign_travel':3,'attendance':1}
        self.assertEqual(scrub_record(m,m)['score_components']['trade_timing'],0)

    @patch('sec_identity.requests.get')
    def test_evidence_retrieval_success_and_unavailable(self,get):
        response=Mock(status_code=200,headers={});response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False);response.iter_content.return_value=[b'identity document']
        get.return_value=response
        m=verified();m['edgar_identity_evidence_checks']=retrieve_identity_evidence(m['edgar_cik'],m['edgar_identity_evidence'],{})
        self.assertEqual(eligible_sec_signals(m),6)
        get.side_effect=requests.HTTPError('403 Forbidden')
        m['edgar_identity_evidence_checks']=retrieve_identity_evidence(m['edgar_cik'],m['edgar_identity_evidence'],{})
        self.assertEqual(eligible_sec_signals(m),0)
        self.assertEqual(get.call_count,2)  # no access-control workaround/retry

    @patch('sec_identity.requests.get')
    def test_redirect_downgrade_and_empty_evidence_fail(self,get):
        response=Mock(status_code=302,headers={'Location':'http://example.org/insecure'})
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        get.return_value=response
        checks=retrieve_identity_evidence('0001234567',['https://example.org/start'],{})
        self.assertEqual(checks[0]['status'],'unavailable');self.assertEqual(get.call_count,1)
        response.status_code=200;response.iter_content.return_value=[]
        self.assertEqual(retrieve_identity_evidence('0001234567',['https://example.org/start'],{})[0]['status'],'unavailable')

    @patch.object(finance,'load_json')
    @patch.object(finance,'retrieve_identity_evidence')
    @patch.object(finance,'sec_search')
    def test_explicit_manual_assertion_and_fetch(self,search,retrieve,load):
        m=verified();load.return_value={m['id']:{'bioguide_id':m['id'],'identity_status':'verified',
            'cik':m['edgar_cik'],'evidence':m['edgar_identity_evidence']}}
        retrieve.return_value=m['edgar_identity_evidence_checks']
        search.return_value={'hits':{'total':{'value':6}}}
        count=finance.fetch_edgar_signals(m);m['corporate_insider_signals']=count
        self.assertEqual(count,6);self.assertEqual(eligible_sec_signals(m),6)
        search.side_effect=requests.HTTPError('500')
        prior=(m['edgar_cik'],m['corporate_insider_signals'],m['edgar_updated'])
        self.assertIsNone(finance.fetch_edgar_signals(m))
        self.assertEqual((m['edgar_cik'],m['corporate_insider_signals'],m['edgar_updated']),prior)
        self.assertEqual(eligible_sec_signals(m),0)

    @patch.object(finance,'load_json',return_value={})
    @patch.object(finance,'save_json')
    @patch.object(finance,'sec_search')
    def test_auto_and_partial_aliases_never_verify(self,search,save,load):
        m={'id':'X000001','name':'Fixture Member'}
        search.return_value={'hits':{'hits':[{'_source':{'ciks':['0001234567']}}]*3}}
        self.assertEqual(finance.resolve_member_cik(m)['status'],'unverified_auto')
        self.assertEqual(m['edgar_identity_status'],'unverified')
        search.side_effect=[requests.HTTPError('500'),search.return_value,search.return_value]
        self.assertEqual(finance.resolve_member_cik(m)['status'],'query_failed')

    @patch.object(finance,'resolve_member_cik',return_value={'status':'verified_manual','cik':'0007654321'})
    @patch.object(finance,'sec_search',side_effect=requests.HTTPError('500'))
    def test_failed_new_cik_preserves_old_pair(self,search,resolve):
        m=verified();self.assertIsNone(finance.fetch_edgar_signals(m))
        self.assertEqual(m['edgar_cik'],'0001234567');self.assertEqual(m['corporate_insider_signals'],6)
        self.assertEqual(eligible_sec_signals(m),0)

    @patch.object(finance,'resolve_member_cik',return_value={'status':'verified_manual','cik':'0001234567'})
    @patch.object(finance,'sec_search',return_value={'hits':{'hits':[]}})
    def test_malformed_sec_result_is_not_successful_zero(self,search,resolve):
        m=verified();self.assertIsNone(finance.fetch_edgar_signals(m))
        self.assertEqual(eligible_sec_signals(m),0)


if __name__=='__main__':
    unittest.main()
