"""Исходный файл карты: загруженный байт в байт, а не пересобранная выгрузка."""
import unittest
import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.test_pmm_tree import _wide_map

IMPORT = '/api/v1/import/file'


class ProcessSourceTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.name = f'Исходник {uuid.uuid4().hex[:6]}'

    def upload(self, content, params=None):
        return self.client.post(IMPORT, params=params or {'asNew': 'true'},
                                files={'file': (f'{self.name}.drawio', content, 'application/xml')})

    def test_uploaded_file_is_kept_byte_for_byte(self):
        content = _wide_map(3).encode()
        process = self.upload(content).json()
        res = self.client.get(f"/api/v1/import/{process['id']}/source")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.content, content)
        self.assertIn('filename*=UTF-8', res.headers['content-disposition'])
        self.assertIn('x-uploaded-at', res.headers)

    def test_reupload_into_the_map_replaces_the_original(self):
        process = self.upload(_wide_map(3).encode()).json()
        newer = _wide_map(5).encode()
        res = self.upload(newer, params={'replace': process['id']})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.client.get(f"/api/v1/import/{process['id']}/source").content, newer)

    def test_map_without_upload_has_no_original(self):
        created = self.client.post('/api/v1/import/xml', json={'xml': _wide_map(2), 'fileName': 'x.drawio', 'fromEditor': True}).json()
        res = self.client.get(f"/api/v1/import/{created['id']}/source")
        self.assertEqual(res.status_code, 404)
        self.assertIn('Оригинал файла не сохранён', res.json()['detail'])
