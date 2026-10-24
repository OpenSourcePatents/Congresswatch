"""Offline regression coverage for the actual consent POST retry path."""
import contextlib
import io
import unittest
from unittest.mock import call, patch

import requests
import fetch_senate_efd as senate


def response(status, url=senate.EFD_HOME):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = b'<input name="csrfmiddlewaretoken" value="test-csrf">'
    result.raw = io.BytesIO(result.content)
    return result


class AgreementPostTests(unittest.TestCase):
    def test_blocked_post_exhausts_retries_with_backoff(self):
        for failure in (None, requests.ConnectionError('connection dropped'),
                        requests.Timeout('timed out'), 403, 429, 503):
            with self.subTest(failure=failure):
                client = senate.SenateEFDClient()
                failures = [response(failure) if isinstance(failure, int) else failure
                            for _ in range(3)]
                output = io.StringIO()
                with patch.object(client.session, 'request', side_effect=[response(200), *failures]) as request, \
                     patch.object(senate.time, 'sleep') as sleep, \
                     patch.object(senate.random, 'uniform', return_value=0.5), \
                     contextlib.redirect_stdout(output):
                    self.assertFalse(client.accept_agreement())
                self.assertFalse(client.agreed)
                self.assertEqual([c.args[0] for c in request.call_args_list],
                                 ['GET', 'POST', 'POST', 'POST'])
                self.assertEqual(sleep.call_args_list, [call(1.5), call(3.5), call(6.5)])
                for attempt in request.call_args_list[1:]:
                    self.assertEqual(attempt.kwargs['data'], {
                        'csrfmiddlewaretoken': 'test-csrf', 'prohibition_agreement': '1'})
                    self.assertEqual(attempt.kwargs['headers']['Referer'], senate.EFD_HOME)
                    self.assertEqual(attempt.kwargs['timeout'], 30)
                self.assertIn('retry 2/3', output.getvalue())
                self.assertIn('retry 3/3', output.getvalue())
                self.assertIn('exhausted 3 attempts', output.getvalue())
                if isinstance(failure, int):
                    self.assertIn(f'Agreement POST failed: HTTP {failure}', output.getvalue())
                    self.assertNotIn('no response', output.getvalue())

    def test_post_recovers_but_requires_search_verification(self):
        for verification_url, accepted in ((senate.EFD_BASE + '/search/', True),
                                           (senate.EFD_HOME, False)):
            with self.subTest(accepted=accepted):
                client = senate.SenateEFDClient()
                with patch.object(client.session, 'request', side_effect=[
                    response(200), requests.ConnectionError('dropped'), response(200),
                    response(200, verification_url),
                ]) as request, patch.object(senate.time, 'sleep'):
                    self.assertEqual(client.accept_agreement(), accepted)
                    self.assertEqual(client.agreed, accepted)
                    self.assertEqual([c.args[0] for c in request.call_args_list],
                                     ['GET', 'POST', 'POST', 'GET'])

    def test_nonretryable_post_error_preserves_status(self):
        client = senate.SenateEFDClient()
        output = io.StringIO()
        with patch.object(client.session, 'request', side_effect=[response(200), response(400)]) as request, \
             patch.object(senate.time, 'sleep'), contextlib.redirect_stdout(output):
            self.assertFalse(client.accept_agreement())
        self.assertEqual(request.call_count, 2)
        self.assertIn('Agreement POST failed: HTTP 400', output.getvalue())


if __name__ == '__main__':
    unittest.main()
