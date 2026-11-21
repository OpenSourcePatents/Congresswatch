"""Run named version-specific controls on cached GovInfo XML from bulk archives."""
from pathlib import Path
import json
import sys
from zipfile import ZipFile
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fetch_bills.utils.official_prose import read_body, read_status, select_version
from fetch_bills.utils.official_passages import Passages, Tokens

CONTROLS = [
    ('hr139eh','s29is', True, 'same legislation, different chambers'),
    ('hr139eh','hr300ih', False, 'same topic, opposing daylight-time mechanisms'),
    ('s29is','hr300ih', False, 'same topic, opposing daylight-time mechanisms'),
    ('hr4313eh','hr7148enr', True, 'real excerpt embedded inside large omnibus with changes elsewhere'),
]
TARGETS = {v for a,b,_,_ in CONTROLS for v in (a,b)} | {'s1383eah','s1383is','s1383es','s1383rs'}


def main(base=Path('.cache/govinfo/zips')):
    docs = {}
    for z in sorted(base.glob('BILLS-119-*.zip')):
        with ZipFile(z) as a:
            for t in TARGETS:
                filename = f'BILLS-119{t}.xml'
                if filename in a.namelist():
                    docs[t] = read_body(filename[:-4],a.read(filename))
    assert TARGETS == docs.keys(), TARGETS - docs.keys()
    assert docs['s1383eah'] is None, 'Engrossed House amendment instruction mistaken for consolidated text'
    with ZipFile(base / 'BILLSTATUS-119-s.zip') as a:
        status = read_status('BILLSTATUS-119s1383', a.read('BILLSTATUS-119s1383.xml'))
    selected = select_version([docs[t] for t in ('s1383is','s1383rs','s1383es')],status['versions'])
    assert selected.version_code == 'es'
    results = [{'case': 'S.1383 version trap', 's1383eah': 'excluded_unconsolidated_instruction',
                'selected': selected.source(status['versions']['es'])}]
    for a,b,want,reason in CONTROLS:
        ta,tb = (Tokens.from_text(docs[t].text) for t in (a,b))
        index = Passages(ta)
        length, *_ = index.longest(tb)
        found = index.evidence(tb)
        assert bool(found) == want, (a,b,reason)
        results.append({'reason': reason, 'target': docs[a].source(), 'matched': docs[b].source(),
                        'longest_contiguous_words': length, 'evidence': found})
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.cache/govinfo/zips'))
