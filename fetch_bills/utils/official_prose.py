"""Read GovInfo bill XML and BILLSTATUS metadata without trusting binary or HTML."""
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from defusedxml import ElementTree as ET

KINDS = r'(hconres|sconres|hjres|sjres|hres|sres|hr|s)'
PACKAGE = re.compile(r'BILLS-(\d+)' + KINDS + r'([1-9]\d*)([a-z0-9]+)')
STATUS = re.compile(r'BILLSTATUS-(\d+)' + KINDS + r'([1-9]\d*)')
HEAD = re.compile(r'(?:(\d+)\s+)?([A-Z. ]+?)\s*(\d+)\s+([A-Z0-9]+)\s*:', re.I)
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
SKIP = {'deleted', 'strike', 'stricken', 'struck', 'strike-through', 'toc',
        'table-of-contents', 'metadata', 'enum', 'footnote', 'endorsement', 'attestation'}
EXTRACTION_VERSION = 'govinfo-legislative-body-v2'
ANALYSIS_VERSION = 'official-prose-overlap-v2'


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    return ET.fromstring(raw, forbid_dtd=False, forbid_entities=True, forbid_external=True)


def package_id(package):
    match = PACKAGE.fullmatch(package)
    if not match:
        raise ValueError(f'invalid bill package {package!r}')
    congress, kind, number, code = match.groups()
    return f'{kind.upper()}{number}-{congress}', code


def words(prose):
    for m in WORD.finditer(prose):
        yield unicodedata.normalize('NFKC', m[0].replace('’', "'")).casefold(), m.start(), m.end()


def _pieces(node):
    if tag(node) in SKIP or any(str(v).lower() in ('deleted','struck','strike-through','strikethrough') for v in node.attrib.values()):
        return
    if node.text:
        yield node.text
    for child in node:
        yield from _pieces(child)
        if child.tail:
            yield child.tail


@dataclass(frozen=True, slots=True)
class BillDocument:
    bill_id: str
    package: str
    version_code: str
    source_url: str
    source_sha256: str
    text_sha256: str
    text: str

    def source(self, version_date=None):
        return {k: getattr(self, k) for k in ('bill_id','version_code','source_url','source_sha256','text_sha256')} | {
            'version_date': version_date, 'extraction_version': EXTRACTION_VERSION,
            'source_format': 'govinfo_official_bill_xml', 'package_id': self.package}


def read_body(package, raw):
    bill_id, code = package_id(package)
    root = parse(raw)
    root_name = tag(root)
    if root_name not in ('bill','resolution','joint-resolution','concurrent-resolution','amendment-doc'):
        raise ValueError(f'{package}: unexpected document root {root_name}')
    form = next((n for n in root if tag(n) in ('form', 'amendment-form', 'engrossed-amendment-form')), None)
    form_congress = next(((n.text or '').strip().lower()
                          for n in form.iter() if tag(n) == 'congress'), '') if form is not None else ''
    numeric = re.match(r'\d+', form_congress)
    congress = numeric[0] if numeric else (
        '119' if re.match(r'one hundred nineteenth congress\b', form_congress) else None)
    form_id = next((''.join(n.itertext()).strip() for n in form.iter()
                    if tag(n) == 'legis-num'), '') if form is not None else ''
    label = re.sub(r'[^A-Z0-9]', '', form_id.upper())
    if not label or not congress:
        # Certain amendment instructions omit a full legislative form.
        # Unlike bills, these are never used as consolidated body text.
        if root_name != 'amendment-doc':
            raise ValueError(f'{package}: no legislative form identity')
        metadata = next((n for n in root if tag(n) == 'metadata'), None)
        heading = next((m for n in metadata.iter() if tag(n) == 'title'
                        and (m := HEAD.match((n.text or '').strip()))), None) if metadata is not None else None
        if not heading:
            raise ValueError(f'{package}: no amendment-instruction identity')
        congress, kind, number, actual_code = heading.groups()
        kind = re.sub(r'[^A-Z]', '', kind.upper())
        label = {'HCON':'HCONRES','SCON':'SCONRES','HJ':'HJRES','SJ':'SJRES'}.get(kind, kind) + number
        if actual_code.lower() != code:
            raise ValueError(f'{package}: amendment-instruction version mismatch')
    if f'{label}-{congress}' != bill_id:
        raise ValueError(f'{package}: XML identity mismatch ({label}-{congress})')
    if root_name == 'amendment-doc':
        return None
    # Only the published legislative body, not digest/form/front matter.
    bodies = [n for n in root if tag(n) in ('legis-body', 'resolution-body')]
    if not bodies:
        raise ValueError(f'{package}: no legislative body')
    text = ' '.join(' '.join(piece for body in bodies for piece in _pieces(body)).split())
    if not text:
        raise ValueError(f'{package}: empty legislative body')
    if re.search(r'%PDF-|endstream\s+endobj|<!DOCTYPE\s+html', text, re.I):
        raise ValueError(f'{package}: contamination in legislative body')
    return BillDocument(bill_id, package, code,
        f'https://www.govinfo.gov/content/pkg/{package}/xml/{package}.xml',
        sha(raw), sha(text.encode()), text)


