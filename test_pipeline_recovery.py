import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import fetch_senate_efd as senate
import fetch_travel_costs as travel


def response(status):
    r = requests.Response()
    r.status_code = status
    r._content = b''
    r.raw = io.BytesIO(b'')
    r.url = senate.EFD_HOME
    return r


class PipelineRecoveryTests(unittest.TestCase):
    def test_gate_recovers_after_forbidden(self):
        client = senate.SenateEFDClient()
        with patch.object(client.session, 'request', side_effect=[response(403), response(200)]) as request, patch.object(senate.time, 'sleep') as sleep:
            self.assertEqual(client._request('GET', senate.EFD_HOME, retry_forbidden=True).status_code, 200)
            self.assertEqual(request.call_count, 2)
            self.assertEqual(sleep.call_count, 1)

    def test_gate_exhaustion_preserves_http_status(self):
        client = senate.SenateEFDClient()
        output = io.StringIO()
        with patch.object(client.session, 'request', side_effect=[response(403) for _ in range(3)]), patch.object(senate.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            self.assertFalse(client.accept_agreement())
            self.assertEqual(sleep.call_count, 2)
        self.assertIn('home page: 403', output.getvalue())

    def test_timeout_is_bounded(self):
        client = senate.SenateEFDClient()
        with patch.object(client.session, 'request', side_effect=requests.Timeout('timeout')) as request, patch.object(senate.time, 'sleep'):
            self.assertFalse(client.accept_agreement())
            self.assertEqual(request.call_count, 3)

    def run_travel(self, status):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        details = root / 'details'
        details.mkdir()
        doc = details / 'member.json'
        original = json.dumps({'travel': [{'source_doc': 'https://disclosures-clerk.house.gov/test.pdf'}]})
        doc.write_text(original)
        manifest = root / 'manifest.json'
        with patch.object(travel, 'DETAILS_DIR', str(details)), patch.object(travel, 'MANIFEST_PATH', str(manifest)), patch.object(travel.requests, 'get', return_value=response(status)):
            if status == 503:
                with self.assertRaises(SystemExit):
                    travel.main()
            else:
                travel.main()
            state = json.loads(manifest.read_text())
            self.assertEqual(doc.read_text(), original)
            if status == 404:
                self.assertEqual(travel.collect_candidates(state), [])
                entry = next(iter(state.values()))
                entry['retry_after'] = '2000-01-01T00:00:00+00:00'
                self.assertEqual(len(travel.collect_candidates(state)), 1)
        return next(iter(state.values()))['status']

    def test_missing_document_is_deferred_without_data_loss(self):
        self.assertEqual(self.run_travel(404), 'document_missing')

    def test_server_outage_still_fails(self):
        self.assertEqual(self.run_travel(503), 'download_error')


if __name__ == '__main__':
    unittest.main()
