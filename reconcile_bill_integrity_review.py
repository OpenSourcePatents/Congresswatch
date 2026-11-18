#!/usr/bin/env python3
"""Pinned offline review-only source adoption. Never fetches, pushes or deploys."""
import json
import subprocess
from pathlib import Path
from fetch_bills.utils.text_processor import text_contamination_reason
from fetch_bills.utils.donor_matcher import score_donor_alignment

BASE = 'f05f766fe3cfdb0526690e6daf26b41a1c440378'
SOURCE = '506ac59adf260d987646832cdcff7d8a9b762225'
COMMITS = [
 ('5c4f4f3a3bbf347c4271c71475757dc9a1e2b82d', {'trades','trade_count','latest_trade_date','trades_updated'}),
 ('cb6a6f65d56446ebfe1139fbef09295b3d0ff5ed', {'travel','travel_count','travel_updated'}),
 ('06455060e90c8ef686bb1fc40c2bee292f8c4b29', {'trades','trade_count','latest_trade_date','trades_updated'}),
 (SOURCE, {'bills','bills_updated','donor_alignment_score','donor_match_count'}),
]
FIELDS = ('keywords','has_text','analysis_status','similarity_score','alec_best_similarity',
          'match_type','alec_match','similar_member_bills','donor_interest')

def read(ref,path):
    return json.loads(subprocess.check_output(['git','show',f'{ref}:{path}']))
def save(path,value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')

def main():
    # Start from immutable review evidence, so a rerun cannot stack transforms.
    members=read(BASE,'data/members.json')
    by_id={m['id']:m for m in members}
    details={str(p):read(BASE,str(p)) for p in Path('data/details').glob('*.json')}
    for commit,allowed in COMMITS:
        paths=subprocess.check_output(['git','diff','--name-only',commit+'^',commit]).decode().splitlines()
        for p in paths:
            if not p.startswith('data/details/') or p not in details:continue
            before,after=read(commit+'^',p),read(commit,p)
            changed={k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
            assert changed<=allowed,(commit,p,changed-allowed)
            for k in changed:
                if k in after:details[p][k]=after[k]
                else:details[p].pop(k,None)
            bid=Path(p).stem
            for k in ('trade_count','latest_trade_date','travel_count'):
                if k in changed and bid in by_id:by_id[bid][k]=after[k]
    counts={'bill_rows':0,'polluted_rows':0,'polluted_members':set()}
    for p,d in details.items():
        rows=d.get('bills',[])
        for b in rows:
            if b.get('analysis_version')=='bill-text-integrity-v1':continue
            counts['bill_rows']+=1
            reason=text_contamination_reason(' '.join(b.get('keywords',[])),cleaned=True)
            b['analysis_provenance']={
                'source_snapshot':SOURCE,'review_base':BASE,
                'reason':'Legacy similarity corpus contains PDF syntax; clean-corpus refit required',
                'original_fields':{k:b[k] for k in FIELDS if k in b},
            }
            b.update(similarity_score=None,alec_best_similarity=0.0,match_type=None,
                     alec_match=None,similar_member_bills=[],analysis_status='quarantined_legacy_corpus')
            if reason:
                counts['polluted_rows']+=1;counts['polluted_members'].add(Path(p).stem)
                b.update(text_integrity='quarantined',text_integrity_reason=reason,
                         analysis_status='quarantined_pdf_text',has_text=False,keywords=[],
                         donor_interest={'match':False,'matched_industries':[],'keyword_hits':{},'analysis_status':'quarantined_pdf_text'})
        d['bills_analysis_status']='quarantined_legacy_corpus'
        d['alec_match_count']=sum(bool(b.get('alec_match')) for b in rows)
        d['donor_match_count']=sum(bool((b.get('donor_interest') or {}).get('match')) for b in rows)
        d['donor_alignment_score']=score_donor_alignment(rows)
        save(p,d)
    save('data/members.json',members)
    counts['polluted_members']=len(counts['polluted_members'])
    print(json.dumps(counts))
if __name__=='__main__':main()
