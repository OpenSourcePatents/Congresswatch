#!/usr/bin/env python3
"""One-shot offline main reconciliation; immutable snapshots, no network/writes to refs."""
import json
import subprocess
from pathlib import Path
from fetch_finance import annotate_sec_identity

REVIEW='04a1cb005e68f5a5e9ffeb493d8f97ebc1c1db04'
MAIN='506ac59adf260d987646832cdcff7d8a9b762225'
FAILED_SEC = {'R000621': '2026-11-18T13:47:23.6732258Z', 'M001239': '2026-11-18T13:47:23.6733939Z', 'G000603': '2026-11-18T13:47:23.6736855Z', 'M001228': '2026-11-18T13:47:23.6738452Z', 'M001213': '2026-11-18T13:47:23.6743976Z', 'T000165': '2026-11-18T13:47:23.6747093Z', 'J000298': '2026-11-18T13:47:23.6750308Z', 'M001160': '2026-11-18T13:47:23.6756815Z', 'C001063': '2026-11-18T13:47:23.6757814Z', 'C001051': '2026-11-18T13:47:23.6758814Z', 'B001327': '2026-11-18T13:47:23.6763606Z', 'B001326': '2026-11-18T13:47:23.6764704Z', 'M001222': '2026-11-18T13:55:28.8665840Z', 'L000602': '2026-11-18T13:55:28.8667717Z', 'B001313': '2026-11-18T13:55:28.8686881Z', 'E000071': '2026-11-18T13:55:28.8687901Z', 'P000048': '2026-11-18T13:55:28.8689269Z', 'C001120': '2026-11-18T13:55:28.8692623Z', 'J000301': '2026-11-18T13:55:28.8694160Z', 'S001205': '2026-11-18T13:55:28.8697802Z', 'B001306': '2026-11-18T13:55:28.8698865Z', 'E000296': '2026-11-18T13:55:28.8701426Z', 'F000459': '2026-11-18T13:55:28.8705525Z', 'K000376': '2026-11-18T13:55:28.8706670Z', 'N000002': '2026-11-18T13:55:28.8709928Z', 'M001157': '2026-11-18T13:55:28.8711349Z', 'M001240': '2026-11-18T13:55:28.8715063Z', 'C001136': '2026-11-18T14:02:02.7508694Z', 'S001201': '2026-11-18T14:02:02.7522203Z', 'R000603': '2026-11-18T14:02:02.7524865Z', 'Z000018': '2026-11-18T14:02:02.7526798Z', 'D000624': '2026-11-18T14:02:02.7528841Z', 'N000188': '2026-11-18T14:02:02.7530529Z', 'A000370': '2026-11-18T14:02:02.7532333Z', 'W000812': '2026-11-18T14:02:02.7533920Z', 'S001195': '2026-11-18T14:02:02.7534906Z', 'H001052': '2026-11-18T14:02:02.7536678Z', 'K000375': '2026-11-18T14:02:02.7537707Z', 'S001172': '2026-11-18T14:02:02.7539455Z', 'W000798': '2026-11-18T14:02:02.7540450Z', 'P000597': '2026-11-18T14:02:02.7541748Z', 'P000034': '2026-11-18T14:10:54.2931895Z', 'T000193': '2026-11-18T14:10:54.2935693Z', 'S000522': '2026-11-18T14:10:54.2937026Z', 'F000485': '2026-11-18T14:10:54.2940284Z', 'R000617': '2026-11-18T14:10:54.2983334Z', 'S000168': '2026-11-18T14:10:54.2989793Z', 'M001215': '2026-11-18T14:10:54.2990895Z', 'K000391': '2026-11-18T14:10:54.2997712Z', 'C001108': '2026-11-18T14:10:54.2999094Z', 'R000395': '2026-11-18T14:10:54.3007068Z', 'B000490': '2026-11-18T14:10:54.3008699Z', 'P000622': '2026-11-18T14:10:54.3011469Z', 'W000830': '2026-11-18T14:18:52.0871031Z', 'T000491': '2026-11-18T14:18:52.0872424Z', 'S001231': '2026-11-18T14:18:52.0873463Z', 'L000596': '2026-11-18T14:18:52.0878956Z', 'K000401': '2026-11-18T14:18:52.0880203Z', 'F000472': '2026-11-18T14:18:52.0882860Z', 'S001200': '2026-11-18T14:18:52.0886915Z', 'K000389': '2026-11-18T14:18:52.0890077Z', 'L000582': '2026-11-18T14:18:52.0891991Z', 'V000129': '2026-11-18T14:18:52.0895655Z', 'C001069': '2026-11-18T14:18:52.0899549Z', 'H001047': '2026-11-18T14:18:52.0900630Z', 'N000147': '2026-11-18T14:18:52.0903840Z', 'H001072': '2026-11-18T14:28:08.6410169Z', 'R000600': '2026-11-18T14:28:08.6412097Z', 'W000805': '2026-11-18T14:28:08.6418639Z', 'L000570': '2026-11-18T14:28:08.6429521Z', 'K000367': '2026-11-18T14:28:08.6432680Z', 'L000577': '2026-11-18T14:28:08.6433781Z', 'L000575': '2026-11-18T14:28:08.6434816Z', 'F000479': '2026-11-18T14:28:08.6439355Z', 'G000574': '2026-11-18T14:28:08.6440696Z', 'B001299': '2026-11-18T14:30:03.7292863Z', 'B001243': '2026-11-18T14:30:03.7294778Z'}
RAW=('edgar_cik','corporate_insider_signals','edgar_status','data_updated')