def read_status(package, raw):
    match = STATUS.fullmatch(package)
    if not match:
        raise ValueError(f'invalid status package {package!r}')
    congress, kind, number = match.groups()
    bill = parse(raw).find('bill')
    if bill is None or (bill.findtext('congress'), (bill.findtext('type') or '').lower(),
                         bill.findtext('number')) != (congress, kind, number):
        raise ValueError(f'{package}: BILLSTATUS identity mismatch')
    bill_id = f'{kind.upper()}{number}-{congress}'
    sponsors = [n.text for n in bill.findall('sponsors/item/bioguideId') if n.text]
    if any(not re.fullmatch(r'[A-Z]\d{6}', s) for s in sponsors):
        raise ValueError(f'{package}: malformed official sponsor ID')
    versions = {}
    for item in bill.findall('textVersions/item'):
        for url_node in item.findall('formats/item/url'):
            path = (url_node.text or '').strip()
            version_package = path.rsplit('/', 1)[-1].removesuffix('.xml')
            if PACKAGE.fullmatch(version_package):
                other_id, code = package_id(version_package)
                if other_id != bill_id:
                    raise ValueError(f'{package}: text version lists wrong bill')
                versions[code] = item.findtext('date')
    identical = []
    for relation in bill.findall('relatedBills/item'):
        if any(item.findtext('identifiedBy') == 'CRS'
               and (item.findtext('type') or '').strip().casefold() in ('identical bill', 'identical')
               for item in relation.findall('relationshipDetails/item')):
            other = (relation.findtext('type') or '').upper() + (relation.findtext('number') or '')
            other_congress = relation.findtext('congress')
            if re.fullmatch(KINDS + r'[1-9]\d*', other, re.I) and other_congress is not None:
                identical.append(f'{other}-{other_congress}')
    return {
        'bill_id': bill_id, 'congress': int(congress), 'title': bill.findtext('title') or '',
        'sponsor_ids': sponsors, 'introduced_date': bill.findtext('introducedDate'),
        'legislation_url': bill.findtext('legislationUrl'),
        'latest_action': bill.findtext('latestAction/text'),
        'latest_action_date': bill.findtext('latestAction/actionDate'),
        'cosponsor_ids': [n.text for n in bill.findall('cosponsors/item/bioguideId') if n.text],
        'crs_identical_bills': sorted(set(identical)),
        'versions': versions, 'source_sha256': sha(raw),
        'source_url': f'https://www.govinfo.gov/bulkdata/BILLSTATUS/{congress}/{kind}/{package}.xml',
    }


def select_version(documents, versions):
    """Prefer enrolled. Otherwise latest dated version with explicit deterministic tie breaks."""
    order = {'enr': 100,'eah': 90,'eas':90,'eh':80,'es':80,'rh':70,'rs':70,
             'rfs': 60,'rfh':60,'cph':55,'cps':55,'ih':40,'is':40}
    return max(documents, default=None, key=lambda d: (
        d.version_code == 'enr', versions.get(d.version_code) or '',
        order.get(d.version_code, 0), d.package))
