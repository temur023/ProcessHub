"""Раскладка холста хранится между открытиями карты (``canvas_cache``)."""
import gzip
import json
import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.routers.canvas as canvas_router
from app.main import app
from app.services import canvas_cache
from app.services.drawio_parser import parse_drawio_xml
from tests.test_pmm_tree import _linked_pages, _wide_map

URL = '/api/v1/canvas/layout'


class CanvasCacheTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get('SQB_CANVAS_CACHE')
        os.environ['SQB_CANVAS_CACHE'] = tempfile.mkdtemp()
        canvas_cache.clear_memory()
        self.calls = 0
        real = canvas_router.canvas_layout

        def counted(page):
            self.calls += 1
            return real(page)

        canvas_router.canvas_layout = counted
        self.addCleanup(setattr, canvas_router, 'canvas_layout', real)
        self.client = TestClient(app)
        self.page = parse_drawio_xml(_wide_map(6), 'wide.drawio')

    def tearDown(self):
        if self._env is None:
            os.environ.pop('SQB_CANVAS_CACHE', None)
        else:
            os.environ['SQB_CANVAS_CACHE'] = self._env
        canvas_cache.clear_memory()

    def post(self, page):
        res = self.client.post(URL, json=page.model_dump(mode='json'))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_second_request_is_served_from_the_cache(self):
        first = self.post(self.page)
        self.assertTrue(first['routes'])
        second = self.post(self.page)
        self.assertEqual(first, second)
        self.assertEqual(self.calls, 1)

    def test_any_change_of_shapes_or_lines_is_computed_anew(self):
        before = self.post(self.page)
        moved = self.page.model_copy(deep=True)
        moved.nodes[0].geometry.x += 40
        after = self.post(moved)
        self.assertEqual(self.calls, 2)
        self.assertNotEqual(before['routes'], after['routes'])
        renamed = self.page.model_copy(deep=True)
        renamed.edges[0].name = 'Да'
        self.assertIn(renamed.edges[0].id, self.post(renamed)['edgeLabels'])
        self.assertEqual(self.calls, 3)

    def test_cache_survives_a_restart(self):
        first = self.post(self.page)
        canvas_cache.clear_memory()          # как после перезапуска: память пуста, файлы на месте
        self.assertEqual(self.post(self.page), first)
        self.assertEqual(self.calls, 1)

    def test_new_layout_code_does_not_reuse_old_layouts(self):
        self.post(self.page)
        self.assertNotEqual(canvas_cache.layout_key(self.page), canvas_cache.layout_key(self.page, 'other-code'))
        old = canvas_cache.CODE_VERSION
        canvas_cache.CODE_VERSION = 'other-code'
        self.addCleanup(setattr, canvas_cache, 'CODE_VERSION', old)
        self.post(self.page)
        self.assertEqual(self.calls, 2)

    def test_saved_map_has_its_pages_ready_before_they_are_opened(self):
        process = parse_drawio_xml(_linked_pages(), 'pages.drawio')
        res = self.client.post('/api/v1/processes/', json=process.model_dump(mode='json'))
        self.assertEqual(res.status_code, 201, res.text)
        saved = canvas_cache.warm_up(process)            # встаёт в очередь за прогревом сохранения
        self.assertTrue(saved.wait(60))
        pages = [process, *process.subProcesses]
        self.assertGreaterEqual(len(pages), 3)
        for page in pages:                              # холст присылает страницу так, как получил её
            self.post(page)
        self.assertEqual(self.calls, 0)

    def test_after_an_update_saved_maps_are_ready_before_they_are_opened(self):
        # Новый код раскладки — прежние раскладки не годятся; запуск бэкенда
        # считает их для всех сохранённых карт заранее.
        process = parse_drawio_xml(_linked_pages(), 'pages.drawio')
        store = SimpleNamespace(infos=lambda: [SimpleNamespace(id=process.id, updatedAt=None)],
                                get=lambda _id: process)
        canvas_cache.warm_up_saved(store)
        for page in [process, *process.subProcesses]:
            self.post(page)
        self.assertEqual(self.calls, 0)

    def test_canvas_sends_only_the_page_compressed(self):
        # Холст шлёт фигуры, связи и дорожки, сжатые gzip: ответ и запись в кэше
        # те же, что для полной карты, — прогрев после сохранения не напрасен.
        full = self.post(self.page)
        slim = json.dumps(self.page.model_dump(mode='json', include={'nodes', 'edges', 'lanes'})).encode()
        res = self.client.post(URL, content=gzip.compress(slim),
                               headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json(), full)
        self.assertEqual(self.calls, 1)
        broken = self.client.post(URL, content=b'\x1f\x8b not gzip',
                                  headers={'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        self.assertEqual(broken.status_code, 400)

    def test_big_answers_travel_compressed(self):
        # Карту и её раскладку браузер получает сжатыми: несжатые мегабайты
        # dev-прокси везёт секунду при каждом открытии карты.
        page = parse_drawio_xml(_wide_map(40), 'wide.drawio')
        res = self.client.post(URL, json=page.model_dump(mode='json'), headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(res.headers.get('content-encoding'), 'gzip')
        self.assertEqual(len(res.json()['routes']), 39)
        saved = self.client.post('/api/v1/processes/', json=page.model_dump(mode='json'))
        self.assertEqual(saved.status_code, 201, saved.text)
        res = self.client.get(f"/api/v1/processes/{saved.json()['id']}", headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(res.headers.get('content-encoding'), 'gzip')
        self.assertEqual(res.json()['id'], saved.json()['id'])

    def test_simultaneous_requests_compute_once(self):
        computed = []

        def slow(page):
            computed.append(page)
            time.sleep(0.3)
            return '{"routes": {}}'

        results = []
        threads = [threading.Thread(target=lambda: results.append(canvas_cache.cached_layout_json(self.page, slow)))
                   for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(computed), 1)
        self.assertEqual(results, ['{"routes": {}}'] * 3)


if __name__ == '__main__':
    unittest.main()


def test_eviction_survives_a_file_removed_by_a_parallel_request(tmp_path, monkeypatch):
    """Параллельный запрос вычистил файл между glob и stat — запись не падает 500."""
    from pathlib import Path

    monkeypatch.setattr(canvas_cache, 'cache_dir', lambda: tmp_path)
    gone = tmp_path / 'gone.json'
    gone.write_text('{}', encoding='utf-8')
    real_glob = Path.glob

    def glob_then_vanish(self, pattern):
        found = list(real_glob(self, pattern))
        gone.unlink(missing_ok=True)
        return found

    monkeypatch.setattr(Path, 'glob', glob_then_vanish)
    canvas_cache._write('fresh', '{"ok": true}')
    assert (tmp_path / 'fresh.json').read_text(encoding='utf-8') == '{"ok": true}'