def read(ref,path):return json.loads(subprocess.check_output(['git','show',ref+':'+path]))
def save(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def main():
    members=read(REVIEW,'data/members.json');latest={m['id']:m for m in read(MAIN,'data/members.json')}
    assert set(latest)=={m['id'] for m in members}, 'Roster membership changed: needs deliberate reconciliation'
    for m in members:
        bid=m['id'];p=f'data/details/{bid}.json';old=read(REVIEW,p);incoming=read(MAIN,p);fresh=latest[bid]
        # Every newest original record must already be represented by the bill
        # quarantine; compare using archived original fields, never bill-ID maps.
        assert len(old.get('bills',[]))==len(incoming.get('bills',[]))
        for a,b in zip(old.get('bills',[]),incoming.get('bills',[])):
            reconstructed={**a,**a.get('analysis_provenance',{}).get('original_fields',{})}
            assert all(reconstructed.get(k)==v for k,v in b.items()), (bid,'bill source differs')
        for k in ('trades','travel','votes','top_donors_list'):
            assert old.get(k)==incoming.get(k),(bid,k,'source array drift; do not overwrite')
        for k in ('name','party','state','district','chamber','photo_url','term_start',
                  'total_raised','pac_contributions','individual_contributions','total_spent','cash_on_hand',
                  'fec_candidate_id','fec_cycle','data_updated'):
            if k in fresh:m[k]=fresh[k]
            if k in incoming:old[k]=incoming[k]
        # Preserve both original source views: legacy null merging can make
        # roster CIK/count disagree with its detail record.
        evidence={'source_snapshot':MAIN,'review_snapshot':REVIEW,'run':44315900065,
                  'prior_roster':{k:m.get(k) for k in RAW if k!='data_updated'},
                  'observed_roster':{k:fresh.get(k) for k in RAW},
                  'observed_detail':{k:incoming.get(k) for k in RAW},
                  'request_failed':bid in FAILED_SEC}
        for record in (m,old):
            record['edgar_reconciliation']=evidence
            record['edgar_checked']=FAILED_SEC.get(bid,fresh.get('data_updated'))
            if bid in FAILED_SEC:
                record['edgar_status']='query_failed'
                record['edgar_data_status']='stale' if record.get('edgar_cik') and record.get('corporate_insider_signals') is not None else 'unavailable'
            else:
                record['edgar_cik']=fresh.get('edgar_cik')
                record['corporate_insider_signals']=fresh.get('corporate_insider_signals')
                record['edgar_status']=fresh.get('edgar_status','unresolved')
                record['edgar_data_status']='available' if fresh.get('edgar_cik') else 'unresolved'
                record['edgar_updated']=fresh.get('data_updated')
            annotate_sec_identity(record)
        # Main's votes_updated is a failed-attempt timestamp, not a success.
        assert incoming.get('votes_status')=='fetch_failed',(bid,'unexpected vote outcome')
        old['votes_refresh_provenance']={'source_snapshot':MAIN,'run':44276045061,
            'legacy_review_votes_updated':old.get('votes_updated'),
            'legacy_source_votes_updated':incoming.get('votes_updated'),
            'note':'Both legacy timestamps came from failed attempts; successful collection time unknown.'}
        old['votes_checked']=incoming.get('votes_updated')
        old['votes_updated']=None
        old['votes_status']='fetch_failed'
        old['votes_fail_count']=incoming.get('votes_fail_count')
        save(p,old)
    save('data/members.json',members)
    print('Reconciled pinned main source metadata without changing source arrays; SEC failures',len(FAILED_SEC))
if __name__=='__main__':main()
