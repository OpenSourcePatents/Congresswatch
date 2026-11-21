#!/usr/bin/env python3
"""Stage a clean local GovInfo corpus. No frontend data or production writes."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.request import urlopen
from urllib.parse import urlparse
from zipfile import ZipFile
from concurrent.futures import ThreadPoolExecutor
from fetch_bills.utils.official_prose import read_status, read_body, select_version

BASE = 'https://www.govinfo.gov'
TYPES = ('hr','s','hjres','sjres','hconres','sconres','hres','sres')
INDEXED = re.compile(r'data-name="((?:BILLS|BILLSTATUS)-\d+[a-z0-9]+\.xml)"')
ROOT_PATH = Path('.cache/govinfo')


def partitions(congress):
    for kind in TYPES:
        status = f'BILLSTATUS-{congress}-{kind}'
        yield status, f'{BASE}/bulkdata/BILLSTATUS/{congress}/{kind}', read_status
        for session in (1, 2):
            bills = f'BILLS-{congress}-{session}-{kind}'
            yield bills, f'{BASE}/bulkdata/BILLS/{congress}/{session}/{kind}', read_body


def fetch(url, path):
    temporary = path.with_suffix(path.suffix + '.tmp')
    try:
        with urlopen(url, timeout=90) as response, temporary.open('wb') as sink:
            if urlparse(response.url).scheme != 'https' or urlparse(response.url).hostname != 'www.govinfo.gov':
                raise ValueError('unexpected redirect leaving GovInfo')
            shutil.copyfileobj(response, sink)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def sha_file(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def write_json(value, path):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as out:
        json.dump(value, out, separators=(',', ':'), ensure_ascii=False)
        out.write('\n')
    temporary.replace(path)


def prepare_partition(base, spec, refresh):
    name, url, _ = spec
    path, index = base / (name + '.zip'), base / (name + '.index.html')
    if refresh or not path.is_file():
        fetch(f'{url}/{name}.zip', path)
    if refresh or not index.is_file():
        fetch(url, index)


def read_partition(base, spec, errors):
    name, url, read = spec
    path, index = base / (name + '.zip'), base / (name + '.index.html')
    indexed = set(INDEXED.findall(index.read_text()))
    partition = {'name': name, 'index_url': url, 'index_sha256': sha_file(index),
                 'archive_sha256': sha_file(path), 'indexed': len(indexed), 'fallback': []}
    result = {}
    counts = Counter()
    with ZipFile(path) as archive:
        filenames = [i.filename for i in archive.infolist() if not i.is_dir()]
        archived = set(filenames)
        if not indexed or len(archived) != len(filenames) or not archived:
            raise ValueError(f'{name}: unexpected empty or duplicate index or archive')
        if archived - indexed:
            raise ValueError(f'{name}: {len(archived-indexed)} archived names missing from source index')
        for filename in sorted(indexed):
            try:
                if filename in archived:
                    content = archive.read(filename)
                else:
                    fallback = base / 'fallback' / filename
                    if not fallback.is_file():
                        fetch(f'{url}/{filename}', fallback)
                    content = fallback.read_bytes()
                    partition['fallback'].append(filename)
                record = read(filename.removesuffix('.xml'), content)
                counts['parsed' if record else 'amendment_instructions_excluded'] += 1
                if record:
                    result[filename] = record
            except Exception as exc:
                errors.append({'partition': name, 'file': filename, 'error': str(exc)})
                counts['errors'] += 1
    partition['counts'] = dict(counts)
    print(name, dict(counts), 'fallbacks', len(partition['fallback']), flush=True)
    return result, partition


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--congress', type=int, default=119)
    cli.add_argument('--cache', type=Path, default=ROOT_PATH)
    cli.add_argument('--refresh', action='store_true')
    opts = cli.parse_args()
    opts.cache.mkdir(parents=True, exist_ok=True)
    base = opts.cache / 'zips'
    (base / 'fallback').mkdir(parents=True, exist_ok=True)
    specs = list(partitions(opts.congress))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda spec: prepare_partition(base, spec, opts.refresh), specs))
    documents, status, manifests, errors, packages = {}, {}, [], [], {}
    for spec in specs:
        rows, manifest = read_partition(base, spec, errors)
        manifests.append(manifest)
        for filename, record in rows.items():
            if spec[2] is read_status:
                key = record['bill_id']
                if key in status: raise ValueError(f'duplicate metadata identity: {key}')
                status[key] = record
            else:
                if filename in packages:
                    if packages[filename] != record.source_sha256:
                        errors.append({'file': filename, 'error': 'package has conflicting payloads between sessions'})
                    continue
                packages[filename] = record.source_sha256
                documents.setdefault(record.bill_id, []).append(record)
    coverage = {'congress': opts.congress, 'checked_at': datetime.now(timezone.utc).isoformat(),
                'partitions': manifests, 'official_metadata_bills': len(status),
                'official_full_text_versions': len(packages),
                'official_full_text_bills': len(documents), 'errors': errors}
    if errors:
        write_json(coverage, opts.cache / 'failed.manifest.json')
        raise SystemExit(f'{len(errors)} source errors; existing corpus untouched')
    corpus = {}
    for key, texts in documents.items():
        dates = status.get(key, {}).get('versions', {})
        doc = select_version(texts, dates)
        corpus[key] = {'source': doc.source(dates.get(doc.version_code)), 'text': doc.text}
    for name, data in [('corpus', corpus), ('status', status)]:
        path = opts.cache / (name + '.json')
        write_json({k: data[k] for k in sorted(data)}, path)
        coverage[name + '_sha256'] = sha_file(path)
    write_json(coverage, opts.cache / 'manifest.json')
    print(json.dumps({k:v for k,v in coverage.items() if k != 'partitions'}, indent=2))


if __name__ == '__main__':
    main()
