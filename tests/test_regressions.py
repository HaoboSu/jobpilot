"""Offline regressions for the release review; never use real user data."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from app import resume
from app.server import Handler


class RequestBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / 'web').mkdir()
        (cls.root / 'web' / 'index.html').write_text('JobPilot test')
        (cls.root / 'web-private').mkdir()
        (cls.root / 'web-private' / 'secret.txt').write_text('private fixture')
        cls.files = patch('app.server.WEB_DIR', str(cls.root / 'web'))
        cls.files.start()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f'127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.files.stop()
        cls.tmp.cleanup()

    def request(self, method='GET', path='/', body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_local_static(self):
        status, body, headers = self.request()
        self.assertEqual((status, body), (200, b'JobPilot test'))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['X-Frame-Options'], 'DENY')

    def test_localhost_alias(self):
        self.assertEqual(self.request(headers={'Host': f'localhost:{self.server.server_port}'})[0], 200)

    def test_rebinding_host(self):
        self.assertEqual(self.request(headers={'Host': f'evil.example:{self.server.server_port}'})[0], 403)

    def test_cross_origin_read(self):
        self.assertEqual(self.request(headers={'Origin': 'https://evil.example'})[0], 403)

    def test_cross_origin_write(self):
        with patch.object(Handler, '_route_post') as route:
            status, _, _ = self.request('POST', '/api/settings', '{}', {
                'Content-Type': 'application/json', 'Origin': 'https://evil.example'})
            self.assertEqual(status, 403)
            route.assert_not_called()

    def test_cross_site_no_origin(self):
        self.assertEqual(self.request(headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_null_origin(self):
        self.assertEqual(self.request(headers={'Origin': 'null'})[0], 403)

    def test_form_submission(self):
        self.assertEqual(self.request('POST', '/api/settings', '{}', {'Content-Type': 'text/plain'})[0], 415)

    def test_same_origin_json(self):
        with patch.object(Handler, '_route_post', lambda handler, path, data: handler._ok(data)):
            status, body, _ = self.request('POST', '/api/test', '{"test": true}', {
                'Content-Type': 'application/json; charset=utf-8', 'Origin': 'http://' + self.host})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)['data']['test'])

    def test_cli_json_without_origin(self):
        with patch.object(Handler, '_route_post', lambda handler, path, data: handler._ok(data)):
            self.assertEqual(self.request('POST', '/api/test', '{}', {'Content-Type': 'application/json'})[0], 200)

    def test_invalid_json(self):
        for body in ['[]', 'null', '"text"', '{broken']:
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', '/api/settings', body, {'Content-Type': 'application/json'})[0], 400)

    def test_invalid_lengths(self):
        for length in ['-1', '16777217', 'not-a-number']:
            with self.subTest(length=length):
                self.assertEqual(self.request('POST', '/api/settings', None, {
                    'Content-Type': 'application/json', 'Content-Length': length})[0], 400)

    def test_static_traversal(self):
        for path in ['/../web-private/secret.txt', '/%2e%2e/web-private/secret.txt']:
            with self.subTest(path=path):
                status, body, _ = self.request(path=path)
                self.assertEqual(status, 404)
                self.assertNotIn(b'private fixture', body)

    def test_symlink_escape(self):
        try:
            (self.root / 'web' / 'link.txt').symlink_to(self.root / 'web-private' / 'secret.txt')
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation not available')
        self.assertEqual(self.request(path='/link.txt')[0], 404)


class PDFSelectionTests(unittest.TestCase):
    def test_garbled_library_output_falls_back(self):
        for broken in ['\x00A\x00B\x00C\x00D\x00E', '(cid:0) A (cid:42)', '\ufffd' * 12]:
            with self.subTest(text=repr(broken)), patch('app.resume._library_pdf_text', return_value=broken), patch('app.pdf.extract_text', return_value='张明 数据分析师') as builtin:
                self.assertEqual(resume._pdf_text(b'%PDF-1.4 fixture'), '张明 数据分析师')
                builtin.assert_called_once()

    def test_valid_library_text_keeps_priority(self):
        with patch('app.resume._library_pdf_text', return_value='Jane Example\nPython developer'), patch('app.pdf.extract_text') as builtin:
            self.assertIn('Jane Example', resume._pdf_text(b'%PDF-1.4 fixture'))
            builtin.assert_not_called()


if __name__ == '__main__':
    unittest.main()
